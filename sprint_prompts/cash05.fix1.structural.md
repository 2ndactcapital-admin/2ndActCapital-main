CASH05 FIX 1 — make verify_cash05.py pass. 3 FAILs (as of the verify's
regression step; FIRST read the end of sprint_prompts/logs/cash05_run.out —
if the nested verify_cash04.py or anything after line 252 also failed, add
it here with the same rule). Two "every link resolves" checks fail although
every listed call answered 200, and one section aborted on a non-bool
check(). 4 tasks. Branch cash05-work. Decide code vs verify with evidence;
never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_cash05 run, 2026-11-09
~17:05–17:25 UTC; the schema / seed, balance series, holdings, activity
mapping, earnings, screens and the sections before 220 PASSED — keep them):
- FAIL 1 (line 220) "EVERY drawer link resolves: GET its api path → 200
  with the same record (the document → a signed URL for its own storage
  key)". The detail lists ('instruction', /api/v1/execution/instructions/…
  cac50091, 200, {"instruction": {"id": "…cac50091", …}}) and ('proposal',
  /api/v1/proposals/…cac50090, 200, …) — both 200 and the instruction body
  carries the right id. So the failing part is either (a) a drawer entry
  NOT in the printed (truncated) detail — e.g. a document / transaction /
  account link — or (b) the "same record" comparison (wrong key path:
  e.g. reading body["id"] when the record is nested under "instruction" /
  "proposal"; or a uuid-vs-text compare). Print the FULL per-link result
  (type, path, status, expected id, found id, pass/fail) — the detail
  truncates; make the detail list only the failing links — then fix the
  side that is wrong (a link that does not resolve or resolves to another
  record is a CODE defect in the drawer).
- FAIL 2 (line 245) "open / download goes through the existing GET
  /documents/{id}/download (200, a signed URL for that document's own key;
  no new storage)" — both downloads 200 with
  {"url": "https://signed.invalid/cash05verify/confirm-t2.pdf?expires=3600"}.
  Same diagnosis: print, per document, its storage key from the documents
  row and the key embedded in the signed URL; if they differ for one
  document → CODE (the list links the wrong document) or a fixture whose
  key is not what the verify assumes → VERIFY. Fix the side that is wrong.
- FAIL 3 (line 252) "member + visibility + isolation raised —
  AssertionError: check() got 'Estimates use the Treasury curve; actual
  yields differ; have counsel review.' (str) for 'the member's overview
  carries cash.coverage_estimate / cash.yield_estimate / cash.tax_estimate
  and holdings cash.yield_estimate from the disclosure library'". VERIFY
  defect: the condition is an and-chain ending in a string — wrap in bool()
  and assert explicitly that EACH of the three keys is present with its
  library text (not just the last). Grep the whole verify for other
  check(...) conditions that can be non-bool and fix them. Every check after
  this point in that section never ran (member view, staff visibility, org
  isolation, no full account number): run the section one-off after the fix
  and fix whatever it finds (code or verify), this sprint.
- WSL memory is limited: do NOT run the full verify_cash05.py,
  verify_cash04.py or npm run build. --self-checks-only, --cleanup-only and
  one-off sections MAY be run (`timeout 600` each; the runner kills a leg at
  90 minutes).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret or a full account number.
Fixtures only; never write a real row. Commit on cash05-work with message
"cash05 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === full per-link and per-document results for 1 and 2;
the non-bool conditions for 3; any extra FAILs at the end of the run log.
=== TASK 2: FIX === per the evidence.
=== TASK 3: PROVE === the drawer section, the documents section and the
member + visibility + isolation section one-off → every check runs and
passes (list each with PASS count); verify_cash05 --self-checks-only →
0 FAIL; --cleanup-only → zero fixture rows.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under cash05 listing
every fix (code vs verify).
