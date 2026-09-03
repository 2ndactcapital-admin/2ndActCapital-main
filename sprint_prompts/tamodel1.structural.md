TA MODEL — SPRINT 1: SUBSTRATE. 6 tasks + verification. Full brief:
docs/TA_MODEL_INTEGRATION_BRIEF.md (commit it in this sprint if not
already present). Three modules — ta_model.py, ta_config.py,
ta_calibrate.py — already built and verified standalone (93/93).
This sprint lands them in the real backend; it does not modify
their internals except where §8's open items require it.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

CONFIRMED, NON-NEGOTIABLE, FROM THE BRIEF ITSELF — DO NOT SOFTEN:
- ta_model.py stays a pure function: no DB, no config import, no
  I/O. Copy it in as-is.
- Decimal throughout, floats rejected at every boundary, jsonb
  decimals stored as strings.
- Projected cash flows are NEVER persisted — computed at read time
  only, same rule as SPV-derived capital calls. Only PARAMETERS
  (when overridden) and CALIBRATION RESULTS persist.
- org_id server-derived, never from request body.
- Admin routes under /api/v1/admin/ specifically (brief notes a
  prior real 404 from assuming otherwise elsewhere in this
  project).

=== TASK 1: DISCOVER — the three real open items from the brief's
own §8, plus the real schema ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Does a ta_strategy (or equivalent) field already exist
      anywhere on the commitments/alternatives schema? Check the
      real asset-class taxonomy for a field that already maps to
      the 8 seeded TA strategy keys (buyout, growth_equity,
      venture_capital, real_estate, real_assets, private_credit,
      fund_of_funds, secondaries) before assuming a new column is
      needed.
  1b. Confirm the real, current shape of Chancery-sourced
      commitment data — does it actually carry committed_capital,
      paid_in_to_date, and nav_to_date in a form these three
      modules can consume directly, or does a mapping layer need
      writing?
  1c. Confirm whether org_settings already has any 'modeling.*'
      category keys deployed, or this is genuinely greenfield.

=== TASK 2: SCHEMA DECISION — persisted TA parameters ===
Per the brief's own stated lean (§8): a SEPARATE table keyed by
commitment_id, not new columns on commitments — because bitemporal
restatement of assumptions ("what did we assume in Q2 and why did
the projection change") is a real, anticipated question. Build
this table with proper bi-temporal columns (valid_from/valid_to)
per this project's standing Rule 3. Also add the 4 seed
org_settings rows (via seed_rows()) for the default org, confirmed
live via direct query, not assumed from the function's return
value alone.

=== TASK 3: THE FREQUENCY-AWARE CALIBRATION FLOOR (brief's own
flagged §8 item) ===
Raise ta_calibrate.py's minimum-realized-periods floor from a flat
3, and make it frequency-aware: 3 periods of ANNUAL data is a
reasonable floor; 3 QUARTERS is a materially weaker basis and
should require more. Decide and implement a real, reasoned
minimum per periods_per_year (e.g. genuinely more quarters
required than years) — do not ship the flat constant as-is, per
the brief's explicit warning.

=== TASK 4: BACKEND — the five endpoints, brief's suggested shape
===
  GET  /api/v1/modeling/ta/defaults
  PUT  /api/v1/admin/modeling/ta/defaults
  GET  /api/v1/modeling/ta/projection/{commitment_id}
  POST /api/v1/modeling/ta/projection/preview
  POST /api/v1/modeling/ta/calibrate/{commitment_id}
Settings fetched ONCE per request (never cached across requests —
brief cites the real /admin/platform theme-caching bug as the
precedent for why this matters), passed into params_for_strategy.
Config-write endpoint gated with the same real permission pattern
as other org_settings writes (confirm and reuse the real existing
pattern, do not invent a new one).

=== TASK 5: REAL PROOF ===
  - A real commitment's real Chancery-sourced data produces a real
    projection end-to-end through the actual API, not a fixture
    bypassing the endpoint.
  - Projected cash flows are confirmed NOT persisted anywhere —
    prove by re-running the same projection twice and confirming
    no growing row count anywhere resembling cached results.
  - An overridden parameter for one commitment persists correctly
    in the new bi-temporal table and is retrievable; a later
    restatement closes the old row rather than mutating it.
  - The new frequency-aware calibration floor genuinely refuses a
    3-quarter calibration attempt while still accepting a
    3-year one — proven with real calls to both.
  - org_settings seed rows are confirmed live via direct query.
  - Cross-org isolation on both the defaults and the projection
    endpoints.
  - A view/write permission split on the admin config-write
    endpoint, matching the real existing org_settings pattern.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md: TA Model Sprint 1 complete, Sprint
2 (admin UX) and Sprint 3 (commitment projection UX) next and
independent of each other per the brief's own sequencing note.

=== VERIFICATION: apps/api/scripts/verify_tamodel1.py ===
Extend, do not replace, the existing 93-assertion standalone
verify — add the API-layer proof from Task 5 on top of it.

Assertions:
  [Y] Report Task 1's three findings explicitly
  [Y] All 93 pre-existing standalone assertions still pass
      unmodified
  [Y] A real end-to-end projection succeeds through the real API
  [Y] Projected cash flows are confirmed never persisted
  [Y] Parameter overrides persist bi-temporally, correctly
      restated on a later change
  [Y] The frequency-aware calibration floor is proven both ways
  [Y] org_settings seed rows are live, confirmed by direct query
  [Y] Cross-org isolation on both endpoints
  [Y] Config-write permission split proven
  [Y] Teardown: zero leftover rows
