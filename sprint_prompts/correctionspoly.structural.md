CORRECTIONS POLYMORPHISM — make document_field_corrections usable by
non-document targets (structured-note terms, proposed templates, etc.)

4 tasks + verification.

WHY THIS SPRINT EXISTS: document_field_corrections.document_id is
NOT NULL with a FK to documents(id), and org_id is NOT NULL with a FK
to organizations(id). Confirmed live:
  document_id -> documents(id), NOT NULL
  org_id      -> organizations(id), NOT NULL
Reference filings (portfolio.reference_filings) are GLOBAL — no org_id,
not a document. A correction against a mis-extracted note term (e.g. a
buffer misread as a floor) cannot be logged against this table today.
The correction-learning loop's demonstrated accuracy improvement is
real (per PROJECT_STATUS: 33.3% -> 100%) and depends on this table.
This sprint extends it WITHOUT touching that proven behavior.

WHAT THIS IS NOT — DO NOT BUILD THESE:
  - Any change to HOW corrections are read/applied for documents. The
    existing document correction path must be byte-for-byte identical
    in behavior after this sprint. This is a strict backward-
    compatibility requirement, not a suggestion.
  - Extraction logic for note terms. This sprint only makes the
    corrections table ABLE to receive a note-terms correction. Nothing
    yet produces one.
  - A parallel table. That was rejected in design discussion precisely
    because it guarantees drift between two correction systems.

STANDING RULES: org_id never from request body; RLS policy in the same
migration; verify scripts pass/fail only.

THERE IS NO HUMAN AVAILABLE. Report discovery, then continue
immediately in the same response. Exceptions are the explicit
STOP/BLOCKED gates below.


=== TASK 1: DISCOVER — do not assume ===

Read the real current code, report, THEN CONTINUE IMMEDIATELY.

  1a. Find EVERY place document_field_corrections is read or written.
      grep the whole apps/api tree. Report each call site, what it
      passes for document_id and org_id, and whether any of them
      assume org_id is always present (e.g. use it in a WHERE clause
      without a NULL check).

  1b. Find and read the DeepEval measurement that produced the
      33.3% -> 100% figure cited in PROJECT_STATUS. Report the exact
      script path. This sprint's verify step will RE-RUN this exact
      measurement as a regression assertion — find it now so Task 3
      can reference it precisely rather than guessing at a path.

      *** STOP CONDITION ***
      If this script cannot be found, or if it requires data/state
      that no longer exists, STOP and report BLOCKED with what's
      missing. Do not fabricate a substitute measurement — a fake
      regression check that always passes is worse than no check.

  1c. Report the exact shape of document_template_extractions and how
      template_extraction_id is used downstream, since this migration
      touches a table with an existing FK into it.

  1d. Confirm portfolio.securities_global_note_terms and
      portfolio.reference_filings both exist (built in prior sprints)
      and report their id column types.


=== TASK 2: SCHEMA — additive, non-breaking ===

Apply via Supabase MCP. This is ADDITIVE ONLY:

  ALTER TABLE document_field_corrections
    ALTER COLUMN document_id DROP NOT NULL,
    ALTER COLUMN org_id DROP NOT NULL;

  ALTER TABLE document_field_corrections
    ADD COLUMN target_type text,
    ADD COLUMN target_id uuid;

  -- Backfill existing rows so nothing silently becomes ambiguous
  UPDATE document_field_corrections
    SET target_type = 'document', target_id = document_id
    WHERE target_type IS NULL;

  ALTER TABLE document_field_corrections
    ALTER COLUMN target_type SET NOT NULL,
    ALTER COLUMN target_id SET NOT NULL;

  ALTER TABLE document_field_corrections
    ADD CONSTRAINT document_field_corrections_target_type_chk
    CHECK (target_type IN ('document', 'note_terms', 'template_proposal'));

  -- Enforce the pairing: a 'document' target still requires document_id
  -- AND org_id (org-scoped correction, unchanged behavior); a
  -- 'note_terms' target requires target_id to reference a GLOBAL row
  -- and must NOT have an org_id (global data has no org).
  ALTER TABLE document_field_corrections
    ADD CONSTRAINT document_field_corrections_document_pairing_chk
    CHECK (
      (target_type = 'document' AND document_id IS NOT NULL AND org_id IS NOT NULL)
      OR
      (target_type <> 'document' AND org_id IS NULL)
    );

  -- Index the new polymorphic lookup path
  CREATE INDEX idx_doc_field_corr_target ON document_field_corrections
    (target_type, target_id);

