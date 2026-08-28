"""Sprint fee34 verification — fee schedule catalog, versioning, assignment.

Pass/fail only, no prompts, no interactive input. Run:

    python3 scripts/verify_fee34.py


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Two disposable orgs, never the real ones.** Every fixture lives under orgs
  this run creates and deletes. Nothing writes to the default org.

* **RLS is proved on ``app_service``, never on ``postgres``.** ``postgres`` has
  ``rolbypassrls`` and every isolation check run on it passes vacuously. Check
  7 asserts ``rolbypassrls = False`` on the role it uses BEFORE it trusts a
  single denial — otherwise "I could not see the other org's row" and "there
  was no row" are the same observation.

* **Check 3 compares a SNAPSHOT of the approved row, field by field, before
  and after the fork.** Asserting only that a new row appeared would pass even
  if the fork had also mutated the original. The assignment pointing at
  version 1 is re-resolved afterwards through the real resolver, not merely
  counted, because "the row still exists" and "the assignment still resolves
  to it" are different claims and only the second one matters.

* **Check 2 proves each tier failure with a DISTINCT type.** A gap, an
  overlap, and two open-ended tiers are three different mistakes. A single
  "tiers are invalid" error would let a test that only ever built gaps claim
  it had proved the other two, so the assertion is on the error CLASS and
  ``code``, not on "some error was returned".

* **Check 4 proves the negative as hard as the positive.** The schedule is
  re-read from the database after the refusal — a refusal that raised but had
  already flipped the status would pass a check that only caught the
  exception.

* **Check 4b measures the thing the prompt assumes and Task 1 disproved.**
  ``minimum_fee`` without ``minimum_fee_scope`` cannot be made to exist as a
  DRAFT at all: ``fee_schedules_minimum_fee_scope_required`` refuses the
  INSERT. So the service's better message cannot be proved by submitting such
  a row — it is proved by showing the validator catches it in memory AND that
  the database really does refuse the raw insert. Skipping the second half
  would leave the validator's value unproven.

* **Check 6 puts all three assignments in place SIMULTANEOUSLY**, then walks
  them down by ending the winner, so inclusion and exclusion are both proved
  on the same dataset. Two assignments would prove an ordering; three prove a
  ladder.

* **Teardown is by fixture org id, with an exact before/after row count as the
  backstop.** Never a TRUNCATE. The six fee tables hold no production rows
  today, which is precisely when a truncate looks safe and starts being a
  data-loss bug the moment they do.
"""

from __future__ import annotations

import asyncio
import glob
import pathlib
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent

for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncpg  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from services.fee_schedules import (  # noqa: E402
    SCOPE_PRECEDENCE,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_RETIRED,
    FeeScheduleError,
    FeeScheduleInvalid,
    ScheduleStatusError,
    ScopeIdRequiredError,
    ScopeLinkError,
    create_assignment,
    create_schedule,
    end_assignment,
    get_schedule,
    load_schedule,
    resolve_assignment_for_account,
    retire_schedule,
    submit_for_approval,
    update_schedule,
)
from services.fee_validation import (  # noqa: E402
    ORDERING_STEPS,
    ApprovedByRequiredError,
    ExclusionAltScheduleError,
    ExclusionFlatAmountError,
    MinimumFeeScopeError,
    MoneyTypeError,
    OrderingPolicyError,
    ReasonRequiredError,
    TierGapError,
    TierOverlapError,
    TierUnboundedError,
    validate_credit,
    validate_discount,
    validate_exclusion,
    validate_schedule,
    validate_tiers,
)

FEE_TABLES = (
    "public.fee_schedules",
    "public.fee_schedule_tiers",
    "public.fee_assignments",
    "public.fee_exclusions",
    "public.fee_discounts",
    "public.fee_credits",
)

#: Every table this run writes to. Check 8 compares each one's count before and
#: after. Listed explicitly rather than derived, so a table the script starts
#: touching without being added here shows up as a review question.
TOUCHED_TABLES = FEE_TABLES + (
    "public.billing_group_members",
    "public.billing_groups",
    "public.accounts",
    "public.documents",
    "public.entities",
    "public.households",
    "public.organizations",
)

#: The policy shape check 1 requires, introspected in Task 1 from the deployed
#: fee tables and identical to fee33's. Compared after whitespace folding —
#: Postgres reformats an expression when it stores it, so a literal string
#: compare fails on formatting alone.
EXPECTED_POLICY = (
    "((org_id = (NULLIF(current_setting('app.current_org_id'::text, true), "
    "''::text))::uuid) OR (current_setting('app.is_super_admin'::text, true) "
    "= 'true'::text))"
)

