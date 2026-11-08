REP15 FIX 1 — verify_rep15.py stopped at its first real section: the model
upload CONFIRM answered 500, so the provider's model never existed and every
later section was skipped. Find and fix the 500, then run every skipped
section and fix whatever they find. 4 tasks. Branch rep15-work. Decide code
vs verify with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_rep15 run, 2026-11-08
~10:25 ET; the nested verify_rep13 regression was still running when this
was written — its result is in sprint_prompts/logs/rep15_run.out; include
it in Task 1):
- 113 checks PASSED before the failure (schema, RLS, catalog constraints,
  Task 1 findings: 0 live published platform-shelf entries; the real
  platform org has 0 chart-of-accounts rows).
- "[FAIL] the provider model v1 uploads (SA1) and is approved by a different
  Super Admin (SA2) — (201, 500, 404, 404) {"raw": "Internal Server Error"}
  {"detail": "model not found"} {"detail": "model not found"}" then
  "[FAIL] the provider model was approved — every later section skipped".
  verify_rep15.py ~lines 869–884: SA1 (fixture Super Admin in the FIXTURE
  platform org) POSTs /api/v1/models/uploads → 201; POST
  /api/v1/models/uploads/{id}/confirm → 500; submit / approve then 404
  because no model was created.
- The nested verify_rep13.py (own final TOTAL) went from 195 PASS / 0 FAIL
  (rep14's run, same day) to 127 PASS, 8 FAIL, starting with "[FAIL] a 130/30
  fixture uploads as a long/short model … — 500 {"raw": "Internal Server
  Error"}" — rep13 uploads in an ORDINARY fixture tenant org. So rep15's
  changes broke model upload CONFIRM for every org, not only the platform
  org: a REGRESSION in rep3's path. Its other 7 FAILs are expected to follow
  from it — prove that once fixed.
- So: a CODE defect is near certain (a 500 is never a valid answer) — in rep3's
  confirm path as rep15 changed it (the publish gate / "platform shelf" check
  rep15 added to rep3's library path?), or in confirm for an upload made in
  the platform org by a Super Admin. Could also be a fixture gap (e.g. the
  fixture platform org lacks a row confirm needs) — then confirm must still
  answer a clear 4xx, never 500.
- WSL memory is limited: do NOT run the full verify_rep15.py,
  verify_rep13.py or npm run build. --self-checks-only, --cleanup-only and
  one-off scripts MAY be run — and here you SHOULD run the verify's
  skipped sections one-off against the live DB (fixtures only), because the
  full run never reached them.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real or demo row; never open the real publishing gate. Commit on rep15-work
with message "rep15 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === reproduce the confirm 500 with the verify's own
fixtures and print the server traceback; the cause; whether a real org's
confirm (2nd Act, a normal org admin) is affected too (reproduce on a
fixture tenant org — never the real one). Also record the nested
verify_rep13 result from the run log.
=== TASK 2: FIX === the 500 (code, with evidence — and any other path that
raised it); if the fixture lacked something real orgs always have, fix the
fixture AND make confirm answer a clear 4xx naming what is missing.
=== TASK 3: PROVE === run every section of verify_rep15 one-off on fixtures
against the live DB (seed → provider model → gate → publish → catalog →
subscribe → use → isolation → fee / GL → disclosures → teardown) and report
each section's PASS / FAIL. Every FAIL they find: cause and fix (code or
verify) in this same sprint — this is the first time those sections run.
Then verify_rep15 --self-checks-only and --cleanup-only → 0 FAIL, zero
fixture rows, the real platform org's gate still OFF and unchanged.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under rep15 listing
every fix (code vs verify).
