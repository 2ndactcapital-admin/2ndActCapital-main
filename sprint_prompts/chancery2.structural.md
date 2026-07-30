CHANCERY — PHASE 2 (SORT + STORE). 3 tasks + verification.
Builds on Phase 1 (documents/document_extractions/document_drops,
chancery_intake.py — route_document/extract_native/
process_document, all merged and proven). Later phases (TABULAR/
NARRATIVE template extraction, INDEX, RETRIEVE) remain OUT OF
SCOPE.

CONTEXT: Phase 1 confirmed the real classifier lives at
services/document_classifier.py :: async classify_document(conn,
org_id, text, *, model=None) -> dict. Its real return shape and
whether it already handles the "propose new category" path
(mentioned in earlier design work — matches against existing
categories first, proposes new ones into a review queue rather
than auto-inserting) needs re-confirming live, not assumed from
memory.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI is touched.

=== TASK 1: Discover, don't assume ===
  (a) Read classify_document's REAL current signature, return
      shape, and confirm whether the "propose new category, don't
      auto-insert" behavior is real and currently working, or
      whether it needs adjustment to be called from a new
      context (Chancery documents, not whatever it was originally
      built to classify).
  (b) Confirm how R2 (Cloudflare) storage is used ELSEWHERE in
      this codebase (e.g. entity documents from Sprint 17, or CRM
      doc uploads) — the real upload function/service, bucket
      naming convention, and how a stored file's key gets
      recorded — reuse that exact mechanism, do not invent a
      second R2 integration pattern.
  (c) Confirm the real reference-data list of valid doc_family/
      doc_category values this classifier already recognizes (the
      12-value doc_category reference list mentioned in earlier
      design work) — report the REAL current list, do not assume
      it matches that number or those exact values.
Report all three findings before proceeding.

=== TASK 2: SORT — wire the classifier into the Chancery
pipeline ===
Extend process_document (or add a new orchestration step called
after it) so that once a document is successfully extracted
(status='extracted' from Phase 1), it is automatically classified:
  - Call classify_document with the real extracted_text
  - If it matches an existing category: set documents.doc_family
    correctly (map the classifier's category to the tabular/
    narrative family distinction from the original design —
    confirm via Task 1c which categories belong to which family,
    do not guess)
  - If the classifier proposes a NEW category: do NOT auto-set
    doc_family to something invented — store the proposal exactly
    the way the existing "propose new category" mechanism already
    does (Task 1a), and leave documents.status reflecting that
    it's pending human review rather than silently guessing
  - Update documents.status to 'sorted' (or 'pending_review' if a
    new category was proposed)

=== TASK 3: STORE — persist the file to R2, versioned ===
  - Using the REAL R2 mechanism found in Task 1b, store the
    original file bytes, recording the resulting storage key in
    documents.storage_key
  - If a document with the same drop/entity is uploaded again
    (a re-upload / correction scenario), do NOT silently overwrite
    the prior stored file — version it (reuse whatever versioning
    convention Task 1b's existing R2 usage already follows, e.g.
    entity documents' own version handling, don't invent a new
    scheme)
  - Update documents.status to 'stored' once the file is
    successfully persisted

=== VERIFICATION ===
Write verify_chancery2.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.
This may run unattended — report failures clearly, never guess
past one silently.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] A real test document with clearly tabular-style content
      (e.g. resembling a K-1) gets correctly classified and its
      doc_family set appropriately
  [Y] A real test document with clearly narrative-style content
      (e.g. resembling correspondence) gets correctly classified
      and its doc_family set appropriately
  [Y] A document whose content doesn't match any existing
      category triggers the propose-new-category path correctly
      (verify via whatever real mechanism Task 1a confirmed
      handles this — e.g. a review-queue table, not a guessed
      doc_family)
  [Y] A successfully classified document's file is actually
      stored in R2 via the real mechanism, and storage_key is
      populated correctly
  [Y] Re-uploading the same document produces a NEW version
      rather than silently overwriting the original stored file
  [Y] A different org's user cannot see this org's documents
      (re-confirm RLS still holds with the new SORT/STORE columns
      in play — test against the real app_service connection, not
      a bypass role)
  [Y] Teardown: zero leftover rows and any R2 test objects created

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier.
