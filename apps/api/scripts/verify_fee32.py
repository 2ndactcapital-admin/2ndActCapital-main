"""Sprint fee32 verification — position↔account linkage + household precedence.

Pass/fail only, no prompts, no interactive input. Run:

    python3 scripts/verify_fee32.py

WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Two disposable orgs, never the real ones.** Every fixture lives under orgs
  this run creates and deletes. Nothing writes to the default org.

* **RLS is proved on ``app_service``, never on ``postgres``.** ``postgres`` has
  ``rolbypassrls`` and every isolation check run on it passes vacuously. Check 7
  asserts ``rolbypassrls = False`` on the role it uses BEFORE it trusts a single
  denial, because otherwise "I could not see the other org's row" and "there was
  no row" are the same observation.

* **Check 5 compares against the REAL pre-sprint code**, not against a hand-
  written expectation of it. The previous revision of
  ``services/portfolio_precedence.py`` is extracted from git, imported as a
  separate module, and run over the SAME fixtures. The commit it extracts is
  located with ``git log -S`` on a marker string this sprint introduced, then
  stepped back one — so the check keeps working after this sprint commits, when
  ``HEAD`` is no longer the pre-sprint state. (A prior sprint's "pre-sprint"
  check silently started reading its own output the moment it committed.)

* **Teardown is by fixture org id, with an exact before/after row count as the
  backstop.** Never a TRUNCATE — several of these tables hold real production
  data on other deployments, and a truncate is a data-loss bug waiting for the
  first row.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import traceback
import uuid
from datetime import date, timedelta
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO_DIR = API_DIR.parent.parent

for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_site) not in sys.path:
        sys.path.insert(0, str(_site))
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import asyncpg  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from services.portfolio_account_link import (  # noqa: E402
    REASON_ACCOUNT_HAS_NO_OWNERS,
    REASON_OWNER_NOT_ACCOUNT_OWNER,
    AccountLinkError,
    check_account_link,
    list_account_link_exceptions,
    review_account_link_exception,
)
from services.portfolio_assets import create_asset, create_position  # noqa: E402
from services.portfolio_positions import update_position  # noqa: E402
from services.portfolio_precedence import (  # noqa: E402
    ORIGIN_DEFAULT,
    ORIGIN_HOUSEHOLD,
    ORIGIN_ORG_SETTING,
    TABLE_HOUSEHOLD_OVERRIDES,
    clear_household_source_order,
    resolve_holding,
    set_household_source_order,
)

# ── Fixture constants ───────────────────────────────────────────────────────

AS_OF = date(2026, 3, 31)

#: The three feeds the precedence checks rank. All three are real
#: `positions_source_chk` values — a token the CHECK rejects would make every
#: fixture insert fail for the wrong reason.
SRC_ADDEPAR = "reporting_tool_addepar"
SRC_ALTRUIST = "altruist"
SRC_MANUAL = "manual"

#: The org-level order these fixtures run under. Deliberately NOT the platform
#: default: check 5 has to distinguish "fell through to the org setting" from
#: "fell through to the platform default", and with the default installed as the
#: org setting those two are indistinguishable.
ORG_ORDER = [SRC_ADDEPAR, SRC_ALTRUIST, SRC_MANUAL]

#: A marker string introduced by THIS sprint into portfolio_precedence.py.
#: Used to locate the pre-sprint revision — see check 5.
SPRINT_MARKER = "TABLE_HOUSEHOLD_OVERRIDES"
PRECEDENCE_REL_PATH = "apps/api/services/portfolio_precedence.py"

#: Every table this run writes to. Check 8 compares each one's count before and
#: after. Listed explicitly rather than derived, so a table the script starts
#: touching without being added here shows up as a review question.
TOUCHED_TABLES = (
    "portfolio.positions",
    "portfolio.assets",
    "public.position_account_exceptions",
    "public.portfolio_precedence_household_overrides",
    "public.account_owners",
    "public.accounts",
    "public.households",
    "public.household_memberships",
    "public.org_settings",
    "public.entities",
    "public.organizations",
)


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
    it made through the real pool to a savepoint that was rolled back at the end.
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

    Org A's shape, and why each piece is there:

      household_override  — has an override row. Checks 4 and 6 live here.
      household_plain     — has NO override row. Check 5 lives here, and its
                            whole job is to be ordinary.

      account_owned       — under household_override, owned by entity_owner.
                            Its positions are the precedence fixtures.
      account_mismatch    — under household_override, owned by SOMEBODY ELSE.
                            Check 2's fixture.
      account_ownerless   — under household_override, with no owners at all.
                            Proves the two reason codes are really two.
      account_plain       — under household_plain, owned by entity_plain.

    Created as superuser: creating an organization is not something app_service
    is permitted to do, and should not be.
    """

    def __init__(self) -> None:
        tag = uuid.uuid4().hex[:8]
        self.tag = tag
        self.org_a = str(uuid.uuid4())
        self.org_b = str(uuid.uuid4())

        self.household_override = str(uuid.uuid4())
        self.household_plain = str(uuid.uuid4())
        self.household_b = str(uuid.uuid4())

        self.entity_owner = str(uuid.uuid4())
        self.entity_stranger = str(uuid.uuid4())
        self.entity_plain = str(uuid.uuid4())
        self.entity_direct = str(uuid.uuid4())
        self.entity_b = str(uuid.uuid4())

        self.account_owned = str(uuid.uuid4())
        self.account_mismatch = str(uuid.uuid4())
        self.account_ownerless = str(uuid.uuid4())
        self.account_plain = str(uuid.uuid4())
        self.account_b = str(uuid.uuid4())

        self.approver = str(uuid.uuid4())

        # Filled in during create()
        self.asset_a: str = ""
        self.asset_plain: str = ""
        self.asset_direct: str = ""

    async def create(self, conn) -> None:
        for org_id, slug in ((self.org_a, "a"), (self.org_b, "b")):
            await conn.execute(
                "INSERT INTO public.organizations (id, name, slug) "
                "VALUES ($1::uuid, $2, $3) ON CONFLICT (id) DO NOTHING",
                org_id, f"fee32 verify {slug} {self.tag}",
                f"fee32-verify-{slug}-{self.tag}",
            )

        for household_id, org_id, name in (
            (self.household_override, self.org_a, "Override Household"),
            (self.household_plain, self.org_a, "Plain Household"),
            (self.household_b, self.org_b, "Other Tenant Household"),
        ):
            await conn.execute(
                "INSERT INTO public.households (id, org_id, name) "
                "VALUES ($1::uuid, $2::uuid, $3) ON CONFLICT (id) DO NOTHING",
                household_id, org_id, f"fee32 {name} {self.tag}",
            )

        # `primary_household_id` is the ENTITY route into a household — the
        # fallback used when a position carries no account_id. entity_direct
        # deliberately sits under household_override with NO account, which is
        # what makes check 1's directly-held case a real precedence fixture and
        # not just a row that inserts.
        for entity_id, org_id, household_id, name in (
            (self.entity_owner, self.org_a, self.household_override, "Owner"),
            (self.entity_stranger, self.org_a, self.household_override, "Stranger"),
            (self.entity_direct, self.org_a, self.household_override, "Direct Holder"),
            (self.entity_plain, self.org_a, self.household_plain, "Plain Member"),
            (self.entity_b, self.org_b, self.household_b, "Other Tenant"),
        ):
            await conn.execute(
                "INSERT INTO public.entities "
                "  (id, org_id, entity_type, display_name, primary_household_id) "
                "VALUES ($1::uuid, $2::uuid, 'individual', $3, $4::uuid) "
                "ON CONFLICT (id) DO NOTHING",
                entity_id, org_id, f"fee32 {name} {self.tag}", household_id,
            )

        for account_id, org_id, household_id, primary_entity, label in (
            (self.account_owned, self.org_a, self.household_override,
             self.entity_owner, "OWNED"),
            (self.account_mismatch, self.org_a, self.household_override,
             self.entity_owner, "MISMATCH"),
            (self.account_ownerless, self.org_a, self.household_override,
             self.entity_owner, "ORPHAN"),
            (self.account_plain, self.org_a, self.household_plain,
             self.entity_plain, "PLAIN"),
            (self.account_b, self.org_b, self.household_b,
             self.entity_b, "OTHERTENANT"),
        ):
            await conn.execute(
                """
                INSERT INTO public.accounts
                    (id, org_id, account_number_masked, account_number_hash,
                     custodian_code, registration_type, tax_status,
                     primary_entity_id, household_id)
                VALUES ($1::uuid, $2::uuid, $3, $4, 'fee32_test', 'individual',
                        'taxable', $5::uuid, $6::uuid)
                ON CONFLICT (id) DO NOTHING
                """,
                account_id, org_id, f"****{label}", f"hash-{account_id}",
                primary_entity, household_id,
            )

        # account_ownerless is deliberately absent from this list.
        for account_id, org_id, entity_id in (
            (self.account_owned, self.org_a, self.entity_owner),
            (self.account_mismatch, self.org_a, self.entity_owner),
            (self.account_plain, self.org_a, self.entity_plain),
            (self.account_b, self.org_b, self.entity_b),
        ):
            await conn.execute(
                """
                INSERT INTO public.account_owners
                    (org_id, account_id, entity_id, ownership_pct, role)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 100, 'primary')
                """,
                org_id, account_id, entity_id,
            )

        # The org-level precedence order. Written directly rather than through
        # `org_settings.set_setting` because this is a fixture, not a test of
        # that write path — but through the SAME (org_id, setting_key) unique
        # the service uses, so the read path cannot tell the difference.
        await conn.execute(
            """
            INSERT INTO public.org_settings
                (org_id, setting_key, setting_value, category)
            VALUES ($1::uuid, 'portfolio.precedence.source_order', $2::jsonb,
                    'portfolio')
            ON CONFLICT (org_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value
            """,
            self.org_a, json.dumps(ORG_ORDER),
        )

        async with OrgSession(conn, self.org_a):
            self.asset_a = await create_asset(
                conn, org_id=self.org_a, name=f"fee32 Asset A {self.tag}",
                asset_type="equity", ownership_basis="units",
            )
            self.asset_plain = await create_asset(
                conn, org_id=self.org_a, name=f"fee32 Asset Plain {self.tag}",
                asset_type="equity", ownership_basis="units",
            )
            self.asset_direct = await create_asset(
                conn, org_id=self.org_a, name=f"fee32 Asset Direct {self.tag}",
                asset_type="real_estate", ownership_basis="value",
            )

    async def teardown(self, conn) -> None:
        """FK-safe order, scoped to THIS run's two org ids. Never a TRUNCATE.

        Runs in a ``finally`` so a failed check still cleans up: two disposable
        orgs left behind per failed run accumulate into exactly the orphan mess
        a prior sprint had to sweep by hand.

        ``portfolio.transactions`` and ``document_record_links`` are swept too
        even though this script writes neither — ``update_position`` carries
        document links forward, and a future edit to these fixtures that starts
        producing them would otherwise block the position delete with an FK
        error that reads as a mysterious teardown failure.
        """
        orgs = [self.org_a, self.org_b]
        for statement in (
            "DELETE FROM public.position_account_exceptions "
            "  WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.document_record_links WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM portfolio.transactions WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM portfolio.external_references WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM portfolio.positions WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM portfolio.asset_identifiers WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM portfolio.assets WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.portfolio_precedence_household_overrides "
            "  WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.account_owners WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.accounts WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.org_settings WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.household_memberships WHERE household_id = ANY("
            "  SELECT id FROM public.households WHERE org_id = ANY($1::uuid[]))",
            "DELETE FROM public.entities WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.households WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.organizations WHERE id = ANY($1::uuid[])",
        ):
            await conn.execute(statement, orgs)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