DO NOT add a target_id FK — it cannot reference two different tables.
Referential integrity for non-document targets is enforced at the
application layer; document it in a comment on the column.

RLS: report the CURRENT policy on document_field_corrections (it is
presumably org-isolated, matching org_id). It must continue to work
UNCHANGED for target_type='document' rows. For target_type='note_terms'
rows (org_id IS NULL, global data), the existing org-isolation policy
would incorrectly hide them from everyone. ADD a policy allowing global
read when target_type <> 'document', matching the four-policy shape
used elsewhere for global tables, WITHOUT removing or weakening the
existing per-org policy. Confirm both coexist correctly — a row with
target_type='document' must still be invisible cross-org, and a row
with target_type='note_terms' must be visible globally.


=== TASK 3: REGRESSION — the DeepEval re-run ===

This is the load-bearing verification in this sprint. A schema change
that quietly breaks retrieval would still pass a naive existence check
but would show up here as the improvement collapsing.

Run the exact script found in Task 1b against the SAME golden set /
fixtures it used originally. Capture its reported before/after (or
accuracy) figures.

  *** STOP CONDITION ***
  If the re-run produces a result meaningfully different from the
  33.3% -> 100% figure on record (e.g. the "after" figure is not at
  or near 100%), STOP. Do not merge. Report the discrepancy exactly —
  this means the schema change broke the correction-retrieval path
  used by document corrections, which is the one thing this sprint is
  forbidden from touching.


=== TASK 4: UPDATE PROJECT STATUS ===

Update docs/PROJECT_STATUS.md: the polymorphism added, the DeepEval
regression figure from this run (not just "still passes" — the actual
number), and that note-terms corrections are schema-ready but nothing
yet produces one.


=== VERIFICATION: apps/api/scripts/verify_correctionspoly.py ===

Pass/fail only. No prompts. Idempotent. Teardown at start AND end.

  [ ] document_id and org_id are nullable (query information_schema)
  [ ] target_type and target_id exist, NOT NULL
  [ ] Existing rows all have target_type='document' with target_id
      matching their document_id (backfill correctness — sample and
      compare, not just count)
  [ ] CHECK constraint rejects target_type='document' with a NULL
      document_id or NULL org_id — assert the rejection
  [ ] CHECK constraint rejects target_type='note_terms' WITH a non-NULL
      org_id — assert the rejection (proves global-data rows can't
      accidentally carry a tenant)
  [ ] Insert a real correction row with target_type='note_terms',
      target_id pointing at an actual securities_global_note_terms.id
      (create a throwaway one, or use an existing fixture), org_id NULL
      — assert it succeeds
  [ ] RLS: the note_terms correction row above is readable under
      app_service with NO org context set (global read)
  [ ] RLS: a target_type='document' correction row is STILL invisible
      cross-org under app_service with a DIFFERENT org context set —
      this is the critical non-regression check, run it explicitly
  [ ] DEEPEVAL REGRESSION: report the actual re-run figure from Task 3
      inline in this verify script's output, not just a boolean. FAIL
      if it is not at or near the on-record 100% figure.
  [ ] Every EXISTING call site found in Task 1a still functions
      unmodified — if any call site was touched to accommodate this
      change, that is scope creep; report it explicitly as a flag for
      manual review even if tests pass.
