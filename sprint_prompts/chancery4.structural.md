CHANCERY — PHASE 4 (multi-format ingestion). 4 tasks +
verification. Generalizes Phase 1's PDF-only route_document/
extract_native into a real file-type dispatcher. Builds on
Phases 1-3 (documents/document_extractions/document_drops,
chancery_intake.py, the batch-drop endpoint) — all merged. Later
phases (linkage, review screen, Workflow Manager integration,
correction-learning) are explicitly OUT OF SCOPE — this phase
only extends EXTRACTION coverage to new file types.

STANDING RULES: org_id never from request body; Decimal for any
monetary figures; no interactive prompts; light theme if any UI
is touched (backend/service work only expected).

=== TASK 1: Discover, don't assume ===
  (a) Check for any EXISTING library usage anywhere in this
      codebase for reading Word/Excel/PowerPoint/email files
      (search broadly — this platform has touched many document
      formats across prior sprints, e.g. entity documents,
      generated reports). Reuse an existing convention if one
      exists rather than picking a new library in isolation.
  (b) For each format WITHOUT existing precedent, identify and
      install a real, currently-maintained library (e.g.
      python-docx for DOCX, openpyxl for XLSX, python-pptx for
      PPTX, a standard email-parsing library for .eml/.msg) —
      ACTUALLY install and import-test each one, do not just add
      to requirements.txt (this exact gap caused real bugs
      earlier this session).
  (c) Confirm exactly how Phase 1's route_document/extract_native
      are currently structured (re-read the real, current file —
      do not assume from memory) so this phase EXTENDS that
      dispatcher cleanly rather than duplicating logic.
Report all three findings before proceeding.

=== TASK 2: Generalize ROUTE into a real file-type dispatcher ===
Modify apps/api/services/chancery_intake.py:
  - route_document should now inspect the file's actual type
    (via MIME type / magic bytes, not just filename extension —
    a mislabeled extension should not fool it) and dispatch to
    the correct handling path. Existing PDF behavior (text-layer
    check, needs_ocr flag) must be UNCHANGED for actual PDFs —
    this is additive, not a rewrite of proven logic.
  - For XLSX specifically: recognize it as already-structured
    data — no "text layer" question applies; route it straight
    to structured extraction.
  - For unrecognized/unsupported file types: fail clearly and
    gracefully (a documents row with a clear 'unsupported_format'
    status), never crash, never silently drop the file.

=== TASK 3: Extraction for each new format ===
Build the actual extraction logic per format (DOCX, XLSX, PPTX,
plain text, standalone images), storing results in
document_extractions the same way Phase 1 does for native PDFs
(extraction_method reflecting which path was used, extracted_text
and/or extracted_tables populated as appropriate for the format).
Standalone images should route through the EXISTING Textract
integration from Phase 3 (reuse, do not duplicate).

=== TASK 4: Email handling — parse body AND recurse into
attachments ===
Build email intake (.eml at minimum; .msg if a real library
supports it cleanly, otherwise report that gap rather than
faking support):
  - Parse the email body as its own extracted text
  - For EACH attachment: create its OWN separate documents row
    (linked to the same document_drops batch as the parent
    email, or a sensible equivalent — your judgment on the
    cleanest way to represent "these came from one email"), and
    run it through the FULL pipeline recursively (an attached
    PDF goes through the PDF path, an attached XLSX through the
    XLSX path, etc.) — do not just extract attachment text
    inline, treat each attachment as a genuine, independently-
    processed document.

=== VERIFICATION ===
Write verify_chancery4.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] Existing PDF behavior (native text-native + needs_ocr scan
      detection) is CONFIRMED UNCHANGED — re-run a Phase-1-style
      PDF test to prove no regression
  [Y] A real, generated DOCX with known text content is correctly
      extracted (the actual known text is present, not just "some
      text")
  [Y] A real, generated XLSX with known cell values is correctly
      extracted as structured data
  [Y] A real, generated PPTX with known slide text is correctly
      extracted
  [Y] A real plain-text file passes through correctly
  [Y] A standalone image (not a scanned PDF) with known text
      routes through Textract and the known text is recovered
  [Y] An unsupported/unrecognized file type fails gracefully with
      a clear status, no crash
  [Y] A real, generated email with TWO attachments (e.g. one PDF,
      one XLSX) produces: the email body as its own extraction,
      AND two additional independently-processed documents rows
      for the attachments, each correctly extracted via ITS OWN
      correct format path
  [Y] A different org's user cannot see any of this org's new
      documents (test against the real app_service connection)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this extends core
pipeline infrastructure everything else in Chancery depends on.
