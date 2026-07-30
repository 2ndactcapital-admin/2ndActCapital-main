CHANCERY — PHASE 1 (DROP + ROUTE + EXTRACT, native only). 3
tasks + verification. This runs UNATTENDED overnight — Joe will
not be available to answer questions or debug live. Be
conservative: discover thoroughly, verify your own dependency
installations actually work (not just that they're listed in
requirements.txt — confirmed real bugs tonight where a listed
dependency wasn't actually installed in the venv), and do not
guess when discovery is possible instead.

CONTEXT: documents + document_extractions + document_drops
tables already exist (Part 1 SQL applied directly), ALL THREE
already have RLS enabled with an org-isolation policy (confirmed
live) — do not add policies again, they exist. documents also
has drop_id (nullable FK to document_drops) and
sequence_in_drop (nullable integer) — for a single-file drop,
these can be null; for a multi-file drop, every document in that
batch shares the same drop_id, ordered by sequence_in_drop. This
is Phase 1 of a 6-phase Chancery build. Later phases (SORT via
the existing S25 document classifier, STORE to R2, TABULAR/
NARRATIVE extraction via Textract, INDEX, RETRIEVE) are
explicitly OUT OF SCOPE — do not build them now, do not add AWS/
Textract calls, do not add embedding/vector logic.

PIPELINE ORDER (do not deviate): ROUTE comes BEFORE extraction
logic runs — a deterministic (NOT AI) check of whether a PDF has
a real, native text layer, or is an image/scan with no extractable
text. EXTRACT in this phase handles ONLY the text-native case
(via pdfplumber or PyMuPDF, whichever proves more reliable in
discovery) — a scanned/image-only PDF should be correctly
detected by ROUTE and marked as needing Textract (a FUTURE
phase), not silently mishandled or crashed on.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI is touched (none expected —
backend/service work only this phase).

=== TASK 1: Discover, don't assume ===
  (a) Check whether pdfplumber and/or PyMuPDF (fitz) are already
      in requirements.txt. Whichever is chosen, ACTUALLY install
      it into the venv and ACTUALLY run a real import test to
      confirm it works — do not just add it to requirements.txt
      and assume that's sufficient (this exact gap caused a real
      bug earlier tonight with a different dependency).
  (b) Check whether any existing "documents," "chancery," or
      similar table/service/router already exists anywhere in
      this codebase that this phase might conflict with or
      should reuse — report clearly either way.
  (c) Confirm the real, current file/module location of the
      existing S25 document-type classifier and its real
      function signature — NOT to call it this phase (that's
      Phase 2), just to confirm it exists and report its
      interface for future reference, so Phase 2 doesn't need to
      rediscover this.
Report all three findings before proceeding.

=== TASK 2: Build ROUTE + EXTRACT (native) as a real service ===
Build apps/api/services/chancery_intake.py:
  - route_document(file_bytes: bytes) -> dict — a DETERMINISTIC
    check (no AI, no network call) of whether the PDF has a real
    native text layer. Return something like
    {"has_text_layer": bool, "page_count": int}. Handle a
    corrupt/invalid PDF gracefully (do not crash — return a clear
    error result).
  - extract_native(file_bytes: bytes) -> dict — for a
    text-native PDF only, extract the full text AND any tables
    found per-page, using whichever library Task 1a confirmed
    works. Return {"extracted_text": str, "extracted_tables":
    list, "page_count": int}.
  - process_document(document_id, org_id) -> None — the
    orchestrating function: loads the document row, runs
    route_document, writes a document_extractions row recording
    the routing decision, and IF text-native, runs extract_native
    and updates that same row with the real extracted content;
    updates documents.status to reflect the outcome ('routed',
    'extracted', or 'needs_ocr' if ROUTE determines it's a scan
    with no text layer — do NOT attempt extraction on a
    non-text-native PDF, that is Textract's job in a future
    phase, out of scope now).

