SECMASTER1 FIX 1 — make verify_secmaster1.py pass. 7 FAILs with 3 root
causes: (A) a new 13F CUSIP is not mapped through OpenFIGI, (B) the forced
step failure never produces a held run, and five checks cascade from it,
(C) the platform-org guard is never reached because the permission check
refuses first. 4 tasks. Branch secmaster1-work (secmaster1 = 8a61922, on
perms1 ff54ef0). Decide code vs verify with evidence; never weaken an
assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from verify_secmaster1's run,
2026-11-04 ~14:00 ET: TOTAL 146 PASS, 7 FAIL; the regression
verify_marketdata1.py passed (114 PASS, 0 FAIL); adapters, the matcher, ISIN,
the 13F load, scheduled fire of the jobs, Run now, job history, real-row
checksums and routes all PASSED — keep them):
- FAIL A: "a new CUSIP mapped through OpenFIGI → a new security with ticker +
  FIGI + CUSIP" — row {'cusip': '9ZZSM00N1', 'map_status': 'not_mapped',
  'map_note': 'not mapped — no match', 'global_security_id': None}. The
  neighbouring checks PASSED: the already-in-master CUSIP maps, and an
  unmapped CUSIP keeps "not mapped — OpenFIGI found no identifier". So the
  OpenFIGI stub's answer for 9ZZSM00N1 is not reaching the mapper. Find out
  which: the stub keyed differently from the request (idType, value
  normalisation, job order — OpenFIGI answers by POSITION in the request
  array, not by key), the stub's signature no longer matches the adapter's
  call, or the mapper discards a valid answer (e.g. no exchange / security
  type it accepts). Print the request jobs and the stub response in the
  detail. If the mapper misreads positions or discards a valid answer, that
  is a CODE defect — fix it (real OpenFIGI answers positionally).
- FAIL B (root of 5): "a failing step HOLDS the run with the error — None",
  then "held-run alert goes to the Super Admin — []", "org admin cannot close
  a platform run (403) — 422", "Super Admin closes the held run — 422 …
  run_id 'None'", "…and the schedule fires again — fired=0 skipped=1". The
  forced failure produced no run id, so every later check got None. Find why:
  the failure injection (stub signature, wrong action key, raised before the
  run row exists, caught and swallowed), or the engine not holding a
  platform run. Note "a held run blocks the next fire" PASSED with skipped=1
  — confirm WHICH run it was skipping (a leftover from another section would
  make that a false pass; it must be the held run from this section). Fix
  the cause; the five checks then run against a real held run id. If the
  engine does not hold a failing platform-job step, that is a CODE defect.
- FAIL C: "the same action in a client org's run is refused (held: 'runs
  only in the platform org')" — the run WAS held, but with "requires
  permission 'run_platform_jobs' … does not hold it", because the verify
  started it as a member who lacks the permission. So the platform-org guard
  itself is untested. VERIFY defect, tighten: start the client-org run as a
  user who HOLDS run_platform_jobs (a Super Admin fixture acting in the
  client fixture org) so only the org guard can refuse — expect held with
  "runs only in the platform org"; keep the existing case as a second check
  (no permission → refused with the permission message). If the org guard
  does not exist or does not fire, that is a CODE defect — add it in the
  action handler, before any write.
- WSL memory is limited: do NOT run verify_secmaster1.py, verify_marketdata1.py
  or npm run build; --self-checks-only and one-off scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network to any data source. Never print a secret. A one-off
script uses only is_test fixture rows and removes them (workflow runs, steps,
triggers, todos included). Commit on secmaster1-work with message
"secmaster1 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === A–C: cause, code or verify, evidence (A: request
jobs vs stub response; B: what the injection did and which run the
blocks-next-fire check skipped; C: whether the org guard exists and its order).
=== TASK 2: FIX === code where defective, and the verify.
=== TASK 3: PROVE === one-off: (A) a fixture CUSIP mapped from a positional
OpenFIGI answer → security with ticker + FIGI + CUSIP; (B) a forced step
failure → held run with the error, Super Admin alert, org admin 403 on
close, Super Admin closes it, schedule fires again; (C) Super Admin in a
client org → held "runs only in the platform org". Teardown → zero fixture
rows. verify_secmaster1 --self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under secmaster1.
