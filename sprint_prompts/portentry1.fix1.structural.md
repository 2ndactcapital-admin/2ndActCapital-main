PORTENTRY1 FIX 1 — make verify_portentry1.py pass. 18 FAILs from 6 root
causes: (A) approving the manual-positions draft returns 500 — 8 checks
cascade; (B) direct-asset / private-investment valuations use methods the
valuations CHECK refuses — 5 checks cascade; (C) a cross-org record read
returns 200 + empty instead of not-found; (D) the support-staff tax-id
refusal is tested with an invalid body; (E) teardown cannot delete
held-away imports; (F) the RLS check on asset_relinks found no rows (cascade
of A). 4 tasks. Branch portentry1-work (HEAD 2abf2b2). Decide code vs verify
with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_portentry1 run, 2026-11-04
~22:15 ET: TOTAL 106 PASS, 18 FAIL; regression verify_crmdetail1.py 116 PASS,
0 FAIL; _verify_self fast-forward fix PASSED (perms1 / secmaster1 /
crmdetail1 self-checks 23/8/23, 0 FAIL); vocabularies, gates, edit_crm_contact
(except D), import screen and CSV, demo protection all PASSED — keep them):
- A (root of 8): "a draft of all seven holding types: nothing rejected; a
  second person approves" → the import row is created (rows_read 7,
  rows_accepted 7, rows_rejected 0) but the APPROVE call returns
  500 {"raw": "Internal Server Error"}. Cascade: round-trip as live
  positions, org-scoped manual asset, muni value = qty × price, cash amount,
  relink (2 checks), RLS on portfolio.asset_relinks (0 / 0). CODE defect:
  reproduce the approve in a one-off script, read the server exception, fix
  it (likely a new row type — muni / cash / option / digital / unmapped fund —
  that the existing approval path does not handle: a NULL
  global_security_id, a missing price for cash, a column name). Every one of
  the seven types must approve into a live position.
- B (root of 5): "valuation_method='estimate' is not one of
  ['amortized_cost', 'appraisal', 'mark_to_model', 'market_price', 'nav']"
  and the same for 'statement'. The DB vocabulary is authoritative — do NOT
  widen the CHECK. CODE defect: the direct-asset and private-investment
  forms and routes must offer the existing vocabulary (labels from the API:
  Appraisal, Estimate (mark to model) → mark_to_model, Statement / NAV →
  nav, Market price, Amortized cost); the verify uses those keys. Cascade:
  new valuation = new row, record lists latest value + history, private
  investment commitment / called / distributed / unfunded, later call
  re-derives unfunded, TA projection reads the commitment, the trust's gross
  includes its LLC's fund (look-through), Recalculate total.
- C: "org A's admin on org B's record → 404" returned 200 with
  {"rows": [] …}. CODE defect: every new record-scoped route (direct assets,
  private investments, manual assets, net worth, recalculate) first
  resolves the record in the caller's org and returns the same not-found as
  a missing id when it is not there — no 200 with an empty list.
- D: "support staff refused: tax id — 422 … Field required: body.value".
  The verify sent an invalid body, so validation refused it, not the gate —
  a refusal that proves nothing. VERIFY defect: send a VALID tax-id body
  (the route's real schema) so only the permission gate can refuse; expect
  403 (and prove the identical valid body is admitted for the org admin).
- E: "teardown raised — InsufficientPrivilegeError: held-away imports are
  never deleted". VERIFY defect: use the documented privileged path
  verify_rep7 / verify_rep7b use for held_away_imports fixture rows (by
  fixture id); never weaken the trigger; zero fixture rows after teardown.
- WSL memory is limited: do NOT run verify_portentry1.py,
  verify_crmdetail1.py or npm run build; --self-checks-only and one-off
  scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret or a full account number. A
one-off script uses only is_test fixture rows and removes them through the
documented privileged paths. Commit on portentry1-work with message
"portentry1 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === A–E: cause, code or verify, evidence (A: the server
exception text and the row type that triggers it).
=== TASK 2: FIX === code (A, B, C) and the verify (D, E; B's keys).
=== TASK 3: PROVE === one-off: (A) a draft with all seven types approves into
seven live org-scoped positions (muni value exact, cash amount, unmapped fund
relinks and is reported, RLS on asset_relinks both orgs); (B) a direct asset
with two valuations (history), a private investment with commitment /
called / distributed / unfunded, a later call re-derives unfunded, the TA
projection reads it, the owner trust's gross includes the fund through its
LLC without a manual rollup; (C) org A's admin on org B's record → the same
not-found as a missing id on every new record route; (D) support staff
refused 403 on a valid tax-id body, org admin admitted on the same body;
(E) teardown leaves zero fixture rows incl. held_away_imports.
verify_portentry1 --self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under portentry1.
