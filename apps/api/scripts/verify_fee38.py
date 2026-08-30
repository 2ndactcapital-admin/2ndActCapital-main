"""Sprint fee38 verification — the Altruist One enrollment evaluator.

Pass/fail only, no prompts. Run:

    python3 scripts/verify_fee38.py

Every table this script writes to is counted before the first insert and again
after the last delete; a difference of even one row fails the run, reported
AFTER the tests so a teardown bug never masquerades as a test failure.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **[1] compares the module's vocabularies to the DEPLOYED CHECKs, both ways.**
  Set equality, so drift in either direction fails. It also asserts the
  ``decided_by`` FOREIGN KEY to ``users`` exists — fee37's F3 was that
  ``approved_by`` had no such FK, and this sprint inherits the fixed shape;
  asserting it is how the fix stays fixed. That FK is also why [5]/[6] seed a
  REAL user row rather than fee37's deterministic literals.

* **[2] proves the design doc's own heuristic is WRONG before proving ENROLL.**
  "10%+ of assets in sweep cash → enroll" does not hold at the seeded rates:
  25 bps on 10% of assets is 2.5 bps against a 12 bps subscription. [2a]
  computes exactly that fixture and shows it recommends DO_NOT_ENROLL. Only
  then does [2b] build the ENROLL fixture. A test that had gone straight to a
  passing fixture would have hidden a false premise.

* **[2b] exercises the model-discount CAP in the binding direction.** The
  discount rate (15 bps) EXCEEDS the fee actually paid (10 bps), so an
  uncapped discount would hand back more than the fee it discounts. The
  fixture asserts the capped figure AND that ``uncapped_amount`` is present
  and larger.

* **[3] proves the account-count term BINDS, not merely that it exists.** It
  asserts the two terms separately, that ``acct_term > bps_term``, and that
  ``annual_cost`` equals the account term exactly. A fixture where the AUM term
  happened to dominate would produce the same DO_NOT_ENROLL and prove nothing
  about the per-account minimum.

* **[4] asserts the band DEFINITION reaches the persisted output**, not just
  that a MARGINAL happened. It also pins the boundary from both sides: the same
  household nudged past the band resolves to ENROLL.

* **[5] and [6] re-read every write from an INDEPENDENT connection.** A write
  that only ever proved itself through the object it returned proves nothing
  about what landed. [6] additionally asserts NOTHING CHANGED after the refusal
  — a service that raised after updating would pass a naive "it raised" check.

* **[6] proves the gap the service closes, not only the service.** [6a] shows
  the deployed CHECK genuinely refuses the raw divergent UPDATE (so the
  database is the real gate), and [6b] shows the service refuses it FIRST with
  an error naming ``override_reason`` and ``decided_by`` as fields rather than
  surfacing a constraint name.

* **[7] toggles TLH on the SAME inputs for all three recommendation classes.**
  A single MARGINAL fixture would leave open that tax alpha moves ENROLL or
  DO_NOT_ENROLL. The synthetic basis is sized so that including tax alpha would
  demonstrably flip each one, and the assertion is that it does not.

* **[8] traces every dollar figure back to a real row by ID** — it re-queries
  each ``rate_source.row_id`` and compares the stored numeric to the value the
  calculation used. It ALSO scans ``evaluate``'s own source text for Decimal
  literals, because a figure can trace correctly and still have been computed
  from a constant that happens to match.

* **[9] runs on app_service, whose ``rolbypassrls`` is asserted False FIRST.**
  Without that assertion every isolation check below it is vacuous. Both
  directions are proven on the same rows — the owning org sees them, the other
  org does not, and an EMPTY org GUC does not (the policy's NULLIF).
"""

from __future__ import annotations

import glob
import inspect
import json
import pathlib
import sys
import traceback
from datetime import date, datetime, timezone
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from services import altruist_one as A  # noqa: E402
from services.altruist_one import (  # noqa: E402
    BENEFIT_BASES,
    BENEFIT_SCOPES,
    DECISIONS,
    RECOMMENDATIONS,
    UNVERIFIED_CAVEAT,
    AlreadyDecidedError,
    AltruistOneError,
    HouseholdInputs,
    MissingRateError,
    OverrideReasonRequiredError,
    evaluate,
    evaluate_household,
    load_rate_book,
    record_decision,
    save_evaluation,
    seed_altruist_benefits,
)
from services.cost_model import seed_altruist_profile  # noqa: E402

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

TAG = "fee38verify"
TODAY = date(2026, 8, 29)

COUNTED = [
    "public.provider_benefit_schedules",
    "public.altruist_one_evaluations",
    "public.cost_providers",
    "public.cost_schedules",
    "public.account_balances_daily",
    "public.accounts",
    "public.households",
    "public.entities",
    "public.users",
]

# ── fixture ids ────────────────────────────────────────────────────────────
ENT = "99000000-0000-0000-0000-0000fee38001"
#: decided_by has a REAL foreign key to users (measured, [1f]) — unlike
#: fee37's approved_by. So this is a seeded row, not a literal.
USER = "99000000-0000-0000-0000-0000fee38009"

HH_ENROLL = "99000000-0000-0000-0000-0000fee38011"
HH_SMALL = "99000000-0000-0000-0000-0000fee38012"
HH_MARGIN = "99000000-0000-0000-0000-0000fee38013"
HOUSEHOLDS = (HH_ENROLL, HH_SMALL, HH_MARGIN)

#: [2b] — $2.0M over 3 accounts. 20% cash, real margin, real model AUM, a real
#: counted trade figure. Every benefit line fires, including the capped one.
ENROLL_ACCOUNTS = [
    (f"99000000-0000-0000-0000-0000fee380{n:02d}", HH_ENROLL, mv, cash, margin)
    for n, mv, cash, margin in (
        (21, D("1000000.00"), D("200000.00"), D("200000.00")),
        (22, D("600000.00"), D("120000.00"), D("0.00")),
        (23, D("400000.00"), D("80000.00"), D("0.00")),
    )
]
#: [3] — eight accounts averaging $9,000, i.e. UNDER the $10,000/account point
#: where the per-account term overtakes the 12 bps term. That crossover is the
#: whole subject of [3]; the fixture is chosen to sit on the far side of it.
SMALL_ACCOUNTS = [
    (f"99000000-0000-0000-0000-0000fee380{30 + i:02d}", HH_SMALL,
     D("9000.00"), D("450.00"), D("0.00"))
    for i in range(8)
]
#: [4] — $2.0M over 3 accounts with $1.0M of sweep cash: benefit $2,500 against
#: a $2,400 cost, i.e. $100 net inside a $250 band.
MARGIN_ACCOUNTS = [
    (f"99000000-0000-0000-0000-0000fee380{n:02d}", HH_MARGIN, mv, cash, D("0.00"))
    for n, mv, cash in (
        (41, D("1000000.00"), D("500000.00")),
        (42, D("600000.00"), D("300000.00")),
        (43, D("400000.00"), D("200000.00")),
    )
]
ALL_ACCOUNTS = ENROLL_ACCOUNTS + SMALL_ACCOUNTS + MARGIN_ACCOUNTS


