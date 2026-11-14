# NAV3 — one Entities record page

Branch `nav3-work` (base `96e9bf2`, merge nav2). Phase 3 of the navigation redesign (nav1 = the menu, nav2 = the
entity chip + Ctrl K). Source: `sprint_prompts/nav3.structural.md`.

## 1. Discovery (Task 1)

### 1a. The record page today
* `app/crm/[id]/page.js` (server) reads `GET /entities/{id}/full` (**internal** — STAFFROLES1 `require_internal`;
  BUGFIX2 made the full record staff-only), `GET /entities/{id}/ownership-graph` (internal), the VISIBILITY1 team
  panel, and renders `components/crm/EntityDetailTabs.jsx` (13 flat tabs). A member never gets this page (the `/full`
  read 403s → error) — NAV3 keeps that: **members are unaffected and see no record page** (their own family's data
  stays on `/portfolio` / My accounts). Never widened.
* Sub-routes kept as they are: `/crm/[id]/hierarchy`, `/crm/[id]/ownership-graph`, `/crm/[id]/targets`.
* Decision: the record page stays at **`/crm/[id]`** (no new path → nothing to redirect; every existing link, the
  chip's `entityDestination`, Ctrl K, `?tab=` deep links keep working; old tab keys are mapped to the new tab that
  contains them).

### 1b. Each tab's existing data path (reuse — move, don't rebuild)

| Tab | Existing component(s) | Existing API(s) | Gate (AUTHZ1_ROUTES) | as-of |
|---|---|---|---|---|
| Overview | `EntityDetailsForm` (read view), `OwnershipTree` up/down, `AttributesSection`, Client Brief link | `/entities/{id}/full`, `/ownership-graph`, `/entities/{id}/net-worth` | internal; net-worth = entity access | net-worth card via the split route (as-of); the rest "Shows today" |
| Holdings | `HoldingsTab` (direct assets + private investments), `LiabilitiesTab` (net worth + liabilities) | `/entities/{id}/direct-assets`, `/private-investments`, `/liabilities`; **new** `GET /entities/{id}/holdings-split?as_of` (services.networth — see 1c) | member scope / entity access | split: yes (`as_of` → `networth.load(as_of=)`); direct-assets / liabilities: today |
| Transactions | the Transactions grid's API | `GET /portfolio/transactions?owner_entity_id&trade_to` | view_portfolio (+ member scope ANDed) | yes — `trade_to = as_of` |
| Allocation & X-ray | allocation lens view; `XrayView` for the primary household | `GET /allocation-lens?selector_type=entity&entity_id&as_of`; `GET /xray/households/{primary_household_id}` | entity access; view_replication | lens: yes; X-ray: today |
| Cash & Treasury | `TreasuryPanel` | `GET /cash/households/{primary_household_id}/treasury` | any(view_portfolio\|manage_portfolio) + member scope | today |
| Models & rebalancing | `AccountsList` | `GET /proposals/accounts?owner_entity_id` (**new narrowing param**, `a.primary_entity_id = $n`, Task 6) | view_proposals | today |
| Ownership & relationships | `OwnershipTab`, `RelationshipsPanel`, hierarchy link | `/entities/{id}/ownership?as_of`, `/crm-relationships` | all auth (ownership), internal | ownership: yes (valid-time slice; read-only when as_of set — existing rule) |
| Details | `EntityDetailsForm` (edit), `DetailFieldsPanel`, Addresses / Employment / Tax IDs / Social / Compliance tabs, **UDF** custom tabs | `/full`, `/entities/{id}/details`, `/udf/tabs?applies_to=entity`, `/udf/layouts/{tab}`, `/udf/values/entity/{id}`, PUT `/udf/values/entity/{id}` | internal; UDF read = internal + view_portfolio, write = manage_portfolio + field access 'edit' | today |
| Documents | `DocumentsTab`, `DocumentsPanel` | `/entities/{id}/documents` (both routers) | entity access | today |
| Tasks & activity | `NotesTab` | `/entities/{id}/notes` | internal | today |

### 1c. Look-through (rollup2 "one net-worth core")
`services/networth.py`: `load(conn, org, roots, as_of=)` reads the edges over BOTH ownership tables and values every
live position of every entity reached; `figures(ctx, roots, weights=)` counts each line at its owner's share.
`entity()` = the entity's card (its own positions at share 1 + its share of everything it owns).
**Split, no second calculation:** ONE `ctx = load(...)`; `direct = figures(ctx, weights={e: 1})`;
`through = figures(ctx, weights=shares({e}) minus e)`. The two weight maps are disjoint and their union is exactly
the card's weights, so `direct + through = entity card` to the cent (same lines, same values, same as-of) and no
line is counted twice. The through-ownership list shows each owned entity with its percentage (`share`).
Note: the network a household reaches can contain the same entity under two members — `shares()` already caps at 1.

