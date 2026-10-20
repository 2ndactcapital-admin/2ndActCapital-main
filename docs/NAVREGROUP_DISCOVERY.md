# NAVREGROUP — Task 1 discovery (2026-10-20)

Read from the repo at commit `e581844` plus this sprint's working tree. Every
gate below was traced page → Next API route / server fetch → FastAPI handler.
The machine-readable copy of the server gates the harness checks against is
`apps/api/scripts/navregroup_server_gates.mjs`.

Global facts that apply to every row:

- **A valid session is always required.** `auth0_jwt_middleware`
  (`apps/api/main.py:478-508`) returns 401 without a valid bearer token for
  every path except `PUBLIC_PATHS` (`main.py:93-121`). An inactive account gets a
  403 (`main.py:461-465`). "Session" in the tables means exactly this and nothing more.
- **Pages never gate themselves.** Every page does `getHostSession()` and redirects
  to login when there is none. A few (`/admin/settings`, `/admin/platform`,
  `/admin/model-catalog`) also hide their body for the wrong role. The API is the
  real gate everywhere. `proxy.js` runs only the Auth0 middleware.
- **Three permission systems, each with its own default:**
  - `rbac:<perm>` is `services.rbac.has_permission` (`rbac.py:58-79`). It lets
    Super Admin through first, then **default-allows a user with no roles**,
    then checks the role-axis grant. `can_manage_org_settings` / `is_org_admin`
    inherit this.
  - `token:<perm>` is `services.permissions.require_permission`
    (`permissions.py:79-99`). It reads token claims. It **allows when the token
    has no permissions claim** and allows any staff role.
  - `profile:<perm>` is `services.profiles.user_has_permission`, reached through
    `routers/workflows.py:_require_workflow_permission` (`:399-414`). It reads
    profile and permission-set grants only, with **no default-allow**, after
    Super Admin. **`/users/me` does not publish these keys**: it returns the
    role-axis `get_user_permissions` only (`routers/users.py:56-60`).

## 1a. Every route, its API and its real server gate

### Admin

