"""Sprint fee35 verification — the fee calculation engine, golden cases.

Pass/fail only, no prompts, no interactive input, NO DATABASE. Run:

    python3 scripts/verify_fee35.py

This script opens no connection and needs no credentials. That is the sprint's
standing rule and it is also the point of the check: an engine whose arithmetic
can only be exercised against a live database is one whose arithmetic is never
exercised, because nobody runs it on a laptop before shipping.


WHAT THIS SCRIPT IS CAREFUL ABOUT
──────────────────────────────────────────────────────────────────────────────

* **Every expected number was computed away from the code and is a literal
  below.** Not one assertion reads a value out of the engine and compares it to
  itself. Each case carries the derivation as a comment — the tier slices, the
  day counts, the division — so a reviewer can re-do the arithmetic without
  running anything, and so a wrong expectation is a visible wrong expectation
  rather than a passing test.

* **Case 2 exists to make case 1 falsifiable.** GRADUATED and CLIFF on the SAME
  balance and the SAME tier ladder must produce genuinely different numbers
  ($5,312.50 vs $4,687.50). A tiering bug that ignored the method entirely
  would pass either case alone.

* **Case 7 exists to make case 6 falsifiable**, the same way. Same schedule,
  same balance, same discount, same minimum — only ``ordering_policy``
  differs, and the answers differ by $100. A hardcoded step sequence passes
  case 6 and fails case 7, which is the only reason case 6 proves anything
  about ordering at all.

* **Case 12 is run TWICE**, once with ``minimum_fee_scope='HOUSEHOLD'`` and
  once with ``'ACCOUNT'`` over the identical two-account fixture. An
  implementation that ignored the column and always applied the minimum per
  account gets $6,000 + $6,000; the household reading gets $2,000 + $4,000.
  Asserting only the household number would let a scope-blind engine that
  happened to have a different bug slip through.

* **Every case asserts the TRACE, not only the total.** Case 1 asserts the tier
  slice amounts in ``calc_detail`` sum to the tiered subtotal and that the
  subtotal divided by four is the fee, so a refactor that reaches $5,312.50 by
  wrong internal arithmetic still fails. Case 5 asserts the ignored flow is
  present in the trace with the reason it was ignored — "the engine never saw
  the flow" and "the engine saw it and correctly left it alone" are different
  outcomes and only one of them is right.

* **[V1] runs in a SUBPROCESS.** Grepping the source for ``asyncpg`` proves the
  string is absent; importing the module in a clean interpreter and inspecting
  ``sys.modules`` proves no database driver is reachable through the whole
  transitive import graph, which is the claim that actually matters. Both are
  run, because the grep catches a lazily-imported driver the import check would
  miss and the import check catches a driver pulled in by a dependency the grep
  would miss.

* **[V2] checks floats at every input type, not one.** A boundary that refuses
  a float tier bound and accepts a float market value is not a boundary. It
  also walks the whole of ``calc_detail`` recursively asserting no ``float``
  survived into the output, since a single ``float()`` anywhere in the pipeline
  would land there.
"""

from __future__ import annotations

import glob
import json
import pathlib
import re
import subprocess
import sys
from datetime import date
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent

for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from services.fee_calc import (  # noqa: E402
    A_BLENDED,
    A_BUSINESS_DAYS,
    A_PCT_SCALE,
    ENGINE_VERSION,
    AltScheduleMissingError,
    GroupScopeMissingError,
    OrderingNotSupportedError,
    calculate_account_fee,
    calculate_group_fees,
    count_days,
    taxonomy_covers,
)
from services.fee_calc_inputs import (  # noqa: E402
    AccountCalcRequest,
    AccountInput,
    AccountPeriodInput,
    BillingPeriod,
    CreditInput,
    DailyBalanceInput,
    DiscountInput,
    ExclusionInput,
    FeeCalcInputError,
    FeeScheduleInput,
    FeeTierInput,
    FlowInput,
    PositionInput,
)

D = Decimal