### 1d. As-of support per API
Yes: allocation-lens (`as_of`), positions (`value_as_of`), transactions (`trade_to`), ownership (`as_of`), the new
split (`as_of`). No (today only): `/full`, net-worth (no param), direct-assets, private-investments, liabilities
list, X-ray, treasury, proposals/accounts, documents, notes, UDF values. Those tabs say **"Shows today"** next to the
date control when the date is not today.

### 1e. UDF read paths + permission resolution
`GET /udf/tabs?applies_to=entity` (`resolve_tab_visibility`, hidden tabs never returned), `GET /udf/layouts/{tab}`
(403 if hidden), `GET /udf/values/entity/{id}` (internal + view_portfolio; hidden fields dropped by
`resolve_field_access_bulk` with tab_id=None). Writes: `PUT /udf/values/entity/{id}` (manage_portfolio, then
`resolve_field_access` must be 'edit' → else 403). Values are bi-temporal (system axis) — every edit leaves a row in
`get_value_history` (the audit). Read-only vs editable per field is the layout item's resolved access
(`get_resolved_layout` carries it); the page renders an input only for 'edit' AND `permissions.can_write`.

### 1f. Where entities.status is enforced
**No DB CHECK or enum** on `entities.status` (pg_constraint: none). It is enforced in the app by
`services/crm_config.check_entity_values` against the org's `public.config` rows of category `crm_entity_statuses`
(keys `crm_status_<key>`; `value_type` 'selectable' or 'system'). Rows exist for the 2nd Act org and the Hollisworks
org; `crm_seed_org_config` copies the default org's rows into an org that has NONE. So the vocabulary extension is a
config migration: `crm_status_partner` "Partner" and `crm_status_former_client` "Former client" (selectable) into
every org that already has `crm_entity_statuses` rows (ON CONFLICT DO NOTHING) — new orgs inherit from the default.
Live current rows (valid_to / system_to NULL, 2026-11-13): prospect 59, client 14, contact 10, active 3, deceased 1
(the sprint's 87 / 18 counts include closed history rows). **No row rewritten.**

### 1g. nav2's screen table
See `docs/NAV2_NOTES.md` §3. The record page needs: accounts (Models tab → owner filter added), treasury and X-ray
(entity → `entities.primary_household_id` → existing household APIs). Rest in §4 below.

## 2. Tab × entity type × role matrix

The page itself needs `/full` = internal. Persona "member" → no page (404-equivalent, unchanged); "other org" → 404
(RLS / org scope, unchanged). Each tab is shown only when its API serves the person — decided from the gate, and at
render time a refused read hides the tab (never an empty tab pretending to be data).

| Tab | Entity types | support (internal, scoped) | advisor (internal) | org admin | Super Admin |
|---|---|---|---|---|---|
| Overview | all | ✓ | ✓ | ✓ | ✓ |
| Holdings | all except `household` (a household entity owns through members — shown as Through ownership) — shown for all; empty is honest | ✓ if entity access | ✓ | ✓ | ✓ |
| Transactions | all | only with view_portfolio | only with view_portfolio | ✓ | ✓ |
| Allocation & X-ray | all; X-ray section only with a primary household | lens: entity access; X-ray: view_replication | same | ✓ | ✓ |
| Cash & Treasury | entities with a primary household | view_portfolio or manage_portfolio | same | ✓ | ✓ |
| Models & rebalancing | individual, trust, llc/partnership/corp types, account (anything that can be an account's primary entity) | view_proposals | view_proposals | ✓ | ✓ |
| Ownership & relationships | all | ✓ | ✓ | ✓ | ✓ |
| Details | all (Employment only for individuals; typed detail fields only for non-individuals — existing rule); UDF tabs only with view_portfolio | ✓ | ✓ | ✓ | ✓ |
| Documents | all | ✓ | ✓ | ✓ | ✓ |
| Tasks & activity | all | ✓ | ✓ | ✓ | ✓ |

The exact type sets live in ONE table, `apps/web/lib/recordTabs.mjs` (tested in node).

## 3. What was built
(filled as tasks land)

## 4. Screens still not entity-filtered
(filled in Task 7)