# ═══════════════════════════════════════════════════════════════════════════
# Harness
# ═══════════════════════════════════════════════════════════════════════════


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def ok(self, ref, msg):
        self.rows.append(("PASS", ref, msg))
        print(f"[PASS] {ref}  {msg}")

    def bad(self, ref, msg, detail=""):
        self.rows.append(("FAIL", ref, f"{msg} — {detail}" if detail else msg))
        print(f"[FAIL] {ref}  {msg}" + (f"\n         {detail}" if detail else ""))

    def find(self, ref, msg):
        self.rows.append(("FIND", ref, msg))
        print(f"[FIND] {ref}  {msg}")

    def blocked(self, ref, msg):
        self.rows.append(("BLOCKED", ref, msg))
        print(f"[BLOCKED] {ref}  {msg}")

    def expect(self, ref, condition, msg, detail=""):
        if condition:
            self.ok(ref, msg)
        else:
            self.bad(ref, msg, detail)
        return bool(condition)

    @property
    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]

    def summary(self):
        counts: dict[str, int] = {}
        for kind, _, _ in self.rows:
            counts[kind] = counts.get(kind, 0) + 1
        total = len(self.rows)
        print("\n" + "=" * 78)
        print(
            f"fee38: {counts.get('PASS', 0)}/{total} PASS"
            + "".join(f"  {k}={v}" for k, v in sorted(counts.items()) if k != "PASS")
        )
        print("=" * 78)


R = Results()


def _m(value: Decimal) -> str:
    return str(value.quantize(D("0.01")))


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


#: Populated by setup. Teardown removes ONLY what this run inserted, so a
#: pre-existing production Altruist profile / benefit card survives untouched.
SEEDED: dict[str, object] = {
    "provider_id": None,
    "provider_was_new": False,
    "new_schedule_ids": [],
    "new_benefit_ids": [],
}


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def teardown(conn) -> None:
    """By fixture id, in FK order. Never a TRUNCATE."""
    await conn.execute(
        "DELETE FROM public.altruist_one_evaluations WHERE household_id = ANY($1::uuid[])",
        list(HOUSEHOLDS),
    )
    acct_ids = [a[0] for a in ALL_ACCOUNTS]
    await conn.execute(
        "DELETE FROM public.account_balances_daily WHERE account_id = ANY($1::uuid[])",
        acct_ids,
    )
    await conn.execute("DELETE FROM public.accounts WHERE id = ANY($1::uuid[])", acct_ids)
    await conn.execute(
        "DELETE FROM public.households WHERE id = ANY($1::uuid[])", list(HOUSEHOLDS)
    )
    await conn.execute("DELETE FROM public.entities WHERE id=$1::uuid", ENT)
    await conn.execute("DELETE FROM public.users WHERE id=$1::uuid", USER)

    new_benefits = list(SEEDED.get("new_benefit_ids") or [])
    if new_benefits:
        await conn.execute(
            "DELETE FROM public.provider_benefit_schedules WHERE id = ANY($1::uuid[])",
            new_benefits,
        )
    new_scheds = list(SEEDED.get("new_schedule_ids") or [])
    if new_scheds:
        await conn.execute(
            "DELETE FROM public.cost_schedules WHERE id = ANY($1::uuid[])", new_scheds
        )
    if SEEDED.get("provider_was_new") and SEEDED.get("provider_id"):
        # Only if THIS run created it. A provider that predates the run is
        # production data.
        for tbl in ("cost_schedules", "provider_benefit_schedules"):
            if await conn.fetchval(
                f"SELECT count(*) FROM public.{tbl} WHERE cost_provider_id=$1::uuid",
                SEEDED["provider_id"],
            ):
                return
        await conn.execute(
            "DELETE FROM public.cost_providers WHERE id=$1::uuid", SEEDED["provider_id"]
        )


async def build_fixtures(conn) -> None:
    await conn.execute(
        "INSERT INTO public.users (id, org_id, email) VALUES ($1::uuid,$2::uuid,$3)",
        USER, ORG, f"{TAG}@test.local",
    )
    await conn.execute(
        """INSERT INTO public.entities (id, org_id, entity_type, display_name)
           VALUES ($1::uuid, $2::uuid, 'individual', $3)""",
        ENT, ORG, f"{TAG} entity",
    )
    for i, hh in enumerate(HOUSEHOLDS):
        await conn.execute(
            "INSERT INTO public.households (id, org_id, name) "
            "VALUES ($1::uuid,$2::uuid,$3)",
            hh, ORG, f"{TAG} household {i}",
        )
    for acc_id, hh, mv, cash, margin in ALL_ACCOUNTS:
        await conn.execute(
            """INSERT INTO public.accounts
                 (id, org_id, account_number_masked, account_number_hash,
                  custodian_code, registration_type, tax_status,
                  primary_entity_id, household_id, is_billable, opened_on)
               VALUES ($1::uuid,$2::uuid,$3,$4,'TEST','individual','taxable',
                       $5::uuid,$6::uuid,true,'2024-01-01')""",
            acc_id, ORG, f"***{acc_id[-4:]}", f"{TAG}-{acc_id[-4:]}", ENT, hh,
        )
        # is_billing_source TRUE, and a SECOND non-billing feed on the same day
        # for the first account of each household — so the DISTINCT ON dedupe
        # is exercised against a real duplicate rather than a hypothetical one.
        await conn.execute(
            """INSERT INTO public.account_balances_daily
                 (org_id, account_id, as_of_date, total_market_value, cash_value,
                  margin_balance, source_system, is_billing_source, is_final)
               VALUES ($1::uuid,$2::uuid,$3::date,$4::numeric,$5::numeric,
                       $6::numeric,'CUSTODIAN',true,true)""",
            ORG, acc_id, TODAY, mv, cash, margin,
        )
        await conn.execute(
            """INSERT INTO public.account_balances_daily
                 (org_id, account_id, as_of_date, total_market_value, cash_value,
                  margin_balance, source_system, is_billing_source, is_final)
               VALUES ($1::uuid,$2::uuid,$3::date,$4::numeric,$5::numeric,
                       $6::numeric,'AGGREGATOR',false,false)""",
            ORG, acc_id, TODAY, mv * D("1.5"), cash * D("1.5"), margin,
        )


