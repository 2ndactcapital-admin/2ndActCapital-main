"""fee35-F36D — reproduce, then prove, the group-minimum subtotal bug.

Runs ONE fixture against an arbitrary fee_calc.py given as argv[1], so the
same fixture can be pointed at the pre-patch module (extracted from HEAD) and
at the patched working-tree module. That is what makes case 13 in
verify_fee35.py a real regression test rather than a test that would pass
either way.

    python3 repro_f36d.py .fee35_patch_scratch/fee_calc_prepatch.py
    python3 repro_f36d.py apps/api/services/fee_calc.py

Opens no connection. Decimal only.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import date
from decimal import Decimal

API_DIR = pathlib.Path(__file__).resolve().parent.parent / "apps" / "api"
sys.path.insert(0, str(API_DIR))

from services.fee_calc_inputs import (  # noqa: E402
    AccountCalcRequest,
    AccountInput,
    AccountPeriodInput,
    BillingPeriod,
    CreditInput,
    DailyBalanceInput,
    FeeScheduleInput,
    FeeTierInput,
)

D = Decimal

target = pathlib.Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("fee_calc_under_test", target)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod  # dataclasses resolves __module__ through sys.modules
spec.loader.exec_module(mod)
calculate_group_fees = mod.calculate_group_fees

P_START, P_END = date(2026, 4, 1), date(2026, 6, 30)
GROUP = "b9000000-0000-0000-0000-0000000000b9"
ACC_A = "aaaaaaaa-0000-0000-0000-0000000000a1"
ACC_B = "bbbbbbbb-0000-0000-0000-0000000000b1"
ACC_C = "cccccccc-0000-0000-0000-0000000000c1"

TIERS = (FeeTierInput(tier_seq=1, lower_bound=D("0"), upper_bound=None,
                      rate_bps=D("100")),)
SCHED = FeeScheduleInput(
    id="5cede111-0000-0000-0000-000000000013", code="GRP-MIN",
    name="Billing-group minimum", billing_frequency="QUARTERLY",
    billing_timing="ARREARS", valuation_method="PERIOD_END",
    proration_method="CALENDAR_DAYS", tier_method="GRADUATED", rate_type="BPS",
    product_type="ASSET_MANAGEMENT", day_weight_flows=True, currency="USD",
    status="APPROVED", minimum_fee=D("5000"), minimum_fee_scope="BILLING_GROUP",
)


def req(account_id: str, value: str, credits=()) -> AccountCalcRequest:
    return AccountCalcRequest(
        data=AccountPeriodInput(
            account=AccountInput(id=account_id, billing_group_id=GROUP,
                                 is_billable=True),
            period=BillingPeriod(period_start=P_START, period_end=P_END),
            balances=(DailyBalanceInput(
                account_id=account_id, as_of_date=P_END,
                total_market_value=D(value), cash_value=D("0"),
                margin_balance=D("0"), source_system="ALTRUIST",
                is_billing_source=True, is_final=True),),
        ),
        schedule=SCHED, tiers=TIERS, credits=credits,
    )


#   A  800,000 @ 1.00% = 8,000 annual / 4 = 2,000.00
#   B  600,000 @ 1.00% = 6,000 annual / 4 = 1,500.00
#   C  480,000 @ 1.00% = 4,800 annual / 4 = 1,200.00, less a 2,000 credit
#                                         = -800.00  (a refund)
REQUESTS = [
    req(ACC_A, "800000"),
    req(ACC_B, "600000"),
    req(ACC_C, "480000", credits=(CreditInput(
        id="c8ed1700-0000-0000-0000-000000000001",
        credit_source="SPV_MGMT_FEE_OFFSET", offset_pct=D("1.0"),
        basis_amount=D("2000"), scope_type="ACCOUNT"),)),
]

grp = calculate_group_fees(REQUESTS)
by = grp.by_account()
g = grp.group_detail["groups"][0] if grp.group_detail["groups"] else {}

print(f"module under test : {target}")
print(f"engine version    : {mod.ENGINE_VERSION}")
print("")
print(f"  account_ids in the bucket        : {g.get('account_ids')}")
print(f"  group_subtotal_before_minimum    : {g.get('group_subtotal_before_minimum')}")
print(f"  minimum_fee                      : {g.get('minimum_fee')}")
print(f"  outcome                          : {g.get('outcome')}")
print(f"  shortfall                        : {g.get('shortfall')}")
for a in g.get("allocations", []):
    print(f"    alloc {a['account_id'][:8]}  before={a['amount_before']:>10}  "
          f"share={a['share']:>10}")
print("")
print(f"  A final : {by[ACC_A].amount:>10}")
print(f"  B final : {by[ACC_B].amount:>10}")
print(f"  C final : {by[ACC_C].amount:>10}   (deferred={by[ACC_C].minimum_deferred_to_group})")
print(f"  GROUP TOTAL : {grp.total:>10}   vs minimum 5000.00")
print("")

print("  hand-computed truth: 2000.00 + 1500.00 + (-800.00) = 2700.00 subtotal,")
print("                       5000.00 floor -> 2300.00 shortfall, total 5000.00")
if g.get("group_subtotal_before_minimum") is not None:
    booked = D(g["group_subtotal_before_minimum"])
    print(f"  subtotal the engine actually compared      : {booked}")
    print(f"  ERROR in the subtotal                      : {booked - D('2700')} "
          f"({'OVERSTATED' if booked > D('2700') else 'understated'})")
print(f"  group total vs the 5,000 floor             : "
      f"{grp.total - D('5000')}")
