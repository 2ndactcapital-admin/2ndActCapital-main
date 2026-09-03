# UDF Discovery Report — sprint `udf00`

**Tier:** `.discovery` — read-only. No DDL, no migrations, no application code changed, no merge.
**Database:** Supabase `mmgwmcinimzuhargsazs`, PostgreSQL 17.6. Connected as `postgres` (`rolbypassrls = true` — noted, because every row count below is an unfiltered count, not an RLS-filtered one).
**Branch:** `claude/udf00-discovery`.
**Date:** 2026-09-02.

Evidence scripts (read-only, added by this sprint, no application code touched):

| Script | What it measured | Raw output |
|---|---|---|
| `apps/api/scripts/discover_udf00_task1.py` | ILIKE sweep, relkinds, exact counts, RLS, policies, `security_invoker`, jsonb value columns | `/tmp/udf00_task1.json` |
| `apps/api/scripts/discover_udf00_task2.py` | Full structure of every UDF-related relation, enums, triggers, samples, distributions | `/tmp/udf00_task2.json`, `logs/udf00_task2.txt` |
| `apps/api/scripts/discover_udf00_task3.py` | Permission model, journal candidates, tags, `reference_data`, prior art | `/tmp/udf00_task3.json`, `logs/udf00_task3.txt` |

---

## Correction to the sprint's stated premise

Two things in the brief are not what the repo says. Recording them here rather than burying them, because both change how much weight the "Phase G already shipped this" assumption can carry.

- **[FIND] Phase G was a 63-assertion run, not 389.** `sprint_prompts/logs/portfoliog.structural.sprint.json` → `result`: *"Phase G shipped — 63/63, idempotent across three consecutive runs. Commit `79803e9`."* `docs/PORTFOLIO_REPORTING_DESIGN_V6.md:189` records the same: `| **G** | UDFs — parallel platform/org/team/user namespaces, NOT a cascade | Shipped (63/63) |`. `apps/api/scripts/verify_portfoliog.py` contains 64 `check(` call sites. Nothing in the repo carries the number 389. The premise "assume UDF functionality already exists in some form" is nevertheless **correct** — it does, and is described below.
- **[FIND] The design doc points at a PROJECT_STATUS section that does not exist.** `docs/PORTFOLIO_REPORTING_DESIGN_V6.md:203` says "Full rationale in `docs/PROJECT_STATUS.md` §7o." `docs/PROJECT_STATUS.md` (1,075 lines) contains no `§7o` and no Phase G / UDF section at all — its only UDF mention is line 552, an aside inside the fee36 write-up. The design doc itself flags this class of problem at line 216 ("Three consecutive briefs citing sections that do not exist is a pattern, not a typo"), but the gap is still open for Phase G specifically. **The Phase G rationale is not written down anywhere except the sprint log JSON and the docstrings in `services/portfolio_udf.py`.**

---

# TASK 1 — Broad sweep

## 1.1 Per-pattern match counts

Every pattern was run against both `pg_class.relname` and `pg_attribute.attname`, across all non-system schemas (`auth`, `extensions`, `graphql*`, `litellm`, `portfolio`, `public`, `realtime`, `storage`, `supabase_migrations`, `vault`).

| Pattern | Table-name matches | Column-name matches |
|---|---|---|
| `%udf%` | 2 | 0 |
| `%user_defined%` | **0** | **0** |
| `%custom_field%` | **0** | **0** |
| `%customfield%` | **0** | **0** |
| `%attribute%` | 1 | 4 |
| `%picklist%` | **0** | **0** |
| `%pick_list%` | **0** | **0** |
| `%value_set%` | **0** | **0** |
| `%valueset%` | **0** | **0** |
| `%layout%` | **0** | **0** |
| `%field_def%` | **0** | **0** |
| `%definition%` | 2 | 3 |
| `%tag%` | 5 | 19 |
| `%custom_tab%` | **0** | **0** |
| `%metadata%` | 0 | 21 |

Nine of the fifteen patterns matched **nothing at all**, in any schema: `user_defined`, `custom_field`, `customfield`, `picklist`, `pick_list`, `value_set`, `valueset`, `layout`, `field_def`, `custom_tab`. That is itself the headline result of Task 1: there is no picklist concept, no value-set concept, no layout concept and no custom-tab concept anywhere in this database under any of those names.

## 1.2 Full distinct-relation match list (39 relations, unfiltered)

False positives are left in deliberately, marked. `secinv` = `security_invoker=true` in `reloptions` (only meaningful for views).

| Schema.relation | Kind | Exact `count(*)` | RLS | Policies | secinv | Matched on | Verdict |
|---|---|---|---|---|---|---|---|
| `portfolio.udf_definitions` | table | **0** | true | 4 | — | name `%udf%`, `%definition%` | **GENUINE — Phase G** |
| `portfolio.udf_values` | table | **0** | true | 1 | — | name `%udf%`; col `definition_id` | **GENUINE — Phase G** |
| `public.entity_attributes` | table | **8** | true | 1 | — | name `%attribute%`; cols `attribute_key`, `attribute_value` | **GENUINE — a second, older EAV on `entities`** |
| `public.entity_document_tags` | table | **1** | true | 1 | — | name `%tag%`; col `tag` | **GENUINE — the tag pattern (Task 3-D)** |
| `public.deals` | table | 5 | true | 1 | — | cols `deal_stage` (`%tag%` via "stage"), `tags` (text[]) | Partial — `tags` genuine, `deal_stage` incidental |
| `public.entities` | table | 24 | true | 1 | — | col `tags` (text[]) | Partial — `tags` genuine |
| `public.workflow_definitions` | table | 2 | true | 1 | — | name `%definition%` | False positive (BPMN workflows) |
| `public.workflow_versions` | table | 3 | true | 1 | — | col `workflow_definition_id` | False positive |
| `public.workflow_triggers` | table | 2 | true | 1 | — | col `workflow_definition_id` | False positive |
| `public.investment_stage_history` | table | 0 | true | 1 | — | name `%tag%` (in "stage"); cols `from_stage`, `to_stage` | False positive (substring "stage") |
| `public.member_investments` | table | 1 | true | 1 | — | cols `investment_stage`, `stage_updated_at`, `stage_updated_by` | False positive (substring "stage") |
| `public.deal_interest` | table | 0 | true | 1 | — | col `investment_stage` | False positive |
| `public.notification_delivery_log` | table | 0 | true | 1 | — | col `metadata` (jsonb) | False positive |
| `portfolio.commitments` | table | 0 | true | 1 | — | col `vintage_year` (`%tag%` via "vintage") | False positive |
| `auth.custom_oauth_providers` | table | 0 | false | 0 | — | col `attribute_mapping` | False positive (Supabase-managed) |
| `auth.saml_providers` | table | 0 | true | 0 | — | cols `attribute_mapping`, `metadata_url`, `metadata_xml` | False positive |
| `auth.sessions` | table | 0 | true | 0 | — | col `tag` | False positive |
| `litellm.DailyTagSpend` | **view** | 0 | false | 0 | **false** | name `%tag%`; col `individual_request_tag` | False positive (vendor) |
| `litellm.LiteLLM_VerificationTokenView` | **view** | 1 | false | 0 | **false** | col `metadata` | False positive (vendor) |
| `litellm.LiteLLM_DailyTagSpend` | table | 0 | false | 0 | — | name `%tag%`; col `tag` | False positive |
| `litellm.LiteLLM_TagTable` | table | 0 | false | 0 | — | name `%tag%`; col `tag_name` | False positive |
| `litellm.LiteLLM_PolicyAttachmentTable` | table | 0 | false | 0 | — | col `tags` (text[]) | False positive |
| `litellm.LiteLLM_AdaptiveRouterSession` | table | 0 | false | 0 | — | col `stagnation_count` (`%tag%` via "stagnation") | False positive |
| `litellm.LiteLLM_SpendLogs` | table | 32 | false | 0 | — | cols `metadata`, `request_tags` | False positive |
| `litellm.LiteLLM_VerificationToken` | table | 1 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_DeletedTeamTable` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_DeletedVerificationToken` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_ManagedVectorStoresTable` | table | 0 | false | 0 | — | col `vector_store_metadata` | False positive |
| `litellm.LiteLLM_MemoryTable` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_OrganizationTable` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_ProjectTable` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_SkillsTable` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_TeamTable` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_UserTable` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `litellm.LiteLLM_WorkflowRun` | table | 0 | false | 0 | — | col `metadata` | False positive |
| `storage.objects` | table | 0 | true | 0 | — | cols `metadata`, `user_metadata` | False positive (Supabase-managed) |
| `storage.s3_multipart_uploads` | table | 0 | true | 0 | — | cols `metadata`, `user_metadata` | False positive |
| `storage.s3_multipart_uploads_parts` | table | 0 | true | 0 | — | col `etag` (`%tag%` via "etag") | False positive |
| `storage.vector_indexes` | table | 0 | true | 0 | — | col `metadata_configuration` | False positive |

