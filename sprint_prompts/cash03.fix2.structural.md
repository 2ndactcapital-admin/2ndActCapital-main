CASH03 FIX 2 — FINISH cash03 fix 1. Fix 1's leg was KILLED at the runner's
90-minute cap (2026-11-09 06:23 UTC) while still working; nothing was
committed and the verify never ran. Its work is in the working tree,
uncommitted. Review it, keep what is right, finish the remaining sections,
prove, commit. 4 tasks. Branch cash03-work. Never weaken an assertion.
Work FAST: do not redo fix 1's discovery.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- The original FAILs (verify_cash03 run, 2026-11-09 ~04:15 UTC, TOTAL 130
  PASS, 7 FAIL): the seed's security-master check expected 4 N-MFP share
  classes and got ONE; six fixture sections (providers, security master,
  ladder, after-tax, plan (db), app + visibility) were gated off.
- Fix 1's uncommitted diff (git diff --stat: 7 files, +105 / −31) — three
  CODE fixes and verify changes:
  1. services/security_master_load.py: new batch_key(r) — Matcher's
     within-batch dedup key now includes the EXTRA_ID_UNIQUE ids (SEC class
     id); before, every id-less N-MFP class (no CUSIP / FIGI / ticker)
     collapsed onto one key and only the first class was written. CODE.
     This is SHARED Matcher code: it must not change any existing
     securities feed's dedup (a record with a CUSIP / FIGI / ticker keys as
     before when it has no unique extra id) — prove it (Task 3).
  2. routers/cash.py plan/regenerate: scope from
     family_visibility.scope_for_request (the VISIBILITY1 staff scope) instead
     of authz.member_entity_scope, which on a POST sent advisors down the
     member path (empty scope → 404 on their own family). CODE.
  3. services/cash_ladder.py + cash_instruments.py: curve_why threaded
     through (a missing FRED curve gets its "needs <series>" text on every
     curve-based yield). CODE.
  4. scripts/verify_cash03.py (+71 / −?) and docs/AUTHZ1_ROUTES.* updated;
     scripts/_cash03_fix1_discover.py (untracked) is fix 1's scratch.
- Fix 1 was last editing verify_cash03.py at 06:20 UTC — the section it was
  on when killed is unknown: start by running --self-checks-only, then each
  of the six sections one-off.
- Time budget: the runner kills a leg at 90 minutes. Give EVERY one-off run
  `timeout 600`; if a section takes > 10 min, find why (a slow query / a loop
  over the real master) instead of re-running it.
- Nightly refresh writes real rows at ~07:00 UTC (02:00 ET): a "real rows
  unchanged (count + checksum)" check run across it can fail on REAL data,
  not on the code — if that is the only failure in a one-off, re-run it,
  and say so.
- WSL memory is limited: do NOT run the full verify_cash03.py,
  verify_cash02.py, verify_secmaster*.py or npm run build. --self-checks-only,
  --cleanup-only and one-off sections MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real row; providers stay switched off. Commit on cash03-work with message
"cash03 fix 2: <one line per fix, code vs verify>" (it carries fix 1's
changes too — name them).

=== TASK 1: REVIEW === read the diff; keep, correct or revert each hunk
(state which and why); delete scripts/_cash03_fix1_discover.py.
=== TASK 2: FINISH === run verify_cash03 --self-checks-only; then each of the
six sections one-off (timeout 600) → fix whatever fails (code or verify,
named). Add to verify_cash03.py a nested regression of verify_secmaster2.py
(the Matcher's owner; flat, after teardown, VERIFY_NESTED=1, 2700 s) next to
verify_cash02.py, because batch_key changed shared Matcher code.
=== TASK 3: PROVE === the seed's security-master check PASSES (5 Treasury
issues, 4 N-MFP classes as 4 securities, yields as expected); each of the six
sections → PASS (list each with PASS count); a one-off unit check that
batch_key gives the SAME key as before for records with no unique extra id
(sample the real feeds' record shapes, read-only); --cleanup-only → zero
fixture rows; verify_cash03 --self-checks-only → 0 FAIL; real security
master and index levels unchanged (count + checksum).
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under cash03 listing
every fix from fix 1 and fix 2 (code vs verify).
