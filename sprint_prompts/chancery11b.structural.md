CHANCERY — PHASE 11b (semantic INDEX + RETRIEVE). 4 tasks +
verification. TWO REAL, POTENTIALLY-BLOCKING GATES — check BOTH
honestly before building anything, exactly like the AWS Textract
credential check earlier this project. Do NOT mock/fake either
dependency if missing.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI touched.

=== TASK 1: Discover, don't assume — TWO gates ===
  (a) PGVECTOR GATE: check whether the pgvector extension is
      enabled (SELECT * FROM pg_extension WHERE extname='vector').
      If NOT enabled, attempt CREATE EXTENSION IF NOT EXISTS
      vector — if the app's DB role lacks permission, STOP and
      report exactly that (Joe will need to enable it via
      Supabase's dashboard/SQL editor directly, outside this
      sprint).
  (b) VOYAGE AI GATE: check for real Voyage API credentials
      (VOYAGE_API_KEY or similar env var) anywhere in this
      environment. If present, ACTUALLY attempt a real, minimal
      embedding call (e.g. voyage-context-4 or whichever current
      model is appropriate) against trivial text — confirm it
      genuinely authenticates, not just that a key-shaped string
      exists. IF THIS FAILS OR NO KEY EXISTS: stop immediately,
      report exactly what was tried and what happened, and do NOT
      proceed to Tasks 2-4. This is a legitimate, expected
      possible outcome.
  (c) IF AND ONLY IF both gates pass: confirm the real current
      chunking/text-preparation needs — what content should be
      embedded (full extracted_text? per-provision for narrative
      docs? per-box for K-1s?) by reading the REAL current data
      shapes across document_extractions, document_template_
      extractions, and document_narrative_extractions.
Report all findings before proceeding. If EITHER gate fails, stop
here — do not attempt Tasks 2-4.

=== TASK 2 (only if Task 1 passes): Embedding + index service ===
Build apps/api/services/document_embedding.py:
  - A function embedding a document's relevant content (per Task
    1c's real findings) via Voyage's API
  - Store embeddings in a new pgvector-typed column/table
    (design based on Task 1c's findings — org_id-scoped, RLS
    applied IMMEDIATELY in the same migration, same discipline as
    every table tonight)
  - Wire this to fire after successful extraction (reuse the same
    SORT-hook pattern already established for K-1/narrative
    extraction — do not invent a new trigger mechanism)

=== TASK 3 (only if Task 1 passes): Semantic search + RETRIEVE ===
Build a real semantic search function: embed a query, search via
pgvector similarity, respect the SAME visibility engines as
everything else (staff visibility, member resolve_entity_set,
restricted-access filter — reuse, do not bypass), return results
with citations back to source documents.

=== TASK 4 (only if Task 1 passes): Minimal UI entry point ===
A simple search interface — reuse Phase 9's existing search UI
pattern if it can be extended, or a clearly-separate "semantic
search" action if not. Report which.

=== VERIFICATION ===
Write verify_chancery11b.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

IF EITHER TASK 1 GATE FAILED: the verify script should still run,
clearly reporting [BLOCKED] for each downstream assertion with
the exact reason, same pattern as the Textract gate — never a
false [PASS].

Assertions (assuming both gates pass):
  [Y] Report Task 1's findings explicitly, including proof of a
      REAL successful Voyage embedding call and pgvector's real
      enabled status
  [Y] A real test document's content is correctly embedded and
      stored
  [Y] A semantic query correctly returns the relevant document
      (and does NOT return an unrelated one) — a real similarity
      proof, not just "it ran"
  [Y] Results respect visibility — a restricted-access document
      does not appear in another user's search results (test
      against the real app_service connection)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass (or push
the honest BLOCKED report if either gate failed) — hold for
manual review regardless of tier.
