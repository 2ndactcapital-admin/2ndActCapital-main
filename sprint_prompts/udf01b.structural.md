# Sprint udf01b — Tabs, Tab Permissions, Layout Model (STRUCTURAL)

**Tier:** `.structural` — held for manual merge review.
**Database:** Supabase project `mmgwmcinimzuhargsazs`.
**Branch:** `sprint/udf01b-structural`, cut from `main` after Sprint 1a merged (main now includes 1a — 119/119 PASS).
**Predecessors:** `docs/discovery/UDF_DISCOVERY_REPORT.md` (udf00), Sprint 1a (`apps/api/services/portfolio_udf.py`, `apps/api/routers/udf.py`, `apps/api/scripts/verify_udf01a.py`).

## Part 1 status — already applied, do not re-run

The Part 1 DDL for this sprint (tabs, tab permissions, layout tables) has already been applied directly and confirmed to exist via `to_regclass` on all five objects:

- `portfolio.udf_tabs`
- `portfolio.udf_tab_permissions`
- `portfolio.udf_layouts`
- `portfolio.udf_layout_sections`
- `portfolio.udf_layout_items`

Do not attempt to create these tables. Task 1 below re-verifies their exact column shape before you write code against them — treat that as the source of truth, not this document's prose.

---

## Before writing any code — mandatory re-verification

Sprint 1a's Task 1 caught three real schema-mismatch errors in a hand-written Part 1 because the prompt author trusted the discovery report's prose instead of querying live columns directly. Do not repeat that.

```sql
select table_name, column_name, data_type from information_schema.columns
 where table_schema='portfolio'
   and table_name in ('udf_tabs','udf_tab_permissions','udf_layouts','udf_layout_sections','udf_layout_items')
 order by table_name, ordinal_position;

select indexname, indexdef from pg_indexes
 where tablename in ('udf_tabs','udf_tab_permissions','udf_layouts','udf_layout_sections','udf_layout_items');

select conname, pg_get_constraintdef(oid) from pg_constraint
 where conrelid::regclass::text like 'portfolio.udf_tab%'
    or conrelid::regclass::text like 'portfolio.udf_layout%';

select column_name, data_type from information_schema.columns
 where table_schema='portfolio' and table_name='udf_definitions'
 order by ordinal_position;
```

Confirm every column and constraint referenced below matches exactly. If anything doesn't match, **stop and report** rather than silently substituting.

---

## Scope boundary

**In scope:** tabs, tab CRUD, tab permissions (both `profile_permissions` and `permission_set_permissions` paths), the three-table layout model, layout CRUD, the layout-serving endpoint, extending the `PositionsGrid` envelope pattern to serve tab+layout metadata.

**Explicitly out of scope:**
- Field-level security (`udf_field_permissions`) — Sprint 1c. Tab hidden ⇒ all fields hidden is achievable now; tab visible ⇒ FLS decides per field is NOT — until 1c ships, tab-visible means all fields in it are visible, full stop. State this limitation plainly in the router docstring so nobody assumes FLS exists.
- DataGrid columns, list filters, CSV import — Sprint 2.
- Record types — reserved columns only, no UI, no logic.
- Migrating the hardcoded `EntityDetailTabs.jsx` / `EntityDetailsForm.jsx` frontend — that is explicitly a follow-on once this backend exists and Joe has reviewed it. Backend only this sprint.

Report anything you skipped and why.

---

## TASK 1 — Discovery re-verification

Run the re-verification queries above. Additionally:

- Re-read `PositionsGrid.jsx:29–39, 259–269, 349, 443` and report the exact shape of the `permissions` + `vocabularies` envelope it consumes, field by field. The layout-serving endpoint in Task 3 must produce a structurally compatible envelope — not a new shape that happens to look similar.
- Re-read `EntityDetailTabs.jsx:23–33` and `EntityDetailsForm.jsx:15,23,30` and quote the current hardcoded arrays verbatim, so the eventual frontend migration (not this sprint) has an exact behavioral target to match.
- Confirm `public.profiles.id` and `public.permission_sets.id` are both `uuid` — already confirmed once, re-confirm against live schema regardless.
- Confirm whether `portfolio.udf_definitions.record_type_id` (added in 1a) is genuinely still unused anywhere. Report if anything already reads it.
- Re-run `apps/api/scripts/verify_udf01a.py` in full and report its result verbatim. This is the regression baseline for this sprint — if it does not show 119/119 (or whatever the current count is) before you touch anything, stop and report rather than proceeding.

Report all findings before proceeding to Task 2.

---

## TASK 2 — Tab and layout service layer

**2a — org_settings additions.** Add these two keys to `DEFAULT_SETTINGS` in `apps/api/services/org_settings.py`, following the exact pattern of the six existing `crm.udf.*` keys added in 1a:

```python
"crm.udf.max_sections_per_layout": 10,
"crm.udf.max_items_per_section": 20,
```

Confirm the `"crm.udf.": "crm"` category-prefix mapping already present still covers these two — it should, since it's a prefix match, but verify rather than assume.

**2b — Tab CRUD.** `create_tab`, `update_tab` (label only — `api_name` immutable, same rule as definitions in 1a), `deactivate_tab`, `reactivate_tab`, `soft_delete_tab`. Enforce `crm.udf.max_custom_tabs` from `org_settings` at creation — reject the (N+1)th active tab for a given `(org_id, applies_to)` with a clear error naming the current count and the limit.

