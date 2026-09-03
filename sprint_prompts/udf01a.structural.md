# Sprint udf01a — UDF Definitions Layer (STRUCTURAL)

**Tier:** `.structural` — held for manual merge review.
**Database:** Supabase project `mmgwmcinimzuhargsazs`.
**Branch:** cut from `main` after fee43 is resolved.
**Predecessor:** `docs/discovery/UDF_DISCOVERY_REPORT.md` (udf00). Read it first — it is authoritative on current state.

## Scope boundary

This sprint touches the **definitions layer only**: `portfolio.udf_definitions`, `portfolio.udf_values`, value sets, tags, and the first HTTP router.

**Explicitly out of scope — do not build, do not stub, do not "prepare":**
- Tabs, layouts, sections, column spans (Sprint 1b)
- Field-level security enforcement (Sprint 1c)
- DataGrid columns, list filters, CSV import (Sprint 2)
- Any consolidation of `investment_profile_questions`, `note_terms_field_registry`, or `config` (registered debt, not this sprint)

Report anything you skipped and why.

---

# PART 1 — SQL (apply manually via Supabase MCP before running the prompt)

```sql
BEGIN;

-- 1a.1  Type parameters and field metadata
ALTER TABLE portfolio.udf_definitions
  ADD COLUMN type_params        jsonb   NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN api_name           text,
  ADD COLUMN help_text          text,
  ADD COLUMN description        text,
  ADD COLUMN is_required        boolean NOT NULL DEFAULT false,
  ADD COLUMN default_value      jsonb,
  ADD COLUMN is_unique          boolean NOT NULL DEFAULT false,
  ADD COLUMN unique_case_sensitive boolean NOT NULL DEFAULT false,
  ADD COLUMN is_external_id     boolean NOT NULL DEFAULT false,
  ADD COLUMN is_platform_managed boolean NOT NULL DEFAULT false,
  ADD COLUMN value_set_id       uuid,
  ADD COLUMN deleted_at         timestamptz,
  ADD COLUMN deleted_by         uuid,
  ADD COLUMN updated_at         timestamptz,
  ADD COLUMN updated_by         uuid,
  -- reserved for Sprint 1b / 1c, deliberately unused this sprint
  ADD COLUMN record_type_id     uuid,
  ADD COLUMN controlling_definition_id uuid;

-- api_name is immutable once set; label is free to change.
-- Uniqueness follows the existing COALESCE-keyed partial-index convention
-- already proven on this table across the four owner namespaces.
CREATE UNIQUE INDEX udf_def_api_name_uq
  ON portfolio.udf_definitions (
    target_type,
    api_name,
    COALESCE(org_id,        '00000000-0000-0000-0000-000000000000'::uuid),
    COALESCE(team_id,       '00000000-0000-0000-0000-000000000000'::uuid),
    COALESCE(owner_user_id, '00000000-0000-0000-0000-000000000000'::uuid)
  )
  WHERE api_name IS NOT NULL
    AND deleted_at IS NULL
    AND system_to IS NULL;

-- 1a.2  Widen the data_type vocabulary
ALTER TABLE portfolio.udf_definitions DROP CONSTRAINT IF EXISTS udf_def_type_chk;
ALTER TABLE portfolio.udf_definitions ADD CONSTRAINT udf_def_type_chk
  CHECK (data_type IN (
    'text','long_text','rich_text',
    'integer','numeric','currency','percent',
    'date','datetime','boolean',
    'select','multiselect','tags',
    'email','url','phone'
  ));

-- 1a.3  Value sets — extend reference_data rather than build a sixth registry
ALTER TABLE public.reference_data
  ADD COLUMN owner_scope     text,
  ADD COLUMN owner_scope_id  uuid,
  ADD COLUMN is_extensible   boolean NOT NULL DEFAULT false;

ALTER TABLE public.reference_data ADD CONSTRAINT reference_data_owner_scope_chk
  CHECK (owner_scope IS NULL OR owner_scope IN ('platform','org'));

-- Existing 155 rows are global platform facts; backfill explicitly.
UPDATE public.reference_data
   SET owner_scope = 'platform'
 WHERE owner_scope IS NULL;

ALTER TABLE public.reference_data ALTER COLUMN owner_scope SET NOT NULL;

-- The existing UNIQUE (list_key, code, parent_code) is GLOBAL — two orgs
-- collide on the same list name. Replace with an org-scoped partial index.
ALTER TABLE public.reference_data DROP CONSTRAINT IF EXISTS reference_data_list_key_code_parent_code_key;
CREATE UNIQUE INDEX reference_data_scoped_uq
  ON public.reference_data (
    list_key, code,
    COALESCE(parent_code, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
  );

ALTER TABLE portfolio.udf_definitions
  ADD CONSTRAINT udf_def_value_set_fk
  FOREIGN KEY (value_set_id) REFERENCES public.reference_data_lists(id);

-- 1a.4  Tags — join table, not an array
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

-- 1a.5  Definition-change audit
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

CREATE POLICY udf_definition_audit_org_isolation
  ON portfolio.udf_definition_audit
  USING (
    org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
    OR org_id IS NULL
    OR NULLIF(current_setting('app.is_super_admin', true), '') = 'true'
  );

COMMIT;
```