async def _counts(conn) -> dict[str, int]:
    out = {}
    for table in TOUCHED_TABLES:
        out[table] = int(await conn.fetchval(f"SELECT count(*) FROM {table}"))
    return out


async def _superseded(conn, position_ids: dict[str, str]) -> dict[str, str | None]:
    """``{label: superseded_by_source}`` read back from the table itself.

    Read from the DATABASE rather than from the PrecedenceOutcome. The outcome
    reports what the function BELIEVES it wrote; the column is what is actually
    there, and those are the two things this sprint has to keep equal.
    """
    rows = await conn.fetch(
        "SELECT id::text AS id, superseded_by_source FROM portfolio.positions "
        "WHERE id = ANY($1::uuid[])",
        list(position_ids.values()),
    )
    by_id = {r["id"]: r["superseded_by_source"] for r in rows}
    return {label: by_id.get(pid) for label, pid in position_ids.items()}


async def _exceptions_for(conn, org_id: str, position_id: str) -> list[dict]:
    rows = await conn.fetch(
        "SELECT reason_code, reason, account_id::text AS account_id, "
        "       owner_entity_id::text AS owner_entity_id, detail "
        "FROM public.position_account_exceptions "
        "WHERE org_id = $1::uuid AND position_id = $2::uuid",
        org_id, position_id,
    )
    return [dict(r) for r in rows]


