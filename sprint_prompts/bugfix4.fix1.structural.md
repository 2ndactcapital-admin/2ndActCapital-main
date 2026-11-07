BUGFIX4 FIX 1 — make verify_bugfix4.py pass. 3 FAILs, all in the new
private-investment / household-total code paths. 4 tasks. Branch
bugfix4-work. Decide code vs verify with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_bugfix4 run, 2026-11-06
~18:35 ET: TOTAL 167 PASS, 3 FAIL; regression verify_portentry1.py 166 PASS,
0 FAIL (own final TOTAL); one-draft-per-account, the repair script, the
roll-up valuing unpriced lines, the job switch, the entry form, the picker,
roles, identity and display fixes all PASSED — keep them):
- FAIL 1 "household total (less the off-account cash) == the X-ray total ==
  independent SQL over the accounts — gross 1435000.00 xray 45000.00 sql
  45000": gross − 1,400,000 (the cash direct asset) = 35,000, but the
  accounts hold 45,000 (X-ray and SQL agree). The 10,000 missing is the
  verify's deliberately UNCLASSIFIED master fund line ("a master FUND stays
  unclassified … counted, not guessed"): the roll-up REPORTS it as skipped
  but leaves its value out of gross. DECISION (Joe's intent: net worth must
  never silently omit a valued holding): CODE — a valued position with no
  taxonomy key is included in gross in an "Unclassified" bucket (shown and
  reported, never dropped); only a position with NO value is skipped. Then
  household total − off-account direct assets == X-ray == SQL exactly.
- FAIL 2 "the projection the link opens answers 200 — 422": the commitment
  row's projection_path is /portfolio/commitments/{commitment_id} and the
  verify calls GET /api/v1/modeling/ta/projection/{commitment_id} → 422.
  Find why (missing required query parameter, the fund lacks TA calibration
  inputs, the id is not the one the route expects). If the route needs
  inputs a new commitment cannot have, the page must show a clear
  "projection needs <x>" state and the API return that reason — the link
  must never lead to an error. Code or verify, with evidence.
- FAIL 3 "a commitment-date edit keeps the commitment id (flows point at it)
  and archives the prior version": the edit created a NEW row (id 78d7…
  archived, id a691… live). Capital-call / distribution flows reference the
  commitment id, so a new id orphans them. CODE defect: a correction is a
  system-axis version that KEEPS the id (Rule 3 — the same pattern as
  portfolio_securities.update_asset: archive the prior version, keep the
  id), and flows still resolve after the edit.
- WSL memory is limited: do NOT run verify_bugfix4.py, verify_portentry1.py
  or npm run build; --self-checks-only and one-off scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
demo-olivo or real row. Commit on bugfix4-work with message
"bugfix4 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === 1–3: cause and evidence (2: the exact 422 detail).
=== TASK 2: FIX === code (1, 3; 2 per evidence) and the verify where the
evidence says so.
=== TASK 3: PROVE === one-off on fixtures: an unclassified valued line is in
gross under "Unclassified" and the household total − off-account assets ==
X-ray == SQL; the commitment link opens a 200 projection or a clear
"needs <x>" state (never an error); a commitment-date edit keeps the id,
archives the prior version, and an existing capital call still resolves to
it. verify_bugfix4 --self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under bugfix4.