**Two views were matched. Neither has `security_invoker=true`** — both are LiteLLM vendor views in the `litellm` schema, neither has RLS-bearing base tables, so this is not a repeat of the `v_profitability_events` leak. No `portfolio` or `public` view matched any pattern.

## 1.3 Suggestive jsonb/json columns

Sweep: every `jsonb`/`json` column on any base table in any non-system schema whose column name is exactly one of `udf_values`, `custom_values`, `attributes`, `extra`, `metadata`, `data`. Populated = non-null and not `{}` / `[]` / `null` / `""`.

| Schema.table.column | Type | Total rows | Populated | Note |
|---|---|---|---|---|
| **`public.reference_data.extra`** | jsonb | **155** | **50** | The only populated non-vendor hit. Holds `{"symbol": "$"}`-style adornments, not user-defined values. See Task 3-D. |
| `public.notification_delivery_log.metadata` | jsonb | 0 | 0 | empty |
| `litellm.LiteLLM_SpendLogs.metadata` | jsonb | 32 | 32 | vendor |
| `litellm.LiteLLM_VerificationToken.metadata` | jsonb | 1 | 0 | vendor |
| `litellm.{DeletedTeamTable, DeletedVerificationToken, MemoryTable, OrganizationTable, ProjectTable, SkillsTable, TeamTable, UserTable, WorkflowRun}.metadata` | jsonb | 0 | 0 | vendor |
| `litellm.LiteLLM_WorkflowEvent.data` | jsonb | 0 | 0 | vendor |
| `storage.objects.metadata`, `storage.s3_multipart_uploads.metadata` | jsonb | 0 | 0 | Supabase-managed |

**No table anywhere carries a `udf_values`, `custom_values`, `attributes` or `data` jsonb column.** A broader sweep of *all* 46 `jsonb` columns in `public` + `portfolio` (`/tmp/udf00_task2.json` → `all_jsonb_columns`) confirms none of them is a bag of user-defined values on a parent record; every one is a typed payload for a specific known purpose (`workflow_runs.context`, `fee_run_lines.calc_detail`, `org_settings.setting_value`, `domain_events.payload`, …).

**TASK 1 — PASS.**

---

# TASK 2 — The Phase G implementation

Four relations qualified as genuinely UDF-related: `portfolio.udf_definitions`, `portfolio.udf_values`, `public.entity_attributes`, `public.entity_document_tags`. `public.reference_data` is carried through as well because Task 3 asks about it directly.

## 2.1 `portfolio.udf_definitions` — 0 rows, RLS on, owner `postgres`

### Columns

| # | Name | Type | Null | Default |
|---|---|---|---|---|
| 1 | `id` | uuid | no | `uuid_generate_v4()` |
| 2 | `org_id` | uuid | **yes** | — |
| 3 | `owner_scope` | text | no | — |
| 4 | `owner_scope_id` | uuid | yes | — |
| 5 | `applies_to` | text | no | — |
| 6 | `field_key` | text | no | — |
| 7 | `label` | text | no | — |
| 8 | `data_type` | text | no | — |
| 9 | `options` | **jsonb** | yes | — |
| 10 | `display_order` | integer | no | `0` |
| 11 | `is_active` | boolean | no | `true` |
| 12 | `valid_from` | timestamptz | no | `now()` |
| 13 | `valid_to` | timestamptz | yes | — |
| 14 | `system_from` | timestamptz | no | `now()` |
| 15 | `system_to` | timestamptz | yes | — |

### Constraints

- PK `udf_definitions_pkey (id)`
- FK `udf_definitions_org_id_fkey (org_id) → organizations(id)`
- CHECK `udf_def_scope_chk`: `owner_scope IN ('platform','org','team','user')`
- CHECK `udf_def_applies_chk`: `applies_to IN ('asset','position','valuation','transaction','commitment','entity')`
- CHECK `udf_def_type_chk`: `data_type IN ('text','numeric','date','boolean','select')`
- CHECK `udf_def_scope_org_chk`:
  `(owner_scope='platform' AND org_id IS NULL AND owner_scope_id IS NULL) OR (owner_scope='org' AND org_id IS NOT NULL) OR (owner_scope IN ('team','user') AND org_id IS NOT NULL AND owner_scope_id IS NOT NULL)`
- **No unique CONSTRAINT.** Uniqueness is a partial index (below).

### Referenced by (inbound FKs)

- `portfolio.udf_values.udf_values_definition_id_fkey (definition_id) → portfolio.udf_definitions(id)` — the only one.

### Indexes — all btree, **no GIN anywhere**

- `udf_definitions_pkey` — UNIQUE btree `(id)`
- `idx_udf_def_key_unique` — **UNIQUE PARTIAL** btree
  `(COALESCE(org_id,'000…0'::uuid), owner_scope, COALESCE(owner_scope_id,'000…0'::uuid), applies_to, field_key) WHERE system_to IS NULL AND valid_to IS NULL`
  The `COALESCE` is load-bearing: NULLs are distinct in a btree, so without it two platform-scope duplicates would never collide.
- `idx_udf_def_applies` — btree `(applies_to)`
- `idx_udf_def_org` — btree `(org_id) WHERE org_id IS NOT NULL`

### Triggers

**None.**

### Policies (4)

- `udf_definitions_scoped_read` (SELECT): `owner_scope='platform' OR org_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid OR current_setting('app.is_super_admin',true)='true'`
- `udf_definitions_scoped_write` (INSERT, WITH CHECK): platform requires super-admin; non-platform requires matching org
- `udf_definitions_scoped_update` (UPDATE, USING + WITH CHECK): same shape
- `udf_definitions_scoped_delete` (DELETE, USING): same shape

All four `NULLIF` the org GUC, per the project's RLS rule.

### Data

**Zero rows.** No scope distribution, no org distribution, no sample. The table has never held a production row.

## 2.2 `portfolio.udf_values` — 0 rows, RLS on

### Columns

| # | Name | Type | Null | Default |
|---|---|---|---|---|
| 1 | `id` | uuid | no | `uuid_generate_v4()` |
| 2 | `org_id` | uuid | no | — |
| 3 | `definition_id` | uuid | no | — |
| 4 | `target_type` | text | no | — |
| 5 | `target_id` | uuid | no | — |
| 6 | `value_text` | text | yes | — |
| 7 | `value_numeric` | numeric | yes | — |
| 8 | `value_date` | date | yes | — |
| 9 | `value_json` | **jsonb** | yes | — |
| 10–13 | `valid_from` / `valid_to` / `system_from` / `system_to` | timestamptz | | `now()` / — / `now()` / — |

### Constraints

- PK `udf_values_pkey (id)`
- FK `udf_values_definition_id_fkey → portfolio.udf_definitions(id)`
- FK `udf_values_org_id_fkey → organizations(id)`
- CHECK `udf_values_target_chk`: `target_type IN ('asset','position','valuation','transaction','commitment','entity')` — the **same six values** as `udf_def_applies_chk`
- **No unique CONSTRAINT.** `target_id` carries **no FK** — it is polymorphic and structurally unconstrained.

### Referenced by

**Nothing.**

### Indexes — all btree, **no GIN**

- `udf_values_pkey` — UNIQUE btree `(id)`
- `idx_udf_values_unique` — **UNIQUE PARTIAL** btree `(org_id, definition_id, target_type, target_id) WHERE system_to IS NULL AND valid_to IS NULL`
- `idx_udf_values_target` — btree `(target_type, target_id)`
- `idx_udf_values_org` — btree `(org_id)`

### Triggers

**None.**

### Policies (1)

- `udf_values_org_isolation` (ALL, USING + WITH CHECK): `org_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid OR current_setting('app.is_super_admin',true)='true'`

### Data

**Zero rows.**

## 2.3 `public.entity_attributes` — 8 rows, RLS on

This is the *other* UDF-shaped store, and it is the one with real data in it. It predates Phase G and is wired to the CRM.

### Columns

| # | Name | Type | Null | Default |
|---|---|---|---|---|
| 1 | `id` | uuid | no | `uuid_generate_v4()` |
| 2 | `org_id` | uuid | no | — |
| 3 | `entity_id` | uuid | no | — |
| 4 | `attribute_key` | text | no | — |
| 5 | `attribute_value` | text | **yes** | — |
| 6 | `value_type` | text | no | `'string'` |
| 7–10 | `valid_from` / `valid_to` / `system_from` / `system_to` | timestamptz | | bi-temporal |
| 11 | `created_by` | uuid | yes | — |
| 12 | `created_at` | timestamptz | no | `now()` |

### Constraints

- PK `entity_attributes_pkey (id)`
- FK → `entities(id)`, `organizations(id)`, `users(id)`
- **No CHECK constraints at all** — `attribute_key` is unconstrained free text and `value_type` is unconstrained free text with a `'string'` default.
- **No unique constraint or unique index of any kind** — nothing stops two active rows for the same `(entity_id, attribute_key)`.

### Indexes — all btree, no unique beyond the PK, **no GIN**

- `entity_attributes_pkey` UNIQUE `(id)`
- `idx_entity_attributes_entity` `(entity_id)`
- `idx_entity_attributes_key` `(attribute_key)`