def _presprint_module(results: Results):
    """Load the revision of portfolio_precedence.py from BEFORE this sprint.

    Located by ``git log -S SPRINT_MARKER`` on the file — the OLDEST commit that
    introduced the marker — then stepped back one commit. Anchoring on a marker
    rather than on ``HEAD`` is what makes this check survive its own sprint
    committing: after the commit lands, ``HEAD`` IS the post-sprint state and a
    HEAD-based comparison would be comparing the sprint to itself and passing
    vacuously. (That exact contamination was recorded on a previous sprint.)

    Returns ``None`` — with a BLOCKED-worthy reason printed — rather than
    raising, so an unavailable git history degrades one check instead of the run.
    """
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO_DIR, capture_output=True, text=True, check=False,
        ).stdout.strip()

    introduced = git(
        "log", "-S", SPRINT_MARKER, "--format=%H", "--", PRECEDENCE_REL_PATH
    ).splitlines()
    if introduced:
        # `git log` is newest-first; the LAST line is the oldest commit that
        # introduced the marker, and its parent is the pre-sprint state.
        ref = f"{introduced[-1]}^"
    else:
        # The sprint has not committed yet, so HEAD still IS the pre-sprint
        # revision of this file. Reported, not assumed silently.
        ref = "HEAD"
    print(f"            pre-sprint revision resolved to {ref} "
          f"({'marker found in history' if introduced else 'marker uncommitted'})")

    source = git("show", f"{ref}:{PRECEDENCE_REL_PATH}")
    if not source or SPRINT_MARKER in source:
        return None, (
            f"could not extract a pre-sprint {PRECEDENCE_REL_PATH} at {ref} "
            f"(empty, or it already contains this sprint's marker)"
        )

    path = pathlib.Path(tempfile.mkdtemp()) / "portfolio_precedence_presprint.py"
    path.write_text(source)
    name = "portfolio_precedence_presprint"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec_module: `dataclasses` resolves a frozen class's
    # `__set_name__`/field machinery through `sys.modules[cls.__module__]`, and
    # a module that is not yet registered makes that lookup return None. The
    # symptom is an opaque AttributeError on NoneType from inside the stdlib.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(name, None)
        return None, (
            f"pre-sprint module failed to import: {type(exc).__name__}: {exc}\n"
            + "".join(traceback.format_exc())
        )
    return module, None


# ═══════════════════════════════════════════════════════════════════════════
# Checks
# ═══════════════════════════════════════════════════════════════════════════


async def check_1(results: Results, admin, fx: Fixture) -> None:
    """account_id: exists, nullable, FK to public.accounts — and a NULL one
    still inserts and still resolves."""
    col = await admin.fetchrow(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema='portfolio' AND table_name='positions' "
        "  AND column_name='account_id'"
    )
    fk = await admin.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid='portfolio.positions'::regclass AND contype='f' "
        "  AND conname='positions_account_id_fkey'"
    )
    problems = []
    if col is None:
        problems.append("portfolio.positions.account_id does not exist")
    else:
        if col["data_type"] != "uuid":
            problems.append(f"account_id is {col['data_type']}, expected uuid")
        if col["is_nullable"] != "YES":
            problems.append("account_id is NOT NULL — it must be optional")
    if not fk or "accounts(id)" not in fk:
        problems.append(f"FK to public.accounts missing or wrong: {fk!r}")

    # The directly-held case, end to end: a position with NO account, on an
    # entity whose primary_household_id DOES point at the override household.
    # That combination is the one that would break if the household lookup
    # assumed an account_id was always present.
    async with OrgSession(admin, fx.org_a):
        direct_id = await create_position(
            admin, org_id=fx.org_a, owner_entity_id=fx.entity_direct,
            asset_id=fx.asset_direct, as_of_date=AS_OF,
            authority="stated", source_system=SRC_MANUAL,
            ownership_basis="value", market_value=Decimal("750000.00"),
        )
        stored = await admin.fetchval(
            "SELECT account_id FROM portfolio.positions WHERE id = $1::uuid",
            direct_id,
        )
        if stored is not None:
            problems.append(f"a position written with no account_id has {stored}")
        try:
            outcome = await resolve_holding(
                admin, fx.org_a, owner_entity_id=fx.entity_direct,
                asset_id=fx.asset_direct, as_of_date=AS_OF,
            )
        except Exception as exc:  # noqa: BLE001
            problems.append(f"resolving a NULL-account holding raised {exc!r}")
            outcome = None
        if outcome is None:
            problems.append("resolve_holding returned None for a real holding")
        elif outcome.winner_position_id != direct_id:
            problems.append("the lone candidate did not win its own resolution")

    if problems:
        results.bad(1, "account_id is optional and a NULL one still resolves",
                    "; ".join(problems))
    else:
        results.ok(
            1, "account_id is optional and a NULL one still resolves",
            f"uuid NULL, {fk}; directly-held position {direct_id[:8]} inserted "
            f"with account_id=NULL and resolved cleanly",
        )


