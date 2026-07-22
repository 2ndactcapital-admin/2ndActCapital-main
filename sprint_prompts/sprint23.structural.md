SPRINT 23 — Investment/Class restructure (light scope — deals
already serves as the Investment parent; no new table needed).

CONTEXT: "Series" is reserved for the Delaware legal
compartment. The economic subdivision within one deal (differing
fee/carry/close-date on the same underlying investment) is
called "Class" — e.g. Class A/B/C. deals is the Investment
parent (already has asset_super_class/asset_class/
asset_sub_category, sponsor_entity_id, bitemporal columns).
spvs is the Class child, already carrying carry_pct/
mgmt_fee_pct/close_date per row. Part 1 SQL already added
spvs.class_label (nullable TEXT) + the unique index
spvs_deal_class_label_uniq on (deal_id, class_label) +
confirmed master_entity_id already has its FK to entities(id).
Read docs/schema_snapshot.sql FIRST to confirm these landed
exactly as expected before writing any code — do not trust this
prompt's description of the schema over the live snapshot.

STANDING RULES: org_id never from request body (always derived
from spvs.org_id / deals.org_id); Decimal for all money; no
interactive prompts anywhere; light theme (whites/creams,
Navy #1B2B4B / Gold #C5A880) throughout any UI touched.

=== TASK 1: Investment-level roll-up ===
Locate the EXISTING per-SPV Committed/Called/Distributed/Fees/
Net calculation (used on the SPV Ledger / Transactions tab —
built in Sprint 14/22). Do not reinvent this math. Extend it
into an investment-level aggregation that:
  - Groups by deal_id across ALL spvs sharing that deal_id
    (i.e. all classes of one investment)
  - Sums Committed/Called/Distributed/Fees/Net using the exact
    same underlying computation as the per-SPV version, just
    aggregated
  - Also returns a per-class breakdown (each SPV's own row)
    alongside the investment-level totals — both views are
    needed, not just the aggregate
Expose this as a new endpoint (e.g. GET /deals/{deal_id}/rollup)
or extend the existing deal-detail response — follow whatever
pattern the codebase already uses for similar aggregate
endpoints.

=== TASK 2: SPV creation flow — class labeling ===
On the "New SPV" / "Add SPV" flow tied to a deal:
  - If the deal has ZERO existing spvs, class_label is OPTIONAL
    (single-class deal, no label needed).
  - If the deal already has ONE OR MORE spvs, REQUIRE a
    class_label on the new one, and suggest the next letter
    (A if none exist yet with a label, B if 'A' is taken, etc.)
    as a pre-filled default the user can override.
  - Enforce this in the API layer, not just the frontend —
    reject (400, clear message) an attempt to create a second
    SPV under a deal without a class_label.

=== TASK 3: Display "Class {label}" ===
On the SPV detail page: when class_label is set, show it
alongside the SPV/deal name (e.g. "My first SPV — Class A").
When null, show nothing extra (current single-class behavior
unchanged).

=== TASK 4: Marketplace deal page — list all classes ===
On the deal detail page's "CO-INVEST VIA SPV" section
(currently just "View open SPVs"): if a deal has multiple
spvs, list each one with its class label and its own
carry_pct/mgmt_fee_pct/close_date, linking to that specific
SPV's detail page. If only one SPV exists, current behavior is
fine (no class label shown).

=== VERIFICATION ===
Write scripts/verify_sprint23.py (apps/api/scripts/), following
the pattern of verify_sprint22.py — pass/fail only, no
interactive notes/save prompt, idempotent fixtures
(ON CONFLICT DO NOTHING) with teardown-at-start AND
teardown-at-end (learn from Sprint 22's teardown bug — verify
zero rows remain after the run, and confirm no triggers or
constraints were left in an unexpected state).

Assertions to include:
  [Y] class_label column + spvs_deal_class_label_uniq index
      exist and match the snapshot
  [Y] Creating a second SPV under a deal WITHOUT a class_label
      is rejected (API-level enforcement)
  [Y] Creating a second SPV WITH a duplicate class_label under
      the same deal_id raises (DB constraint)
  [Y] Creating a second SPV WITH a distinct class_label succeeds
  [Y] Investment-level roll-up: create 2 classes under one test
      deal with known commitment amounts; assert the roll-up's
      total_committed equals the sum of both, AND the per-class
      breakdown matches each individually
  [Y] Roll-up numbers are Decimal-precise (no float drift)
  [Y] Teardown: zero leftover rows for all tables touched,
      confirm via count(*) on each

Report each assertion explicitly (pass/fail), matching the
style of verify_sprint22.py's output. Push when 100% pass.