| Route | API it calls | Real server gate | |
|---|---|---|---|
| `/admin` | GET /users/me | session (it filters its own cards) | |
| `/admin/users` | GET /admin/users, /admin/roles, /admin/profiles | rbac:manage_members (`admin.py:148-153`) | |
| `/admin/staff-visibility` | GET /admin/staff/teams, /admin/staff/assignments | rbac:manage_members (`staff_assignments.py:87-92`) | |
| `/admin/profiles` | GET /admin/profiles, /admin/permissions | rbac:manage_org_settings (`profiles.py:94-103`) | |
| `/admin/permission-sets` | GET /admin/permission-sets | rbac:manage_org_settings (`profiles.py:278`) | |
| `/admin/workflows` | GET /admin/workflows | profile:author_workflows (`workflows.py:546`) | pre-existing menu mismatch, see below |
| `/admin/workflows/runs` | GET /admin/workflow-runs | profile:view_workflow_runs (`workflows.py:1005`) | |
| `/admin/workflows/runs/[runId]` | none; redirects to `/admin/workflows/runs?run=` | inherits /runs | |
| `/admin/workflows/triggers` | GET /admin/workflow-triggers | profile:view_workflow_runs OR configure_workflow_triggers (`workflows.py:475-502`) | |
| `/admin/workflows/[id]/versions` | GET /admin/workflows/{id}/versions | profile:author_workflows (`workflows.py:1695`) | |
| `/admin/workflows/[id]/edit` | GET /admin/workflows/{id} | profile:author_workflows (`workflows.py:636`) | |
| `/admin/settings` | GET/PUT /orgs/{org_id}/settings | read: session (own org); write: rbac:manage_org_settings (`services/org_settings.py:656`) | |
| `/admin/modeling/ta` | GET /modeling/ta/defaults; PUT /admin/modeling/ta/defaults | read: NONE by design (`modeling_ta.py:211`); write: rbac:manage_org_settings | [FIND] open read (documented) |
| `/admin/model-research` | GET /admin/model-research | rbac:manage_org_settings (`model_research.py:39`) | |
| `/admin/model-catalog` | GET /admin/model-catalog, /admin/system-one-catalog | read: session (by design); write: super admin (`org_settings.py:567`). The page renders only for super_admin | [FIND] open read (documented) |
| `/admin/restricted-access` | GET /admin/restricted | super admin (`restricted_access.py:47-54`) | |
| `/admin/trading-authority` | GET /admin/trading-authority | super admin (`trading_authority.py:39-46`) | |
| `/admin/pricing/note-terms-queue` | GET /admin/pricing/note-terms/queue | super admin (`pricing_admin.py:144-157`) | |
| `/admin/pricing/surface` | POST /admin/pricing/surface | super admin (`pricing_surface.py:108-115`) | |
| `/admin/edgar-pipeline` | GET /admin/edgar/filings (+ progress, issuers, cohorts, bulk/*) | super admin (`edgar_pipeline_admin.py:62-68`) | |
| `/admin/note-extraction/gold` | GET /admin/note-extraction/gold/candidates | super admin (`note_extraction_admin.py:50-56`) | |
| `/admin/note-extraction/results` | GET /admin/note-extraction/runs | super admin (`note_extraction_admin.py:407`) | |
| `/admin/platform` | GET/POST /orgs | super admin (`org_settings.py:177`) | |
| `/admin/billing-groups` | GET /billing-groups | rbac:view_portfolio (`billing_groups.py:63-73`); writes manage_billing | [FIND] any member holding view_portfolio can read billing groups |
| `/admin/custody-import` | GET /custody/profiles, /custody/batches | rbac:view_portfolio (`custody_import.py:129`); writes manage_billing | [FIND] same as above |
| `/admin/document-search` | GET /document-search | **before:** NONE, org filter only. **After (Task 3):** session, results scoped to the caller's visibility | [FIND] leak, fixed this sprint |
| `/admin/semantic-search` | GET /semantic-search | session, results scoped to the caller's visibility (`semantic_search.py:28-50`) | |
| `/admin/vdr-proposals` | GET /vdr-proposals; approve/reject | **before:** NONE (`vdr.py:62`). **After (Task 3):** rbac:manage_deals (DB role grants, the same rule the menu uses). Approve/reject remain token:manage_deals | [FIND] fixed this sprint; see token FIND below |
| `/admin/document-review/[documentId]` | GET /documents/{id}/review (+ corrections, confirm, download) | NONE, org filter only (`document_review.py:55-63`) | **[FIND] ungated, not fixed here**; a parameterised detail page, so it stays off the menu |

### Portfolio and the main navigation

| Route | API it calls | Real server gate | |
|---|---|---|---|
| `/dashboard` | /dashboard/* | session (blocks filtered per permission) | |
| `/crm` | GET /entities | session, org-wide list | [FIND] |
| `/marketplace` | /deals, /taxonomy, /config | session; non-staff filtered to member statuses, **but `?status=` skips the filter** (`marketplace.py:~483`) | [FIND] |
| `/portfolio` | /portfolio/my-investments, /portfolio/allocations | session (my-investments is user-scoped) | |
| `/portfolio/positions` | /portfolio/positions | rbac:view_portfolio (`portfolio_positions.py:101`) | pre-existing menu mismatch, see below |
| `/portfolio/transactions` | /portfolio/transactions | rbac:view_portfolio (`portfolio_transactions.py:124`) | |
| `/portfolio/securities` | /portfolio/securities | rbac:view_portfolio (`portfolio_securities.py:158`) | pre-existing menu mismatch, see below |
| `/portfolio/commitments` | none (a lookup form) | session | |
| `/portfolio/commitments/[commitmentId]` | /modeling/ta/projection/{id} | rbac:view_portfolio (`modeling_ta.py:454`) | |
| `/portfolio/allocation` | /allocation-lens | session. **The page has no gate, and the `entity`/`subtree` selectors accept any entity in the org** (`allocation_lens.py:95`) | **[FIND] effectively ungated → listed for GATE_SUPER_ADMIN only** |
| `/portfolio/ownership-tree` | GET /me/ownership-graph | session plus the caller's own visibility engine (`ownership_tree.py:98-127`) | |
| `/portfolio-reporting` | none: **no page exists** (404) | missing | [FIND] dead link, kept (no href removed) |
| `/market` | /market/* | session, by design (`market_data/access.py:33`) | |
| `/spvs` | GET /spvs | session; `?status=` skips the member filter (`spv.py:~251`) | [FIND] |
| `/insurance` | none: **no page exists** (404) | missing | [FIND] dead link, kept |
| `/community` | none: **no page exists** (404) | missing | [FIND] dead link, kept |
| `/notifications` | /notifications | session, user-scoped | |

Off-menu pages that are not in scope and stay off it: `/fee-schedules/chat`
(view_portfolio), `/profitability` (view_portfolio), `/taxonomy` (session),
`/investment-profile` and `/settings` (linked from the TopBar), and
`/firm-search` (public).

### Menu gates for previously unlisted pages (Task 2)

| Page | Menu gate | Why |
|---|---|---|
| `/portfolio/transactions` | `GATE_VIEW_PORTFOLIO` | matches rbac:view_portfolio exactly, including the role-less default-allow |
| `/portfolio/ownership-tree` | none | the API serves any member, scoped to their own visibility |
| `/portfolio/allocation` | `GATE_SUPER_ADMIN` | **[FIND]** no effective server gate (see above) |
| `/admin/document-search`, `/admin/semantic-search` | none | both filter by the caller's visibility after Task 3 |
| `/admin/workflows/runs`, `/admin/workflows/triggers` | `GATE_SUPER_ADMIN` | **[FIND]** the server gate is profile-axis, and `/users/me` does not publish it, so the menu cannot evaluate it. Fails closed until it does |
| `/admin/billing-groups`, `/admin/custody-import` | `GATE_VIEW_PORTFOLIO` | the real server read gate. **Consequence:** a plain member (whose live role holds view_portfolio) now sees "Admin — Organization → Billing & Custody" |
| `/admin/vdr-proposals` | `GATE_MANAGE_DEALS` | the Task 3 server gate |
| `/admin/model-catalog` | `GATE_SUPER_ADMIN` | unchanged; it was already on the /admin index |

### Pre-existing menu/server mismatches (NOT changed: the hard rule is "no href disappears")

The harness computes these from the baseline and confirms the sprint adds none:

- `/portfolio-reporting`, `/insurance`, `/community` are shown to everyone and 404 for everyone.
- `/portfolio/positions` and `/portfolio/securities` are shown ungated. Their API
  needs view_portfolio, which the live `member_manager` and `org_admin` roles do not hold.
- `/admin/workflows` is shown on manage_org_settings. Its API needs profile-axis
  author_workflows, which no role grant provides.

## 1b. Baseline: what each persona could reach before

Computed, not transcribed, by `apps/api/scripts/navregroup_baseline.mjs` from the
pre-sprint `Sidebar.jsx` (parsed) and `menuVisibility.mjs` (imported), pinned to
commit `e581844`. The output is in `docs/NAVREGROUP_BASELINE.json`. Personas are
the live role grants (`apps/api/scripts/navregroup_personas.mjs`). `reachable` =
sidebar ∪ /admin index cards.

| Persona | Sidebar | /admin cards | Reachable |
|---|---|---|---|
| plain_member (role member) | 13 | 0 | 13 |
| staff_member (role investment_staff) | 13 | 0 | 13 |
| org_admin_manage_members (role member_manager) | 16 | 2 | 16 |
| org_admin_manage_org_settings (role org_admin) | 19 | 6 | 19 |
| super_admin (account super_admin, granted admin) | 30 | 17 | 31 (`/admin/model-catalog` only via the /admin index) |
| no_roles (default-allow branch) | 21 | 7 | 21 |

The drift the prompt reported is confirmed. Positions, Securities and
Commitments were in the sidebar only. Model Catalog was in `MENU_ITEMS` and the
/admin index, never in the sidebar.

## 1c. How the existing sub-pages are routed

- **EDGAR Pipeline** inner tabs are **client state only**: `useState("filings")`
  at `EdgarPipelineMonitor.jsx:706`, with tabs filings, progress, cohorts, issuers
  and bulk at `:60-66`. There is no query param or sub-route. The page stays one
  route, `/admin/edgar-pipeline`, and keeps its own inner tabs as the "Download"
  tab of Structured Notes.
- **Workflows** sub-pages are separate routes with **no shared tab bar**. Each
  page hard-codes its own links: `workflows/page.js` links to Run Console and
  Scheduler, `runs` links to Triggers, `triggers` back to Workflows, and so on.
  `/admin/workflows/runs/[runId]` is a redirect to `/runs?run=`. They become the
  Workflows · Runs · Triggers tabs. `edit` and `versions` match the Workflows tab
  by longest-prefix.

## 1d. The visibility function semantic search uses

`services/document_embedding._visible_entity_ids(pool, org_id, user_id,
is_staff)`, now public as **`visible_entity_ids`**, with the old name kept as an
alias:

- staff → `staff_visibility.get_staff_visible_entity_ids`: assignments, teams and
  the reporting hierarchy. **Super Admin gets every org entity.**
- member → `delegate_grants.get_delegate_visible_entity_ids`: `resolve_entity_set`
  subtree over the member's active grants.
- both → `restricted_access.filter_restricted`, which drops restricted entities
  unless allow-listed.

The per-document rule is now named `document_visible`: an entity document needs
its entity in the set, and an org-general document (no entity) is staff-only.
Document Search reuses both (Task 3). Two consequences, both [FIND], identical to
semantic search today:

- **[FIND]** A non-super-admin staff user is now narrowed to their staff-visible
  entities. Before, keyword search showed them every org document.
- **[FIND]** `filter_restricted` has **no Super Admin bypass**. A Super Admin who
  is not allow-listed does not see a restricted entity's documents, in either
  search. This is not changed here: `filter_restricted` also governs the
  ownership tree and the assistant.

## Further [FIND]s

- **[FIND] — the most important one. Token-claim gates are inert for real
  callers.** `routers/entities.py org_id_from_claims` records that a real
  API-audience access token carries only `sub/iss/aud/azp/scope`. No code adds
  roles or permissions claims (grep of `main.py` and the routers). So
  `services.permissions.is_staff` returns **True** for every real caller, and
  `require_permission` **allows** every real caller. Consequences:
  - **VDR approve/reject** (`token:manage_deals`), and every `manage_deals` write
    in `marketplace.py` / `spv.py`, are open to any signed-in user in
    production. **Not changed here.** The VDR *list* was gated with the DB-backed
    `rbac.require_permission` instead, so it is enforced for real users.
  - **Semantic search, and now Document Search** (which reuses the same rule
    exactly, as Task 3 asked), put every real caller on the **staff** path. A
    member with no staff assignments therefore sees org-general documents (no
    entity) and nothing tied to an entity — not even their own entities'
    documents, which they would see on the member path. That is still narrower
    than the old org-wide listing (no other entity's document leaks), but the
    member/staff split only works as designed once the staff decision reads the
    DB (user_roles), not the token. Recommended next sprint: one DB-backed
    `caller_is_staff` used by both searches.
- **[FIND]** `/admin/document-review/[documentId]` and its corrections, confirm
  and download endpoints have no gate beyond the org. Not fixed here.
