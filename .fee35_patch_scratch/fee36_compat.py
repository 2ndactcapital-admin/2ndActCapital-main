"""fee36 must not need to change. Check the shapes its checks actually read.

fee36 check 6f calls calculate_account_fee DIRECTLY, not through the group
pass, and asserts three things about a refunding account under a
BILLING_GROUP-scoped minimum. All three must still hold.
"""
from __future__ import annotations

import pathlib
import py_compile
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = ROOT / "apps" / "api"
sys.path.insert(0, str(API))
sys.path.insert(0, str(API / "scripts"))

for f in ("services/fee_calc.py", "services/fee_runs.py",
          "scripts/verify_fee35.py", "scripts/verify_fee36.py"):
    py_compile.compile(str(API / f), doraise=True)
    print(f"compiles OK: {f}")

import verify_fee35 as V  # noqa: E402

D = V.D
r = V.calculate_account_fee(V.request(
    sched=V.schedule(minimum_fee=D("5000"), minimum_fee_scope="BILLING_GROUP"),
    acct=V.account(V.ACCOUNT_C, billing_group_id=V.BILLING_GROUP),
    balances=(V.balance("480000", account_id=V.ACCOUNT_C),),
    credits=(V.CreditInput(
        id="c", credit_source="SPV_MGMT_FEE_OFFSET", offset_pct=D("1.0"),
        basis_amount=D("2000.00"), scope_type="ACCOUNT",
        scope_id=V.ACCOUNT_C, effective_from=V.date(2026, 1, 1)),)))
s = V.step_of(r, "MINIMUM")

print("\nfee36 check 6f shape, post-patch, via calculate_account_fee directly:")
print(f"  amount                     = {r.amount}")
print(f"  MINIMUM outcome            = {s['outcome']!r}"
      f"   <- 6f asserts == 'skipped'")
print(f"  minimum_deferred_to_group  = {r.minimum_deferred_to_group}"
      f"   <- 6f asserts is False")
print(f"  minimum_group_contributor  = {r.minimum_group_contributor}"
      f"   <- new; 6f does not read it")
print(f"  json.dumps(calc_detail)    = {len(r.as_json())} chars, encodes clean "
      f"(fee36 stores it as jsonb)")

assert s["outcome"] == "skipped", "fee36 6f would break"
assert r.minimum_deferred_to_group is False, "fee36 6f would break"
print("\n[OK] every assertion fee36 check 6f makes still holds. fee36 unchanged.")