async def setup_rates(conn) -> None:
    """Seed the fee37 cost card (idempotent) then this sprint's benefit card."""
    before_sched = {
        r["id"]
        for r in await conn.fetch(
            "SELECT id::text AS id FROM public.cost_schedules WHERE org_id=$1::uuid",
            ORG,
        )
    }
    profile = await seed_altruist_profile(conn, ORG, source_verified_on=TODAY)
    SEEDED["provider_id"] = profile.provider_id
    SEEDED["provider_was_new"] = profile.created
    SEEDED["new_schedule_ids"] = [
        sid for sid in profile.schedule_ids.values() if sid not in before_sched
    ]

    benefits = await seed_altruist_benefits(conn, ORG, source_verified_on=TODAY)
    SEEDED["new_benefit_ids"] = [
        benefits.benefit_ids[c] for c in benefits.created_codes
    ]


# ═══════════════════════════════════════════════════════════════════════════
# [1] Deployment, RLS, constraint / policy shape
# ═══════════════════════════════════════════════════════════════════════════


def _check_vocab(defn: str) -> set[str]:
    """Pull the ARRAY['A','B'] members out of a deployed CHECK definition."""
    out: set[str] = set()
    for chunk in defn.split("'")[1::2]:
        if chunk.strip() and "::" not in chunk:
            out.add(chunk)
    return out


