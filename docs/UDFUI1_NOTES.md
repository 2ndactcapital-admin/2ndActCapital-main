# UDFUI1 — custom fields & tabs builder: notes

Sprint: `sprint_prompts/udfui1.structural.md` · branch `udfui1-work` · 2026-11-13.

## 1. Discovery

### 1a. Every UDF route — request, response, gate (apps/api/routers/udf.py)

All gates go through `_tenant_gate` → `services.rbac.require_permission` (Super Admin first, 403 naming the
permission). `READ` = `view_portfolio`, `WRITE` = `manage_portfolio`. `internal` = `require_internal` (BUGFIX3).

| Route | Gate | Body / query | Response |
|---|---|---|---|
| GET /udf/definitions | READ | `target_type` | `{rows, permissions, vocabularies}` — ACTIVE, non-deleted, current, visible to the caller (4 namespaces), FLS-hidden dropped |
| POST /udf/definitions | WRITE (platform scope: super admin) | `DefinitionCreateIn` (extra=forbid) | `{id, definition}` |
| PATCH /udf/definitions/{id} | WRITE | sparse `DefinitionUpdateIn` | `{definition}`; 422 (type change refused, `affected_rows`) |
| DELETE /udf/definitions/{id} | WRITE | — | soft delete; **409 `{message, references:{udf_values, udf_tag_assignments}}`** while anything references it |
| POST …/deactivate · reactivate · undelete | WRITE | — | `{definition}` |
| GET /udf/values/{type}/{id} | internal + READ | — | `{rows, permissions}` |
| PUT /udf/values/{type}/{id} | WRITE + field access = edit | `{definition_id, value}` | `{id, value, history}` |
| GET /udf/tags/{def} | READ | — | `{rows, permissions}` (vocabulary) |
| PUT /udf/tags/{def}/{target} | WRITE (+create_tags to mint) | `{codes}` | `{rows}` |
| POST /udf/tags/{def}/merge | WRITE | `{from_code, into_code}` | `{targets_repointed}` |
| GET /udf/tabs | READ | `applies_to` | `{rows, permissions, vocabularies}` — ACTIVE, visible to the caller |
| POST /udf/tabs | WRITE | `{applies_to, label, api_name, display_order}` | `{tab}`; cap `crm.udf.max_custom_tabs` |
| PATCH /udf/tabs/{id} | WRITE | `{label}` (api_name immutable) | `{tab}` |
| DELETE /udf/tabs/{id} | WRITE | — | 409 with references while the layout is non-empty |
| POST /udf/tabs/{id}/deactivate · reactivate · undelete | WRITE | — | `{tab}` |
| PUT /udf/tabs/{id}/permissions | WRITE | `{profile_id \| permission_set_id, is_visible}` | `{permission}` (upsert) |
| PUT /udf/fields/{id}/permissions | WRITE | `{profile_id \| permission_set_id, access: hidden\|read\|edit}` | `{permission}` (upsert) |
| GET /udf/fields/{id}/permissions | WRITE | — | `{rows}` |
| GET /udf/layouts/{tab} | READ (403 if tab hidden for the caller) | — | `{layout_id, sections[items], rows, permissions, vocabularies}` |
| POST /udf/layouts/{tab}/sections | WRITE | `{title, column_count 1\|2, display_order}` | `{section}` (layout created lazily) |
| DELETE /udf/layouts/{tab}/sections/{s} | WRITE | — | 409 while it has items |
| POST /udf/layouts/{tab}/sections/{s}/items | WRITE | `{definition_id, display_order, column_index 0\|1, col_span 1\|2, is_read_only}` | `{item}` |
| PATCH /udf/layouts/{tab}/items/{i} | WRITE | sparse `{section_id, column_index, col_span, display_order}` | `{item}` |
| DELETE /udf/layouts/{tab}/items/{i} | WRITE | — | `{deleted}` |
| GET /udf/records/{type} | internal + READ | `tab_id, filter (JSON), sort, limit, offset` | `{rows, columns, total_count, permissions, vocabularies}` |
| GET /udf/records/{type}/export | internal + READ | `tab_id, filter` | text/csv |
| POST /udf/records/{type}/import | WRITE | multipart | (not used by this sprint's UI) |

### 1b. Definition schema options (services/portfolio_udf.py)
* `applies_to` (= target type): asset, position, valuation, transaction, commitment, entity (the deployed CHECK).
  Entity kinds are NOT a separate target — one `entity` target covers every entity type.
* `data_type`: text, long_text, rich_text, integer, numeric, currency, percent, date, datetime, boolean, select,
  multiselect, tags, email, url, phone.
* `type_params` REQUIRED per type: text/long_text/rich_text/email/url/phone → `length`; integer → `precision`;
  numeric/percent → `precision, scale`; currency → `precision, scale (=4 fixed), currency_code`;
  select/multiselect → `value_set_id` (a `public.reference_data_lists` id); OPTIONAL `min`/`max` on the numeric
  types. Unknown params are refused.
* Other options: `label`, `field_key` (immutable key), `api_name` (immutable once set), `help_text`, `description`,
  `is_required`, `default_value`, `is_unique`, `unique_case_sensitive`, `is_external_id`, `display_order`, `options`.
* Type change: widening only (text→long_text, integer→numeric, select→multiselect); narrowing a bound is refused
  when stored values would violate it (422 with `affected_rows`); scale may never decrease.
* Owner scope: platform (super admin only) / org / team / user. The builder creates **org** scope; platform rows
  are shown read-only unless the caller is Super Admin.
* Lifecycle: deactivate (values kept, no new writes, hidden from resolution) ↔ reactivate; soft delete REFUSED
  (409) while any value or tag assignment exists — "deactivate instead" — ↔ undelete. Every lifecycle change
  writes a `udf_definition_audit` row (`change_kind`, `before_state`, `after_state`, `changed_by`, `changed_at`).

### 1c. Permission model
* Gate for the whole builder = **`manage_portfolio`** (every write route's own gate). The screen renders only for
  a caller whose server envelope says `can_write`; others see nothing (menu gated `internal + manage_portfolio`,
  strict; the page 403s server-side).
* Tab visibility (`udf_tab_permissions`) and field access (`udf_field_permissions`) bind to **profiles** and
  **permission sets** (`public.profiles` / `public.permission_sets`) — not roles. Most restrictive wins across the
  two paths; no grant = visible / edit (display default). Tab hidden ⇒ every field on it hidden.
* Resolution: `resolve_tab_visibility`, `resolve_field_access(_bulk)` — the preview calls exactly these.

### 1d. Existing components reused
`components/ui/DataGrid.jsx` (TanStack + @dnd-kit for column order), `components/ui/Busy.jsx` (`BusyButton`,
`BusyError`, `useBusy`), `lib/busyAction.mjs` (`jsonOrThrow`), the profile / permission-set lists from
`GET /admin/profiles` / `GET /admin/permission-sets` (their own gate, manage_org_settings), the user list from
`GET /admin/users` (manage_members) for "preview as". `@dnd-kit/core` + `@dnd-kit/sortable` are installed and used
by DataGrid, so the layout editor uses them too, with up / down buttons as the keyboard-accessible equivalent.

### 1e. Entity write path + status validation
* The ONE write path: `services/entity_writes.update_entity_row` (system-axis archive: prior version copied with
  `system_to = now()`, live row keeps its id) followed by `services.audit.write_audit_log` — exactly what
  `PUT /entities/{id}` does (routers/entities.py ≈ 849).
* Status vocabulary: `public.config` category `crm_entity_statuses` (`crm_status_<key>`, `value_type`
  selectable / system) → `services/crm_config.build_vocab`; validation `check_entity_values` accepts a SELECTABLE
  status or the record's unchanged current one. Live labels (default org): Prospect, Client, Contact, Active,
  Inactive, Archived, Deceased (system), Partner, Former client — already readable.
* Live current rows (2026-11-13, valid_to/system_to NULL): prospect 59, client 14, contact 10, **active 3**,
  deceased 1. (The prompt's 87 / 18 counts include archived system-axis versions.)

## 2. Decisions

* **New read routes** (the builder needs what the visible-only reads hide; all `internal + manage_portfolio`,
  i.e. the builder's own write gate — nothing loosened):
  `GET /udf/admin/definitions?target_type=` (every org + platform definition incl. deactivated / deleted, with
  used-by counts, layout placements, value sets, type-param rules), `GET /udf/definitions/{id}/audit`,
  `GET /udf/admin/tabs?applies_to=` (incl. deactivated / deleted), `GET /udf/tabs/{id}/permissions`,
  `GET /udf/preview?user_id=&target_type=` (the existing resolution, for a chosen user of the org).
* **New write route**: `PUT /udf/layouts/{tab}/sections/order` over the existing `reorder_sections` (no route
  called it). Tab order: `PATCH /udf/tabs/{id}` additionally accepts `display_order` (sparse).
* Status 'active': **not offered** in pickers (`selectable: false`, `retired: true` in the envelope), still
  **accepted** by validation (`check_entity_values` accepts selectable ∪ retired) — a code constant
  `RETIRED_STATUSES = ("active",)` next to `STATUS_DECEASED`; label still from config. No config row rewritten.
* Clean-up script `apps/api/scripts/relationship_status_cleanup.py`: dry run lists; real run moves each 'active'
  entity to 'client' through `update_entity_row` + `write_audit_log` in one transaction per entity, under that
  entity's org RLS context; 'contact' untouched; reports what still carries 'active'.

* **The screen** `app/admin/custom-fields/page.js` (menu: Firm settings → "Custom fields & tabs", gate
  `{perm: manage_portfolio, strict}` — no staff bypass, because the API has none). The page reads the two admin
  envelopes server-side; a 403 or an envelope without `can_write === true` renders no builder.
  Six sections (`components/admin/udf/`): Fields, Tabs & layouts (layout editor inside), Permissions & preview, Tags,
  Change history, Records. Every control is an action in `lib/udfBuilder.mjs UDF_ROUTES` (one FastAPI route each);
  the browser reaches them only through `app/api/udf/builder/[...path]` which forwards ONLY listed (method, path)
  pairs and strips any `org_id`. Labels (record types, field types, statuses, access levels, change kinds) are
  published by the server in `vocabularies.labels` (Rule 1).
* **Not built** (no route exists and the sprint said stick with existing constructs): removing a grant row, creating
  a value set, CSV import UI (the import route exists; the builder's scope is records view + export).
* **nav1's menu test** pinned "the only new href since NAV1 is /treasury". It now carries an explicit `LATER` list
  (`/admin/custom-fields`, sprint udfui1, group settings, its exact gate); every other NAV1 assertion is unchanged
  and an unlisted new href still fails.

## 3. Findings
* [FIND] The live `org_admin` role carries no `manage_portfolio` (only `admin`, `advisor`, `super_admin` roles do in
  the default org) — an org admin without it gets no builder. Gate not widened; grant it if intended.
* [FIND] `undelete_definition` audits its change as `change_kind = 'reactivate'` (not 'undelete'); the audit view
  shows the stored kind verbatim.
* [FIND] No route removes a tab / field grant (only upsert); the UI can set a grant to visible / edit but not delete
  the row.
* [FIND] No route creates a value set (`reference_data_lists`); a picklist field picks an EXISTING list.
* [FIND] `GET /udf/layouts/{tab}` resolves FLS for the caller, so an admin whose own grant hides a field does not
  see it in the layout editor; the admin definitions list still shows it.
* [FIND] Tab lifecycle has no audit table (documented in portfolio_udf_tabs) — only definitions are audited.
* [FIND] A DEACTIVATED field's layout item is still returned by `GET /udf/layouts/{tab}` (`get_resolved_layout` filters
  `deleted_at` only, not `is_active`); value writes to it are refused (422). Engine behaviour, unchanged.
* [FIND] A malformed id in a UDF path (e.g. the literal `None`) is a 500, not a 404 / 422 (asyncpg DataError) —
  pre-existing route behaviour, seen while a verify defect passed a missing tab id.
* [FIND] Pre-existing web test failure: `tests/bugfix3` reads `components/crm/EntityDetailTabs.jsx`, which NAV3 removed
  (full web suite 909 pass / 1 fail / 1 skip). Not touched here.
* [FIND] `verify_udf00.py` does not exist; `verify_udf01a…02b` have no `--self-checks-only`, so fast mode cannot nest
  them (as NAV3 recorded). This sprint's verify proves the engine paths it uses through the real app instead.

## 4. Proof
* `verify_udfui1.py`: run 1 170 / 13 (both verify defects: a 4th tab hit the engine's cap of 3; creation is audited
  too) → run 2 185 / 0 (VERIFY_NESTED=1) → full run **186 / 0** (incl. nested verify_nav3). `--self-checks-only`
  16 / 0; `--cleanup-only` 1 / 0.
* Node: `tests/udfui1` 19 / 0; with nav3 / nav2 / nav1 / nav 109 / 0.
* One-offs: `verify_nav3.py --self-checks-only` 25 / 0, `verify_nav1.py --self-checks-only` 28 / 0;
  `authz1_routes.py --check` and `visibility1_routes.py --check` CURRENT (both regenerated for the 6 routes).
* Not run (by instruction): `npm run build`, other sprints' full verifies.

## 5. Operator step
    doppler run -- apps/api/venv/bin/python apps/api/scripts/relationship_status_cleanup.py --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/relationship_status_cleanup.py
Expect 3 entities (live rows with status 'active' on 2026-11-13). The script ends with how many still carry
'active'; once that is 0, a later sprint may drop 'active' from validation (`RETIRED_STATUSES`).
