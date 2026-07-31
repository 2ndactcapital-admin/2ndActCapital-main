CHANCERY — PHASE 6 (review/confirm screen). 4 tasks +
verification. First real UI work in Chancery — everything before
this was backend/service only. Builds on Phases 1-5 (all merged,
including the real end-to-end K-1 extraction proven in the Phase
3 completion sprint).

CONTEXT: document_field_corrections already exists (Part 1 SQL
applied directly, RLS + policy in the same migration) — captures
every human correction with original_value/corrected_value, even
though the actual learning system that CONSUMES these (Phase 8)
is not built yet. This phase only needs to WRITE correct records
here, not build any retrieval/learning logic.

STANDING RULES: org_id never from request body; Decimal for
monetary figures; no interactive prompts; light theme (whites/
creams, Navy #1B2B4B/Gold #C5A880) matching every other admin
screen already built.

=== TASK 1: Discover, don't assume — source-traceability is
NOT guaranteed, check honestly ===
  (a) Read the REAL, current document_template_extractions.
      raw_extraction content for a Textract-sourced K-1 (from the
      Phase 3 completion sprint) — confirm whether AWS's response
      actually includes Geometry/BoundingBox data per field, and
      whether it survived being stored (not stripped before
      saving). Textract typically includes this by default —
      confirm it is REALLY there, do not assume.
  (b) Read the REAL, current native pdfplumber extraction path
      (Phase 1's extract_native) — confirm whether ANY location/
      bounding-box metadata was captured and stored, or whether
      only plain text/table VALUES were kept with no position
      data. pdfplumber CAN provide this but Phase 1 may not have
      captured it — check the actual stored data, not the
      library's theoretical capability.
  (c) Read the REAL, current admin-screen UI conventions from the
      most recently built screens (Profiles/Permission-Sets
      manager, or the Ownership Graph screens) — reuse the same
      component/layout patterns, do not invent a new UI paradigm.
Report all three findings before proceeding. IF Task 1a/1b show
location data is NOT available for one or both extraction paths:
design the UI to gracefully degrade for that path (e.g. show
"page N, see attached document" rather than a precise on-page
highlight) rather than promising something the data can't
support — report this gap clearly rather than faking a highlight
overlay with made-up coordinates.

=== TASK 2: Review-payload endpoint ===
Build an endpoint returning everything needed to review one
document in one call: its extracted content (text/tables),
template extraction if applicable (mapped_fields with confidence
if the classifier/extraction provides it — check what's REALLY
available, don't invent a confidence score if none exists),
source-location data per Task 1's findings (real coordinates or
graceful fallback), and its current entity/record links (from
Phase 5, reusable directly).

=== TASK 3: Correction + confirm endpoints ===
  - Submit a correction for a specific field: creates a
    document_field_corrections row (original_value = what was
    there before, corrected_value = the human's new value,
    notes optional) AND updates the actual mapped_fields value
    on the real document_template_extractions row so the
    corrected value becomes the system of record going forward
    — not just logged and ignored.
  - Mark a document as reviewed/confirmed: update documents.
    status to reflect this (a new status value fitting the
    existing free-text convention, e.g. 'confirmed') plus record
    who confirmed it and when.
  - Both must reuse the existing auth/org-scoping pattern from
    every other Chancery endpoint — do not invent new gating
    logic.

=== TASK 4: The review screen itself ===
Build the actual screen (reusing Task 1c's discovered UI
conventions): displays extracted fields with their current
values, confidence where genuinely available, source-location
per Task 1's real findings (a real highlight if coordinates
exist, a clear page-reference fallback if not — never fake
precision the data doesn't support), inline editing to correct a
field (calls Task 3's correction endpoint), shows/allows editing
the document's entity/record links (reuse Phase 5's real
endpoints, do not duplicate), and a clear "confirm reviewed"
action.

=== VERIFICATION ===
Write verify_chancery6.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly,
      INCLUDING an honest statement of whether real source-
      coordinates are available for Textract-sourced, native-
      sourced, both, or neither
  [Y] Review-payload endpoint returns correct, complete data for
      a real test document (extracted content + template fields +
      real links from Phase 5)
  [Y] Submitting a correction creates a document_field_corrections
      row with the correct original_value/corrected_value AND
      updates the real mapped_fields value on
      document_template_extractions (verify the actual DB row
      changed, not just that the correction log entry exists)
  [Y] Confirming a document updates its status correctly with the
      confirming user + timestamp recorded
  [Y] A different org's user cannot see this org's review payload,
      corrections, or confirm this org's document (test against
      the real app_service connection)
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex in any new file
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier.
