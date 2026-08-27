#!/usr/bin/env python3
"""verify_fee31.py — the fee module's account layer. Seven checks.

NAMING: the sprint prompt asked for ``verify_sprint31.py``. That filename is
already taken by the SSVI volatility-surface sprint (commit 1e23658, 634 lines,
twelve unrelated checks) — the fee module's "31" and the platform's "Sprint 31"
are different numbering schemes that collide. Overwriting it would delete a
green verify script for shipped work, so this one is named for its sprint
(``fee31``) the way verify_workflowmgr1.py and verify_chancery1.py are.

EFFECTS, NOT EXIT CODES. Every check reads back the rows that were actually
written. Nothing here concludes anything from "the call returned without
raising" — a commit that silently wrote zero rows returns just as quietly as one
that wrote four hundred.

RLS IS EXERCISED THROUGH app_service, NEVER THE SUPERUSER DSN. Check 5 is the
whole point of that: RLS policies are INERT under ``postgres`` (superuser
bypass), so a cross-org isolation test run on the admin connection passes
vacuously and always has. The admin DSN is used ONLY to create and tear down
fixtures that RLS itself would otherwise prevent.

Three outcomes:
    PASS     the effect was observed
    FAIL     the effect was observed to be wrong
    BLOCKED  the effect could not be measured here

No interactive prompts, no note-entry, no save step.
"""

from __future__ import annotations

import asyncio
import io
import logging
import pathlib
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent

for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_site) not in sys.path:
        sys.path.insert(0, str(_site))
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from services.custody import (  # noqa: E402
    AccountNumber,
    CsvCustodyAdapter,
    UnknownAdapterError,
    UnknownCustodianError,
    get_adapter_class,
    register_adapter,
    registered_adapters,
    resolve_profile,
)
from services.custody.importer import (  # noqa: E402
    build_plan,
    commit_plan,
    get_batch,
)

TABLES = (
    "accounts",
    "account_owners",
    "account_balances_daily",
    "account_flows",
    "account_import_batches",
    "account_import_exceptions",
)

EXPECTED_POLICY = "org_isolation"
CUSTODIAN = "GENERIC_CSV"

# ── Test fixtures ─────────────────────────────────────────────────────────
# Fixed, obviously-synthetic account numbers. Check 6 greps for these literals,
# so they must be distinctive enough that a substring match is meaningful — a
# number like "12345678" would appear inside unrelated uuids by chance and turn
# check 6 into a coin flip.
ACCOUNT_A = "ZQ7734410099"
ACCOUNT_B = "ZQ7734420088"
ACCOUNT_ORPHAN = "ZQ7734430077"
SECRET_LITERALS = (ACCOUNT_A, ACCOUNT_B, ACCOUNT_ORPHAN)

BALANCE_DAYS = 30
START_DAY = date(2026, 6, 1)


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def record(self, number: int, outcome: str, name: str, detail: str = "") -> None:
        self.rows.append((f"[{number}] {outcome}", name, detail))
        line = f"[{number}] {outcome:<7} {name}"
        if detail:
            line += f"\n            {detail}"
        print(line, flush=True)

    def summary(self) -> int:
        passed = sum(1 for r in self.rows if "PASS" in r[0])
        failed = sum(1 for r in self.rows if "FAIL" in r[0])
        blocked = sum(1 for r in self.rows if "BLOCKED" in r[0])
        print("\n" + "=" * 72)
        print(f"  {passed} PASS   {failed} FAIL   {blocked} BLOCKED   "
              f"({len(self.rows)} checks)")
        print("=" * 72)
        if blocked:
            print("  BLOCKED checks were NOT measured — this sprint stays HELD.")
        return 1 if failed else 0


# ═══════════════════════════════════════════════════════════════════════════
# Log capture — check 6 needs the real log stream, not a promise about it
# ═══════════════════════════════════════════════════════════════════════════


class LogCapture(logging.Handler):
    """Capture everything every logger emits during the run.

    Attached to the ROOT logger at level 0 with ``propagate`` left alone, so it
    sees asyncpg's own logging as well as ours. Check 6 searches this buffer for
    the fixture account numbers; a check that only searched the database would
    miss the more likely leak, which is an exception message in a log line.
    """

    def __init__(self) -> None:
        super().__init__(level=0)
        self.buffer = io.StringIO()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.write(self.format(record) + "\n")
        except Exception:  # noqa: BLE001 — a broken formatter must not fail the run
            self.buffer.write(f"<unformattable {record.name}>\n")

    @property
    def text(self) -> str:
        return self.buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
# Fixture CSVs
# ═══════════════════════════════════════════════════════════════════════════