async def check_2(results: Results, admin, fx: Fixture) -> dict[str, str]:
    """A mismatched account_id is WRITTEN and produces a reviewable exception."""
    problems = []
    written: dict[str, str] = {}

    async with OrgSession(admin, fx.org_a):
        # account_mismatch is owned by entity_owner; this position's owner is
        # entity_stranger. Owners exist, none of them is this one.
        mismatch_id = await create_position(
            admin, org_id=fx.org_a, owner_entity_id=fx.entity_stranger,
            asset_id=fx.asset_a, as_of_date=AS_OF,
            authority="custodial", source_system=SRC_ADDEPAR,
            ownership_basis="units", quantity=Decimal("100"),
            account_id=fx.account_mismatch,
        )
        written["mismatch"] = mismatch_id

        # account_ownerless has NO active owners at all — a different finding
        # with a different fix, and it must not be folded into the first code.
        ownerless_id = await create_position(
            admin, org_id=fx.org_a, owner_entity_id=fx.entity_stranger,
            asset_id=fx.asset_plain, as_of_date=AS_OF,
            authority="custodial", source_system=SRC_ADDEPAR,
            ownership_basis="units", quantity=Decimal("7"),
            account_id=fx.account_ownerless,
        )
        written["ownerless"] = ownerless_id

    row = await admin.fetchrow(
        "SELECT account_id::text AS account_id, valid_to, system_to "
        "FROM portfolio.positions WHERE id = $1::uuid", mismatch_id,
    )
    if row is None:
        problems.append("the mismatched position was NOT written — it must be")
    else:
        if row["account_id"] != fx.account_mismatch:
            problems.append(
                f"the position was written without its account_id "
                f"({row['account_id']!r})"
            )
        if row["valid_to"] is not None or row["system_to"] is not None:
            problems.append("the position was written already closed")

    excs = await _exceptions_for(admin, fx.org_a, mismatch_id)
    if len(excs) != 1:
        problems.append(f"expected exactly 1 exception, got {len(excs)}")
    elif excs[0]["reason_code"] != REASON_OWNER_NOT_ACCOUNT_OWNER:
        problems.append(
            f"reason_code is {excs[0]['reason_code']!r}, expected "
            f"{REASON_OWNER_NOT_ACCOUNT_OWNER!r}"
        )
    else:
        detail = excs[0]["detail"]
        detail = json.loads(detail) if isinstance(detail, str) else detail
        if detail.get("account_owner_entity_ids") != [fx.entity_owner]:
            problems.append(
                f"the exception does not record who DOES own the account: "
                f"{detail.get('account_owner_entity_ids')}"
            )

    ownerless_excs = await _exceptions_for(admin, fx.org_a, ownerless_id)
    if len(ownerless_excs) != 1:
        problems.append(
            f"expected 1 exception on the ownerless account, got "
            f"{len(ownerless_excs)}"
        )
    elif ownerless_excs[0]["reason_code"] != REASON_ACCOUNT_HAS_NO_OWNERS:
        problems.append(
            f"an account with no owners reported "
            f"{ownerless_excs[0]['reason_code']!r} rather than "
            f"{REASON_ACCOUNT_HAS_NO_OWNERS!r} — the two findings must stay "
            f"distinguishable"
        )

    # And the exception is genuinely REVIEWABLE: it comes back from the read
    # path the API serves, not merely from a table this script knows about.
    listing = await list_account_link_exceptions(admin, fx.org_a)
    listed = {e["position_id"] for e in listing["exceptions"]}
    if mismatch_id not in listed or ownerless_id not in listed:
        problems.append(
            "the exceptions are in the table but do NOT come back from "
            "list_account_link_exceptions — a row nothing reads is not reviewable"
        )

    # Idempotent: re-validating the same position must not append a second
    # identical OPEN row.
    async with OrgSession(admin, fx.org_a):
        from services.portfolio_account_link import validate_position_account
        await validate_position_account(
            admin, fx.org_a, position_id=mismatch_id,
            account_id=fx.account_mismatch, owner_entity_id=fx.entity_stranger,
        )
    if len(await _exceptions_for(admin, fx.org_a, mismatch_id)) != 1:
        problems.append("re-validating duplicated the open exception")

    if problems:
        results.bad(2, "an owner mismatch is written AND flagged, never refused",
                    "; ".join(problems))
    else:
        results.ok(
            2, "an owner mismatch is written AND flagged, never refused",
            f"position {mismatch_id[:8]} persisted with its account_id and "
            f"raised one {REASON_OWNER_NOT_ACCOUNT_OWNER} exception naming the "
            f"real owner; the ownerless account raised "
            f"{REASON_ACCOUNT_HAS_NO_OWNERS} instead; re-validation is idempotent",
        )
    return written


async def check_2b(results: Results, admin, fx: Fixture) -> None:
    """A cross-tenant account_id is REFUSED, and no position is written.

    Not in the sprint's own list, and it is the one case where "written and
    flagged" would be wrong: ``positions_account_id_fkey`` references
    ``accounts(id)`` with no org predicate, so another tenant's account id
    satisfies the FK. If this were a warning, the exception list would be the
    audit trail of a cross-tenant reference that already succeeded.
    """
    problems = []
    before = int(await admin.fetchval(
        "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid",
        fx.org_a,
    ))
    raised = None
    try:
        async with OrgSession(admin, fx.org_a):
            await create_position(
                admin, org_id=fx.org_a, owner_entity_id=fx.entity_owner,
                asset_id=fx.asset_a, as_of_date=AS_OF,
                authority="custodial", source_system=SRC_ADDEPAR,
                ownership_basis="units", quantity=Decimal("1"),
                account_id=fx.account_b,          # org B's account
            )
    except AccountLinkError as exc:
        raised = str(exc)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"raised {type(exc).__name__} rather than AccountLinkError")

    if raised is None and not problems:
        problems.append("a cross-tenant account_id was ACCEPTED")
    after = int(await admin.fetchval(
        "SELECT count(*) FROM portfolio.positions WHERE org_id = $1::uuid",
        fx.org_a,
    ))
    if after != before:
        problems.append(f"the refused write still left {after - before} row(s)")

    if problems:
        results.bad("2b", "a cross-tenant account_id is refused, not flagged",
                    "; ".join(problems))
    else:
        results.ok(
            "2b", "a cross-tenant account_id is refused, not flagged",
            f"AccountLinkError raised and org A's position count is unchanged "
            f"at {after}",
        )