# ═══════════════════════════════════════════════════════════════════════════
# Harness
# ═══════════════════════════════════════════════════════════════════════════


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

    def check(self, number, name: str, fn) -> None:
        """Run one case. An exception is a FAIL that names itself, not a crash."""
        try:
            detail = fn()
        except AssertionError as exc:
            self.bad(number, name, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.bad(number, name, f"{type(exc).__name__}: {exc}")
        else:
            self.ok(number, name, detail)

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


def eq(actual, expected, what: str) -> None:
    assert actual == expected, f"{what}: expected {expected!r}, got {actual!r}"


def step_of(result, name: str) -> dict:
    """One named step out of ``calc_detail``. Absent is a failure, not a None."""
    for s in result.calc_detail["steps"]:
        if s.get("step") == name:
            return s
    raise AssertionError(
        f"calc_detail has no {name} step; steps present: "
        f"{[s.get('step') for s in result.calc_detail['steps']]}"
    )


def step_names(result) -> list[str]:
    return [s.get("step") for s in result.calc_detail["steps"]]


# ═══════════════════════════════════════════════════════════════════════════
# The shared fixture
# ═══════════════════════════════════════════════════════════════════════════
#
# Q2 2026 is 30 + 31 + 30 = 91 days. Every day-count in this file is inclusive
# of both ends, matching BillingPeriod.calendar_days.
#
# The rate ladder, used by nearly every case (annual bps):
#     tier 1  [        0,  1,000,000)  100 bps
#     tier 2  [1,000,000,  5,000,000)   75 bps
#     tier 3  [5,000,000,        inf)   50 bps

P_START = date(2026, 4, 1)
P_END = date(2026, 6, 30)
PERIOD_DAYS = 91

ACCOUNT_A = "aaaaaaaa-0000-0000-0000-00000000000a"
ACCOUNT_B = "bbbbbbbb-0000-0000-0000-00000000000b"
ACCOUNT_C = "cccccccc-0000-0000-0000-00000000000c"
HOUSEHOLD = "hhhhhhhh-0000-0000-0000-00000000000h"
BILLING_GROUP = "b9111111-0000-0000-0000-0000000000b9"
SCHEDULE_ID = "5cede111-0000-0000-0000-000000000001"
ALT_SCHEDULE_ID = "5cede111-0000-0000-0000-000000000002"
ASSET_CONCENTRATED = "a55e7000-0000-0000-0000-0000000000c1"
ASSET_CARVE = "a55e7000-0000-0000-0000-0000000000c2"


def ladder() -> tuple[FeeTierInput, ...]:
    return (
        FeeTierInput(tier_seq=1, lower_bound=D("0"),
                     upper_bound=D("1000000"), rate_bps=D("100")),
        FeeTierInput(tier_seq=2, lower_bound=D("1000000"),
                     upper_bound=D("5000000"), rate_bps=D("75")),
        FeeTierInput(tier_seq=3, lower_bound=D("5000000"),
                     upper_bound=None, rate_bps=D("50")),
    )


def schedule(**overrides) -> FeeScheduleInput:
    base = dict(
        id=SCHEDULE_ID,
        code="STD-TIERED",
        name="Standard tiered",
        billing_frequency="QUARTERLY",
        billing_timing="ARREARS",
        valuation_method="PERIOD_END",
        proration_method="CALENDAR_DAYS",
        tier_method="GRADUATED",
        rate_type="BPS",
        product_type="ASSET_MANAGEMENT",
        day_weight_flows=True,
        currency="USD",
        status="APPROVED",
    )
    base.update(overrides)
    return FeeScheduleInput(**base)


def balance(value: str, *, as_of: date = P_END, cash: str = "0",
            margin: str = "0", account_id: str = ACCOUNT_A) -> DailyBalanceInput:
    return DailyBalanceInput(
        account_id=account_id, as_of_date=as_of, total_market_value=D(value),
        cash_value=D(cash), margin_balance=D(margin), source_system="ALTRUIST",
        is_billing_source=True, is_final=True,
    )


def account(account_id: str = ACCOUNT_A, **overrides) -> AccountInput:
    base = dict(id=account_id, household_id=HOUSEHOLD, is_billable=True)
    base.update(overrides)
    return AccountInput(**base)


def request(
    *, sched: FeeScheduleInput | None = None,
    tiers=None, balances=None, flows=(), positions=(),
    exclusions=(), discounts=(), credits=(), alt_schedules=None,
    acct: AccountInput | None = None,
    service_start: date | None = None, service_end: date | None = None,
) -> AccountCalcRequest:
    sched = sched or schedule()
    return AccountCalcRequest(
        data=AccountPeriodInput(
            account=acct or account(),
            period=BillingPeriod(
                period_start=P_START, period_end=P_END,
                service_start=service_start, service_end=service_end,
            ),
            balances=balances if balances is not None else (balance("2500000"),),
            flows=flows,
            positions=positions,
        ),
        schedule=sched,
        tiers=tiers if tiers is not None else ladder(),
        exclusions=exclusions,
        discounts=discounts,
        credits=credits,
        alt_schedules=alt_schedules,
    )


def slice_sum(tier_step: dict) -> Decimal:
    return sum((D(s["amount_annual"]) for s in tier_step["slices"]
                if s.get("amount_annual") is not None), D(0))


# ═══════════════════════════════════════════════════════════════════════════
# Golden cases
# ═══════════════════════════════════════════════════════════════════════════


def case_1() -> str:
    """GRADUATED on a clean $2,500,000. No exclusions, no flows.

        tier 1   1,000,000 @ 1.00%  =  10,000.00   annual
        tier 2   1,500,000 @ 0.75%  =  11,250.00   annual
        tier 3           0 @ 0.50%  =       0.00
                                       ---------
                            annual  =  21,250.00
                          quarterly =   5,312.50
    """
    r = calculate_account_fee(request())
    eq(r.amount, D("5312.50"), "case 1 fee")
    eq(r.billable_value, D("2500000"), "case 1 billable")

    tiers = step_of(r, "TIERS")
    eq(slice_sum(tiers), D("21250.00"),
       "case 1 tier slices must sum to the annual subtotal")
    eq(D(tiers["primary_annual"]), D("21250.00"), "case 1 primary_annual")
    eq(D(tiers["primary_annual"]) / 4, D("5312.50"),
       "case 1 annual / periods_per_year must be the fee")
    eq([D(s["amount_annual"]) for s in tiers["slices"]],
       [D("10000.00"), D("11250.0000"), D("0")], "case 1 individual slices")
    eq([D(s["base"]) for s in tiers["slices"]],
       [D("1000000"), D("1500000"), D("0")], "case 1 slice bases")
    eq(step_names(r),
       ["VALUATION", "FLOWS", "EXCLUSIONS", "TIERS", "PRORATION",
        "DISCOUNTS", "CREDITS", "MINIMUM", "MAXIMUM"],
       "case 1 trace follows the default ordering_policy")
    json.dumps(r.calc_detail)
    return ("$5,312.50 — slices 10,000.00 + 11,250.00 = 21,250.00 annual, / 4. "
            "calc_detail slices sum to the subtotal")


def case_2() -> str:
    """CLIFF on the SAME $2,500,000 and the SAME ladder as case 1.

        whole balance at tier 2's rate:
            2,500,000 @ 0.75%  =  18,750.00  annual
                       quarterly =   4,687.50

    $4,687.50 != $5,312.50. If these matched, neither case would prove the
    tiering method is read at all.
    """
    r = calculate_account_fee(request(sched=schedule(tier_method="CLIFF")))
    eq(r.amount, D("4687.50"), "case 2 fee")
    assert r.amount != D("5312.50"), "CLIFF and GRADUATED produced the same fee"

    tiers = step_of(r, "TIERS")
    eq(tiers["tier_method"], "CLIFF", "case 2 method in trace")
    reached = [s for s in tiers["slices"] if s.get("reached")]
    eq(len(reached), 1, "case 2 exactly one tier is reached under CLIFF")
    eq(reached[0]["tier_seq"], 2, "case 2 the reached tier")
    eq(D(reached[0]["base"]), D("2500000"),
       "case 2 CLIFF charges the WHOLE balance at the reached tier")
    eq(slice_sum(tiers), D("18750.0000"), "case 2 slices sum to the subtotal")
    return ("$4,687.50 vs case 1's $5,312.50 — whole 2,500,000 at tier 2's "
            "75 bps, one reached slice in the trace")


def case_3() -> str:
    """Mid-quarter inception. 47 of 91 calendar days.

    Service starts 2026-05-15: May 15-31 is 17 days, June is 30 → 47.

        full-quarter gross          =  5,312.50
        x 47/91                     =  2,743.8186813186...
        to the cent                 =  2,743.82
    """
    r = calculate_account_fee(request(service_start=date(2026, 5, 15)))
    eq(r.amount, D("2743.82"), "case 3 fee")
    eq(r.gross_fee, D("5312.50"), "case 3 gross before proration")

    pro = step_of(r, "PRORATION")
    eq(pro["period_days"], PERIOD_DAYS, "case 3 period days")
    eq(pro["in_service_days"], 47, "case 3 in-service days")
    eq(pro["outcome"], "inception_partial", "case 3 proration outcome")
    eq(D(pro["gross_before_proration"]), D("5312.50"), "case 3 trace gross")
    eq(D(pro["factor"]), D(47) / D(91), "case 3 factor")
    assert r.amount < D("5312.50"), "a partial period must bill less than a full one"
    return "$2,743.82 — 5,312.50 x 47/91, both day counts in the trace"


def case_4() -> str:
    """A $30,000 flow on day 47, ABOVE the $10,000 threshold. Weighted.

    2026-05-17 is day 47 of the quarter (April = days 1-30, so May 17 = 30+17).
    Present 2026-05-17..2026-06-30 inclusive = 45 days; absent 46.

        period-end value                         = 2,530,000.00
        less the absent share, 30,000 x 46/91    =   -15,164.8351648351...
        billable                                 = 2,514,835.1648351648...

        tier 1  1,000,000.0000000000 @ 1.00%     =    10,000.0000000000
        tier 2  1,514,835.1648351648 @ 0.75%     =    11,361.2637362637
                                                   ------------------
                                          annual =    21,361.2637362637
                                       quarterly =     5,340.3159340659
                                     to the cent =     5,340.32
    """
    r = calculate_account_fee(request(
        balances=(balance("2530000"),),
        flows=(FlowInput(id="f-30k", account_id=ACCOUNT_A,
                         flow_date=date(2026, 5, 17), amount=D("30000"),
                         flow_type="CONTRIBUTION"),),
        sched=schedule(day_weight_flows=True, day_weight_threshold=D("10000")),
    ))
    eq(r.amount, D("5340.32"), "case 4 fee")

    flows = step_of(r, "FLOWS")
    eq(len(flows["flows"]), 1, "case 4 one flow in the trace")
    f = flows["flows"][0]
    eq(f["outcome"], "weighted", "case 4 flow outcome")
    eq(f["days_present"], 45, "case 4 days present")
    eq(f["days_absent"], 46, "case 4 days absent")
    eq(D(f["adjustment"]), -(D("30000") * D(46)) / D(91), "case 4 adjustment")
    eq(D(flows["total_adjustment"]), D(f["adjustment"]), "case 4 total adjustment")

    ex = step_of(r, "EXCLUSIONS")
    eq(D(ex["account_value"]), D("2530000"), "case 4 account value")
    eq(D(ex["value_after_flow_adjustment"]), D(ex["billable_value"]),
       "case 4 billable is the flow-adjusted value")
    assert r.billable_value < D("2530000"), (
        "the weighted deposit must reduce the billable value below the "
        "period-end market value")
    return ("$5,340.32 — 30,000 present 45 of 91 days, absent share "
            "15,164.8351648351 removed from a 2,530,000 period-end value")


def case_5() -> str:
    """A $2,000 flow on day 47 with a $10,000 threshold. Ignored entirely.

        period-end value           = 2,502,000.00   (the deposit IS in it)
        no adjustment at all       =         0.00

        tier 1  1,000,000 @ 1.00%  =    10,000.00
        tier 2  1,502,000 @ 0.75%  =    11,265.00
                            annual =    21,265.00
                         quarterly =     5,316.25
    """
    r = calculate_account_fee(request(
        balances=(balance("2502000"),),
        flows=(FlowInput(id="f-2k", account_id=ACCOUNT_A,
                         flow_date=date(2026, 5, 17), amount=D("2000"),
                         flow_type="CONTRIBUTION"),),
        sched=schedule(day_weight_flows=True, day_weight_threshold=D("10000")),
    ))
    eq(r.amount, D("5316.25"), "case 5 fee")
    eq(r.billable_value, D("2502000"),
       "an ignored flow must leave the billable value exactly as valued")

    flows = step_of(r, "FLOWS")
    eq(D(flows["total_adjustment"]), D(0), "case 5 no adjustment")
    eq(len(flows["flows"]), 1,
       "the ignored flow must still appear in the trace — 'never seen' and "
       "'seen and correctly skipped' are different outcomes")
    f = flows["flows"][0]
    eq(f["outcome"], "ignored", "case 5 flow outcome")
    eq(D(f["adjustment"]), D(0), "case 5 flow adjustment")
    assert "day_weight_threshold" in f["reason"], (
        f"the trace must say WHY the flow was ignored; got {f['reason']!r}")
    assert "days_present" not in f, (
        "a below-threshold flow must not be weighted at all, not weighted by a "
        "small factor")
    return "$5,316.25 — the $2,000 flow is traced as ignored, adjustment 0.00"


def case_6() -> str:
    """minimum_fee that bites AFTER a 20% PCT_OFF discount. Default ordering.

        $400,000 all in tier 1:  400,000 @ 1.00% =  4,000.00 annual
                                       quarterly =  1,000.00
        DISCOUNTS  -20% of 1,000.00              =   -200.00 →   800.00
        MINIMUM    900.00 > 800.00               =   +100.00 →   900.00
    """
    r = calculate_account_fee(request(
        balances=(balance("400000"),),
        sched=schedule(minimum_fee=D("900"), minimum_fee_scope="ACCOUNT"),
        discounts=(DiscountInput(id="d-20", discount_type="PCT_OFF",
                                 value=D("20"), applies_to="GROSS"),),
    ))
    eq(r.amount, D("900.00"), "case 6 fee")

    disc = step_of(r, "DISCOUNTS")
    eq(D(disc["amount_before"]), D("1000.00"), "case 6 gross before discount")
    eq(D(disc["discounts"][0]["amount"]), D("-200.00"), "case 6 discount amount")
    eq(D(disc["amount_after"]), D("800.00"), "case 6 amount after discount")

    mini = step_of(r, "MINIMUM")
    eq(mini["outcome"], "applied", "case 6 minimum outcome")
    eq(D(mini["amount_before"]), D("800.00"),
       "the minimum must be compared against the POST-discount amount")
    eq(D(mini["uplift"]), D("100.00"), "case 6 minimum uplift")
    names = step_names(r)
    assert names.index("DISCOUNTS") < names.index("MINIMUM"), (
        f"case 6 trace order is wrong: {names}")
    assert A_PCT_SCALE in r.assumptions, (
        "the percent-vs-fraction scale assumption must travel with the number")
    return "$900.00 — 1,000.00 -20% = 800.00, then the 900.00 minimum lifts it"


def case_7() -> str:
    """Case 6's schedule and balance, with MINIMUM moved BEFORE DISCOUNTS.

        quarterly gross                          =  1,000.00
        MINIMUM    900.00 <= 1,000.00, no change =  1,000.00
        DISCOUNTS  -20% of 1,000.00              =   -200.00 →   800.00

    $800.00, not case 6's $900.00. A hardcoded step sequence cannot produce
    both numbers from the same inputs.
    """
    policy = ["EXCLUSIONS", "TIERS", "MINIMUM", "DISCOUNTS", "CREDITS", "MAXIMUM"]
    r = calculate_account_fee(request(
        balances=(balance("400000"),),
        sched=schedule(minimum_fee=D("900"), minimum_fee_scope="ACCOUNT",
                       ordering_policy=policy),
        discounts=(DiscountInput(id="d-20", discount_type="PCT_OFF",
                                 value=D("20"), applies_to="GROSS"),),
    ))
    eq(r.amount, D("800.00"), "case 7 fee")
    assert r.amount != D("900.00"), (
        "case 7 must differ from case 6 — identical answers would mean "
        "ordering_policy is not read")

    mini = step_of(r, "MINIMUM")
    eq(mini["outcome"], "not_reached", "case 7 minimum outcome")
    eq(D(mini["amount_before"]), D("1000.00"),
       "case 7 the minimum sees the PRE-discount amount")
    names = step_names(r)
    assert names.index("MINIMUM") < names.index("DISCOUNTS"), (
        f"case 7 trace must follow the customised policy: {names}")
    eq(r.calc_detail["ordering_policy"], policy, "case 7 policy echoed in trace")
    return ("$800.00 vs case 6's $900.00 on identical inputs — only "
            "ordering_policy differs")


def case_8() -> str:
    """An excluded concentrated position, basis_type='SECURITY'.

        account value                =  2,500,000
        less the excluded position   =   -600,000
        billable                     =  1,900,000

        tier 1  1,000,000 @ 1.00%    =    10,000.00
        tier 2    900,000 @ 0.75%    =     6,750.00
                              annual =    16,750.00
                           quarterly =     4,187.50
    """
    r = calculate_account_fee(request(
        positions=(
            PositionInput(id="p-conc", account_id=ACCOUNT_A,
                          asset_id=ASSET_CONCENTRATED,
                          market_value=D("600000"), as_of_date=P_END,
                          taxonomy_key="taxonomy_sc_1"),
            PositionInput(id="p-rest", account_id=ACCOUNT_A,
                          asset_id="a55e7000-0000-0000-0000-0000000000c9",
                          market_value=D("1900000"), as_of_date=P_END,
                          taxonomy_key="taxonomy_sc_2"),
        ),
        exclusions=(ExclusionInput(
            id="x-conc", basis_type="SECURITY", basis_value=ASSET_CONCENTRATED,
            treatment="EXCLUDE", scope_type="ACCOUNT", scope_id=ACCOUNT_A,
            reason="concentrated legacy holding, not advised on",
            effective_from=date(2026, 1, 1)),),
    ))
    eq(r.amount, D("4187.50"), "case 8 fee")
    eq(r.account_value, D("2500000"), "case 8 account value")
    eq(r.billable_value, D("1900000"), "case 8 billable value")
    assert r.billable_value < r.account_value, (
        "the whole point of case 8: billable must fall BELOW total account "
        "value")

    ex = step_of(r, "EXCLUSIONS")
    applied = [d for d in ex["deductions"] if d.get("outcome") == "excluded"]
    eq(len(applied), 1, "case 8 one exclusion applied")
    eq(applied[0]["basis_type"], "SECURITY", "case 8 basis type")
    eq(D(applied[0]["amount"]), D("-600000"), "case 8 excluded amount")
    eq([p["asset_id"] for p in applied[0]["matched_positions"]],
       [ASSET_CONCENTRATED],
       "case 8 the trace must name the position it excluded")
    eq(D(applied[0]["running_billable"]), D("1900000"), "case 8 running billable")
    return ("$4,187.50 — 2,500,000 less a 600,000 SECURITY exclusion; the "
            "trace names the asset")


def case_9() -> str:
    """REDUCED_RATE: part of the account bills on alt_fee_schedule_id.

        primary billable  2,500,000 - 500,000 = 2,000,000
            tier 1  1,000,000 @ 1.00% = 10,000.00
            tier 2  1,000,000 @ 0.75% =  7,500.00
                               annual = 17,500.00 → quarterly  4,375.00

        carve-out on ALT-25 (flat 25 bps, single open-ended tier)
              500,000 @ 0.25%         =  1,250.00 → quarterly    312.50

                                          4,375.00 + 312.50 = 4,687.50
    """
    alt = FeeScheduleInput(
        id=ALT_SCHEDULE_ID, code="ALT-25", name="Reduced rate 25bps",
        billing_frequency="QUARTERLY", billing_timing="ARREARS",
        valuation_method="PERIOD_END", proration_method="CALENDAR_DAYS",
        tier_method="GRADUATED",
    )
    alt_tiers = (FeeTierInput(tier_seq=1, lower_bound=D("0"),
                              upper_bound=None, rate_bps=D("25")),)
    r = calculate_account_fee(request(
        positions=(PositionInput(id="p-carve", account_id=ACCOUNT_A,
                                 asset_id=ASSET_CARVE, market_value=D("500000"),
                                 as_of_date=P_END),),
        exclusions=(ExclusionInput(
            id="x-carve", basis_type="SECURITY", basis_value=ASSET_CARVE,
            treatment="REDUCED_RATE", alt_fee_schedule_id=ALT_SCHEDULE_ID,
            scope_type="ACCOUNT", scope_id=ACCOUNT_A,
            reason="held-away model billed at a reduced rate",
            effective_from=date(2026, 1, 1)),),
        alt_schedules={ALT_SCHEDULE_ID: (alt, alt_tiers)},
    ))
    eq(r.amount, D("4687.50"), "case 9 fee")
    eq(r.billable_value, D("2000000"), "case 9 primary billable")

    tiers = step_of(r, "TIERS")
    eq(D(tiers["primary_amount_period"]), D("4375.00"), "case 9 primary component")
    eq(len(tiers["carve_outs"]), 1, "case 9 one carve-out")
    carve = tiers["carve_outs"][0]
    eq(carve["alt_fee_schedule_id"], ALT_SCHEDULE_ID, "case 9 alt schedule id")
    eq(carve["alt_schedule_code"], "ALT-25", "case 9 alt schedule code")
    eq(D(carve["carved_value"]), D("500000"), "case 9 carved value")
    eq(D(carve["amount_period"]), D("312.50"), "case 9 carve-out component")
    eq(D(tiers["primary_amount_period"]) + D(carve["amount_period"]), r.amount,
       "case 9 the two components must sum to the reported total")
    return ("$4,687.50 = 4,375.00 primary + 312.50 on ALT-25; both components "
            "in the trace and summing to the total")


def case_10() -> str:
    """An SPV_MGMT_FEE_OFFSET credit.

        quarterly advisory gross         =  5,312.50
        credit  50% of a 3,000.00 basis  = -1,500.00
                                            3,812.50
    """
    r = calculate_account_fee(request(
        credits=(CreditInput(
            id="c-spv", credit_source="SPV_MGMT_FEE_OFFSET",
            offset_pct=D("0.50"), basis_amount=D("3000.00"),
            scope_type="ACCOUNT", scope_id=ACCOUNT_A,
            reason="offsets the SPV management fee charged for this quarter",
            effective_from=date(2026, 1, 1)),),
    ))
    eq(r.amount, D("3812.50"), "case 10 fee")

    cr = step_of(r, "CREDITS")
    eq(D(cr["amount_before"]), D("5312.50"), "case 10 pre-credit amount")
    eq(len(cr["credits"]), 1, "case 10 one credit")
    c = cr["credits"][0]
    eq(c["credit_source"], "SPV_MGMT_FEE_OFFSET", "case 10 credit source")
    eq(D(c["offset_pct"]), D("0.50"), "case 10 offset_pct")
    eq(D(c["basis_amount"]), D("3000.00"), "case 10 basis")
    eq(D(c["amount"]), D("-1500.00"),
       "case 10 credit must be exactly offset_pct x basis")
    eq(D(cr["amount_after"]), D("3812.50"), "case 10 post-credit amount")
    return "$3,812.50 — 5,312.50 less 50% of the 3,000.00 SPV fee basis"


def case_11() -> str:
    """Termination mid-period on a schedule billed in ADVANCE. A refund.

    Service ends 2026-05-17: in service Apr 1..May 17 = 47 of 91 days, so 44
    days were paid for at the start of the quarter and not earned.

        full-quarter gross    =  5,312.50
        x -(44/91)            = -2,568.6813186813...
        to the cent           = -2,568.68
    """
    r = calculate_account_fee(request(
        sched=schedule(billing_timing="ADVANCE"),
        service_end=date(2026, 5, 17),
    ))
    eq(r.amount, D("-2568.68"), "case 11 refund")
    assert r.is_refund, "case 11 must be flagged as a refund"
    assert r.amount < 0, "an ADVANCE termination must produce a NEGATIVE line"

    pro = step_of(r, "PRORATION")
    eq(pro["outcome"], "termination_refund", "case 11 proration outcome")
    eq(pro["in_service_days"], 47, "case 11 earned days")
    eq(pro["unearned_days"], 44, "case 11 unearned days")
    eq(D(pro["factor"]), -(D(44) / D(91)), "case 11 factor is negative")
    eq(D(pro["gross_before_proration"]), D("5312.50"), "case 11 gross")

    arrears = calculate_account_fee(request(service_end=date(2026, 5, 17)))
    assert arrears.amount > 0, (
        "the same termination billed in ARREARS must be a positive charge for "
        "the earned days, not a refund")
    eq(arrears.amount, D("2743.82"),
       "ARREARS termination bills 5,312.50 x 47/91")
    return ("-$2,568.68 refund of 44 unearned days; the same termination in "
            "ARREARS bills +$2,743.82 for the 47 earned days")


def case_12() -> str:
    """A HOUSEHOLD-scoped minimum_fee across two accounts.

        account A   400,000 @ 1.00% = 4,000.00 annual → 1,000.00
        account B   800,000 @ 1.00% = 8,000.00 annual → 2,000.00
        household subtotal                              3,000.00
        household minimum                               6,000.00
        shortfall                                       3,000.00
          allocated pro-rata:  A 1/3 = 1,000.00,  B 2/3 = 2,000.00

        A final = 2,000.00      B final = 4,000.00      total = 6,000.00

    The same fixture with minimum_fee_scope='ACCOUNT' gives 6,000.00 EACH.
    Running both is what proves the column is read rather than assumed.
    """
    def two_accounts(scope: str):
        sched = schedule(minimum_fee=D("6000"), minimum_fee_scope=scope)
        return [
            request(sched=sched, acct=account(ACCOUNT_A),
                    balances=(balance("400000", account_id=ACCOUNT_A),)),
            request(sched=sched, acct=account(ACCOUNT_B),
                    balances=(balance("800000", account_id=ACCOUNT_B),)),
        ]

    grp = calculate_group_fees(two_accounts("HOUSEHOLD"))
    by_id = grp.by_account()
    eq(by_id[ACCOUNT_A].amount, D("2000.00"), "case 12 account A")
    eq(by_id[ACCOUNT_B].amount, D("4000.00"), "case 12 account B")
    eq(grp.total, D("6000.00"), "case 12 household total equals the minimum")

    detail = grp.group_detail["groups"]
    eq(len(detail), 1, "case 12 one household group")
    g = detail[0]
    eq(g["scope"], "HOUSEHOLD", "case 12 scope")
    eq(g["scope_id"], HOUSEHOLD, "case 12 scope id")
    eq(D(g["group_subtotal_before_minimum"]), D("3000.00"),
       "case 12 the group minimum must be compared against BOTH accounts' fees")
    eq(D(g["shortfall"]), D("3000.00"), "case 12 shortfall")
    eq(sorted(D(a["share"]) for a in g["allocations"]),
       [D("1000.00"), D("2000.00")], "case 12 pro-rata shares")
    eq(sum((D(a["share"]) for a in g["allocations"]), D(0)), D(g["shortfall"]),
       "case 12 allocations must sum EXACTLY to the shortfall")
    for res in grp.results:
        eq(step_of(res, "MINIMUM")["outcome"], "group_share_applied",
           "case 12 each account's trace records its group share")

    per_account = calculate_group_fees(two_accounts("ACCOUNT")).by_account()
    eq(per_account[ACCOUNT_A].amount, D("6000.00"),
       "case 12 control: ACCOUNT scope charges the full minimum to A")
    eq(per_account[ACCOUNT_B].amount, D("6000.00"),
       "case 12 control: ACCOUNT scope charges the full minimum to B")
    assert per_account[ACCOUNT_A].amount != by_id[ACCOUNT_A].amount, (
        "HOUSEHOLD and ACCOUNT scope produced the same number — "
        "minimum_fee_scope is not being read")
    return ("A $2,000.00 + B $4,000.00 = $6,000.00 household minimum; the same "
            "fixture at ACCOUNT scope gives $6,000.00 each")


def case_13() -> str:
    """A refunding account inside a BILLING_GROUP-scoped minimum. fee35-F36D.

    THIS CASE IS A REGRESSION TEST. It was added by patch fee35-F36D, and the
    numbers below are what the engine produced BEFORE and AFTER that patch —
    both are recorded, because a regression test that only knows the right
    answer cannot tell you it is guarding anything.

        account A   800,000 @ 1.00% = 8,000.00 annual →  2,000.00
        account B   600,000 @ 1.00% = 6,000.00 annual →  1,500.00
        account C   480,000 @ 1.00% = 4,800.00 annual →  1,200.00
                    less a credit of 1.0 x a 2,000.00 basis
                                                      →   -800.00   a REFUND
        billing-group minimum                            5,000.00

    CORRECT (this engine):
        subtotal    2,000.00 + 1,500.00 + (-800.00)  =   2,700.00
        shortfall   5,000.00 - 2,700.00              =   2,300.00
        allocated pro-rata over the CHARGING accounts only, 2,000 : 1,500
            A  2,300 x 2000/3500 = 1,314.285714…  →  1,314.29  (larger cent
            B  2,300 x 1500/3500 =   985.714285…  →    985.71   remainder to A)
        A 3,314.29  +  B 2,485.71  +  C -800.00   =   5,000.00  = the minimum

    WRONG (before fee35-F36D):
        ``_minimum_step`` short-circuited on ``run.amount < ZERO`` BEFORE it
        reached the group branch, so C never set a group flag,
        ``calculate_group_fees`` never put C in the bucket, and C's -800.00
        never reached the sum:
        subtotal 3,500.00 (OVERSTATED by the whole refund), shortfall 1,500.00
        (UNDERSTATED by 800.00), A 2,857.14, B 2,142.86, and a group total of
        4,200.00 — 800.00 BELOW the floor the group was told it was paying.

    Note the direction. fee36's finding 6g, which reported this bug, described
    the shortfall charged to the rest as "too large". Measured, it is too
    SMALL: dropping a NEGATIVE contribution raises the subtotal, and a higher
    subtotal is a smaller shortfall. The client is undercharged, not
    overcharged.

    Reverting the patch turns the four ``assert … != …`` guards below into
    failures, so this case cannot pass against the old arithmetic.
    """
    sched = schedule(minimum_fee=D("5000"), minimum_fee_scope="BILLING_GROUP")

    def acct(account_id: str) -> AccountInput:
        return account(account_id, billing_group_id=BILLING_GROUP)

    reqs = [
        request(sched=sched, acct=acct(ACCOUNT_A),
                balances=(balance("800000", account_id=ACCOUNT_A),)),
        request(sched=sched, acct=acct(ACCOUNT_B),
                balances=(balance("600000", account_id=ACCOUNT_B),)),
        request(sched=sched, acct=acct(ACCOUNT_C),
                balances=(balance("480000", account_id=ACCOUNT_C),),
                credits=(CreditInput(
                    id="c-f36d", credit_source="SPV_MGMT_FEE_OFFSET",
                    offset_pct=D("1.0"), basis_amount=D("2000.00"),
                    scope_type="ACCOUNT", scope_id=ACCOUNT_C,
                    effective_from=date(2026, 1, 1)),)),
    ]

    grp = calculate_group_fees(reqs)
    by_id = grp.by_account()

    # ── The refunding account's own line is untouched by the group minimum.
    eq(by_id[ACCOUNT_C].amount, D("-800.00"),
       "case 13 the refunding account must bill exactly its refund")
    assert by_id[ACCOUNT_C].is_refund, "case 13 C must still be flagged a refund"
    c_min = step_of(by_id[ACCOUNT_C], "MINIMUM")
    eq(c_min["outcome"], "skipped",
       "case 13 a minimum must never bump a refund")
    assert "uplift" not in c_min, (
        "case 13 the refunding account received a group uplift — a minimum "
        "turned a refund into a smaller refund")
    eq(by_id[ACCOUNT_C].minimum_deferred_to_group, False,
       "case 13 a refunding account is NOT eligible for a share of the uplift")

    # ── The group arithmetic. This block comes FIRST among the post-fix
    #    assertions on purpose: every check above it also passes against the
    #    pre-F36D engine, so these are the ones that actually discriminate,
    #    and a reader running this case against the old code should see it
    #    fail on the arithmetic rather than on a missing attribute.
    detail = grp.group_detail["groups"]
    eq(len(detail), 1, "case 13 one billing group")
    g = detail[0]
    eq(g["scope"], "BILLING_GROUP", "case 13 scope")
    eq(g["scope_id"], BILLING_GROUP, "case 13 scope id")
    eq(sorted(g["account_ids"]), sorted([ACCOUNT_A, ACCOUNT_B, ACCOUNT_C]),
       "case 13 all THREE accounts are in the bucket, the refund included")

    eq(D(g["group_subtotal_before_minimum"]), D("2700.00"),
       "case 13 the subtotal must be 2,000 + 1,500 - 800")
    assert D(g["group_subtotal_before_minimum"]) != D("3500.00"), (
        "case 13 REGRESSION: the subtotal is 3,500.00 — the refunding account "
        "has been dropped from the sum again (fee35-F36D)")
    eq(D(g["shortfall"]), D("2300.00"), "case 13 shortfall")
    assert D(g["shortfall"]) != D("1500.00"), (
        "case 13 REGRESSION: a 1,500.00 shortfall is the pre-F36D number, "
        "computed against a subtotal that omitted the refund")

    # ── C IS counted, and its own line's trace says so. [requirement 4]
    eq(by_id[ACCOUNT_C].minimum_group_contributor, True,
       "case 13 a refunding account is still a MEMBER of the group bucket")
    eq(c_min["counted_in_group_subtotal"], True,
       "case 13 C's own MINIMUM step must record that it was counted")
    eq(D(c_min["group_contribution"]), D("-800.00"),
       "case 13 C's own trace must name the -800.00 it contributed")
    eq(c_min["scope_id"], BILLING_GROUP,
       "case 13 C's trace must name the group it contributed to")

    # calc_detail traces every contribution by name. [requirement 4]
    contrib = {c["account_id"]: c for c in g["contributions"]}
    eq(len(contrib), 3, "case 13 one contribution row per account")
    eq(D(contrib[ACCOUNT_C]["amount_before"]), D("-800.00"),
       "case 13 the group trace must show C's -800.00 explicitly")
    eq(contrib[ACCOUNT_C]["counted_in_subtotal"], True, "case 13 C counted")
    eq(contrib[ACCOUNT_C]["eligible_for_uplift"], False, "case 13 C not bumped")
    eq(D(g["refund_contribution"]), D("-800.00"),
       "case 13 the refunds inside the subtotal are totalled for the reader")
    eq(sum((D(c["amount_before"]) for c in g["contributions"]), D(0)),
       D(g["group_subtotal_before_minimum"]),
       "case 13 the traced contributions must ADD UP to the stated subtotal")
    # Spelled out with the EXACT unrounded terms, trailing zeros and all —
    # a sum quantized for looks is no longer the sum that was used.
    eq(g["subtotal_arithmetic"], "2000.00 + 1500.00 + (-800.000) = 2700.000",
       "case 13 the sum is spelled out so a reader can check it by eye")
    eq([x["account_id"] for x in g["uplift_excludes"]], [ACCOUNT_C],
       "case 13 the trace names who was excluded from the uplift, and why")

    # ── The uplift goes to the charging accounts only, and sums exactly.
    alloc = {a["account_id"]: D(a["share"]) for a in g["allocations"]}
    eq(sorted(alloc), sorted([ACCOUNT_A, ACCOUNT_B]),
       "case 13 only the CHARGING accounts share the uplift")
    eq(alloc[ACCOUNT_A], D("1314.29"), "case 13 A's pro-rata share")
    eq(alloc[ACCOUNT_B], D("985.71"), "case 13 B's pro-rata share")
    eq(sum(alloc.values(), D(0)), D(g["shortfall"]),
       "case 13 allocations must sum EXACTLY to the shortfall")

    eq(by_id[ACCOUNT_A].amount, D("3314.29"), "case 13 account A")
    eq(by_id[ACCOUNT_B].amount, D("2485.71"), "case 13 account B")
    assert by_id[ACCOUNT_A].amount != D("2857.14"), (
        "case 13 REGRESSION: A billed 2,857.14, its pre-F36D number")
    assert by_id[ACCOUNT_B].amount != D("2142.86"), (
        "case 13 REGRESSION: B billed 2,142.86, its pre-F36D number")

    eq(grp.total, D("5000.00"),
       "case 13 the group's total net fee must equal the minimum EXACTLY when "
       "the floor binds — the refund included")

    # ── The floor that does NOT bind: the same fixture, minimum 2,000. The
    #    subtotal (2,700) clears it, so nobody is bumped and the group bills
    #    its true subtotal. A subtotal that still dropped the refund would
    #    read 3,500 here too, but the group total would be 2,700 either way —
    #    which is exactly why the binding case above is the one that proves it.
    loose = calculate_group_fees([
        request(sched=schedule(minimum_fee=D("2000"),
                               minimum_fee_scope="BILLING_GROUP"),
                acct=acct(r.data.account.id), balances=r.data.balances,
                credits=r.credits)
        for r in reqs
    ])
    lg = loose.group_detail["groups"][0]
    eq(lg["outcome"], "not_reached", "case 13 a 2,000 floor is already cleared")
    eq(D(lg["group_subtotal_before_minimum"]), D("2700.00"),
       "case 13 the non-binding subtotal counts the refund too")
    eq(loose.total, D("2700.00"),
       "case 13 when the floor does not bind the group bills its true subtotal")

    # ── A group that is ALL refunds has nowhere to put a shortfall.
    all_refund = calculate_group_fees([reqs[2]])
    ar = all_refund.group_detail["groups"][0]
    eq(ar["outcome"], "no_chargeable_account",
       "case 13 a group of nothing but refunds cannot be bumped to its minimum")
    eq(all_refund.total, D("-800.00"),
       "case 13 and it bills the refund, not the minimum")

    # ── One DELIBERATE behaviour change comes with the fix, pinned here.
    #    The refund guard now sits BELOW the scope resolution, so a refunding
    #    account under a group-scoped minimum with no resolved group id raises
    #    instead of silently billing its refund. It must: an account that
    #    cannot be placed in a bucket cannot be counted in one, and silently
    #    dropping it is the bug this patch exists to fix. Every NON-refunding
    #    account under the same schedule already raised here (see F4), so this
    #    removes an inconsistency rather than adding a rule.
    orphan = request(
        sched=sched,
        acct=AccountInput(id=ACCOUNT_C, household_id=None,
                          billing_group_id=None, is_billable=True),
        balances=(balance("480000", account_id=ACCOUNT_C),),
        credits=(CreditInput(
            id="c-f36d", credit_source="SPV_MGMT_FEE_OFFSET",
            offset_pct=D("1.0"), basis_amount=D("2000.00"),
            scope_type="ACCOUNT", scope_id=ACCOUNT_C,
            effective_from=date(2026, 1, 1)),))
    try:
        calculate_account_fee(orphan)
        raise AssertionError(
            "case 13 a refunding account under a BILLING_GROUP minimum with no "
            "billing_group_id was billed silently — it cannot be placed in a "
            "bucket, so its refund would go uncounted, which is fee35-F36D "
            "again by another route")
    except GroupScopeMissingError as exc:
        assert "billing_group_id" in str(exc), (
            "case 13 the refusal must name the field it could not resolve")

    return ("A $3,314.29 + B $2,485.71 + C -$800.00 = $5,000.00, the group "
            "minimum exactly; the subtotal is 2,700.00 counting C's refund, "
            "not the pre-F36D 3,500.00 that dropped it")


# ═══════════════════════════════════════════════════════════════════════════
# Structural verification
# ═══════════════════════════════════════════════════════════════════════════

_DB_TOKENS = (
    "asyncpg", "psycopg", "sqlalchemy", "aiopg", "DATABASE_URL",
    "create_pool", "statement_cache_size",
)
_DB_PARAM_NAMES = {
    "conn", "connection", "con", "pool", "session", "db", "cursor", "cur",
    "executor", "dsn",
}


def v1_no_database() -> str:
    """The engine cannot reach a database. Proved twice, two different ways."""
    sources = {}
    for name in ("services/fee_calc.py", "services/fee_calc_inputs.py"):
        text = (API_DIR / name).read_text()
        # Strip the module docstring's own mention of the grep, and comments,
        # so the check measures code rather than prose about the check.
        code = re.sub(r'"""(?:.|\n)*?"""', "", text)
        code = re.sub(r"#.*", "", code)
        sources[name] = code
    hits = {
        name: [t for t in _DB_TOKENS if t in code]
        for name, code in sources.items()
    }
    offenders = {k: v for k, v in hits.items() if v}
    assert not offenders, f"database tokens found in engine source: {offenders}"

    probe = (
        "import sys, glob\n"
        f"for s in sorted(glob.glob({str(API_DIR / 'venv/lib/python3*/site-packages')!r})):\n"
        "    sys.path.insert(0, s)\n"
        f"sys.path.insert(0, {str(API_DIR)!r})\n"
        "import services.fee_calc\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('asyncpg','psycopg','psycopg2','sqlalchemy','aiopg')]\n"
        "print('MODULES:' + ','.join(sorted(bad)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, (
        f"the engine could not be imported in a clean interpreter: "
        f"{proc.stderr.strip()[-600:]}")
    line = [l for l in proc.stdout.splitlines() if l.startswith("MODULES:")]
    assert line, f"probe produced no verdict: {proc.stdout!r} {proc.stderr!r}"
    loaded = line[0][len("MODULES:"):]
    assert loaded == "", (
        f"importing services.fee_calc pulled in database drivers: {loaded}")

    import inspect

    import services.fee_calc as engine
    import services.fee_calc_inputs as inputs

    bad_params = []
    for module in (engine, inputs):
        for fname, fn in vars(module).items():
            if not callable(fn) or getattr(fn, "__module__", None) != module.__name__:
                continue
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                continue
            for p in sig.parameters:
                if p.lower() in _DB_PARAM_NAMES:
                    bad_params.append(f"{module.__name__}.{fname}({p})")
    assert not bad_params, f"connection-shaped parameters: {bad_params}"

    return ("no DB token in either module's code; a clean interpreter importing "
            "services.fee_calc loads zero database drivers; no function takes a "
            "connection-shaped parameter")


def _floats_in(node, path="calc_detail") -> list[str]:
    if isinstance(node, float):
        return [path]
    if isinstance(node, dict):
        out = []
        for k, v in node.items():
            out += _floats_in(v, f"{path}.{k}")
        return out
    if isinstance(node, (list, tuple)):
        out = []
        for i, v in enumerate(node):
            out += _floats_in(v, f"{path}[{i}]")
        return out
    return []


def v2_floats_refused() -> str:
    """A float is refused at the boundary, at every input type, not coerced."""
    attempts = (
        ("FeeTierInput.lower_bound",
         lambda: FeeTierInput(tier_seq=1, lower_bound=0.0, rate_bps=D("100"))),
        ("FeeTierInput.rate_bps",
         lambda: FeeTierInput(tier_seq=1, lower_bound=D("0"), rate_bps=100.0)),
        ("FeeScheduleInput.minimum_fee",
         lambda: schedule(minimum_fee=900.0, minimum_fee_scope="ACCOUNT")),
        ("FeeScheduleInput.day_weight_threshold",
         lambda: schedule(day_weight_threshold=10000.0)),
        ("DailyBalanceInput.total_market_value",
         lambda: DailyBalanceInput(as_of_date=P_END, total_market_value=2500000.0)),
        ("FlowInput.amount",
         lambda: FlowInput(flow_date=P_END, amount=30000.0)),
        ("PositionInput.market_value",
         lambda: PositionInput(asset_id=ASSET_CARVE, market_value=600000.0)),
        ("DiscountInput.value",
         lambda: DiscountInput(discount_type="PCT_OFF", value=20.0)),
        ("CreditInput.basis_amount",
         lambda: CreditInput(credit_source="12B1", basis_amount=3000.0)),
        ("CreditInput.offset_pct",
         lambda: CreditInput(credit_source="12B1", basis_amount=D("3000"),
                             offset_pct=0.5)),
    )
    accepted = []
    for label, build in attempts:
        try:
            build()
        except FeeCalcInputError:
            continue
        except Exception as exc:  # noqa: BLE001
            accepted.append(f"{label} raised the wrong error: {type(exc).__name__}")
        else:
            accepted.append(f"{label} SILENTLY ACCEPTED a float")
    assert not accepted, "; ".join(accepted)

    # int and str must still work — JSON has no decimal type.
    eq(FeeTierInput(tier_seq=1, lower_bound=0, rate_bps="100").lower_bound, D(0),
       "int must still be accepted")
    eq(FeeTierInput(tier_seq=1, lower_bound="1000000.00",
                    rate_bps=D("75")).lower_bound, D("1000000.00"),
       "a decimal string must still be accepted")

    # And nothing became a float on the way out.
    r = calculate_account_fee(request())
    leaked = _floats_in(r.calc_detail)
    assert not leaked, f"floats leaked into calc_detail at: {leaked}"
    for field in ("amount", "amount_unrounded", "billable_value", "gross_fee",
                  "account_value"):
        value = getattr(r, field)
        assert isinstance(value, Decimal), (
            f"result.{field} is {type(value).__name__}, not Decimal")
    return (f"{len(attempts)} float attempts all refused with "
            f"FeeCalcInputError; int and decimal-string still accepted; zero "
            f"floats anywhere in calc_detail or the result")


def v3_pure_function() -> str:
    """Identical inputs, byte-identical output. No clock, no randomness."""
    a = calculate_account_fee(request())
    b = calculate_account_fee(request())
    ja, jb = json.dumps(a.calc_detail), json.dumps(b.calc_detail)
    assert ja == jb, "two identical single-account runs produced different traces"
    eq(a.amount, b.amount, "two identical runs must give the same amount")

    complex_req = lambda: request(  # noqa: E731
        balances=(balance("2530000"),),
        flows=(FlowInput(id="f", account_id=ACCOUNT_A, flow_date=date(2026, 5, 17),
                         amount=D("30000")),),
        sched=schedule(day_weight_threshold=D("10000"),
                       minimum_fee=D("900"), minimum_fee_scope="ACCOUNT",
                       maximum_fee=D("9000")),
        discounts=(DiscountInput(id="d", discount_type="PCT_OFF", value=D("20")),),
        credits=(CreditInput(id="c", credit_source="12B1", basis_amount=D("120"),
                             offset_pct=D("1.0")),),
        service_start=date(2026, 5, 15),
    )
    c1 = json.dumps(calculate_account_fee(complex_req()).calc_detail)
    c2 = json.dumps(calculate_account_fee(complex_req()).calc_detail)
    assert c1 == c2, "two identical full-pipeline runs produced different traces"

    def group():
        sched = schedule(minimum_fee=D("6000"), minimum_fee_scope="HOUSEHOLD")
        return calculate_group_fees([
            request(sched=sched, acct=account(ACCOUNT_A),
                    balances=(balance("400000", account_id=ACCOUNT_A),)),
            request(sched=sched, acct=account(ACCOUNT_B),
                    balances=(balance("800000", account_id=ACCOUNT_B),)),
        ])

    g1, g2 = group(), group()
    assert json.dumps(g1.group_detail) == json.dumps(g2.group_detail), (
        "two identical group runs produced different group traces")
    eq([r.amount for r in g1.results], [r.amount for r in g2.results],
       "two identical group runs must give the same amounts")
    return (f"{len(ja)}-char single-account trace, {len(c1)}-char "
            f"full-pipeline trace and the group trace all byte-identical "
            f"across repeat runs")


def v4_ordering_refusals() -> str:
    """The two policy shapes the engine refuses rather than reinterprets."""
    try:
        schedule(ordering_policy=["EXCLUSIONS", "TIERS", "DISCOUNTS",
                                  "CREDITS", "MINIMUM"])
    except FeeCalcInputError as exc:
        assert "MAXIMUM" in str(exc), (
            f"a policy missing a step must name the missing step: {exc}")
    else:
        raise AssertionError(
            "a policy missing MAXIMUM was accepted — the step would simply "
            "never run and nothing would say so")

    try:
        calculate_account_fee(request(sched=schedule(
            ordering_policy=["TIERS", "EXCLUSIONS", "DISCOUNTS", "CREDITS",
                             "MINIMUM", "MAXIMUM"])))
    except OrderingNotSupportedError as exc:
        assert "EXCLUSIONS after TIERS" in str(exc), str(exc)
    else:
        raise AssertionError("EXCLUSIONS after TIERS was silently reinterpreted")

    try:
        calculate_account_fee(request(
            sched=schedule(ordering_policy=["EXCLUSIONS", "TIERS", "DISCOUNTS",
                                            "CREDITS", "MINIMUM", "MAXIMUM"]),
            discounts=(DiscountInput(id="d", discount_type="PCT_OFF",
                                     value=D("20"),
                                     applies_to="NET_OF_CREDITS"),)))
    except OrderingNotSupportedError as exc:
        assert "NET_OF_CREDITS" in str(exc), str(exc)
    else:
        raise AssertionError(
            "a NET_OF_CREDITS discount ran before CREDITS and produced a "
            "number anyway")

    try:
        calculate_account_fee(request(
            positions=(PositionInput(id="p", account_id=ACCOUNT_A,
                                     asset_id=ASSET_CARVE,
                                     market_value=D("500000")),),
            exclusions=(ExclusionInput(
                id="x", basis_type="SECURITY", basis_value=ASSET_CARVE,
                treatment="REDUCED_RATE", alt_fee_schedule_id=ALT_SCHEDULE_ID,
                reason="r", effective_from=date(2026, 1, 1)),)))
    except AltScheduleMissingError as exc:
        assert ALT_SCHEDULE_ID in str(exc), str(exc)
    else:
        raise AssertionError(
            "a REDUCED_RATE carve-out with no loaded alt schedule billed at "
            "zero instead of refusing")
    return ("missing policy step, EXCLUSIONS-after-TIERS, NET_OF_CREDITS "
            "before CREDITS and an unloaded alt schedule are all refused by "
            "name")


def v5_business_days() -> str:
    """BUSINESS_DAYS prorates differently from CALENDAR_DAYS, and says why.

    Q2 2026 has 91 calendar days and 65 weekdays. Service from 2026-05-15
    (a Friday) covers 47 calendar days and 33 weekdays.

        calendar  5,312.50 x 47/91 = 2,743.8186... → 2,743.82
        business  5,312.50 x 33/65 = 2,697.1153... → 2,697.12
    """
    eq(count_days(P_START, P_END, "CALENDAR_DAYS"), 91, "Q2 2026 calendar days")
    eq(count_days(P_START, P_END, "BUSINESS_DAYS"), 65, "Q2 2026 weekdays")
    eq(count_days(date(2026, 5, 15), P_END, "BUSINESS_DAYS"), 33,
       "weekdays from 2026-05-15")

    r = calculate_account_fee(request(
        sched=schedule(proration_method="BUSINESS_DAYS"),
        service_start=date(2026, 5, 15)))
    eq(r.amount, D("2697.12"), "business-day prorated fee")
    assert r.amount != D("2743.82"), (
        "BUSINESS_DAYS and CALENDAR_DAYS gave the same answer")
    assert A_BUSINESS_DAYS in r.assumptions, (
        "the Mon-Fri simplification must be declared in the result, not only "
        "in a docstring")

    none_r = calculate_account_fee(request(
        sched=schedule(proration_method="NONE"),
        service_start=date(2026, 5, 15)))
    eq(none_r.amount, D("5312.50"),
       "proration_method='NONE' must bill a partial period in full")
    return ("91 calendar / 65 business days; $2,743.82 vs $2,697.12 vs "
            "$5,312.50 under NONE — the assumption is carried in the result")


def v6_blended_flagged() -> str:
    """BLENDED_PUBLISHED is calculated as GRADUATED and says so out loud."""
    r = calculate_account_fee(request(
        sched=schedule(tier_method="BLENDED_PUBLISHED")))
    graduated = calculate_account_fee(request())
    eq(r.amount, graduated.amount,
       "BLENDED_PUBLISHED must match GRADUATED under the stated assumption")
    assert A_BLENDED in r.assumptions, (
        "the BLENDED_PUBLISHED assumption must appear in the result's "
        "assumptions, not be picked silently")
    assert A_BLENDED not in graduated.assumptions, (
        "a plain GRADUATED schedule must not carry the BLENDED assumption")
    assert A_BLENDED in r.calc_detail["assumptions"], (
        "the assumption must also survive into the persisted calc_detail")
    return (f"${r.amount} matches GRADUATED, and the assumption is stamped "
            f"into calc_detail for fee36 to surface")


def v7_cash_and_margin() -> str:
    """cash_treatment and margin_treatment, all four branches.

        value 2,500,000, cash 300,000, margin 200,000

        INCLUDE            billable 2,500,000 → 5,312.50
        EXCLUDE            billable 2,200,000 → 1,000,000@1% + 1,200,000@.75%
                                    = 10,000 + 9,000 = 19,000 /4 = 4,750.00
        EXCLUDE_ABOVE_PCT  allowance 5% of 2,500,000 = 125,000;
                           excess 175,000; billable 2,325,000
                                    = 10,000 + 9,937.50 = 19,937.50 /4
                                    = 4,984.375 → 4,984.38
        REDUCE_BILLABLE    billable 2,300,000
                                    = 10,000 + 9,750 = 19,750 /4 = 4,937.50
    """
    b = (balance("2500000", cash="300000", margin="200000"),)
    plain = calculate_account_fee(request(balances=b))
    eq(plain.amount, D("5312.50"), "INCLUDE / IGNORE")

    ex_cash = calculate_account_fee(request(
        balances=b, sched=schedule(cash_treatment="EXCLUDE")))
    eq(ex_cash.billable_value, D("2200000"), "EXCLUDE cash billable")
    eq(ex_cash.amount, D("4750.00"), "EXCLUDE cash fee")

    above = calculate_account_fee(request(
        balances=b, sched=schedule(cash_treatment="EXCLUDE_ABOVE_PCT",
                                   cash_exclusion_pct=D("0.05"))))
    eq(above.billable_value, D("2325000.00"), "EXCLUDE_ABOVE_PCT billable")
    eq(above.amount, D("4984.38"), "EXCLUDE_ABOVE_PCT fee")

    margin = calculate_account_fee(request(
        balances=b, sched=schedule(margin_treatment="REDUCE_BILLABLE")))
    eq(margin.billable_value, D("2300000"), "REDUCE_BILLABLE billable")
    eq(margin.amount, D("4937.50"), "REDUCE_BILLABLE fee")

    try:
        schedule(cash_treatment="EXCLUDE_ABOVE_PCT")
    except FeeCalcInputError:
        pass
    else:
        raise AssertionError(
            "EXCLUDE_ABOVE_PCT with a NULL cash_exclusion_pct was accepted; it "
            "would have excused 100% of cash")
    return ("INCLUDE 5,312.50 / EXCLUDE 4,750.00 / EXCLUDE_ABOVE_PCT 4,984.38 "
            "/ REDUCE_BILLABLE 4,937.50, and the unpaired pct is refused")


def v8_valuation_methods() -> str:
    """All four valuation methods, and the billing-source rule.

    Three month-end balances: Apr 30 = 2,400,000, May 31 = 2,500,000,
    Jun 30 = 2,600,000, plus an Apr 1 opener of 2,000,000.

        PERIOD_END     2,600,000 → 10,000 + 12,000 = 22,000 /4 = 5,500.00
        PERIOD_START   2,000,000 → 10,000 +  7,500 = 17,500 /4 = 4,375.00
        AVG_DAILY      mean of all four = 2,375,000
                       → 10,000 + 10,312.50 = 20,312.50 /4 = 5,078.125
                       → 5,078.13
        AVG_MONTH_END  mean of three = 2,500,000 → 21,250 /4 = 5,312.50
    """
    rows = (
        balance("2000000", as_of=P_START),
        balance("2400000", as_of=date(2026, 4, 30)),
        balance("2500000", as_of=date(2026, 5, 31)),
        balance("2600000", as_of=P_END),
    )
    got = {}
    for method, expected in (("PERIOD_END", D("5500.00")),
                             ("PERIOD_START", D("4375.00")),
                             ("AVG_DAILY", D("5078.13")),
                             ("AVG_MONTH_END", D("5312.50"))):
        r = calculate_account_fee(request(
            balances=rows, sched=schedule(valuation_method=method)))
        eq(r.amount, expected, f"{method} fee")
        got[method] = r.amount
    assert len(set(got.values())) == 4, (
        f"the four valuation methods must give four different answers: {got}")

    contaminated = rows + (balance("9999999", as_of=date(2026, 3, 31)),)
    r = calculate_account_fee(request(
        balances=contaminated, sched=schedule(valuation_method="AVG_DAILY")))
    eq(r.amount, got["AVG_DAILY"],
       "a balance from before the period must not move an in-period average")

    from services.fee_calc import AmbiguousBalanceError
    two_sources = (
        DailyBalanceInput(as_of_date=P_END, total_market_value=D("2500000"),
                          source_system="ALTRUIST"),
        DailyBalanceInput(as_of_date=P_END, total_market_value=D("2600000"),
                          source_system="SCHWAB"),
    )
    try:
        calculate_account_fee(request(balances=two_sources))
    except AmbiguousBalanceError as exc:
        assert "ALTRUIST" in str(exc) and "SCHWAB" in str(exc), str(exc)
    else:
        raise AssertionError(
            "two disagreeing unflagged sources for the same day produced a "
            "number instead of a refusal")

    flagged = (
        DailyBalanceInput(as_of_date=P_END, total_market_value=D("2500000"),
                          source_system="ALTRUIST", is_billing_source=True),
        DailyBalanceInput(as_of_date=P_END, total_market_value=D("2600000"),
                          source_system="SCHWAB"),
    )
    r = calculate_account_fee(request(balances=flagged))
    eq(r.amount, D("5312.50"),
       "is_billing_source must settle which feed is billed on")
    return ("PERIOD_END 5,500.00 / PERIOD_START 4,375.00 / AVG_DAILY 5,078.13 "
            "/ AVG_MONTH_END 5,312.50; out-of-period rows ignored; "
            "disagreeing unflagged sources refused; is_billing_source honoured")


def v9_remaining_adjustment_types() -> str:
    """The exclusion, discount and credit shapes the golden cases do not reach.

    All six basis_type values, all five discount_type values and all five
    credit_source values are deployed vocabulary. Cases 8-10 exercise three of
    them; a vocabulary member with no test is a branch nobody has ever run.
    """
    pos = (
        PositionInput(id="p1", account_id=ACCOUNT_A, asset_id="ast-1",
                      market_value=D("400000"), taxonomy_key="taxonomy_sc_3",
                      tags=("ESG_SCREENED",)),
        PositionInput(id="p2", account_id=ACCOUNT_A, asset_id="ast-2",
                      market_value=D("600000"), taxonomy_key="taxonomy_mc_3_2"),
        PositionInput(id="p3", account_id=ACCOUNT_A, asset_id="ast-3",
                      market_value=D("300000"), taxonomy_key="taxonomy_sub_3_2_1"),
        # The trap: a string prefix of taxonomy_sc_3 and an unrelated class.
        PositionInput(id="p4", account_id=ACCOUNT_A, asset_id="ast-4",
                      market_value=D("200000"), taxonomy_key="taxonomy_sc_30"),
    )

    # ASSET_CLASS must catch the classes BENEATH the one named — which are not
    # string prefixes of it — and must NOT catch taxonomy_sc_30, which is.
    # Inclusion and exclusion proved on the same fixture, in both directions.
    r = calculate_account_fee(request(positions=pos, exclusions=(
        ExclusionInput(id="x", basis_type="ASSET_CLASS",
                       basis_value="taxonomy_sc_3", treatment="EXCLUDE",
                       reason="r", effective_from=date(2026, 1, 1)),)))
    matched = step_of(r, "EXCLUSIONS")["deductions"][-1]["matched_positions"]
    eq(sorted(p["taxonomy_key"] for p in matched),
       ["taxonomy_mc_3_2", "taxonomy_sc_3", "taxonomy_sub_3_2_1"],
       "ASSET_CLASS at taxonomy_sc_3 must catch its mc and sub descendants and "
       "must NOT catch taxonomy_sc_30")
    eq(r.billable_value, D("1200000"),
       "ASSET_CLASS excludes 400,000 + 600,000 + 300,000, leaving the "
       "200,000 of taxonomy_sc_30 billable")

    r = calculate_account_fee(request(positions=pos, exclusions=(
        ExclusionInput(id="x", basis_type="ASSET_CLASS",
                       basis_value="taxonomy_mc_3_2", treatment="EXCLUDE",
                       reason="r", effective_from=date(2026, 1, 1)),)))
    eq(r.billable_value, D("1600000"),
       "ASSET_CLASS at the major-class level catches its sub-categories only")

    assert taxonomy_covers("taxonomy_sc_3", "taxonomy_sub_3_2_1")
    assert not taxonomy_covers("taxonomy_sc_3", "taxonomy_sc_30")
    assert not taxonomy_covers("taxonomy_mc_3_2", "taxonomy_sc_3")

    r = calculate_account_fee(request(positions=pos, exclusions=(
        ExclusionInput(id="x", basis_type="POSITION_TAG",
                       basis_value="ESG_SCREENED", treatment="EXCLUDE",
                       reason="r", effective_from=date(2026, 1, 1)),)))
    eq(r.billable_value, D("2100000"), "POSITION_TAG exclusion")

    r = calculate_account_fee(request(exclusions=(
        ExclusionInput(id="x", basis_type="ACCOUNT", treatment="EXCLUDE",
                       reason="r", effective_from=date(2026, 1, 1)),)))
    eq(r.amount, D("0.00"), "an ACCOUNT exclusion bills nothing")

    r = calculate_account_fee(request(
        acct=account(is_held_away=True), exclusions=(
            ExclusionInput(id="x", basis_type="HELD_AWAY", treatment="EXCLUDE",
                           reason="r", effective_from=date(2026, 1, 1)),)))
    eq(r.amount, D("0.00"), "HELD_AWAY excludes a held-away account")
    r = calculate_account_fee(request(
        acct=account(is_held_away=False), exclusions=(
            ExclusionInput(id="x", basis_type="HELD_AWAY", treatment="EXCLUDE",
                           reason="r", effective_from=date(2026, 1, 1)),)))
    eq(r.amount, D("5312.50"), "HELD_AWAY excludes nothing when it is false")

    # CASH exclusion, and the double-count guard when cash_treatment already ran.
    cash_b = (balance("2500000", cash="300000"),)
    r = calculate_account_fee(request(balances=cash_b, exclusions=(
        ExclusionInput(id="x", basis_type="CASH", treatment="EXCLUDE",
                       reason="r", effective_from=date(2026, 1, 1)),)))
    eq(r.billable_value, D("2200000"), "a CASH exclusion removes cash once")
    r = calculate_account_fee(request(
        balances=cash_b, sched=schedule(cash_treatment="EXCLUDE"),
        exclusions=(ExclusionInput(id="x", basis_type="CASH",
                                   treatment="EXCLUDE", reason="r",
                                   effective_from=date(2026, 1, 1)),)))
    eq(r.billable_value, D("2200000"),
       "cash_treatment=EXCLUDE plus a CASH exclusion must not deduct cash twice")

    # FLAT exclusion: value out of the base, a fixed per-period amount back in.
    r = calculate_account_fee(request(
        positions=pos, exclusions=(ExclusionInput(
            id="x", basis_type="SECURITY", basis_value="ast-2",
            treatment="FLAT", flat_amount=D("250.00"), reason="r",
            effective_from=date(2026, 1, 1)),)))
    # 1,900,000 billable → 10,000 + 6,750 = 16,750 /4 = 4,187.50, + 250.00
    eq(r.billable_value, D("1900000"), "FLAT exclusion removes the value")
    eq(r.amount, D("4437.50"), "FLAT exclusion adds its per-period amount")

    # Discounts.
    for dtype, value, expected in (
        ("DOLLAR_CREDIT", D("500.00"), D("4812.50")),   # 5,312.50 - 500.00
        ("FEE_HOLIDAY", None, D("0.00")),
        ("BPS_OFF", D("10"), D("4687.50")),  # 2,500,000 x 10bps = 2,500 /4 = 625
    ):
        r = calculate_account_fee(request(discounts=(
            DiscountInput(id="d", discount_type=dtype, value=value),)))
        eq(r.amount, expected, f"{dtype} discount")

    from services.fee_calc import DiscountNotCalculableError
    try:
        calculate_account_fee(request(discounts=(
            DiscountInput(id="d", discount_type="SCHEDULE_OVERRIDE"),)))
    except DiscountNotCalculableError:
        pass
    else:
        raise AssertionError(
            "SCHEDULE_OVERRIDE was silently treated as no discount at all")

    # Every credit_source uses the same arithmetic; prove they all reach it.
    for source in ("12B1", "SUB_TA", "SI_EMBEDDED_FEE_OFFSET", "MODEL_FEE_OFFSET"):
        r = calculate_account_fee(request(credits=(
            CreditInput(id="c", credit_source=source, basis_amount=D("200.00"),
                        offset_pct=D("1.0")),)))
        eq(r.amount, D("5112.50"), f"{source} credit")

    # An expired adjustment must not bill.
    r = calculate_account_fee(request(discounts=(
        DiscountInput(id="d", discount_type="PCT_OFF", value=D("20"),
                      effective_from=date(2025, 1, 1),
                      effective_to=date(2025, 12, 31)),)))
    eq(r.amount, D("5312.50"),
       "a discount that expired before the period must not apply")
    eq(step_of(r, "DISCOUNTS")["discounts"][0]["outcome"], "skipped",
       "and the trace must record that it was considered and skipped")

    return ("all 6 basis_type, all 5 discount_type and all 5 credit_source "
            "values exercised; ASSET_CLASS proved in BOTH directions "
            "(catches sc->mc->sub, refuses taxonomy_sc_30); cash double-count "
            "guarded; expired rows skipped with a reason")


def v10_minimum_billable_and_edges() -> str:
    """minimum_billable_value, a non-billable account, and refund guards."""
    r = calculate_account_fee(request(
        balances=(balance("40000"),),
        sched=schedule(minimum_billable_value=D("50000"))))
    eq(r.amount, D("0.00"), "an account under minimum_billable_value bills zero")
    eq(step_of(r, "TIERS")["outcome"], "below_minimum_billable_value",
       "and the trace says which rule zeroed it")

    r = calculate_account_fee(request(acct=account(is_billable=False)))
    eq(r.amount, D("0.00"), "a non-billable account bills zero")
    eq(step_of(r, "SHORT_CIRCUIT")["reason"], "accounts.is_billable is false",
       "and leaves a record that it was considered")

    r = calculate_account_fee(request(
        sched=schedule(billing_timing="ADVANCE", minimum_fee=D("900"),
                       minimum_fee_scope="ACCOUNT", maximum_fee=D("9000")),
        service_end=date(2026, 5, 17)))
    eq(r.amount, D("-2568.68"),
       "a minimum must not turn a refund into a charge")
    eq(step_of(r, "MINIMUM")["outcome"], "skipped", "minimum skipped on a refund")
    eq(step_of(r, "MAXIMUM")["outcome"], "skipped", "maximum skipped on a refund")

    r = calculate_account_fee(request(sched=schedule(maximum_fee=D("4000"))))
    eq(r.amount, D("4000.00"), "maximum_fee caps the fee")
    eq(D(step_of(r, "MAXIMUM")["reduction"]), D("1312.50"), "cap reduction")
    return ("minimum_billable_value zeroes with a reason; is_billable=false "
            "short-circuits with a record; minimum/maximum both step aside on a "
            "refund; maximum_fee caps at 4,000.00")


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════


def main() -> int:
    results = Results()
    print(f"engine version:  {ENGINE_VERSION}")
    print(f"python:          {sys.version.split()[0]}")
    print("database:        none — this suite opens no connection\n")

    print("── Task 2: golden cases, every expectation hand-computed ──────────")
    results.check("1", "GRADUATED tiering on a clean 2,500,000", case_1)
    results.check("2", "CLIFF on the same balance gives a different number", case_2)
    results.check("3", "mid-quarter inception, 47 of 91 calendar days", case_3)
    results.check("4", "a 30,000 flow on day 47 is day-weighted", case_4)
    results.check("5", "a 2,000 flow below the threshold is ignored", case_5)
    results.check("6", "minimum_fee bites AFTER a 20% PCT_OFF discount", case_6)
    results.check("7", "the same fixture with MINIMUM before DISCOUNTS differs",
                  case_7)
    results.check("8", "a SECURITY exclusion cuts billable below account value",
                  case_8)
    results.check("9", "a REDUCED_RATE carve-out bills on alt_fee_schedule_id",
                  case_9)
    results.check("10", "an SPV_MGMT_FEE_OFFSET credit reduces the fee", case_10)
    results.check("11", "termination mid-period billed in ADVANCE is a refund",
                  case_11)
    results.check("12", "a HOUSEHOLD minimum sees both accounts", case_12)
    results.check("13", "a refund inside a GROUP minimum is counted, not bumped",
                  case_13)

    print("\n── Verification: the properties the sprint asks for ───────────────")
    results.check("V1", "the engine cannot reach a database", v1_no_database)
    results.check("V2", "a float is refused at the boundary, never coerced",
                  v2_floats_refused)
    results.check("V3", "identical inputs produce byte-identical output",
                  v3_pure_function)
    results.check("V4", "incoherent ordering_policy shapes are refused by name",
                  v4_ordering_refusals)
    results.check("V5", "BUSINESS_DAYS, CALENDAR_DAYS and NONE all differ",
                  v5_business_days)
    results.check("V6", "BLENDED_PUBLISHED is GRADUATED, declared in the result",
                  v6_blended_flagged)
    results.check("V7", "cash_treatment and margin_treatment, all branches",
                  v7_cash_and_margin)
    results.check("V8", "all four valuation methods, and the billing-source rule",
                  v8_valuation_methods)
    results.check("V9", "every deployed exclusion/discount/credit vocabulary value",
                  v9_remaining_adjustment_types)
    results.check("V10", "minimum_billable_value, non-billable, refund guards",
                  v10_minimum_billable_and_edges)

    print("\n── Findings ───────────────────────────────────────────────────────")
    results.find(
        "F1", "fee_credits has NO amount column — offset_pct multiplies nothing",
        "Its only numeric column is offset_pct, confined to [0,1] by "
        "fee_credits_offset_pct_range. The sprint's 'offset_pct of the credit's "
        "stated basis' describes a basis the table does not store, so "
        "CreditInput.basis_amount is a REQUIRED caller-supplied field. fee36 "
        "must decide where that number comes from before any credit is billed.")
    results.find(
        "F2", "fee_discounts.value and fee_credits.offset_pct use different scales",
        "offset_pct is constrained to [0,1] — a fraction. fee_discounts.value "
        "has no constraint at all, and a PCT_OFF of 20 vs 0.20 differs by 100x. "
        "The engine reads PCT_OFF as a percent and refuses values outside "
        "[0,100]; a 0.20 entered meaning 20% would otherwise bill as 0.2%.")
    results.find(
        "F3", "portfolio.positions has no tag column, but POSITION_TAG is deployed",
        "fee_exclusions_basis_type_check admits POSITION_TAG. Tags live in "
        "portfolio.udf_values. PositionInput.tags is caller-supplied; a "
        "POSITION_TAG exclusion against untagged positions excludes nothing, "
        "visibly in calc_detail rather than silently.")
    results.find(
        "F4", "accounts has no billing_group_id",
        "Membership is billing_group_members. A BILLING_GROUP-scoped "
        "minimum_fee therefore depends on a join the engine deliberately does "
        "not do; AccountInput.billing_group_id is the caller's resolved answer "
        "and a missing one raises GroupScopeMissingError rather than quietly "
        "falling back to an account-scoped minimum.")
    results.find(
        "F5", "fee_exclusions.basis_type admits six values, not one",
        "SECURITY, ASSET_CLASS, ACCOUNT, HELD_AWAY, CASH, POSITION_TAG. The "
        "sprint prompt names only SECURITY. All six are implemented and "
        "exercised in [V9].")
    results.find(
        "F6", "no holiday calendar exists in this codebase",
        "BUSINESS_DAYS proration uses a plain Mon-Fri count. A market holiday "
        "inside the period is counted as a business day, overstating the "
        "denominator and slightly understating a partial-period fee. Declared "
        "in every affected result's assumptions, not only in a docstring.")
    results.find(
        "F7", "the schema records no unit for rate_bps, flat_amount or minimum_fee",
        "The engine reads tier rates and tier flat_amounts as ANNUAL, and "
        "minimum_fee / maximum_fee / DOLLAR_CREDIT / exclusion flat_amount as "
        "PER-PERIOD. Neither is derivable from the deployed schema; both are "
        "stamped into calc_detail['assumptions'] so the reading travels with "
        "the number.")
    results.find(
        "F8", "an ASSET_CLASS exclusion cannot use string-prefix matching",
        "Caught by this suite during the sprint: the first implementation used "
        "taxonomy_key.startswith(basis_value), which is wrong in BOTH "
        "directions for Rule 4's key scheme. taxonomy_mc_3_2 is a child of "
        "taxonomy_sc_3 and is NOT a string prefix of it, so a super-class "
        "exclusion caught nothing; taxonomy_sc_30 IS a string prefix and is an "
        "unrelated class, so it would have been excluded by accident. The keys "
        "are now parsed into numeric components and compared component-wise "
        "(taxonomy_covers), and [V9] asserts both directions.")
    results.find(
        "F9", "ordering_policy admits permutations the arithmetic cannot honour",
        "fee34 validates ordering_policy as a permutation, which lets "
        "EXCLUSIONS sit after TIERS — coherent as a list, meaningless as a "
        "calculation, since exclusions change a VALUE and tiers turn a value "
        "into money. The engine refuses rather than re-sorting. Same for a "
        "NET_OF_CREDITS discount under a policy that runs DISCOUNTS first.")
    results.find(
        "F10", "fee36's finding 6g had the SIGN of F36-D backwards",
        "6g reported that dropping a refunding account from the group subtotal "
        "made 'the shortfall charged to the rest too large'. Measured against "
        "the pre-patch engine before anything was changed, it is too SMALL. "
        "Dropping a NEGATIVE contribution RAISES the subtotal — 3,500.00 "
        "instead of 2,700.00 on case 13's fixture — and a higher subtotal is a "
        "smaller shortfall: 1,500.00 instead of 2,300.00. The group settled at "
        "4,200.00 against a 5,000.00 floor. The error was a client UNDERCHARGE "
        "of 800.00, exactly the size of the refund, not an overcharge. The same "
        "bug either way, but 'who was billed wrongly, and do we owe them or do "
        "they owe us' has the opposite answer, so the direction is recorded "
        "here measured rather than reasoned.")
    results.find(
        "F11", "a refunding account with no group id now RAISES — behaviour change",
        "The fix moves the refund guard BELOW the scope resolution in "
        "_minimum_step, because an account that cannot be placed in a bucket "
        "cannot be counted in one. A refunding account under a "
        "BILLING_GROUP/HOUSEHOLD-scoped minimum whose billing_group_id or "
        "household_id is unresolved therefore now raises GroupScopeMissingError "
        "where it previously billed its refund silently. Every NON-refunding "
        "account under the same schedule already raised (see F4), so this "
        "removes an inconsistency rather than adding a rule — but it is still a "
        "real change for any caller that was relying on the silence. Pinned in "
        "case 13.")

    return results.summary()


if __name__ == "__main__":
    raise SystemExit(main())