def build_csv(entity_name: str, *, include_orphan: bool) -> bytes:
    """One wide export: account identity + 30 daily balances + a few flows.

    Deliberately the awkward real shape rather than three tidy files — a
    custodial export puts identity, balance and flow on the same line, which is
    what makes the adapter's "three views over one file" contract worth testing.

    ``include_orphan`` adds a row naming an entity that does not exist. It is
    the check-4 fixture: it must land in the exception list without taking the
    other rows down with it.
    """
    header = (
        "account_number,entity,household,registration_type,tax_status,"
        "as_of_date,total_market_value,cash_value,flow_date,amount,flow_type\n"
    )
    lines = [header]

    for offset in range(BALANCE_DAYS):
        day = START_DAY + timedelta(days=offset)
        value = Decimal("1000000.00") + Decimal(offset) * Decimal("1250.55")
        # Flows on three of the thirty days, including TWO IDENTICAL $500
        # contributions on the same day (offset 10). That pair is the reason
        # the flow fingerprint folds in an occurrence index: a naive unique key
        # over (account, date, amount, type) would silently discard one of them.
        flow_date = ""
        amount = ""
        flow_type = ""
        if offset in (5, 10, 20):
            flow_date = day.isoformat()
            amount = "500.00" if offset != 20 else "-2500.00"
            flow_type = "contribution" if offset != 20 else "withdrawal"
        lines.append(
            f"{ACCOUNT_A},{entity_name},,joint,taxable,{day.isoformat()},"
            f"{value},{value / 10:.2f},{flow_date},{amount},{flow_type}\n"
        )
        if offset == 10:
            lines.append(
                f"{ACCOUNT_A},{entity_name},,joint,taxable,{day.isoformat()},"
                f"{value},{value / 10:.2f},{day.isoformat()},500.00,contribution\n"
            )

    # A second, smaller account so "at least one account" is not "exactly one".
    lines.append(
        f"{ACCOUNT_B},{entity_name},,ira,tax_deferred,{START_DAY.isoformat()},"
        f"250000.0000,1000.00,,,\n"
    )

    if include_orphan:
        lines.append(
            f"{ACCOUNT_ORPHAN},No Such Entity 9f3a,,individual,taxable,"
            f"{START_DAY.isoformat()},9999.00,0.00,,,\n"
        )
    return "".join(lines).encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


class Fixture:
    """Two disposable orgs, each with one entity. Created and torn down as
    superuser because creating an organization is not something app_service is
    permitted (or should be permitted) to do."""

    def __init__(self) -> None:
        self.org_a = str(uuid.uuid4())
        self.org_b = str(uuid.uuid4())
        self.entity_a = str(uuid.uuid4())
        self.entity_b = str(uuid.uuid4())
        self.entity_name_a = f"fee31 Client A {self.org_a[:8]}"
        self.entity_name_b = f"fee31 Client B {self.org_b[:8]}"

    async def create(self, conn) -> None:
        for org_id, slug in ((self.org_a, "a"), (self.org_b, "b")):
            await conn.execute(
                "INSERT INTO public.organizations (id, name, slug) "
                "VALUES ($1::uuid, $2, $3) ON CONFLICT (id) DO NOTHING",
                org_id, f"fee31 verify {slug} {org_id[:8]}",
                f"fee31-verify-{slug}-{org_id[:8]}",
            )
        for entity_id, org_id, name in (
            (self.entity_a, self.org_a, self.entity_name_a),
            (self.entity_b, self.org_b, self.entity_name_b),
        ):
            await conn.execute(
                "INSERT INTO public.entities (id, org_id, entity_type, display_name) "
                "VALUES ($1::uuid, $2::uuid, 'individual', $3) "
                "ON CONFLICT (id) DO NOTHING",
                entity_id, org_id, name,
            )

    async def teardown(self, conn) -> None:
        """FK-safe order: children first, then accounts, then the org's own rows.

        Runs in a try/finally at the top level, so a failed check still cleans
        up. Two disposable orgs left behind per failed run would accumulate
        into exactly the orphan mess a prior sprint had to sweep by hand.
        """
        orgs = [self.org_a, self.org_b]
        for statement in (
            "DELETE FROM public.account_import_exceptions WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.account_flows WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.account_balances_daily WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.account_owners WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.accounts WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.account_import_batches WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.org_settings WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.entities WHERE org_id = ANY($1::uuid[])",
            "DELETE FROM public.organizations WHERE id = ANY($1::uuid[])",
        ):
            await conn.execute(statement, orgs)