async def check_3(results: Results, admin, fx: Fixture) -> str:
    """A genuine owner produces NO exception — and an edit keeps it that way."""
    problems = []
    async with OrgSession(admin, fx.org_a):
        good_id = await create_position(
            admin, org_id=fx.org_a, owner_entity_id=fx.entity_owner,
            asset_id=fx.asset_a, as_of_date=AS_OF - timedelta(days=1),
            authority="custodial", source_system=SRC_ADDEPAR,
            ownership_basis="units", quantity=Decimal("42"),
            account_id=fx.account_owned,
        )

    if await _exceptions_for(admin, fx.org_a, good_id):
        problems.append("a legitimate owner raised an exception")

    check = await check_account_link(
        admin, fx.org_a, account_id=fx.account_owned,
        owner_entity_id=fx.entity_owner,
    )
    if not check.ok:
        problems.append(f"check_account_link says not-ok: {check.reason}")

    # The regression Part 1's column silently created: `update_position`
    # re-inserts a successor through `create_position` from an explicit column
    # list. Before this sprint that list had no account_id, so the FIRST edit of
    # any linked position would quietly unlink it.
    async with OrgSession(admin, fx.org_a):
        edited_id = await update_position(
            admin, org_id=fx.org_a, position_id=good_id,
            changes={"quantity": Decimal("43")},
        )
    carried = await admin.fetchval(
        "SELECT account_id::text FROM portfolio.positions WHERE id = $1::uuid",
        edited_id,
    )
    if carried != fx.account_owned:
        problems.append(
            f"an edit dropped the account link: successor carries {carried!r}, "
            f"expected {fx.account_owned}"
        )
    if await _exceptions_for(admin, fx.org_a, edited_id):
        problems.append("editing a correctly-linked position raised an exception")

    if problems:
        results.bad(3, "a genuine owner produces no exception, and edits carry the link",
                    "; ".join(problems))
    else:
        results.ok(
            3, "a genuine owner produces no exception, and edits carry the link",
            f"position {good_id[:8]} clean; its restated successor "
            f"{edited_id[:8]} still carries account_id and is still clean",
        )
    return edited_id


async def _make_conflict(admin, fx: Fixture) -> dict[str, str]:
    """Three positions on ONE holding key from three feeds, all in the override
    household via ``account_owned``. The precedence fixture for checks 4-6."""
    ids: dict[str, str] = {}
    async with OrgSession(admin, fx.org_a):
        for label, source, qty in (
            ("addepar", SRC_ADDEPAR, "1000"),
            ("altruist", SRC_ALTRUIST, "1001"),
            ("manual", SRC_MANUAL, "1002"),
        ):
            ids[label] = await create_position(
                admin, org_id=fx.org_a, owner_entity_id=fx.entity_owner,
                asset_id=fx.asset_a, as_of_date=AS_OF,
                authority="custodial" if label != "manual" else "manual",
                source_system=source, ownership_basis="units",
                quantity=Decimal(qty), account_id=fx.account_owned,
            )
    return ids


async def check_4(results: Results, admin, fx: Fixture, ids: dict[str, str]) -> None:
    """A household override beats the org-level order on the same fixture."""
    problems = []

    # First WITHOUT an override, to establish what the org level actually picks.
    # Asserting the override's winner without this would prove only that some
    # order was applied, not that the override changed the answer.
    async with OrgSession(admin, fx.org_a):
        base = await resolve_holding(
            admin, fx.org_a, owner_entity_id=fx.entity_owner,
            asset_id=fx.asset_a, as_of_date=AS_OF,
        )
    if base.winner_position_id != ids["addepar"]:
        problems.append(
            f"the org order {ORG_ORDER} should pick addepar; it picked "
            f"{base.winner_source_system}"
        )
    if base.order_origin != ORIGIN_ORG_SETTING:
        problems.append(
            f"with no override the order origin should be {ORIGIN_ORG_SETTING}, "
            f"got {base.order_origin}"
        )

    # Now the override, which disagrees with the org order about the winner.
    async with OrgSession(admin, fx.org_a):
        await set_household_source_order(
            admin, fx.org_a, household_id=fx.household_override,
            source_order=[SRC_ALTRUIST, SRC_ADDEPAR, SRC_MANUAL],
            reason="fee32 verification — household trusts Altruist",
            approved_by=fx.approver,
        )
        with_override = await resolve_holding(
            admin, fx.org_a, owner_entity_id=fx.entity_owner,
            asset_id=fx.asset_a, as_of_date=AS_OF,
        )

    if with_override.winner_position_id != ids["altruist"]:
        problems.append(
            f"the household override should pick altruist; it picked "
            f"{with_override.winner_source_system}"
        )
    if with_override.order_origin != ORIGIN_HOUSEHOLD:
        problems.append(
            f"order_origin is {with_override.order_origin}, expected "
            f"{ORIGIN_HOUSEHOLD}"
        )
    if with_override.household_id != fx.household_override:
        problems.append(
            f"household_id is {with_override.household_id}, expected the "
            f"account's household"
        )
    if list(with_override.order) != [SRC_ALTRUIST, SRC_ADDEPAR, SRC_MANUAL]:
        problems.append(f"the applied order is {list(with_override.order)}")
    if base.winner_position_id == with_override.winner_position_id:
        problems.append(
            "the org order and the household order picked the SAME winner — "
            "this fixture proves nothing about precedence between them"
        )

    if problems:
        results.bad(4, "a household override overrules the org-level order",
                    "; ".join(problems))
    else:
        results.ok(
            4, "a household override overrules the org-level order",
            f"same three rows, same holding key: org order {ORG_ORDER} → "
            f"{base.winner_source_system}; household override "
            f"[altruist, addepar, manual] → {with_override.winner_source_system} "
            f"(origin={with_override.order_origin})",
        )


