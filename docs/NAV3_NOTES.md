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

1. **Record page** at `/crm/[id]` (`app/crm/[id]/page.js` → `components/record/RecordPage.jsx`; pure views in
   `components/record/RecordViews.jsx`; the ONE tab table `lib/recordTabs.mjs`). Navy header: name, type, the
   relationship-status badge (label from the CRM vocabulary envelope), one line of context (primary household ·
   owners), and the as-of date control (`?as_of=`, default today, never after today; "Back to today"). Accessible
   tabs: `role=tablist / tab / tabpanel`, `aria-selected`, `aria-controls` / `aria-labelledby`, roving tabindex,
   ArrowLeft / ArrowRight (wrap), Home / End. Each tab is mounted the first time it is opened. Old `?tab=` keys
   (relationships, addresses, tax_ids, notes, liabilities …) open the new tab containing them. The old flat
   `components/crm/EntityDetailTabs.jsx` was REMOVED — its views moved in, nothing rebuilt. Sub-routes
   (`/hierarchy`, `/ownership-graph`, `/targets`) unchanged.
2. **Holdings** — `GET /entities/{id}/holdings-split?as_of` (`routers/crm_detail.py`, the SAME gate as GET
   `/net-worth`: `_member_scoped_entity`; VISIBILITY1 path guard covers it) → `services/networth.entity_split`: one
   `load`, `figures` over two disjoint weight maps. "Held directly" / "Through ownership" switch; the through view lists
   each owned entity with its percentage. Below it, the existing direct-asset / private-investment entry
   (`HoldingsTab`) and liabilities (`LiabilitiesTab`) — those show today.
3. **One as-of date** — passed to the split (`as_of`), Transactions (`trade_to`), Allocation lens (`as_of`) and
   Ownership (its own as-of control, seeded with the page date). Every other tab shows "Shows today — this tab has no
   as-of date" when the date is not today, and the header lists them ("Shows today: …").
4. **Details** — the typed CRM fields (record form, typed detail fields, attributes, addresses, employment, tax IDs,
   social, compliance — the same components) + every visible UDF custom tab for `entity`: `GET /udf/tabs` →
   `GET /udf/layouts/{tab}` (hidden fields dropped, read → `is_read_only`) + `GET /udf/values/entity/{id}`. An input is
   rendered only for an item that is not read-only AND when the layout envelope says `can_write` (no fallback); Save
   = `PUT /udf/values/entity/{id}` (its existing gate + field access; each edit is a history row). New Next routes
   forward only `{definition_id, value}`.
5. **Relationship status** — `docs/nav3_part1.sql` (APPLIED 2026-11-13): config rows `crm_status_partner` "Partner"
   and `crm_status_former_client` "Former client" (selectable) for both orgs that have the category; new orgs copy
   them from the default org. No entity row touched. Filters: entity chip picker + Ctrl K Records (`status` on
   `GET /entities/search`, ANDed with the existing scope; options from `/crm/vocabularies`, hidden when the person
   gets none); the Entities list already filtered by status (PORTENTRY1 pills from config — the two new statuses
   appear there automatically). **Cross Book**: Positions and Transactions default to **Clients only** for staff
   (`owner_status=client`, a new narrowing parameter on both APIs, `services.portfolio_positions.OWNER_STATUS_SQL`)
   with a visible "Clients only / All relationships" switch; a member is unaffected (no switch, no parameter); a
   chosen owner (chip / Owner filter) wins over the default; the grid waits for `/users/me` so it never fetches the
   wrong population first.
6. **nav2 screens wired for the record page** — Accounts (`GET /proposals/accounts?owner_entity_id`, a narrowing
   `a.primary_entity_id` predicate) for Models & rebalancing; Treasury and X-ray through the entity's primary
   household (`GET /admin/entities/{id}/primary-household`, existing). Positions / Transactions / Allocation lens
   already filtered (nav2).

[FIND]s
* Members never had (and still do not have) a record page: `GET /entities/{id}/full` is internal (BUGFIX2).
* The household lookup needs manage_members, so support staff / advisors without it get no household line and no
  Cash & Treasury tab, although the treasury API would serve them. More restrictive; the lookup's gate unchanged.
