TA MODEL — SPRINT 4: CALIBRATION UX + OBLIGATION LEDGER
INTEGRATION. 6 tasks + verification. Sprints 1-3 are complete and
merged: real backend, admin settings, commitment projection view.
This is the LAST sprint in the TA Model sequence.

CONFIRMED REAL FACTS FROM SPRINTS 1-3, DO NOT RE-DERIVE:
- POST /api/v1/modeling/ta/calibrate/{commitment_id} is real,
  proven in Sprint 1: accepts real annual call transactions,
  refuses a 3-quarter attempt (frequency-aware floor), accepts 3
  years, persists a bi-temporal override, upgrades bow_confidence
  to OBSERVED, correctly closes (not deletes) a superseded row on
  restatement.
- ta_model.py's own TAProjection carries two real, documented
  read-time primitives — contributions_between(period_start,
  period_end) and contributions_in_years(start_year, end_year) —
  whose docstrings state explicitly: "This is the read-time
  primitive the obligation ledger consumes" and describes a
  "36-month visibility horizon". FIND, in Task 1, whether a real
  "obligation ledger" consumer already exists ANYWHERE in this
  codebase, or whether these primitives have never been called by
  anything outside ta_model.py's own tests. Do not assume either
  answer — this is genuinely unconfirmed as of Sprint 3.
- view_portfolio is the real, reused permission for commitment/
  projection reads (Sprint 3) — confirm whether calibration
  (a WRITE to a persisted override) needs the same or a stricter
  permission; Sprint 1 itself did not specify which gate the real
  /calibrate endpoint uses — CONFIRM, do not assume it matches the
  read permission.
- ConfidenceTier (OBSERVED / PEER_CALIBRATED / STRATEGY_DEFAULT /
  ASSUMED) and TAParameters.weakest_confidence are real, live
  fields on every projection's parameters — confirm in Task 1
  whether Sprint 3's projection screen surfaces this AT ALL. If
  not, this sprint must add it — a member should never read a
  projection without knowing how much confidence its own inputs
  deserve.
- No charting library exists in apps/web (confirmed Sprint 3) —
  reuse the same inline-SVG approach already built, do not
  introduce a dependency.
- Decimal-as-string discipline applies identically here — reuse
  lib/decimalString.js's exact formatters verbatim.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts; light theme.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Does a real "obligation ledger" — anything consuming
      contributions_between/contributions_in_years, anything
      tracking near-term (e.g. 36-month) capital-call visibility
      for a member or org — exist anywhere in this codebase today?
      Grep thoroughly. Report honestly if the answer is "nothing
      calls these functions outside ta_model.py's own tests" —
      that is a real, valid finding, not a failure to find
      something.
  1b. Confirm the REAL, current permission gate on POST
      /calibrate/{commitment_id} — read the router directly, do
      not assume it matches view_portfolio.
  1c. Confirm whether Sprint 3's CommitmentProjectionScreen
      currently displays ANY confidence-tier information — if
      absent, this is a real, user-facing gap this sprint closes.
  1d. Confirm the REAL, exact input shape /calibrate expects for
      historical realized distributions (a list of period+amount
      pairs, per Sprint 1) — and confirm whether real distribution
      transaction data for a real commitment can be assembled from
      portfolio.transactions directly, or whether a mapping step
      is needed.

=== TASK 2: OBLIGATION LEDGER — build the REAL, minimal consumer,
per Task 1a's finding ===
If Task 1a finds no consumer exists: build the real, minimal one
the docstrings describe — a genuine 36-month forward capital-call
visibility view, using contributions_in_years (or
contributions_between at monthly granularity if the real need is
finer than yearly) called at READ TIME against a commitment's
live projection. Do NOT persist obligation rows — this must follow
the exact same "computed at read time, never written as an
obligation row" rule already established for SPV-derived capital
calls elsewhere in this platform (confirm that precedent directly
if referenced anywhere, and match it). If Task 1a finds a real
consumer already exists: wire the TA projection into IT rather
than building a second, competing ledger view.

=== TASK 3: CALIBRATION UX ===
A real "Calibrate" panel/action, most likely added to Sprint 3's
CommitmentProjectionScreen (confirm this is the right host, per
Task 1c) — lets a user with the real permission (Task 1b) submit
a commitment's actual realized distribution history and see the
real, resulting fitted bow factor and updated confidence tier
BEFORE committing it (a real preview-then-confirm flow, not a
blind POST). Real, server-sourced validation messages only (per
the established pattern from every prior UX sprint this project) —
the frequency-aware minimum-periods floor's refusal message
surfaces verbatim, not re-derived client-side.

=== TASK 4: CONFIDENCE TIER DISPLAY ===
Add real, honest confidence-tier display to the projection screen
(per Task 1c's finding) — showing weakest_confidence in plain
language (not just a color chip), so a member reading a STRATEGY_
DEFAULT-tier projection understands it rests on generic assumptions,
not this fund's own realized behavior. This should update visibly
once a real calibration is confirmed (Task 3) — the same screen,
same load, reflecting the upgraded tier without a manual refresh
being the only way to see it.

=== TASK 5: REAL PROOF ===
  - A real commitment's real historical distributions, submitted
    through the UI, produce a real, persisted calibration —
    confirmed via a fresh GET showing the upgraded OBSERVED tier
    and the new bow_factor.
  - The frequency-aware floor's real refusal (a call with too few
    quarters) is surfaced in the UI verbatim from the API's own
    error, not re-implemented.
  - The confidence tier display genuinely reflects
    weakest_confidence's real, computed value — proven with a
    fixture spanning at least two different tiers (e.g. one
    strategy default, one post-calibration), showing genuinely
    different real output, not a static label.
  - The obligation-ledger view (or its real, existing counterpart
    if Task 1a found one) produces genuinely different output for
    two commitments with different real committed-capital/call
    schedules — proving it's driven by real, live data, not
    fixture-shaped coincidence.
  - The ledger view computes at READ TIME — confirmed by a
    real row-count check showing nothing new persisted.
  - Permission: the real gate from Task 1b is proven both ways
    (refuses without it, admits with it) on the calibrate action
    specifically — separate from the read-only projection gate
    already proven in Sprint 3.
  - Cross-org isolation on both the calibration action and the
    obligation ledger view.
  - npm run build exits 0.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and the TA brief's phasing note: ALL
FOUR TA Model sprints complete. Note explicitly whether Task 1a
found and integrated with a pre-existing obligation ledger, or
built the first real one — this matters for anyone extending it
later.

=== VERIFICATION: apps/api/scripts/verify_tamodel4.py ===
Pass/fail only.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] A real calibration submitted via the UI persists and is
      confirmed via a fresh, independent GET
  [Y] The frequency-aware floor's refusal surfaces verbatim in
      the UI
  [Y] Confidence tier display genuinely reflects real, differing
      computed values across at least two tiers
  [Y] The obligation-ledger view produces genuinely different
      real output for two differently-shaped commitments
  [Y] The ledger view is confirmed read-time-only via row-count
  [Y] The real calibration permission gate is proven both ways,
      independent of the projection-read gate
  [Y] Cross-org isolation on both new surfaces
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
