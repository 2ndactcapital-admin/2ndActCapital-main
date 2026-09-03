-- udf01a — UDF definitions layer (STRUCTURAL)
--
-- This is the CORRECTED Part 1. The sprint prompt's Part 1 could not be applied:
-- three statements referenced objects that do not exist in the deployed schema.
-- Each correction is marked inline. Verified against project mmgwmcinimzuhargsazs
-- on 2026-09-02 with udf_definitions=0 rows, udf_values=0 rows, reference_data=155.
--
--   Blocker 1  udf_def_api_name_uq named target_type / team_id / owner_user_id.
--              The deployed owner namespace is
--              (org_id, owner_scope, owner_scope_id, applies_to). There are TWO
--              owner columns, not four: owner_scope_id is polymorphic and
--              udf_def_scope_org_chk is what enforces the four namespaces.
--
--   Blocker 2  public.reference_data_lists did not exist, so the value_set_id FK
--              had no target (42P01). reference_data holds VALUES; a "list" was
--              only a repeated list_key text with no row and no id. Consequently
--              owner_scope / owner_scope_id / is_extensible — all LIST-level
--              facts — are created here on the new header table rather than on
--              all 155 value rows, where nothing would stop two rows of one list
--              disagreeing about is_extensible.
--
--   Blocker 3  Dropping only reference_data_list_key_code_parent_code_key leaves
--              reference_data_org_list_code_uniq — UNIQUE (org_id, list_key, code)
--              NULLS NOT DISTINCT — in place. That index is STRICTER than the
--              replacement, so the replacement's parent_code component would be
--              unreachable. Both are dropped; reference_data_scoped_uq becomes
--              the single rule.

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- 1a.1  Type parameters and field metadata
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE portfolio.udf_definitions
  ADD COLUMN type_params          jsonb   NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN api_name             text,
  ADD COLUMN help_text            text,
  ADD COLUMN description          text,
  ADD COLUMN is_required          boolean NOT NULL DEFAULT false,
  ADD COLUMN default_value        jsonb,
  ADD COLUMN is_unique            boolean NOT NULL DEFAULT false,
  ADD COLUMN unique_case_sensitive boolean NOT NULL DEFAULT false,
  ADD COLUMN is_external_id       boolean NOT NULL DEFAULT false,
  ADD COLUMN is_platform_managed  boolean NOT NULL DEFAULT false,
  ADD COLUMN value_set_id         uuid,
  ADD COLUMN deleted_at           timestamptz,
  ADD COLUMN deleted_by           uuid,
  ADD COLUMN updated_at           timestamptz,
  ADD COLUMN updated_by           uuid,
  -- reserved for Sprint 1b / 1c, deliberately unused this sprint
  ADD COLUMN record_type_id       uuid,
  ADD COLUMN controlling_definition_id uuid;

-- api_name is immutable once set; label is free to change.
--
-- CORRECTION (blocker 1). Mirrors the shape of the already-proven
-- idx_udf_def_key_unique, including its valid_to IS NULL predicate — the prompt's
-- version omitted valid_to, which would have diverged from the convention this
-- table already uses for its field_key uniqueness.
CREATE UNIQUE INDEX udf_def_api_name_uq
  ON portfolio.udf_definitions (
    COALESCE(org_id,         '00000000-0000-0000-0000-000000000000'::uuid),
    owner_scope,
    COALESCE(owner_scope_id, '00000000-0000-0000-0000-000000000000'::uuid),
    applies_to,
    api_name
  )
  WHERE api_name IS NOT NULL
    AND deleted_at IS NULL
    AND system_to IS NULL
    AND valid_to IS NULL;

-- ═══════════════════════════════════════════════════════════════════════════
-- 1a.2  Widen the data_type vocabulary
-- ═══════════════════════════════════════════════════════════════════════════
-- Non-destructive: udf_definitions is empty, so no existing row can violate the
-- new CHECK. services/portfolio_udf.DATA_TYPES mirrors this list verbatim and is
-- widened in the same commit — widening the CHECK alone changes no behaviour,
-- because _check_choice refuses anything outside the Python frozenset first.

ALTER TABLE portfolio.udf_definitions DROP CONSTRAINT IF EXISTS udf_def_type_chk;
ALTER TABLE portfolio.udf_definitions ADD CONSTRAINT udf_def_type_chk
  CHECK (data_type IN (
    'text','long_text','rich_text',
    'integer','numeric','currency','percent',
    'date','datetime','boolean',
    'select','multiselect','tags',
    'email','url','phone'
  ));

-- ═══════════════════════════════════════════════════════════════════════════
-- 1a.3  Value sets — a real list header, extending reference_data
-- ═══════════════════════════════════════════════════════════════════════════
-- CORRECTION (blocker 2). This is a list HEADER, not a sixth field registry:
-- it holds no field metadata, only the identity and scope of a list whose
-- members already live in reference_data.

CREATE TABLE public.reference_data_lists (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid REFERENCES public.organizations(id),
  list_key       text NOT NULL,
  label          text NOT NULL,
  description    text,
  owner_scope    text NOT NULL,
  owner_scope_id uuid,
  is_extensible  boolean NOT NULL DEFAULT false,
  is_active      boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  created_by     uuid,
  CONSTRAINT reference_data_lists_owner_scope_chk
    CHECK (owner_scope IN ('platform','org')),
  -- Mirrors udf_def_scope_org_chk: a platform list is org-less, an org list is not.
  CONSTRAINT reference_data_lists_scope_org_chk
    CHECK ((owner_scope = 'platform' AND org_id IS NULL)
        OR (owner_scope = 'org'      AND org_id IS NOT NULL))
);