=== TASK 3: Batch-capable intake endpoint + end-to-end proof ===
  - An endpoint (e.g. POST /api/v1/documents) accepting ONE OR
    MORE files in a single multipart request. Behavior:
      * Create ONE document_drops row for the request (org_id
        from the authenticated request, never the body),
        file_count = however many files were submitted.
      * For EACH file, in the order received: create its own
        documents row (drop_id = the drop above,
        sequence_in_drop = its position, starting at 1), then
        call process_document for THAT document SEQUENTIALLY —
        do not process files concurrently/in parallel. A later
        file in the same drop must not start processing until
        the previous one has finished (success or failure) —
        this is a deliberate simplicity/safety choice for Phase
        1, not a performance optimization to bypass.
      * If one file in a batch fails (corrupt PDF, extraction
        error), that failure must NOT stop the remaining files
        in the same drop from processing — record its failure on
        ITS OWN documents row and continue to the next file.
      * After all files are processed, set document_drops.status
        to 'completed' and completed_at to now(), and return a
        response listing EACH document's own outcome (its id,
        sequence, and whether routing/extraction succeeded) —
        not just one overall pass/fail for the whole batch.
      * Reuse however file uploads are handled elsewhere in this
        codebase (e.g. entity document uploads from Sprint 17)
        for the actual multipart/file-handling mechanics — don't
        invent a new upload pattern if one already exists.
  - Prove the FULL pipeline end-to-end with BOTH scenarios:
      (a) a SINGLE-file drop: one real, valid, text-native test
          PDF, uploaded alone, confirm dropped -> routed
          (has_text_layer=true) -> extracted (real known text
          content present).
      (b) a MULTI-file drop: at least 3 real test PDFs (mix
          text-native and at least one you deliberately make
          corrupt/invalid) uploaded together in ONE request,
          confirming: all 3 share the same drop_id, each has the
          correct sequence_in_drop (1, 2, 3), each processed in
          order (verify via created_at/updated_at timestamps or
          an explicit processing-order log — prove sequential,
          not just correct), the corrupt one's failure is
          recorded on its own row without preventing the other
          two from succeeding, and the drop's own status reaches
          'completed' once all 3 are done regardless of the one
          failure.

=== VERIFICATION ===
Write verify_chancery1.py (apps/api/scripts/) — pass/fail only,
NO interactive prompts (this runs unattended, nothing can wait
for input), idempotent, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] documents, document_extractions, and document_drops
      confirmed to exist with RLS enabled + policy present (query
      pg_class/pg_policy directly, don't just trust this prompt's
      claim)
  [Y] route_document correctly identifies a real, generated
      text-native test PDF as has_text_layer=true
  [Y] extract_native correctly pulls the actual known text
      content from that same test PDF (assert the REAL text you
      embedded when generating it is present in the result, not
      just that SOME text came back)
  [Y] A corrupt/invalid PDF (e.g. random bytes with a .pdf
      filename) is handled gracefully by route_document — no
      crash, a clear error/false result instead
  [Y] SINGLE-file drop: upload one real test PDF through the
      actual endpoint, confirm a documents row was created with
      status reflecting successful extraction, and a
      document_extractions row contains the real extracted text
  [Y] MULTI-file drop (3 files): one document_drops row created
      with file_count=3, and 3 documents rows sharing that
      drop_id with sequence_in_drop = 1, 2, 3 respectively
  [Y] Files within a drop are processed SEQUENTIALLY, not
      concurrently — proven via timestamps or an explicit order
      log, not just asserted
  [Y] One corrupt file within that 3-file drop fails gracefully
      on its OWN row without preventing the other 2 (a second
      real text-native PDF among them, to prove continuation)
      from completing successfully
  [Y] document_drops.status reaches 'completed' with
      completed_at populated once all files in the drop are
      done, regardless of the one partial failure
  [Y] A different org's user CANNOT see this org's documents or
      drops (confirms RLS is actually protecting the new tables,
      not just present in the schema)
  [Y] npm run build exits 0 (only if any frontend file was
      touched — report if none was)
  [Y] Teardown: zero leftover rows across all three tables

Report each assertion explicitly. If ANY assertion fails, do NOT
attempt to silently work around it — report the failure clearly
in the verify output so Joe can review it in the morning. Push
when 100% pass — hold for manual review regardless of tier, per
standard practice for a first-phase foundational build.
