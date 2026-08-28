"""Prove golden case 13 is a REAL regression test.

Runs verify_fee35.case_13 -- the exact function now committed in the suite --
against the PRE-PATCH fee_calc.py extracted from HEAD. It must FAIL, and it
must fail on the ARITHMETIC. Then every discriminating guard inside case 13 is
re-evaluated one at a time against the pre-patch numbers: any guard the old
code already satisfies is a guard that guards nothing, and is reported.

    python3 .fee35_patch_scratch/regression_proof.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
API_DIR = ROOT / "apps" / "api"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "scripts"))

PRE = ROOT / ".fee35_patch_scratch" / "fee_calc_prepatch.py"

# Load the pre-patch engine and install it as `services.fee_calc` BEFORE
# verify_fee35 is imported, so the suite's own `from services.fee_calc import
# ...` binds to the old code.
spec = importlib.util.spec_from_file_location("services.fee_calc", PRE)
old = importlib.util.module_from_spec(spec)
sys.modules["services.fee_calc"] = old
spec.loader.exec_module(old)

import verify_fee35 as V  # noqa: E402

assert V.calculate_group_fees is old.calculate_group_fees, (
    "the suite did not bind to the pre-patch module — this proof is vacuous")

print(f"pre-patch module : {PRE}")
print("running verify_fee35.case_13 against it...\n")

try:
    summary = V.case_13()
except AssertionError as exc:
    print("case 13 FAILED against the pre-patch engine, as it must.")
    print(f"  first assertion to fire: {exc}\n")
except Exception as exc:  # noqa: BLE001
    print(f"case 13 raised {type(exc).__name__} against the pre-patch "
          f"engine: {exc}\n")
else:
    print(f"case 13 PASSED against the PRE-PATCH engine: {summary}")
    print("\n[FAIL] case 13 would pass without the fix — it guards nothing.")
    raise SystemExit(1)

# One assertion firing proves the case is not vacuous, but not that every
# guard in it is load-bearing. Rebuild the same fixture and check EACH
# discriminating guard against the pre-patch numbers, one at a time.
print("each discriminating guard in case 13, evaluated against the pre-patch")
print("engine — every one must be VIOLATED:\n")

D = V.D
sched = V.schedule(minimum_fee=D("5000"), minimum_fee_scope="BILLING_GROUP")


def acct(account_id):
    return V.account(account_id, billing_group_id=V.BILLING_GROUP)


reqs = [
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
]
grp = old.calculate_group_fees(reqs)
by = grp.by_account()
g = grp.group_detail["groups"][0]

GUARDS = [
    ("bucket holds all THREE accounts",
     sorted(g["account_ids"]) == sorted([V.ACCOUNT_A, V.ACCOUNT_B, V.ACCOUNT_C]),
     f"got {len(g['account_ids'])} accounts"),
    ("group_subtotal == 2700.00",
     D(g["group_subtotal_before_minimum"]) == D("2700.00"),
     f"got {g['group_subtotal_before_minimum']}"),
    ("group_subtotal != 3500.00",
     D(g["group_subtotal_before_minimum"]) != D("3500.00"),
     f"got {g['group_subtotal_before_minimum']}"),
    ("shortfall == 2300.00", D(g["shortfall"]) == D("2300.00"),
     f"got {g['shortfall']}"),
    ("shortfall != 1500.00", D(g["shortfall"]) != D("1500.00"),
     f"got {g['shortfall']}"),
    ("account A == 3314.29", by[V.ACCOUNT_A].amount == D("3314.29"),
     f"got {by[V.ACCOUNT_A].amount}"),
    ("account A != 2857.14", by[V.ACCOUNT_A].amount != D("2857.14"),
     f"got {by[V.ACCOUNT_A].amount}"),
    ("account B == 2485.71", by[V.ACCOUNT_B].amount == D("2485.71"),
     f"got {by[V.ACCOUNT_B].amount}"),
    ("account B != 2142.86", by[V.ACCOUNT_B].amount != D("2142.86"),
     f"got {by[V.ACCOUNT_B].amount}"),
    ("group total == the 5000.00 minimum", grp.total == D("5000.00"),
     f"got {grp.total}"),
    ("contributions trace exists", "contributions" in g, "key absent"),
    ("refund_contribution trace exists", "refund_contribution" in g,
     "key absent"),
    ("uplift_excludes trace exists", "uplift_excludes" in g, "key absent"),
    ("C carries minimum_group_contributor",
     getattr(by[V.ACCOUNT_C], "minimum_group_contributor", None) is True,
     "attribute absent"),
]

satisfied = []
for name, holds, got in GUARDS:
    mark = "SATISFIED (guards nothing!)" if holds else "VIOLATED"
    print(f"  [{mark:<26}] {name:<38} {got}")
    if holds:
        satisfied.append(name)

# Controls: the refunding LINE itself was already correct before the patch
# (fee36 check 6f proves it), so these must hold either way. If one of them
# were violated pre-patch, case 13 would be testing the wrong thing.
print("\ncontrols — already correct before the patch, must still hold:\n")
for name, holds, got in [
    ("C bills exactly -800.00", by[V.ACCOUNT_C].amount == D("-800.00"),
     f"got {by[V.ACCOUNT_C].amount}"),
    ("C's MINIMUM outcome is 'skipped'",
     next(s for s in by[V.ACCOUNT_C].calc_detail["steps"]
          if s.get("step") == "MINIMUM")["outcome"] == "skipped", ""),
    ("C is not deferred_to_group",
     by[V.ACCOUNT_C].minimum_deferred_to_group is False, ""),
]:
    print(f"  [{'HOLDS' if holds else 'BROKEN':<26}] {name:<38} {got}")

print("")
if satisfied:
    print(f"[FAIL] {len(satisfied)} guard(s) satisfied by the OLD code: "
          f"{satisfied}")
    raise SystemExit(1)
print(f"[OK] all {len(GUARDS)} discriminating guards are violated by the "
      f"pre-patch engine.")
print("     Case 13 is a real regression test: reverting fee35-F36D breaks it.")
raise SystemExit(0)