### Triggers

**None.**

### Policies (1)

- `entity_attributes_org_isolation` (ALL): standard org GUC + super-admin bypass, `NULLIF`-guarded.

### Data — all 8 rows, org distribution

`org_id` distribution: `00000000-0000-0000-0000-000000000001` × 8 (2nd Act only). No scope column exists.

| entity_id | attribute_key | attribute_value | value_type |
|---|---|---|---|
| `15ced073…9ef1` | `risk_floor` | `catastrophic loss - unacceptable` | `string` |
| `15ced073…9ef1` | `time_horizon_years` | `20` | `string` |
| `15ced073…9ef1` | `investment_objectives` | `["wealth preservation", "family protection"]` | `string` |
| `15ced073…9ef1` | `behavioral_risk_indicators` | `["loss aversion", "capital preservation focus"]` | `string` |
| `a1e5938b…5ea5` | `risk_floor` | `catastrophic loss` | `string` |
| `a1e5938b…5ea5` | `non_negotiables` | `["capital preservation", "family wealth protection"]` | `string` |
| `a1e5938b…5ea5` | `time_horizon_years` | `20` | `string` |
| `a1e5938b…5ea5` | `investment_objectives` | `["wealth preservation"]` | `string` |

**[FIND] `value_type` is a lie in the live data.** Every row is `value_type='string'`, but four of the eight hold a JSON array in `attribute_value` and two hold an integer. The column exists, has a default, has no CHECK, and no writer sets it to anything other than the default — `apps/api/routers/entities.py:857`, `:984` and `apps/api/routers/investment_profile.py:633` are the three INSERT sites and none of them supplies `value_type`. A reader that trusts `value_type` to decide how to parse `attribute_value` is wrong 6 times out of 8 on the data that is actually there.

## 2.4 `public.entity_document_tags` — 1 row, RLS on

Covered in full under **Task 3-D** below.

## 2.5 `public.reference_data` — 155 rows, RLS on

Covered in full under **Task 3-D** below.

## 2.6 Enums

**No enum type is used by any of these tables.** Every vocabulary on `udf_definitions` / `udf_values` / `entity_attributes` / `entity_document_tags` / `reference_data` is `text` plus (sometimes) a CHECK.

For completeness, the ten enums that exist in `public` + `portfolio`, values in `enumsortorder`:

| Enum | Values |
|---|---|
| `public.accreditation_status` | not_verified, self_certified, third_party_verified, expired |
| `public.address_type` | primary_residence, mailing, business, registered |
| `public.aml_risk_rating` | low, medium, high |
| `public.deal_document_status` | pending, processing, extracted, failed |
| `public.deal_status` | draft, submitted, under_review, active, closed, archived |
| `public.entity_type` | individual, trust, llc, lp, corporation, foundation, other, gp, s_corp, c_corp, corp_uk, corp_eu, corp_cayman, corp_luxembourg, corp_other_intl, family_office, household, spv, account |
| `public.kyc_status` | not_started, in_progress, approved, flagged, expired |
| `public.ofac_status` | not_screened, passed, false_positive, review_required |
| `public.social_platform` | linkedin, twitter, facebook, instagram, angellist, crunchbase, other |
| `public.tax_id_type` | ssn, ein, itin, utr, vat, trn, nino, tin_other |

`portfolio` contains **zero** enum types.

## 2.7 The ten questions

### Q1 — Where do UDF values live?

**Answer: an EAV table with four typed value columns — `portfolio.udf_values`. Not JSONB-on-parent, not physical columns.**

Evidence: `portfolio.udf_values` columns 6–9 are `value_text` / `value_numeric` / `value_date` / `value_json`. `apps/api/services/portfolio_udf.py:771` `coerce_value()` returns **all four columns with exactly one non-NULL**, and the upsert at `portfolio_udf.py:_VALUE_UPSERT` overwrites all four on conflict — deliberately, so a `data_type` correction cannot strand a value in the old column next to the new one. `boolean` is stored in `value_json`, not in a boolean column (`portfolio_udf.py:783`). `select` is stored in `value_text` (`portfolio_udf.py:789`).

`None` is refused outright (`portfolio_udf.py:773`): an absent UDF is an absent ROW, so a reader can distinguish "not recorded" from "recorded as nothing."

**The parallel, older answer is also EAV**: `public.entity_attributes` is a single-`text`-column EAV on `entities`. So this database has **two** EAV stores, with no relationship between them.

### Q2 — Which parent objects are UDF-enabled? Is `entities` among them?

**Answer: yes — six target types, and `entity` is one of them. This is not portfolio-only.**

Evidence: CHECK `udf_def_applies_chk` and CHECK `udf_values_target_chk` both enumerate the identical six: `asset`, `position`, `valuation`, `transaction`, `commitment`, **`entity`**. Mirrored in `apps/api/services/portfolio_udf.py:128` (`APPLIES_TO`) with `TARGET_TYPES = APPLIES_TO` at line 131.

Two important qualifications:

- **`target_id` carries no FK.** `udf_values.target_id` is a bare `uuid` with no referential integrity to *any* of the six parents. `apps/api/scripts/verify_portfoliog.py:145` says so explicitly: *"udf_values.target_id is polymorphic and carries NO FK."* Nothing prevents a `target_type='entity'` row pointing at a deleted or nonexistent entity, or at a position id.
- **CRM `entities` UDF support is theoretical.** The vocabulary permits it; nothing in the application ever writes it (see Q10), and the table is empty.

### Q3 — Is there a concept of field type? What types, stored where?

**Answer: yes. Five types, stored as `portfolio.udf_definitions.data_type` (`text`, CHECK-constrained).**

Evidence: CHECK `udf_def_type_chk`: `data_type IN ('text','numeric','date','boolean','select')`. Mirrored at `apps/api/services/portfolio_udf.py:134` (`DATA_TYPES`). Not an enum type — a text column plus a CHECK.

Type → storage column mapping (`portfolio_udf.py:771–812`): `numeric`→`value_numeric`, `date`→`value_date`, `boolean`→`value_json`, `text`→`value_text`, `select`→`value_text`.

The three other field-definition systems in this database use *different* type vocabularies, none of which agrees with this one:
- `public.investment_profile_questions.question_type` — `text`, `boolean`, `select`, `number` (note **`number`**, not `numeric`), no CHECK at all.
- `portfolio.note_terms_field_registry.data_type` — CHECK `IN ('numeric','text','boolean','date')` — no `select`.
- `public.entity_attributes.value_type` — free text, defaults `'string'`, no CHECK, never set by any writer.

### Q4 — Are there type parameters (precision, scale, length, min, max)?

**Answer: no. None, in any form, anywhere.**

Evidence: `portfolio.udf_definitions` has 15 columns; none is a precision, scale, length, min, max, regex, format, required-ness or nullability parameter. The only non-vocabulary metadata columns are `label`, `options`, `display_order`, `is_active`. `apps/api/services/portfolio_udf.py:301` (`_numeric`) coerces to `Decimal` with no bound checks; `_text` (line 382) applies no length limit. `portfolio.note_terms_field_registry` likewise has none. `public.investment_profile_questions` has exactly one such parameter — `is_required boolean` — and Phase G does not.

### Q5 — Is there a picklist / value-set concept?

**Answer: yes, but only as an inline per-definition option list — there is no shared, reusable value-set entity.**

Evidence: `portfolio.udf_definitions.options jsonb` (nullable, **no CHECK**, no GIN index). `data_type='select'` requires a non-empty option list, enforced in Python only (`apps/api/services/portfolio_udf.py:239` `_normalize_options`, raising `UdfError` — *"A select field with no choices can never accept a value"*). `_coerce_choice_list` (line 269) accepts a bare list **or** `{"choices":[…]}` / `{"options":[…]}` / `{"values":[…]}`, because "the Part 1 SQL left `options` as an unconstrained `jsonb` and both shapes are things a caller plausibly sends" — i.e. the on-disk shape is genuinely not pinned down. Values are validated against the list at write time (`portfolio_udf.py:789`).

Confirming the negative: `%picklist%`, `%pick_list%`, `%value_set%`, `%valueset%` matched **zero** tables and **zero** columns in every schema. Two options lists cannot be shared between two definitions; each carries its own copy.

`public.investment_profile_questions.options jsonb` is the same pattern with the same lack of constraint, storing `{"options": ["<$1M", "$1M-$5M", …]}`.

### Q6 — Is there any permission binding on UDFs?

**Answer: no. Not field-level, not tab-level, and no join to any permission-set table.**

Evidence, three independent ways:

