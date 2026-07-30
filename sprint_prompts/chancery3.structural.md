CHANCERY — PHASE 3 (TABULAR extraction via Textract). 3 tasks +
verification. This is the FIRST phase touching AWS Textract
specifically — a genuinely separate AWS service from the R2/S3-
compatible storage Phase 2 already uses (boto3 the library being
installed does NOT mean Textract IAM permissions/credentials
exist). CRITICAL: Task 1 must confirm real AWS Textract access
actually works BEFORE any other work proceeds. If it does not,
STOP and report clearly — do NOT mock/fake a Textract call or
proceed with invented sample output. This is exactly the kind of
gap that must be discovered, not assumed.

CONTEXT: document_template_extractions already exists (Part 1
SQL applied directly, RLS enabled + policy applied in the same
migration). Builds on Phase 1 (documents/document_extractions/
document_drops, chancery_intake.py) and Phase 2 (SORT/STORE,
document_classifier.py, services.storage R2 mechanism) — both
merged. Confirmed real doc_category values from Phase 2: k1,
tax_return, financial_statement, subscription_doc, accreditation,
id_document (tabular family) among others.

SCOPE: prove the pattern for ONE concrete template — K-1 (the
most common tabular document for this platform's cap-table/SPV
context) — not the full universe of tax forms. 1099 and others
are a natural extension of this same pattern in a LATER phase,
out of scope now.

STANDING RULES: org_id never from request body; Decimal for any
monetary figures — extracted K-1 amounts (income, deductions,
capital) must never be handled as float, store as exact strings
in JSON to avoid float-precision loss, parse to Decimal on read;
no interactive prompts; light theme if any UI is touched.

=== TASK 1: Discover, don't assume — AWS Textract access is the
FIRST gate ===
  (a) Check for real AWS credentials/IAM configuration anywhere
      in this environment (env vars, .env, existing boto3 client
      usage beyond the R2/S3-compatible pattern from Phase 2).
      ACTUALLY attempt a real, minimal Textract API call (e.g.
      DetectDocumentText or AnalyzeDocument on a trivial test
      image/PDF) to confirm real access works — do not just
      check that credentials exist as strings, confirm they
      actually authenticate and authorize successfully against
      the real Textract service.
      IF THIS FAILS: stop immediately, report exactly what was
      tried and what error resulted, and do NOT proceed to Task
      2 or 3. This is a legitimate, expected possible outcome —
      report it clearly rather than working around it.
  (b) If Textract access is confirmed working: confirm which
      Textract API/feature-type is appropriate for K-1 extraction
      (AnalyzeDocument with TABLES+FORMS feature types is the
      likely fit for a structured tax form — confirm via AWS's
      current documentation or SDK behavior, don't guess blindly).
  (c) Confirm there is no existing K-1 template/box-mapping
      defined anywhere in this codebase already (reference_data,
      a fixture file, etc.) — Phase 2 confirmed reference_data has
      zero doc_family rows; confirm the same for any K-1-specific
      structure.
Report all three findings before proceeding. If Task 1a fails,
STOP HERE — do not attempt Tasks 2/3.

=== TASK 2: Textract-calling service for scanned/complex
documents ===
Build apps/api/services/textract_extraction.py:
  - A function that takes a document's stored file (from R2, via
    Phase 2's real storage mechanism) and calls Textract's
    AnalyzeDocument (or whichever API Task 1b confirmed) with
    TABLES+FORMS feature types, returning the raw structured
    result.
  - This should be invoked specifically for documents where
    Phase 1's ROUTE determined needs_ocr=true (scanned, no native
    text layer) — native text-native documents continue using
    Phase 1's pdfplumber path, do NOT route text-native documents
    through Textract unnecessarily (cost + the original design's
    explicit "Textract à la carte ONLY for scans or messy
    ML-table-detection needs").
  - Store the raw Textract response in
    document_template_extractions.raw_extraction for audit
    purposes, regardless of what happens in Task 3's mapping step.

=== TASK 3: K-1 template mapping — the concrete proof case ===
  - Build a function mapping raw extraction (from EITHER
    Textract's structured output for a scanned K-1, OR Phase 1's
    pdfplumber table output for a text-native K-1 — confirm which
    applies based on the document's actual routing) into a K-1
    template structure: the known K-1 box fields (e.g. ordinary
    business income, net rental real estate income, interest
    income, dividends, capital gains — use your judgment on a
    reasonable, real subset of actual K-1 boxes, this does not
    need to be exhaustively complete for every possible K-1 line
    item in this first pass).
  - Store the result in document_template_extractions
    (template_type='k1', mapped_fields=the box->value structure,
    values as exact STRING representations preserving decimal
    precision, extraction_source='textract' or 'native' depending
    on which path was used).
  - This produces a "pre-filled form a human confirms" result —
    do NOT auto-post these figures anywhere else in the system
    (no ledger entries, no automatic anything) — this phase only
    produces the structured, human-reviewable extraction.

=== VERIFICATION ===
Write verify_chancery3.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

IF TASK 1A DETERMINED TEXTRACT ACCESS DOES NOT WORK: the verify
script should still run, reporting that finding clearly as a
[SKIP] or [BLOCKED] with the exact reason, and should NOT report
false [PASS] results for anything downstream of it. This is an
acceptable, informative outcome — do not treat it as something to
hide or work around.

Assertions to include (assuming Task 1a succeeds):
  [Y] Report Task 1's three discovery findings explicitly,
      including proof of a REAL successful Textract API call
  [Y] document_template_extractions confirmed to exist with RLS
      enabled + policy present
  [Y] A scanned (needs_ocr) test K-1-like document is correctly
      routed through Textract, not pdfplumber
  [Y] A text-native test K-1-like document continues using
      Phase 1's pdfplumber path, NOT Textract (cost discipline
      respected)
  [Y] The K-1 template mapping correctly extracts at least 3
      real, known box values from a real test document, with
      figures preserved as exact decimal-precision strings (not
      floats — assert the exact string matches what was embedded
      in the test document, not an approximately-equal float)
  [Y] The raw Textract/native extraction is preserved in
      raw_extraction for audit, separate from the mapped_fields
      template result
  [Y] A different org's user cannot see this org's template
      extractions (test against the real app_service connection)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier. If Task 1a blocked further
work, report that clearly as the sprint's outcome rather than a
failure to be hidden.
