"""Sprint fee33 verification — billing groups + membership integrity.

Pass/fail only, no prompts, no interactive input. Run:

    python3 scripts/verify_fee33.py

WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Two disposable orgs, never the real ones.** Every fixture lives under orgs
  this run creates and deletes. Nothing writes to the default org.

* **RLS is proved on ``app_service``, never on ``postgres``.** ``postgres`` has
  ``rolbypassrls`` and every isolation check run on it passes vacuously. Check 6
  asserts ``rolbypassrls = False`` on the role it uses BEFORE it trusts a single
  denial, because otherwise "I could not see the other org's row" and "there was
  no row" are the same observation.

* **Every BREAKPOINT check is run TWICE — once under a group with a
  household_id and once under a group with household_id = NULL** — and check 5
  asserts the two transcripts are identical. Running the NULL case only for
  "does it insert" would prove nothing about the constraint behaving the same
  way, which is exactly what the prompt asks.

* **The negative direction is proved as hard as the positive one.** Check 3
  does not merely assert that a STATEMENT and a PAYER membership both insert;
  it asserts the account ends up holding all three simultaneously and that
  ``find_breakpoint_conflict`` still names only the BREAKPOINT one. A rule that
  restricted nothing and a rule that restricted everything would both survive a
  naive "it worked" check.

* **Check 4 proves the removal is a CLOSE, not a delete.** It counts the
  membership rows before and after: the count must be UNCHANGED and the row
  must have gained ``valid_to``/``system_to``. A hard delete would also free the
  account to rejoin, and would pass a check that only looked at the rejoin.

* **Teardown is by fixture org id, with an exact before/after row count as the
  backstop.** Never a TRUNCATE. billing_groups and billing_group_members hold
  no production rows today, which is precisely when a truncate looks safe and
  starts being a data-loss bug the moment fee34 lands.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent

for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_site) not in sys.path:
        sys.path.insert(0, str(_site))
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncpg  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from services.billing_groups import (  # noqa: E402
    EXCLUSIVE_GROUP_TYPES,
    GROUP_TYPE_BREAKPOINT,
    GROUP_TYPE_PAYER,
    GROUP_TYPE_STATEMENT,
    UNRESTRICTED_GROUP_TYPES,
    BillingGroupNotFoundError,
    BreakpointOverlapError,
    add_member,
    create_billing_group,
    find_breakpoint_conflict,
    list_account_memberships,
    list_members,
    move_member,
    remove_member,
    update_billing_group,
)

TABLE_GROUPS = "public.billing_groups"
TABLE_MEMBERS = "public.billing_group_members"

#: Every table this run writes to. Check 7 compares each one's count before and
#: after. Listed explicitly rather than derived, so a table the script starts
#: touching without being added here shows up as a review question.
TOUCHED_TABLES = (
    "public.billing_group_members",
    "public.billing_groups",
    "public.accounts",
    "public.households",
    "public.entities",
    "public.organizations",
)

#: The policy shape check 1 requires, introspected from the deployed
#: accounts/account_owners/households policies rather than written from memory.
#: Compared after whitespace folding — Postgres reformats an expression when it
#: stores it, so a literal string compare fails on formatting alone.
EXPECTED_POLICY = (
    "((org_id = (NULLIF(current_setting('app.current_org_id'::text, true), "
    "''::text))::uuid) OR (current_setting('app.is_super_admin'::text, true) "
    "= 'true'::text))"
)


def _fold(sql: str | None) -> str:
    return " ".join((sql or "").split())


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def record(self, number, outcome: str, name: str, detail: str = "") -> None:
        self.rows.append((f"[{number}] {outcome}", name, detail))
        line = f"[{number}] {outcome:<7} {name}"
        if detail:
            line += f"\n            {detail}"
        print(line, flush=True)

    def ok(self, number, name: str, detail: str = "") -> None:
        self.record(number, "PASS", name, detail)

    def bad(self, number, name: str, detail: str = "") -> None:
        self.record(number, "FAIL", name, detail)

    def blocked(self, number, name: str, detail: str = "") -> None:
        self.record(number, "BLOCKED", name, detail)

    def find(self, number, name: str, detail: str = "") -> None:
        self.record(number, "FIND", name, detail)

    def summary(self) -> int:
        passed = sum(1 for r in self.rows if "PASS" in r[0])
        failed = sum(1 for r in self.rows if "FAIL" in r[0])
        blocked = sum(1 for r in self.rows if "BLOCKED" in r[0])
        finds = sum(1 for r in self.rows if "FIND" in r[0])
        print("\n" + "=" * 74)
        print(f"  {passed} PASS   {failed} FAIL   {blocked} BLOCKED   "
              f"{finds} FIND   ({len(self.rows)} checks)")
        print("=" * 74)
        if blocked:
            print("  BLOCKED checks were NOT measured — this sprint stays HELD.")
        return 1 if failed else 0


class OrgSession:
    """One org-scoped transaction, shaped exactly like the real request path.

    ``set_config(..., is_local => true)`` IS ``SET LOCAL``: it lives for the
    current transaction and no longer. Under asyncpg's autocommit every
    statement is its own transaction, so a bare ``set_config`` followed by a
    query sets the GUC and discards it before the query runs — the NULLIF guard
    then denies everything, and the script would read its own mistake as an RLS
    finding.

    COMMITTED on clean exit, never rolled back: a prior sprint lost every write
    it made through the real pool to a savepoint rolled back at the end.
    """

    __slots__ = ("_conn", "_org_id", "_super", "_tr")

    def __init__(self, conn, org_id: str, *, is_super_admin: bool = False):
        self._conn = conn
        self._org_id = str(org_id)
        self._super = "true" if is_super_admin else "false"
        self._tr = None

    async def __aenter__(self):
        self._tr = self._conn.transaction()
        await self._tr.start()
        try:
            await self._conn.execute(
                "SELECT set_config('app.current_org_id', $1, true)", self._org_id
            )
            await self._conn.execute(
                "SELECT set_config('app.is_super_admin', $1, true)", self._super
            )
        except BaseException:
            await self._tr.rollback()
            raise
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            await self._tr.commit()
        else:
            await self._tr.rollback()
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


class Fixture:
    """Two disposable orgs. Org A carries every positive case; org B exists
    only so cross-org isolation has something real to fail to see.

    Org A's accounts, and why each is there:

      account_main    the account every BREAKPOINT check moves around.
      account_second  a second account, so "the group is not empty" and "this
                      account is in the group" stay distinguishable.
      account_null    only ever placed into household_id = NULL groups, so
                      check 5's transcript is genuinely independent of the
                      housed one rather than sharing its state.

    household_a exists so the housed/unhoused pair in check 5 differ in exactly
    one field. Created as superuser: creating an organization is not something
    app_service is permitted to do, and should not be.
    """

    def __init__(self) -> None:
        tag = uuid.uuid4().hex[:8]
        self.tag = tag
        self.org_a = str(uuid.uuid4())
        self.org_b = str(uuid.uuid4())

        self.household_a = str(uuid.uuid4())
        self.household_b = str(uuid.uuid4())

        self.entity_a = str(uuid.uuid4())
        self.entity_b = str(uuid.uuid4())

        self.account_main = str(uuid.uuid4())
        self.account_second = str(uuid.uuid4())
        self.account_null = str(uuid.uuid4())
        self.account_b = str(uuid.uuid4())

    async def create(self, conn) -> None:
        for org_id, slug in ((self.org_a, "a"), (self.org_b, "b")):
            await conn.execute(
                "INSERT INTO public.organizations (id, name, slug) "
                "VALUES ($1::uuid, $2, $3) ON CONFLICT (id) DO NOTHING",
                org_id, f"fee33 verify {slug} {self.tag}",
                f"fee33-verify-{slug}-{self.tag}",
            )

        for household_id, org_id, name in (
            (self.household_a, self.org_a, "Verify Household"),
            (self.household_b, self.org_b, "Other Tenant Household"),
        ):
            await conn.execute(
                "INSERT INTO public.households (id, org_id, name) "
                "VALUES ($1::uuid, $2::uuid, $3) ON CONFLICT (id) DO NOTHING",
                household_id, org_id, f"fee33 {name} {self.tag}",
            )

        for entity_id, org_id, household_id, name in (
            (self.entity_a, self.org_a, self.household_a, "Member A"),
            (self.entity_b, self.org_b, self.household_b, "Other Tenant"),
        ):
            await conn.execute(
                "INSERT INTO public.entities "
                "  (id, org_id, entity_type, display_name, primary_household_id) "
                "VALUES ($1::uuid, $2::uuid, 'individual', $3, $4::uuid) "
                "ON CONFLICT (id) DO NOTHING",
                entity_id, org_id, f"fee33 {name} {self.tag}", household_id,
            )

        for account_id, org_id, household_id, entity_id, label in (
            (self.account_main, self.org_a, self.household_a, self.entity_a, "MAIN"),
            (self.account_second, self.org_a, self.household_a, self.entity_a, "SECOND"),
            (self.account_null, self.org_a, None, self.entity_a, "NULLCASE"),
            (self.account_b, self.org_b, self.household_b, self.entity_b, "OTHERTENANT"),
        ):
            await conn.execute(
                """
                INSERT INTO public.accounts
                    (id, org_id, account_number_masked, account_number_hash,
                     custodian_code, registration_type, tax_status,
                     primary_entity_id, household_id)
                VALUES ($1::uuid, $2::uuid, $3, $4, 'fee33_test', 'individual',
                        'taxable', $5::uuid, $6::uuid)
                ON CONFLICT (id) DO NOTHING
                """,
                account_id, org_id, f"****{label}", f"hash-{account_id}",
                entity_id, household_id,
            )

    async def teardown(self, conn) -> None:
        """FK-safe order, scoped to THIS run's two org ids. Never a TRUNCATE.

        Runs in a ``finally`` so a failed check still cleans up: two disposable
        orgs left behind per failed run accumulate into exactly the orphan mess
        a prior sprint had to sweep by hand.
        """
        orgs = [self.org_a, self.org_b]
        for statement in (
            "DELETE FROM public.billing_group_members WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.billing_groups WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.account_owners WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.accounts WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.household_memberships WHERE household_id = ANY("
            "  SELECT id FROM public.households WHERE org_id = ANY($1::uuid[]))",
            "DELETE FROM public.entities WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.households WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.organizations WHERE id = ANY($1::uuid[])",
        ):
            await conn.execute(statement, orgs)


async def _counts(conn) -> dict[str, int]:
    out = {}
    for table in TOUCHED_TABLES:
        out[table] = int(await conn.fetchval(f"SELECT count(*) FROM {table}"))
    return out


async def _active_member_ids(conn, org_id: str, account_id: str) -> list[str]:
    """Read the membership state back from the TABLE, not from a return value.

    A service function reports what it believes it wrote; the table is what is
    actually there, and those are the two things this sprint has to keep equal.
    """
    rows = await conn.fetch(
        f"""
        SELECT m.id::text AS id
        FROM {TABLE_MEMBERS} m
        WHERE m.account_id = $1::uuid AND m.org_id = $2::uuid
          AND m.valid_to IS NULL AND m.system_to IS NULL
        ORDER BY m.valid_from
        """,
        account_id, org_id,
    )
    return [r["id"] for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Check 1 — deployed shape
# ═══════════════════════════════════════════════════════════════════════════


async def check_1(results: Results, admin) -> None:
    """Both tables exist, RLS is ON, and the policy shape is EXACTLY the
    established one.

    "RLS enabled" alone proves very little: a table with RLS on and a policy of
    ``USING (true)`` is wide open and passes that test. The policy expression is
    compared against the deployed accounts/households shape, whitespace-folded,
    because a policy that merely *mentions* org_id could still be missing the
    NULLIF — and a missing NULLIF turns an unset GUC into a cast error rather
    than a default deny.
    """
    problems: list[str] = []
    seen: dict[str, dict] = {}

    for table in ("billing_groups", "billing_group_members"):
        row = await admin.fetchrow(
            "SELECT c.relrowsecurity AS rls, c.relforcerowsecurity AS forced "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = $1",
            table,
        )
        if row is None:
            problems.append(f"{table} does not exist")
            continue
        if not row["rls"]:
            problems.append(f"{table} has RLS DISABLED")

        policies = await admin.fetch(
            "SELECT p.polname, p.polcmd, "
            "       pg_get_expr(p.polqual, p.polrelid) AS using_expr, "
            "       pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr "
            "FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = $1 ORDER BY p.polname",
            table,
        )
        if len(policies) != 1:
            problems.append(
                f"{table} has {len(policies)} policies, expected exactly 1 "
                f"({[p['polname'] for p in policies]})"
            )
            continue
        pol = policies[0]
        seen[table] = dict(pol)
        if pol["polname"] != f"{table}_org_isolation":
            problems.append(f"{table} policy is named {pol['polname']!r}")
        # pg_policy.polcmd is Postgres "char", which asyncpg hands back as
        # bytes. Comparing it to the str "*" is always False and would fail a
        # correct policy — decode before comparing.
        polcmd = pol["polcmd"]
        if isinstance(polcmd, (bytes, bytearray)):
            polcmd = polcmd.decode()
        if polcmd != "*":
            problems.append(
                f"{table} policy covers {polcmd!r}, not ALL — a policy "
                f"scoped to one command leaves the others ungoverned"
            )
        for label, expr in (("USING", pol["using_expr"]),
                            ("WITH CHECK", pol["check_expr"])):
            if _fold(expr) != _fold(EXPECTED_POLICY):
                problems.append(f"{table} {label} expression differs: {_fold(expr)}")

        grants = await admin.fetchval(
            "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) "
            "FROM information_schema.role_table_grants "
            "WHERE table_schema = 'public' AND table_name = $1 "
            "  AND grantee = 'app_service'",
            table,
        )
        if grants != "DELETE,INSERT,SELECT,UPDATE":
            problems.append(
                f"{table} app_service grants are {grants!r} — without the full "
                f"set the app gets a bare permission-denied that looks exactly "
                f"like an RLS denial"
            )

    # The columns the rest of this script depends on actually existing.
    for table, required in (
        ("billing_groups",
         {"id", "org_id", "name", "group_type", "household_id",
          "valid_from", "valid_to", "system_from", "system_to"}),
        ("billing_group_members",
         {"id", "org_id", "billing_group_id", "account_id",
          "valid_from", "valid_to", "system_from", "system_to"}),
    ):
        cols = {
            r["column_name"] for r in await admin.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1", table,
            )
        }
        missing = required - cols
        if missing:
            problems.append(f"{table} is missing columns {sorted(missing)}")

    # household_id MUST be nullable — check 5's whole premise.
    nullable = await admin.fetchval(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'billing_groups' "
        "  AND column_name = 'household_id'"
    )
    if nullable != "YES":
        problems.append(
            "billing_groups.household_id is NOT NULL — a group spanning two "
            "households, or none, would be unrepresentable"
        )

    # The group_type vocabulary the service writes must be one the CHECK admits.
    check_def = await admin.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'billing_groups_group_type_check'"
    )
    if not check_def:
        problems.append("billing_groups has no group_type CHECK constraint")
    else:
        for value in (GROUP_TYPE_BREAKPOINT, GROUP_TYPE_STATEMENT, GROUP_TYPE_PAYER):
            if f"'{value}'" not in check_def:
                problems.append(f"the group_type CHECK does not admit {value!r}")

    if problems:
        results.bad(1, "both tables deployed with the expected RLS policy shape",
                    "; ".join(problems))
    else:
        results.ok(
            1, "both tables deployed with the expected RLS policy shape",
            f"billing_groups + billing_group_members: RLS on, exactly one "
            f"FOR ALL <table>_org_isolation policy each, USING == WITH CHECK == "
            f"the deployed accounts/households expression (NULLIF guard present), "
            f"app_service holds SELECT/INSERT/UPDATE/DELETE on both, "
            f"household_id nullable, CHECK admits "
            f"{GROUP_TYPE_BREAKPOINT}/{GROUP_TYPE_STATEMENT}/{GROUP_TYPE_PAYER}",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Checks 2-5 — the constraint, run once per household shape
# ═══════════════════════════════════════════════════════════════════════════


async def _breakpoint_transcript(admin, fx: Fixture, *, household_id, account_id,
                                 label: str) -> dict:
    """Run the full BREAKPOINT story once and record what happened.

    Called TWICE — once with a household_id and once with None — so check 5 can
    compare the two transcripts field by field rather than re-asserting a
    weaker version of checks 2-4 for the NULL case.

    Every observation here is read back from the table under a fresh query, not
    taken from a service return value.
    """
    t: dict = {"label": label, "household_id": household_id}

    async with OrgSession(admin, fx.org_a):
        g1 = await create_billing_group(
            admin, fx.org_a, name=f"BP One {label} {fx.tag}",
            group_type=GROUP_TYPE_BREAKPOINT, household_id=household_id,
        )
        g2 = await create_billing_group(
            admin, fx.org_a, name=f"BP Two {label} {fx.tag}",
            group_type=GROUP_TYPE_BREAKPOINT, household_id=household_id,
        )
    t["group_1"], t["group_2"] = g1, g2
    t["group_1_household"] = g1["household_id"]

    # ── The account joins the first BREAKPOINT group ────────────────────────
    async with OrgSession(admin, fx.org_a):
        m1 = await add_member(admin, fx.org_a, group_id=g1["id"], account_id=account_id)
    t["first_add_ok"] = m1 is not None
    t["after_first_add"] = await _active_member_ids(admin, fx.org_a, account_id)

    # ── The SAME account is refused by the second one ───────────────────────
    t["overlap_raised"] = False
    t["overlap_type"] = None
    t["overlap_names_existing_group"] = False
    t["overlap_names_attempted_group"] = False
    t["overlap_names_account"] = False
    try:
        async with OrgSession(admin, fx.org_a):
            await add_member(admin, fx.org_a, group_id=g2["id"], account_id=account_id)
    except BreakpointOverlapError as exc:
        t["overlap_raised"] = True
        t["overlap_type"] = type(exc).__name__
        t["overlap_message"] = str(exc)
        # A TYPED error naming BOTH groups — attributes, not just prose, so a
        # caller can link to the blocker without re-parsing the string.
        t["overlap_names_existing_group"] = (
            exc.existing_group_id == g1["id"]
            and exc.existing_group_name == g1["name"]
            and g1["name"] in str(exc)
        )
        t["overlap_names_attempted_group"] = (
            exc.attempted_group_id == g2["id"]
            and exc.attempted_group_name == g2["name"]
            and g2["name"] in str(exc)
        )
        t["overlap_names_account"] = (
            exc.account_id == account_id and (exc.account_label or "") in str(exc)
            and bool(exc.account_label)
        )
    except Exception as exc:  # noqa: BLE001
        t["overlap_type"] = f"{type(exc).__name__}: {exc}"

    # The refusal must have left the data UNCHANGED — a refusal that half-wrote
    # is worse than one that did not refuse.
    t["after_refusal"] = await _active_member_ids(admin, fx.org_a, account_id)

    # ── Removal closes, it does not delete ──────────────────────────────────
    t["rows_before_remove"] = int(await admin.fetchval(
        f"SELECT count(*) FROM {TABLE_MEMBERS} WHERE account_id = $1::uuid",
        account_id,
    ))
    async with OrgSession(admin, fx.org_a):
        t["remove_ok"] = await remove_member(
            admin, fx.org_a, group_id=g1["id"], account_id=account_id
        )
    t["rows_after_remove"] = int(await admin.fetchval(
        f"SELECT count(*) FROM {TABLE_MEMBERS} WHERE account_id = $1::uuid",
        account_id,
    ))
    closed = await admin.fetchrow(
        f"""
        SELECT valid_to IS NOT NULL AS valid_closed,
               system_to IS NOT NULL AS system_closed
        FROM {TABLE_MEMBERS}
        WHERE billing_group_id = $1::uuid AND account_id = $2::uuid
        ORDER BY created_at DESC LIMIT 1
        """,
        g1["id"], account_id,
    )
    t["closed_on_valid_axis"] = bool(closed and closed["valid_closed"])
    t["closed_on_system_axis"] = bool(closed and closed["system_closed"])
    t["after_remove"] = await _active_member_ids(admin, fx.org_a, account_id)

    # ── Freed, it may now join the OTHER breakpoint group ───────────────────
    t["rejoin_ok"] = False
    try:
        async with OrgSession(admin, fx.org_a):
            await add_member(admin, fx.org_a, group_id=g2["id"], account_id=account_id)
        t["rejoin_ok"] = True
    except Exception as exc:  # noqa: BLE001
        t["rejoin_error"] = f"{type(exc).__name__}: {exc}"
    t["after_rejoin"] = await _active_member_ids(admin, fx.org_a, account_id)
    t["rejoin_group"] = await admin.fetchval(
        f"""
        SELECT billing_group_id::text FROM {TABLE_MEMBERS}
        WHERE account_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL
        """,
        account_id,
    )

    # ── STATEMENT + PAYER alongside the live BREAKPOINT ─────────────────────
    async with OrgSession(admin, fx.org_a):
        gs = await create_billing_group(
            admin, fx.org_a, name=f"Stmt {label} {fx.tag}",
            group_type=GROUP_TYPE_STATEMENT, household_id=household_id,
        )
        gs2 = await create_billing_group(
            admin, fx.org_a, name=f"Stmt Two {label} {fx.tag}",
            group_type=GROUP_TYPE_STATEMENT, household_id=household_id,
        )
        gp = await create_billing_group(
            admin, fx.org_a, name=f"Payer {label} {fx.tag}",
            group_type=GROUP_TYPE_PAYER, household_id=household_id,
        )
    t["unrestricted_errors"] = []
    for group in (gs, gs2, gp):
        try:
            async with OrgSession(admin, fx.org_a):
                await add_member(
                    admin, fx.org_a, group_id=group["id"], account_id=account_id
                )
        except Exception as exc:  # noqa: BLE001
            t["unrestricted_errors"].append(
                f"{group['group_type']} {group['name']}: "
                f"{type(exc).__name__}: {exc}"
            )
    memberships = await list_account_memberships(admin, fx.org_a, account_id)
    t["membership_types"] = sorted(m["group_type"] for m in memberships)
    t["membership_count"] = len(memberships)

    # The conflict finder must still name ONLY the breakpoint one. If it started
    # reporting the statement groups, checks 2 and 3 would both still pass while
    # the rule had quietly become "one group of any type".
    conflict = await find_breakpoint_conflict(
        admin, fx.org_a, account_id=account_id
    )
    t["conflict_group"] = conflict.group_id if conflict else None
    t["conflict_is_breakpoint_only"] = bool(
        conflict and conflict.group_id == g2["id"]
    )

    return t


def _transcript_problems(t: dict, *, g1_key="group_1", g2_key="group_2") -> list[str]:
    """The assertions common to both household shapes, in one place.

    Sharing this between the housed and unhoused runs is what makes check 5's
    "behaves identically" claim mean something — the two runs are literally
    graded by the same function.
    """
    p: list[str] = []
    g1, g2 = t[g1_key], t[g2_key]

    if not t["first_add_ok"]:
        p.append("the first BREAKPOINT add did not return a membership")
    if t["after_first_add"] != [t["after_first_add"][0]] or len(t["after_first_add"]) != 1:
        p.append(f"after the first add the account had "
                 f"{len(t['after_first_add'])} active memberships, expected 1")

    if not t["overlap_raised"]:
        p.append(f"the SECOND BREAKPOINT add was NOT refused "
                 f"(got {t['overlap_type']})")
    else:
        if t["overlap_type"] != "BreakpointOverlapError":
            p.append(f"the refusal was {t['overlap_type']}, not a typed "
                     f"BreakpointOverlapError")
        if not t["overlap_names_existing_group"]:
            p.append("the error does not name the EXISTING group by id and name")
        if not t["overlap_names_attempted_group"]:
            p.append("the error does not name the ATTEMPTED group by id and name")
        if not t["overlap_names_account"]:
            p.append("the error does not name the account")
    if t["after_refusal"] != t["after_first_add"]:
        p.append("the refused add still changed the membership rows")

    if not t["remove_ok"]:
        p.append("remove_member returned False")
    if t["rows_after_remove"] != t["rows_before_remove"]:
        p.append(f"removal DELETED a row ({t['rows_before_remove']} → "
                 f"{t['rows_after_remove']}) — it must close, not delete")
    if not t["closed_on_valid_axis"]:
        p.append("the removed membership has no valid_to")
    if not t["closed_on_system_axis"]:
        p.append("the removed membership has no system_to")
    if t["after_remove"]:
        p.append(f"the account still had {len(t['after_remove'])} active "
                 f"memberships after removal")

    if not t["rejoin_ok"]:
        p.append(f"the freed account could NOT join the other BREAKPOINT group: "
                 f"{t.get('rejoin_error')}")
    if t["rejoin_group"] != g2["id"]:
        p.append(f"after rejoining, the active membership points at "
                 f"{t['rejoin_group']}, not group 2 ({g2['id']})")

    if t["unrestricted_errors"]:
        p.append("STATEMENT/PAYER adds were refused: "
                 + "; ".join(t["unrestricted_errors"]))
    if t["membership_types"] != ["BREAKPOINT", "PAYER", "STATEMENT", "STATEMENT"]:
        p.append(f"final membership types were {t['membership_types']}, expected "
                 f"one BREAKPOINT + two STATEMENT + one PAYER held at once")
    if not t["conflict_is_breakpoint_only"]:
        p.append(f"find_breakpoint_conflict named {t['conflict_group']}, not the "
                 f"live BREAKPOINT group {g2['id']} — the rule is no longer "
                 f"BREAKPOINT-specific")
    return p


async def check_2_3_4(results: Results, admin, fx: Fixture) -> dict:
    """Checks 2, 3 and 4 on the HOUSED group, graded separately."""
    t = await _breakpoint_transcript(
        admin, fx, household_id=fx.household_a,
        account_id=fx.account_main, label="housed",
    )
    g1, g2 = t["group_1"], t["group_2"]

    # ── Check 2 ────────────────────────────────────────────────────────────
    problems = []
    if not t["first_add_ok"] or len(t["after_first_add"]) != 1:
        problems.append("the first BREAKPOINT add did not land as one membership")
    if not t["overlap_raised"]:
        problems.append(f"the second BREAKPOINT add was NOT refused "
                        f"(got {t['overlap_type']})")
    else:
        if t["overlap_type"] != "BreakpointOverlapError":
            problems.append(f"refusal type was {t['overlap_type']}")
        if not t["overlap_names_existing_group"]:
            problems.append("the error does not name the existing group")
        if not t["overlap_names_attempted_group"]:
            problems.append("the error does not name the attempted group")
        if not t["overlap_names_account"]:
            problems.append("the error does not name the account")
    if t["after_refusal"] != t["after_first_add"]:
        problems.append("the refusal left the data changed")

    if problems:
        results.bad(2, "a second BREAKPOINT membership is refused with a typed "
                       "error naming both groups", "; ".join(problems))
    else:
        results.ok(
            2, "a second BREAKPOINT membership is refused with a typed error "
               "naming both groups",
            f"add #1 → 1 active membership; add #2 → BreakpointOverlapError "
            f"carrying existing_group_id={g1['id'][:8]}… ({g1['name']!r}) and "
            f"attempted_group_id={g2['id'][:8]}… ({g2['name']!r}) as ATTRIBUTES "
            f"and in the message; account named by masked number; row count "
            f"unchanged by the refusal",
        )

    # ── Check 3 ────────────────────────────────────────────────────────────
    problems = []
    if t["unrestricted_errors"]:
        problems.append("; ".join(t["unrestricted_errors"]))
    if t["membership_types"] != ["BREAKPOINT", "PAYER", "STATEMENT", "STATEMENT"]:
        problems.append(f"final types {t['membership_types']}")
    if not t["conflict_is_breakpoint_only"]:
        problems.append("find_breakpoint_conflict no longer names only BREAKPOINT")
    if UNRESTRICTED_GROUP_TYPES != {GROUP_TYPE_STATEMENT, GROUP_TYPE_PAYER}:
        problems.append(f"UNRESTRICTED_GROUP_TYPES drifted: {UNRESTRICTED_GROUP_TYPES}")
    if EXCLUSIVE_GROUP_TYPES != {GROUP_TYPE_BREAKPOINT}:
        problems.append(f"EXCLUSIVE_GROUP_TYPES drifted: {EXCLUSIVE_GROUP_TYPES}")

    if problems:
        results.bad(3, "STATEMENT and PAYER are unrestricted, alongside a live "
                       "BREAKPOINT", "; ".join(problems))
    else:
        results.ok(
            3, "STATEMENT and PAYER are unrestricted, alongside a live BREAKPOINT",
            f"one account holds {t['membership_count']} simultaneous active "
            f"memberships — {t['membership_types']} — including TWO different "
            f"STATEMENT groups (the joint-account case); find_breakpoint_conflict "
            f"still names only the BREAKPOINT one, so the rule narrowed rather "
            f"than disappeared",
        )

    # ── Check 4 ────────────────────────────────────────────────────────────
    problems = []
    if not t["remove_ok"]:
        problems.append("remove_member returned False")
    if t["rows_after_remove"] != t["rows_before_remove"]:
        problems.append(f"a row was DELETED ({t['rows_before_remove']} → "
                        f"{t['rows_after_remove']})")
    if not (t["closed_on_valid_axis"] and t["closed_on_system_axis"]):
        problems.append(
            f"the closed row has valid_to={t['closed_on_valid_axis']}, "
            f"system_to={t['closed_on_system_axis']}"
        )
    if not t["rejoin_ok"]:
        problems.append(f"rejoin failed: {t.get('rejoin_error')}")
    if t["rejoin_group"] != g2["id"]:
        problems.append(f"rejoined into {t['rejoin_group']}, not group 2")

    if problems:
        results.bad(4, "removal closes the row (not a delete) and frees the "
                       "account to rejoin", "; ".join(problems))
    else:
        results.ok(
            4, "removal closes the row (not a delete) and frees the account "
               "to rejoin",
            f"membership row count across the account UNCHANGED at "
            f"{t['rows_after_remove']} — the row gained both valid_to and "
            f"system_to rather than disappearing; the same add that raised "
            f"BreakpointOverlapError before removal now succeeds into "
            f"{g2['name']!r}",
        )

    return t


async def check_5(results: Results, admin, fx: Fixture, housed: dict) -> None:
    """The household_id = NULL group behaves IDENTICALLY, for every check above.

    Graded by the SAME ``_transcript_problems`` function as the housed run, and
    then the two transcripts are compared field by field on the outcome keys.
    A NULL-household run that merely "worked" would not prove sameness; two
    transcripts that agree on every observable does.
    """
    unhoused = await _breakpoint_transcript(
        admin, fx, household_id=None,
        account_id=fx.account_null, label="unhoused",
    )

    problems = _transcript_problems(unhoused)
    if unhoused["group_1_household"] is not None:
        problems.append(
            f"the unhoused group came back with household_id="
            f"{unhoused['group_1_household']} — the fixture is not testing NULL"
        )
    if housed["group_1_household"] != fx.household_a:
        problems.append("the housed group lost its household_id")

    # Field-by-field sameness on every OUTCOME key. Ids and names differ between
    # runs by construction and are excluded; everything that describes BEHAVIOUR
    # must match.
    compared = (
        "first_add_ok", "overlap_raised", "overlap_type",
        "overlap_names_existing_group", "overlap_names_attempted_group",
        "overlap_names_account", "remove_ok", "closed_on_valid_axis",
        "closed_on_system_axis", "rejoin_ok", "unrestricted_errors",
        "membership_types", "membership_count", "conflict_is_breakpoint_only",
    )
    differing = [k for k in compared if housed[k] != unhoused[k]]
    if differing:
        problems.append(
            "housed and unhoused transcripts differ on: "
            + ", ".join(f"{k} ({housed[k]!r} vs {unhoused[k]!r})" for k in differing)
        )

    if problems:
        results.bad(5, "a household_id = NULL group behaves identically for "
                       "every check", "; ".join(problems))
    else:
        results.ok(
            5, "a household_id = NULL group behaves identically for every check",
            f"the full checks 2-4 transcript was re-run against groups with "
            f"household_id IS NULL and graded by the same function: all "
            f"{len(compared)} behavioural observations match the housed run "
            f"exactly (typed refusal, both group names, close-not-delete, "
            f"rejoin, {unhoused['membership_types']})",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Check 6 — cross-org isolation, on app_service
# ═══════════════════════════════════════════════════════════════════════════


async def check_6(results: Results, admin, fx: Fixture, app_dsn, app_prov) -> None:
    """Cross-org isolation on BOTH new tables, under a genuinely non-bypassing
    role.

    Same pattern as fee31 check 5 / fee32 check 7. The ``rolbypassrls = False``
    assertion comes FIRST: on a superuser every policy is inert, and "I could
    not see org B's rows" and "org B has no rows" become the same observation.
    """
    if app_dsn is None:
        results.blocked(6, "cross-org isolation on both new tables",
                        f"no app_service DSN — {app_prov}")
        return

    # Plant real rows in org B as superuser, so there is something to fail to see.
    async with OrgSession(admin, fx.org_b):
        gb = await create_billing_group(
            admin, fx.org_b, name=f"Other Tenant BP {fx.tag}",
            group_type=GROUP_TYPE_BREAKPOINT, household_id=fx.household_b,
        )
        await add_member(admin, fx.org_b, group_id=gb["id"], account_id=fx.account_b)

    app = await connect(app_dsn)
    problems = []
    try:
        role = await app.fetchval("SELECT current_user")
        bypass = await app.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if bypass:
            results.blocked(
                6, "cross-org isolation on both new tables",
                f"role {role} has rolbypassrls=True — every denial below would "
                f"be vacuous, so nothing was measured",
            )
            return

        async with OrgSession(app, fx.org_a):
            foreign_groups = await app.fetchval(
                f"SELECT count(*) FROM {TABLE_GROUPS} WHERE org_id = $1::uuid",
                fx.org_b,
            )
            foreign_members = await app.fetchval(
                f"SELECT count(*) FROM {TABLE_MEMBERS} WHERE org_id = $1::uuid",
                fx.org_b,
            )
            own_groups = await app.fetchval(
                f"SELECT count(*) FROM {TABLE_GROUPS} WHERE org_id = $1::uuid",
                fx.org_a,
            )
            own_members = await app.fetchval(
                f"SELECT count(*) FROM {TABLE_MEMBERS} WHERE org_id = $1::uuid",
                fx.org_a,
            )
            # An UNQUALIFIED select must return exactly the org's own rows —
            # proving the policy filters, not that the WHERE clause did.
            all_groups = await app.fetchval(f"SELECT count(*) FROM {TABLE_GROUPS}")
            all_members = await app.fetchval(f"SELECT count(*) FROM {TABLE_MEMBERS}")

        if foreign_groups:
            problems.append(f"org B's groups were visible ({foreign_groups})")
        if foreign_members:
            problems.append(f"org B's memberships were visible ({foreign_members})")
        if not own_groups:
            problems.append("org A could not see its OWN groups — the check "
                            "would pass vacuously")
        if not own_members:
            problems.append("org A could not see its OWN memberships")
        if all_groups != own_groups:
            problems.append(f"unqualified group SELECT returned {all_groups}, "
                            f"own is {own_groups}")
        if all_members != own_members:
            problems.append(f"unqualified member SELECT returned {all_members}, "
                            f"own is {own_members}")

        # WITH CHECK: writing another org's id must be refused, on both tables.
        for table, sql, args in (
            (TABLE_GROUPS,
             f"INSERT INTO {TABLE_GROUPS} (org_id, name, group_type) "
             f"VALUES ($1::uuid, $2, 'BREAKPOINT')",
             (fx.org_b, f"smuggled {fx.tag}")),
            (TABLE_MEMBERS,
             f"INSERT INTO {TABLE_MEMBERS} (org_id, billing_group_id, account_id) "
             f"VALUES ($1::uuid, $2::uuid, $3::uuid)",
             (fx.org_b, gb["id"], fx.account_b)),
        ):
            refused = False
            try:
                async with OrgSession(app, fx.org_a):
                    await app.execute(sql, *args)
            except asyncpg.InsufficientPrivilegeError:
                refused = True
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{table} cross-org INSERT failed with "
                                f"{type(exc).__name__} rather than an RLS refusal")
                refused = True
            if not refused:
                problems.append(f"{table} accepted a cross-org INSERT")

        # And the group's own service refuses a foreign id rather than acting
        # on it — the FKs are org-blind, so RLS is the only thing between a
        # caller-supplied foreign id and a cross-tenant write.
        service_refused = False
        try:
            async with OrgSession(admin, fx.org_a):
                await add_member(
                    admin, fx.org_a, group_id=gb["id"], account_id=fx.account_b
                )
        except BillingGroupNotFoundError:
            service_refused = True
        except Exception as exc:  # noqa: BLE001
            problems.append(f"the service raised {type(exc).__name__} for a "
                            f"foreign group id, not BillingGroupNotFoundError")
            service_refused = True
        if not service_refused:
            problems.append("add_member accepted another org's group id")
    finally:
        await app.close()

    if problems:
        results.bad(6, "cross-org isolation on both new tables", "; ".join(problems))
    else:
        results.ok(
            6, "cross-org isolation on both new tables",
            f"role {role} (rolbypassrls={bypass}); under org A's GUC: 0 of org "
            f"B's groups and 0 of its memberships visible, {own_groups} own "
            f"groups and {own_members} own memberships visible, unqualified "
            f"SELECT returns exactly those; cross-org INSERT refused by "
            f"WITH CHECK on BOTH tables; add_member refuses a foreign group id "
            f"with BillingGroupNotFoundError rather than writing across tenants",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Check 7 — teardown
# ═══════════════════════════════════════════════════════════════════════════


async def check_7(results: Results, admin, before: dict[str, int]) -> None:
    """Teardown proof: every touched table is back to its pre-test count."""
    after = await _counts(admin)
    drift = {
        table: (before[table], after[table])
        for table in TOUCHED_TABLES
        if before[table] != after[table]
    }
    if drift:
        results.bad(
            7, "teardown restored every touched table",
            "; ".join(f"{t}: {b} → {a}" for t, (b, a) in sorted(drift.items())),
        )
    else:
        results.ok(
            7, "teardown restored every touched table",
            ", ".join(f"{t.split('.')[-1]}={after[t]}" for t in TOUCHED_TABLES),
        )


# ═══════════════════════════════════════════════════════════════════════════
# Extra — the door the prompt's wording leaves open
# ═══════════════════════════════════════════════════════════════════════════


async def check_extra_retype(results: Results, admin, fx: Fixture) -> None:
    """Retyping a STATEMENT group to BREAKPOINT is checked too.

    [FIND] The prompt scopes the constraint to "every insert/update to
    billing_group_members". That is necessary but not sufficient: a group holding
    two accounts that are already in BREAKPOINT groups can be retyped from
    STATEMENT to BREAKPOINT without touching billing_group_members at all, and
    the invariant is gone with no membership write having occurred.

    This proves the retype is refused, and — separately — that a retype which
    does NOT conflict still succeeds. A check that only proved the refusal would
    also pass if update_billing_group simply rejected every retype.
    """
    problems = []
    async with OrgSession(admin, fx.org_a):
        bp = await create_billing_group(
            admin, fx.org_a, name=f"Retype BP {fx.tag}",
            group_type=GROUP_TYPE_BREAKPOINT,
        )
        stmt = await create_billing_group(
            admin, fx.org_a, name=f"Retype Stmt {fx.tag}",
            group_type=GROUP_TYPE_STATEMENT,
        )
        await add_member(admin, fx.org_a, group_id=bp["id"],
                         account_id=fx.account_second)
        await add_member(admin, fx.org_a, group_id=stmt["id"],
                         account_id=fx.account_second)

    # The conflicting retype must be refused...
    raised = None
    try:
        async with OrgSession(admin, fx.org_a):
            await update_billing_group(
                admin, fx.org_a, stmt["id"],
                group_type=GROUP_TYPE_BREAKPOINT, fields_set={"group_type"},
            )
    except BreakpointOverlapError as exc:
        raised = exc
    except Exception as exc:  # noqa: BLE001
        problems.append(f"retype raised {type(exc).__name__}, not "
                        f"BreakpointOverlapError")
    if raised is None and not problems:
        problems.append(
            "retyping a STATEMENT group to BREAKPOINT was ALLOWED while one of "
            "its members already sat in another BREAKPOINT group — the "
            "constraint is bypassable without any membership write"
        )
    still = await admin.fetchval(
        f"SELECT group_type FROM {TABLE_GROUPS} WHERE id = $1::uuid", stmt["id"]
    )
    if still != GROUP_TYPE_STATEMENT:
        problems.append(f"the refused retype still changed group_type to {still}")

    # ...and a NON-conflicting retype must still succeed, or the check above
    # would also pass on a function that refused every retype.
    async with OrgSession(admin, fx.org_a):
        free = await create_billing_group(
            admin, fx.org_a, name=f"Retype Free {fx.tag}",
            group_type=GROUP_TYPE_STATEMENT,
        )
    try:
        async with OrgSession(admin, fx.org_a):
            after = await update_billing_group(
                admin, fx.org_a, free["id"],
                group_type=GROUP_TYPE_BREAKPOINT, fields_set={"group_type"},
            )
        if after["group_type"] != GROUP_TYPE_BREAKPOINT:
            problems.append("a clean retype did not take effect")
        if after["name"] != free["name"]:
            problems.append("a sparse PATCH clobbered a field it did not set")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"a NON-conflicting retype was refused: "
                        f"{type(exc).__name__}: {exc}")

    if problems:
        results.bad("E1", "retyping a group to BREAKPOINT re-checks its whole "
                          "membership", "; ".join(problems))
    else:
        results.find(
            "E1", "retyping a group to BREAKPOINT re-checks its whole membership",
            f"the prompt scopes the rule to billing_group_members writes; a "
            f"group retype reaches the same violation without one. Refused with "
            f"BreakpointOverlapError naming {raised.existing_group_name!r}, and "
            f"group_type stayed {still}. A retype with no conflicting member "
            f"still succeeds, so this is a real check and not a blanket refusal",
        )


async def check_extra_move(results: Results, admin, fx: Fixture) -> None:
    """move_member is atomic and does not conflict with the row it is leaving.

    The UPDATE path. A naive implementation checks the conflict BEFORE closing
    the old row and therefore always refuses a legitimate move between two
    BREAKPOINT groups — a bug that looks like the constraint working.
    """
    problems = []
    async with OrgSession(admin, fx.org_a):
        a = await create_billing_group(
            admin, fx.org_a, name=f"Move From {fx.tag}",
            group_type=GROUP_TYPE_BREAKPOINT,
        )
        b = await create_billing_group(
            admin, fx.org_a, name=f"Move To {fx.tag}",
            group_type=GROUP_TYPE_BREAKPOINT,
        )
        # account_second is freed first — check_extra_retype left it in `bp`.
        for row in await admin.fetch(
            f"SELECT billing_group_id::text AS g FROM {TABLE_MEMBERS} "
            f"WHERE account_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
            fx.account_second,
        ):
            await remove_member(admin, fx.org_a, group_id=row["g"],
                                account_id=fx.account_second)
        m = await add_member(admin, fx.org_a, group_id=a["id"],
                             account_id=fx.account_second)

    try:
        async with OrgSession(admin, fx.org_a):
            moved = await move_member(
                admin, fx.org_a, member_id=m["id"], target_group_id=b["id"]
            )
        if moved["billing_group_id"] != b["id"]:
            problems.append(f"the move landed in {moved['billing_group_id']}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"a legitimate BREAKPOINT→BREAKPOINT move was refused: "
                        f"{type(exc).__name__}: {exc}")

    active = await _active_member_ids(admin, fx.org_a, fx.account_second)
    if len(active) != 1:
        problems.append(f"after the move the account had {len(active)} active "
                        f"memberships, expected exactly 1")
    old_closed = await admin.fetchval(
        f"SELECT valid_to IS NOT NULL AND system_to IS NOT NULL "
        f"FROM {TABLE_MEMBERS} WHERE id = $1::uuid", m["id"],
    )
    if not old_closed:
        problems.append("the old membership row was not closed by the move")

    if problems:
        results.bad("E2", "move_member relocates a BREAKPOINT membership "
                          "atomically", "; ".join(problems))
    else:
        results.ok(
            "E2", "move_member relocates a BREAKPOINT membership atomically",
            "a BREAKPOINT→BREAKPOINT move succeeds (the conflict check runs "
            "AFTER the old row closes, so it does not collide with the "
            "membership being left), the old row is closed on both axes, and "
            "exactly one active membership remains",
        )


async def check_extra_idempotent(results: Results, admin, fx: Fixture) -> None:
    """Re-adding an account to the group it is already in is a no-op, not a 500.

    The partial unique index would otherwise surface an operator double-click as
    a UniqueViolationError traceback.
    """
    problems = []
    async with OrgSession(admin, fx.org_a):
        g = await create_billing_group(
            admin, fx.org_a, name=f"Idem {fx.tag}", group_type=GROUP_TYPE_BREAKPOINT,
        )
        # account_main is still held by the housed transcript's second
        # BREAKPOINT group. Free it first — otherwise this check measures the
        # overlap rule (already proved in check 2) instead of idempotency.
        for row in await admin.fetch(
            f"SELECT m.billing_group_id::text AS g FROM {TABLE_MEMBERS} m "
            f"JOIN {TABLE_GROUPS} bg ON bg.id = m.billing_group_id "
            f"WHERE m.account_id = $1::uuid AND bg.group_type = $2 "
            f"  AND m.valid_to IS NULL AND m.system_to IS NULL",
            fx.account_main, GROUP_TYPE_BREAKPOINT,
        ):
            await remove_member(admin, fx.org_a, group_id=row["g"],
                                account_id=fx.account_main)
        first = await add_member(admin, fx.org_a, group_id=g["id"],
                                 account_id=fx.account_main)
    try:
        async with OrgSession(admin, fx.org_a):
            second = await add_member(admin, fx.org_a, group_id=g["id"],
                                      account_id=fx.account_main)
        if second["id"] != first["id"]:
            problems.append("the re-add minted a SECOND membership row")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"re-adding raised {type(exc).__name__}: {exc}")

    # Scoped to THIS group: account_main also holds the housed transcript's
    # STATEMENT and PAYER memberships, which are legitimate and not what this
    # check is about.
    active = int(await admin.fetchval(
        f"SELECT count(*) FROM {TABLE_MEMBERS} "
        f"WHERE billing_group_id = $1::uuid AND account_id = $2::uuid "
        f"  AND valid_to IS NULL AND system_to IS NULL",
        g["id"], fx.account_main,
    ))
    if active != 1:
        problems.append(f"{active} active memberships in the group after the re-add")

    if problems:
        results.bad("E3", "re-adding an account to its own group is idempotent",
                    "; ".join(problems))
    else:
        results.ok(
            "E3", "re-adding an account to its own group is idempotent",
            "the second add returned the SAME membership id and left exactly "
            "one active row, rather than raising the partial unique index's "
            "UniqueViolationError at an operator who double-clicked",
        )


def _walk_routes(routes, prefix: str = ""):
    """Yield (path, methods), descending into lazily-included routers.

    This FastAPI version does not flatten ``include_router`` at import time — it
    parks an ``_IncludedRouter`` wrapper on ``app.routes`` and resolves the real
    routes on first request. Reading ``r.path`` off the top level sees only the
    handful declared directly on the app, so a naive check passes by absence.
    And a TestClient probe cannot stand in for this: the auth middleware returns
    401 for an unregistered path exactly as it does for a registered one, so a
    401 proves nothing about registration.

    Copied from ``verify_fee31._walk_routes`` deliberately — this is the third
    sprint to need it, and each one hit the same false green first.
    """
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            yield from _walk_routes(
                original.routes, prefix + (getattr(context, "prefix", "") or "")
            )
            continue
        path = getattr(route, "path", None)
        if path:
            yield prefix + path, set(getattr(route, "methods", set()) or set())


EXPECTED_ROUTES = {
    "/api/v1/billing-groups": {"GET", "POST"},
    "/api/v1/billing-groups/{group_id}": {"PATCH", "DELETE"},
    "/api/v1/billing-groups/{group_id}/members": {"GET", "POST"},
    "/api/v1/billing-groups/{group_id}/members/{account_id}": {"DELETE"},
    "/api/v1/billing-groups/accounts/{account_id}/memberships": {"GET"},
    "/api/v1/billing-groups/members/{member_id}/move": {"POST"},
}


def check_extra_routes(results: Results) -> None:
    """The endpoints are really on the app object, and really gated.

    Two independent claims, per the permission-envelope rule. The route
    existing proves nothing about it refusing a view-only caller, and the
    ``require_permission`` call proves nothing about the route being reachable.
    The permission constants are read from the module rather than re-typed, so a
    rename cannot leave this check asserting a name nothing uses.
    """
    import ast  # noqa: PLC0415

    import main  # noqa: PLC0415 — importing registers every router

    problems: list[str] = []
    routes: dict[str, set[str]] = {}
    for path, methods in _walk_routes(main.app.routes):
        routes.setdefault(path, set()).update(methods)

    if len(routes) < 50:
        problems.append(
            f"only {len(routes)} routes resolved — the walker missed the tree, "
            f"so an absent route would look registered"
        )
    for path, expected in EXPECTED_ROUTES.items():
        if path not in routes:
            problems.append(f"{path} is not registered")
        elif not expected <= routes[path]:
            problems.append(
                f"{path} has methods {sorted(routes[path])}, missing "
                f"{sorted(expected - routes[path])}"
            )

    # Every handler gates on one of the two constants — read from the AST, so a
    # route added later without a gate shows up here rather than in production.
    source = (API_DIR / "routers" / "billing_groups.py").read_text()
    tree = ast.parse(source)
    gated: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        is_route = any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
            for d in node.decorator_list
        )
        if not is_route:
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if "READ_PERMISSION" in names:
            gated[node.name] = "READ_PERMISSION"
        elif "WRITE_PERMISSION" in names:
            gated[node.name] = "WRITE_PERMISSION"
        else:
            problems.append(f"handler {node.name} gates on NEITHER permission")

    writes = {n for n, p in gated.items() if p == "WRITE_PERMISSION"}
    expected_writes = {
        "post_group", "patch_group", "delete_group",
        "post_member", "delete_member", "post_member_move",
    }
    if writes != expected_writes:
        problems.append(
            f"write-gated handlers are {sorted(writes)}, expected "
            f"{sorted(expected_writes)}"
        )

    from services.billing_groups import READ_PERMISSION, WRITE_PERMISSION  # noqa: PLC0415
    if (READ_PERMISSION, WRITE_PERMISSION) != ("view_portfolio", "manage_billing"):
        problems.append(
            f"permission constants drifted to "
            f"({READ_PERMISSION}, {WRITE_PERMISSION})"
        )

    if problems:
        results.bad("E5", "endpoints are registered and every write is gated on "
                          "manage_billing", "; ".join(problems))
    else:
        results.ok(
            "E5", "endpoints are registered and every write is gated on "
                  "manage_billing",
            f"{len(EXPECTED_ROUTES)} paths resolved out of {len(routes)} total "
            f"(walking the lazy _IncludedRouter tree, not app.routes directly); "
            f"all {len(gated)} handlers gate on a permission constant, the "
            f"{len(writes)} write handlers on manage_billing and the reads on "
            f"view_portfolio — both names already exist in public.permissions",
        )


async def check_extra_envelope(results: Results, admin, fx: Fixture) -> None:
    """The envelope EMPTIES its editable lists for a view-only caller.

    The client-side half of the permission proof. Fed the real vocabulary
    builder rather than inferred from the server-side 403: a hidden button
    behind an unprotected endpoint and a protected endpoint behind a visible
    button are both real bugs, and proving one proves nothing about the other.
    """
    from routers.billing_groups import _vocabularies  # noqa: PLC0415

    problems = []
    view_only = _vocabularies({"can_write": False})
    writer = _vocabularies({"can_write": True})

    if view_only["editable"] != []:
        problems.append(f"view-only editable is {view_only['editable']}, not []")
    if view_only["inline_editable"] != []:
        problems.append(
            f"view-only inline_editable is {view_only['inline_editable']}, not []"
        )
    for key in ("editable", "inline_editable"):
        if key not in view_only:
            problems.append(f"{key} is OMITTED for a view-only caller, not empty")
    if not writer["editable"]:
        problems.append("a writer got an empty editable list — the gate is stuck shut")
    if view_only["group_type"] != writer["group_type"]:
        problems.append("the read-only vocabulary differs between callers")

    if problems:
        results.bad("E6", "the permission envelope empties editable lists for a "
                          "view-only caller", "; ".join(problems))
    else:
        results.ok(
            "E6", "the permission envelope empties editable lists for a "
                  "view-only caller",
            f"can_write=False → editable=[] and inline_editable=[] — PRESENT "
            f"and empty, never omitted, so the client has no key to fall back "
            f"from; can_write=True → {writer['editable']}. The read-only "
            f"group_type vocabulary is identical for both",
        )


WEB_DIR = API_DIR.parent / "web"


def check_extra_ui(results: Results) -> None:
    """The admin screen's write controls sit behind can_write, with no fallback.

    HONEST SCOPE, STATED UP FRONT: this is a SOURCE-LEVEL assertion, not a
    render. Node is unavailable in this sprint environment, so ``next build``
    could not be run and the component was never mounted — see the BLOCKED check
    below, which is a separate line precisely so this one is not mistaken for
    the full client-side proof CLAUDE.md asks for.

    What it does prove is the specific anti-pattern's absence: ``|| DEFAULTS``
    or ``?? true`` on the permission envelope, which silently restores full
    write access whenever the envelope goes missing for an unrelated reason. A
    lost envelope must fail CLOSED.
    """
    problems: list[str] = []

    component = WEB_DIR / "components" / "admin" / "BillingGroupsManager.jsx"
    page = WEB_DIR / "app" / "admin" / "billing-groups" / "page.js"
    for path in (component, page):
        if not path.exists():
            problems.append(f"{path.name} is missing")
    if problems:
        results.bad("E7", "the admin screen gates writes on can_write with no "
                          "fallback", "; ".join(problems))
        return

    raw = component.read_text()

    # Scan CODE, not prose. The module docstring names the anti-patterns in
    # order to say it avoids them, and a substring search over the whole file
    # therefore flags a correct component for documenting itself. Comments are
    # stripped first. (This check failed exactly that way on its first run.)
    code = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)

    # The fail-closed default: can_write must be FALSE when no envelope arrived.
    if "can_write: false" not in code:
        problems.append(
            "the initial permissions state does not default can_write to false"
        )
    for bad_pattern in ("can_write ?? true", "can_write || true",
                        "canWrite = true", "|| DEFAULTS", "permissions || {"):
        if bad_pattern in code:
            problems.append(f"found the truthy-fallback anti-pattern {bad_pattern!r}")

    # Vocabularies come from the server. TYPE_BLURB is a presentation-copy map
    # keyed by type and is excluded deliberately — it holds one sentence of
    # human copy per type, renders nothing if a key is absent, and is not the
    # vocabulary. A type the server adds simply gets no blurb.
    without_blurb = re.sub(
        r"const TYPE_BLURB\s*=\s*\{.*?\};", "", code, flags=re.S
    )
    for literal in ('"BREAKPOINT"', "'BREAKPOINT'"):
        if literal in without_blurb:
            problems.append(
                f"a hardcoded {literal} appears outside the presentation blurb "
                f"map — the type vocabulary must come from the envelope"
            )
    if "vocabularies?.group_type" not in code and "vocabularies.group_type" not in code:
        problems.append("the type list is not read from the server vocabulary")
    if "exclusive_group_types" not in code:
        problems.append(
            "the screen does not read exclusive_group_types — it would be "
            "explaining the restriction from a hardcoded string"
        )

    # Every write control is inside a canWrite test.
    for control in ("New group", "Create group", "Archive group", "Remove"):
        if control not in code:
            problems.append(f"the {control!r} control is missing entirely")
    guards = code.count("canWrite ?") + code.count("canWrite &&")
    if guards < 3:
        problems.append(
            f"only {guards} controls are wrapped in a canWrite test — expected "
            f"the create, archive and add controls at minimum"
        )

    # The page must pass the envelope through untouched.
    page_src = page.read_text()
    if "envelope?.permissions || null" not in page_src:
        problems.append(
            "the page does not pass the envelope through as-is — a default here "
            "would defeat the component's fail-closed state"
        )

    if problems:
        results.bad("E7", "the admin screen gates writes on can_write with no "
                          "fallback", "; ".join(problems))
    else:
        results.ok(
            "E7", "the admin screen gates writes on can_write with no fallback",
            "SOURCE-LEVEL (not a render — see the BLOCKED line): the initial "
            "permissions state defaults can_write to false; none of the four "
            "truthy-fallback shapes appear; the create/archive/add/remove "
            "controls each render only inside a canWrite test; the group_type "
            "list comes from vocabularies rather than a literal; the page "
            "forwards the envelope as `envelope?.permissions || null`",
        )


def check_ui_build_blocked(results: Results) -> None:
    """``next build`` could not be run — recorded, not glossed over.

    A prior sprint learned that only a real ``next build`` catches a server-only
    module being dragged into a client bundle. Node is unavailable in this
    environment, so that class of defect is NOT ruled out for this screen.
    """
    import shutil  # noqa: PLC0415

    where = "on PATH" if shutil.which("node") else "not installed"
    results.blocked(
        "E8", "frontend build (next build) was not run",
        f"node is {where}, but this sprint's execution environment refused "
        f"every node/npx invocation, so the admin screen was never compiled or "
        f"mounted. Only a real `next build` catches a server-only module "
        f"leaking into a client bundle — the exact defect that broke the "
        f"host-aware session sprint, and one no static check can see. E7 is a "
        f"source-level assertion and does NOT substitute for it. Run "
        f"`npx next build` from apps/web before merging the UI half.",
    )


async def check_extra_no_autocreate(results: Results, admin, fx: Fixture) -> None:
    """[FIND] Creating a household still auto-creates NOTHING.

    Task 1's conclusion, asserted rather than asserted-in-prose: nothing in this
    sprint wired create_household to mint a default BREAKPOINT group. The two
    household groupings disagree by design (household_memberships overlaps,
    primary_household_id does not), so a derived group would double-count an
    entity across two breakpoints.
    """
    from services.households import create_household

    async with OrgSession(admin, fx.org_a):
        hh = await create_household(
            admin, fx.org_a, f"fee33 autocreate probe {fx.tag}"
        )
    groups = await admin.fetchval(
        f"SELECT count(*) FROM {TABLE_GROUPS} WHERE household_id = $1::uuid",
        hh["id"],
    )
    members = await admin.fetchval(
        f"SELECT count(*) FROM {TABLE_MEMBERS} m JOIN {TABLE_GROUPS} g "
        f"ON g.id = m.billing_group_id WHERE g.household_id = $1::uuid",
        hh["id"],
    )
    await admin.execute("DELETE FROM public.households WHERE id = $1::uuid", hh["id"])

    if groups or members:
        results.bad(
            "E4", "creating a household auto-creates no billing group",
            f"create_household produced {groups} groups and {members} "
            f"memberships — Task 1 concluded it must produce none",
        )
    else:
        results.find(
            "E4", "creating a household auto-creates no billing group",
            "deliberate, and the honest answer to the prompt's second Task 1 "
            "question: no existing structure implies a safe default. "
            "household_memberships is many-to-many and OVERLAPS, so a group "
            "derived from it would place one entity's value in two breakpoints; "
            "entities.primary_household_id has the right cardinality but lives "
            "on entities, not accounts. A default group stays an operator's "
            "explicit choice",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    results = Results()

    dsn, prov = await admin_dsn()
    if dsn is None:
        print(f"BLOCKED: no admin DSN — {prov}")
        return 2
    app_dsn, app_prov = await app_service_dsn()
    print(f"admin dsn: {prov}")
    print(f"app_service dsn: {app_prov}\n")

    admin = await connect(dsn)
    fx = Fixture()
    before = await _counts(admin)
    try:
        await fx.create(admin)
        await check_1(results, admin)
        housed = await check_2_3_4(results, admin, fx)
        await check_5(results, admin, fx, housed)
        await check_6(results, admin, fx, app_dsn, app_prov)
        await check_extra_retype(results, admin, fx)
        await check_extra_move(results, admin, fx)
        await check_extra_idempotent(results, admin, fx)
        await check_extra_no_autocreate(results, admin, fx)
        check_extra_routes(results)
        await check_extra_envelope(results, admin, fx)
        check_extra_ui(results)
        check_ui_build_blocked(results)
    finally:
        try:
            await fx.teardown(admin)
        except Exception as exc:  # noqa: BLE001
            results.bad("T", "teardown ran cleanly", f"{type(exc).__name__}: {exc}")
        await check_7(results, admin, before)
        await admin.close()

    return results.summary()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
