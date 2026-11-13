# NAV2 — the selected entity follows you, and Ctrl K finds anything

Branch `nav2-work` (base `80a4d18`, merge nav1). Phase 2 of the navigation redesign (nav1 = the new menu; nav3 = one
Entities record page). Source: Joe's mockup (2026-11-12) and `sprint_prompts/nav2.structural.md`.

## 1. Discovery

* **Entity search visibility (the CRITICAL check) — no gap.** `GET /entities/search` (`apps/api/routers/entities.py`
  `search_entities`) calls `services.authz.member_entity_scope` and filters IN SQL (`id = ANY($n::uuid[])`):
  a member gets only the member visibility engine's set (`document_embedding.visible_entity_ids`, delegate grants →
  subtree → restricted-access filter); staff / internal readers are unscoped within the org (RLS) **unless** their
  org enforces VISIBILITY1 staff visibility, in which case `family_visibility.scope_for_request` returns their families
  (possibly empty — nothing, never all). Org isolation comes from `org_id = $1` + RLS. No fix needed.
* **Single-entity lookup (the chip's name).** `GET /entities/{id}` runs `require_entity_access` (404 for an entity
  outside the caller's scope) and then 404s a missing row: the SAME status and detail for missing and forbidden;
  a malformed id is FastAPI's 422. The new Next route `app/api/entities/[id]/route.js` forwards to it and collapses
  every non-success to one 404 "Not available", returning only `{id, display_name, entity_type}`.
* **Preference store.** `users.nav_pinned` and `users.assistant_panel_posture` (PATCH `/users/me`, `MePatch`) are
  single-purpose columns — there is no generic preference store. The last selection and Recent therefore live in
  per-browser storage (try/catch), keyed by the person's user id (`nav2-entity:<id>`, `nav2-recent:<id>`), and the
  URL always wins. No schema change.
* **Assistant context today.** `AssistantPanel` already sends `context_ref: {type, id}` on `POST /assistant/message`
  and `context_type` / `context_id` on `GET /assistant/conversation` — but `AppShell` never passed one, and the server
  used it ONLY to key the conversation row (`assistant_conversations.context_ref`); the model never saw it.
* [FIND] **`/portfolio`'s `entity_id` filters only half the page.** It is read on every tab and carried in the tab
  links, but used only on the Allocation heat map / Gap analysis tabs (`GET /portfolio/allocations?entity_id`, target
  side; `require_entity_access` first). The Investments tab (`GET /portfolio/my-investments`) ignores it (always the
  caller's own investments). So `/portfolio` is a FILTERS screen on `tab=allocation|gap` and says "isn't filtered" on
  Investments — a "Filtered to" banner there would have been the silent mismatch the sprint forbids.
* [FIND] **Households are a separate id space.** `public.households.id` ≠ any `entities.id`; an entity belongs to a
  household through `household_memberships (household_id, entity_id)`. Treasury (`?household`, path
  `/cash/households/{id}/treasury`) and Approvals (`/approvals/bulk?household_id`) filter by households.id, so passing
  the selected entity id would be wrong. They say "isn't filtered"; nav3 needs entity → household(s).
* [FIND] **The member ownership tree refuses staff focals.** `GET /me/ownership-graph?focal_id` accepts a focal only
  inside the caller's OWN delegate tree (`services/ownership_tree.member_tree` → PermissionError → 403). Staff
  choosing a client they can see would get a refusal — so it is NOT filtered (staff use the CRM graph); nav3.
* [FIND] **Securities have no per-security page for ordinary users** (`/admin/security-data/master` is Super Admin;
  `/portfolio/securities` has no id param). A security record in Ctrl K opens Market Indicators (`/market`).
* [FIND] **The assistant's entity check follows the method rule.** `require_entity_access` gives internal NON-staff
  roles their unscoped read on GET/HEAD only (STAFFROLES1 `_sees_unscoped`); `POST /assistant/message` is a POST, so
  for them the context note is limited to their own family's entities. More restrictive, never wider — left as is.

## 2. What was built

1. **One context** — `entity_id` in the URL (`lib/entityContext.mjs` — the rule; `lib/useEntityContext.js` — the
   wiring). Every shell link carries it when set and drops it when cleared: sidebar (`sidebarModel(me, path,
   entityId)`), master-page tab bar (`tabBarModel(me, path, entityId)`), the top-bar account menu, the footer's in-app
   links. The remembered selection applies only when the URL has no `entity_id` at all and is then written INTO the
   URL with `router.replace` (`components/nav/EntityContextSync.jsx`) so page, chip and links agree.
2. **Entity chip** (`components/nav/EntityChip.jsx`) — type + name; picker = search (`/api/entities/search`,
   `include_operational=true` so accounts are findable) + Recent (last 8 opened). Choosing → the same screen with
   `entity_id` when that screen filters, else the entity's CRM page when the person passes `GATE_CRM`, else
   `/portfolio?entity_id` (`entityDestination`, one rule shared with Ctrl K). Clear drops the parameter and the
   remembered value. A forbidden / missing / malformed id → "Not available", no name, and every screen renders as if
   nothing were selected (`effectiveEntityId` = null; the id leaves Recent).
3. **Screens** — ONE table, `lib/entityScreens.mjs` (`SCREENS`). `components/nav/EntityScreen.jsx` sits once in
   `AppShell` around every page: on a FILTERS screen it waits for the chip's lookup (a grid never fetches with a pending
   or forbidden id), shows "Filtered to <name> — clear the chip to see everything you can see" and hands the screen the
   VISIBLE entity through `ScreenEntityContext`; on a NOT_FILTERED screen it shows "This screen isn't filtered by
   entity"; elsewhere nothing. The Positions / Transactions grids seed AND hold their EXISTING owner filter from it
   (the grid's own Clear returns to the chip's entity); the Allocation lens seeds its entity selector (its body moved
   inside `AppShell` to read the context). `/portfolio` falls back to the no-entity allocations on a 404, so a
   forbidden id renders exactly like no id. **No server filter was added** (every FILTER-REUSE candidate is listed
   for nav3 below); no route URL changed; no gate touched.
4. **Ctrl K** (`components/nav/CommandPalette.jsx`, model `lib/commandPalette.mjs`) — Ctrl/Cmd K or the top-bar
   Search button. Records: entities (entity search) + securities (`/api/market/securities/search`), labelled.
   Screens: `screensFor(me)` = `visibleMenuItems(me)` from the nav1 menu definition — exactly the person's visible
   hrefs. "Ask about <entity>…" only for a VISIBLE selected entity. Esc closes and returns focus; arrows wrap, Enter
   chooses; Tab is trapped; ONE global keydown handler that ignores inputs / textareas / selects / editable regions.
5. **Assistant context** — `AssistantPanel` (no explicit `contextRef`) sends the selected VISIBLE entity as
   `context_ref: {type: "entity", id, entity_type}` — the existing shape; the panel shows "About: <type> · <name>".
   Server (`routers/assistant.py` `entity_context_note`): only after `require_entity_access` passes, the model's system
   prompt gains one context line naming the entity; missing / forbidden / malformed → nothing (the same silence). The
   tools, their gates and `caller_is_staff` are unchanged.

## 3. Per-screen table (filters / not yet) — for nav3

| Screen | Decision | Evidence / what nav3 needs |
|---|---|---|
| `/portfolio` (Overview) | **Filters** on `tab=allocation` / `tab=gap`; **not filtered** on Investments | `GET /portfolio/allocations?entity_id` (`routers/portfolio.py` ~316, `require_entity_access`); `my-investments` has no entity param |
| `/portfolio/positions` | **Filters** | `GET /portfolio/positions?owner_entity_id` (`routers/portfolio_positions.py` ~334), ANDed with `member_entity_scope`; the entity's OWN positions (no household roll-up) |
| `/portfolio/transactions` | **Filters** | `GET /portfolio/transactions?owner_entity_id` (`routers/portfolio_transactions.py` ~341), ANDed with the caller's scope |
| `/portfolio/allocation` | **Filters** | `GET /allocation-lens?selector_type=entity&entity_id` (`routers/allocation_lens.py` ~29, `require_entity_access` for non-staff) |
| `/portfolio/securities` | Not yet | no entity param; `list_assets(entity_scope=)` internal — FILTER-REUSE candidate |
| `/portfolio/commitments` | Not yet | no entity param; `visible_entity_ids` → `owner_entity_id = ANY` internal — FILTER-REUSE candidate |
| `/portfolio/households` | Not yet | lists only; households are their own id space — link to `/portfolio/households/[id]` once entity → household exists |
| `/portfolio/ownership-tree` | Not yet | `focal_id` only inside the caller's own delegate tree (403 for staff) |
| `/portfolio/approvals` | Not yet | `household_id` (households.id) — needs entity → household |
| `/portfolio/models` | Not filtered (by design) | org model library |
| `/portfolio/replication/catalog` | Not filtered (by design) | provider catalog |
| `/portfolio/replication` | Not filtered (by design) | org replica registry |
| `/portfolio/replication/tilts` | Not yet | account-scoped (`?account`) — needs entity → accounts |
| `/portfolio/replication/backtest` | Not yet | rows carry `household_id`; `member_view` filters by it — FILTER-REUSE candidate |
| `/portfolio/accounts` | Not yet | `FROM public.accounts a` — a `household_id` predicate is a FILTER-REUSE candidate |
| `/portfolio/tax` | Not yet | `tax_overview` groups by household internally — FILTER-REUSE candidate |
| `/portfolio/replication/section351` | Not yet | account-based candidates, no list filter |
| `/portfolio/blocks` | Not filtered (by design) | blocks span accounts |
| `/portfolio/instructions` | Not yet | `account_id` only; the SQL already joins accounts (household) |
| `/treasury` | Not yet | `?household` = households.id; needs entity → household |
| `/documents` | Not yet | `visible_entity_ids` internal; Next route passes only `q`/`limit` — FILTER-REUSE candidate |
| `/documents/semantic` | Not yet | same |
| `/portfolio/replication/notes` | Not filtered (by design) | note catalog |
| `/portfolio/replication/notes/new-issues` | Not filtered (by design) | new issues |

## 4. Proof

See `apps/api/scripts/verify_nav2.py` (the docstring lists what it proves) and `apps/web/tests/nav2/nav2.test.mjs`.
Results are recorded in `docs/PROJECT_STATUS.md` (NAV2 line).