Also seed these `org_settings` keys with platform defaults (do not hardcode):
`crm.udf.max_custom_tabs` = 3, `crm.udf.max_fields_per_target` = 100,
`crm.udf.max_value_set_values` = 500, `crm.udf.max_tags_per_record` = 25,
`crm.udf.max_tag_vocabulary` = 500, `crm.udf.max_rich_text_chars` = 131072.

---

# PART 3 — Sprint prompt

## TASK 1 — Discovery confirmation

Before writing code, confirm against the live database:

- Every Part 1 object exists with the expected columns, constraints, indexes, and policies.
- `portfolio.udf_definitions` and `portfolio.udf_values` are still empty (`count(*)` on both). If either is non-empty, **stop and report** — the migration path changes.
- `public.reference_data` row count is 155 and every row now has `owner_scope = 'platform'`.
- Re-read `apps/api/services/portfolio_udf.py` and report: the exact current body of `record_udf_value` and its `_VALUE_UPSERT` statement, every `create_*` function, and confirmation that no `update_*` or `delete_*` function exists.
- Confirm `services/fee_run_inputs.py:775–819` is still the only production consumer of `udf_values`, and quote the SQL it runs. Anything this sprint changes about the read shape must not break it.

Report findings before proceeding.

## TASK 2 — Fix the write path and add the missing lifecycle

**2a — Drive the bi-temporal columns.** `record_udf_value` currently upserts via `ON CONFLICT DO UPDATE`, overwriting in place, which destroys the prior value. Replace with close-predecessor-then-insert: set `system_to = now()` on the current row, insert the successor. This is the pattern already used on 52 tables in this database — match the existing convention rather than inventing one. The result is an append-only value history with no new table.

`_current` / `_VISIBLE_PREDICATE` and every read path must continue to return exactly one row per (definition, target). Verify the fee_run_inputs query still returns identical results.

**2b — Type-parameter validation.** Add a per-`data_type` parameter contract validated on definition save and again on every value write:

| data_type | required params | bounds |
|---|---|---|
| `text` | `length` | 1–4000 |
| `long_text` / `rich_text` | `length` | ≤ `crm.udf.max_rich_text_chars` |
| `integer` | `precision` | 1–18 |
| `numeric` / `percent` | `precision`, `scale` | scale ≤ precision ≤ 38 |
| `currency` | `precision`, `scale`, `currency_code` | **scale fixed at 4** |
| `select` / `multiselect` | `value_set_id` | — |
| `tags` | — | caps from `org_settings` |
| `email` / `url` / `phone` | `length` | — |
| `date` / `datetime` / `boolean` | — | — |

Reject invalid combinations (scale > precision, length 0, currency with scale ≠ 4) at definition-save time with a clear 422.

Numeric values land in `value_numeric`, a real `numeric` column — let Postgres enforce the type, but still `quantize()` to the declared scale with `ROUND_HALF_UP` before the write, and parse as `Decimal(str(...))`, never `float`.