#: The CHECK constraints Task 1 measured, as (table, constraint, substrings the
#: definition must contain). Substrings rather than a whole-text compare so a
#: harmless reformat does not fail the check, but every vocabulary value the
#: prompt names is asserted individually — a constraint that merely EXISTS
#: proves nothing about what it admits.
EXPECTED_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("fee_schedules", "fee_schedules_status_check",
     ("DRAFT", "APPROVED", "RETIRED")),
    ("fee_schedules", "fee_schedules_minimum_fee_scope_required",
     ("minimum_fee", "minimum_fee_scope")),
    ("fee_schedules", "fee_schedules_minimum_fee_scope_check",
     ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD")),
    ("fee_schedules", "fee_schedules_product_type_check",
     ("ASSET_MANAGEMENT", "SPV", "STRUCTURED_INVESTMENT", "PLANNING",
      "CLUB_DUES", "TRANSACTION")),
    ("fee_schedules", "fee_schedules_rate_type_check",
     ("BPS", "FLAT", "HYBRID", "HOURLY", "PER_ACCOUNT")),
    ("fee_schedules", "fee_schedules_tier_method_check",
     ("GRADUATED", "CLIFF", "BLENDED_PUBLISHED")),
    ("fee_schedules", "fee_schedules_billing_frequency_check",
     ("MONTHLY", "QUARTERLY", "SEMIANNUAL", "ANNUAL")),
    ("fee_schedules", "fee_schedules_billing_timing_check",
     ("ADVANCE", "ARREARS")),
    ("fee_schedules", "fee_schedules_valuation_method_check",
     ("PERIOD_END", "PERIOD_START", "AVG_DAILY", "AVG_MONTH_END")),
    ("fee_schedules", "fee_schedules_proration_method_check",
     ("CALENDAR_DAYS", "BUSINESS_DAYS", "NONE")),
    ("fee_schedules", "fee_schedules_cash_treatment_check",
     ("INCLUDE", "EXCLUDE", "EXCLUDE_ABOVE_PCT")),
    ("fee_schedules", "fee_schedules_margin_treatment_check",
     ("IGNORE", "REDUCE_BILLABLE")),
    ("fee_schedule_tiers", "fee_schedule_tiers_bounds_check",
     ("upper_bound", "lower_bound")),
    ("fee_schedule_tiers", "fee_schedule_tiers_rate_or_flat_check",
     ("rate_bps", "flat_amount")),
    ("fee_assignments", "fee_assignments_scope_type_check",
     ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD", "ENTITY", "ORG_DEFAULT")),
    ("fee_assignments", "fee_assignments_scope_id_required",
     ("ORG_DEFAULT", "scope_id")),
    ("fee_exclusions", "fee_exclusions_treatment_check",
     ("EXCLUDE", "REDUCED_RATE", "FLAT")),
    ("fee_exclusions", "fee_exclusions_reduced_rate_requires_schedule",
     ("REDUCED_RATE", "alt_fee_schedule_id")),
    ("fee_exclusions", "fee_exclusions_flat_requires_amount",
     ("FLAT", "flat_amount")),
    ("fee_exclusions", "fee_exclusions_basis_type_check",
     ("SECURITY", "ASSET_CLASS", "ACCOUNT", "HELD_AWAY", "CASH",
      "POSITION_TAG")),
    ("fee_exclusions", "fee_exclusions_scope_type_check",
     ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD", "ORG")),
    ("fee_discounts", "fee_discounts_discount_type_check",
     ("PCT_OFF", "BPS_OFF", "DOLLAR_CREDIT", "FEE_HOLIDAY",
      "SCHEDULE_OVERRIDE")),
    ("fee_discounts", "fee_discounts_applies_to_check",
     ("GROSS", "NET_OF_CREDITS")),
    ("fee_discounts", "fee_discounts_scope_type_check",
     ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD")),
    ("fee_credits", "fee_credits_source_check",
     ("12B1", "SUB_TA", "SPV_MGMT_FEE_OFFSET", "SI_EMBEDDED_FEE_OFFSET",
      "MODEL_FEE_OFFSET")),
    ("fee_credits", "fee_credits_offset_pct_range", ("offset_pct",)),
    ("fee_credits", "fee_credits_scope_type_check",
     ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD")),
)

#: A valid, contiguous three-tier ladder. 0 → 1M → 5M → open.
GOOD_TIERS = [
    {"tier_seq": 1, "lower_bound": Decimal("0"),
     "upper_bound": Decimal("1000000"), "rate_bps": Decimal("100")},
    {"tier_seq": 2, "lower_bound": Decimal("1000000"),
     "upper_bound": Decimal("5000000"), "rate_bps": Decimal("75")},
    {"tier_seq": 3, "lower_bound": Decimal("5000000"),
     "upper_bound": None, "rate_bps": Decimal("50")},
]

#: A schedule definition that passes validation. Reused so every check that is
#: not ABOUT validation starts from a known-clean schedule.
GOOD_DEFINITION = {
    "name": "fee34 Verify Standard",
    "product_type": "ASSET_MANAGEMENT",
    "rate_type": "BPS",
    "tier_method": "GRADUATED",
    "billing_frequency": "QUARTERLY",
    "billing_timing": "ARREARS",
    "valuation_method": "PERIOD_END",
    "currency": "USD",
}


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
    then denies everything and the script would read its own mistake as an RLS
    finding.
    """

    __slots__ = ("_conn", "_org_id", "_super", "_tr")

    def __init__(self, conn, org_id: str | None, *, is_super_admin: bool = False):
        self._conn = conn
        self._org_id = "" if org_id is None else str(org_id)
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
    """Two disposable orgs.

    Org A carries every positive case. Org B exists only so cross-org isolation
    has something real to fail to see.

    Org A's scope rows, and why each is there:

      account_main    the account check 6 resolves. Belongs to household_a and
                      entity_a, so all four non-default scopes are reachable
                      from it and the ladder is genuinely three-deep.
      group_open      an open BILLING_GROUP — check 5's positive case.
      group_closed    a group with valid_to set. Check 5's 'closed' case, which
                      is the one a bare existence check would let through.
      document_a      an agreement document, so the FK on
                      agreement_document_id is exercised rather than left NULL.
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
        self.account_b = str(uuid.uuid4())

        self.group_open = str(uuid.uuid4())
        self.group_closed = str(uuid.uuid4())
        self.document_a = str(uuid.uuid4())

        #: A uuid that is deliberately never inserted anywhere.
        self.group_ghost = str(uuid.uuid4())

    def code(self, suffix: str) -> str:
        return f"FEE34-{self.tag.upper()}-{suffix}"

    async def create(self, conn) -> None:
        for org_id, slug in ((self.org_a, "a"), (self.org_b, "b")):
            await conn.execute(
                "INSERT INTO public.organizations (id, name, slug) "
                "VALUES ($1::uuid, $2, $3) ON CONFLICT (id) DO NOTHING",
                org_id, f"fee34 verify {slug} {self.tag}",
                f"fee34-verify-{slug}-{self.tag}",
            )

        for household_id, org_id, name in (
            (self.household_a, self.org_a, "Verify Household"),
            (self.household_b, self.org_b, "Other Tenant Household"),
        ):
            await conn.execute(
                "INSERT INTO public.households (id, org_id, name) "
                "VALUES ($1::uuid, $2::uuid, $3) ON CONFLICT (id) DO NOTHING",
                household_id, org_id, f"fee34 {name} {self.tag}",
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
                entity_id, org_id, f"fee34 {name} {self.tag}", household_id,
            )

        for account_id, org_id, household_id, entity_id, label in (
            (self.account_main, self.org_a, self.household_a, self.entity_a, "MAIN"),
            (self.account_b, self.org_b, self.household_b, self.entity_b, "OTHER"),
        ):
            await conn.execute(
                """
                INSERT INTO public.accounts
                    (id, org_id, account_number_masked, account_number_hash,
                     custodian_code, registration_type, tax_status,
                     primary_entity_id, household_id)
                VALUES ($1::uuid, $2::uuid, $3, $4, 'fee34_test', 'individual',
                        'taxable', $5::uuid, $6::uuid)
                ON CONFLICT (id) DO NOTHING
                """,
                account_id, org_id, f"****{label}", f"hash-{account_id}",
                entity_id, household_id,
            )

        for group_id, name, closed in (
            (self.group_open, "Open Breakpoint", False),
            (self.group_closed, "Closed Breakpoint", True),
        ):
            await conn.execute(
                "INSERT INTO public.billing_groups "
                "  (id, org_id, name, group_type, household_id, valid_to) "
                "VALUES ($1::uuid, $2::uuid, $3, 'BREAKPOINT', $4::uuid, "
                "        CASE WHEN $5::boolean THEN now() ELSE NULL END) "
                "ON CONFLICT (id) DO NOTHING",
                group_id, self.org_a, f"fee34 {name} {self.tag}",
                self.household_a, closed,
            )

        # Membership in the OPEN group, so check 6's BILLING_GROUP rung is
        # reachable from account_main rather than being an unreached branch.
        await conn.execute(
            "INSERT INTO public.billing_group_members "
            "  (org_id, billing_group_id, account_id) "
            "VALUES ($1::uuid, $2::uuid, $3::uuid)",
            self.org_a, self.group_open, self.account_main,
        )

        await conn.execute(
            "INSERT INTO public.documents (id, org_id, original_filename) "
            "VALUES ($1::uuid, $2::uuid, $3) ON CONFLICT (id) DO NOTHING",
            self.document_a, self.org_a, f"fee34-agreement-{self.tag}.pdf",
        )

    async def teardown(self, conn) -> None:
        """FK-safe order, scoped to THIS run's two org ids. Never a TRUNCATE.

        Runs in a ``finally`` so a failed check still cleans up: two disposable
        orgs left behind per failed run accumulate into exactly the orphan mess
        a prior sprint had to sweep by hand.
        """
        orgs = [self.org_a, self.org_b]
        for statement in (
            "DELETE FROM public.fee_assignments   WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.fee_exclusions    WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.fee_discounts     WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.fee_credits       WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.fee_schedule_tiers WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.fee_schedules     WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.billing_group_members WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.billing_groups    WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.documents         WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.account_owners    WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.accounts          WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.household_memberships WHERE household_id = ANY("
            "  SELECT id FROM public.households WHERE org_id = ANY($1::uuid[]))",
            "DELETE FROM public.entities          WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.households        WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.organizations     WHERE id = ANY($1::uuid[])",
        ):
            await conn.execute(statement, orgs)


async def _counts(conn) -> dict[str, int]:
    return {
        table: int(await conn.fetchval(f"SELECT count(*) FROM {table}"))
        for table in TOUCHED_TABLES
    }


# ═══════════════════════════════════════════════════════════════════════════
# Check 1 — deployed shape
# ═══════════════════════════════════════════════════════════════════════════


async def check_1(results: Results, admin) -> None:
    """Six tables, RLS ON, the exact policy shape, every CHECK the prompt names.

    "RLS enabled" alone proves very little: a table with RLS on and a policy of
    ``USING (true)`` is wide open and passes that test. The policy expression is
    compared against the established shape, whitespace-folded, because Postgres
    reformats an expression when it stores it.

    Each CHECK is asserted on its CONTENTS, not merely its existence. A
    constraint named ``fee_schedules_status_check`` that admitted anything
    would satisfy a name-only test.
    """
    rows = await admin.fetch(
        """
        SELECT c.relname AS table_name, c.relrowsecurity,
               p.polname, p.polcmd::text AS polcmd, p.polpermissive,
               pg_get_expr(p.polqual, p.polrelid) AS using_expr,
               pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_policy p ON p.polrelid = c.oid
        WHERE n.nspname = 'public' AND c.relname = ANY($1::text[])
        ORDER BY c.relname
        """,
        [t.split(".", 1)[1] for t in FEE_TABLES],
    )
    by_table = {r["table_name"]: r for r in rows}

    missing = [t for t in FEE_TABLES if t.split(".", 1)[1] not in by_table]
    if missing:
        results.bad("1a", "all six fee tables are deployed", f"missing: {missing}")
        return
    results.ok("1a", "all six fee tables are deployed",
               ", ".join(sorted(by_table)))

    bad_rls = [n for n, r in by_table.items() if not r["relrowsecurity"]]
    if bad_rls:
        results.bad("1b", "RLS is enabled on all six", f"RLS off: {bad_rls}")
    else:
        results.ok("1b", "RLS is enabled on all six")

    wrong: list[str] = []
    for name, r in sorted(by_table.items()):
        if r["polname"] is None:
            wrong.append(f"{name}: no policy at all")
            continue
        if r["polcmd"] != "*":
            wrong.append(f"{name}: policy is {r['polcmd']}, expected ALL")
        if not r["polpermissive"]:
            wrong.append(f"{name}: policy is RESTRICTIVE, expected PERMISSIVE")
        if _fold(r["using_expr"]) != _fold(EXPECTED_POLICY):
            wrong.append(f"{name}: USING is {r['using_expr']}")
        if _fold(r["check_expr"]) != _fold(EXPECTED_POLICY):
            wrong.append(f"{name}: WITH CHECK is {r['check_expr']}")
    if wrong:
        results.bad("1c", "policy shape is exactly the established one",
                    "; ".join(wrong))
    else:
        results.ok(
            "1c", "policy shape is exactly the established one",
            "one permissive FOR ALL policy per table, org_id = NULLIF(GUC) OR "
            "super-admin, USING and WITH CHECK identical",
        )

    deployed = {
        (r["table_name"], r["conname"]): r["def"]
        for r in await admin.fetch(
            """
            SELECT rel.relname AS table_name, con.conname,
                   pg_get_constraintdef(con.oid) AS def
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            WHERE n.nspname = 'public' AND con.contype = 'c'
              AND rel.relname = ANY($1::text[])
            """,
            [t.split(".", 1)[1] for t in FEE_TABLES],
        )
    }
    problems: list[str] = []
    for table, conname, needles in EXPECTED_CHECKS:
        definition = deployed.get((table, conname))
        if definition is None:
            problems.append(f"{table}.{conname} is MISSING")
            continue
        absent = [n for n in needles if n not in definition]
        if absent:
            problems.append(f"{table}.{conname} does not mention {absent}")
    if problems:
        results.bad("1d", "every CHECK constraint present and matching",
                    "; ".join(problems))
    else:
        results.ok(
            "1d", "every CHECK constraint present and matching",
            f"{len(EXPECTED_CHECKS)} constraints asserted on their CONTENTS, "
            f"not merely their names",
        )

    grants = {
        r["table_name"]
        for r in await admin.fetch(
            """
            SELECT table_name FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND grantee = 'app_service'
              AND table_name = ANY($1::text[])
              AND privilege_type = 'SELECT'
            """,
            [t.split(".", 1)[1] for t in FEE_TABLES],
        )
    }
    ungranted = sorted({t.split(".", 1)[1] for t in FEE_TABLES} - grants)
    if ungranted:
        results.bad("1e", "app_service is granted on all six",
                    f"no grant: {ungranted} — RLS would be unreachable, and a "
                    f"bare permission-denied looks exactly like an RLS denial")
    else:
        results.ok("1e", "app_service is granted on all six")

    # The finding that decides the whole versioning design, re-measured here
    # rather than trusted from Task 1's run.
    partial = await admin.fetchval(
        "SELECT indexdef LIKE '%WHERE%' FROM pg_indexes "
        "WHERE schemaname='public' AND indexname='fee_schedules_code_version_uq'"
    )
    if partial is None:
        results.bad("1f", "fee_schedules_code_version_uq exists")
    elif partial:
        results.bad(
            "1f", "fee_schedules_code_version_uq is NOT partial",
            "it has a WHERE clause — the versioning design in "
            "services/fee_schedules.py assumes it does not",
        )
    else:
        results.find(
            "1f", "fee_schedules_code_version_uq is UNIQUE (org_id, code, "
                  "version) with NO partial predicate",
            "this is why a schedule edit cannot be a Rule 3 valid-axis "
            "restatement: closing a row and re-inserting the same "
            "(code, version) collides with the row just closed, because a "
            "closed row still occupies the index. Versioning goes through "
            "version+1 and a DRAFT edit is an in-place UPDATE — not a style "
            "choice, the only shape the deployed index allows",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Check 2 — tier contiguity, four distinct outcomes
# ═══════════════════════════════════════════════════════════════════════════


def check_2(results: Results) -> None:
    """A correct ladder passes; a gap, an overlap, and two open tiers each fail
    with a DISTINCT, typed error naming the tier at fault.

    Pure — no connection is opened anywhere in this check. That is the property
    the sprint asks for and it is proved by the check's own shape: if
    ``validate_tiers`` needed a database, this function could not run.
    """
    clean = validate_tiers(GOOD_TIERS)
    if clean:
        results.bad("2a", "a contiguous tier set passes",
                    f"unexpected errors: {[e.code for e in clean]}")
    else:
        results.ok("2a", "a contiguous tier set passes",
                   "0 → 1M → 5M → open, three tiers, no errors")

    # ── gap ──────────────────────────────────────────────────────────────
    gapped = [
        dict(GOOD_TIERS[0]),
        {**GOOD_TIERS[1], "lower_bound": Decimal("1500000")},   # 1M..1.5M unbilled
        dict(GOOD_TIERS[2]),
    ]
    errors = validate_tiers(gapped)
    gaps = [e for e in errors if isinstance(e, TierGapError)]
    if len(gaps) == 1 and gaps[0].code == "tier_gap" and gaps[0].tier_seq == 2:
        results.ok(
            "2b", "a GAP fails with TierGapError naming the tier",
            f"tier_seq={gaps[0].tier_seq} field={gaps[0].field!r} — "
            f"{gaps[0].message[:90]}...",
        )
    else:
        results.bad("2b", "a GAP fails with TierGapError naming the tier",
                    f"got {[(type(e).__name__, e.code) for e in errors]}")

    # ── overlap ──────────────────────────────────────────────────────────
    overlapped = [
        dict(GOOD_TIERS[0]),
        {**GOOD_TIERS[1], "lower_bound": Decimal("900000")},    # 900k..1M twice
        dict(GOOD_TIERS[2]),
    ]
    errors = validate_tiers(overlapped)
    overlaps = [e for e in errors if isinstance(e, TierOverlapError)]
    if len(overlaps) == 1 and overlaps[0].code == "tier_overlap" \
            and overlaps[0].tier_seq == 2:
        results.ok(
            "2c", "an OVERLAP fails with TierOverlapError naming the tier",
            f"tier_seq={overlaps[0].tier_seq} — {overlaps[0].message[:90]}...",
        )
    else:
        results.bad("2c", "an OVERLAP fails with TierOverlapError naming the tier",
                    f"got {[(type(e).__name__, e.code) for e in errors]}")

    # ── two open-ended tiers ─────────────────────────────────────────────
    two_open = [
        dict(GOOD_TIERS[0]),
        {**GOOD_TIERS[1], "upper_bound": None},
        dict(GOOD_TIERS[2]),
    ]
    errors = validate_tiers(two_open)
    unbounded = [e for e in errors if isinstance(e, TierUnboundedError)]
    if len(unbounded) == 1 and unbounded[0].code == "tier_unbounded_duplicate":
        results.ok(
            "2d", "TWO NULL upper_bounds fail with TierUnboundedError",
            f"code={unbounded[0].code} tier_seqs="
            f"{unbounded[0].context.get('tier_seqs')} — "
            f"{unbounded[0].message[:80]}...",
        )
    else:
        results.bad("2d", "TWO NULL upper_bounds fail with TierUnboundedError",
                    f"got {[(type(e).__name__, e.code) for e in errors]}")

    # ── the three error TYPES are genuinely distinct ─────────────────────
    codes = {
        "gap": [e.code for e in validate_tiers(gapped) if isinstance(e, TierGapError)],
        "overlap": [e.code for e in validate_tiers(overlapped)
                    if isinstance(e, TierOverlapError)],
        "two_open": [e.code for e in validate_tiers(two_open)
                     if isinstance(e, TierUnboundedError)],
    }
    distinct = {v[0] for v in codes.values() if v}
    if len(distinct) == 3:
        results.ok(
            "2e", "the three failures carry three DISTINCT codes",
            f"{sorted(distinct)} — a single 'tiers are invalid' error would let "
            f"a test that only ever built gaps claim it proved overlap too",
        )
    else:
        results.bad("2e", "the three failures carry three DISTINCT codes",
                    f"got {codes}")

    # ── the two remaining unbounded variants ─────────────────────────────
    no_open = [dict(GOOD_TIERS[0]), dict(GOOD_TIERS[1]),
               {**GOOD_TIERS[2], "upper_bound": Decimal("9000000")}]
    got_missing = [e.code for e in validate_tiers(no_open)
                   if isinstance(e, TierUnboundedError)]
    # Tier 2 open, tier 3 CLOSED — so there is exactly one open tier and it is
    # not the top one. Leaving tier 3's own NULL upper_bound in place here
    # would make this a second two-open-tiers case, not the mid-ladder one.
    mid_open = [dict(GOOD_TIERS[0]), {**GOOD_TIERS[1], "upper_bound": None},
                {**GOOD_TIERS[2], "upper_bound": Decimal("9000000")}]
    got_mid = [e.code for e in validate_tiers(mid_open)
               if isinstance(e, TierUnboundedError)]
    # mid_open has exactly ONE null upper and it is not the top tier.
    if got_missing == ["tier_unbounded_missing"] and \
            got_mid == ["tier_unbounded_not_last"]:
        results.ok(
            "2f", "the other two unbounded variants are distinguished too",
            "no open tier → tier_unbounded_missing; open tier in the middle → "
            "tier_unbounded_not_last",
        )
    else:
        results.bad("2f", "the other two unbounded variants are distinguished too",
                    f"missing-case={got_missing} mid-case={got_mid}")

    # ── float refusal ────────────────────────────────────────────────────
    floaty = [{**GOOD_TIERS[0], "upper_bound": 1000000.10}]
    errors = validate_tiers(floaty)
    if any(isinstance(e, MoneyTypeError) for e in errors):
        results.ok(
            "2g", "a float tier bound is REFUSED, not coerced",
            "Decimal(0.1) != Decimal('0.1'), and tier contiguity is an equality "
            "test between two bounds — a coerced float reports a gap between "
            "values an operator entered as identical",
        )
    else:
        results.bad("2g", "a float tier bound is REFUSED, not coerced",
                    f"got {[e.code for e in errors]}")

    # ── ordering_policy ──────────────────────────────────────────────────
    policy_cases = (
        ("permutation", list(reversed(ORDERING_STEPS)), False),
        ("missing step", [s for s in ORDERING_STEPS if s != "MINIMUM"], True),
        ("duplicate step", list(ORDERING_STEPS) + ["TIERS"], True),
        ("invented step", list(ORDERING_STEPS)[:-1] + ["ROUNDING"], True),
    )
    outcomes = []
    for label, value, should_fail in policy_cases:
        errs = validate_schedule({**GOOD_DEFINITION, "ordering_policy": value},
                                 GOOD_TIERS)
        failed = any(isinstance(e, OrderingPolicyError) for e in errs)
        outcomes.append((label, failed == should_fail))
    if all(ok for _, ok in outcomes):
        results.ok(
            "2h", "ordering_policy must be a permutation of the six steps",
            "a reversed permutation PASSES; a missing step, a duplicate, and an "
            "invented step each FAIL — the positive case matters as much as the "
            "three negatives, since a rule that rejected everything would pass "
            "all three of them",
        )
    else:
        results.bad("2h", "ordering_policy must be a permutation of the six steps",
                    f"{outcomes}")

    # ── the exclusion/discount/credit rules ──────────────────────────────
    sub_cases = (
        ("REDUCED_RATE without alt_fee_schedule_id",
         validate_exclusion({"treatment": "REDUCED_RATE", "reason": "x",
                             "alt_fee_schedule_id": None}),
         ExclusionAltScheduleError),
        ("FLAT without flat_amount",
         validate_exclusion({"treatment": "FLAT", "reason": "x",
                             "flat_amount": None}),
         ExclusionFlatAmountError),
        ("exclusion with an EMPTY-STRING reason",
         validate_exclusion({"treatment": "EXCLUDE", "reason": "   "}),
         ReasonRequiredError),
        ("discount without approved_by",
         validate_discount({"scope_type": "ACCOUNT", "reason": "x",
                            "approved_by": None}),
         ApprovedByRequiredError),
        ("credit with an EMPTY-STRING reason",
         validate_credit({"scope_type": "ACCOUNT", "reason": "",
                          "approved_by": str(uuid.uuid4())}),
         ReasonRequiredError),
    )
    misses = [
        label for label, errs, kind in sub_cases
        if not any(isinstance(e, kind) for e in errs)
    ]
    if misses:
        results.bad("2i", "exclusion / discount / credit rules each fire",
                    f"did not fire: {misses}")
    else:
        results.ok(
            "2i", "exclusion / discount / credit rules each fire",
            "including the two the DATABASE cannot catch: a reason of '' and a "
            "reason of '   ' both satisfy NOT NULL and are refused here",
        )

    # ── the positive direction on those same rules ───────────────────────
    clean_sub = (
        validate_exclusion({"treatment": "REDUCED_RATE", "reason": "Held away",
                            "alt_fee_schedule_id": str(uuid.uuid4()),
                            "scope_type": "ACCOUNT"})
        + validate_exclusion({"treatment": "FLAT", "reason": "Legacy",
                              "flat_amount": Decimal("250.00"),
                              "scope_type": "ORG"})
        + validate_discount({"scope_type": "ACCOUNT", "reason": "Founder rate",
                             "approved_by": str(uuid.uuid4()),
                             "value": Decimal("0.10")})
        + validate_credit({"scope_type": "HOUSEHOLD", "reason": "12b-1 rebate",
                           "approved_by": str(uuid.uuid4()),
                           "offset_pct": Decimal("0.5")})
    )
    if clean_sub:
        results.bad("2j", "well-formed exclusions/discounts/credits PASS",
                    f"false positives: {[e.code for e in clean_sub]}")
    else:
        results.ok(
            "2j", "well-formed exclusions/discounts/credits PASS",
            "the negative cases in 2i would also pass a validator that rejected "
            "everything; this is the direction that rules that out",
        )

    results.find(
        "2k", "the exclusion rules are NOT reachable from the schedule-approval "
              "gate, and the prompt assumes they are",
        "fee_exclusions has NO fee_schedule_id — only alt_fee_schedule_id, the "
        "REDUCED_RATE target. Exclusions are scoped to an account, billing "
        "group or household, so there is no join path from a schedule to 'its' "
        "exclusions because a schedule does not have any. Folding them into "
        "validate_schedule would have produced a gate that always passes "
        "vacuously on an empty list. They are separate callables used at their "
        "own rows' write time, and validate_schedule takes an OPTIONAL "
        "exclusions argument for the one honest case — validating a proposed "
        "bundle before saving any of it",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Check 3 — DRAFT edits in place, APPROVED edits fork
# ═══════════════════════════════════════════════════════════════════════════

_SNAPSHOT_FIELDS = (
    "id", "code", "version", "name", "product_type", "rate_type", "tier_method",
    "billing_frequency", "billing_timing", "valuation_method", "status",
    "minimum_fee", "minimum_fee_scope", "maximum_fee", "currency",
    "ordering_policy",
)


def _snapshot(schedule: dict) -> dict:
    return {k: schedule.get(k) for k in _SNAPSHOT_FIELDS}


async def check_3(results: Results, admin, fx: Fixture) -> dict:
    """DRAFT → same id. APPROVED → a new row at version+1, original untouched."""
    code = fx.code("VERSIONING")
    created = await create_schedule(
        admin, fx.org_a, code=code, tiers=GOOD_TIERS, **GOOD_DEFINITION
    )
    draft_id = created["schedule"]["id"]

    # ── DRAFT edits in place ─────────────────────────────────────────────
    outcome = await update_schedule(
        admin, fx.org_a, draft_id, name="fee34 Renamed While Draft"
    )
    after = await load_schedule(admin, fx.org_a, draft_id)
    same_id = outcome.schedule_id == draft_id and not outcome.versioned
    row_count = await admin.fetchval(
        "SELECT count(*) FROM public.fee_schedules WHERE org_id=$1::uuid AND code=$2",
        fx.org_a, code,
    )
    if same_id and after["name"] == "fee34 Renamed While Draft" \
            and after["version"] == 1 and row_count == 1:
        results.ok(
            "3a", "editing a DRAFT mutates it IN PLACE — same id, no new row",
            f"id unchanged ({draft_id[:8]}...), version still 1, and exactly "
            f"{row_count} row exists for this code",
        )
    else:
        results.bad(
            "3a", "editing a DRAFT mutates it IN PLACE — same id, no new row",
            f"versioned={outcome.versioned} new_id={outcome.schedule_id} "
            f"name={after['name']!r} version={after['version']} rows={row_count}",
        )

    # ── approve it, and hang an assignment off it ────────────────────────
    approved = await submit_for_approval(
        admin, fx.org_a, draft_id, approved_by=str(uuid.uuid4())
    )
    if approved["schedule"]["status"] != STATUS_APPROVED:
        results.bad("3b", "the draft reaches APPROVED",
                    f"status={approved['schedule']['status']}")
        return {"code": code, "v1_id": draft_id}
    results.ok("3b", "the draft reaches APPROVED",
               f"status=APPROVED, approved_at set")

    assignment = await create_assignment(
        admin, fx.org_a, fee_schedule_id=draft_id, scope_type="ACCOUNT",
        scope_id=fx.account_main, effective_from=date.today() - timedelta(days=30),
        agreement_document_id=fx.document_a,
    )

    before_v1 = _snapshot(await load_schedule(admin, fx.org_a, draft_id))
    before_tiers = await admin.fetch(
        "SELECT tier_seq, lower_bound, upper_bound, rate_bps FROM "
        "public.fee_schedule_tiers WHERE fee_schedule_id=$1::uuid ORDER BY tier_seq",
        draft_id,
    )

    # ── APPROVED edit forks ──────────────────────────────────────────────
    fork = await update_schedule(
        admin, fx.org_a, draft_id, name="fee34 Version Two",
        minimum_fee=Decimal("2500.00"), minimum_fee_scope="HOUSEHOLD",
    )
    v2 = await load_schedule(admin, fx.org_a, fork.schedule_id)
    after_v1 = _snapshot(await load_schedule(admin, fx.org_a, draft_id))
    after_tiers = await admin.fetch(
        "SELECT tier_seq, lower_bound, upper_bound, rate_bps FROM "
        "public.fee_schedule_tiers WHERE fee_schedule_id=$1::uuid ORDER BY tier_seq",
        draft_id,
    )

    if fork.versioned and fork.schedule_id != draft_id and v2["version"] == 2 \
            and v2["status"] == STATUS_DRAFT and v2["code"] == code:
        results.ok(
            "3c", "editing an APPROVED schedule creates version+1 as a new DRAFT",
            f"new id {fork.schedule_id[:8]}... at version 2, status DRAFT, same "
            f"code {code}",
        )
    else:
        results.bad(
            "3c", "editing an APPROVED schedule creates version+1 as a new DRAFT",
            f"versioned={fork.versioned} id={fork.schedule_id} "
            f"version={v2['version']} status={v2['status']}",
        )

    if before_v1 == after_v1:
        results.ok(
            "3d", "version 1 is byte-for-byte UNCHANGED by the fork",
            f"all {len(_SNAPSHOT_FIELDS)} snapshot fields identical, including "
            f"status=APPROVED and the minimum_fee the fork set on v2 only — "
            f"asserting merely that a new row appeared would pass even if the "
            f"fork had also mutated the original",
        )
    else:
        drift = {k: (before_v1[k], after_v1[k])
                 for k in before_v1 if before_v1[k] != after_v1[k]}
        results.bad("3d", "version 1 is byte-for-byte UNCHANGED by the fork",
                    f"drifted: {drift}")

    if [dict(r) for r in before_tiers] == [dict(r) for r in after_tiers]:
        results.ok("3e", "version 1's TIER rows are untouched by the fork",
                   f"{len(before_tiers)} tiers, identical before and after")
    else:
        results.bad("3e", "version 1's TIER rows are untouched by the fork")

    copied = await admin.fetch(
        "SELECT tier_seq, lower_bound, upper_bound, rate_bps FROM "
        "public.fee_schedule_tiers WHERE fee_schedule_id=$1::uuid ORDER BY tier_seq",
        fork.schedule_id,
    )
    if [dict(r) for r in copied] == [dict(r) for r in before_tiers]:
        results.ok("3f", "the fork COPIES the tier ladder forward",
                   f"v2 has the same {len(copied)} tiers as v1")
    else:
        results.bad("3f", "the fork COPIES the tier ladder forward",
                    f"v1={[dict(r) for r in before_tiers]} v2={[dict(r) for r in copied]}")

    # ── the existing assignment still RESOLVES to v1 ─────────────────────
    still = await admin.fetchrow(
        "SELECT fee_schedule_id::text AS fee_schedule_id, valid_to, effective_to "
        "FROM public.fee_assignments WHERE id=$1::uuid", assignment["id"],
    )
    resolved = await resolve_assignment_for_account(
        admin, fx.org_a, fx.account_main
    )
    if still and still["fee_schedule_id"] == draft_id and still["valid_to"] is None \
            and resolved is not None and resolved.fee_schedule_id == draft_id \
            and resolved.schedule_version == 1:
        results.ok(
            "3g", "the fee_assignment made against v1 still RESOLVES to v1",
            f"the resolver returns version 1 (status "
            f"{resolved.schedule_status}), not the newer draft — an invoice "
            f"produced last quarter is still reproducible from the exact "
            f"schedule that produced it. 'The row still exists' and 'the "
            f"assignment still resolves to it' are different claims; this is "
            f"the second",
        )
    else:
        results.bad(
            "3g", "the fee_assignment made against v1 still RESOLVES to v1",
            f"row={dict(still) if still else None} resolved="
            f"{resolved.fee_schedule_id if resolved else None}",
        )

    # ── RETIRED refuses both edit and new assignment ─────────────────────
    retire_code = fx.code("RETIRED")
    retired = await create_schedule(
        admin, fx.org_a, code=retire_code, tiers=GOOD_TIERS, **GOOD_DEFINITION
    )
    retired_id = retired["schedule"]["id"]
    await submit_for_approval(admin, fx.org_a, retired_id)
    prior_assignment = await create_assignment(
        admin, fx.org_a, fee_schedule_id=retired_id, scope_type="ENTITY",
        scope_id=fx.entity_a,
    )
    await retire_schedule(admin, fx.org_a, retired_id)

    edit_refused = assign_refused = False
    try:
        await update_schedule(admin, fx.org_a, retired_id, name="nope")
    except ScheduleStatusError as exc:
        edit_refused = exc.status == STATUS_RETIRED
    try:
        await create_assignment(
            admin, fx.org_a, fee_schedule_id=retired_id,
            scope_type="HOUSEHOLD", scope_id=fx.household_a,
        )
    except ScheduleStatusError as exc:
        assign_refused = exc.status == STATUS_RETIRED

    survivor = await admin.fetchrow(
        "SELECT valid_to, effective_to, fee_schedule_id::text AS fee_schedule_id "
        "FROM public.fee_assignments WHERE id=$1::uuid", prior_assignment["id"],
    )
    undisturbed = (
        survivor is not None
        and survivor["valid_to"] is None
        and survivor["effective_to"] is None
        and survivor["fee_schedule_id"] == retired_id
    )
    if edit_refused and assign_refused and undisturbed:
        results.ok(
            "3h", "a RETIRED schedule refuses edit and new assignment, but its "
                  "EXISTING assignment is undisturbed",
            "both refusals raise ScheduleStatusError with status=RETIRED, and "
            "the assignment made before retirement still has valid_to NULL and "
            "effective_to NULL — retiring stops new business, it does not "
            "rewrite old business",
        )
    else:
        results.bad(
            "3h", "a RETIRED schedule refuses edit and new assignment, but its "
                  "EXISTING assignment is undisturbed",
            f"edit_refused={edit_refused} assign_refused={assign_refused} "
            f"undisturbed={undisturbed}",
        )

    return {"code": code, "v1_id": draft_id, "v2_id": fork.schedule_id}


# ═══════════════════════════════════════════════════════════════════════════
# Check 4 — a failing submit is refused and stays DRAFT
# ═══════════════════════════════════════════════════════════════════════════


async def check_4(results: Results, admin, fx: Fixture) -> None:
    """Reproduce the refusal, then show the fix resolves it.

    The schedule is re-read from the database after the refusal. A refusal that
    raised but had already flipped the status would pass a check that only
    caught the exception.
    """
    code = fx.code("SUBMITGATE")
    gapped = [
        dict(GOOD_TIERS[0]),
        {**GOOD_TIERS[1], "lower_bound": Decimal("1500000")},
        dict(GOOD_TIERS[2]),
    ]
    created = await create_schedule(
        admin, fx.org_a, code=code, tiers=gapped, **GOOD_DEFINITION
    )
    schedule_id = created["schedule"]["id"]

    if created["schedule"]["status"] != STATUS_DRAFT or created["is_valid"]:
        results.bad("4a", "an invalid schedule can still be SAVED as a DRAFT",
                    f"status={created['schedule']['status']} "
                    f"is_valid={created['is_valid']}")
    else:
        results.ok(
            "4a", "an invalid schedule can still be SAVED as a DRAFT",
            f"created DRAFT with a tier gap; the read publishes "
            f"{len(created['validation_errors'])} validation_errors so the "
            f"screen can show what blocks approval without attempting it",
        )

    refused_with = None
    try:
        await submit_for_approval(admin, fx.org_a, schedule_id)
    except FeeScheduleInvalid as exc:
        refused_with = exc

    after = await load_schedule(admin, fx.org_a, schedule_id)
    if refused_with is not None and after["status"] == STATUS_DRAFT \
            and after["approved_at"] is None:
        codes = [e.code for e in refused_with.errors]
        results.ok(
            "4b", "submitting an invalid schedule is REFUSED and it stays DRAFT",
            f"raised FeeScheduleInvalid with codes {codes}; re-read from the "
            f"database, status is still DRAFT and approved_at is still NULL",
        )
    else:
        results.bad(
            "4b", "submitting an invalid schedule is REFUSED and it stays DRAFT",
            f"raised={type(refused_with).__name__ if refused_with else None} "
            f"status={after['status']} approved_at={after['approved_at']}",
        )

    # ── fix exactly the flagged issue, resubmit ──────────────────────────
    await update_schedule(admin, fx.org_a, schedule_id, tiers=GOOD_TIERS)
    fixed = await submit_for_approval(admin, fx.org_a, schedule_id)
    if fixed["schedule"]["status"] == STATUS_APPROVED and fixed["is_valid"]:
        results.ok(
            "4c", "fixing the one flagged issue and resubmitting SUCCEEDS",
            "only the tier ladder was changed — the same schedule, the same id, "
            "now APPROVED. Reproducing the refusal first is what makes this a "
            "proof that the gap was the blocker, rather than a fresh schedule "
            "that happens to pass",
        )
    else:
        results.bad("4c", "fixing the one flagged issue and resubmitting SUCCEEDS",
                    f"status={fixed['schedule']['status']} "
                    f"errors={fixed['validation_errors']}")

    # ── re-submitting an APPROVED schedule is a status conflict ──────────
    try:
        await submit_for_approval(admin, fx.org_a, schedule_id)
        results.bad("4d", "re-submitting an APPROVED schedule is refused",
                    "it was accepted")
    except ScheduleStatusError as exc:
        results.ok("4d", "re-submitting an APPROVED schedule is refused",
                   f"ScheduleStatusError status={exc.status}")
    except Exception as exc:  # noqa: BLE001
        results.bad("4d", "re-submitting an APPROVED schedule is refused",
                    f"{type(exc).__name__}: {exc}")

    # ── 4e: the minimum_fee rule, and why it needs BOTH halves ───────────
    #
    # This rule cannot be proved by submitting, because the DATABASE refuses
    # the DRAFT insert. Proving only that the validator objects would leave
    # open whether the constraint exists at all; proving only that the
    # constraint fires would leave the validator's better message unproven.
    pure = validate_schedule(
        {**GOOD_DEFINITION, "minimum_fee": Decimal("2500")}, GOOD_TIERS
    )
    validator_fires = any(isinstance(e, MinimumFeeScopeError) for e in pure)
    names_field = any(
        getattr(e, "field", None) == "minimum_fee_scope" for e in pure
    )

    db_refused = False
    db_message = ""
    try:
        async with OrgSession(admin, fx.org_a):
            await admin.execute(
                """
                INSERT INTO public.fee_schedules
                    (org_id, code, name, product_type, rate_type,
                     billing_frequency, billing_timing, valuation_method,
                     minimum_fee)
                VALUES ($1::uuid, $2, 'raw', 'ASSET_MANAGEMENT', 'BPS',
                        'QUARTERLY', 'ARREARS', 'PERIOD_END', 2500)
                """,
                fx.org_a, fx.code("RAWMIN"),
            )
    except asyncpg.exceptions.CheckViolationError as exc:
        db_refused = True
        db_message = exc.constraint_name or str(exc)[:60]

    if validator_fires and names_field and db_refused:
        results.ok(
            "4e", "minimum_fee without minimum_fee_scope: validator names the "
                  "FIELD, database refuses the ROW",
            f"the constraint ({db_message}) genuinely fires — so this rule can "
            f"never be reached through submit_for_approval, because such a "
            f"DRAFT cannot be made to exist. The validator's value is the "
            f"message, and it is proved in memory; the constraint's value is "
            f"the refusal, and it is proved against the real database. Either "
            f"half alone proves nothing about the other",
        )
    else:
        results.bad(
            "4e", "minimum_fee without minimum_fee_scope: validator names the "
                  "FIELD, database refuses the ROW",
            f"validator_fires={validator_fires} names_field={names_field} "
            f"db_refused={db_refused}",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Check 5 — the BILLING_GROUP cross-scope integrity check
# ═══════════════════════════════════════════════════════════════════════════


async def check_5(results: Results, admin, fx: Fixture) -> str:
    """A ghost group, a closed group, and a real open one."""
    code = fx.code("SCOPECHECK")
    created = await create_schedule(
        admin, fx.org_a, code=code, tiers=GOOD_TIERS, **GOOD_DEFINITION
    )
    schedule_id = created["schedule"]["id"]
    await submit_for_approval(admin, fx.org_a, schedule_id)

    before = await admin.fetchval(
        "SELECT count(*) FROM public.fee_assignments WHERE org_id=$1::uuid",
        fx.org_a,
    )

    # ── a scope_id that does not exist ───────────────────────────────────
    ghost = None
    try:
        await create_assignment(
            admin, fx.org_a, fee_schedule_id=schedule_id,
            scope_type="BILLING_GROUP", scope_id=fx.group_ghost,
        )
    except ScopeLinkError as exc:
        ghost = exc
    if ghost is not None and ghost.reason == "missing":
        results.ok(
            "5a", "a BILLING_GROUP scope_id that does not exist is REFUSED",
            f"ScopeLinkError reason='missing'. scope_id carries no foreign key "
            f"— it addresses a different table per scope_type — so without this "
            f"check the row would insert cleanly and surface only as a fee that "
            f"resolves to nothing",
        )
    else:
        results.bad("5a", "a BILLING_GROUP scope_id that does not exist is REFUSED",
                    f"got {ghost!r}")

    # ── a scope_id that exists but is CLOSED ─────────────────────────────
    closed = None
    try:
        await create_assignment(
            admin, fx.org_a, fee_schedule_id=schedule_id,
            scope_type="BILLING_GROUP", scope_id=fx.group_closed,
        )
    except ScopeLinkError as exc:
        closed = exc
    if closed is not None and closed.reason == "closed":
        results.ok(
            "5b", "a CLOSED BILLING_GROUP is REFUSED, and distinguishably so",
            f"ScopeLinkError reason='closed', not 'missing' — the group really "
            f"is in the table, so a bare existence check would have let this "
            f"through. The two reasons are different operator situations: "
            f"'missing' means look at the id, 'closed' means pick another group",
        )
    else:
        results.bad("5b", "a CLOSED BILLING_GROUP is REFUSED, and distinguishably so",
                    f"got {closed!r}")

    # ── another tenant's group is 'missing', not 'closed' ────────────────
    cross = None
    try:
        await create_assignment(
            admin, fx.org_b, fee_schedule_id=schedule_id,
            scope_type="BILLING_GROUP", scope_id=fx.group_open,
        )
    except (ScopeLinkError, FeeScheduleError) as exc:
        cross = exc
    if cross is not None:
        results.ok(
            "5c", "org B cannot assign against org A's schedule or group",
            f"{type(cross).__name__} — the FKs on fee_assignments are org-blind "
            f"(they reference id alone), so another tenant's id satisfies them; "
            f"the explicit org predicate is the real gate",
        )
    else:
        results.bad("5c", "org B cannot assign against org A's schedule or group",
                    "it was accepted")

    mid = await admin.fetchval(
        "SELECT count(*) FROM public.fee_assignments WHERE org_id=ANY($1::uuid[])",
        [fx.org_a, fx.org_b],
    )
    if mid == before:
        results.ok(
            "5d", "all three refusals left the table UNCHANGED",
            f"{before} assignment rows before, {mid} after — a refusal that "
            f"raised but had already inserted would pass a check that only "
            f"caught the exception",
        )
    else:
        results.bad("5d", "all three refusals left the table UNCHANGED",
                    f"{before} → {mid}")

    # ── the real, open group succeeds ────────────────────────────────────
    try:
        good = await create_assignment(
            admin, fx.org_a, fee_schedule_id=schedule_id,
            scope_type="BILLING_GROUP", scope_id=fx.group_open,
        )
        persisted = await admin.fetchrow(
            "SELECT scope_id::text AS scope_id, precedence FROM "
            "public.fee_assignments WHERE id=$1::uuid", good["id"],
        )
        if persisted and persisted["scope_id"] == fx.group_open \
                and persisted["precedence"] == SCOPE_PRECEDENCE["BILLING_GROUP"]:
            results.ok(
                "5e", "a real, OPEN billing_groups.id SUCCEEDS and persists",
                f"re-read from the table: scope_id matches and precedence="
                f"{persisted['precedence']} was DERIVED from scope_type, not "
                f"supplied — a filter that refused everything would have passed "
                f"5a-5c and failed only here",
            )
        else:
            results.bad("5e", "a real, OPEN billing_groups.id SUCCEEDS and persists",
                        f"{dict(persisted) if persisted else None}")
    except Exception as exc:  # noqa: BLE001
        results.bad("5e", "a real, OPEN billing_groups.id SUCCEEDS and persists",
                    f"{type(exc).__name__}: {exc}")

    # ── the scope_id_required constraint's intent ────────────────────────
    both_ways = []
    try:
        await create_assignment(
            admin, fx.org_a, fee_schedule_id=schedule_id,
            scope_type="ACCOUNT", scope_id=None,
        )
    except ScopeIdRequiredError:
        both_ways.append("account-without-id")
    try:
        await create_assignment(
            admin, fx.org_a, fee_schedule_id=schedule_id,
            scope_type="ORG_DEFAULT", scope_id=fx.account_main,
        )
    except ScopeIdRequiredError:
        both_ways.append("org-default-with-id")
    if len(both_ways) == 2:
        results.ok(
            "5f", "scope_id_required is enforced in BOTH directions with a "
                  "clean error",
            "ACCOUNT with no scope_id AND ORG_DEFAULT with one are each refused "
            "by ScopeIdRequiredError before reaching the constraint, whose own "
            "message names only the constraint",
        )
    else:
        results.bad("5f", "scope_id_required is enforced in BOTH directions with "
                          "a clean error", f"only caught: {both_ways}")

    return schedule_id


# ═══════════════════════════════════════════════════════════════════════════
# Check 6 — precedence, with three assignments live at once
# ═══════════════════════════════════════════════════════════════════════════


#: The settled precedence order, most specific first. Every rung is reachable
#: from ``account_main``: it belongs to household_a, to entity_a, and to
#: group_open, and ORG_DEFAULT applies to everything.
LADDER = ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD", "ENTITY", "ORG_DEFAULT")


async def check_6(results: Results, admin, fx: Fixture) -> None:
    """The FULL five-rung ladder, all five assignments live at once.

    The prompt asks for three simultaneous assignments. Five is what the scope
    vocabulary actually contains and what ``account_main`` can actually reach,
    and the extra two are not decoration: checks 3 and 5 legitimately leave an
    ENTITY and a BILLING_GROUP assignment live on this same account, and
    BILLING_GROUP (20) outranks HOUSEHOLD (30). A check that built only three
    rungs would have been measuring a ladder it did not control — which is
    exactly what the first run of this script discovered, and the reason this
    check now starts by closing whatever is already there.

    Then each winner is ended in turn and the next rung takes over, four times,
    ending with no assignment at all. Inclusion and exclusion are both proved
    on the same dataset: a resolver that always returned the account-level row
    and a resolver that always returned the first row it found would both pass
    a check that only asserted the top of the ladder.
    """
    # Fixture isolation, not a production path. Checks 3 and 5 leave live
    # assignments on this account by design; this check needs to own the whole
    # ladder, so it closes them first and says so rather than quietly
    # resolving against them.
    prior = await admin.fetch(
        "SELECT id::text AS id FROM public.fee_assignments "
        "WHERE org_id=$1::uuid AND valid_to IS NULL AND system_to IS NULL",
        fx.org_a,
    )
    for row in prior:
        await end_assignment(admin, fx.org_a, row["id"])
    results.find(
        "6-pre", f"closed {len(prior)} assignment(s) left live by checks 3 and 5 "
                 f"before building the ladder",
        "checks 3 and 5 leave an ENTITY and a BILLING_GROUP assignment on this "
        "same account. BILLING_GROUP (20) outranks HOUSEHOLD (30), so a "
        "three-rung check that ignored them would have resolved to a rung it "
        "never created — the first run of this script did exactly that. Real "
        "state beats intended state; this check now owns the whole ladder",
    )

    ids: dict[str, str] = {}
    assignments: dict[str, str] = {}
    scope_ids = {
        "ACCOUNT": fx.account_main,
        "BILLING_GROUP": fx.group_open,
        "HOUSEHOLD": fx.household_a,
        "ENTITY": fx.entity_a,
        "ORG_DEFAULT": None,
    }
    # Created least-specific first, so the resolver is never merely returning
    # the most recently inserted row.
    for scope_type in reversed(LADDER):
        created = await create_schedule(
            admin, fx.org_a, code=fx.code(f"PREC-{scope_type}"), tiers=GOOD_TIERS,
            **{**GOOD_DEFINITION, "name": f"fee34 {scope_type}"},
        )
        sid = created["schedule"]["id"]
        await submit_for_approval(admin, fx.org_a, sid)
        ids[scope_type] = sid
        made = await create_assignment(
            admin, fx.org_a, fee_schedule_id=sid, scope_type=scope_type,
            scope_id=scope_ids[scope_type],
        )
        assignments[scope_type] = made["id"]

    live = await admin.fetch(
        "SELECT scope_type, precedence FROM public.fee_assignments "
        "WHERE org_id=$1::uuid AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY precedence",
        fx.org_a,
    )
    live_types = [r["scope_type"] for r in live]
    if live_types == list(LADDER):
        results.ok(
            "6a", "all FIVE assignments are live SIMULTANEOUSLY",
            f"precedences {[r['precedence'] for r in live]} for "
            f"{live_types} — the prompt asks for three; five is every rung the "
            f"scope vocabulary contains and every rung this account can reach",
        )
    else:
        results.bad("6a", "all FIVE assignments are live SIMULTANEOUSLY",
                    f"found {live_types}, expected {list(LADDER)}")

    # ── walk the ladder down, one rung per step ──────────────────────────
    labels = {
        "ACCOUNT": ("6b", "ACCOUNT wins over all four less-specific scopes"),
        "BILLING_GROUP": ("6c", "with ACCOUNT ended, BILLING_GROUP wins"),
        "HOUSEHOLD": ("6d", "with BILLING_GROUP ended, HOUSEHOLD wins"),
        "ENTITY": ("6e", "with HOUSEHOLD ended, ENTITY wins"),
        "ORG_DEFAULT": ("6f", "with ENTITY ended, ORG_DEFAULT wins — the last "
                              "rung, and only now"),
    }
    for index, scope_type in enumerate(LADDER):
        number, name = labels[scope_type]
        won = await resolve_assignment_for_account(
            admin, fx.org_a, fx.account_main
        )
        expected_losers = sorted(LADDER[index + 1:])
        actual_losers = sorted(r["scope_type"] for r in won.losers) if won else []
        if won and won.scope_type == scope_type \
                and won.fee_schedule_id == ids[scope_type] \
                and won.precedence == SCOPE_PRECEDENCE[scope_type] \
                and actual_losers == expected_losers:
            results.ok(
                number, name,
                f"winner precedence={won.precedence}; losers are exactly "
                f"{actual_losers} — the losers list is asserted as an EXACT set, "
                f"not a containment, so an assignment the ladder did not create "
                f"could not hide inside it",
            )
        else:
            results.bad(
                number, name,
                f"winner={won.scope_type if won else None} "
                f"(expected {scope_type}); losers={actual_losers} "
                f"(expected {expected_losers})",
            )
        await end_assignment(admin, fx.org_a, assignments[scope_type])

    # ── nothing left: None, not a fallback ───────────────────────────────
    empty = await resolve_assignment_for_account(admin, fx.org_a, fx.account_main)
    if empty is None:
        results.ok(
            "6g", "with every rung ended, resolution returns None",
            "not a fallback and not a zero-rate schedule. An account with no "
            "assignment is NOT BILLED, which is a different thing from billed "
            "at zero, and a caller has to decide what it means",
        )
    else:
        results.bad("6g", "with every rung ended, resolution returns None",
                    f"got {empty.scope_type}")

    # ── the closed rows SURVIVE ──────────────────────────────────────────
    survivors = await admin.fetchval(
        "SELECT count(*) FROM public.fee_assignments WHERE org_id=$1::uuid "
        "AND valid_to IS NOT NULL",
        fx.org_a,
    )
    if survivors >= len(LADDER):
        results.ok(
            "6h", "every ended assignment was CLOSED, never deleted",
            f"{survivors} rows carry valid_to; a hard delete would have "
            f"produced exactly the same answers in 6b–6g and lost the audit "
            f"trail silently — which is why this is asserted separately rather "
            f"than inferred from the resolution walking down correctly",
        )
    else:
        results.bad("6h", "every ended assignment was CLOSED, never deleted",
                    f"only {survivors} closed rows, expected at least "
                    f"{len(LADDER)}")

    # ── precedence is DERIVED, and cannot be supplied ────────────────────
    from routers.fee_schedules import AssignmentCreate  # local: router import

    forbidden = []
    for field, value in (("precedence", 1), ("org_id", str(uuid.uuid4()))):
        try:
            AssignmentCreate(
                fee_schedule_id=str(uuid.uuid4()), scope_type="ORG_DEFAULT",
                **{field: value},
            )
        except Exception:  # noqa: BLE001  — pydantic ValidationError
            forbidden.append(field)
    if sorted(forbidden) == ["org_id", "precedence"]:
        results.ok(
            "6i", "the request model REFUSES both org_id and precedence",
            "extra='forbid' with neither field declared. precedence is NOT NULL "
            "with no default and no tie to scope_type in the database, so a "
            "body carrying precedence=1 on an ORG_DEFAULT assignment would "
            "outrank every account-specific agreement in the org — silently, "
            "and visible only as a wrong number on an invoice",
        )
    else:
        results.bad("6i", "the request model REFUSES both org_id and precedence",
                    f"refused only {forbidden}")


# ═══════════════════════════════════════════════════════════════════════════
# Check 7 — cross-org isolation on all six tables, under app_service
# ═══════════════════════════════════════════════════════════════════════════


async def check_7(results: Results, admin, fx: Fixture, app_dsn, app_prov) -> None:
    """Every one of the six tables, read by a genuinely non-bypassing role."""
    if app_dsn is None:
        results.blocked("7", "cross-org isolation on all six tables",
                        f"no app_service DSN — {app_prov}")
        return

    app = await connect(app_dsn)
    try:
        role, bypass = await app.fetchrow(
            "SELECT current_user::text, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user"
        )
        if bypass:
            results.blocked(
                "7a", "the isolation role does not bypass RLS",
                f"{role} has rolbypassrls=True — every denial below would be "
                f"vacuous, so nothing downstream is measured",
            )
            return
        results.ok("7a", "the isolation role does not bypass RLS",
                   f"current_user={role} rolbypassrls=False")

        # Seed one row of org A's in every one of the six tables.
        seeded = {}
        async with OrgSession(admin, fx.org_a):
            sid = await admin.fetchval(
                """
                INSERT INTO public.fee_schedules
                    (org_id, code, name, product_type, rate_type,
                     billing_frequency, billing_timing, valuation_method)
                VALUES ($1::uuid, $2, 'iso', 'ASSET_MANAGEMENT', 'BPS',
                        'QUARTERLY', 'ARREARS', 'PERIOD_END')
                RETURNING id::text
                """,
                fx.org_a, fx.code("ISOLATION"),
            )
            seeded["public.fee_schedules"] = sid
            seeded["public.fee_schedule_tiers"] = await admin.fetchval(
                "INSERT INTO public.fee_schedule_tiers (org_id, fee_schedule_id, "
                "  tier_seq, lower_bound, upper_bound, rate_bps) "
                "VALUES ($1::uuid, $2::uuid, 1, 0, NULL, 100) RETURNING id::text",
                fx.org_a, sid,
            )
            seeded["public.fee_assignments"] = await admin.fetchval(
                "INSERT INTO public.fee_assignments (org_id, fee_schedule_id, "
                "  scope_type, scope_id, precedence, effective_from) "
                "VALUES ($1::uuid, $2::uuid, 'ACCOUNT', $3::uuid, 10, CURRENT_DATE) "
                "RETURNING id::text",
                fx.org_a, sid, fx.account_main,
            )
            seeded["public.fee_exclusions"] = await admin.fetchval(
                "INSERT INTO public.fee_exclusions (org_id, scope_type, scope_id, "
                "  basis_type, treatment, reason, effective_from) "
                "VALUES ($1::uuid, 'ACCOUNT', $2::uuid, 'CASH', 'EXCLUDE', "
                "        'fee34 isolation fixture', CURRENT_DATE) RETURNING id::text",
                fx.org_a, fx.account_main,
            )
            seeded["public.fee_discounts"] = await admin.fetchval(
                "INSERT INTO public.fee_discounts (org_id, scope_type, scope_id, "
                "  discount_type, value, approved_by, reason, effective_from) "
                "VALUES ($1::uuid, 'ACCOUNT', $2::uuid, 'PCT_OFF', 0.1, "
                "        $3::uuid, 'fee34 isolation fixture', CURRENT_DATE) "
                "RETURNING id::text",
                fx.org_a, fx.account_main, str(uuid.uuid4()),
            )
            seeded["public.fee_credits"] = await admin.fetchval(
                "INSERT INTO public.fee_credits (org_id, scope_type, scope_id, "
                "  credit_source, offset_pct, effective_from, reason, approved_by) "
                "VALUES ($1::uuid, 'ACCOUNT', $2::uuid, '12B1', 1.0, "
                "        CURRENT_DATE, 'fee34 isolation fixture', $3::uuid) "
                "RETURNING id::text",
                fx.org_a, fx.account_main, str(uuid.uuid4()),
            )

        leaks: list[str] = []
        invisible: list[str] = []
        for table, row_id in seeded.items():
            async with OrgSession(app, fx.org_b):
                seen_b = await app.fetchval(
                    f"SELECT count(*) FROM {table} WHERE id = $1::uuid", row_id
                )
            async with OrgSession(app, fx.org_a):
                seen_a = await app.fetchval(
                    f"SELECT count(*) FROM {table} WHERE id = $1::uuid", row_id
                )
            if seen_b:
                leaks.append(f"{table} visible to org B")
            if not seen_a:
                invisible.append(f"{table} invisible to its OWN org")

        if leaks:
            results.bad("7b", "org B sees NONE of org A's six fee rows",
                        "; ".join(leaks))
        else:
            results.ok("7b", "org B sees NONE of org A's six fee rows",
                       f"{len(seeded)} tables, one real row each")

        if invisible:
            results.bad(
                "7c", "org A DOES see its own six rows",
                "; ".join(invisible) + " — without this, a policy denying "
                "everything would pass 7b",
            )
        else:
            results.ok(
                "7c", "org A DOES see its own six rows",
                "the direction that rules out a policy that simply denies "
                "everything, which would pass 7b perfectly",
            )

        # The empty-GUC case: the NULLIF guard must default-DENY, not raise.
        async with OrgSession(app, None):
            blind = {
                table: await app.fetchval(
                    f"SELECT count(*) FROM {table} WHERE id = $1::uuid", row_id
                )
                for table, row_id in seeded.items()
            }
        if not any(blind.values()):
            results.ok(
                "7d", "with NO org GUC set, all six tables return ZERO rows",
                "on a pooled backend a custom GUC reverts to '' rather than "
                "NULL; without the NULLIF a bare ''::uuid cast RAISES instead "
                "of default-denying, and the error is easy to mistake for a "
                "connection fault",
            )
        else:
            results.bad("7d", "with NO org GUC set, all six tables return ZERO rows",
                        f"{ {k: v for k, v in blind.items() if v} }")

        # A write into the wrong org is refused by WITH CHECK, not just reads.
        try:
            async with OrgSession(app, fx.org_b):
                await app.execute(
                    "INSERT INTO public.fee_schedules (org_id, code, name, "
                    "  product_type, rate_type, billing_frequency, "
                    "  billing_timing, valuation_method) "
                    "VALUES ($1::uuid, $2, 'cross', 'ASSET_MANAGEMENT', 'BPS', "
                    "        'QUARTERLY', 'ARREARS', 'PERIOD_END')",
                    fx.org_a, fx.code("CROSSWRITE"),
                )
            results.bad("7e", "a write into another org is refused by WITH CHECK",
                        "the insert succeeded")
        except asyncpg.exceptions.InsufficientPrivilegeError:
            results.ok(
                "7e", "a write into another org is refused by WITH CHECK",
                "org B's connection cannot insert a row stamped org A, so RLS "
                "gates writes and not merely reads",
            )
    finally:
        await app.close()


# ═══════════════════════════════════════════════════════════════════════════
# Check 9 — the router is actually reachable on the real app
# ═══════════════════════════════════════════════════════════════════════════


def check_9(results: Results) -> None:
    """The ten endpoints are registered on ``main.app``, not merely importable.

    This check exists because the obvious version of it is WRONG on this
    codebase. ``main.app.routes`` reports 47 entries and not one of them has a
    ``.path`` for a feature router: every ``include_router`` call appends a
    lazy ``_IncludedRouter`` wrapper, so a scan for ``'/fee-schedules' in
    r.path`` finds nothing — for fee34, and equally for billing_groups,
    portfolio, and every other router that has shipped. "Zero routes found"
    would have been read as a registration failure and sent someone editing a
    ``main.py`` that was already correct.

    The real routes live on each wrapper's ``original_router``.
    """
    import main  # noqa: PLC0415 — imported here so an import error is a FAIL

    found: dict[str, set[str]] = {}
    wrappers = 0
    for route in main.app.routes:
        original = getattr(route, "original_router", None)
        if original is None:
            continue
        wrappers += 1
        for sub in original.routes:
            path = getattr(sub, "path", "")
            if path.startswith("/fee-schedules"):
                found.setdefault(path, set()).update(getattr(sub, "methods", []))

    expected = {
        ("/fee-schedules", "GET"),
        ("/fee-schedules", "POST"),
        ("/fee-schedules/{schedule_id}", "GET"),
        ("/fee-schedules/{schedule_id}", "PATCH"),
        ("/fee-schedules/{schedule_id}/assignments", "GET"),
        ("/fee-schedules/{schedule_id}/submit", "POST"),
        ("/fee-schedules/{schedule_id}/retire", "POST"),
        ("/fee-schedules/resolve/account/{account_id}", "GET"),
        ("/fee-schedules/assignments", "POST"),
        ("/fee-schedules/assignments/{assignment_id}/end", "POST"),
    }
    actual = {(p, m) for p, methods in found.items() for m in methods
              if m in {"GET", "POST", "PATCH", "PUT", "DELETE"}}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        results.bad(
            "9a", "all ten fee-schedule endpoints are registered on main.app",
            f"missing={missing} unexpected={extra}",
        )
    else:
        results.ok(
            "9a", "all ten fee-schedule endpoints are registered on main.app",
            f"found via {wrappers} lazy _IncludedRouter wrappers — "
            f"main.app.routes carries NO .path for any feature router, so the "
            f"naive scan reports zero for fee34 and for every router already "
            f"shipped. That false negative is the reason this check reads "
            f"original_router",
        )


# ═══════════════════════════════════════════════════════════════════════════
# Check 8 — nothing left behind
# ═══════════════════════════════════════════════════════════════════════════


async def check_8(results: Results, admin, before: dict[str, int]) -> None:
    after = await _counts(admin)
    drift = {t: (before[t], after[t]) for t in before if before[t] != after[t]}
    if drift:
        results.bad(
            "8", "every touched table's row count is back to its pre-test value",
            f"drift: {drift}",
        )
    else:
        results.ok(
            "8", "every touched table's row count is back to its pre-test value",
            f"{len(before)} tables, exact before/after equality. Teardown is by "
            f"this run's two fixture org ids — never a TRUNCATE",
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
    print(f"admin dsn:       {prov}")
    print(f"app_service dsn: {app_prov}\n")

    admin = await connect(dsn)
    fx = Fixture()
    before = await _counts(admin)
    try:
        await fx.create(admin)
        await check_1(results, admin)
        check_2(results)
        await check_3(results, admin, fx)
        await check_4(results, admin, fx)
        await check_5(results, admin, fx)
        await check_6(results, admin, fx)
        await check_7(results, admin, fx, app_dsn, app_prov)
        check_9(results)
    finally:
        try:
            await fx.teardown(admin)
        except Exception as exc:  # noqa: BLE001
            results.bad("T", "teardown ran cleanly", f"{type(exc).__name__}: {exc}")
        await check_8(results, admin, before)
        await admin.close()

    return results.summary()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
