"""Print case 13's actual calc_detail trace — requirement 4, shown not claimed."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = ROOT / "apps" / "api"
sys.path.insert(0, str(API))
sys.path.insert(0, str(API / "scripts"))

import verify_fee35 as V  # noqa: E402

D = V.D
sched = V.schedule(minimum_fee=D("5000"), minimum_fee_scope="BILLING_GROUP")


def acct(a):
    return V.account(a, billing_group_id=V.BILLING_GROUP)


grp = V.calculate_group_fees([
    V.request(sched=sched, acct=acct(V.ACCOUNT_A),
              balances=(V.balance("800000", account_id=V.ACCOUNT_A),)),
    V.request(sched=sched, acct=acct(V.ACCOUNT_B),
              balances=(V.balance("600000", account_id=V.ACCOUNT_B),)),
    V.request(sched=sched, acct=acct(V.ACCOUNT_C),
              balances=(V.balance("480000", account_id=V.ACCOUNT_C),),
              credits=(V.CreditInput(
                  id="c-f36d", credit_source="SPV_MGMT_FEE_OFFSET",
                  offset_pct=D("1.0"), basis_amount=D("2000.00"),
                  scope_type="ACCOUNT", scope_id=V.ACCOUNT_C,
                  effective_from=V.date(2026, 1, 1)),)),
])

print("── the REFUNDING account's own MINIMUM step ───────────────────────────")
print(json.dumps(
    V.step_of(grp.by_account()[V.ACCOUNT_C], "MINIMUM"), indent=2))
print("\n── the group-level trace ──────────────────────────────────────────────")
print(json.dumps(grp.group_detail["groups"][0], indent=2))
