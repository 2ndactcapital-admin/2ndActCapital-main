-- CORRECTIONS POLYMORPHISM
-- Applied 2026-08-22 via Supabase migration 20260822125432
--   "corrections_polymorphism_target_type_target_id".
-- Recorded here for repo auditability; the deployed DB already has it.
--
-- Make document_field_corrections usable by non-document targets (global
-- structured-note terms, proposed templates). ADDITIVE ONLY: the existing
-- document correction path is unchanged in behaviour and requires no code change.

ALTER TABLE public.document_field_corrections
  ALTER COLUMN document_id DROP NOT NULL,
  ALTER COLUMN org_id DROP NOT NULL;

ALTER TABLE public.document_field_corrections
  ADD COLUMN target_type text,
  ADD COLUMN target_id   uuid;

-- Backfill existing rows so nothing silently becomes ambiguous.
UPDATE public.document_field_corrections
   SET target_type = 'document', target_id = document_id
 WHERE target_type IS NULL;

ALTER TABLE public.document_field_corrections
  ALTER COLUMN target_type SET NOT NULL,
  ALTER COLUMN target_id   SET NOT NULL;

-- BACKWARD COMPATIBILITY (not in the sprint's literal DDL, added deliberately):
-- every existing writer -- services/document_review.submit_field_correction,
-- submit_classification_correction, scripts/eval_correction_loop._seed_correction,
-- scripts/verify_chancery8 -- INSERTs without target_type/target_id. A bare
-- NOT NULL would break all of them and force call-site edits, which this sprint
-- forbids. A column DEFAULT plus a BEFORE INSERT trigger keeps those INSERTs
-- byte-for-byte valid while still guaranteeing both columns are populated.
ALTER TABLE public.document_field_corrections
  ALTER COLUMN target_type SET DEFAULT 'document';

CREATE OR REPLACE FUNCTION public.document_field_corrections_default_target()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  -- Only ever FILLS a NULL. An explicitly supplied target_id is never rewritten,
  -- so polymorphic writers are unaffected.
  IF NEW.target_id IS NULL AND NEW.target_type = 'document' THEN
    NEW.target_id := NEW.document_id;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER document_field_corrections_default_target_trg
  BEFORE INSERT ON public.document_field_corrections
  FOR EACH ROW EXECUTE FUNCTION public.document_field_corrections_default_target();

ALTER TABLE public.document_field_corrections
  ADD CONSTRAINT document_field_corrections_target_type_chk
  CHECK (target_type IN ('document', 'note_terms', 'template_proposal'));

-- Enforce the pairing: a 'document' target still requires document_id AND org_id
-- (org-scoped correction, unchanged behaviour); a non-document target references
-- GLOBAL data and must NOT carry an org_id.
ALTER TABLE public.document_field_corrections
  ADD CONSTRAINT document_field_corrections_document_pairing_chk
  CHECK (
    (target_type = 'document' AND document_id IS NOT NULL AND org_id IS NOT NULL)
    OR
    (target_type <> 'document' AND org_id IS NULL)
  );

CREATE INDEX idx_doc_field_corr_target
  ON public.document_field_corrections (target_type, target_id);

COMMENT ON COLUMN public.document_field_corrections.target_id IS
  'Polymorphic target key, paired with target_type. Deliberately has NO foreign '
  'key: it references documents(id) when target_type=''document'', '
  'portfolio.securities_global_note_terms(id) when target_type=''note_terms'', '
  'and a proposed-template row when target_type=''template_proposal''. A single '
  'FK cannot span three tables, so referential integrity for non-document '
  'targets is enforced at the application layer.';

COMMENT ON COLUMN public.document_field_corrections.target_type IS
  'Discriminator for target_id: document | note_terms | template_proposal. '
  'document rows are org-scoped (org_id NOT NULL, RLS org-isolated); '
  'non-document rows reference GLOBAL data (org_id NULL, RLS global-read).';

-- RLS: the existing per-org policy (document_field_corrections_org_isolation,
-- PERMISSIVE, FOR ALL) is left completely untouched. Permissive policies OR
-- together, so document rows keep their exact cross-org invisibility while the
-- four-policy global shape used by portfolio.reference_filings /
-- securities_global_note_terms is added for non-document rows only.
CREATE POLICY document_field_corrections_global_read
  ON public.document_field_corrections
  FOR SELECT
  USING (target_type <> 'document');

CREATE POLICY document_field_corrections_global_super_admin_insert
  ON public.document_field_corrections
  FOR INSERT
  WITH CHECK (target_type <> 'document'
              AND current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY document_field_corrections_global_super_admin_update
  ON public.document_field_corrections
  FOR UPDATE
  USING (target_type <> 'document'
         AND current_setting('app.is_super_admin', true) = 'true')
  WITH CHECK (target_type <> 'document'
              AND current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY document_field_corrections_global_super_admin_delete
  ON public.document_field_corrections
  FOR DELETE
  USING (target_type <> 'document'
         AND current_setting('app.is_super_admin', true) = 'true');