**2c — Tab permissions.** `set_tab_visibility(tab_id, profile_id=None, permission_set_id=None, is_visible)` — exactly one of the two grantee params, matching the CHECK constraint. `resolve_tab_visibility(tab_id, user_context)` must check both paths and combine with most-restrictive-wins logic: if either an applicable profile grant or an applicable permission-set grant says hidden, it's hidden. If no grant row exists at all for a tab, default to visible — confirm this matches how the rest of RBAC treats an absent grant, and flag explicitly if it doesn't.

**2d — Layout CRUD.** `create_layout`, `add_section`, `reorder_sections`, `add_item`, `move_item` (change section/column/order), `remove_item`. `col_span=2` is only valid for `data_type IN ('long_text','rich_text')` on the referenced definition — validate this by joining to `udf_definitions`, don't trust the caller. A spacer item (`definition_id IS NULL`) may have any `col_span`. Enforce `max_sections_per_layout` and `max_items_per_section` from `org_settings`.

**2e — Layout resolution for rendering.** `get_resolved_layout(tab_id, org_id, record_type_id=None)` returns the full section→item tree, each item enriched with its definition's `label`, `data_type`, `type_params`, `is_required` — everything the frontend needs to render without a second round-trip. This is the function the Task 3 endpoint calls directly.

---

## TASK 3 — Endpoint and verification

**3a — Router additions** in `apps/api/routers/udf.py` (extend the existing file from 1a, do not duplicate or recreate it):

```
GET    /udf/tabs?applies_to=              -> tabs visible to the caller, per resolve_tab_visibility
POST   /udf/tabs
PATCH  /udf/tabs/{id}
DELETE /udf/tabs/{id}                      -> soft delete
POST   /udf/tabs/{id}/deactivate
POST   /udf/tabs/{id}/undelete
PUT    /udf/tabs/{id}/permissions          -> body: {profile_id|permission_set_id, is_visible}
GET    /udf/layouts/{tab_id}               -> resolved layout, PositionsGrid-compatible envelope
POST   /udf/layouts/{tab_id}/sections
POST   /udf/layouts/{tab_id}/sections/{section_id}/items
PATCH  /udf/layouts/{tab_id}/items/{item_id}
DELETE /udf/layouts/{tab_id}/items/{item_id}
```

`GET /udf/tabs` must never return a tab the caller's permission grants say is hidden — filter server-side, same non-bypassing principle as RLS. `GET /udf/layouts/{tab_id}` on a hidden tab returns 403, not an empty layout.

**3b — `verify_udf01b.py`**, same pattern and rigor as `verify_udf01a.py`: real writes, real RLS checks, real HTTP calls via `TestClient`, teardown to baseline confirmed by row count on every touched table. No interactive prompts. Report BLOCKED explicitly for anything that can't be verified, never silently omit it.

Assertions:

- [ ] Column/constraint re-verification from Task 1 matched expectations (or deviations were reported and handled)
- [ ] `verify_udf01a.py` re-run and passes at its established baseline — this is a hard gate, not informational
- [ ] `max_custom_tabs` enforced from `org_settings`; creating the (N+1)th active tab is rejected with the count and limit named
- [ ] `api_name` on a tab is immutable; `label` is mutable
- [ ] Tab soft-delete hides it from `GET /udf/tabs` and is reversible
- [ ] Tab soft-delete blocked when the tab has a non-empty layout, with the reference reported
- [ ] `resolve_tab_visibility`: profile-level hidden wins even when permission-set-level says visible (negative case)
- [ ] `resolve_tab_visibility`: permission-set-level hidden wins even when profile-level says visible (the other negative case — both directions tested independently)
- [ ] `resolve_tab_visibility`: no grant row at all defaults to visible, and this is confirmed consistent with RBAC's treatment elsewhere
- [ ] `col_span=2` accepted for a `long_text` definition, rejected for a `text` definition
- [ ] A spacer item (`definition_id NULL`) can be created and appears in `get_resolved_layout` output
- [ ] `max_sections_per_layout` and `max_items_per_section` enforced from `org_settings`, not constants
- [ ] `get_resolved_layout` output is structurally compatible with what `PositionsGrid.jsx` consumes — assert the actual key names match, not just "similar shape"
- [ ] `GET /udf/tabs` excludes a tab hidden for the caller's permission set
- [ ] `GET /udf/layouts/{tab_id}` on a hidden tab returns 403
- [ ] RLS: org A cannot read org B's tabs, tab permissions, layouts, sections, or items — assert the empty result explicitly
- [ ] Every router endpoint: 403 without the relevant permission, success with it
- [ ] Teardown: every touched table (`udf_tabs`, `udf_tab_permissions`, `udf_layouts`, `udf_layout_sections`, `udf_layout_items`, plus every 1a table) returns to its exact pre-sprint row count

Do not merge. Do not push until every assertion is PASS, FAIL, or explicitly BLOCKED with a stated reason — no silent omissions, no placeholder completion text. **If a turn is about to end without the verify script having actually been executed, do not end the turn.** This exact failure occurred once already in Sprint 1a and cost several retries to catch: writing the verify script is not the same as running it, and a description of intent to run it is not a result.
