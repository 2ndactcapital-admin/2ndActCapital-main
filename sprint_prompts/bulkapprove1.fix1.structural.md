BULKAPPROVE1 FIX 1 — make verify_bulkapprove1.py pass. 3 FAILs, all in the
verify scripts (no bulk-approval behaviour failed): (A) the catalog read
returned no constraints at all, (B) the maker ≠ checker CHECK lookup found
nothing (same cause), (C) verify_rep7b's regression assumes every REAL
account is on the migrated default authority — false since the Olivo demo's
11 approved authority settings (a frozen-live-state check). 4 tasks. Branch
bulkapprove1-work. Decide code vs verify with evidence; never weaken an
assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_bulkapprove1 run, 2026-11-05
~18:05 ET: TOTAL 157 PASS, 3 FAIL; every bulk-approval check — per-item
decisions through the single-approval service, maker refused per item,
permission both ways, partial results, cap, audit, bulk == single, isolation,
demo / real rows untouched — PASSED; keep it):
- A: "[FAIL] CHECK, FK and PK constraints were read — {'p': 0, 'c': 0,
  'u': 0, 'f': 0}" — y_catalog (verify_bulkapprove1.py) runs
  "SELECT conname, contype, pg_get_constraintdef(oid) … FROM pg_constraint
  WHERE conrelid = $1::regclass …" through the `platform(...)` helper for 10
  public tables and gets ZERO rows for every one. Find why (the helper's
  fetch mode / return value, the connection, the role, the parameter cast)
  and fix it so the real constraints are read.
- ALSO in y_catalog: `lit()` returns True when NO definition was found
  (`not defs or …`) — an empty result passes. That is exactly why the
  vocabulary checks after it PASSED on zero constraints. VERIFY defect: an
  empty definition list must FAIL, never pass (VERIFY_RULES: an empty list
  never passes a check).
- B: "[FAIL] maker ≠ checker is a CHECK on household_authority,
  account_authority_settings and held_away_imports — []" — same empty
  catalog. After A is fixed, assert it against the real definitions; if a
  table truly has no maker ≠ checker CHECK, say which and assert where the
  rule IS enforced (service / trigger) instead — evidence, not assumption.
- C: nested verify_rep7b.py (own final TOTAL): 129 PASS, 1 FAIL — "the
  existing account(s) outside this run's orgs, loaded through the real
  loader: every path allowed / refused exactly as before, every one on the
  migrated default (no settings row) — 34 row(s); 11 break the rule:
  [('******8836', ['one_off', 'execute', 'instructions', 'route'], True), …]".
  The 34 are the Olivo demo accounts; 11 carry approved non-default
  authority (approved by Joe 2026-11-05) — legitimate live data. VERIFY
  defect in verify_rep7b.py: a check frozen against live state
  (docs/VERIFY_RULES.md). Rewrite it to hold for any real data: every real
  account behaves exactly as its EFFECTIVE authority / custody (its approved
  settings row, else the household's, else the migrated default) says —
  same allowed / refused paths as the rule table — with accounts on the
  default still asserted as before, and a [FIND] line counting how many
  carry approved settings. Not weaker: every account is still checked,
  against the rule that actually applies to it.
- WSL memory is limited: do NOT run verify_bulkapprove1.py, verify_rep7b.py
  or npm run build; --self-checks-only and one-off scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret or a full account number.
Fixtures only; never write a demo-olivo or real row (verify_rep7b reads real
accounts, never writes them). Commit on bulkapprove1-work with message
"bulkapprove1 fix 1: <one line per fix>".

=== TASK 1: DISCOVER === A–C: cause and evidence (A: the helper's behaviour
on this query; C: the 11 accounts' effective settings).
=== TASK 2: FIX === y_catalog (A, empty-never-passes, B) and verify_rep7b's
real-account check (C).
=== TASK 3: PROVE === one-off: the catalog read returns the real PK / FK /
CHECK / UNIQUE counts for the 10 tables; lit() fails on an empty set; the
maker ≠ checker enforcement located and asserted; verify_rep7b's rewritten
check, run read-only against the live accounts, passes with the 11 approved
settings and FAILS if one account's effective authority were mis-applied
(prove with a fixture account, not a real one). Both scripts'
--self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under bulkapprove1.