-- Two orgs may hold the same list_key; a platform list is unique on its own.
CREATE UNIQUE INDEX reference_data_lists_key_uq
  ON public.reference_data_lists (
    list_key,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );

ALTER TABLE public.reference_data_lists ENABLE ROW LEVEL SECURITY;

-- Deliberately identical in shape to reference_data_global_or_org: platform rows
-- are readable by every org, writable only by a Super Admin (the WITH CHECK has
-- no org_id IS NULL disjunct, so a NULL-org insert fails the tenant test and
-- only the super-admin bypass can satisfy it).
CREATE POLICY reference_data_lists_global_or_org
  ON public.reference_data_lists
  USING (
    org_id IS NULL
    OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
    OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  )
  WITH CHECK (
    org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
    OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  );

-- Backfill one platform header per existing list. All 155 reference_data rows
-- carry org_id IS NULL (measured), so this yields exactly the 11 platform lists:
-- account_type, ca_province, country, currency, doc_category, ledger_basis,
-- month, name_prefix, name_suffix, tax_character, us_state.
INSERT INTO public.reference_data_lists (org_id, list_key, label, owner_scope, is_extensible)
SELECT DISTINCT rd.org_id,
       rd.list_key,
       initcap(replace(rd.list_key, '_', ' ')),
       'platform',
       false
FROM public.reference_data rd
WHERE rd.org_id IS NULL;

-- Link values to their header. Left NULLABLE on purpose: existing writers of
-- reference_data do not know about list_id yet, and a NOT NULL here would break
-- them at the next insert. The service layer sets it on every new write.
-- TODO(udf-1b): audit reference_data writers, then SET NOT NULL.
ALTER TABLE public.reference_data
  ADD COLUMN list_id uuid REFERENCES public.reference_data_lists(id);

UPDATE public.reference_data rd
   SET list_id = l.id
  FROM public.reference_data_lists l
 WHERE l.list_key = rd.list_key
   AND l.org_id IS NOT DISTINCT FROM rd.org_id;

-- CORRECTION (blocker 3): drop BOTH prior uniqueness rules, not just the global
-- one, so reference_data_scoped_uq is actually the operative constraint.
ALTER TABLE public.reference_data
  DROP CONSTRAINT IF EXISTS reference_data_list_key_code_parent_code_key;
DROP INDEX IF EXISTS public.reference_data_org_list_code_uniq;

CREATE UNIQUE INDEX reference_data_scoped_uq
  ON public.reference_data (
    list_key, code,
    COALESCE(parent_code, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );

ALTER TABLE portfolio.udf_definitions
  ADD CONSTRAINT udf_def_value_set_fk
  FOREIGN KEY (value_set_id) REFERENCES public.reference_data_lists(id);

-- ═══════════════════════════════════════════════════════════════════════════
-- 1a.4  Tags — join table, not an array
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE portfolio.udf_tag_assignments (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            uuid NOT NULL,
  definition_id     uuid NOT NULL REFERENCES portfolio.udf_definitions(id),
  target_type       text NOT NULL,
  target_id         uuid NOT NULL,
  tag_code          text NOT NULL,
  normalized_code   text NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid,
  valid_from        timestamptz NOT NULL DEFAULT now(),
  valid_to          timestamptz,
  system_from       timestamptz NOT NULL DEFAULT now(),
  system_to         timestamptz,
  CONSTRAINT udf_tag_target_chk CHECK (target_type IN
    ('asset','position','valuation','transaction','commitment','entity'))
);

CREATE UNIQUE INDEX udf_tag_assign_uq
  ON portfolio.udf_tag_assignments (definition_id, target_id, normalized_code)
  WHERE system_to IS NULL;

CREATE INDEX udf_tag_assign_lookup
  ON portfolio.udf_tag_assignments (org_id, definition_id, normalized_code)
  WHERE system_to IS NULL;

ALTER TABLE portfolio.udf_tag_assignments ENABLE ROW LEVEL SECURITY;

CREATE POLICY udf_tag_assignments_org_isolation
  ON portfolio.udf_tag_assignments
  USING (
    org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
    OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  );

-- ═══════════════════════════════════════════════════════════════════════════
-- 1a.5  Definition-change audit
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE portfolio.udf_definition_audit (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  definition_id   uuid NOT NULL,
  org_id          uuid,
  changed_by      uuid,
  changed_at      timestamptz NOT NULL DEFAULT now(),
  change_kind     text NOT NULL,
  before_state    jsonb,
  after_state     jsonb,
  CONSTRAINT udf_def_audit_kind_chk
    CHECK (change_kind IN ('create','update','deactivate','reactivate','soft_delete','purge'))
);

ALTER TABLE portfolio.udf_definition_audit ENABLE ROW LEVEL SECURITY;

-- org_id IS NULL is readable by everyone here BY DESIGN: a platform-scope
-- definition's audit trail has no owning tenant, and the definition itself is
-- already globally readable under udf_definitions_scoped_read.
CREATE POLICY udf_definition_audit_org_isolation
  ON portfolio.udf_definition_audit
  USING (
    org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
    OR org_id IS NULL
    OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  );

COMMIT;
