# NAV1 — the new menu (phase 1 of the navigation redesign)

Branch `nav1-work`. Menu presentation only: every page keeps its URL and its gate. Source: Joe's review of the
clickable mockup "Hollisworks navigation redesign" (2026-11-12). Phase 2 (nav2: entity chip + Ctrl K) and phase 3
(nav3: one Entities record page) come next.

## 1. Discovery

* The ONE menu definition is `apps/web/lib/menuVisibility.mjs` (`SECTIONS`, `GROUPS`, `MENU_ITEMS`). Everything
  else renders from it: `components/Sidebar.jsx` (`sidebarModel` / `SidebarNav`), `components/TabbedSection.jsx`
  (`tabBarModel`), `components/GroupLanding.jsx` (each group's landing route, by group id: `/admin/people`,
  `/admin/configuration`, `/admin/automation`, `/admin/billing`, `/admin/deal-intake`, `/admin/controls`,
  `/admin/ai-models`, `/admin/structured-notes`), the `/admin` index (`visibleAdminGroups`), and ~40 page
  headings via `labelFor(href)` — so a renamed tab label renames that page's heading too (Rule 1, intended).
* `GATE_ADMIN_AREA` (the `/admin/*` layout gate) is "can see at least one non-`main` item". NAV1 moves only
  Super-Admin-gated items into the admin sections (Underlying queue) and nothing out of them, so the area decision
  is unchanged for every persona (proved in verify_nav1 / the node test against the frozen baseline).
* User-preference store: `users.assistant_panel_posture` (text) + `users.nav_pinned`, read by `GET /users/me`
  and written by `PATCH /users/me` (`routers/users.py`, `MePatch`, `model_fields_set`). The sidebar's pin already
  uses this pattern (localStorage first for no flash, the account value wins once, PATCH on toggle). The assistant
  panel will reuse the SAME column — no schema change.
* [FIND] `AssistantPanel` read `user.assistant_panel_posture` / `user.role` from the **Auth0 session user**, which
  carries neither, so the stored override was never read and every user fell to `ROLE_POSTURE["member"]` =
  "expanded". The panel was effectively open for everyone.
* Treasury (`/treasury`, CASH01): the page itself requires only a session; its data API
  `GET /cash/households` is `require_any_permission(view_portfolio, manage_portfolio)` + member scope
  (`routers/cash.py` READ_PERMISSIONS). Without either key the page renders "Treasury is open to staff with
  portfolio access and to the family itself." The menu entry mirrors the API gate (strict).
* [FIND] Treasury's "Coverage, Ladder, Payments, Rules, Payees" are **sections of one page**
  (`TreasuryPanel` / `TreasuryDaily`, in-page anchors such as `#treasury-payments`), not routes. A sidebar tab is a
  route (`tabForPath` matches paths), so Treasury is ONE sidebar entry → `/treasury`, and those sections stay on the
  page where they already are.
* [FIND] Security master's "Review queue" and "Tracked view" are sections of `/admin/security-data/master`
  (`?view=tracked`), not routes; they are reached from that one tab.