1. **Schema.** Neither `udf_definitions` nor `udf_values` has a `permission_key`, `permission_set_id`, `profile_id`, `role_id` or any similar column. Conversely, a sweep of all ten permission tables (`permissions`, `permission_sets`, `permission_set_permissions`, `profiles`, `profile_permissions`, `roles`, `role_permissions`, `user_permission_sets`, `user_roles`, `assistant_action_catalog`) for any column matching `%field%` / `%column%` / `%attribute%` / `%udf%` returned **an empty set**.
2. **Code.** `apps/api/services/portfolio_udf.py` never calls `rbac.has_permission` and never reads a `permission_key`. The only gate is a boolean `is_super_admin` for platform-scope creation (`portfolio_udf.py:479`) and `_OrgWrite` for org/team/user scope (`:512`, `:552`, `:594`, `:877`).
3. **RLS.** All five policies key on `app.current_org_id` and `app.is_super_admin` only.

The **closest thing to per-user narrowing** that exists is `_VISIBLE_PREDICATE` (`portfolio_udf.py:647`), which restricts *visibility of definitions* by scope — a user sees platform definitions, their own org's, team definitions for teams they are actually on (via `public.team_members` JOIN `public.teams`), and their own user-scope definitions. That is scope narrowing, not permission binding: it answers "whose field is this," not "may this user read this field."

### Q7 — Is there any layout / placement / ordering metadata?

**Answer: ordering only — a single `display_order integer`. No placement, no sections, no column spans, no tabs.**

Evidence: `portfolio.udf_definitions.display_order integer NOT NULL DEFAULT 0`. That is the entire layout surface. `%layout%` and `%custom_tab%` matched **zero** tables and **zero** columns database-wide.

And `display_order` is *only* a sort key, deliberately carrying no precedence — `portfolio_udf.py:672`:

> *"Deterministic, and carrying NO precedence. `owner_scope` leads the sort only so two runs return the same list; a caller reading meaning into the order is reading meaning that is not there."*

Full ordering: `ORDER BY d.display_order, d.field_key, array_position(ARRAY['platform','org','team','user'], d.owner_scope), d.id`.

### Q8 — Is there any audit of definition changes, or history of value changes?

**Answer: no dedicated audit and no value journal. Both tables are bi-temporal, and that is the whole mechanism — and on `udf_values` it is not actually driven.**

Evidence:

- No trigger on either table (`pg_trigger` for both relations: empty).
- No `*_history` / `*_audit` / `*_journal` companion table for either.
- Both carry the four bi-temporal columns (`valid_from`, `valid_to`, `system_from`, `system_to`), and both unique indexes are partial on `system_to IS NULL AND valid_to IS NULL` — so a Rule 3 restatement *would* leave the superseded row in place and readable.
- **But `record_udf_value` does not restate — it UPDATEs in place.** `_VALUE_UPSERT` (`portfolio_udf.py:815`) is `INSERT … ON CONFLICT (org_id, definition_id, target_type, target_id) WHERE system_to IS NULL AND valid_to IS NULL DO UPDATE SET value_text=…, value_numeric=…, value_date=…, value_json=…`. It sets neither `valid_to` on the old row nor inserts a successor. **Overwriting a UDF value destroys the prior value with no trace.** The bi-temporal columns on `udf_values` are, as written today, decorative.
- `udf_definitions` has no update path in the service at all (four `create_*` functions, no `update_*`), so the definition-side bi-temporal columns are also undriven.

`public.entity_attributes` is the same story: bi-temporal columns, no trigger, and no unique index — so its writers append without ever closing a predecessor.

### Q9 — Is there soft-delete / active-inactive state on definitions?

**Answer: yes, two independent mechanisms — but nothing in the codebase ever uses either to retire a definition.**

Evidence:

- `portfolio.udf_definitions.is_active boolean NOT NULL DEFAULT true`, read by `_VISIBLE_PREDICATE` (`portfolio_udf.py:649`: `AND d.is_active  = true`), so an inactive definition disappears from resolution.
- `valid_to` / `system_to` are the second mechanism, and `_current(alias)` (`portfolio_udf.py:163`) pins both to `IS NULL` in every read.
- `portfolio.udf_values` has **no `is_active`** — only the bi-temporal pair.
- There is **no `deactivate` / `retire` / `delete` function** in `services/portfolio_udf.py`. `is_active` can only ever be flipped by hand-written SQL today. The DELETE policy exists on `udf_definitions` and no application code issues a DELETE against it.

### Q10 — Does anything reference these tables from application code?

**Answer: almost nothing. `services/portfolio_udf.py` is a 997-line orphan — imported by exactly one verify script and by no router, no service, and no frontend file. There is exactly one production reader of `portfolio.udf_values`, and it bypasses the service module entirely with raw SQL.**