async def check_5(results: Results, admin, fx: Fixture) -> None:
    """A household with NO override resolves exactly as the PRE-SPRINT code did.

    Compared against the real previous revision of the module, extracted from
    git and run over the same rows — not against a written-down expectation of
    what it used to do.
    """
    module, reason = _presprint_module(results)
    if module is None:
        results.blocked(5, "no-override households are unchanged", reason)
        return

    problems = []
    # A conflicting pair on the PLAIN household — no override row exists or will.
    async with OrgSession(admin, fx.org_a):
        plain_ids = {}
        for label, source, qty in (
            ("addepar", SRC_ADDEPAR, "500"),
            ("altruist", SRC_ALTRUIST, "501"),
        ):
            plain_ids[label] = await create_position(
                admin, org_id=fx.org_a, owner_entity_id=fx.entity_plain,
                asset_id=fx.asset_plain, as_of_date=AS_OF,
                authority="custodial", source_system=source,
                ownership_basis="units", quantity=Decimal(qty),
                account_id=fx.account_plain,
            )

        # apply=False on BOTH sides: this compares the DECISION, and letting
        # either side write would mean the second one ran against a table the
        # first had already changed.
        old = await module.resolve_precedence(
            admin, fx.org_a, list(plain_ids.values()), apply=False
        )
        new = await resolve_holding(
            admin, fx.org_a, owner_entity_id=fx.entity_plain,
            asset_id=fx.asset_plain, as_of_date=AS_OF, apply=False,
        )

    if old.winner_position_id != new.winner_position_id:
        problems.append(
            f"winner differs: pre-sprint {old.winner_source_system}, "
            f"post-sprint {new.winner_source_system}"
        )
    if tuple(old.order) != tuple(new.order):
        problems.append(f"order differs: {list(old.order)} vs {list(new.order)}")
    if old.order_is_default != new.order_is_default:
        problems.append(
            f"order_is_default differs: {old.order_is_default} vs "
            f"{new.order_is_default}"
        )
    if old.loser_position_ids != new.loser_position_ids:
        problems.append("the loser set differs")
    if new.order_origin == ORIGIN_HOUSEHOLD:
        problems.append(
            "a household with no override row resolved through the household "
            "level — the override lookup is matching something it should not"
        )

    # The same comparison for the OVERRIDE household, where the two SHOULD
    # disagree. Without this the check above passes just as well against a
    # household layer that never fires at all.
    async with OrgSession(admin, fx.org_a):
        old_over = await module.resolve_precedence(
            admin, fx.org_a,
            [r["id"] for r in await admin.fetch(
                "SELECT id::text AS id FROM portfolio.positions "
                "WHERE org_id=$1::uuid AND owner_entity_id=$2::uuid "
                "  AND asset_id=$3::uuid AND as_of_date=$4 "
                "  AND valid_to IS NULL AND system_to IS NULL",
                fx.org_a, fx.entity_owner, fx.asset_a, AS_OF,
            )],
            apply=False,
        )
        new_over = await resolve_holding(
            admin, fx.org_a, owner_entity_id=fx.entity_owner,
            asset_id=fx.asset_a, as_of_date=AS_OF, apply=False,
        )
    if old_over.winner_position_id == new_over.winner_position_id:
        problems.append(
            "the OVERRIDE household resolved identically under the pre-sprint "
            "code too — the fixture does not distinguish the two code paths, so "
            "the no-override match above is not evidence of anything"
        )

    if problems:
        results.bad(5, "no-override households are unchanged", "; ".join(problems))
    else:
        results.ok(
            5, "no-override households are unchanged",
            f"plain household: pre-sprint and post-sprint both pick "
            f"{new.winner_source_system} with order {list(new.order)} "
            f"(origin={new.order_origin}); on the OVERRIDE household the same "
            f"comparison DIVERGES ({old_over.winner_source_system} → "
            f"{new_over.winner_source_system}), so the two code paths are "
            f"genuinely distinguishable",
        )


async def check_6(results: Results, admin, fx: Fixture, ids: dict[str, str]) -> None:
    """superseded_by_source across FOUR states: none → added → changed → removed.

    Each transition must both mark the new losers AND clear the new winner. A
    row that lost a previous resolution and wins this one but keeps its stale
    flag is invisible to every downstream reader — which is the actual failure
    the ``rows_cleared`` half of the function exists to prevent.
    """
    problems = []
    observed = []

    async def apply_and_read(label: str, expect_winner: str, expect_origin: str):
        async with OrgSession(admin, fx.org_a):
            outcome = await resolve_holding(
                admin, fx.org_a, owner_entity_id=fx.entity_owner,
                asset_id=fx.asset_a, as_of_date=AS_OF,
            )
        marks = await _superseded(admin, ids)
        observed.append((label, outcome.winner_source_system, marks))
        if outcome.winner_position_id != ids[expect_winner]:
            problems.append(
                f"{label}: winner is {outcome.winner_source_system}, expected "
                f"{expect_winner}"
            )
        if outcome.order_origin != expect_origin:
            problems.append(
                f"{label}: origin is {outcome.order_origin}, expected "
                f"{expect_origin}"
            )
        if marks[expect_winner] is not None:
            problems.append(
                f"{label}: the WINNER is still flagged superseded_by_source="
                f"{marks[expect_winner]!r} — a stale flag on the answer hides it "
                f"from every reader that filters on it"
            )
        winning_source = outcome.winner_source_system
        for other, value in marks.items():
            if other == expect_winner:
                continue
            if value != winning_source:
                problems.append(
                    f"{label}: loser {other} is marked {value!r}, expected "
                    f"{winning_source!r}"
                )
        return outcome

    # State 1 — the override from check 4 is still active: altruist wins.
    await apply_and_read("override v1 (altruist first)", "altruist", ORIGIN_HOUSEHOLD)

    # State 2 — CHANGED. A different order, a different winner. Not merely a
    # re-save of the same order, which would prove only that nothing broke.
    async with OrgSession(admin, fx.org_a):
        await set_household_source_order(
            admin, fx.org_a, household_id=fx.household_override,
            source_order=[SRC_MANUAL, SRC_ALTRUIST, SRC_ADDEPAR],
            reason="fee32 verification — household re-pointed at manual entry",
            approved_by=fx.approver,
        )
    await apply_and_read("override v2 (manual first)", "manual", ORIGIN_HOUSEHOLD)

    # The superseded v1 row must still be there with its own reason intact —
    # that is the whole audit value of a system-axis change.
    history = await admin.fetch(
        f"SELECT reason, system_to FROM {TABLE_HOUSEHOLD_OVERRIDES} "
        f"WHERE org_id=$1::uuid AND household_id=$2::uuid ORDER BY system_from",
        fx.org_a, fx.household_override,
    )
    if len(history) != 2:
        problems.append(
            f"changing the override left {len(history)} row(s); the previous "
            f"policy decision must be kept, not overwritten"
        )
    elif history[0]["system_to"] is None or history[1]["system_to"] is not None:
        problems.append("the override history is not closed on the system axis")

    # State 3 — REMOVED. Back to the org order, which picks addepar.
    async with OrgSession(admin, fx.org_a):
        cleared = await clear_household_source_order(
            admin, fx.org_a, household_id=fx.household_override
        )
    if not cleared:
        problems.append("clear_household_source_order reported nothing to clear")
    await apply_and_read("override removed", "addepar", ORIGIN_ORG_SETTING)

    if problems:
        results.bad(6, "superseded_by_source flips across all four states",
                    "; ".join(problems))
    else:
        detail = " | ".join(
            f"{label} → {winner} (marks: "
            + ", ".join(f"{k}={v or 'NULL'}" for k, v in sorted(marks.items()))
            + ")"
            for label, winner, marks in observed
        )
        results.ok(6, "superseded_by_source flips across all four states", detail)