* [FIND] "Instructions defaults to trades": `/portfolio/instructions` already lists trades only (REP2 — "every
  trade a person carries out"); nothing to change.
* [FIND] "Entities — for a member the label reads My family": CRM is `GATE_CRM` (internal roles only), so no member
  sees it today and none will after NAV1 (no gate widened). The member label is implemented in the definition
  (`memberLabel`) and proved on the label rule, but it is not reachable by a member until a member-visible
  entities page exists (nav3).
* Pages the map lists with no menu tab today — left out, as instructed: taxonomy, firm search, enrollment.

## 2. Old → new, every item (gate per href unchanged — verify_nav1 diffs it)

| href | old place | old label | new place | gate (unchanged) |
|---|---|---|---|---|
| `/dashboard` | Main | Dashboard | Main · Dashboard | none (any signed-in user) |
| `/crm` | Main | CRM | Main · Entities (member label: My family) | `{"internal":true,"strict":true}` |
| `/marketplace` | Main · Marketplace | Deals | Main · Marketplace → Deals | none (any signed-in user) |
| `/marketplace/my-access` | Main · Marketplace | My deal access | Main · Marketplace → My deal access | none (any signed-in user) |
| `/marketplace/access` | Main · Marketplace | Deal access | Main · Marketplace → Deal access | `{"perm":"manage_deals","strict":true}` |
| `/portfolio` | Main · Portfolio | Overview | Main · Cross Book → Overview | none (any signed-in user) |
| `/portfolio/positions` | Main · Portfolio | Positions | Main · Cross Book → Positions | none (any signed-in user) |
| `/portfolio/transactions` | Main · Portfolio | Transactions | Main · Cross Book → Transactions | `{"perm":"view_portfolio"}` |
| `/portfolio/securities` | Main · Portfolio | Securities | Main · Cross Book → Securities | none (any signed-in user) |
| `/portfolio/commitments` | Main · Portfolio | Commitments | Main · Cross Book → Commitments | none (any signed-in user) |
| `/portfolio/allocation` | Main · Portfolio | Allocation | Main · Cross Book → Allocation | `{"roles":["super_admin"]}` |
| `/portfolio/ownership-tree` | Main · Portfolio | Ownership tree | Main · Cross Book → Ownership tree | none (any signed-in user) |
| `/portfolio/replication` | Main · Portfolio | Replication | Main · Model Portfolios → Build from a fund | `{"perm":"view_replication","strict":true}` |
| `/portfolio/instructions` | Main · Portfolio | Instructions | Main · Model Portfolios → Instructions (Trading) | `{"perm":"view_execution","strict":true}` |
| `/portfolio/models` | Main · Portfolio | Models | Main · Model Portfolios → Models | `{"perm":"view_models","strict":true}` |
| `/portfolio/accounts` | Main · Portfolio | Accounts | Main · Model Portfolios → Accounts & rebalancing | `{"perm":"view_proposals","strict":true}` |
| `/portfolio/tax` | Main · Portfolio | Tax | Main · Model Portfolios → Tax management | `{"perm":"view_tax","strict":true}` |
| `/portfolio/blocks` | Main · Portfolio | Blocks | Main · Model Portfolios → Blocks (Trading) | `{"perm":"view_blocks","strict":true}` |
| `/portfolio/replication/tilts` | Main · Portfolio | Tilts | Main · Model Portfolios → Tilts & completion | `{"perm":"view_replication","strict":true}` |
| `/portfolio/replication/backtest` | Main · Portfolio | Backtest | Main · Model Portfolios → Backtest | `{"perm":"view_replication","strict":true}` |
| `/portfolio/replication/notes` | Main · Portfolio | Notes | Main · Structured Notes → Notes | `{"perm":"view_replication","strict":true}` |
| `/portfolio/replication/notes/new-issues` | Main · Portfolio | New issues | Main · Structured Notes → New issues | `{"internal":true,"perm":"view_replication","strict":true}` |
| `/portfolio/replication/notes/underlyings` | Main · Portfolio | Underlying queue | Platform · Structured notes pipeline → Underlying queue | `{"roles":["super_admin"]}` |
| `/portfolio/households` | Main · Portfolio | Households | Main · Cross Book → Households | `{"internal":true,"perm":"manage_members","strict":true}` |
| `/portfolio/approvals` | Main · Portfolio | Approvals | Main · Cross Book → Approvals (the one shared inbox) | `{"anyOf":["view_authority","approve_authority","view_held_away","approve_held_away","manage_cash_policy","initiate_payment","approve_payment"],"strict":true}` |
| `/portfolio/replication/section351` | Main · Portfolio | Section 351 | Main · Model Portfolios → Transitions | `{"perm":"view_proposals","strict":true}` |
| `/portfolio/replication/catalog` | Main · Portfolio | Model catalog | Main · Model Portfolios → Model catalog | `{"perm":"view_models","strict":true}` |
| `/portfolio-reporting` | Main · Portfolio | Reporting (coming soon) | Main · Cross Book → Reporting (still coming soon, never listed) | none (any signed-in user) |
| `/market` | Main | Market Indicators | Main · Market Indicators | none (any signed-in user) |
| `/spvs` | Main | SPV Manager | Main · SPV Manager | `{"internal":true,"perm":"view_spv","strict":true}` |
| `/insurance` | Main | Insurance (coming soon) | Main · Insurance (still coming soon) | none (any signed-in user) |
| `/community` | Main | Community (coming soon) | Main · Community (still coming soon) | none (any signed-in user) |
| `/documents` | Main · Documents | Search | Main · Documents → Search | none (any signed-in user) |
| `/documents/semantic` | Main · Documents | Semantic search | Main · Documents → Semantic search | none (any signed-in user) |
| `/notifications` | Main | Notifications | Main · Notifications | none (any signed-in user) |
| `/admin` | Admin — Organization | Admin | Organization admin · Admin (index) | `{"perm":"manage_members"}` |
| `/admin/users` | Admin — Organization · People & Access | Users | Organization admin · People & Access → Users | `{"perm":"manage_members"}` |
| `/admin/staff-visibility` | Admin — Organization · People & Access | Teams & Coverage | Organization admin · People & Access → Teams & coverage | `{"perm":"manage_members"}` |
| `/admin/profiles` | Admin — Organization · People & Access | Profiles | Organization admin · People & Access → Profiles | `{"perm":"manage_org_settings"}` |
| `/admin/permission-sets` | Admin — Organization · People & Access | Permission Sets | Organization admin · People & Access → Permission sets | `{"perm":"manage_org_settings"}` |
| `/admin/sod` | Admin — Organization · People & Access | Segregation of duties | Organization admin · People & Access → Segregation of duties | `{"anyOf":["manage_org_settings","manage_compliance","view_audit_log"],"strict":true}` |
| `/admin/settings` | Admin — Organization · Settings | Organization | Organization admin · Firm settings → Organization & branding | `{"perm":"manage_org_settings"}` |
| `/admin/modeling/ta` | Admin — Organization · Settings | TA Model Defaults | Organization admin · Firm settings → TA model defaults | `{"perm":"manage_org_settings"}` |
| `/admin/security-data/sources` | Admin — Organization · Settings | Price sources | Organization admin · Data & pricing → Price sources | `{"perm":"manage_org_settings"}` |
| `/admin/security-data/quality` | Admin — Organization · Settings | Data quality | Organization admin · Data & pricing → Data quality | `{"perm":"manage_org_settings"}` |
| `/admin/allocation-policy` | Admin — Organization · Settings | Allocation policy | Organization admin · Model Portfolio Admin → Allocation policy | `{"perm":"view_blocks","strict":true}` |
| `/admin/executing-accounts` | Admin — Organization · Settings | Executing accounts | Organization admin · Model Portfolio Admin → Executing accounts | `{"perm":"view_blocks","strict":true}` |
| `/admin/hypothetical-policy` | Admin — Organization · Settings | Hypothetical-performance policy | Organization admin · Firm settings → Hypothetical-performance policy | `{"perm":"view_replication","strict":true}` |
| `/admin/disclosures` | Admin — Organization · Settings | Disclosures | Organization admin · Firm settings → Disclosures | `{"perm":"view_disclosures","strict":true}` |
| `/admin/workflows` | Admin — Organization · Workflows | Workflows | Organization admin · Automation → Workflows | `{"perm":"author_workflows","strict":true}` |
| `/admin/workflows/runs` | Admin — Organization · Workflows | Runs | Organization admin · Automation → Runs | `{"perm":"view_workflow_runs","strict":true}` |
| `/admin/workflows/triggers` | Admin — Organization · Workflows | Triggers | Organization admin · Automation → Triggers & schedules | `{"anyOf":["view_workflow_runs","configure_workflow_triggers"],"strict":true}` |
| `/admin/workflows/templates` | Admin — Organization · Workflows | Templates | Organization admin · Automation → Templates | `{"anyOf":["author_workflows","view_workflow_runs","configure_workflow_triggers"],"strict":true}` |
| `/admin/workflows/autonomy` | Admin — Organization · Workflows | Autonomy | Organization admin · Automation → Autonomy | `{"anyOf":["manage_org_settings","author_workflows","view_workflow_runs"],"strict":true}` |
| `/admin/billing-groups` | Admin — Organization · Billing & Custody | Billing Groups | Organization admin · Custody & Billing → Billing groups | `{"internal":true,"perm":"view_portfolio","strict":true}` |
| `/admin/custody-import` | Admin — Organization · Billing & Custody | Custody Import | Organization admin · Custody & Billing → Custody import | `{"internal":true,"perm":"view_portfolio","strict":true}` |
| `/fee-schedules/chat` | Admin — Organization · Billing & Custody | Fee schedules | Organization admin · Custody & Billing → Fee schedules | `{"internal":true,"perm":"view_portfolio","strict":true}` |
| `/profitability` | Admin — Organization · Billing & Custody | Profitability | Organization admin · Custody & Billing → Profitability | `{"internal":true,"perm":"view_portfolio","strict":true}` |
| `/admin/custodian-capabilities` | Admin — Organization · Billing & Custody | Custodian capabilities | Organization admin · Custody & Billing → Custodian capabilities | `{"staff":true,"perm":"manage_org_settings","strict":true}` |
| `/admin/vdr-proposals` | Admin — Organization · Deal Intake | VDR Proposals | Organization admin · Deal Intake → VDR proposals | `{"perm":"manage_deals"}` |
| `/admin/platform` | Admin — Platform · Tenants | Tenants | Platform · Tenants & controls → Tenants | `{"roles":["super_admin"]}` |
| `/admin/restricted-access` | Admin — Platform · Controls | Restricted Access | Platform · Tenants & controls → Restricted access | `{"roles":["super_admin"]}` |
| `/admin/trading-authority` | Admin — Platform · Controls | Trading Authority | Platform · Tenants & controls → Trading authority | `{"roles":["super_admin"]}` |
| `/admin/security-data` | Admin — Platform · Controls | Security data | Platform · Security data → Providers & licences | `{"roles":["super_admin"]}` |
| `/admin/security-data/master` | Admin — Platform · Controls | Security master | Platform · Security data → Security master (Review queue + Tracked view are sections of this page) | `{"roles":["super_admin"]}` |
| `/admin/integrated-import` | Admin — Platform · Controls | Integrated import | Platform · Tenants & controls → Integrated import | `{"roles":["super_admin"]}` |
| `/admin/model-provider` | Admin — Platform · Controls | Published models | Platform · Publishing → Published models | `{"roles":["super_admin"]}` |
| `/admin/platform/workflow-templates` | Admin — Platform · Controls | Workflow templates | Platform · Publishing → Workflow templates | `{"roles":["super_admin"]}` |
| `/admin/counterparties` | Admin — Platform · Controls | Counterparties | Organization admin · Custody & Billing → Counterparties (gate still Super Admin) | `{"roles":["super_admin"]}` |
| `/admin/model-catalog` | Admin — Platform · AI Models | Model Catalog | Platform · AI → Model catalog | `{"roles":["super_admin"]}` |
| `/admin/model-research` | Admin — Platform · AI Models | Model Research | Platform · AI → Model research | `{"perm":"manage_org_settings","strict":true}` |
| `/admin/ai-jobs` | Admin — Platform · AI Models | Model Jobs | Platform · AI → Jobs & budgets | `{"roles":["super_admin"]}` |
| `/admin/ai-drift` | Admin — Platform · AI Models | Drift monitor | Platform · AI → Drift monitor | `{"perm":"manage_org_settings","strict":true}` |
| `/admin/edgar-pipeline` | Admin — Platform · Structured Notes | Download | Platform · Structured notes pipeline → EDGAR download | `{"roles":["super_admin"]}` |
| `/admin/pricing/note-terms-queue` | Admin — Platform · Structured Notes | Review queue | Platform · Structured notes pipeline → Terms review | `{"roles":["super_admin"]}` |
| `/admin/note-extraction/gold` | Admin — Platform · Structured Notes | Gold answers | Platform · Structured notes pipeline → Gold answers | `{"roles":["super_admin"]}` |
| `/admin/note-extraction/results` | Admin — Platform · Structured Notes | Accuracy results | Platform · Structured notes pipeline → Accuracy | `{"roles":["super_admin"]}` |
| `/admin/pricing/surface` | Admin — Platform · Structured Notes | Volatility surface | Platform · Structured notes pipeline → Volatility surface | `{"roles":["super_admin"]}` |
| `/treasury` | — (NO menu entry before NAV1) | — | Main · Treasury (NEW) | `{"anyOf":["view_portfolio","manage_portfolio"],"strict":true}` — the page's API gate (GET /cash/households) |