Complete reference list (repo-wide grep, `node_modules` / `venv` / `.next` excluded, this sprint's own scripts excluded):

**Backend — the service module itself**
- `apps/api/services/portfolio_udf.py` — 997 lines. Constants at `:112–146`; six exceptions at `:170–205`; validators `:209–412`; `create_platform_definition` `:453`, `create_org_definition` `:490`, `create_team_definition` `:519`, `create_user_definition` `:571`; `resolve_visible_definitions` `:685`; `get_definition` `:729`; `is_team_member` `:746`; `coerce_value` `:771`; `record_udf_value` `:829`; `get_udf_value` `:926`; `list_udf_values_for_target` `:948`.

**Backend — the only production consumer**
- `apps/api/services/fee_run_inputs.py:775–819` — `_load_positions()` issues a **raw SQL SELECT against `portfolio.udf_values`** (`:809`) to load position tags for fee exclusions, filtering `target_type='position' AND value_text IS NOT NULL AND valid_to IS NULL AND system_to IS NULL`. **It does not import `services.portfolio_udf`.** Its docstring at `:777` records the fee35 finding that positions have no tag column and that the key is `(target_type, target_id)`, "NOT `record_id`, which is what the name suggests."
- `apps/api/services/fee_calc_inputs.py:34` — docstring only, no query.

**Backend — verification**
- `apps/api/scripts/verify_portfoliog.py:73–93` — the only `import` of `services.portfolio_udf` in the entire repo.
- `apps/api/scripts/verify_fee35.py:1617` — string literal in an assertion message.

**Backend — routers**: **none.** There is no `routers/udf.py`. `apps/api/main.py` registers `portfolio`, `portfolio_ingest`, `portfolio_positions`, `portfolio_securities`, `portfolio_transactions` (`:43–47`, `:535–539`) — no UDF router among them. **No HTTP endpoint anywhere reaches `portfolio.udf_definitions` or `portfolio.udf_values`.**

**Frontend / DataGrid**: **none.** No file under `apps/web` contains `udf`, in any casing. `components/portfolio/PositionsGrid.jsx`, `SecuritiesGrid.jsx` and `TransactionsGrid.jsx` render fixed column sets and do not fetch or render user-defined fields.

**BPMN action registry**: **none.** `apps/api/services/action_registry.py` contains no UDF action, and `assistant_action_catalog` (16 rows) contains no UDF `action_key`.

**Docs** (mentions, not references): `docs/PORTFOLIO_REPORTING_DESIGN_V6.md:189,195`; `docs/PROJECT_STATUS.md:552`; `sprint_prompts/portfoliog.structural.md`.

For contrast, the *other* two stores are fully wired:
- `public.entity_attributes` — read at `apps/api/routers/entities.py:584`; written at `entities.py:857`, `entities.py:984`, `apps/api/routers/investment_profile.py:633`; rendered by `apps/web/components/crm/AttributesSection.jsx` (imported by `components/crm/EntityDetailTabs.jsx:6`).
- `public.entity_document_tags` — read at `apps/api/routers/entity_documents.py:61`, `:235`, `:248`; written at `:127`, `:199`, `:292`; deleted at `:298`.

**TASK 2 — PASS.**

---

# TASK 3 — The four blocked design questions

## A — Field-level security

**Does the SOC permission model have per-field granularity? No. It is strictly per-action, and in two parallel dialects.**

There are two coexisting permission axes in this database, and they do not share a vocabulary:

**Axis 1 — normalized `permissions` (resource + action), 28 rows**

`public.permissions (id, name, resource, action)`, UNIQUE `(name)`, UNIQUE `(resource, action)`. Joined to `roles` (13 rows) via `role_permissions` (91 rows, PK `(role_id, permission_id)`), and to users via `user_roles` (1 row).

The 28 `(resource, action)` pairs, in full:
`audit_log/view`, `billing/manage`, `community/view`, `compliance/{manage,override,view}`, `content/manage`, `dashboard/view`, `deals/{interest,score,view,vote}`, `documents/manage`, `insurance/view`, `marketplace/{manage,submit,view}`, `members/{manage,view}`, `portfolio/{manage,view}`, `roles/manage`, `spv/{manage,view}`, `users/manage`, `workflows/{author,configure_triggers,view_runs}`.

**The grain is `resource`, and `resource` is a subsystem — never a table, never a column.** There is no `portfolio/manage_field_x`, no per-object resource, and nothing below `portfolio`.

**Axis 2 — flat `permission_key` text**

- `public.profiles (id, org_id, name, description, is_seed, …)` — 6 rows, UNIQUE `(org_id, name)`
- `public.profile_permissions (id, org_id, profile_id, permission_key, created_at)` — 32 rows, UNIQUE `(profile_id, permission_key)`, FK `profile_id → profiles(id) ON DELETE CASCADE`
- `public.permission_sets (id, org_id, name, description, …)` — 1 row, UNIQUE `(org_id, name)`
- `public.permission_set_permissions (id, org_id, permission_set_id, permission_key, created_at)` — 1 row, UNIQUE `(permission_set_id, permission_key)`, FK `permission_set_id → permission_sets(id) ON DELETE CASCADE`
- `public.user_permission_sets (user_id, permission_set_id, granted_at, granted_by)` — 1 row, PK `(user_id, permission_set_id)`

The 18 distinct `permission_key` values actually granted across both tables:
`author_workflows`, `configure_workflow_triggers`, `indicate_interest`, `manage_deals`, `manage_documents`, `manage_members`, `manage_portfolio`, `score_deal`, `staff`, `view_community`, `view_dashboard`, `view_deals`, `view_insurance`, `view_marketplace`, `view_members`, `view_portfolio`, `view_workflow_runs`, `vote_deal`.

Again: `verb_subsystem`. No field appears in any of them.

**Axis 3 — the action registry** (what the brief calls "Sprint-11"): `public.assistant_action_catalog`, 16 rows, columns `(id, org_id, action_key, module, description, access_type, required_permission, default_autonomy, reversible, render_target, is_active, registered_at)`, UNIQUE `(org_id, action_key)`. `required_permission` is a **single** `permission_key` per action — again per-action, and again no field column. Populated from `apps/api/services/action_registry.py` (`register_actions()` calls in service modules, upserted at `action_registry.py:76`).

**Mechanical proof of the negative:** every column across all ten permission tables matching `%field%`, `%column%`, `%attribute%` or `%udf%` → **empty set** (`/tmp/udf00_task3.json` → `A_permissions.field_shaped_columns_in_perm_tables`).

**If per-field security were added, what is the natural join?**

**`profile_id` and `permission_set_id` — both, not one.** The evidence does not support picking a single axis:

- `profile_permissions` and `permission_set_permissions` are **structurally identical** — same five columns, same `(parent_id, permission_key)` unique, same `ON DELETE CASCADE`. Neither is a subset of the other, and both are live: `profile_permissions` has 32 rows, `permission_set_permissions` has 1.
- They are reached differently: a profile is attached to a user via `users.profile_id`; a permission set is attached via the `user_permission_sets` join table (a user may hold several).
- **[FIND] Binding to only one of them would be inert in practice today.** The `workflowpermsfix` sprint established that `org_admin` is not a `roles` row and that the gate read the *profile* axis only while every real org_admin had `profile_id` NULL. A UDF field-security table bound to `profile_id` alone would repeat that failure; bound to `permission_set_id` alone it would cover exactly one real permission set. The safe read of the evidence is that both axes must be honoured through the shared `rbac.has_permission` helper, exactly as the CLAUDE.md super-admin rule already requires.

Concretely, a field-security row would bind to:
`public.profile_permissions.profile_id → public.profiles.id` **and** `public.permission_set_permissions.permission_set_id → public.permission_sets.id`, carrying `org_id uuid` and a field identifier (`portfolio.udf_definitions.id`, since `field_key` alone is ambiguous across the four parallel `owner_scope` namespaces).

**Not settled by the evidence:** whether field security should be expressed as new `permission_key` strings inside the existing tables (needing no new table, but exploding the key space by one key per field per org) or as a new binding table. That is a design call the database cannot answer.

## B — Layout metadata

**Does anything already render a configurable field layout? No. Field placement is hardcoded JSX in every screen. What IS server-driven is *editability*, via the permission envelope — and that convention should be extended rather than duplicated.**

**CRM entity detail — hardcoded.**
- `apps/web/components/crm/EntityDetailTabs.jsx:23–33` — the tab list is a literal array in the component: `overview`, `addresses`, `employment` (conditional on `isIndividual`), `tax_ids`, `social`, `notes`, `documents`, `linked_documents`, `ownership`, `compliance`. Each tab maps to a hardcoded import (`:5–16`). **A configurable tab would have nowhere to come from.**
- `apps/web/components/crm/EntityDetailsForm.jsx:15`, `:23`, `:30` — three module-level constant arrays, `COMMON_TEXT_FIELDS`, `ENTITY_TEXT_FIELDS`, `PERSON_TEXT_FIELDS`, chosen by entity type in JS.
- Layout itself is raw Tailwind: a two-column grid `dl className="mt-4 grid gap-4 sm:grid-cols-2"` (`:87`) and again at `:152`, with individual fields promoted to full width by `className="sm:col-span-2"` (`:141`, `:188`, `:245`, `:266`, `:270`). **That is the only "column span" concept that exists — a literal Tailwind class on a specific `div`.** There is no ordering, section or span value anywhere in the database or in any API response.

**The entity-attributes UI has no layout at all.** `apps/web/components/crm/AttributesSection.jsx` renders `attributes.map(...)` into a flat `<dl>` of key/value rows in whatever order the API returned them, with an "Add attribute" form taking a free-text `attribute_key` and `attribute_value`. No grouping, no ordering, no type awareness.

**The one real convention worth extending — the permission envelope.** `apps/web/components/portfolio/PositionsGrid.jsx` is explicit about it in its own header comment (`:29–39`):

> *"The server publishes both lists (`vocabularies.inline_editable` / `.editable`) and this component honours them rather than keeping its own … `permissions.can_write` says so directly. That is the only thing deciding …"*

In practice: `const vocabularies = meta?.vocabularies` (`:259`), `const permissions = meta?.permissions` (`:260`), `new Set(vocabularies?.inline_editable || [])` (`:266`), `const canWrite = !!permissions?.can_write` (`:269`), then per-cell `editable={inlineEditable.has("taxonomy_key")}` (`:349`) and `inlineEditable.has("is_reconciled")` (`:443`). Server-published option lists drive the selects too (`vocabularies?.source_system` `:514`, `vocabularies?.authority` `:533`), and a view-only user is told which permission they lack (`:614–617`).

**So the shape to extend is: a server response envelope carrying `permissions` + `vocabularies`, consumed with no truthy fallback.** What that envelope does **not** carry today, anywhere, is *which fields exist, in what order, in what section, at what width* — the column set is still a literal in the JSX. A UDF layout would be the first thing to put field identity into the envelope; it is an extension of the pattern, not a parallel invention.

**One reusable server-driven picker already exists:** `apps/web/components/ReferenceSelect.jsx` fetches `/api/reference/{listKey}` on mount and renders the options. `apps/api/routers/reference.py:10` → `services/reference_data.get_list`. If UDF `select` options were ever moved out of the inline `options` jsonb, this is the component that would render them unchanged.

## C — Value history / retention

**Is there any existing append-only journal, audit table, or history-tracking trigger recording old/new values? Yes — six of them, all populated by application code. Zero are populated by a trigger. A UDF value journal would be an instance of an existing pattern, not a new one.**

**There are only 10 non-internal triggers in `public` + `portfolio`, and not one of them writes history.** In full:

| Table | Trigger | Function | What it does |
|---|---|---|---|
| `portfolio.securities_global_relationships` | `trg_sec_global_rel_confirm_gate` | `portfolio.sec_global_rel_confirm_gate` | maker-checker gate |
| `public.chart_of_accounts` | `trg_validate_coa_refs` | `public.fn_validate_coa_refs` | referential validation |
| `public.document_field_corrections` | `document_field_corrections_default_target_trg` | `public.document_field_corrections_default_target` | defaults `target_type`/`target_id` on insert |
| `public.fee_run_lines` | `fee_run_lines_immutable_once_posted` | `…_prevent_posted_mutation` | immutability guard |
| `public.fee_runs` | `fee_runs_immutable_once_posted` | `…_prevent_posted_mutation` | immutability guard |
| `public.journal_lines` | `trg_guard_posted_lines` | `public.fn_guard_posted_lines` | immutability guard |
| `public.journal_lines` | `trg_validate_line_org` | `public.fn_validate_line_org` | org validation |
| `public.spv_carry_run_lines` | `spv_carry_run_lines_immutable_once_posted` | `…_prevent_posted_mutation` | immutability guard |
| `public.spv_carry_run_lines` | `spv_carry_run_lines_no_insert_on_posted` | `…_prevent_posted_insert` | immutability guard |
| `public.spv_carry_runs` | `spv_carry_runs_immutable_once_posted` | `…_prevent_posted_mutation` | immutability guard |

The six functions whose bodies reference `OLD.` values (`sec_global_rel_confirm_gate`, `fee_run_lines_prevent_posted_mutation`, `fee_runs_prevent_posted_mutation`, `fn_guard_posted_lines`, `spv_carry_run_lines_prevent_posted_mutation`, `spv_carry_runs_prevent_posted_mutation`) all **compare** OLD to NEW to refuse a write. None **records** OLD anywhere.

**Existing old/new-value journals — every one application-populated:**

| Table | Old/new columns | Rows | Triggers | Populated by |
|---|---|---|---|---|
| `public.document_field_corrections` | `field_name`, **`original_value`**, **`corrected_value`**, `notes`, `corrected_by`, `corrected_at`, `target_type`, `target_id` | **29** | 1 (defaults target only, does **not** populate values) | application code |
| `public.ownership_change_log` | `prior_pct`, `new_pct`, `change_reason`, `change_source_type`, `change_source_id`, `effective_date`, `changed_by`, `changed_at` | 2 | none | application code |
| `public.spv_status_history` | `from_status`, `to_status`, `note`, `changed_by`, `changed_at` | 2 | none | application code |
| `public.investment_stage_history` | `from_stage`, `to_stage`, `changed_by`, `notes` | 0 | none | application code |
| `public.audit_log` | `action`, `resource_type`, `resource_id`, `payload` (jsonb) | **88** | none | `services/audit.py:85` `write_audit_log()` |
| `public.restricted_access_audit` | `action`, `performed_by`, `performed_at`, `notes` | 0 | none | application code |

**The closest structural match to a UDF value journal is `document_field_corrections`** — it is already field-keyed (`field_name`) and already polymorphic-target (`target_type` / `target_id`, added by the corrections-polymorphism sprint, defaulted by that one trigger). It is a per-field before/after journal in everything but name.

**`public.audit_log` is the generic mechanism**, and it is unusually defensive: `apps/api/services/audit.py:3` states *"The exact shape of `audit_log` is not known at build time"*, so the module introspects `information_schema.columns` on first write (`audit.py:18` `_load_columns`), builds the insert dynamically (`audit.py:139`), and **silently no-ops if the table is absent** (`audit.py:110`). It is called from at least `routers/admin.py` (`:329`, `:382`, `:432`, `:484`, `:573`), `routers/assistant.py` (`:498`, `:561`), `routers/enroll.py:230`, `routers/entity_graph.py` (`:225`, `:309`, `:354`, `:718`, `:813`).

**The other, larger history mechanism is bi-temporality — 52 tables carry both `valid_to` and `system_to`**, including `portfolio.udf_definitions` and `portfolio.udf_values` themselves. Rule 3 restatement (close the old row, insert a successor) is the project's default way of keeping value history, and it needs no journal table at all.

**[FIND] The bi-temporal mechanism is present on `udf_values` but not driven.** As established in Q8, `record_udf_value` UPDATEs the winning row in place rather than closing it and inserting a successor. So today, changing a UDF value leaves **no** history by either mechanism — not bi-temporal, not journal. Whichever route a value journal takes, this is a real gap that exists right now on a table Phase G already shipped.

## D — Tags

### `public.entity_document_tags` — full structure

1 row. RLS enabled. Owner `postgres`.

| # | Column | Type | Null | Default |
|---|---|---|---|---|
| 1 | `id` | uuid | no | `uuid_generate_v4()` |
| 2 | `org_id` | uuid | no | — |
| 3 | `document_id` | uuid | no | — |
| 4 | `tag` | text | no | — |
| 5 | `is_fixed` | boolean | no | `false` |
| 6 | `created_at` | timestamptz | no | `now()` |

- PK `entity_document_tags_pkey (id)`
- FK `document_id → entity_documents(id)`; FK `org_id → organizations(id)`
- UNIQUE `entity_document_tags_document_id_tag_key (document_id, tag)`
- Indexes (all btree, no GIN): the PK, the unique, and `idx_entity_doc_tags_doc (document_id)`
- **No CHECK constraints.** No FK from `tag` to any vocabulary table.
- Triggers: **none**
- Policy: `entity_document_tags_org_isolation` (ALL) — standard org GUC + super-admin, `NULLIF`-guarded
- **Not bi-temporal.** No `valid_to`/`system_to`. Tags are hard-deleted (`routers/entity_documents.py:298`).

**Are tags free-form or constrained? Free-form.** No CHECK, no FK, no reference list. The application takes them straight from the request body with only `.strip()` applied (`routers/entity_documents.py:124`, `:196`, `:288`).

**Is the vocabulary scoped per-org?** Only implicitly. `org_id` is on the row and RLS scopes reads to the caller's org, but the UNIQUE is `(document_id, tag)` — document-scoped, and a document already belongs to one org — so there is **no org-scoped vocabulary table at all**. There is no list of "the tags this org uses"; the vocabulary is whatever strings happen to exist on that org's rows. Live contents: one row, `tag='signed'`, `is_fixed=false`, org `00000000-…-0001`.

**How is `is_fixed` used?**

**[FIND] `is_fixed` is write-only dead metadata.** All three production INSERT sites hardcode the literal `false`:
- `apps/api/routers/entity_documents.py:127–128` — `INSERT INTO entity_document_tags (org_id, document_id, tag, is_fixed) VALUES ($1, $2, $3, false) ON CONFLICT DO NOTHING`
- `apps/api/routers/entity_documents.py:199–200` — identical
- `apps/api/routers/entity_documents.py:292–293` — identical

and **no query anywhere in the repo ever reads it**. The repo-wide grep for `is_fixed` returns exactly those three INSERTs plus one identical INSERT in `apps/api/scripts/verify_sprint17.py:218` and this sprint's own discovery script. It is not selected, not filtered on, not returned in any response, and not rendered. All 1 live rows have `is_fixed = false`. Whatever "fixed" was meant to mean (a system-applied tag a user may not remove, most likely — the DELETE at `:298` does not check it), that meaning was never implemented.

### Other tag surfaces

| Location | Type | In use |
|---|---|---|
| `public.entities.tags` | `text[]` | **0 tags across 24 entities** |
| `public.deals.tags` | `text[]` | **0 tags across 5 deals** |
| `portfolio.udf_values` (`target_type='position'`, `value_text`) | EAV | **0 rows** — but this *is* the position-tag mechanism, per `services/fee_run_inputs.py:775–819` and `docs/PROJECT_STATUS.md:552` |

Other `text[]` columns in `public`/`portfolio`, none of which is a tag vocabulary: `portfolio.note_terms_field_registry.applies_to_archetypes`, `public.deal_ai_summaries.key_risks`, `public.deal_ai_summaries.key_strengths`, `public.deals.highlights`, `public.entity_briefs.key_themes`, `public.transaction_types.applies_to_security_types`.

**Is there a reusable tag pattern? Not really — there are three unrelated ones, and none is reusable as-is.**

The database has (1) a join table with a dead flag and no vocabulary, (2) two `text[]` columns that are entirely unused, and (3) a UDF EAV row pressed into service as a position tag. They share no vocabulary, no constraint, no org-scoping mechanism and no code. `entity_document_tags` is the only one with any code behind it, and adopting it wholesale would mean adopting `is_fixed`-as-dead-column and the absence of any org tag vocabulary. **The honest reading is that no reusable tag pattern exists yet** — but `entity_document_tags`'s *shape* (join table, `(parent_id, tag)` unique, org column, free-form text) is a reasonable template if a vocabulary table and a live `is_fixed` semantic were added.

### Does `reference_data` exist, and is it a viable home for UDF value sets?

**It exists, it is well-formed, and it is a genuinely different concern — but it is the closest existing thing and the gap is narrow.**

`public.reference_data`, 155 rows, RLS enabled:

| # | Column | Type | Null | Default |
|---|---|---|---|---|
| 1 | `id` | uuid | no | `uuid_generate_v4()` |
| 2 | `org_id` | uuid | **yes** | — |
| 3 | `list_key` | text | no | — |
| 4 | `code` | text | no | — |
| 5 | `label` | text | no | — |
| 6 | `parent_code` | text | yes | — |
| 7 | `extra` | jsonb | yes | — |
| 8 | `display_order` | integer | no | `100` |
| 9 | `is_active` | boolean | no | `true` |
| 10 | `created_at` | timestamptz | no | `now()` |

- UNIQUE `(list_key, code, parent_code)` **and** UNIQUE `(org_id, list_key, code) NULLS NOT DISTINCT`
- Index `idx_refdata_list (list_key, is_active, display_order)`
- Policy `reference_data_global_or_org` (ALL): read is `org_id IS NULL OR org_id = <org GUC> OR super-admin`; **WITH CHECK omits the `org_id IS NULL` disjunct**, so a tenant can read the global lists but cannot write one. That is the correct asymmetry, and it is exactly the platform/org split `udf_definitions` needs.

The 11 lists it holds:

| `list_key` | Rows | Org-scoped | With `extra` | With `parent_code` | Inactive |
|---|---|---|---|---|---|
| `account_type` | 7 | 0 | 7 | 0 | 0 |
| `ca_province` | 13 | 0 | 0 | 13 | 0 |
| `country` | 24 | 0 | 24 | 0 | 0 |
| `currency` | 6 | 0 | 6 | 0 | 0 |
| `doc_category` | 12 | 0 | 0 | 0 | 0 |
| `ledger_basis` | 3 | 0 | 3 | 0 | 0 |
| `month` | 12 | 0 | 0 | 0 | 0 |
| `name_prefix` | 5 | 0 | 0 | 0 | 0 |
| `name_suffix` | 6 | 0 | 0 | 0 | 0 |
| `tax_character` | 16 | 0 | 0 | 0 | 0 |
| `us_state` | 51 | 0 | 51 | 0 | 0 |

**All 155 rows are global (`org_id IS NULL`). Not one org-scoped row exists yet**, though the schema and the policy both support it.

**Assessment.** Structurally it is a near-perfect value-set table: stable `code` + display `label`, hierarchy via `parent_code` (used by `us_state`/`ca_province` under `country`), `display_order`, `is_active` soft-delete, `extra` for adornments, a live API (`/api/reference/{listKey}` → `routers/reference.py` → `services/reference_data.get_list`) and a live renderer (`components/ReferenceSelect.jsx`).

But its *contents* are a different concern: these are **canonical world facts** — ISO currencies, US states, months, honorifics — seeded once and shared by every tenant. A UDF value set is a **tenant-authored** list that lives and dies with one field definition. Three concrete mismatches:

1. **No owner.** `reference_data` has no `owner_scope` and no `owner_scope_id`, so it cannot express the four parallel namespaces `udf_definitions` already uses. Its `org_id` axis is two-valued (global or one org); UDF definitions are four-valued.
2. **No link to a field.** Nothing binds a `list_key` to a `udf_definitions.id`. Today the option list is inline in `udf_definitions.options`; moving it here means inventing that binding.
3. **Namespace collision.** `list_key` is a bare global string with `UNIQUE (list_key, code, parent_code)` **not** scoped by `org_id`. Two orgs both wanting a `risk_tolerance` list would collide on that constraint. This is the same defect noted below in `investment_profile_questions`.

**Verdict: a viable home only with schema changes** (an owner axis, a field binding, and org-scoping the `list_key` uniqueness). As deployed, it is a different concern. The `document_classifier` service already treats it as a controlled canonical vocabulary — `services/document_classifier.py:150`: *"canonical reference_data list is NEVER modified here"* — which is a signal that opening it to tenant-authored lists would cut across an existing assumption.

**TASK 3 — PASS.**

---

# Prior art Task 1's patterns did NOT catch

Three further field-definition systems exist in this database. None matched any of the fifteen sweep patterns, and all three are relevant to the design.

## `public.investment_profile_questions` + `investment_profile_answers` — the closest working analogue

This is a **complete, live, working UDF system for CRM entities** — definitions, typed values, picklists, ordering, required-ness, bi-temporality, an API and a UI — and it is not called a UDF.

`investment_profile_questions`, **20 rows**: `id`, `org_id`, `question_key`, `question_text`, `question_type` (default `'text'`, **no CHECK**), `options jsonb`, `category` (default `'general'`), `is_required boolean`, `display_order integer`, plus the four bi-temporal columns, `created_by`, `created_at`. Live `question_type` values: `text`, `boolean`, `select`, `number`. Live `options` shape: `{"options": ["<$1M", "$1M-$5M", "$5M-$10M", "$10M-$25M", "$25M+"]}`.

`investment_profile_answers`, **10 rows**: `id`, `org_id`, `entity_id` (FK → `entities`), `question_id` (FK → `investment_profile_questions`), `answer_value text`, `answer_json jsonb`, four bi-temporal columns, `created_by`, `created_at`, `updated_at`. UNIQUE `(entity_id, question_id)`.

Point by point against Phase G: it has a **real FK** from value to parent (`entity_id → entities`), which `udf_values.target_id` lacks. It has `is_required` and `category`, which `udf_definitions` lacks. It has a router (`apps/api/routers/investment_profile.py`) and UI components (`apps/web/components/investment-profile/`), which Phase G lacks entirely. Phase G has four owner scopes and six target types, which this lacks.

**[FIND] `investment_profile_questions.question_key` is UNIQUE globally, not per org.** The constraint is `investment_profile_questions_question_key_key UNIQUE (question_key)` — a total unique on a bare text column, on a table that has an `org_id`. Two consequences, both real:
1. **Cross-tenant collision.** A second org cannot define a question named `net_worth`; 2nd Act already owns that key for the whole database.
2. **Restatement is impossible.** The table carries `valid_from`/`valid_to`/`system_from`/`system_to`, but a Rule 3 valid-time restatement inserts a second row with the same `question_key` and would violate this unique. The live data confirms the workaround in use: all 20 keys have exactly one row, and 10 of them have `valid_to` set with `system_to` NULL — i.e. questions are retired with **no successor row**, because a successor cannot be inserted.

Phase G got this right where this table did not: `idx_udf_def_key_unique` is `COALESCE`-keyed on org and owner, and partial on `system_to IS NULL AND valid_to IS NULL`. **Whatever the new design does, it should follow Phase G's index and not this table's constraint.**

## `portfolio.note_terms_field_registry` — a third field registry

**19 rows.** `field_key text PRIMARY KEY`, `display_label text NOT NULL`, `data_type text NOT NULL` (CHECK `IN ('numeric','text','boolean','date')` — **no `select`**), `applies_to_archetypes text[]` (CHECK: NULL or non-empty), `hazard_field boolean NOT NULL DEFAULT false`, `created_at`.

Sample: `terms_status`/Terms Status/text/hazard, `protection_type`/text/hazard, `is_decrement_index`/boolean/hazard, `autocall_frequency`/text/`{autocallable}`/hazard.

**No `org_id`.** It is a global registry, like `portfolio.securities_global*`. Its `applies_to_archetypes text[]` is a **different** way of expressing "which parents does this field apply to" than `udf_definitions.applies_to text` — an array of archetypes rather than a single scalar.

## `public.config` — the Rule 1 display-vocabulary table

29-row-per-category table: `(id, org_id, config_key, config_value, value_type, category, display_order, is_active, created_at)`. Categories in use: `asset_taxonomy` (181), `fee_narrative_vocab` (66), `investment_stages` (9), `deal_scoring` (6), `deal_stages` (6), `notification_types` (11), `notification_channels` (5), `roles_config` (5), `document_statuses` (3), `assistant_posture` (2), `profile_modes` (2).

Note it has the **same `value_type` + `display_order` + `is_active`** shape as `reference_data` and `udf_definitions`. **Four tables in this database independently reinvent "key, label, type, display_order, is_active"**: `config`, `reference_data`, `udf_definitions`, `investment_profile_questions` — plus `note_terms_field_registry` as a fifth partial. That is a real convergence and worth naming before a sixth is added.

---

# Blocking questions — answered / unanswered

## Task 2

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | Where do UDF values live? | **YES — an EAV table with four typed columns.** `portfolio.udf_values.{value_text, value_numeric, value_date, value_json}`, exactly one non-NULL. Not JSONB-on-parent, not physical columns. | `portfolio.udf_values` cols 6–9; `apps/api/services/portfolio_udf.py:771` `coerce_value`; `_VALUE_COLUMNS` at `:160` |
| 2 | Which parents are UDF-enabled? Is `entities` among them? | **YES — six: `asset`, `position`, `valuation`, `transaction`, `commitment`, `entity`. Not portfolio-only.** But `target_id` has **no FK** to any of them, and zero rows exist. | CHECK `udf_def_applies_chk`; CHECK `udf_values_target_chk`; `portfolio_udf.py:128`; `apps/api/scripts/verify_portfoliog.py:145` |
| 3 | Is there a field-type concept? | **YES — five types in `udf_definitions.data_type` (text, CHECK-constrained, not an enum): `text`, `numeric`, `date`, `boolean`, `select`.** | CHECK `udf_def_type_chk`; `portfolio_udf.py:134` |
| 4 | Type parameters (precision, scale, length, min, max)? | **NO — none, in any form.** 15 columns on `udf_definitions`, not one is a type parameter. `_numeric` coerces to `Decimal` unbounded; `_text` applies no length cap. | `portfolio.udf_definitions` full column list; `portfolio_udf.py:301`, `:382` |
| 5 | Picklist / value-set concept? | **YES for inline picklists (`udf_definitions.options jsonb`, unconstrained, Python-validated). NO for a shared/reusable value set.** `%picklist%`, `%value_set%`, `%valueset%` matched zero tables and zero columns database-wide. | `udf_definitions.options`; `portfolio_udf.py:239` `_normalize_options`, `:269` `_coerce_choice_list`, `:789`; Task 1 §1.1 |
| 6 | Permission binding on UDFs — field-level, tab-level, or a join to SOC permission-set tables? | **NO — none of the three.** No permission column on either table; no `%field%`/`%column%`/`%attribute%`/`%udf%` column in any of the 10 permission tables; `portfolio_udf.py` never calls `rbac.has_permission`. Only gate is `is_super_admin` for platform scope. | `/tmp/udf00_task3.json` → `field_shaped_columns_in_perm_tables` = `[]`; `portfolio_udf.py:479`, `:512` |
| 7 | Layout / placement / ordering metadata? | **Ordering ONLY — a single `display_order integer`, and it deliberately carries no precedence. NO placement, sections, spans or tabs.** `%layout%` and `%custom_tab%` matched zero, database-wide. | `udf_definitions.display_order`; `portfolio_udf.py:672` `_VISIBLE_ORDER` + comment; Task 1 §1.1 |
| 8 | Audit of definition changes, or history of value changes? | **NO to both.** No trigger on either table, no companion audit table. Bi-temporal columns exist but are **not driven**: `record_udf_value` UPDATEs in place via `ON CONFLICT DO UPDATE` without closing the predecessor, so overwriting a value destroys the prior value. `udf_definitions` has no update path at all. | `pg_trigger` empty for both; `portfolio_udf.py:815` `_VALUE_UPSERT`; four `create_*` and no `update_*` in `portfolio_udf.py` |
| 9 | Soft-delete / active-inactive on definitions? | **YES — two mechanisms (`is_active boolean DEFAULT true`, read by `_VISIBLE_PREDICATE`; plus `valid_to`/`system_to`). But no code path ever sets either** — there is no deactivate/retire/delete function. `udf_values` has no `is_active`. | `udf_definitions.is_active`; `portfolio_udf.py:653`, `:163` `_current`; absence of any `update_*`/`delete_*` in `portfolio_udf.py` |
| 10 | Does application code reference these tables? | **BARELY.** `services/portfolio_udf.py` (997 lines) is imported ONLY by `verify_portfoliog.py`. **No router, no HTTP endpoint, no frontend file, no DataGrid, no BPMN action.** One production consumer: `services/fee_run_inputs.py:809`, raw SQL against `udf_values`, not importing the service module. | `apps/api/services/portfolio_udf.py`; `verify_portfoliog.py:73`; `fee_run_inputs.py:775–819`; `main.py:43–47`, `:535–539`; zero `udf` matches under `apps/web` |

## Task 3

| # | Question | Answer | Evidence |
|---|---|---|---|
| A | Does SOC permission have per-field granularity? If added, what is the natural join? | **NO per-field granularity — strictly per-action, in two parallel dialects (28 `(resource, action)` rows; 18 flat `permission_key` strings). `resource` is always a subsystem, never a table or column.** Natural join if added: **BOTH** `public.profile_permissions.profile_id → profiles.id` **and** `public.permission_set_permissions.permission_set_id → permission_sets.id` — the two tables are structurally identical, both live (32 and 1 rows), reached by different paths, and binding to only one repeats the `workflowpermsfix` inert-grant failure. | `public.permissions` (28 rows, UNIQUE `(resource, action)`); `profile_permissions` / `permission_set_permissions` full DDL; `assistant_action_catalog.required_permission` (single key per action); `field_shaped_columns_in_perm_tables` = `[]` |
| B | Does anything already render a configurable field layout? | **NO — every field list, tab list and column span is hardcoded JSX.** Tabs: `EntityDetailTabs.jsx:23–33` literal array. Fields: `EntityDetailsForm.jsx:15,23,30` three const arrays. Layout: Tailwind `sm:grid-cols-2` (`:87`, `:152`) with `sm:col-span-2` overrides (`:141`, `:188`, `:245`, `:266`, `:270`). **What IS server-driven is editability** — the `permissions` + `vocabularies` envelope, honoured with no truthy fallback in `PositionsGrid.jsx:29–39, 259–269, 349, 443`. **Extend that envelope; do not invent a parallel one.** | files/lines as cited; `ReferenceSelect.jsx` + `routers/reference.py:10` for the server-driven picker precedent |
| C | Any existing append-only journal / audit table / history trigger recording old-new values? | **YES — six journals, ALL populated by application code; ZERO populated by a trigger.** Only 10 triggers exist in `public`+`portfolio` and every one is a guard or a validator, never a recorder. Closest structural match: **`public.document_field_corrections`** (29 rows) — already field-keyed (`field_name`) and polymorphic (`target_type`/`target_id`), with `original_value`/`corrected_value`. Generic mechanism: `public.audit_log` (88 rows) via `services/audit.py:85`, which introspects its own columns at runtime. Larger mechanism: **bi-temporality on 52 tables**, including both UDF tables. **A UDF value journal is an instance of an existing pattern, not a new one.** | `pg_trigger` full listing (10 rows); `/tmp/udf00_task3.json` → `C_journals`; `services/audit.py:3,18,110,139` |
| D | Does a tag concept already exist? `entity_document_tags` + `is_fixed`? Reusable? | **A tag TABLE exists; a reusable tag PATTERN does not.** `entity_document_tags`: 6 columns, UNIQUE `(document_id, tag)`, org-isolation RLS, **no CHECK, no vocabulary FK, not bi-temporal, hard-deleted**. Tags are **free-form**. Vocabulary is **not** org-scoped — there is no vocabulary table at all, only whatever strings exist on that org's rows. **`is_fixed` is write-only dead metadata**: hardcoded `false` at all three INSERT sites and read by zero queries repo-wide. The other two tag surfaces (`entities.tags`, `deals.tags`, both `text[]`) hold **zero tags**. | `entity_document_tags` DDL; `routers/entity_documents.py:127,199,292` (all `false`), `:298` (unconditional delete); repo-wide `is_fixed` grep = 3 INSERTs + 1 verify script |
| D′ | Does `reference_data` exist? What does it hold? Viable home for UDF value sets? | **YES it exists — 155 rows, 11 lists (`account_type`, `ca_province`, `country`, `currency`, `doc_category`, `ledger_basis`, `month`, `name_prefix`, `name_suffix`, `tax_character`, `us_state`), ALL global (`org_id IS NULL`), zero org-scoped rows.** Structurally excellent (`code`/`label`/`parent_code`/`display_order`/`is_active`/`extra`, asymmetric RLS letting tenants read but not write globals, a live API and renderer). **Genuinely a different concern as deployed** — canonical world facts, not tenant-authored lists. **Viable only with three changes:** an owner axis (it has no `owner_scope`/`owner_scope_id`), a binding to `udf_definitions.id`, and org-scoping the `list_key` uniqueness (`UNIQUE (list_key, code, parent_code)` is global, so two orgs collide on the same list name). | `reference_data` DDL + `reference_data_lists` distribution; policy `reference_data_global_or_org`; `routers/reference.py:10`; `components/ReferenceSelect.jsx`; `services/document_classifier.py:150` |

## Could not determine

Stated explicitly, per the sprint's instruction:

1. **How Phase G behaves with real data.** Both tables are empty. Every statement above about `udf_definitions` / `udf_values` behaviour is derived from DDL, policies, indexes and source code — **never from observed rows.** Confirming behaviour would require writing rows, which this read-only sprint did not do.
2. **Whether the `options` jsonb on-disk shape is a bare list or a wrapper object.** `_coerce_choice_list` accepts four shapes (bare list, `{"choices":…}`, `{"options":…}`, `{"values":…}`) and there is no CHECK and no stored row to settle which one is actually written. `investment_profile_questions` uses `{"options": [...]}`, but that is a different table.
3. **Whether per-field security should be new `permission_key` strings or a new binding table.** Both are consistent with the deployed schema. The database cannot decide this.
4. **Whether `is_fixed` was intended as "system-applied, user may not remove."** The name and the unguarded DELETE at `entity_documents.py:298` suggest it, but there is no comment, no doc, no consumer and no non-`false` row. The intent is genuinely unrecorded.
5. **Why `portfolio.udf_definitions` has no `updated_at`/`updated_by` and no update path.** Cannot tell from the evidence whether definitions were meant to be immutable-and-restated or whether the update path was simply never built.
6. **Whether the Phase G rationale exists anywhere outside the sprint log.** `docs/PORTFOLIO_REPORTING_DESIGN_V6.md:203` cites `PROJECT_STATUS.md §7o`, which does not exist. The design intent as written down is the phase-map row plus the docstrings in `portfolio_udf.py`.

---

## Summary for the Sprint 1a DDL decision

Phase G shipped a **real, well-constructed, and entirely unused** UDF layer: two bi-temporal, RLS-protected tables with four parallel owner namespaces, six target types, five data types, inline picklists, and a 997-line service module — behind **no API, no UI, and no writer**. It is closer to a completed foundation than to a false start.

The four things it does **not** have, all confirmed absent rather than assumed:

1. **No type parameters** of any kind.
2. **No permission binding** — not field-level, not tab-level, no join to any permission table.
3. **No layout metadata** beyond a single `display_order` that explicitly carries no meaning.
4. **No value history** — the bi-temporal columns exist and `record_udf_value` overwrites through them.

And two things it has that the surrounding code does not: a `COALESCE`-keyed **partial** unique index that actually works across four namespaces (versus `investment_profile_questions`' global unique, which makes restatement impossible and collides cross-tenant), and a scope-resolution predicate that joins real `team_members` membership.

The genuinely awkward finding is not in Phase G — it is that **this database now holds five independent implementations of "key, label, type, display_order, is_active"** (`config`, `reference_data`, `udf_definitions`, `investment_profile_questions`, `note_terms_field_registry`), with five different type vocabularies, three different ways to say "which parent does this apply to," and no shared code. `investment_profile_questions` + `investment_profile_answers` in particular is a fully-wired, fully-working UDF system for CRM entities that is not called one.
