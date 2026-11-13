NAV1 — the new menu (phase 1 of the navigation redesign; Joe reviewed and changed the clickable mockup "Hollisworks navigation redesign" on 2026-11-12). This sprint changes ONLY the menu, its grouping and labels, the Treasury entry and the assistant panel's default; every page keeps its URL and its gate. Phase 2 (nav2: the selected-entity chip that follows you + Ctrl K find / ask) and phase 3 (nav3: one Entities record page with tabs, relationship status, direct vs look-through holdings, one as-of date) come next. 5 tasks. Branch nav1-work. Never weaken an existing assertion; never widen a gate.

LESSONS: STATUS line first; small committed steps ("nav1 (wip): …"); the runner kills a leg at 90 minutes — commit after every step.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- The menu is defined in apps/web/lib/menuVisibility.mjs (665 lines): sidebar groups with id / label / section ("main" | "org_admin" | "platform") / order / landing, and tab items with section / group / tab_order / href / label / icon and their permission gates (e.g. line 176 marketplace, 181 portfolio (22 visible tabs), 186 documents, 191 people, 196 settings, 201 workflows, 206 billing, 211 deal_intake, 216 tenants, 221 controls, 226 ai_models, 231 structured_notes). The shell is components/AppShell.jsx; the assistant is components/assistant/AssistantPanel.jsx.
- The function → menu map (project doc claude/HOLLISWORKS_FEATURES_AND_MENU_MAP_v1.md, also summarised below) lists every page, its current tab and its gate. Treasury (/treasury) has NO menu entry today.

=== THE NEW MENU (Joe's decisions; mockup labels) ===
MAIN (label → today's tabs, gates unchanged):
- Dashboard → Dashboard.
- Entities → today's CRM (label "CRM" becomes "Entities"; for a member the label reads "My family"). Covers households, clients, prospects, partners, former clients, individuals, accounts, legal entities.
- Cross Book → Positions, Transactions, Securities, Commitments, Households, Allocation, Ownership tree, Approvals (ONE shared inbox). For a member: "My accounts" with only what they can see today. (Joe chose the name "Cross Book".)
- Model Portfolios → Models (Portfolio → Models + Model catalog), Build from a fund (Replication), Tilts & completion (Tilts), Backtest, Accounts & rebalancing (Accounts), Tax management (Tax), Transitions (Section 351), Trading (Blocks + Instructions; Instructions defaults to trades).
- Treasury → NEW sidebar entry for /treasury (Coverage, Ladder, Payments, Rules, Payees — use the page's own sections; same gates as the page).
- Marketplace (unchanged), SPV Manager (unchanged).
- Structured Notes → Notes (Portfolio → Notes), New issues (Portfolio → New issues). The Underlying queue (Super Admin) moves to Platform → Structured notes pipeline.
- Market Indicators, Documents (unchanged).
ORGANIZATION ADMIN (section heading "Organization admin"):
- Firm settings → Organization & branding, Disclosures, Hypothetical-performance policy, TA model defaults.
- People & Access → Users, Teams & coverage, Profiles & permission sets, Segregation of duties.
- Model Portfolio Admin → Allocation policy, Executing accounts (and the model-subscription side of the catalog when it is switched on).
- Data & pricing → Price sources, Data quality.
- Custody & Billing → Custodian capabilities, Counterparties, Custody import, Fee schedules, Billing groups, Profitability.
- Automation → Workflows, Runs, Triggers & schedules, Templates, Autonomy.
- Deal Intake → VDR proposals.
PLATFORM · SUPER ADMIN (section heading):
- Tenants & controls → Tenants, Restricted access, Trading authority, Integrated import.
- Security data → Providers & licences, Security master (incl. its Review queue and Tracked view — link to the existing screens/sections).
- AI → Model catalog, Jobs & budgets, Model research, Drift monitor.
- Structured notes pipeline → EDGAR download, Terms review, Gold answers, Accuracy, Volatility surface, Underlying queue.
- Publishing → Published models, Workflow templates.
Rules: a page appears in exactly ONE place (except the shared Approvals inbox linked, not duplicated); a group with no visible tab for the user is hidden; a member sees only main groups they can use today. If a page the map lists has no tab today (taxonomy, firm search, enrollment), leave it out and list it in the notes.

=== ASSISTANT PANEL ===
Collapsed by default for everyone; one button in the top bar opens / closes it; each user's choice is remembered (the existing user-preference store if there is one, else per-browser storage wrapped in try/catch) and survives a reload. No other assistant behaviour changes in this sprint.

WSL memory is limited: do NOT run npm run build or any other sprint's full verify. One-offs `timeout 600`. Light theme; keep the navy sidebar frame.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — nothing will ever notify you that a script finished. Never wait for one. Run every command synchronously in the foreground. No network. No model call. Never print a secret. No route, URL or gate changes (menu presentation only). Stick with existing constructs. Final commit on nav1-work with message "nav1: new menu — Entities, Cross Book, Model Portfolios, Treasury, Structured Notes; admin regrouped (Organization admin / Platform); assistant collapsed by default".

=== TASK 1: STATUS FIRST + DISCOVER === docs/PROJECT_STATUS.md NAV1 line; read menuVisibility.mjs + its tests, AppShell, AssistantPanel, any user-preference store; build the old → new mapping table for EVERY current tab (href, old group, new group, gate) into docs/NAV1_NOTES.md. COMMIT.
=== TASK 2: MENU === new groups / labels / order / sections in menuVisibility.mjs (role-aware labels for members), Treasury entry. COMMIT.
=== TASK 3: ASSISTANT === collapsed default + remembered toggle. COMMIT.
=== TASK 4: VERIFY === node tests (the menu module's existing test file + new cases) and apps/api/scripts/verify_nav1.py (static / self-check style, in regression_set.txt): every current href appears exactly once in the new menu (except Approvals' shared link) and nothing is lost; each item keeps its exact gate (diff old vs new gate per href = empty); persona checks — member, support staff, org admin, Super Admin — see exactly the groups / tabs they could reach before (same set of hrefs, regrouped), with member labels "My family" / "My accounts"; empty groups hidden; Treasury visible exactly to whoever can open /treasury; assistant renders collapsed, toggles, remembers; render tests for the sidebar in each persona. Route matrices (authz1_routes.py, visibility1_routes.py --check) CURRENT (no route changed). One-offs listed with PASS counts. COMMIT.
=== TASK 5: FINAL === PROJECT_STATUS line + a short "where did it go" table in docs/NAV1_NOTES.md for Joe (old place → new place). Final commit.