async def check_7(results: Results, admin, fx: Fixture, app_dsn: str | None,
                  app_prov: str) -> None:
    """Cross-org isolation on the override table, under app_service."""
    if app_dsn is None:
        results.blocked(
            7, "cross-org isolation on the override table",
            f"no working app_service DSN — {app_prov}. RLS cannot be proved on "
            f"the postgres DSN: it has rolbypassrls and every denial would be "
            f"vacuous.",
        )
        return

    problems = []
    # Org B gets a real override row, written as superuser, so there IS
    # something for org A to fail to see. An isolation check against an empty
    # table proves nothing.
    async with OrgSession(admin, fx.org_b):
        await set_household_source_order(
            admin, fx.org_b, household_id=fx.household_b,
            source_order=[SRC_ALTRUIST, SRC_ADDEPAR],
            reason="fee32 verification — other tenant's policy",
            approved_by=fx.approver,
        )
    planted = int(await admin.fetchval(
        f"SELECT count(*) FROM {TABLE_HOUSEHOLD_OVERRIDES} WHERE org_id=$1::uuid",
        fx.org_b,
    ))
    if planted != 1:
        problems.append(f"the org B fixture row was not planted ({planted})")

    conn = await connect(app_dsn)
    try:
        role = await conn.fetchval("SELECT current_user")
        bypass = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        superuser = await conn.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        )
        # Asserted FIRST and on its own. If this role bypassed RLS, every
        # denial below would be a denial that never happened.
        if bypass or superuser:
            problems.append(
                f"the isolation role {role!r} has rolbypassrls={bypass} "
                f"rolsuper={superuser} — every check below would pass vacuously"
            )

        async with OrgSession(conn, fx.org_a):
            visible_b = int(await conn.fetchval(
                f"SELECT count(*) FROM {TABLE_HOUSEHOLD_OVERRIDES} "
                f"WHERE org_id = $1::uuid", fx.org_b,
            ))
            total_visible = int(await conn.fetchval(
                f"SELECT count(*) FROM {TABLE_HOUSEHOLD_OVERRIDES}"
            ))
            own = int(await conn.fetchval(
                f"SELECT count(*) FROM {TABLE_HOUSEHOLD_OVERRIDES} "
                f"WHERE org_id = $1::uuid", fx.org_a,
            ))
        if visible_b:
            problems.append(f"org A can SELECT {visible_b} of org B's overrides")
        # Prove the READ is not simply broken: under org A's context the role
        # must still see org A's OWN rows. A policy that denies everything
        # passes an isolation test and fails the product.
        if own == 0:
            problems.append(
                "org A cannot see its OWN overrides either — the read is denied, "
                "not isolated, and the negative result above proves nothing"
            )
        if total_visible != own:
            problems.append(
                f"an unqualified SELECT returned {total_visible} rows while org "
                f"A owns {own} — rows from another tenant leaked"
            )

        # WITH CHECK: writing another org's row from this org's context.
        refused = False
        try:
            async with OrgSession(conn, fx.org_a):
                await conn.execute(
                    f"INSERT INTO {TABLE_HOUSEHOLD_OVERRIDES} "
                    f"  (org_id, household_id, source_order, reason, approved_by) "
                    f"VALUES ($1::uuid, $2::uuid, $3::jsonb, 'rls probe', $4::uuid)",
                    fx.org_b, fx.household_b, json.dumps([SRC_MANUAL]), fx.approver,
                )
        except asyncpg.InsufficientPrivilegeError:
            refused = True
        except Exception as exc:  # noqa: BLE001
            problems.append(f"the cross-org INSERT failed unexpectedly: {exc!r}")
            refused = True
        if not refused:
            problems.append("org A INSERTED a row into org B — WITH CHECK is inert")

        # And the same isolation on the sprint's other new table.
        async with OrgSession(conn, fx.org_a):
            leaked_exc = int(await conn.fetchval(
                "SELECT count(*) FROM public.position_account_exceptions "
                "WHERE org_id = $1::uuid", fx.org_b,
            ))
            own_exc = int(await conn.fetchval(
                "SELECT count(*) FROM public.position_account_exceptions "
                "WHERE org_id = $1::uuid", fx.org_a,
            ))
        if leaked_exc:
            problems.append(
                f"position_account_exceptions leaked {leaked_exc} org B rows"
            )
        if own_exc == 0:
            problems.append(
                "app_service cannot read org A's OWN position_account_exceptions "
                "— the grant or the policy is wrong"
            )
    finally:
        await conn.close()

    # Undo the org B row through the superuser connection so teardown's counts
    # stay exact regardless of what happened above.
    if problems:
        results.bad(7, "cross-org isolation on the override table",
                    "; ".join(problems))
    else:
        results.ok(
            7, "cross-org isolation on the override table",
            f"role {role} (rolbypassrls={bypass}); under org A's GUC: 0 of org "
            f"B's {planted} override rows visible, {own} of org A's own visible, "
            f"unqualified SELECT returns exactly those {total_visible}, "
            f"cross-org INSERT refused by WITH CHECK; "
            f"position_account_exceptions isolates the same way "
            f"({own_exc} own, 0 foreign)",
        )