class OrgSession:
    """One org-scoped transaction — the same shape ``services.database`` gives
    every request, reproduced here rather than approximated.

    THIS IS NOT CEREMONY. ``set_config(..., is_local => true)`` IS ``SET LOCAL``:
    the value lives for the current transaction and no longer. Under asyncpg's
    default autocommit every statement is its own transaction, so a bare
    ``set_config`` followed by a query sets the GUC and then discards it before
    the query runs — the connection is back to the empty-string default and the
    NULLIF guard denies everything. A verify script that got that wrong would
    read "RLS denied me" as a bug in the sprint rather than in itself.

    The transaction is COMMITTED on clean exit, never rolled back. A prior
    sprint lost every held run to exactly that: writes made through the real RLS
    pool went into a savepoint that was rolled back at the end, and the check
    that looked for them afterwards found nothing.
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
# Checks
# ═══════════════════════════════════════════════════════════════════════════


async def check_1_tables_and_rls(results: Results, admin) -> None:
    """Existence + RLS enabled + exactly the one expected policy per table."""
    problems: list[str] = []
    for table in TABLES:
        row = await admin.fetchrow(
            """
            SELECT c.relrowsecurity AS rls,
                   (SELECT array_agg(p.polname ORDER BY p.polname)
                      FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = $1
            """,
            table,
        )
        if row is None:
            problems.append(f"{table}: NOT DEPLOYED")
            continue
        if not row["rls"]:
            problems.append(f"{table}: RLS not enabled")
        policies = list(row["policies"] or [])
        if policies != [f"{table}_{EXPECTED_POLICY}"]:
            problems.append(
                f"{table}: expected exactly ['{table}_{EXPECTED_POLICY}'], got {policies}"
            )
            continue
        # The policy must be the NULLIF-guarded org form. A policy that reads
        # the GUC without NULLIF raises "invalid input syntax for uuid" on a
        # reused pooled connection instead of default-denying, and the table
        # would look protected right up to the moment it errored in production.
        detail = await admin.fetchrow(
            "SELECT cmd, qual, with_check FROM pg_policies "
            "WHERE schemaname='public' AND tablename=$1",
            table,
        )
        for label, clause in (("USING", detail["qual"]), ("CHECK", detail["with_check"])):
            if not clause or "NULLIF" not in clause or "app.current_org_id" not in clause:
                problems.append(f"{table}: {label} clause is not the NULLIF org form")
        if detail["cmd"] != "ALL":
            problems.append(f"{table}: policy cmd is {detail['cmd']}, expected ALL")

    if problems:
        results.record(1, "FAIL", "tables exist with RLS and the expected policy",
                       "; ".join(problems))
    else:
        results.record(
            1, "PASS", "tables exist with RLS and the expected policy",
            f"{len(TABLES)} tables, one NULLIF-guarded ALL policy each",
        )


async def check_2_import_commits(results: Results, conn, fixture: Fixture) -> dict:
    """A real import of 2 accounts / 30 days of balances / 4 flows.

    Asserts against rows read back from the database, and asserts the BATCH's
    own counters against the file — a batch whose row_count disagrees with what
    it wrote is the reconciliation bug this check exists to catch.
    """
    csv_bytes = build_csv(fixture.entity_name_a, include_orphan=False)
    async with OrgSession(conn, fixture.org_a) as c:
        plan = await build_plan(
            c, org_id=fixture.org_a, custodian_code=CUSTODIAN,
            file_bytes=csv_bytes, filename="fee31-verify.csv",
        )
        result = await commit_plan(
            c, org_id=fixture.org_a, plan=plan, imported_by=None
        )

    async with OrgSession(conn, fixture.org_a) as c:
        accounts = await c.fetchval(
            "SELECT count(*) FROM public.accounts "
            "WHERE org_id = $1::uuid AND system_to IS NULL",
            fixture.org_a,
        )
        balances = await c.fetchval(
            "SELECT count(*) FROM public.account_balances_daily WHERE org_id = $1::uuid",
            fixture.org_a,
        )
        flows = await c.fetchval(
            "SELECT count(*) FROM public.account_flows "
            "WHERE org_id = $1::uuid AND system_to IS NULL",
            fixture.org_a,
        )
        distinct_days = await c.fetchval(
            "SELECT count(DISTINCT as_of_date) FROM public.account_balances_daily "
            "WHERE org_id = $1::uuid",
            fixture.org_a,
        )
        batch = await c.fetchrow(
            "SELECT row_count, matched_count, unmatched_count, status "
            "FROM public.account_import_batches WHERE id = $1::uuid",
            result["batch_id"],
        )
        stored = await c.fetchval(
            "SELECT total_market_value FROM public.account_balances_daily b "
            "JOIN public.accounts a ON a.id = b.account_id "
            "WHERE b.org_id = $1::uuid AND b.as_of_date = $2 "
            "  AND a.account_number_masked = $3",
            fixture.org_a, START_DAY, AccountNumber(ACCOUNT_A).masked,
        )

    problems: list[str] = []
    if accounts != 2:
        problems.append(f"expected 2 accounts, found {accounts}")
    # 30 days for account A + 1 day for account B.
    if balances != BALANCE_DAYS + 1:
        problems.append(f"expected {BALANCE_DAYS + 1} balance rows, found {balances}")
    if distinct_days != BALANCE_DAYS:
        problems.append(f"expected {BALANCE_DAYS} distinct dates, found {distinct_days}")
    # Four flows: one at offset 5, TWO identical at offset 10, one at offset 20.
    if flows != 4:
        problems.append(f"expected 4 flow rows, found {flows}")
    if batch["status"] != "COMMITTED":
        problems.append(f"batch status is {batch['status']}")
    if batch["unmatched_count"] != 0:
        problems.append(f"batch reports {batch['unmatched_count']} unmatched, expected 0")
    if batch["row_count"] != batch["matched_count"] + batch["unmatched_count"]:
        problems.append(
            f"batch row_count {batch['row_count']} != matched "
            f"{batch['matched_count']} + unmatched {batch['unmatched_count']}"
        )

    # The value is the point of the whole module: it must survive as an exact
    # Decimal, not a float that has drifted in the fourth place.
    if stored != Decimal("1000000.0000"):
        problems.append(f"day-1 market value is {stored!r}, expected 1000000.0000")

    if problems:
        results.record(2, "FAIL", "CSV import commits cleanly", "; ".join(problems))
    else:
        results.record(
            2, "PASS", "CSV import commits cleanly",
            f"2 accounts, {balances} balances over {distinct_days} days, "
            f"{flows} flows; batch counts reconcile to the file",
        )
    return {"csv": csv_bytes, "batch_id": result["batch_id"], "first": result}


async def check_3_idempotent(results: Results, conn, fixture: Fixture, state: dict) -> None:
    """The identical file again: zero new balance or flow rows, no error."""
    async def counts(c):
        return (
            await c.fetchval(
                "SELECT count(*) FROM public.account_balances_daily "
                "WHERE org_id = $1::uuid",
                fixture.org_a,
            ),
            await c.fetchval(
                "SELECT count(*) FROM public.account_flows WHERE org_id = $1::uuid",
                fixture.org_a,
            ),
            # Counted INCLUDING archived rows: an "update in place" that in
            # fact archived-and-reinserted would leave the live count unchanged
            # while doubling the table, and only a total count sees that.
            await c.fetchval(
                "SELECT count(*) FROM public.accounts WHERE org_id = $1::uuid",
                fixture.org_a,
            ),
        )

    async with OrgSession(conn, fixture.org_a) as c:
        before_balances, before_flows, before_accounts = await counts(c)

    try:
        async with OrgSession(conn, fixture.org_a) as c:
            plan = await build_plan(
                c, org_id=fixture.org_a, custodian_code=CUSTODIAN,
                file_bytes=state["csv"], filename="fee31-verify.csv",
            )
            second = await commit_plan(
                c, org_id=fixture.org_a, plan=plan, imported_by=None
            )
    except Exception as exc:  # noqa: BLE001
        results.record(3, "FAIL", "re-import is idempotent",
                       f"raised {type(exc).__name__}: {exc}")
        return

    async with OrgSession(conn, fixture.org_a) as c:
        after_balances, after_flows, after_accounts = await counts(c)

    problems: list[str] = []
    if after_balances != before_balances:
        problems.append(
            f"balance rows went {before_balances} → {after_balances}"
        )
    if after_flows != before_flows:
        problems.append(f"flow rows went {before_flows} → {after_flows}")
    if after_accounts != before_accounts:
        problems.append(
            f"account rows (incl. archived) went {before_accounts} → "
            f"{after_accounts} — the second import restated an unchanged account"
        )
    if second["flows_created"]:
        problems.append(f"reported {second['flows_created']} flows created")
    if second["balances_created"]:
        problems.append(f"reported {second['balances_created']} balances created")

    if problems:
        results.record(3, "FAIL", "re-import is idempotent", "; ".join(problems))
    else:
        results.record(
            3, "PASS", "re-import is idempotent",
            f"balances {after_balances} and flows {after_flows} unchanged; "
            f"no error; no account restatement",
        )


async def check_4_unmatched(results: Results, conn, fixture: Fixture) -> None:
    """An unresolvable entity becomes an exception, not a dropped row and not a
    failed batch."""
    csv_bytes = build_csv(fixture.entity_name_a, include_orphan=True)
    try:
        async with OrgSession(conn, fixture.org_a) as c:
            plan = await build_plan(
                c, org_id=fixture.org_a, custodian_code=CUSTODIAN,
                file_bytes=csv_bytes, filename="fee31-verify-orphan.csv",
            )
            result = await commit_plan(
                c, org_id=fixture.org_a, plan=plan, imported_by=None
            )
    except Exception as exc:  # noqa: BLE001
        results.record(4, "FAIL", "unresolvable row lands in the exception set",
                       f"the whole batch failed: {type(exc).__name__}: {exc}")
        return

    async with OrgSession(conn, fixture.org_a) as c:
        batch = await get_batch(c, fixture.org_a, result["batch_id"])
        # The orphan account must NOT have been created.
        orphan_hash_present = await c.fetchval(
            "SELECT count(*) FROM public.accounts "
            "WHERE org_id = $1::uuid AND account_number_masked = $2",
            fixture.org_a, AccountNumber(ACCOUNT_ORPHAN).masked,
        )
        good_accounts = await c.fetchval(
            "SELECT count(*) FROM public.accounts "
            "WHERE org_id = $1::uuid AND system_to IS NULL",
            fixture.org_a,
        )
    exceptions = batch["exceptions"]
    kinds = {e["reason_code"] for e in exceptions}

    problems: list[str] = []
    if not exceptions:
        problems.append("no exception rows were written")
    if "unresolved_entity" not in kinds:
        problems.append(f"expected an unresolved_entity exception, got {sorted(kinds)}")
    if batch["unmatched_count"] != len(exceptions):
        problems.append(
            f"batch.unmatched_count is {batch['unmatched_count']} but "
            f"{len(exceptions)} exception rows exist"
        )
    if orphan_hash_present:
        problems.append("the unresolvable account was created anyway")
    if batch["status"] != "COMMITTED":
        problems.append(f"batch status is {batch['status']}, expected COMMITTED")
    # The rest of the file must still be there.
    if good_accounts != 2:
        problems.append(
            f"the good rows did not survive — {good_accounts} live accounts, expected 2"
        )

    if problems:
        results.record(4, "FAIL", "unresolvable row lands in the exception set",
                       "; ".join(problems))
    else:
        results.record(
            4, "PASS", "unresolvable row lands in the exception set",
            f"{len(exceptions)} exception(s) on the batch ({sorted(kinds)}); "
            f"batch still COMMITTED; the other 2 accounts survived",
        )


async def check_5_cross_org(results: Results, conn, fixture: Fixture) -> None:
    """Org B's session cannot read org A's accounts, balances or flows.

    Runs on the app_service connection. The same assertions on the postgres DSN
    would pass with RLS entirely absent, so a "cross-org isolation" check that
    used the admin connection would be measuring nothing at all.
    """
    who = await conn.fetchval("SELECT current_user")
    superuser = await conn.fetchval(
        "SELECT usesuper FROM pg_user WHERE usename = current_user"
    )
    if superuser:
        results.record(
            5, "BLOCKED", "cross-org isolation via app_service",
            f"connected as {who}, which BYPASSES RLS — this check cannot "
            f"distinguish a working policy from no policy at all",
        )
        return

    tables = ("accounts", "account_balances_daily", "account_flows")

    # Baseline: org A's own session sees its rows. Without this, "zero rows for
    # org B" is unfalsifiable — an empty table gives the same answer.
    async with OrgSession(conn, fixture.org_a) as c:
        own = {
            table: await c.fetchval(f"SELECT count(*) FROM public.{table}")
            for table in tables
        }

    async with OrgSession(conn, fixture.org_b) as c:
        leaked = {
            table: await c.fetchval(f"SELECT count(*) FROM public.{table}")
            for table in tables
        }
        # Direct, org-qualified attempt: the policy must filter even a query
        # that names the other tenant's id explicitly.
        targeted = await c.fetchval(
            "SELECT count(*) FROM public.accounts WHERE org_id = $1::uuid",
            fixture.org_a,
        )

    problems: list[str] = []
    for table, count in own.items():
        if count == 0:
            problems.append(f"org A sees 0 rows in {table} — nothing to isolate")
    for table, count in leaked.items():
        if count != 0:
            problems.append(f"org B sees {count} rows in {table}")
    if targeted != 0:
        problems.append(
            f"org B's targeted query for org A's accounts returned {targeted} rows"
        )

    if problems:
        results.record(5, "FAIL", "cross-org isolation via app_service",
                       "; ".join(problems))
    else:
        results.record(
            5, "PASS", "cross-org isolation via app_service",
            f"as {who} (non-superuser): org A sees {own}, org B sees 0 of each",
        )


async def check_6_no_plaintext(
    results: Results, conn, admin, fixture: Fixture, capture: LogCapture
) -> None:
    """No full account number anywhere in the tables, or in the run's logs.

    Searched as SUPERUSER across ALL orgs on purpose. Scoping this to the
    fixture org would miss a leak that landed in the wrong tenant, which is
    exactly the leak worth finding.
    """
    problems: list[str] = []

    for table in ("accounts", "account_import_batches", "account_import_exceptions",
                  "account_flows", "account_balances_daily", "org_settings"):
        # Cast the whole row to text and search it — that catches a full number
        # hiding in ANY column, including one added later that this script does
        # not know the name of.
        for literal in SECRET_LITERALS:
            hits = await admin.fetchval(
                f"SELECT count(*) FROM public.{table} t "
                f"WHERE t::text LIKE '%' || $1 || '%'",
                literal,
            )
            if hits:
                problems.append(f"{table}: {hits} row(s) contain {literal}")

    # The masked form MUST be present — otherwise "no plaintext found" could
    # simply mean nothing was written and the check passed vacuously.
    masked_present = await admin.fetchval(
        "SELECT count(*) FROM public.accounts WHERE account_number_masked = $1",
        AccountNumber(ACCOUNT_A).masked,
    )
    if not masked_present:
        problems.append(
            f"the masked form {AccountNumber(ACCOUNT_A).masked} is absent — "
            f"this check would have passed on an empty table"
        )

    log_text = capture.text
    for literal in SECRET_LITERALS:
        if literal in log_text:
            problems.append(f"application log contains {literal}")
    if not log_text.strip():
        problems.append(
            "no log output was captured at all, so the log half of this check "
            "was not actually measured"
        )

    # And the repr defence itself, directly: this is the mechanism the rest of
    # the codebase relies on without knowing it.
    number = AccountNumber(ACCOUNT_A)
    for rendered in (repr(number), str(number), f"{number}", f"{number!r}"):
        if ACCOUNT_A in rendered:
            problems.append(f"AccountNumber leaks through {rendered[:40]!r}")

    if problems:
        results.record(6, "FAIL", "no unmasked account number in the DB or logs",
                       "; ".join(problems))
    else:
        results.record(
            6, "PASS", "no unmasked account number in the DB or logs",
            f"6 tables searched all-orgs for 3 literals; masked form "
            f"{AccountNumber(ACCOUNT_A).masked} IS present; "
            f"{len(log_text.splitlines())} log lines clean; repr/str/format masked",
        )


async def check_7_registry(results: Results, conn, fixture: Fixture) -> None:
    """The registry resolves by custodian_code and raises a TYPED error otherwise."""
    problems: list[str] = []

    # A SECOND adapter registers without touching any of this sprint's files —
    # which is the claim the sprint actually makes, so it is executed rather
    # than asserted in a comment.
    class _ProbeAdapter(CsvCustodyAdapter):
        pass

    async with OrgSession(conn, fixture.org_a) as c:
        try:
            profile = await resolve_profile(c, fixture.org_a, CUSTODIAN)
        except Exception as exc:  # noqa: BLE001
            results.record(
                7, "FAIL", "adapter registry resolves and raises typed errors",
                f"{CUSTODIAN} did not resolve: {type(exc).__name__}: {exc}",
            )
            return

        if profile.adapter_class() is not CsvCustodyAdapter:
            problems.append(
                f"{CUSTODIAN} resolved to {profile.adapter_class().__name__}"
            )
        if get_adapter_class("csv") is not CsvCustodyAdapter:
            problems.append("get_adapter_class('csv') is not the CSV adapter")

        # An unregistered code raises the typed error, and the error names what
        # IS available — the most useful thing to tell someone who mistyped.
        try:
            await resolve_profile(c, fixture.org_a, "NO_SUCH_CUSTODIAN_9f3a")
        except UnknownCustodianError as exc:
            if "NO_SUCH_CUSTODIAN_9f3a" not in str(exc):
                problems.append("UnknownCustodianError does not name the bad code")
            if CUSTODIAN not in str(exc):
                problems.append(
                    "UnknownCustodianError does not list the codes that DO work"
                )
        except Exception as exc:  # noqa: BLE001
            problems.append(
                f"unregistered code raised {type(exc).__name__}, not "
                f"UnknownCustodianError"
            )
        else:
            problems.append("an unregistered custodian code did not raise at all")

        # A profile naming a missing adapter is a DIFFERENT error. Conflating
        # the two would send an operator to fix settings already correct.
        await c.execute(
            """
            INSERT INTO public.org_settings
                (org_id, setting_key, setting_value, category, is_public)
            VALUES ($1::uuid, 'custody.profiles', $2::jsonb, 'custody', false)
            ON CONFLICT (org_id, setting_key)
            DO UPDATE SET setting_value = EXCLUDED.setting_value
            """,
            fixture.org_a,
            '{"BROKEN_PROFILE": {"adapter": "sftp_that_does_not_exist"}}',
        )
        try:
            await resolve_profile(c, fixture.org_a, "BROKEN_PROFILE")
        except UnknownAdapterError:
            pass
        except Exception as exc:  # noqa: BLE001
            problems.append(
                f"a profile naming a missing adapter raised {type(exc).__name__}, "
                f"not UnknownAdapterError"
            )
        else:
            problems.append("a profile naming a missing adapter did not raise")

        register_adapter("fee31_probe", _ProbeAdapter)
        if "fee31_probe" not in registered_adapters():
            problems.append("a newly registered adapter did not appear in the registry")
        await c.execute(
            "UPDATE public.org_settings SET setting_value = $2::jsonb "
            "WHERE org_id = $1::uuid AND setting_key = 'custody.profiles'",
            fixture.org_a,
            '{"PROBE_CSV": {"adapter": "fee31_probe", "label": "Probe"}}',
        )
        try:
            probe_profile = await resolve_profile(c, fixture.org_a, "PROBE_CSV")
            if probe_profile.adapter_class() is not _ProbeAdapter:
                problems.append("the second adapter did not resolve through settings")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"the second adapter failed to resolve: {exc}")

        # Leave settings as we found them so check ordering cannot matter.
        await c.execute(
            "DELETE FROM public.org_settings "
            "WHERE org_id = $1::uuid AND setting_key = 'custody.profiles'",
            fixture.org_a,
        )

    if problems:
        results.record(7, "FAIL", "adapter registry resolves and raises typed errors",
                       "; ".join(problems))
    else:
        results.record(
            7, "PASS", "adapter registry resolves and raises typed errors",
            "GENERIC_CSV → CsvCustodyAdapter; unknown code → "
            "UnknownCustodianError naming the valid codes; unknown adapter → "
            "UnknownAdapterError; a second adapter resolves purely from settings",
        )


async def check_10_salt_not_readable(results: Results, conn, fixture: Fixture) -> None:
    """The account-number salt does not leak through the settings read paths.

    NOT a code-reading check — the salt is minted for real by running an import,
    then every settings surface a member can reach is called and searched for
    the actual value.

    Why this is check-worthy at all: ``org_settings`` reads are documented as
    "open to any authenticated user of the org", so a credential parked in that
    table is readable by every member unless something removes it. is_public =
    false only covers the unauthenticated /theme/public path. The salt is the
    single thing making ``accounts.account_number_hash`` non-reversible for
    values as short and low-entropy as an account number.
    """
    from services.custody.base import SALT_SETTING_KEY
    from services.custody.registry import get_or_create_salt
    from services.org_settings import (
        get_all_settings,
        get_public_settings,
        get_setting,
        get_settings_detail,
    )

    problems: list[str] = []
    async with OrgSession(conn, fixture.org_a) as c:
        salt = await get_or_create_salt(c, fixture.org_a)
        if not salt or len(salt) < 32:
            results.record(
                10, "FAIL", "account-number salt is not readable as a setting",
                f"no usable salt was minted (got {len(salt or '')} chars)",
            )
            return

        # The by-key read MUST still work — the custody module depends on it,
        # and a filter that broke it would be caught here rather than by an
        # import failing in production.
        by_key = await get_setting(c, fixture.org_a, SALT_SETTING_KEY)
        if by_key != salt:
            problems.append(
                "get_setting no longer returns the salt — the custody module "
                "reads it by key and would break"
            )

        for name, fetch in (
            ("get_all_settings", get_all_settings),
            ("get_public_settings", get_public_settings),
        ):
            payload = await fetch(c, fixture.org_a)
            if SALT_SETTING_KEY in payload:
                problems.append(f"{name} exposes the {SALT_SETTING_KEY} key")
            if salt in str(payload):
                problems.append(f"{name} leaks the salt VALUE")

        detail = await get_settings_detail(c, fixture.org_a)
        if any(entry["key"] == SALT_SETTING_KEY for entry in detail):
            problems.append("get_settings_detail lists the salt as an editable field")
        if salt in str(detail):
            problems.append("get_settings_detail leaks the salt value")

        # And it really is stored non-public, so /theme/public cannot reach it.
        is_public = await c.fetchval(
            "SELECT is_public FROM public.org_settings "
            "WHERE org_id = $1::uuid AND setting_key = $2",
            fixture.org_a, SALT_SETTING_KEY,
        )
        if is_public is not False:
            problems.append(
                f"the salt row has is_public={is_public!r} — the column DEFAULTS "
                f"to true, so it must be set false explicitly"
            )

    if problems:
        results.record(10, "FAIL", "account-number salt is not readable as a setting",
                       "; ".join(problems))
    else:
        results.record(
            10, "PASS", "account-number salt is not readable as a setting",
            "salt minted and stored is_public=false; absent from "
            "get_all_settings / get_public_settings / get_settings_detail by "
            "key AND by value; still reachable by key for the custody module",
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
    "/api/v1/custody/profiles": "GET",
    "/api/v1/custody/import/inspect": "POST",
    "/api/v1/custody/import/dry-run": "POST",
    "/api/v1/custody/import/commit": "POST",
    "/api/v1/custody/batches": "GET",
    "/api/v1/custody/batches/{batch_id}": "GET",
}


def check_8_routes(results: Results) -> None:
    """The import endpoints are really on the app object."""
    import main  # noqa: PLC0415 — importing registers every router

    routes = dict(_walk_routes(main.app.routes))
    problems: list[str] = []
    if len(routes) < 50:
        problems.append(
            f"only {len(routes)} routes resolved — the walker missed the tree, "
            f"so an absent route would look registered"
        )
    for path, method in EXPECTED_ROUTES.items():
        if path not in routes:
            problems.append(f"{path} is not registered")
        elif method not in routes[path]:
            problems.append(f"{path} has methods {sorted(routes[path])}, not {method}")

    if problems:
        results.record(8, "FAIL", "import endpoints registered on the app",
                       "; ".join(problems))
    else:
        results.record(
            8, "PASS", "import endpoints registered on the app",
            f"{len(EXPECTED_ROUTES)} custody routes present "
            f"({len(routes)} routes resolved in total)",
        )


def check_9_ui_wiring(results: Results) -> None:
    """The four wizard steps exist end-to-end, and no hex is hardcoded.

    Checks the browser→Next→FastAPI chain is unbroken, because a page that
    posts to a Next route that does not exist fails only at runtime. The hex
    scan is the standing rule: colours come from the org's palette, never a
    literal in a component.
    """
    web = API_DIR.parent / "web"
    required = {
        "page": web / "app/admin/custody-import/page.js",
        "wizard": web / "components/admin/CustodyImportWizard.jsx",
        "forwarder": web / "lib/apiForwardMultipart.js",
        "route:profiles": web / "app/api/custody/profiles/route.js",
        "route:inspect": web / "app/api/custody/import/inspect/route.js",
        "route:dry-run": web / "app/api/custody/import/dry-run/route.js",
        "route:commit": web / "app/api/custody/import/commit/route.js",
        "route:batches": web / "app/api/custody/batches/route.js",
    }
    problems = [f"missing {name} ({p})" for name, p in required.items() if not p.exists()]
    if problems:
        results.record(9, "FAIL", "import UI is wired end-to-end", "; ".join(problems))
        return

    wizard = required["wizard"].read_text()
    # Every path the wizard posts to must be a Next route that exists.
    import re as _re

    for path in sorted(set(_re.findall(r'"(/api/custody/[a-z\-/]+)"', wizard))):
        candidate = web / "app" / path.lstrip("/") / "route.js"
        if not candidate.exists():
            problems.append(f"the wizard calls {path} but {candidate} does not exist")

    hex_literals = sorted(set(_re.findall(r"#[0-9A-Fa-f]{6}\b", wizard)))
    if hex_literals:
        problems.append(f"hardcoded colours in the wizard: {hex_literals}")

    # And the four steps are actually distinct, not a single-shot upload.
    for marker in ("import/inspect", "import/dry-run", "import/commit"):
        if marker not in wizard:
            problems.append(f"the wizard never calls {marker}")

    if problems:
        results.record(9, "FAIL", "import UI is wired end-to-end", "; ".join(problems))
    else:
        results.record(
            9, "PASS", "import UI is wired end-to-end",
            "page + wizard + 5 Next routes present; every path the wizard posts "
            "to resolves to a route file; no hex literals",
        )


# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    capture = LogCapture()
    capture.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(capture)

    results = Results()

    admin_url, admin_prov = await admin_dsn()
    if not admin_url:
        print(f"BLOCKED — no admin database connection: {admin_prov}")
        return 1
    service_url, service_prov = await app_service_dsn()

    print(f"[db] admin       : {admin_prov}")
    print(f"[db] app_service : {service_prov if service_url else 'UNAVAILABLE'}")
    print()

    admin = await connect(admin_url)
    fixture = Fixture()
    service = None
    try:
        await fixture.create(admin)

        await check_1_tables_and_rls(results, admin)
        check_8_routes(results)
        check_9_ui_wiring(results)

        if not service_url:
            for number, name in (
                (2, "CSV import commits cleanly"),
                (3, "re-import is idempotent"),
                (4, "unresolvable row lands in the exception set"),
                (5, "cross-org isolation via app_service"),
                (6, "no unmasked account number in the DB or logs"),
                (7, "adapter registry resolves and raises typed errors"),
                (10, "account-number salt is not readable as a setting"),
            ):
                results.record(
                    number, "BLOCKED", name,
                    f"app_service connection unavailable ({service_prov}); "
                    f"running these on the superuser DSN would bypass RLS and "
                    f"report a false green",
                )
            return results.summary()

        service = await connect(service_url)
        state = await check_2_import_commits(results, service, fixture)
        await check_3_idempotent(results, service, fixture, state)
        await check_4_unmatched(results, service, fixture)
        await check_5_cross_org(results, service, fixture)
        await check_6_no_plaintext(results, service, admin, fixture, capture)
        await check_7_registry(results, service, fixture)
        await check_10_salt_not_readable(results, service, fixture)
        return results.summary()
    finally:
        if service is not None:
            await service.close()
        try:
            await fixture.teardown(admin)
            print("\n[teardown] fixture orgs removed")
        finally:
            await admin.close()
            root.removeHandler(capture)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