* The household strict networth (`/admin/households/{id}/networth`) reads the stored snapshot; the live household
  total is `GET /admin/households/{id}` (BUGFIX4 `household_total`) — the reconciliation uses the live one.
* `udf/values` resolves field access with `tab_id=None` while `udf/layouts` resolves per tab; the page renders from
  the layout and the PUT's own 403 stays authoritative.

## 4. Screens still not entity-filtered (after NAV3)

From NAV2_NOTES §3, still "isn't filtered by entity": `/portfolio/securities`, `/portfolio/commitments`,
`/portfolio/households` (list), `/portfolio/ownership-tree` (member tree; staff use the record's Ownership tab),
`/portfolio/approvals` (households.id), `/portfolio/replication/tilts` (account-scoped), `/portfolio/replication/
backtest`, `/portfolio/tax`, `/portfolio/replication/section351`, `/portfolio/instructions`, `/treasury` (the page;
the record tab reads it by household), `/documents`, `/documents/semantic`, and the Investments tab of `/portfolio`.
`/portfolio/accounts` gained the server parameter (the record tab uses it) but the screen itself is not yet in the
FILTER set (its list component has no owner control) — a small follow-up. By design: models, catalog, replicas,
blocks, notes, new issues.

## 5. For Joe

* **Status mapping**: 'active' (3 current rows) and 'contact' (10) are kept as they are. Keep both, or map 'active' →
  client / partner and 'contact' → prospect / partner? Nothing was rewritten; the mapping is your call (a one-line
  UPDATE per decision through the bi-temporal path).
* **Next sprint: udfui1** — the admin builder for custom tabs / sections / fields (this sprint only SHOWS them).

## 6. Proof

* Run 1 (2026-11-13, VERIFY_NESTED=1): 96 PASS / 2 FAIL — both VERIFY defects, no code changed: (1) the UDF fixture
  definition omitted `type_params.length`, which the service requires for `data_type='text'` (422 — the existing
  validation working); (2) the NAV3_NOTES self-check ran against HEAD before the notes commit. Teardown: zero fixture
  rows; every counted table at its pre-test count.
* Run 2 (VERIFY_NESTED=1): **119 PASS / 0 FAIL**. Run 3 (full, VERIFY_REGRESSIONS=fast): **123 PASS / 0 FAIL** —
  fixtures in two orgs, staff visibility enforced in org A; teardown zero fixture rows, 18 counted tables at their
  pre-test counts. Sections: tab APIs per persona (6 personas = the matrix), holdings split = net-worth card and the
  household total (direct sums; the naive card sum double-counts exactly the through part), as-of honoured / future
  refused, UDF hidden / read / edit + audit + 403s, status vocabulary + real rows unchanged, Cross Book = SQL, accounts
  owner filter = SQL, static, matrices CURRENT, node tests.
* `--self-checks-only` 25/0. Node: tests/nav3 21/0; with nav2 + nav1 + nav 90/0.
* Fast regressions (nested --self-checks-only): verify_nav1 28/0, verify_nav2 17/0, verify_rollup2 49/0,
  verify_visibility1 56/0 (it was 55/1 mid-sprint: its Task 4 needle wants the Team panel in `app/crm/[id]/page.js` —
  fixed by rendering it there again and handing it to the record page, assertion untouched).
* [FIND] verify_udf01a/01b/01c/02a/02b have no `--self-checks-only`, so fast mode cannot nest them. Full one-off runs
  of **udf02b and udf01c** stop in their own fixture setup (`INSERT INTO users` → "new row violates row-level security
  policy for table users") — pre-existing (scripts unchanged since 2026-10-21; NAV3 touches no UDF code); 01a / 01b /
  02a not run. verify_nav3's Details section proves the resolution NAV3 relies on through the real app instead.
* Route matrices regenerated once (the new route) and CURRENT.
* Not run (WSL memory / instructions): `npm run build`, the full web suite, other sprints' full verifies.