async def check_1(conn) -> None:
    tables = ("provider_benefit_schedules", "altruist_one_evaluations")
    rows = {
        r["relname"]: r
        for r in await conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid) AS npol
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname = ANY($1::text[])
            """,
            list(tables),
        )
    }
    R.expect(
        "1a",
        set(rows) == set(tables),
        "both Part-1 tables are deployed in public",
        f"found={sorted(rows)}",
    )
    if set(rows) != set(tables):
        return
    R.expect(
        "1b",
        all(rows[t]["relrowsecurity"] for t in tables),
        "RLS is ENABLED on both tables",
        {t: rows[t]["relrowsecurity"] for t in tables},
    )
    R.expect(
        "1c",
        all(rows[t]["npol"] == 1 for t in tables),
        "each table carries exactly one org-isolation policy",
        {t: rows[t]["npol"] for t in tables},
    )

    cons = {}
    for t in tables:
        cons[t] = {
            r["conname"]: r["def"]
            for r in await conn.fetch(
                """
                SELECT con.conname, pg_get_constraintdef(con.oid) AS def
                FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname=$1
                """,
                t,
            )
        }

    basis_def = cons["provider_benefit_schedules"].get(
        "provider_benefit_schedules_basis_check", ""
    )
    R.expect(
        "1d",
        _check_vocab(basis_def) == set(BENEFIT_BASES),
        "the module's BENEFIT_BASES set-EQUALS the deployed basis CHECK "
        "(drift in either direction fails)",
        f"deployed={sorted(_check_vocab(basis_def))} module={sorted(BENEFIT_BASES)}",
    )
    scope_def = cons["provider_benefit_schedules"].get(
        "provider_benefit_schedules_scope_check", ""
    )
    R.expect(
        "1e",
        _check_vocab(scope_def) == set(BENEFIT_SCOPES),
        "BENEFIT_SCOPES set-EQUALS the deployed applies_scope CHECK",
        f"deployed={sorted(_check_vocab(scope_def))}",
    )

    ev = cons["altruist_one_evaluations"]
    rec_def = ev.get("altruist_one_evaluations_recommendation_check", "")
    R.expect(
        "1f",
        _check_vocab(rec_def) == set(RECOMMENDATIONS),
        "RECOMMENDATIONS set-EQUALS the deployed recommendation CHECK",
        f"deployed={sorted(_check_vocab(rec_def))}",
    )
    dec_def = ev.get("altruist_one_evaluations_decision_check", "")
    R.expect(
        "1g",
        _check_vocab(dec_def) == set(DECISIONS),
        "DECISIONS set-EQUALS the deployed decision CHECK — and it admits only "
        "ENROLL/DO_NOT_ENROLL, so MARGINAL can never be MATCHED by a decision",
        f"deployed={sorted(_check_vocab(dec_def))}",
    )
    R.expect(
        "1h",
        "altruist_one_evaluations_override_requires_reason" in ev,
        "the override-requires-reason CHECK is deployed (this is the gate [6] "
        "proves the service explains rather than replaces)",
        sorted(ev),
    )
    fk = ev.get("altruist_one_evaluations_decided_by_fkey", "")
    R.expect(
        "1i",
        "REFERENCES users(id)" in fk,
        "decided_by carries a REAL foreign key to users — fee37's F3 was that "
        "approved_by did NOT, and this sprint inherits the fixed shape",
        fk or "<absent>",
    )
    R.expect(
        "1j",
        "altruist_one_evaluations_decided_pair_check" in ev,
        "decided_by and decided_at are constrained as a pair (both or neither)",
        sorted(ev),
    )
    if A.MARGINAL_ALWAYS_DIVERGES and "MARGINAL" not in _check_vocab(dec_def):
        R.find(
            "1k",
            "MARGINAL is a recommendation the model can make but NOT a decision "
            "a human can record. Consequence: the deployed "
            "override_requires_reason CHECK treats EVERY decision on a MARGINAL "
            "evaluation as a divergence needing a written reason. Correct "
            "behaviour for a near-breakeven call, but it is not visible from "
            "the column list — record_decision names it in its error.",
        )


# ═══════════════════════════════════════════════════════════════════════════
# [2] The ENROLL fixture — and the false premise it corrects first
# ═══════════════════════════════════════════════════════════════════════════


async def check_2(conn) -> None:
    rates = await load_rate_book(conn, ORG, as_of=TODAY)

    # ── [2a] the design doc's own heuristic, computed ───────────────────────
    # "10%+ of assets in sweep cash → enroll". Purely arithmetic, no fixture:
    # the sweep uplift is a rate on CASH, the subscription a rate on VALUE, so
    # the crossover is a ratio and can be stated exactly.
    sweep = rates.sweep_uplift_annual.value
    sub_annual = rates.sub_bps_monthly.value * A.MONTHS_PER_YEAR
    crossover = sub_annual / sweep
    heuristic = evaluate(
        HouseholdInputs(
            household_id=HH_ENROLL,
            as_of=TODAY,
            household_value=D("2000000.00"),
            account_count=3,
            sweep_cash=D("200000.00"),  # exactly 10%
            hy_cash=A.ZERO,
            margin_balance=A.ZERO,
        ),
        rates,
    )
    R.expect(
        "2a",
        heuristic.recommendation == "DO_NOT_ENROLL",
        "a household with EXACTLY 10% of assets in sweep cash and nothing else "
        f"does NOT clear the subscription: {sweep} on 10% of assets is "
        f"{sweep / 10} against a {sub_annual} subscription",
        f"net={heuristic.net_benefit} rec={heuristic.recommendation}",
    )
    R.find(
        "2a-F",
        "the design doc's '10%+ in sweep cash → ENROLL' heuristic does NOT "
        f"hold at the seeded rates. Sweep cash alone must be {crossover:.2%} of "
        "household value to break even on the subscription — nearly five times "
        "the stated threshold. The verification requirement is still met (see "
        "[2b]), but by a household where cash is one of several real benefits, "
        "not by cash alone.",
    )

    # ── [2b] the real ENROLL fixture ────────────────────────────────────────
    ev = await evaluate_household(
        conn, ORG, HH_ENROLL,
        evaluated_on=TODAY,
        sweep_share_of_cash=D("0.75"),
        model_marketplace_aum=D("500000.00"),
        trade_count=300,
    )
    lines = {line.component: line for line in ev.benefit_lines}

    R.expect(
        "2b",
        ev.inputs["household_value"] == "2000000.00" and ev.inputs["account_count"] == 3,
        "the household's value and account count came from account_balances_daily "
        "/ accounts, deduped to ONE billing-source row per account despite a "
        "second AGGREGATOR feed on the same day (a plain SUM would read "
        "$5,000,000.00)",
        ev.inputs,
    )
    R.expect(
        "2c",
        ev.annual_cost == D("2400.00"),
        "annual_cost = max(0.0012 x $2,000,000 = $2,400.00, $12 x 3 = $36.00) "
        "= $2,400.00 — the FLOOR reading, hand-derived",
        f"{ev.annual_cost} via {ev.cost_formula}",
    )
    R.expect(
        "2d",
        lines["sweep_cash_uplift"].amount == D("750.00")
        and lines["hy_cash_uplift"].amount == D("100.00"),
        "sweep ($300,000 x 0.0025 = $750.00) and high-yield ($100,000 x 0.0010 "
        "= $100.00) are NAMED separately in benefit_breakdown, not blended",
        {k: str(v.amount) for k, v in lines.items()},
    )
    disc = lines["model_marketplace_discount"]
    R.expect(
        "2e",
        disc.amount == D("500.00") and disc.uncapped_amount == D("750.00"),
        "the model-marketplace discount is CAPPED at the fee actually paid: "
        "15 bps would give $750.00 but only 10 bps ($500.00) is being paid, so "
        "$500.00 is credited and the uncapped figure is reported alongside",
        f"amount={disc.amount} uncapped={disc.uncapped_amount}",
    )
    R.expect(
        "2f",
        lines["margin_savings"].amount == D("2000.00"),
        "margin savings = $200,000 drawn x (0.0625 - 0.0525) = $2,000.00, and "
        "only the ONE account that actually draws margin contributes",
        str(lines["margin_savings"].amount),
    )
    R.expect(
        "2g",
        lines["ticket_savings"].amount == D("300.00"),
        "ticket savings appear ONLY because a real counted figure (300) was "
        "supplied; the rate is seeded, the count is never invented",
        str(lines["ticket_savings"].amount),
    )
    R.expect(
        "2h",
        ev.annual_benefit == D("3650.00")
        and ev.net_benefit == D("1250.00")
        and ev.recommendation == "ENROLL",
        "net benefit $3,650.00 - $2,400.00 = +$1,250.00, clear of the "
        f"${ev.marginal_band} band → ENROLL",
        f"benefit={ev.annual_benefit} net={ev.net_benefit} "
        f"band={ev.marginal_band} rec={ev.recommendation}",
    )
    R.expect(
        "2i",
        sum((line.amount for line in ev.benefit_lines), A.ZERO) == ev.annual_benefit,
        "the benefit_breakdown lines sum to EXACTLY annual_benefit — a reader "
        "adding up what is shown lands on the number the threshold used",
    )
    # A household with NO trade count must omit the line entirely rather than
    # zero it: a zero reads as "counted, and it was nothing".
    no_ticket = await evaluate_household(
        conn, ORG, HH_ENROLL, evaluated_on=TODAY, sweep_share_of_cash=D("0.75")
    )
    R.expect(
        "2j",
        not any(line.component == "ticket_savings" for line in no_ticket.benefit_lines)
        and any("trade count" in g for g in no_ticket.data_gaps),
        "with no trade count supplied the ticket line is OMITTED (not zeroed) "
        "and the missing source is reported in data_gaps",
        [line.component for line in no_ticket.benefit_lines],
    )
    R.expect(
        "2k",
        ev.benefit_breakdown()["caveat"] == UNVERIFIED_CAVEAT
        and "UNVERIFIED" in ev.benefit_breakdown()["caveat"],
        "the fee37-F6 unverified-rate caveat is carried in the PERSISTED "
        "benefit_breakdown, not left in a code comment",
    )


# ═══════════════════════════════════════════════════════════════════════════
# [3] The account-count term must BIND, not merely exist
# ═══════════════════════════════════════════════════════════════════════════


async def check_3(conn) -> None:
    rates = await load_rate_book(conn, ORG, as_of=TODAY)
    ev = await evaluate_household(conn, ORG, HH_SMALL, evaluated_on=TODAY)

    value = D(ev.inputs["household_value"])
    bps_term = value * rates.sub_bps_monthly.value * A.MONTHS_PER_YEAR
    acct_term = D(ev.inputs["account_count"]) * rates.sub_per_account_monthly.value * A.MONTHS_PER_YEAR

    R.expect(
        "3a",
        ev.inputs["account_count"] == 8 and value == D("72000.00"),
        "eight accounts averaging $9,000 — under the $10,000/account crossover "
        "where the per-account term overtakes the 12 bps term",
        ev.inputs,
    )
    R.expect(
        "3b",
        acct_term > bps_term,
        f"the account-count term (${acct_term}) EXCEEDS the AUM term "
        f"(${bps_term}) on this fixture — without this the check below would "
        "prove nothing about the per-account minimum",
    )
    R.expect(
        "3c",
        ev.annual_cost == D("96.00") and ev.annual_cost == acct_term,
        "annual_cost = max($86.40, $96.00) = $96.00, i.e. it EQUALS the "
        "account-count term: the per-account minimum is what is binding",
        f"{ev.annual_cost} via {ev.cost_formula}",
    )
    R.expect(
        "3d",
        ev.annual_benefit == D("9.00"),
        "$3,600 of sweep cash x 0.0025 = $9.00 of benefit",
        str(ev.annual_benefit),
    )
    R.expect(
        "3e",
        ev.net_benefit == D("-87.00")
        and ev.net_benefit < -ev.marginal_band
        and ev.recommendation == "DO_NOT_ENROLL",
        f"net -$87.00 is below the -${ev.marginal_band} band → DO_NOT_ENROLL",
        f"net={ev.net_benefit} band={ev.marginal_band} rec={ev.recommendation}",
    )


# ═══════════════════════════════════════════════════════════════════════════
# [4] MARGINAL, with the band's definition visible in the output
# ═══════════════════════════════════════════════════════════════════════════


async def check_4(conn) -> tuple:
    ev = await evaluate_household(conn, ORG, HH_MARGIN, evaluated_on=TODAY)
    R.expect(
        "4a",
        ev.annual_cost == D("2400.00") and ev.annual_benefit == D("2500.00"),
        "$1,000,000 of sweep cash x 0.0025 = $2,500.00 against a $2,400.00 "
        "subscription — $100.00 apart",
        f"cost={ev.annual_cost} benefit={ev.annual_benefit}",
    )
    R.expect(
        "4b",
        ev.net_benefit == D("100.00")
        and ev.marginal_band == D("250.00")
        and ev.recommendation == "MARGINAL",
        "+$100.00 net sits inside the $250.00 band (10% of $2,500.00, the "
        "larger of cost and benefit) → MARGINAL, not a bare >0 ENROLL",
        f"net={ev.net_benefit} band={ev.marginal_band} rec={ev.recommendation}",
    )
    breakdown = ev.benefit_breakdown()
    R.expect(
        "4c",
        breakdown["marginal_band"] == "250.00"
        and "MARGINAL when |net_benefit| <= $250.00" in breakdown["marginal_band_description"]
        and "10.00% of $2500.00" in breakdown["marginal_band_description"]
        and "ENROLL above the band, DO_NOT_ENROLL below it"
        in breakdown["marginal_band_description"],
        "the band's DEFINITION — not just its value — is in the persisted "
        "output, so a reader can see why the model declined to call it",
        breakdown["marginal_band_description"],
    )
    # Both sides of the boundary, on the same household: nudge the benefit past
    # the band and the model calls it. A MARGINAL that could never become an
    # ENROLL would be a band that swallowed everything.
    rates = await load_rate_book(conn, ORG, as_of=TODAY)
    base = await A.load_household_inputs(conn, ORG, HH_MARGIN, as_of=TODAY)
    nudged = evaluate(
        HouseholdInputs(
            household_id=base.household_id, as_of=base.as_of,
            household_value=base.household_value, account_count=base.account_count,
            sweep_cash=base.sweep_cash, hy_cash=base.hy_cash,
            margin_balance=D("100000.00"),  # +$1,000 of real margin savings
        ),
        rates,
    )
    R.expect(
        "4d",
        nudged.recommendation == "ENROLL" and nudged.net_benefit == D("1100.00"),
        "the SAME household with $100,000 of drawn margin (+$1,000.00) crosses "
        "the band and resolves to ENROLL — the band is a band, not a sink",
        f"net={nudged.net_benefit} band={nudged.marginal_band} "
        f"rec={nudged.recommendation}",
    )
    return ev, base, rates


# ═══════════════════════════════════════════════════════════════════════════
# [5] A decision EQUAL to the recommendation needs no reason
# ═══════════════════════════════════════════════════════════════════════════


async def check_5(conn, indep) -> str:
    ev = await evaluate_household(
        conn, ORG, HH_ENROLL, evaluated_on=TODAY,
        sweep_share_of_cash=D("0.75"),
        model_marketplace_aum=D("500000.00"), trade_count=300,
    )
    saved = await save_evaluation(conn, ORG, ev, next_review_on=date(2027, 8, 29))
    R.expect(
        "5a",
        saved.recommendation == "ENROLL",
        "the ENROLL evaluation persisted as a row",
        saved.id,
    )

    rec = await record_decision(conn, ORG, saved.id, "ENROLL")
    R.expect(
        "5b",
        rec.diverged is False
        and rec.override_reason is None
        and rec.decided_by is None,
        "recording ENROLL against an ENROLL recommendation needs NO reason and "
        "NO decider — the service asks for neither",
    )

    row = await indep.fetchrow(
        "SELECT decision, override_reason, decided_by, decided_at, next_review_on, "
        "recommendation, annual_cost, annual_benefit, net_benefit, benefit_breakdown "
        "FROM public.altruist_one_evaluations WHERE id=$1::uuid",
        saved.id,
    )
    R.expect(
        "5c",
        row is not None
        and row["decision"] == "ENROLL"
        and row["override_reason"] is None
        and row["decided_by"] is None
        and row["decided_at"] is None,
        "re-read on an INDEPENDENT connection: decision ENROLL landed with "
        "override_reason, decided_by AND decided_at all NULL",
        dict(row) if row else None,
    )
    R.expect(
        "5d",
        row["next_review_on"] == date(2027, 8, 29),
        "next_review_on persisted; due_for_review() is the query a scheduled "
        "trigger will call once S29b lands (NOT wired here, by design)",
        str(row["next_review_on"]),
    )
    due = await A.due_for_review(conn, ORG, as_of=date(2027, 9, 1))
    R.expect(
        "5e",
        any(d["id"] == saved.id for d in due)
        and not any(
            d["id"] == saved.id
            for d in await A.due_for_review(conn, ORG, as_of=date(2027, 1, 1))
        ),
        "due_for_review() INCLUDES the row after its review date and EXCLUDES "
        "it before — both directions on the same row",
    )

    # Append-only: a second evaluation of the same household on the same day is
    # a SECOND row, and the first one's recorded decision is untouched.
    saved2 = await save_evaluation(conn, ORG, ev)
    n = await indep.fetchval(
        "SELECT count(*) FROM public.altruist_one_evaluations "
        "WHERE household_id=$1::uuid AND evaluated_on=$2::date",
        HH_ENROLL, TODAY,
    )
    R.expect(
        "5f",
        saved2.id != saved.id and n == 2,
        "re-evaluating the same household on the same day APPENDS a second row "
        "rather than overwriting the first — evaluations are never updated",
        f"rows={n}",
    )
    try:
        await record_decision(conn, ORG, saved.id, "DO_NOT_ENROLL",
                              override_reason="second thoughts", decided_by=USER)
        R.bad("5g", "an already-decided evaluation accepted a SECOND decision")
    except AlreadyDecidedError as exc:
        after = await indep.fetchval(
            "SELECT decision FROM public.altruist_one_evaluations WHERE id=$1::uuid",
            saved.id,
        )
        R.expect(
            "5g",
            after == "ENROLL",
            "overwriting a recorded decision is refused and the original "
            "survives — the audit trail cannot be edited",
            f"{type(exc).__name__}: {str(exc)[:90]}",
        )
    return saved2.id


# ═══════════════════════════════════════════════════════════════════════════
# [6] A divergent decision REQUIRES a reason and a decider
# ═══════════════════════════════════════════════════════════════════════════


async def check_6(conn, indep, marginal_ev) -> None:
    saved = await save_evaluation(conn, ORG, marginal_ev)
    R.expect(
        "6a",
        saved.recommendation == "MARGINAL",
        "a MARGINAL evaluation is on record to decide against",
        saved.id,
    )

    # ── the DATABASE is the real gate: reproduce the refusal in raw SQL ─────
    before = await indep.fetchrow(
        "SELECT decision, override_reason, decided_by FROM "
        "public.altruist_one_evaluations WHERE id=$1::uuid",
        saved.id,
    )
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(
            "UPDATE public.altruist_one_evaluations SET decision='ENROLL' "
            "WHERE id=$1::uuid",
            saved.id,
        )
        R.bad(
            "6b",
            "raw SQL set a divergent decision with no reason — the deployed "
            "override_requires_reason CHECK is not doing anything",
        )
    except Exception as exc:  # noqa: BLE001
        R.expect(
            "6b",
            "override_requires_reason" in str(exc),
            "the DEPLOYED CHECK refuses a divergent decision with no reason, "
            "even in raw SQL — the database is the real gate",
            str(exc)[:150],
        )
    finally:
        await tx.rollback()

    # ── the SERVICE refuses first, and names the FIELDS ─────────────────────
    try:
        await record_decision(conn, ORG, saved.id, "ENROLL")
        R.bad("6c", "the service accepted a divergent decision with no reason")
    except OverrideReasonRequiredError as exc:
        R.expect(
            "6c",
            set(exc.missing) == {"override_reason", "decided_by"}
            and "override_reason" in str(exc)
            and "decided_by" in str(exc)
            and "constraint" not in str(exc).lower(),
            "the service refuses FIRST with an error naming override_reason and "
            "decided_by as FIELDS — not a raw constraint violation (fee34 "
            "pattern)",
            f"missing={exc.missing} msg={str(exc)[:150]}",
        )
    except Exception as exc:  # noqa: BLE001
        R.bad("6c", "wrong error type", f"{type(exc).__name__}: {exc}")

    after = await indep.fetchrow(
        "SELECT decision, override_reason, decided_by FROM "
        "public.altruist_one_evaluations WHERE id=$1::uuid",
        saved.id,
    )
    R.expect(
        "6d",
        dict(after) == dict(before) and after["decision"] is None,
        "NOTHING changed after the refusal — a service that raised AFTER "
        "updating would pass a naive 'it raised' check",
        dict(after),
    )

    # Half a reason is still refused: reason without decider names the decider.
    try:
        await record_decision(conn, ORG, saved.id, "ENROLL",
                              override_reason="client insisted")
        R.bad("6e", "a divergent decision with a reason but NO decider was accepted")
    except OverrideReasonRequiredError as exc:
        R.expect(
            "6e",
            exc.missing == ("decided_by",),
            "a reason with no decider is still refused, naming only the field "
            "that is actually missing",
            f"missing={exc.missing}",
        )

    # ── the same divergent decision WITH both succeeds ──────────────────────
    when = datetime(2026, 8, 29, 16, 0, tzinfo=timezone.utc)
    rec = await record_decision(
        conn, ORG, saved.id, "ENROLL",
        override_reason=f"{TAG}: client wants the cash yield regardless",
        decided_by=USER, decided_at=when,
    )
    R.expect(
        "6f",
        rec.diverged is True and rec.decided_by == USER,
        "the SAME divergent decision, with a reason and a decider, succeeds",
    )
    row = await indep.fetchrow(
        "SELECT decision, override_reason, decided_by::text AS decided_by, "
        "decided_at FROM public.altruist_one_evaluations WHERE id=$1::uuid",
        saved.id,
    )
    R.expect(
        "6g",
        row["decision"] == "ENROLL"
        and row["override_reason"].startswith(TAG)
        and row["decided_by"] == USER
        and row["decided_at"] == when,
        "re-read independently: decision, reason, decider and timestamp all "
        "landed, and decided_by resolves to a REAL users row via its FK",
        dict(row),
    )

    # A reason on a NON-divergent decision is refused too — otherwise the
    # column stops being evidence that anyone departed from the model.
    plain = await save_evaluation(conn, ORG, marginal_ev)
    try:
        await record_decision(conn, ORG, plain.id, "ENROLL")
        R.bad("6h", "a MARGINAL evaluation accepted ENROLL with no reason")
    except OverrideReasonRequiredError as exc:
        R.expect(
            "6h",
            "MARGINAL" in str(exc),
            "a MARGINAL recommendation can never be MATCHED, so every decision "
            "on one is a divergence — and the error says so rather than "
            "leaving the reader to infer it from the CHECK",
            str(exc)[-140:],
        )


# ═══════════════════════════════════════════════════════════════════════════
# [7] TLH tax alpha is labelled, and CANNOT move the recommendation
# ═══════════════════════════════════════════════════════════════════════════


async def check_7(conn) -> None:
    rates = await load_rate_book(conn, ORG, as_of=TODAY)
    #: Sized so that INCLUDING it would demonstrably flip every class: at
    #: 0.0050 this is $25,000 of tax alpha against costs of $96-$2,400.
    HUGE = D("5000000.00")

    # ``expect_flip`` says whether INCLUDING tax alpha would have changed this
    # class. Tax alpha is always POSITIVE, so it is directionally incapable of
    # flipping an ENROLL — that case can only prove invariance. Demanding a
    # flip there would be demanding something arithmetically impossible, and
    # the other two carry the load-bearing half of the proof.
    cases = [
        ("ENROLL", HH_ENROLL, False, dict(sweep_share_of_cash=D("0.75"),
                                          model_marketplace_aum=D("500000.00"),
                                          trade_count=300)),
        ("DO_NOT_ENROLL", HH_SMALL, True, {}),
        ("MARGINAL", HH_MARGIN, True, {}),
    ]
    for expected, hh, expect_flip, kwargs in cases:
        without = await evaluate_household(conn, ORG, hh, evaluated_on=TODAY, **kwargs)
        with_tlh = await evaluate_household(
            conn, ORG, hh, evaluated_on=TODAY, tlh_harvestable_basis=HUGE, **kwargs
        )
        alpha = with_tlh.tax_alpha
        would_flip = A._recommend(
            with_tlh.net_benefit + alpha.amount,
            with_tlh.annual_cost,
            with_tlh.annual_benefit + alpha.amount,
        )
        because = (
            f"and including it WOULD have produced {would_flip}, so the "
            "exclusion is load-bearing here, not vacuous"
            if expect_flip
            else "(tax alpha is always positive, so it cannot flip an ENROLL "
            "in any case — this leg proves invariance only; 7-DO_NOT_ENROLL "
            "and 7-MARGINAL carry the load-bearing half)"
        )
        R.expect(
            f"7-{expected}",
            without.recommendation == expected
            and with_tlh.recommendation == expected
            and with_tlh.net_benefit == without.net_benefit
            and with_tlh.annual_benefit == without.annual_benefit
            and (would_flip != expected) == expect_flip,
            f"a synthetic ${_m(alpha.amount)} of tax alpha leaves {expected} "
            f"UNCHANGED {because}",
            f"without={without.recommendation}/{without.net_benefit} "
            f"with={with_tlh.recommendation}/{with_tlh.net_benefit} "
            f"if_included={would_flip} expect_flip={expect_flip}",
        )

    ev = await evaluate_household(
        conn, ORG, HH_MARGIN, evaluated_on=TODAY, tlh_harvestable_basis=HUGE
    )
    bd = ev.benefit_breakdown()
    R.expect(
        "7a",
        bd["tax_alpha"]["estimated"] is True
        and bd["tax_alpha"]["included_in_threshold"] is False
        and bd["tax_alpha_excluded_from_recommendation"] is True,
        "tax alpha appears in the persisted output LABELLED estimated and "
        "flagged as excluded from the threshold",
        bd["tax_alpha"],
    )
    R.expect(
        "7b",
        not any(line["component"] == "tlh_tax_alpha" for line in bd["lines"])
        and sum((D(line["amount"]) for line in bd["lines"]), A.ZERO)
        == D(bd["annual_benefit"]),
        "tax alpha sits OUTSIDE benefit_breakdown['lines'], so a consumer "
        "summing the lines still lands exactly on annual_benefit",
    )
    sig = inspect.signature(A._recommend)
    R.expect(
        "7c",
        set(sig.parameters) == {"net_benefit", "annual_cost", "annual_benefit"},
        "the threshold function cannot see tax alpha — its signature admits "
        "only the three threshold numbers, so the exclusion is structural "
        "rather than a caller remembering to subtract",
        str(sig),
    )


# ═══════════════════════════════════════════════════════════════════════════
# [8] Every dollar figure traces to a real seeded row
# ═══════════════════════════════════════════════════════════════════════════


async def check_8(conn) -> None:
    ev = await evaluate_household(
        conn, ORG, HH_ENROLL, evaluated_on=TODAY,
        sweep_share_of_cash=D("0.75"), model_marketplace_aum=D("500000.00"),
        trade_count=300, tlh_harvestable_basis=D("1000000.00"),
    )
    bd = ev.benefit_breakdown()
    cites = list(bd["cost_sources"]) + [line["rate_source"] for line in bd["lines"]]
    cites.append(bd["tax_alpha"]["rate_source"])

    R.expect(
        "8a",
        len(cites) >= 7 and all(c.get("row_id") for c in cites),
        f"all {len(cites)} rate citations in the persisted output carry a row id",
    )

    bad: list[str] = []
    tables = set()
    for c in cites:
        tables.add(c["table"])
        col = "flat_amount" if c["code"].endswith("PER_TRADE") else None
        row = await conn.fetchrow(
            f"SELECT rate, flat_amount, "
            + ("minimum_amount, " if c["table"].endswith("cost_schedules") else "")
            + "source_url, source_verified_on "
            f"FROM {c['table']} WHERE id=$1::uuid AND org_id=$2::uuid",
            c["row_id"], ORG,
        )
        if row is None:
            bad.append(f"{c['code']}: row {c['row_id']} does not exist")
            continue
        used = D(c["value"])
        candidates = [row["rate"], row["flat_amount"]]
        if "minimum_amount" in row.keys():
            candidates.append(row["minimum_amount"])
        if not any(v is not None and D(str(v)) == used for v in candidates):
            bad.append(
                f"{c['code']}: calculation used {used} but the row holds "
                f"{[str(v) for v in candidates]}"
            )
        if col and row["flat_amount"] is None:
            bad.append(f"{c['code']}: expected a flat_amount")
    R.expect(
        "8b",
        not bad,
        "EVERY cited rate re-queries to a live row in this org whose stored "
        "numeric equals the value the calculation used",
        "; ".join(bad),
    )
    R.expect(
        "8c",
        tables == {"public.cost_schedules", "public.provider_benefit_schedules"},
        "the citations span BOTH rate tables — the cost card fee37 seeded and "
        "the benefit card this sprint seeded",
        sorted(tables),
    )

    # A figure can trace correctly and still have been computed from a literal
    # that happens to match. So: scan the calculation's own source text.
    literals: dict[str, list[str]] = {}
    for fn in (A.evaluate, A._recommend, A.load_rate_book):
        src = inspect.getsource(fn)
        found = [
            chunk.split(")")[0]
            for chunk in src.split('Decimal("')[1:]
        ]
        if found:
            literals[fn.__name__] = found
    R.expect(
        "8d",
        not literals,
        "the calculation path (evaluate, _recommend, load_rate_book) contains "
        "ZERO Decimal literals — every rate arrives through RateBook, so no "
        "figure can be produced from a hardcoded number",
        json.dumps(literals),
    )
    R.expect(
        "8e",
        A.MONTHS_PER_YEAR == D("12"),
        "the one non-DB constant the calculation uses is MONTHS_PER_YEAR=12 — "
        "a unit conversion, not a rate, and named at module scope",
    )

    # The gate that makes the missing-rate case loud instead of silent.
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(
            "UPDATE public.provider_benefit_schedules SET system_to = now() "
            "WHERE org_id=$1::uuid AND benefit_code=$2",
            ORG, A.BENEFIT_SWEEP_UPLIFT,
        )
        try:
            await load_rate_book(conn, ORG, as_of=TODAY)
            R.bad("8f", "a missing benefit rate did not raise — it was defaulted")
        except MissingRateError as exc:
            R.expect(
                "8f",
                A.BENEFIT_SWEEP_UPLIFT in exc.missing,
                "archiving a benefit rate makes load_rate_book RAISE, naming "
                "the missing code — a rate silently read as zero would "
                "understate the benefit and flip a recommendation invisibly",
                f"missing={exc.missing}",
            )
    finally:
        await tx.rollback()

    # fee37's ambiguity guard: this evaluator must never read both readings.
    codes = A.required_cost_codes(A.READING_FLOOR)
    R.expect(
        "8g",
        "ALTRUIST_ONE_SUB_FLOOR" in codes
        and not any(c.startswith("ALTRUIST_ONE_SUB_ADDITIVE") for c in codes),
        "the evaluator reads exactly ONE reading of the ambiguous subscription "
        "line (FLOOR), routed through fee37's assert_no_ambiguous_overlap",
        list(codes),
    )
    add = await evaluate_household(
        conn, ORG, HH_ENROLL, evaluated_on=TODAY,
        subscription_reading=A.READING_ADDITIVE, sweep_share_of_cash=D("0.75"),
    )
    R.expect(
        "8h",
        add.annual_cost == D("2436.00") and add.subscription_reading == "ADDITIVE",
        "the ADDITIVE reading is implemented and selectable ($2,400 + $36 = "
        "$2,436.00), and every evaluation records WHICH reading produced its "
        "cost — the ambiguity is surfaced, not silently resolved",
        f"{add.annual_cost} via {add.cost_formula}",
    )
    R.find(
        "8i",
        "fee37 seeded BOTH readings and its own note argues ADDITIVE is the "
        "conservative choice (it is the more expensive). This evaluator "
        "defaults to FLOOR because the design doc states the cost as "
        "max(0.0012 x value, 12 x accounts), which IS the FLOOR reading. The "
        "conflict is unresolved upstream; subscription_reading is a parameter "
        "and the choice is recorded on every row.",
    )


# ═══════════════════════════════════════════════════════════════════════════
# [9] Cross-org isolation, on app_service
# ═══════════════════════════════════════════════════════════════════════════


async def check_9(app_dsn, admin_conn) -> None:
    if app_dsn is None:
        R.blocked("9", "no working app_service DSN — RLS is unprovable on postgres")
        return
    conn = await connect(app_dsn)
    try:
        bypass = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        if not R.expect(
            "9a",
            bypass is False,
            "the test role does NOT bypass RLS — without this every check "
            "below is vacuous",
            f"rolbypassrls={bypass}",
        ):
            return

        async def as_org(org, sql, *args):
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute(
                    "SELECT set_config('app.current_org_id', $1, true)", org
                )
                await conn.execute(
                    "SELECT set_config('app.is_super_admin', 'false', true)"
                )
                return await conn.fetch(sql, *args)
            finally:
                await tx.rollback()

        probes = [
            ("9b", "provider_benefit_schedules",
             "SELECT id FROM public.provider_benefit_schedules "
             "WHERE org_id=$1::uuid AND benefit_code LIKE 'AONE_%'", ORG),
            ("9c", "altruist_one_evaluations",
             "SELECT id FROM public.altruist_one_evaluations "
             "WHERE org_id=$1::uuid AND household_id = ANY($2::uuid[])",
             (ORG, list(HOUSEHOLDS))),
        ]
        for ref, table, sql, arg in probes:
            args = arg if isinstance(arg, tuple) else (arg,)
            mine = await as_org(ORG, sql, *args)
            theirs = await as_org(OTHER_ORG, sql, *args)
            empty = await as_org("", sql, *args)
            R.expect(
                ref,
                len(mine) >= 1 and len(theirs) == 0 and len(empty) == 0,
                f"{table}: the owning org sees the rows, the other org sees "
                "none, and an EMPTY org GUC sees none — inclusion, exclusion, "
                "and the policy's NULLIF, on the same rows",
                f"own={len(mine)} other={len(theirs)} empty={len(empty)}",
            )

        # WITH CHECK: writing INTO another org is refused, not merely hidden.
        writes = [
            ("9d", "provider_benefit_schedules",
             "INSERT INTO public.provider_benefit_schedules "
             "(org_id, cost_provider_id, benefit_code, basis, rate, "
             " effective_from) VALUES ($1::uuid,$2::uuid,$3,'RATE_DELTA',"
             "0.001,'2026-01-01')",
             (ORG, SEEDED["provider_id"], f"{TAG}-XORG")),
            ("9e", "altruist_one_evaluations",
             "INSERT INTO public.altruist_one_evaluations "
             "(org_id, household_id, evaluated_on, inputs, annual_cost, "
             " benefit_breakdown, annual_benefit, net_benefit, recommendation) "
             "VALUES ($1::uuid,$2::uuid,'2026-08-29','{}'::jsonb,1,"
             "'{}'::jsonb,1,0,'MARGINAL')",
             (ORG, HH_ENROLL)),
        ]
        for ref, table, sql, args in writes:
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute(
                    "SELECT set_config('app.current_org_id', $1, true)", OTHER_ORG
                )
                await conn.execute(
                    "SELECT set_config('app.is_super_admin', 'false', true)"
                )
                try:
                    await conn.execute(sql, *args)
                    R.bad(
                        ref,
                        f"{table}: an org INSERTed a row into ANOTHER org — the "
                        "policy's WITH CHECK is not doing anything",
                    )
                except Exception as exc:  # noqa: BLE001
                    R.expect(
                        ref,
                        "policy" in str(exc).lower(),
                        f"{table}: inserting a row whose org_id is not the "
                        "connection's org is refused by the policy's WITH "
                        "CHECK, not merely hidden from reads",
                        str(exc)[:140],
                    )
            finally:
                await tx.rollback()
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    admin_url, admin_prov = await admin_dsn()
    app_url, app_prov = await app_service_dsn()
    print(f"admin dsn:       {admin_prov}")
    print(f"app_service dsn: {app_prov}\n")
    if admin_url is None:
        print("BLOCKED: no working admin DSN")
        return 2

    conn = await connect(admin_url)
    indep = await connect(admin_url)
    before = None
    try:
        await teardown(conn)
        before = await counts(conn)
        print("pre-test row counts captured\n")

        await build_fixtures(conn)
        await setup_rates(conn)

        await check_1(conn)
        await check_2(conn)
        await check_3(conn)
        marginal_ev, _, _ = await check_4(conn)
        await check_5(conn, indep)
        await check_6(conn, indep, marginal_ev)
        await check_7(conn)
        await check_8(conn)
        await check_9(app_url, conn)

    except Exception:
        R.bad("RUN", "the script raised", traceback.format_exc())
    finally:
        try:
            await teardown(conn)
        except Exception:
            R.bad("TEARDOWN", "teardown raised", traceback.format_exc())

        R.summary()

        if before is not None:
            after = await counts(conn)
            drift = {t: (before[t], after[t]) for t in COUNTED if before[t] != after[t]}
            if drift:
                R.bad(
                    "10", "row counts differ after teardown",
                    json.dumps({k: list(v) for k, v in drift.items()}),
                )
                print(
                    "\n[FAIL] 10  ROW COUNT DRIFT: "
                    + json.dumps({k: list(v) for k, v in drift.items()})
                )
            else:
                print(
                    f"\n[PASS] 10  every one of {len(COUNTED)} touched tables is "
                    "back to its pre-test row count"
                )
                R.rows.append(("PASS", "10", "no row-count drift"))
        await indep.close()
        await conn.close()

    return 1 if R.failed else 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
