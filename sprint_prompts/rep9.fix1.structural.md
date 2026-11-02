REP9 FIX 1 — make verify_rep9.py pass. 8 FAILs: some are verify-script
defects, at least one may be a code defect; three are verify_rep7b checks
made stale by later sprints and by the cleaned-up data. 5 tasks. Branch
rep9-work (rep9 = 67a2e4e). For EVERY FAIL decide code vs verify with
evidence and say which; fix the code where the code is wrong; never weaken
an assertion.

Context: verify_rep9.py ran (run_sprint Step 3, 2026-10-29): TOTAL 150
PASS, 8 FAIL. Schema, eligibility, suggested groupings, allocation fixed at
approval, average price, partials, corrections, DVP settlement, isolation,
gates, routes, node tests and npm build passed. Keep all of it.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from that run's output):
- FAIL A "route fix refused with the reason (no FIX router connected)" →
  409 {'detail': 'route fix refused: no FIX router is contracted or connected
  (rep9 builds the interface only — no client code, no network). Use the
  manual route: pri…'}. The refusal itself looks right; read the check's
  expected code / wording and decide (the spec's word is "not connected" —
  if the check wants it, put it in the message).
- FAIL B "a sell block of the same security in the same window is flagged
  (cross-trade risk, the buy block named) and needs a reason; then
  cancelled" → 409 "not every instruction can join this block: ******0002:
  3.00000000 is below the policy's minimum allocation of 5.00000000". The
  minimum-allocation rule fired before the cross-trade flag could be
  tested: the FIXTURE's sell quantity (3) is below the policy minimum (5).
  Give the sell fixture a quantity at or above the minimum (or test the
  cross flag on a block that is otherwise valid). Also confirm the ORDER of
  checks is sensible (cross-trade flag reported alongside any other
  refusal, not hidden by it) — if not, fix the code.
- FAIL C "the PDF itself (inflated): masked references present, no full
  number, no member name" → 0 and FAIL D "the single-instruction sheet:
  masked account, no full number, no member name" → 0: the verify found NO
  text in the PDF at all — the sheet rows checked through the API passed
  (all masked, no member name). The verify's raw inflate of content streams
  is the likely defect (reportlab text is in compressed streams with font
  encoding); extract text with pypdf (already a dependency) and keep the
  same assertions. If the PDF truly has no text layer, that is a code
  defect: fix it.
- FAIL E "manual is always available; fix is refused with the reason" — a
  roll-up of A, C, D. Re-check after them.
- FAIL F "no network call and no custodian adapter reached by any block
  path" → network [] but AltruistTradeAdapter reached for accounts
  …9301, …9302, …9302. Determine WHERE: during fixture setup (creating the
  account instructions through rebalance_proposals.execute →
  custody/trading.route_trade_list, which picks an adapter before falling
  back to manual) or inside a block path (build / approve / sent / fills /
  allocate / settle). If setup: reset the spy after setup so the check
  covers block paths only. If a block path: that is a CODE defect — no
  block path may touch a custodian adapter; fix it.
- FAIL G "execute-by raised — TypeError: 'NoneType' object is not iterable"
  at verify_rep9.py:1397 (`dict(b)` with b None): the block row read after
  the execute-by sweep returned nothing. Find why (the sweep deletes /
  archives the block row instead of moving it to a terminal status; the
  query filters system_to / status wrongly; RLS context missing on the
  read). The spec: an unsent block past its execute-by is logged with no
  actor and its allocations cancelled — the block row must remain readable.
- FAIL H regression verify_rep7b.py → 126 PASS, 3 FAIL, all stale checks,
  not rep9 behaviour:
    (1) "the implemented matrix equals the decided one in every authority ×
        custody cell (paths and executors)" — rep8 added the 'options' path
        (managed only) after rep7b; update verify_rep7b's DECIDED matrix to
        include 'options' exactly where services/authority.py allows it
        (managed × integrated / other_custodian), nowhere else — show the
        diff of the two matrices.
    (2) "the 0 existing account(s), loaded through the real loader: every
        path allowed / refused exactly as before …" → [] and
    (3) "the existing held-away DRAFT instruction(s): read through the real
        detail — approval refused …" → [] []: lotmethod's cleanup removed
        the leftover REP2 test orgs that held the only accounts and the only
        held-away draft instruction. With zero such rows the checks must
        report a [FIND] ("none exist today") and pass, still asserting the
        rule for every row when rows exist.
  Fix verify_rep7b.py ONLY in those three checks; say which lines.
- WSL memory on the operator's laptop is limited: do NOT run verify_rep9.py,
  verify_rep7b.py or npm run build.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground. No network calls. Never print a secret or a full account number.
A one-off script may use your own is_test fixture org and must delete every
row it creates (by id) and confirm zero remain. SQL parameters inside CASE /
COALESCE get explicit casts. Commit on rep9-work with message "rep9 fix 1:
<one line per fix, code vs verify>".

=== TASK 1: DISCOVER === for each FAIL A–H: cause, code or verify, evidence.
=== TASK 2: FIX THE CODE (any FAIL Task 1 proves is code) ===
=== TASK 3: FIX verify_rep9.py (fixtures, PDF text via pypdf, adapter spy
scope, the execute-by read) — no assertion weakened ===
=== TASK 4: FIX verify_rep7b.py's three stale checks (H) ===
=== TASK 5: UPDATE PROJECT STATUS === one UPDATE line under the rep9 entry.