**2c — Lifecycle functions that do not currently exist.** Add `update_definition`, `deactivate_definition`, `reactivate_definition`, `soft_delete_definition`. Every one writes a `udf_definition_audit` row with before/after state. `api_name` is immutable — reject any attempt to change it. Soft delete leaves values untouched and is blocked if the definition is referenced anywhere; report the reference list in the error.

**2d — Type-change matrix.** Widening only: `text`→`long_text`, `integer`→`numeric`, `select`→`multiselect`, increase `length`/`precision`/`scale`. **Decreasing `scale` is blocked unconditionally.** Decreasing `length`/`precision` or narrowing `min`/`max` requires a dry-run count of affected rows returning zero. Everything else is rejected with "create a new field instead."

**2e — Tags.** New tag values are minted only by a caller holding a `tag.create` permission; without it, a tag not already in the vocabulary is rejected. Normalize on write (trim, case-fold into `normalized_code`, preserve first-entered casing in `tag_code`). Enforce `max_tags_per_record` and `max_tag_vocabulary` from `org_settings`. Provide merge and rename, both audited.

## TASK 3 — Router and verification

**3a — First HTTP surface.** The entire UDF layer currently has no endpoint. Add `apps/api/routers/udf.py`, registered in `main.py`:

```
GET    /udf/definitions?target_type=&scope=
POST   /udf/definitions
PATCH  /udf/definitions/{id}
DELETE /udf/definitions/{id}          -> soft delete
POST   /udf/definitions/{id}/deactivate
GET    /udf/values/{target_type}/{target_id}
PUT    /udf/values/{target_type}/{target_id}
GET    /udf/tags/{definition_id}       -> vocabulary
POST   /udf/tags/{definition_id}/merge
```

`org_id` is derived server-side from the request context and never read from a request body. Gate writes on the existing action registry — do not invent new permission strings. Platform-scope writes remain `is_super_admin` only.

Follow the `permissions` + `vocabularies` envelope shape that `PositionsGrid` already consumes, with no truthy fallback. Do not invent a parallel envelope.

**3b — `verify_udf01a.py`** in `apps/api/scripts/`, same pattern as prior verify scripts. Pass/fail only, no interactive prompts, no note-entry step, idempotent, teardown at start and end.

Assertions:

- [ ] Every Part 1 column, constraint, index, and policy exists as specified
- [ ] `api_name` unique index holds across all four owner namespaces; the same `api_name` in two different orgs is permitted
- [ ] `api_name` cannot be changed by `update_definition`
- [ ] Each type contract accepts a valid param set and **rejects** an invalid one (negative case per type)
- [ ] Currency with `scale != 4` is rejected
- [ ] Scale decrease is rejected unconditionally; scale increase succeeds and existing values survive unchanged
- [ ] Length decrease with affected rows > 0 is rejected; with zero affected rows it succeeds
- [ ] Writing a value twice leaves the predecessor with `system_to` set and returns exactly one current row
- [ ] The prior value is still retrievable after overwrite (this is the 17a-4 assertion)
- [ ] `Decimal` round-trips at declared scale; a float never reaches the database
- [ ] Soft-delete hides the definition, leaves values intact, and is reversible
- [ ] Soft-delete is blocked when the definition is referenced
- [ ] Every lifecycle operation writes exactly one `udf_definition_audit` row with correct before/after
- [ ] Tag mint **without** `tag.create` is rejected; **with** it, succeeds
- [ ] Tag normalization dedupes `"Prospect"` / `"prospect"` / `" PROSPECT "` to one vocabulary entry
- [ ] `max_tags_per_record` is enforced from `org_settings`, not a constant
- [ ] Tag merge repoints assignments and is audited
- [ ] RLS: an org cannot read another org's definitions, values, tags, or audit rows — assert the empty result, not just the populated one
- [ ] `value_set_id` FK rejects a non-existent list
- [ ] `reference_data` org-scoped uniqueness permits two orgs to hold the same `list_key`
- [ ] **Regression:** the `fee_run_inputs.py` query returns byte-identical results to a pre-sprint capture
- [ ] Every router endpoint returns 403 without permission and 200 with it
- [ ] Teardown leaves zero rows in all new tables, confirmed by `count(*)`

Report each assertion explicitly. Do not merge. Do not push until 100% pass.
