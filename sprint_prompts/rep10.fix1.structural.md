REP10 FIX 1 — make verify_rep10.py pass. 4 FAILs: three in rep10's own
proofs (completion after a stale-data confirmation, the gift received date,
the rebuy proposal) and one in the verify_rep5 regression (an UNPRICED buy
now gets a price — likely a real rep10 code defect in the security-data
layer). 5 tasks. Branch rep10-work (rep10 = 5baf607). For EVERY FAIL decide
code vs verify with evidence and say which; fix the code where the code is
wrong; never weaken an assertion.

Context: verify_rep10.py ran (run_sprint Step 3, 2026-11-02 ~21:55 ET):
TOTAL 142 PASS, 4 FAIL. Schema, tilts, completion maths, consolidation,
gifting lot choice / comparison maths / maker-checker / transfer letter,
backtest, isolation, gates, routes, node tests and npm build passed. Keep all
of it.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from that run's output):
- FAIL A "after the adviser confirms that as-of (rep7's task), completion
  applies again" → 200 False None. The line before it: "[INFO] confirm task:
  AgentActorRefused: human tasks are approvals of human-initiated work: the
  actor is not an admitted user". The verify completed rep7's stale-data
  confirmation task as an actor human_tasks refuses, so nothing was
  confirmed. Most likely a VERIFY defect (complete the task as an admitted
  fixture adviser the way verify_rep7 does). If the task is completed
  properly and completion still does not re-apply, that is a CODE defect in
  services/completion.py's staleness check (it must honour the confirmation
  for that as-of): fix it.
- FAIL B "gift date = the date the donee's account received the shares
  (entered; re-read)" → 200 422 None: the received-date call returned 422
  (request validation). The detail does not print the body — print it. Decide:
  the verify sent the wrong field / shape (verify defect) or the route's
  model is wrong against the spec (received date entered, it becomes the gift
  date; status sent → received) — code defect.
- FAIL C "rebuy: a rep5 proposal for the giving account (same security), the
  wash-sale note stated" → 409 409 []: both rebuy calls refused, no proposal.
  Print the 409 body. Likely causes: the gift is not in a status that allows
  a rebuy (e.g. it needs 'received', which FAIL B left unset — then C is a
  consequence of B; re-check after B), or rep5's one-open-proposal-per-
  account rule (the consolidation proof earlier opened a proposal on the
  same account — then the verify must close / use another account, or the
  code should say so plainly). Decide with evidence.
- FAIL D regression verify_rep5.py → 165 PASS, 1 FAIL: "an unpriced buy is
  by NOTIONAL (an amount), never a guessed quantity" — rep5's fixture
  security sec_B (deliberately unpriced) now gets price 100.00000000,
  price_source 'security_price', price_as_of 2026-11-01 (a Sunday), quantity
  19 instead of a notional. portfolio.securities_global_prices and
  portfolio.index_levels hold 0 rows NOW (after both teardowns), so the price
  came from somewhere else the layer reads: rep10 changed
  services/security_data/ (providers/csv_upload.py, providers/store.py,
  providers/vendor_stub.py, settings.py, types.py, uploads.py, writes.py —
  177 insertions) and rebalance_proposals.py (price lookup at ~509-522
  stores 'security_price' from the layer). Find the exact source row /
  provider that answered for sec_B. Candidates: rep10's change to history /
  "longest_history" leaking into layer.get; a synthetic-history scope that is
  not scoped to the fixture org (a platform-scope upload visible to every
  org); a provider default that returns a value when none exists; a
  leftover row in an upload / staging table rep10's teardown misses. This is
  a CODE defect if any real org could see a price that was never loaded for
  it — fix it so: a price exists only where a source holds one for that org
  (or the platform), synthetic test history is visible only to its fixture
  org, and rep5's unpriced security stays unpriced. Add a rep10 check that
  proves it (an org with no data for a security gets found = false through
  get, get_bulk and history).
- rep10's own 1d finding: security_data.history takes the FIRST ranked
  source with ANY point; rep10 added rep10_common.longest_history. Confirm
  that change did not alter layer.get's single-value behaviour for rep5's
  callers.
- WSL memory on the operator's laptop is limited: do NOT run verify_rep10.py,
  verify_rep5.py or npm run build. --self-checks-only and --cleanup-only MAY
  be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground. No network calls. Never print a secret or a full account number.
A one-off script may use your own is_test fixture org and must delete every
row it creates (by id) and confirm zero remain. SQL parameters inside CASE /
COALESCE get explicit casts. A failing check's detail prints the response
body. Commit on rep10-work with message "rep10 fix 1: <one line per fix, code
vs verify>".

=== TASK 1: DISCOVER === for each FAIL A–D: cause, code or verify, evidence
(for D: the exact table / provider / row that answered, and why it was
visible to rep5's org).
=== TASK 2: FIX THE CODE (any FAIL Task 1 proves is code) ===
=== TASK 3: FIX verify_rep10.py (task actor, received-date request, rebuy
preconditions, bodies in details, the new "no data → not found" check, and
teardown of any table Task 1 found it missed) — no assertion weakened ===
=== TASK 4: PROVE D WITHOUT A FULL RUN === a one-off script on your own
is_test org: seed an unpriced security, call layer.get / get_bulk / history
and rebalance_proposals' price lookup → found = false / 'none'; then load
synthetic history into a SECOND fixture org and confirm the first still sees
nothing; delete everything; zero remain. Record the output in
docs/PROJECT_STATUS.md.
=== TASK 5: UPDATE PROJECT STATUS === one UPDATE line under the rep10 entry.
