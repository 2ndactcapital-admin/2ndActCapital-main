FEE MODULE — PATCH fee35-F36D (group-minimum subtotal fix). 1 task +
regression verification. NO PART 1 SQL — fee35 is pure Python, this
patch touches no schema.

BUG, confirmed by fee36's own verify suite (check 6g), not this
sprint's discovery:
  calculate_group_fees in services/fee_calc.py skips minimum-fee logic
  for any account whose credits exceed its gross fee (a refund) — this
  skip is correct in isolation, a refund must never be bumped up to a
  minimum. The bug is WHEN it skips: the refunding account is excluded
  from the group subtotal itself, not just exempted from its own
  minimum bump. A group with accounts A ($2,000 fee), B ($1,500 fee),
  C (-$800 refund) and a $5,000 GROUP minimum should compare $2,700
  (2,000 + 1,500 - 800) against the $5,000 floor, needing a $2,300
  uplift distributed across A and B. As currently implemented, C is
  dropped entirely from the subtotal, comparing $3,500 (2,000 + 1,500)
  against the floor — needing only $1,500 of uplift. The shortfall
  charged to A and B is UNDERSTATED in this direction... verify the
  actual sign live before assuming which direction it's wrong; fee36's
  finding said "too large," which may mean the subtotal is being
  OVERSTATED by dropping a POSITIVE contribution somewhere else in the
  bucket logic instead. DO NOT ASSUME THE DIRECTION FROM THIS PROMPT'S
  PARAPHRASE — reproduce the exact bug with a fixture before touching
  the fix, then fix the reproduced behavior, then prove the fix with
  the same fixture.

CONTEXT, settled: fee36 already correctly resolves billing_group_id
and calls this function correctly. Nothing in fee36 needs to change.
This is entirely inside fee_calc.py's own group-aggregation logic.

STANDING RULES: Decimal only. No interactive prompts. Zero database
access — this module still may not open a connection.

=== TASK 1: Reproduce, fix, prove ===
1. Write a fixture reproducing fee36 check 6g exactly: a billing group
   with at least one refunding account (credits exceed gross fee) and
   at least one charging account, under a GROUP-scoped minimum_fee.
   Run it against the CURRENT (unpatched) calculate_group_fees and
   assert the WRONG behavior first — confirm the bug is real and
   understand its exact direction and magnitude before fixing anything.
2. Fix calculate_group_fees so the refunding account's actual signed
   net_fee (pre-minimum) contributes to the group subtotal used for
   the minimum comparison, while that account individually still never
   receives a minimum-fee bump itself (only non-refunding accounts in
   the group share the uplift). Do not change per-account (ACCOUNT-
   scoped) minimum logic — this fix is scoped to GROUP/HOUSEHOLD-scoped
   minimums only, since fee36 check 6f already proves the per-account
   refund-skip is correct on its own.
3. Re-run the same fixture against the FIXED function and assert the
   correct behavior — the group subtotal must include every account's
   signed contribution, refunding or not, before the floor comparison,
   and the resulting uplift distributed across only the non-refunding
   accounts must sum correctly with the group's total net fee equal to
   exactly the minimum (when the floor binds) or the true subtotal
   (when it doesn't).
4. Add this fixture as a permanent 13th golden case in
   scripts/verify_fee35.py alongside the original 12 — this is exactly
   the kind of case the golden suite exists to guard forever, not a
   one-off regression check that gets deleted after this patch merges.

=== VERIFICATION ===
Re-run the FULL existing fee35 golden-case suite (all 12 original
cases) and confirm all still pass unchanged — this patch must not
alter behavior for any case that doesn't involve a refund inside a
group-scoped minimum. Then confirm the new 13th case:
  1. The bug is reproduced against a saved pre-patch snapshot of the
     function (or documented precisely enough that reverting the fix
     would make this check fail) — prove this is a real regression
     test, not a test that would pass even without the fix.
  2. The patched function produces the correct group subtotal,
     correct per-account uplift distribution, and the refunding
     account's own line is completely unaffected by the group
     minimum (still exactly its refund amount, no bump).
  3. All 12 pre-existing golden cases produce byte-identical results
     to before this patch.
  4. calc_detail for the new case traces the refunding account's
     contribution to the subtotal explicitly, so a future reader can
     see the -$800 (or whatever the real fixture number is) counted,
     not silently dropped.
Report actual results, then stop.