async def check_8(results: Results, admin, before: dict[str, int]) -> None:
    """Teardown proof: every touched table is back to its pre-test count."""
    after = await _counts(admin)
    drift = {
        table: (before[table], after[table])
        for table in TOUCHED_TABLES
        if before[table] != after[table]
    }
    if drift:
        results.bad(
            8, "teardown restored every touched table",
            "; ".join(f"{t}: {b} → {a}" for t, (b, a) in sorted(drift.items())),
        )
    else:
        results.ok(
            8, "teardown restored every touched table",
            ", ".join(f"{t.split('.')[-1]}={after[t]}" for t in TOUCHED_TABLES),
        )


async def check_extra_review(results: Results, admin, fx: Fixture,
                             mismatch_id: str) -> None:
    """Closing an exception really closes it, and a re-raise is a NEW finding.

    The partial unique index is on OPEN rows only. That is deliberate and worth
    proving: a mismatch that recurs after somebody signed it off is new
    information, and an index over all rows would swallow it silently.
    """
    problems = []
    listing = await list_account_link_exceptions(admin, fx.org_a)
    target = next(
        (e for e in listing["exceptions"] if e["position_id"] == mismatch_id), None
    )
    if target is None:
        problems.append("the open exception is missing from the review list")
    else:
        closed = await review_account_link_exception(
            admin, fx.org_a, exception_id=target["id"], reviewed_by=fx.approver
        )
        if not closed:
            problems.append("review_account_link_exception refused to close it")
        again = await review_account_link_exception(
            admin, fx.org_a, exception_id=target["id"], reviewed_by=fx.approver
        )
        if again:
            problems.append("closing an already-closed exception succeeded twice")

        open_now = await list_account_link_exceptions(admin, fx.org_a)
        if any(e["position_id"] == mismatch_id for e in open_now["exceptions"]):
            problems.append("the closed exception is still in the OPEN list")
        with_closed = await list_account_link_exceptions(
            admin, fx.org_a, include_reviewed=True
        )
        if not any(e["position_id"] == mismatch_id
                   for e in with_closed["exceptions"]):
            problems.append("the closed exception vanished from the full list")

        # Re-raise: the same mismatch after a close is a new open row.
        async with OrgSession(admin, fx.org_a):
            from services.portfolio_account_link import validate_position_account
            await validate_position_account(
                admin, fx.org_a, position_id=mismatch_id,
                account_id=fx.account_mismatch,
                owner_entity_id=fx.entity_stranger,
            )
        rows = await _exceptions_for(admin, fx.org_a, mismatch_id)
        if len(rows) != 2:
            problems.append(
                f"a mismatch re-raised after review produced {len(rows)} total "
                f"rows, expected 2 (one closed, one new)"
            )

    if problems:
        results.bad("R", "an exception can be reviewed, and a re-raise is new",
                    "; ".join(problems))
    else:
        results.ok(
            "R", "an exception can be reviewed, and a re-raise is new",
            "close is idempotent, the closed row leaves the open list but stays "
            "in the full list, and the same mismatch re-raised afterwards opens "
            "a second row",
        )


async def check_extra_ambiguous(results: Results, admin, fx: Fixture) -> None:
    """Candidates spanning two households apply NO override, and say why.

    The alternative — picking the first household seen — would make which
    family's policy governs a holding depend on row insertion order, and the
    winner would flip on re-resolution with no setting having changed.
    """
    problems = []
    async with OrgSession(admin, fx.org_a):
        # Same owner and asset, same date. One row carries the PLAIN household's
        # account; the other carries no account and falls back to the owner's
        # primary_household_id, which is the OVERRIDE household.
        a = await create_position(
            admin, org_id=fx.org_a, owner_entity_id=fx.entity_owner,
            asset_id=fx.asset_direct, as_of_date=AS_OF,
            authority="custodial", source_system=SRC_ADDEPAR,
            ownership_basis="value", market_value=Decimal("10"),
        )
        b = await create_position(
            admin, org_id=fx.org_a, owner_entity_id=fx.entity_owner,
            asset_id=fx.asset_direct, as_of_date=AS_OF,
            authority="custodial", source_system=SRC_ALTRUIST,
            ownership_basis="value", market_value=Decimal("11"),
            account_id=fx.account_plain,
        )
        await set_household_source_order(
            admin, fx.org_a, household_id=fx.household_override,
            source_order=[SRC_ALTRUIST, SRC_ADDEPAR],
            reason="fee32 verification — ambiguity fixture",
            approved_by=fx.approver,
        )
        outcome = await resolve_holding(
            admin, fx.org_a, owner_entity_id=fx.entity_owner,
            asset_id=fx.asset_direct, as_of_date=AS_OF, apply=False,
        )
        await clear_household_source_order(
            admin, fx.org_a, household_id=fx.household_override
        )

    if outcome.order_origin == ORIGIN_HOUSEHOLD:
        problems.append(
            "an override was applied to candidates spanning two households"
        )
    if outcome.winner_position_id != a:
        problems.append(
            f"the org order should still pick addepar; it picked "
            f"{outcome.winner_source_system}"
        )
    if not outcome.household_reason or "different households" not in \
            outcome.household_reason:
        problems.append(
            f"no reason was reported for skipping the override: "
            f"{outcome.household_reason!r}"
        )

    if problems:
        results.bad("A", "candidates spanning two households skip the override",
                    "; ".join(problems))
    else:
        results.ok(
            "A", "candidates spanning two households skip the override",
            f"two rows, two households; origin={outcome.order_origin}, winner "
            f"{outcome.winner_source_system}, reason reported "
            f"({outcome.household_reason.split('(')[0].strip()})",
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
        await check_1(results, admin, fx)
        written = await check_2(results, admin, fx)
        await check_2b(results, admin, fx)
        await check_3(results, admin, fx)
        ids = await _make_conflict(admin, fx)
        await check_4(results, admin, fx, ids)
        await check_5(results, admin, fx)
        await check_6(results, admin, fx, ids)
        await check_7(results, admin, fx, app_dsn, app_prov)
        await check_extra_review(results, admin, fx, written["mismatch"])
        await check_extra_ambiguous(results, admin, fx)
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
