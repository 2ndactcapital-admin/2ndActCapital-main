CHANCERY — PHASE 3 COMPLETION (real K-1 extraction — closing a
gap discovered during Phase 5). 4 tasks + verification. Real AWS
Textract access is CONFIRMED WORKING (the textractgate sprint,
merged). document_template_extractions ALREADY EXISTS with RLS
(original Phase 3 Part 1 SQL). This sprint builds the actual
extraction logic that was never drafted after the access gate
was confirmed — the missing piece Phase 5 discovered and worked
around with a forward field-naming contract.

CONTEXT — the field-naming CONTRACT Phase 5 established and is
ALREADY BUILT AGAINST (its auto-link logic expects this): a K-1's
party-name field in mapped_fields should be checked in this
priority order: partner_name, then shareholder_name, then
beneficiary_name, then member_name, then recipient_name — use
whichever is the FIRST one that actually applies to a genuine K-1
(a partnership K-1 uses partner_name; an S-corp K-1 uses
shareholder_name; etc. — use real judgment, this is not "use
partner_name always").

STANDING RULES: org_id never from request body; Decimal for
monetary figures — store as exact STRING representations in JSON
(never float) — this was already the rule when the table was
designed, still applies; no interactive prompts; light theme if
any UI touched (none expected).

=== TASK 1: Discover, don't assume ===
  (a) Re-verify Textract access still works with a real, live
      call (do not assume the textractgate sprint's confirmation
      is still valid without checking — confirm fresh).
  (b) Re-read the REAL current chancery_intake.py (it has been
      extended twice since the original Phase 3 attempt — Phase 4
      generalized ROUTE, and other work has touched this file) —
      confirm exactly where a K-1 (doc_family='tabular',
      category='k1', from Phase 2's SORT) should hook into this
      template-extraction step, and what state the document is in
      at that point (has real extracted_text/extracted_tables
      already, from Phase 1's native path or Phase 4's Textract-
      for-scans path).
  (c) Re-read Phase 5's ACTUAL auto-link trigger code (services/
      chancery_intake.py or wherever it lives) — confirm exactly
      what condition currently fires it (was it built expecting
      to be called explicitly once mapped_fields exists, or does
      it already poll/trigger some other way) so this phase wires
      in correctly, firing Phase 5's REAL logic for the first
      time with REAL data instead of Phase 5's test simulation.
Report all three findings before proceeding.

=== TASK 2: Textract + native table extraction for K-1s ===
Build (or extend, if partial work already exists — check first)
apps/api/services/textract_extraction.py:
  - For a K-1 that came through Phase 1/4's needs_ocr path
    (scanned): call Textract's AnalyzeDocument with TABLES+FORMS
    feature types (confirmed appropriate in the original Phase 3
    design) using the NOW-CONFIRMED-WORKING credentials.
  - For a K-1 that's text-native: use Phase 1's already-extracted
    pdfplumber table output directly — do NOT call Textract
    unnecessarily (cost discipline, established rule).
  - Store the raw structured result in document_template_
    extractions.raw_extraction for audit, regardless of source.

=== TASK 3: K-1 template mapping ===
Build the actual mapping from raw extraction (Textract or native
table structure) into K-1 template fields:
  - The party-name field per the CONTEXT contract above (whichever
    of partner_name/shareholder_name/beneficiary_name/member_name/
    recipient_name genuinely applies)
  - A reasonable, real subset of actual K-1 box fields (e.g.
    ordinary business income, net rental real estate income,
    interest income, dividends, capital gains — use judgment, does
    not need to be exhaustive for every possible K-1 line item)
  - Store in document_template_extractions.mapped_fields, all
    monetary values as exact decimal-precision STRINGS
  - Set documents.status to 'sorted' -> now genuinely ready for
    Phase 5's linkage (which was previously only tested against
    simulated data)

=== TASK 4: Wire into the real pipeline end-to-end ===
  - Connect this to Phase 2's SORT step: when a document is
    classified as category='k1', this K-1 extraction should fire
    automatically as part of that same flow (per Task 1b's real
    hook point) — not a manually-triggered separate step.
  - Connect the output to Phase 5's REAL auto-link/propose logic
    (per Task 1c's real trigger point) — prove this fires for
    real, using REAL extracted data this time, not Phase 5's
    test-simulated mapped_fields.

=== VERIFICATION ===
Write verify_chancery3b.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] A real, generated scanned (needs_ocr) K-1-like test document
      is correctly routed through Textract, extraction succeeds
  [Y] A real, generated text-native K-1-like test document uses
      the native pdfplumber path, NOT Textract (cost discipline)
  [Y] The template mapping correctly extracts the party-name field
      using the correct priority-order field name for the test
      document's actual K-1 type, plus at least 3 real box values,
      all monetary values as exact decimal-precision strings (not
      floats — assert exact string match against embedded test
      values)
  [Y] END-TO-END, REAL (not simulated) PROOF: a full pipeline run
      from DROP through this K-1 extraction correctly triggers
      Phase 5's REAL auto-link logic — a K-1 whose extracted party
      name matches a real seeded entity gets automatically linked
      (document_entity_links, created_by=NULL), proving the two
      phases are now genuinely connected, not just individually
      tested
  [Y] The SAME end-to-end proof for the no-match case: a K-1 whose
      party name matches nothing creates a real
      document_link_proposals row via the real pipeline
  [Y] A different org's user cannot see this org's template
      extractions (test against the real app_service connection)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier. This closes a real, previously-
undiscovered gap — be thorough, do not rush past the end-to-end
proof, that is the actual point of this sprint.
