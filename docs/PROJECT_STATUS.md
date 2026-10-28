# Project Status — open blockers and tracked follow-ups
Last updated: 2026-10-27 (rep7b.structural — Replication Studio sprint 7b: account AUTHORITY (read_only | advise | managed — a household setting, overridable per account, maker-checker) and CUSTODY (integrated | other_custodian | outside) as two separate settings; rep7's held-away guard re-keyed on authority; advise one-off proposals → member / other-adviser instructions + letter; managed at another custodian → MANUAL DVP settlement instructions; schema APPLIED; migration writes no rows (every account behaves as before); verify WRITTEN, not yet run; see the top entry). Previously 2026-10-26 (rep7.structural — Replication Studio sprint 7: held-away accounts and the portfolio X-ray — read-only positions and lots for accounts held elsewhere via manual entry, file upload through rep3's mapping, Chancery statements and an aggregator interface (none connected), with source and as-of on every row; held-away data in household wash-sale control, the gain budget and the loss bank; the stale-data confirmation task; every trade path refuses a held-away account; the X-ray with its one-click join request; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-24 (rep6.structural — Replication Studio sprint 6: the tax engine — member tax profiles and carryforwards, the substitution table and its denials, household wash-sale CONTROL replacing rep5's flag, daily loss harvesting as proposals, the loss bank and tax-alpha ledger, declared outside trades, the custodian-harvest switch, the Tax screens; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-23 (rep5.structural — Replication Studio sprint 5: accounts join models with account-level settings and exclusions, the lot-level transition analysis under a gain budget, rebalance proposals from join / model change / drift / cash flow / manual, approval on human tasks, approved proposals → execution instructions through rep2; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-23 (rep4.structural — Replication Studio sprint 4: build a model from a fund — "Holdings of <TICKER>" models from approved terms + live holdings into rep3's registry, the five-question viability check, the fund-versus-replica cost comparison, the registry "join the existing model" offer, holdings / terms monitoring that drafts versions for review; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-23 (rep3.structural — Replication Studio sprint 3: model management — one model registry, Excel/CSV upload with column mapping, a permanent upload log, the model library (platform + org shelves, copy or subscribe), composite (sleeve) models, approvals on governed human tasks, rep1's terms approvals moved onto them; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-22 (rep2.structural — Replication Studio sprint 2: custodian capabilities, trade adapters, governed human tasks on the workflow engine, execution instructions; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-22 (rep1.structural — Replication Studio sprint 1; previously bulkfetchui3.structural — Bulk download: sortable
full-height Institutions table with Downloaded / Stored + Refresh, Refresh
index in the Institutions box, weekday automatic index refresh (operator must
deploy the cron service), GS image-cover finals classified, --reclassify-other,
scripts/run_regressions.sh; verify WRITTEN, not yet run; see the top entry).
Previously 2026-10-22 (dealroom2.structural — Marketplace Deal Room Phase 2:
immutable agreement versions with keep / require-re-signing, uploaded PDF
agreements with a page viewer, the firm template library, the master NDA, the
signed copy as a PDF with a certificate page; schema APPLIED live; verify
WRITTEN, not yet run; see the top entry). Previously 2026-10-22 (dealroom1.structural — Marketplace Deal Room Phase 1:
publishers, delegates, nine-folder data room, DRAFT NDA + joinder with in-house
e-signature, eligibility, automatic approval, the access grid; schema and
migration APPLIED live; verify WRITTEN, not yet run; see the top entry).
Previously 2026-10-21 (bugfix1.structural — HollisMuse rounds 1–2: one
SPV-totals function, one deal lifecycle, search over attachments, dead menu
items flagged comingSoon, EDGAR peek before download; verify WRITTEN, not yet
run; see the top entry). Previously 2026-10-21 (usermgmt1.structural — People & Access shows one row
per person with how they sign in; password reset / resend invite / link a
pending login / edit email; users.is_test; 19 orphan fixtures removed; verify
WRITTEN, not yet run; see the top entry). Previously 2026-10-21 (authz1.structural — one DB-backed, fail-closed
authorization path; no default-allow; pending provisioning). Previously 2026-10-20 (navregroup.structural — the navigation regrouped into
master pages with tabs rendered from ONE menu definition; Document Search now
filters by the caller's visibility and the VDR proposals list requires
manage_deals; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-16 (bulkfetch.structural — a resumable, rate-limited bulk
EDGAR downloader run from the command line with a cover-page peek, sources as
configuration, index refresh, product supplements once per program, WF/DB
prefilter v2, the 200 stuck 'fetched' rows, and five gold review screen fixes;
verify WRITTEN, not yet run; see the top entry). Previously 2026-10-14 (notefix2.structural — seven systematic errors found
by checking all 50 gold-1 notes against their own SEC filings: affiliated
agents resolved by rule, junk list members dropped before they become
readings, buffer-vs-barrier and digital-vs-participation field definitions
rewritten with a filing-wording test instead of relying on the word
"barrier"/"buffer" literally appearing, blank-field defaults, a fee-total
default, two new self-checks (coupon cross-check, maturity-after-pricing)
plus a payoff check extended to the protection level itself, and the
second-reader findings loaded into a new table the review screen surfaces
per field; verify WRITTEN, not yet run; see the top entry). Previously
2026-10-13 (notefix1.structural — four fixes found by the first
real gold pre-fill: fee fields become ranges, one bad field no longer rejects
a whole note, a wider hypothetical-example detector, and the gold verify's
regression runs after its own teardown; verify WRITTEN, not yet run; see the
top entry). Previously 2026-10-08 (goldset.structural — the gold set workflow: stratified
plan, propose with trap cases, gpt-5-mini pre-fill, the rebuilt review screen and
the scorer; verify WRITTEN, not yet run; see the top entry). Earlier the same day:
(notefields.structural — the approved v3 structured-note
field list is the extraction schema; verify WRITTEN, not yet run). Previously 2026-10-07 (mkt04c.structural — market indicators: key dates,
My dates, saved views and the correlations panel, front end only; verify
WRITTEN, not yet run; see the top entry). Earlier the same day: (mkt04b.structural — market indicators chart: anchor
bar, client-side rebasing, overlays, series `stddev` field; verify WRITTEN, not
yet run; see the top entry). Earlier the same day: (mkt04a.structural — market indicators page: Next.js
routes, page shell, selection panel and grid; verify WRITTEN, not yet run; see
the top entry). Earlier the same day: (mkt03b.structural — market data key dates,
regimes, personal dates and saved views: API, seeds, loader; verify WRITTEN,
not yet run; see the top entry). Previously 2026-10-04 (mkt02c.structural — market data long history:
S&P 500 splice from Yahoo, row-level provenance, fred.baa10y, five note
underlyings linked, Yahoo index units; verify WRITTEN, not yet run; see the
top entry). Previously 2026-10-03 (mkt03.structural — market data READ API:
catalog, series, grid, correlations; verified 68/0). Earlier the same day:
mkt02.structural — nightly market data refresh:
adapter registry, Yahoo adapter, nightly orchestrator, staleness report,
Render cron entrypoint + setup doc; verify WRITTEN, not yet run; no Render
service created; see the top entry). Earlier the same day: edgarcohorts.structural — EDGAR cohorts: named,
frozen sets of filings; cohort-targeted fetch; --cohort for B1's tools; the
template-study preset and inventory pass; verify WRITTEN, not yet run; see the
top entry). Earlier the same day: mkt01.structural — market data foundation: indicator
registry, FRED adapter, historical backfill; ingest + verify WRITTEN, not yet
run. Previously 2026-10-02 (noteextractb1.structural — note
extraction B1: engine, gold set, evaluation harness, pilot runner; results
STAGED). Earlier the same day: edgarpipelinea.structural — EDGAR pipeline A: status
lifecycle, selection policies, incremental discovery, fetch-to-R2, the nightly
Render job and the monitoring screen; see the top entry). Earlier, 2026-10-01:
modelresearch.structural (read-only Model Research grid). Earlier the same day: ensemblesystemone.structural
(it overwrites ensemblemodels.structural).
Previous update: 2026-09-30 (tiergating.structural — closes the verb-tier
gating gap `wave2discovery.lowrisk` flagged as the most important open item:
a Tier-1 registry verb invoked from a BPMN Service Task executed unattended,
exactly like a Tier-3 verb, because `_execute_service_task` read
`action_registry_key` and nothing else — `workflow_steps.autonomy_tier` was
fetched but only ever used for a display count. EFFECTIVE TIER IS NOW
min(registry tier, diagram tier), computed at execution
(`workflow_engine.compute_effective_tier`) and surfaced in the run console
in place of the diagram's own value — same most-restrictive-wins rule
CLAUDE.md documents for dual-path permission resolution: an author may
UPGRADE a Tier-3 verb by marking its diagram element Tier 1, but can never
DOWNGRADE a Tier-1 verb by marking it Tier 3. A Service Task whose effective
tier is 1 no longer executes when `_drive` reaches it: the run suspends
(`workflow_runs.status='awaiting_approval'`, the step
`status='suspended'`), and an `agent_proposals` row is created to gate it —
REUSING the existing maker-checker/disclosed-self-approval mechanism
(`agenticmakerchecker.structural` / `selfapproval.structural`) rather than
building a second approval system. Reviewers are alerted via the SAME
`member_todos` sibling pattern every other workflow alert uses
(`workflow_todos.create_tier_approval_alerts`), scoped to
`review_agent_proposals` holders — never `org_admin` broadly, and never
wider than the set who could actually decide the proposal. THE SCHEDULED-RUN
MAKER QUESTION the sprint prompt posed turned out to already be answered:
`workflow_scheduler._fire` already passes the trigger's own `created_by` as
`started_by`, so `workflow_runs.started_by` is already the correct maker for
both a manual run and a scheduled one — no change was needed there, only
documentation that `_suspend_step` refuses outright (rather than treating a
NULL as "anyone may approve") if a run somehow has none. THE DIAGRAM
EDITOR'S "Tier 1 — approval required" OPTION (`WorkflowDiagramEditor.jsx`,
flagged by `wave2discovery.lowrisk` as "worse than no gate" while it did
nothing) IS NOW REAL. New endpoint `POST /admin/workflow-runs/{run_id}/
steps/{step_id}/decision`, gated on `review_agent_proposals` (a permission
SEPARATE from the three existing workflow keys, because "may see runs" and
"may decide a suspended step" are different questions). STAFFING FACT,
stated plainly per CLAUDE.md: `review_agent_proposals` is granted to six
roles and only `org_admin` has a real holder (one user) in the live 2nd Act
org, so in practice most Tier-1 approvals today take the disclosed
self-approval path, not a genuine second-reviewer path — this was already
true before this sprint and is unchanged by it. Wave 2's one remaining item
after this is the NL-to-workflow-template library. See
`docs/WORKFLOW_WAVE2_DISCOVERY.md` (Task 5 section updated) and
`apps/api/scripts/verify_tiergating.py` (WRITTEN, not yet run by the
operator); previously 2026-09-18 (scripttaskrefusal.structural — closes a live
arbitrary-code-execution gap confirmed by `wave2discovery.lowrisk`
(2026-09-18, read-only): SpiffWorkflow's stock `PythonScriptEngine` runs a
BPMN `bpmn:scriptTask`'s body via bare `eval()`/`exec()` in-process, with
this application's own database credentials, outside the action registry,
outside every permission check, outside audit, and outside the custody
cliff — and nothing in this codebase rejected one. The NL generator never
offered `scriptTask` to the model, but the hand-edit diagram-editor path had
no such restriction: an org admin could drag one from bpmn-js's stock
palette, write arbitrary Python, save it (validation did not object), and
the next run executed it. DECISION: refuse outright at validation, never
sandbox — RestrictedPython (named in earlier design work, §5.3 of
`docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md`) is now SUPERSEDED, because a
sandbox is a weaker guarantee than a refusal and costs materially more to
build and maintain; a workflow step that needs logic belongs on a Service
Task calling a registered, permission-checked, audited action instead — the
entire point of the action registry. Both writers of `workflow_versions.
bpmn_xml` (`workflow_nl_generator.generate_workflow`,
`workflow_editor.save_new_version`) share one validator
(`workflow_nl_generator._validate`), which now refuses any BPMN containing a
`bpmn:scriptTask`, naming the element, before either writer stores anything;
`services/workflow_engine.py`'s BPMN parser also refuses the element type
outright at the SpiffWorkflow parse layer itself (same
`OVERRIDE_PARSER_CLASSES` mechanism already used for `businessRuleTask`) as
defense in depth for any future direct-`parse_bpmn` caller. The bpmn-js
diagram editor also got a best-effort, non-enforcing client-side guard
(undoes a Script Task the instant it is created/replaced) — usability only;
the real control is server-side, proven by a direct API POST bypassing the
client entirely. `wave2discovery.lowrisk` queried the live database and
confirmed `workflow_versions` held ZERO rows at discovery time, so this was
a preventive fix, not a remediation of an already-exploited gap.
`wave2discovery.lowrisk` also corrected a standing inaccuracy in both
`docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md` and
`docs/CROSS_PROJECT_STATUS_RECONCILIATION.md`, neither of which had ever
been updated to reflect that five structural sprints (`workflowmgr1`–
`workflowmgr5`, plus `workflowbpmnfix`) building the NL generator, the real
SpiffWorkflow execution engine, the diagram editor, the run console, and a
granular three-permission model were already merged to `main` — none of
which had ever appeared in this file either. What remains genuinely unbuilt
for Wave 2: the verb-tier gating engine (a Service Task does not suspend on
Tier-1 today) and the NL-to-workflow-template library. See
`docs/WORKFLOW_WAVE2_DISCOVERY.md` for the full discovery record and
`apps/api/scripts/verify_scripttaskrefusal.py` (WRITTEN, not yet run by the
operator); previously 2026-09-17 (selfapproval.structural — the maker-checker rule
`agenticmakerchecker.structural` built (below) made a single-privileged-user
org unable to ever approve its own proposals: excluding the maker from
`review_agent_proposals` holders can leave the eligible-checker set EMPTY,
and 2nd Act's real data confirms this is not hypothetical (`org_admin` is
the only one of six granted roles with a real holder, one user). Fixed by
ALLOW WITH DISCLOSURE, not escalation (rejected — in a lean org that target
is frequently the same human wearing a second hat) and not an outright
block (rejected — unusable for a single-advisor tenant). `agent_proposals_
maker_checker_chk` is now conditional — a raw UPDATE setting `reviewed_by =
proposed_by` still fails unless the row also carries `self_approved = true`
and a non-null `self_approval_reason` — and the emptiness gate itself
(`services.agent_proposals.has_other_eligible_checker`) is COMPUTED and
org-scoped, never a caller-supplied flag: a maker with an available
reviewer is still refused. See the `selfapproval.structural` entry below
for the full accounting, including why the gate deliberately excludes
`is_super_admin` (unlike `is_eligible_reviewer`'s own per-candidate check)
and why self-approval is currently the NORMAL path for 2nd Act's one
real `org_admin`-holding user, not an edge case; previously 2026-09-17 (actionregistryfix.structural — the action registry's
16-verb ceiling means `required_permission` already IS the capability
vocabulary; the long-open "#3 capability annotation" item from
`agenticmakerchecker.structural` (below) is DISSOLVED, not solved — no
capability column added, `required_permission`'s own defects fixed instead:
`spv.subscribe` (commits capital, had NO gate at all) now requires the real,
seeded `indicate_interest` permission, matching the real HTTP endpoint's own
member-initiated (not staff-gated) design; `entity.link_ownership` moved off
the role string `'staff'` (which silently refused every caller, forever,
since `rbac.get_user_permissions` never returns role names) onto
`manage_deals`, this registry's existing de facto staff gate. Added `tier`
(int, 1/2/3) per a three-test rule (moves money/creates an obligation;
produces an artifact a third party relies on; mutates ownership/economic
terms/a posted ledger line) and dropped `default_autonomy` (correlated 1:1
with `access_type` across all 16 rows, never encoded a real decision).
**Tier direction is counterintuitive and now written down where it can't be
missed** (`services/action_registry.py`'s `AssistantAction.tier` docstring):
Tier 1 is the HIGHEST-stakes class, not the smallest allowance — a future
agent-side `max_tier` column names the DEEPEST (numerically LOWEST) tier an
agent may reach, so `max_tier=1` is the MOST permissive agent and `max_tier=3`
the LEAST, with eligibility as `action.tier >= agent.max_tier`, never `<=`.
Collapsed the `entities`/`entity`/`entity_graph` module split into one
`entity` domain (module is write-only metadata with zero code readers other
than `sync_catalog`'s own upsert — action_key never changes, so there is no
rename blast radius). Added `propose(agent_key, object_type, payload,
rationale)` — the one new, universal write surface every agent depends on,
landing as an `agent_proposals` row; deliberately ungated
(`required_permission=None`) because the real safety gate is the maker-checker
review step `agenticmakerchecker.structural` already built, not the propose
call itself. `crm.draft_note.reversible` was reported, not changed — already
correctly `False` since 72ba8c0, reverting 2999846's earlier flip; the
`/undo` path is still hollow (no `undo_token` stored, `entity_notes` has no
soft-delete column). `reversible` confirmed meaningless on all 10 reads (its
only two live readers, both in `routers/assistant.py`, are unreachable unless
`access_type == "write"`) — left `False` rather than made nullable.
Migration `apps/api/migrations/actionregistryfix_tier.sql` applied live via
the supabase-2ndact-dev MCP `apply_migration` tool; `docs/schema_snapshot.sql`
refreshed. `apps/api/scripts/verify_actionregistryfix.py` is WRITTEN, not yet
run by the operator — per SPRINT_WORKFLOW_STANDARD.md's "sprint writes,
operator runs" rule, this entry does not claim a PASS/FAIL count. The
registry is still thin against five of the seven named agents (Document &
Custodial Ops and Compliance Analyst have zero verbs; only Deal & SPV,
Portfolio & Suitability, and Fund Admin & Billing have any) — by design, per
this sprint's own instruction: verbs get added when the agent that needs them
is built, not spec'd speculatively ahead of it); previously 2026-09-17 (agenticmakerchecker.structural — agentic substrate:
a real `review_agent_proposals` permission plus a generic `agent_proposals`
table make maker-checker eligibility COMPUTED rather than stored (holds the
permission AND is not the row's own maker), correcting the original design's
"boundary = tool allowlist × reviewer role" to allowlist-alone since reviewer
is not fixed per agent; compliance_sr/compliance_jr (confirmed live to hold
zero permissions and zero holders) collapsed into one `compliance` role;
escalation_reason (created earlier, unwired) now lives on a real, nullable
column. Schema + code shipped and confirmed live against the dev database;
`apps/api/scripts/verify_agenticmakerchecker.py` is WRITTEN, not yet run by
the operator — per SPRINT_WORKFLOW_STANDARD.md's "sprint writes, operator
runs" rule, this entry does not claim a PASS/FAIL count. See the entry below
for the full accounting, including the two real code references found and
fixed (one live endpoint, one dead role-keyed lookup) and why #3 capability
annotation remains out of scope); previously 2026-09-17 (litellmphasef.structural — LiteLLM design-doc §7.5,
the Hollisworks-only force-Anthropic emergency bypass: a super_admin-only,
genuinely platform-scoped (`platform_ai_controls`, no `org_id` column at all —
same convention as `platform_model_catalog`) toggle that forces every TEXT AI
call straight to a single fixed Anthropic model, bypassing LiteLLM entirely,
reusing the existing `LITELLM_ROUTING_DISABLED` rollback's own direct-Anthropic
branch as a second driver rather than building a second mechanism. Embeddings
are explicitly OUT OF SCOPE and keep routing through LiteLLM unaffected while
engaged — Voyage has no direct-Anthropic equivalent, proven live by a real
Voyage call succeeding, unmarked, while a concurrent text call is proven
bypassed. `ai_decision_log` gained two columns (`litellm_bypassed`,
`bypass_reason`) so an incident leaves a queryable record even though
LiteLLM's own spend log stays dark. No blockers — `62/62 PASS, 0 FAIL, 1 FIND`
via `apps/api/scripts/verify_litellmphasef.py`. See
`docs/LITELLM_INTEGRATION_DESIGN_V1.md` §7.5 for the full design and proof
summary); previously 2026-09-16 (litellmphasee.structural — LiteLLM Phase E,
per-task model assignment + effort: an org_admin now assigns a model (from
the org's D2-authorised set) to each of the platform's three real AI-task
dials, and an effort level where the assigned model reports
`supports_reasoning: true`; `54/54 PASS, 0 FAIL, 4 FIND` via
`apps/api/scripts/verify_litellmphasee.py`; see the entry below); previously
2026-09-16 (litellmseedfix.structural — claude-haiku deployment registered,
naming convention aligned across settings/catalog/proxy, `31/31 PASS` via
`apps/api/scripts/verify_litellmseedfix.py` — this file never got its own
entry when that sprint shipped; two real, unrelated bugs in that same
script were found and fixed live during Phase E, see the entry below);
previously 2026-09-16 (litellmphased2.structural — LiteLLM Phase D2, the
model pick-list UI: a Hollisworks super_admin curates which models are
platform-supportable (`platform_model_catalog`, two new tables — the real
org_settings-can't-hold-platform-scope finding forced this, not a design
preference); an org_admin picks which of those its own org may use
(`org_model_selections`); enforcement is real at `services.extraction`'s
call path (`AIModelNotAuthorizedError`, raised before any provider call,
never silently swallowed — including a real pre-existing bug in
`call_claude_json`'s exception handling, found and fixed in this pass); a
real Starlette route-registration-order bug (`{key}` swallowing the literal
`model-selections` segment) found and fixed live; `47/47 PASS, 0 FAIL, 4
FIND` via `apps/api/scripts/verify_litellmphased2.py`; see the entry below);
previously 2026-09-15 (litellmphased1b.structural — LiteLLM Phase D1b,
routing + spend attribution: an org's own AI calls now actually route to its
own LiteLLM deployment when `ai.credential_source.{provider}` is `'org'`
(D1a stored the credential but nothing read the flag at call time — this
sprint is the wiring); every LiteLLM request, text and embedding, now
carries real attribution metadata so `LiteLLM_SpendLogs` can answer "which
org, against whose key" instead of landing under a null team; `54/54 PASS, 0
FAIL, 3 FIND` via `apps/api/scripts/verify_litellmphased1b.py`; see the entry
below); previously 2026-09-15 (litellmphased1a.structural — LiteLLM Phase D1a,
per-org AI provider credential storage: an org_admin can now supply their
own provider key via `PUT /orgs/{org_id}/settings/ai-credentials/{provider}`,
which provisions a real, dedicated LiteLLM deployment for that (provider,
org) pair and flips `org_settings.ai.credential_source.{provider}` to
`'org'`; removing it (`DELETE` on the same route) deprovisions the
deployment and reverts to `'platform'`. Central finding: `POST /model/new`
accepts a LITERAL `api_key` value, not just `os.environ/<NAME>` indirection
— proved live (real deployment, real call, real 200) — so an org's key is
passed to LiteLLM once and never stored in our own database. Deployment
names are internal-only, never returned by any org-facing response
(confirmed by grep). No routing/spend-attribution/alerting built yet —
scoped out on purpose, see the entry below); previously 2026-09-14
(orgadminwrites.structural — closed the invite/
promotion/demotion write-path gap that org_admin role reconciliation
deliberately left open: `create_invite` and `assign_role` now keep `users.role` and the
real RBAC grant in lockstep both ways, plus a newly-found org-blind role
lookup fixed in the same pass; `32/32 PASS` via `apps/api/scripts/
verify_orgadminwrites.py`; see the entry below); previously 2026-09-14
(org_admin role reconciliation — org_admin is now a
real RBAC role resolved by permission (`manage_org_settings`), not a
`users.role` string; the single real holder migrated additively and proven
count-for-count; alert recipient resolution and every org-admin-gated page
switched to permission-based resolution; a zero-recipient alert now leaves a
findable `audit_log` row instead of failing silently; `35/35 PASS` via
`apps/api/scripts/verify_orgadminrole.py`; two real call sites — the invite
flow and the user-management role dropdown — still WRITE `users.role`
directly with no corresponding RBAC grant, see the entry below); previously
2026-09-13 (Registry defects + escalation_reason enum — five `assistant_action_catalog` module/action_key drifts corrected per-key (one rename, four module fixes), `crm.draft_note.reversible` fixed with a new undo-path gap surfaced and recorded, `escalation_reason` enum reconfirmed and still deliberately unwired; see the entry below); previously 2026-09-10 (RLS enforcement cutover — DATABASE_URL now genuinely `app_service` in Doppler, code-level proof complete via the real application against the real database (21/21 PASS across smoke-test/cross-org-isolation/scheduler-tick), a second real RLS gap found and fixed live (`main.py` startup `sync_catalog`); Render redeploy confirmation still owed — see the entry below); previously 2026-09-07 (Altruist Sprint 6 — Realtime API webhook receiver, DISCOVERY ONLY, STOPPED per standing rule, schema decision owed; Altruist Sprint 5 — sync orchestration endpoint + auto-trigger on connect; Altruist Sprint 4 — connection lifecycle API + automatic token refresh; Altruist Sprint 3 — positions/transactions sync for resolved accounts; Altruist Sprint 2 — household/account identity resolution; Altruist Sprint 1 — OpenAPI OAuth2 connection scaffold; TA Model Sprint 4 — calibration UX + obligation ledger integration, ALL FOUR TA MODEL SPRINTS COMPLETE; Fee module fee43 — invoices, reconciliation, GL posting)

## About this file

This file records work that is **blocked on something outside the codebase** —
an AWS console change, a vendor contract, a credential only Joe can provision —
so that a blocked item is tracked in one place instead of living in a code
comment that the next sprint deletes.

**A note on this file's own history, since it matters for how much to trust
older references to it:** several earlier sprints
(`verify_litellmreloadaction.py`, `verify_portfolioc.py`, `verify_portfolioux1.py`,
`verify_superadminmenu.py`) state that a follow-up was "recorded as a tracked
follow-up in docs/PROJECT_STATUS.md". **The file did not exist** — it was never
committed and git shows no deletion. Those sprints' follow-ups are therefore
*not* recorded here yet and have not been back-filled by this sprint. If you are
looking for one of them, it is in that sprint's verify script and log, not here.
This file starts with the email item below.

---

## 0000000000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep7b (sprint 7b): account authority and custody — household authority (read_only / advise / managed) overridable per account, maker-checker; custody (integrated / other custodian / outside); the guard re-keyed on authority; advise one-off proposals + letter; managed at another custodian → manual DVP settlement instructions; verify WRITTEN, not yet run (2026-10-27)

Design, matrix and findings 1a–1e: `docs/REPLICATION_STUDIO.md` (rep7b section).
Schema `docs/rep7b_part1.sql` — APPLIED (`scripts/_apply_rep7b.py --schema`) and read back
(`--verify`: SCHEMA OK — 3 new tables, 32 constraints, 7 partial uniques, RLS with a NULLIF org
policy for all four operations, guards, grants, 3 permissions on every org_admin role).
`--migrate` ran: 5 live accounts, all UNCHANGED on every path. Verify:
`apps/api/scripts/verify_rep7b.py` — WRITTEN, NOT RUN (the sprint runs no verify script and no
`npm run build`). Run during the sprint: `authz1_routes.py` (11 new routes, every one gated, no
existing gate changed, `--check` CURRENT) and `node --test tests/` 389 pass / 0 fail (1 skip,
pre-existing), including 6 new rep7b render tests.

- **Authority × custody (one matrix, `services/authority.py`).** read_only: nothing. advise (any
  custody): a one-off proposal (manual, no model) → instructions executed by the member or another
  adviser, each with rep2's letter of instruction; never routed to an adapter. managed + integrated:
  as before. managed + other custodian: every model path; instructions are manual DVP settlement
  instructions (broker / trader) with the receiving custodian's details and T+1. managed + outside:
  refused as a setting (request 409 + CHECK).
- **Effective authority** = account override ?? household ?? the migrated default (held-away →
  read_only / outside; else managed / integrated — rep7's rule exactly). A household change to
  managed is refused while an inheriting account is outside the platform (named).
- **Changes** open the human task `approve_authority_change` (new permission `approve_authority`;
  not `approve_held_away` — approving trading authority is a different decision from approving a
  data import). Approval withdraws (expires, task withdrawn) every open draft / pending proposal
  the new authority forbids, with the plain reason; an approved-not-routed one stays approved and
  every route refuses it.
- **[FIND] the "5 real accounts" are all in the leftover REP2 fixture orgs** (`…e2001` 4, `…e2002`
  1; is_test) — no non-test org has an account. So the migration WRITES NO ROWS (a settings row
  would also put an FK in front of verify_rep2's own teardown); the default is computed at read time.
  The held-away DRAFT instruction (`4de3c1ac…`, member, 2026-10-26) is kept; its approval (submit
  and the task precheck) is refused with the read-only reason.
- **[FIND] "advise ⇔ not discretionary" NOT adopted.** A non-discretionary managed account that
  follows a model gets drift / model-change proposals as advice (rep5); mapping it to advise would
  refuse them — an unapproved behaviour change. One source per decision: paths ← authority ×
  custody; managed executor ← rep5 advice_only; advise executor ← the authority.
- **[FIND] no holiday calendar exists** — T+1 counts weekends only, stated on the settlement row
  (`calendar_basis = 'weekends_only'`), the PDF and the panel.
- **[FIND] fills do not book lots** (rep2). On a held-away account the lots arrive through the next
  approved held-away import at the reported price; a reconcile can name the held-away transaction
  record (`reconcile_method 'custodian_data'`, reference `held_away_record:<id>`). No second lot
  write path was added.
- **[FIND] naming:** `services/trading_authority.py` (SOC phase 5, per-USER tiers) is unrelated to
  rep7b's per-ACCOUNT `services/authority.py`.
- `rebalance_proposals` gained `proposal_kind` ('model' | 'one_off'); `link_id` / `model_id` /
  `model_version_id` are nullable ONLY for a one-off (CHECK `rebalance_proposals_kind_chk`).
- `verify_rep7.py` changed in ONE line (1408): the confirmation's `expires_on` expectation is
  computed at the call (`date.today()`), not from the script-start `TODAY` (a run crossing midnight
  failed by one day). Nothing else changed.
- Nothing about billing for advising on held-away assets is shown anywhere (open: Joe + counsel).
  Block trading in an omnibus account, allocation and automated DVP remain rep9.
- Operator: run `verify_rep7b.py` (it runs `node --test`, `npm run build` and the `verify_rep7.py`
  regression itself — VERIFY_NESTED=1, 2700 s); redeploy the API; refresh the schema snapshot.

UPDATE 2026-10-27 (rep7b fix 1, after verify_rep7b ran 104 PASS / 12 FAIL): NO code defect. All fixes are in the verify scripts. (A) `verify_rep7b.py` y_changes unpacked an async generator (`(await … for …)`); it now uses sequential awaits, so ACCT_DVP reaches managed + other_custodian. FAILs F–K (DVP proposal, settlement rows, matrix cell, RLS on instruction_settlements) were cascades: reading the code, join → generate → route → `create_instructions` already writes the DVP settlement row in the same transaction. (B) The checker's 409 on the advise one-off was rep7's staleness rule, working correctly: every ORG_A fixture account shares one primary entity, which rep6's household includes, so the one-off depends on ACCT_DVP's deliberately stale data. The verify now proves the 409 names `confirm_held_away_data` and the account, confirms, then approves. (C) The migration check's `"CHANGED" in line` also matched every UNCHANGED line; it now parses each line's verdict, matches the reported count to `SELECT count(*)` of live accounts, and reports zero non-test accounts as a [FIND]. (D) `verify_rep7.py` y_routes is scoped to rep7's own 19 routes: rep7b's 7 `/authority/…` routes and its 4 others (both one-off routes, `…/settlement`, `…/reconcile-held-away`) are excluded by explicit list. Its TODAY fix is unchanged. verify_rep7b's regression diff guard now allows exactly those lines. Re-run `verify_rep7b.py` pending (operator).

## 000000000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep7 (sprint 7 of 15): held-away accounts and the portfolio X-ray — sources (manual, file, Chancery statement, aggregator interface — none connected), maker-checker imports, held-away data in wash-sale / gain budget / loss bank, the stale-data confirmation task, the trade-path guard, the X-ray + one-click join; verify WRITTEN, not yet run (2026-10-26)

UPDATE 2026-10-26 (rep7 fix 1, after verify_rep7 ran 173 PASS / 7 FAIL): `services/xray.py` — `_iso` called `.isoformat()` on `tax_profiles.combined_rates`' `as_of`, already an ISO str, so every X-ray with an approved tax profile 500'd (the run, both sweeps, the org-admin read, the transition tax / break-even — one cause, not a lookup mismatch); `_iso` now accepts date | datetime | str | None and the rates' as_of is passed through. Overlap between funds re-added a fund's file weights once per account holding it (FUND in two accounts → C1 1.0 → min 0.6); each fund's own file weights are now built once per snapshot (0.5, the expectation, stands). `verify_rep6.py` self-checks scoped to rep6: y_rep5_diff allows rep7's held-away join refusal in y_join / y_execution; the routes check ignores rep7's `/held-away/` and `/xray/` prefixes (still 31 under /tax/ and /harvest/). Re-run `verify_rep7.py` pending (operator).

Design + findings 1a–1e: `docs/REPLICATION_STUDIO.md` (rep7 section).
Schema `docs/rep7_part1.sql` — APPLIED (`scripts/_apply_rep7.py --schema`) and read
back (`--verify`: SCHEMA OK — 6 new tables with a policy for every operation, 7
partial uniques, every CHECK and guard trigger, the loss-bank source `held_away`, the 3
held-away permissions on every org_admin role). Verify: `apps/api/scripts/verify_rep7.py`
— WRITTEN, NOT RUN (WSL memory; it runs `node --test tests/`, `npm run build` and the
`verify_rep6.py` regression itself). In-sprint proof only: the app imports cleanly, pyflakes
clean on every new file, rep3's mapping reused on a sample CSV (the model vocabulary
unchanged), `node --test tests/replication/` 42/42 (incl. 8 new rep7 render tests), and
`authz1_routes.py --check` CURRENT (19 new routes, no existing gate changed).

- **Custody cliff held — a held-away account is NEVER traded.** One guard per trade
  path (`services.held_away.refuse`): join / transition preview, every proposal trigger,
  execute, harvest scan, instruction creation (and `route_trade_list`).
- **[FIND] behaviour change:** before rep7 a held-away account was ROUTED TO MANUAL
  (rep2) and its proposals' instructions went to the member (rep5). Both now refuse;
  `verify_rep2.py` / `verify_rep5.py` assertions restated (their held-away join and
  instruction now expect 409). `verify_rep6.py` uses no held-away account.
- **Data lives in portfolio.positions / tax_lots / transactions** with provenance in
  `held_away_records` (source, import, as-of) — every rep5 / rep6 reader sees it.
- **Imports:** draft → a second holder of `approve_held_away` approves (human task;
  DB maker-checker CHECK); an approved import replaces positions as of its date
  (earlier rows closed, kept). Older-than-current data and refused files are refused
  BEFORE the task commits (new `human_tasks.register_precheck`).
- **Statements:** ensemble task key `statement_positions` (added to KNOWN_TASK_KEYS);
  no active config → "no extraction models configured" + manual entry; Claude refused.
- **Tax:** held-away trades count in household wash-sale control (an IRA held elsewhere
  blocks via rule c); a declared trade matched by a held-away transaction counts once;
  `realized_ytd` adds gains realized in held-away accounts of the same subject; the loss
  bank takes held-away closed lots as source `held_away`.
- **Staleness:** as-of older than `stale_after_days` (exactly N days is fresh) → the
  proposal shows the date and cannot be approved until `confirm_held_away_data` is
  completed; confirmations expire on newer data or after N days.
- **X-ray:** look-through by holdings file (as-of on every line), sectors from the file
  (else "unclassified"), concentration, overlap, fee difference / embedded gain /
  transition tax / break-even as estimates; tracking error, harvest value of switching,
  after-tax performance: "not estimated — needs prices". One-click join → rep5's
  `propose_change` for MANAGED accounts only.
- **[FIND]** `org_admin` is not a staff role: X-ray household access admits Super Admin,
  staff AND holders of `manage_org_settings` (org admins); others via the visibility
  engine (404 otherwise).
- **[FIND]** rep3's mapping functions were module-global and hard-wired to a weight
  column; they now take an optional field vocabulary (defaults unchanged).
- Operator: run `verify_rep7.py`; redeploy the API and the scheduler cron service (the
  tick now runs the staleness check and the X-ray sweep). Optional org settings:
  `replication.held_away.stale_after_days` (30), `replication.xray.single_name_pct` (0.10).

## 00000000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep6 (sprint 6 of 15): the tax engine — tax profiles + carryforwards, the substitution table + denials, household wash-sale CONTROL (replacing rep5's flag), daily loss harvesting as proposals, the loss bank, declared outside trades, the custodian-harvest switch, the Tax screens; verify WRITTEN, not yet run (2026-10-24)

Design + findings 1a–1e: `docs/REPLICATION_STUDIO.md` (rep6 section).
Schema `docs/rep6_part1.sql` — APPLIED (`scripts/_apply_rep6.py --schema`) and read
back (`--verify`: SCHEMA OK — 9 new tables with a policy for every operation, 18
partial uniques, every CHECK and guard trigger, the 3 tax permissions on every
org_admin role, and the rep5 changes). Verify: `apps/api/scripts/verify_rep6.py` —
WRITTEN, NOT RUN (WSL memory; it runs `node --test tests/`, `npm run build` and the
`verify_rep5.py` regression itself). In-sprint proof only: the pure rules on
hand-built inputs (both window edges, replace / defer / block, IRA losses, rule b in
`plan_sales`, IRS netting with the $3,000 / $1,500 limit, the harvest arithmetic),
every new SQL statement PREPAREd against the live catalog (232 statements across the
services, the router and the verify script, 0 errors), a TypeScript transpile of every
new / changed web file, and `authz1_routes.py --check` CURRENT (31 new routes, no
existing gate changed). `node --test` was NOT run in the sprint.

- **Custody cliff held:** a harvest is a rep5 PROPOSAL (trigger 'harvest', the
  system's, on the review task); a person approves; its instructions go to rep2.
- **Wash-sale control** (`services/wash_sale.py`) on every proposal: 61-day window
  (day 30 in, day 31 clear, both directions), every household account + declared
  outside trades + open instructions + pending proposals; substantially identical =
  same security or an ACTIVE denial. Outcomes clear / replaced / deferred / blocked on
  every line; a deferred or blocked line is never routed (DB CHECK backs it).
- **[FIND]** a loss realized INSIDE an IRA / Roth is not deductible, so it opens no
  wash window (an IRA BUY still counts — Rev. Rul. 2008-5 — and is a hard block).
- **[FIND]** `accounts.tax_status` is free text with no CHECK (the importer writes
  `UNKNOWN` when the file has none); an unknown status is treated as taxable.
- **[FIND] verify_rep5:** its IRA "bought X 10 days ago" fixture is now CONTROL, not a
  flag, and would hold the transition's X loss lot (breaking rep5's gain-budget
  arithmetic checks). The fixture moved to 31 days (day 31 = clear); y_wash restated
  as outcomes; y_execution's "flags never block" restated. Nothing else changed;
  verify_rep6 checks the diff line by line.
- **[FIND]** no tax-rate data existed anywhere; no default is ever used — "not
  estimated — no tax profile". The NIIT 3.8% is the statutory rate, stored on the row.
- **Loss bank sources** counted once: closed lots (the custodian's number), fills
  (provisional, closed when the custodian's lot arrives), approved carryforwards,
  declared outside sales. Transactions carry no gain and are not a source.
- **Not estimated — needs prices:** tracking-error cost of a harvest; after-tax
  performance (`securities_global_prices` has 0 rows).
- Operator: run `verify_rep6.py`; redeploy the API and the scheduler cron service (the
  tick now ingests the loss bank and runs the harvest scan before the drift scan).
  Optional org settings: `replication.tax.trading_cost_bps` (0),
  `.harvest_loss_pct` (0.05), `.harvest_loss_min` (500), `.harvest_to_cash` (false),
  `.harvest_swap_back` (false), `.wash_buy_fallback` ('defer').

## 0000000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep5 (sprint 5 of 15): accounts join models (account-level settings + exclusions), the lot-level transition analysis under a gain budget, rebalance PROPOSALS from every trigger, approval on human tasks, approved proposals → execution instructions through rep2; verify WRITTEN, not yet run (2026-10-23)

Design + findings 1a–1e: `docs/REPLICATION_STUDIO.md` (rep5 section).
Schema `docs/rep5_part1.sql` — APPLIED (`scripts/_apply_rep5.py --schema`) and read
back (`--verify`: SCHEMA OK — 4 new tables with a policy for every operation, 7
partial uniques, every CHECK and guard trigger, the 3 permissions on every org_admin
role, and the two rep2 guard changes). Verify: `apps/api/scripts/verify_rep5.py` —
WRITTEN, NOT RUN (WSL memory; it runs `node --test tests/`, `npm run build` and the
`verify_rep2.py` regression itself). In-sprint proof only: the pure engine on
hand-built inputs (exclusion policies, lot order, gain budget, plan by year), every
new SQL statement PREPAREd against the live catalog (91 statements, 0 errors),
`node --test` on tests/replication, nav, models and execution (64 pass, 1 pre-existing
skip), and `authz1_routes.py --check` CURRENT (17 new routes, no existing gate changed).

- **Custody cliff held:** every trigger produces a PROPOSAL; an approved proposal
  becomes manual execution instructions a person sends, fills and reconciles.
  Nothing is sent anywhere automatically.
- **Accounts join, never fork:** `services/account_models.py` — a versioned
  `account_model_links` row per change (join / settings / change of model / end), each
  on a maker-checker human task (`approve_proposals`). Personal settings on the
  ACCOUNT: exclusions with reasons + policy (pro_rata / to_cash / use_alternates),
  cash floor, drift-threshold and frequency overrides, gain budget, short-term guard,
  harvesting engine (custodian only when evidenced), fee override.
- **Fees through the fee module:** the join's approval writes the ACCOUNT-scope
  assignment (`create_assignment`) and, while the replicated fund is held, a SECURITY
  exclusion per held asset (new `create_exclusion`). The scheduler tick ends the
  exclusion once the asset is no longer held.
- **[FIND]** nothing wrote `fee_exclusions` before this sprint (fee35 reads it,
  `validate_exclusion` existed but nothing called it). Added
  `fee_schedules.create_exclusion / end_exclusion / list_exclusions`. A SECURITY basis
  is the org's `portfolio.assets.id`, not a global security id (fee_calc matches
  `position.asset_id`).
- **[FIND]** the fee module gives assignments and exclusions no approval of their
  own. The join task (whose payload names them) is their approval.
- **[FIND] (1c)** `domain_events` fans out only to BPMN workflow triggers; there is no
  in-process subscriber. 'model.version_approved' is consumed by a direct call from
  rep3's `after_approval`, plus a tick sweep of the last 7 days (idempotent per
  account + model version).
- **[FIND] (1d)** `execution_instruction_lots_guard` admitted lots only while the
  instruction was a DRAFT. Changed narrowly: lots may also go onto an approved,
  proposal-sourced instruction, but only in the transaction that creates it. A new
  BEFORE INSERT guard admits an 'approved' instruction only from an approved proposal
  whose maker and checker it carries. Every rep2 CHECK is unchanged.
- **[FIND]** a SYSTEM proposal (drift / cash flow / model change) has no human maker,
  and rep2's CHECKs need one distinct from the approver. Its instructions are
  therefore drafts made by the reviewer, submitted to rep2's own approval (a second
  person). Only a person's proposal starts its instructions APPROVED.
- **[FIND]** `account_balances_daily` has no cash flow or withdrawal concept. The
  cash_flow trigger is cash above the account's cash target (the floor, or the
  model's cash weight when higher) by more than an org threshold, plus a
  staff-entered withdrawal.
- **[FIND]** transactions carry no realized gain. "Already realized this year" is
  closed `tax_lots.realized_gain_loss` whose CLOSING transaction's trade date is this
  year. A loss for the wash-sale flag is read the same way, and a sale with no lot
  record is flagged as "loss unknown", never dropped.
- **Name:** a manual-custodian variant now reads "Holdings of X — Manual"
  (`replica_models.VARIANT_NAME_LABELS`). The capability matrix keeps its own label.
- **.gitignore:** `sprint_prompts/logs/` ignored entirely; the tracked log files were
  `git rm --cached` (they stay on disk).
- Operator: run `verify_rep5.py`; nothing else to apply (the schema is live; the
  scheduler entrypoint gains a `proposals_tick` step — redeploy the cron service to
  pick it up). Org settings (all optional, with defaults):
  `replication.proposals.min_trade_size` (100), `.expiry_days` (5),
  `.stale_valuation_days` (3), `.cash_flow_threshold` (0.02), `.execute_within_days` (5).

## 000000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep4 (sprint 4 of 15): build a model from a fund — "Holdings of <TICKER>" models, the five-question viability check, fund-vs-replica cost, find-or-build registry, holdings / terms monitoring; verify WRITTEN, not yet run (2026-10-23)

Design + findings 1a–1e: `docs/REPLICATION_STUDIO.md` (rep4 section).
Schema `docs/rep4_part1.sql` — APPLIED (`scripts/_apply_rep4.py --schema`) and read
back (`--verify`: SCHEMA OK — 3 new tables with a policy for every operation, every
partial unique, CHECK, FK and guard trigger; rep3's origin / event CHECKs widened).
Verify: `apps/api/scripts/verify_rep4.py` — WRITTEN, NOT RUN (WSL memory; it runs
`node --test tests/`, `npm run build` and the `verify_rep3.py` regression itself).
In-sprint proof only: a local run of the pure construction math (full / top N /
min weight / cash, exact sums), `node --test tests/replication/rep4.test.mjs` 7/7
and `rep1.test.mjs` 9/9, `authz1_routes.py --check` CURRENT (8 new routes, no existing
gate changed).

- **Custody cliff held:** a build is a DRAFT approved through rep3's maker-checker
  human task; monitoring drafts versions for REVIEW; nothing trades.
- **[FIND]** `securities_global_prices` is EMPTY (0 rows, 2026-10-23) — Q3 (account
  size) is "unknown — needs prices" for every real fund until a price feed exists;
  an N-PORT value / balance quotient is never used as a price.
- **[FIND]** rep1's refresh has no "new snapshot" event — only its return value; the
  model monitor hooks both refresh paths (weekday + manual) on that value.
- **[FIND]** rep3's `create_model` refused `fund_replica` by design; added
  `models.create_fund_model` (the only path) and `_submit_system(extra_payload=…)`
  so the review task carries the holdings diff (both additive).
- **[FIND]** rep3's version origins / event actions are DB CHECKs — widened
  (`fund_build`, `fund_monitor`; `fund_build_joined`, `holdings_monitor`,
  `terms_monitor`, `terms_review_done`), and monitoring is idempotent by partial
  unique indexes on `model_events`, not by a code check alone.
- **[FIND]** model names are unique per org, so the same fund + method for a second
  custodian gets the custodian label appended ("Holdings of QQQ — Manual — execution
  instruction").
- **[FIND]** weight changes between snapshots include price moves, so the trading-
  cost estimate (turnover × 2 sides × bps) overstates trading — stated in its inputs.
- **Regression scripts fixed (test defects, not behaviour):** `verify_rep3.py` and
  `verify_rep2.py` counted EVERY route added since their base ref (rep2 has been
  wrong since rep3 merged); both now count their own routes by path. `verify_rep3.py`
  skips `npm run build` under `SKIP_BUILD=1` (a nested run; the outer verify builds).
- Follow-ups: rep5 links accounts to models ("accounts using" is 0 until then) and
  consumes `model.version_approved`; rep6 estimates the tax line; a price feed for
  Q3; the `replication.viability.*` / `replication.models.*` settings have no
  settings-screen fields yet.

## 00000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep3 (sprint 3 of 15): model management — one registry, uploads + column mapping, a permanent upload log, the model library, composite sleeves, approvals on human tasks (and rep1's terms approvals moved onto them); verify WRITTEN, not yet run (2026-10-23)

Design + findings 1a–1e: `docs/REPLICATION_STUDIO.md` (rep3 section) and `docs/HUMAN_TASKS.md`.
Schema `docs/rep3_part1.sql` — APPLIED (`scripts/_apply_rep3.py --schema`) and read
back (`--verify`: SCHEMA OK — 10 tables, every partial unique, trigger and a policy for
every operation). Verify: `apps/api/scripts/verify_rep3.py` — WRITTEN, NOT RUN (WSL
memory; it runs `npm run build` and the `verify_rep1.py` regression itself).
In-sprint proof only: a rollback-only service smoke (every write inside one
transaction that was rolled back — 29/30, the one miss a test-string mismatch on an
exact value), a rollback-only smoke of the moved rep1 path (7/7), `node --test
tests/models` 10/10 (nav / execution / replication suites still 37 pass, 1 skip),
`authz1_routes.py --check` CURRENT (28 new routes, no existing gate changed).

- **Custody cliff held:** approving a model version never trades; it emits
  `model.version_approved` (with the diff) for rep5's rebalance PROPOSALS.
- **Operator step:** `pip install -r apps/api/requirements.txt` (adds `xlrd==2.0.1`)
  before running the verify; until then a .xls upload is logged `failed`
  (`reader_unavailable`) and the verify's .xls proofs FAIL by design.
- **rep2 test defect fixed:** `verify_rep2.py`'s CSV dry-run check now reads the
  exception count from `plan.exceptions` (0 on that file — the blank-number line is
  skipped by the adapter); the importer is unchanged.
- **[FIND]** an unresolved model row cannot join the security master's unresolved
  queue (`securities_global_relationships` needs a `from` security; a model is not
  one) — it is queued on the version itself (`resolution_state 'unresolved'`, raw
  identifiers kept) and blocks approval.
- **[FIND]** `human_tasks.open_task` required a human maker, so a system import
  (subscription update, composite following a child) could not open its review
  task. Added `system_open` kinds (additive; agents still refused; completing still
  needs a person).
- **[FIND]** rep1's makers are everyone who edited a draft, which the engine's single
  `started_by` cannot express — the terms task kind is NOT engine-level
  maker-checker; rep1's own 403 (same text) and its DB CHECK remain the gate.
- **[FIND]** a deployment's request-body limit (Vercel 4.5 MB) sits below the 5 MB
  upload cap; a file between the two is refused by the Next.js proxy.
- **[FIND]** version status `submitted` added (not in the prompt's list): content is
  frozen while an approver decides; a rejection returns the version to `draft`.
- Follow-ups: rep4 builds `fund_replica` models ("Holdings of" names); rep5 consumes
  `model.version_approved`; per-org weight tolerance key
  `portfolio.models.weight_tolerance_pct` (default 0.05%) has no settings-screen field yet.

## 0000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep2 (sprint 2 of 15): custodian capabilities, trade adapters (Altruist → manual, manual), governed human tasks on the workflow engine, execution instructions with a letter of instruction; verify WRITTEN, not yet run (2026-10-22)

Design + findings 1a–1d: `docs/REPLICATION_STUDIO.md` (rep2 section) and `docs/HUMAN_TASKS.md`.
Schema `docs/rep2_part1.sql` — APPLIED (`scripts/_apply_rep2.py --schema`) and
read back (`--verify`: SCHEMA OK). Verify: `apps/api/scripts/verify_rep2.py` —
WRITTEN, NOT RUN (WSL memory; it runs `npm run build` and the
`verify_workflowmgr5.py` regression itself).

- **Custody cliff held:** no trade or money leaves the system; every trade becomes
  an execution instruction a person approves (maker ≠ checker), sends, fills,
  reconciles and closes.
- **Engine change (additive):** `workflow_steps.assigned_permission` (NULL on every
  existing step = unchanged behaviour) + `cancel_user_task`.
- **rep1 gap closed:** a new org's first org_admin now gets the three replication
  and three execution permissions (`rbac.ensure_org_admin_role`).
- **[FIND]** `public.accounts`, `portfolio.positions` and `portfolio.tax_lots` hold
  ZERO rows today — lot instructions and the CSV-unchanged proof run on fixtures.
- **[FIND → decided 2026-10-23]** capabilities read was `require_staff`; `org_admin` is
  not a staff role, so org admins could not see the matrix. Now staff OR
  `manage_org_settings` (`require_staff_or_permission`); write stays Super Admin only.
- **[FIND]** the database stores only the masked number and a salted hash, so a
  letter with the full number needs the person to type it (checked against the hash).
- **[FIND]** the system human-task definitions appear in each org's Workflow library.
- Follow-ups: rep3 moves rep1's terms approvals onto `services.human_tasks`; rep9 FIX
  route; Altruist trading stays manual until a trading client + an evidenced
  `equity_trading_api = yes` exist.

## 000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep1 (sprint 1 of 14): security registry, fund holdings ingestion (issuer files + N-PORT fallback), fund terms extraction (no Claude), the correct / deliberate-change / maker-checker terms flow; verify WRITTEN, not yet run (2026-10-22)

Design + operator guide: `docs/REPLICATION_STUDIO.md` (incl. Task 1 findings 1a–1f).
Schema `docs/rep1_part1.sql` — APPLIED (`scripts/_apply_rep1.py --schema`) and
read back (`--verify`: SCHEMA OK, 91 checks). Verify: `apps/api/scripts/verify_rep1.py`
(written, NOT run — the sprint does not run verify scripts or npm build);
regression for this sprint: `verify_underlyingresolution.py` once (it shares the
security master + relationships queue). Added to `scripts/regression_set.txt`.

What exists:
- Platform tables (shared, fetched once): instrument classes, holdings sources,
  snapshots + rows (immutable), source documents, terms field registry (20
  fields; critical: gross/net expense ratio, index name, weighting, rebalance),
  term readings, refresh events. Org tables: `replica_securities` (registry,
  "Holdings of <ticker>" by CHECK), `replica_terms_versions`, `replica_terms_actions`.
- Ingestion (`services/replica_holdings.py`, CLI `scripts/replica_ingest.py`):
  same bytes = no snapshot; validation → needs_review; diff + threshold;
  constituents resolved only by a published identifier through
  `securities_global` (bulk variants added in that module), else queued as
  'unresolved' `constituent_of` edges.
- Terms (`services/replica_terms.py`): prospectus (497K, else 485BPOS) via the
  EDGAR client; two readers through the LiteLLM proxy; quote verification;
  draft with needs-review flags; corrections → `document_field_corrections`
  (`target_type 'replica_terms'`, CHECKs widened); deliberate changes →
  departures; approve/reject with maker-checker (app 403 + DB CHECK); one
  approved version live; approved rows never edited (trigger).
- Every human action → action row + audit_log + `domain_events`
  ('replication.<action>'); approval-needed / holdings-review → `member_todos`.
- Weekday refresh 11:00 UTC from `workflow_scheduler_tick.py` (after the EDGAR
  index refresh); `REPLICA_HOLDINGS_AUTO_REFRESH`.
- API `/api/v1/replication/*` (10 routes, view/manage/approve_replication; AUTHZ1
  --check CURRENT; no existing gate changed). Permissions granted to every
  existing org_admin role.
- Screens: Portfolio → Replication (registry, Add, entry page with Terms /
  Holdings / Sources); node tests `apps/web/tests/replication` (9/9 pass).

Findings:
- [FIND] `ai_ensemble_configs` has NO rows live — extraction records 'blocked'
  until a Super Admin activates one for `replica_terms_extraction` (task key added
  to KNOWN_TASK_KEYS and PUBLIC_DATA_TASK_KEYS).
- [FIND] Invesco answered 406 and iShares an HTML page to a non-browser client;
  both sources are seeded INACTIVE (parsers follow documented layouts, unconfirmed).
  SSGA (SPY) confirmed live. SPY has no SEC series (UIT) → issuer file only.
- [FIND] N-PORT lag 2–5 months — fallback only.
- Operator: the daily refresh needs the same Render cron service bulkfetchui3's index refresh
  needs (`workflow_scheduler_tick.py`); not deployed → neither refresh runs.
- [FIND] Issuer-site terms of use NOT reviewed — counsel before production.
- [FIND] `rbac.ensure_org_admin_role` (an org's first org_admin) grants only
  manage_org_settings: a NEW org's org_admin lacks the replication permissions
  until the §14 grant is re-run.
- [FIND] First ingestion of a large fund queues most constituents (the master
  holds few equities): SPY's 504 names land in the resolution queue.

## 00000000000000000000000000000000000000000000000000000000. BULKFETCH UI 3 — Bulk download: sortable full-height Institutions table with Downloaded / Stored and Refresh; Refresh index moved into the Institutions box; a weekday automatic index refresh; GS image-cover finals no longer 'other' + a reclassify command; zero-byte downloads retried; scripts/run_regressions.sh; verify WRITTEN, not yet run (2026-10-22)

Discovery: `docs/BULKFETCHUI3_DISCOVERY.md` (1a–1d, with SQL and live totals).

**Bulk download tab.** `InstitutionPicker` — every header sorts (click toggles
asc/desc, `aria-sort`, an arrow on the sorted column; numeric for counts,
case-insensitive for names; filer sub-rows sort the same way under their
institution; default Institution ascending). New columns **Downloaded** and
**Stored** after Total (labels + one-line explanations from the envelope's
`vocabularies.institution_columns`). The card fills the Institutions box (xl:
as tall as the right-hand column, table scrolls inside with a sticky header;
below xl: max 70vh); no `max-h-80`. Header row: Institutions, search, Select
all, Select none, **Refresh** (re-reads with `?fresh=1`, "as of h:mm"), then —
only when writable — **Refresh index** with "Index loaded through <date>."
(removed from the Time frame card); it reads "Index refresh queued" / "Index
refreshing" and is disabled while such a run is active. Counts re-read every
60 s only while a bulk run is queued or running.

**API.** `GET /admin/edgar/bulk/institutions[?fresh=1]` (same route, same super
admin gate; `fresh=1` bypasses the 60 s cache) now returns per institution and
per filer `downloaded` and `stored` (all years, the same scope as Total) and
`as_of`. Downloaded = pipeline_status in fetched, fetch_failed,
not_pricing_supplement, prefilter_skipped, ready_for_extraction (+ the later
extraction statuses); Stored = the reference_filings row still has `r2_key` or
`text_r2_key`. Live 2026-10-22: all institutions 21,877 downloaded / 11,667
stored; Goldman Sachs 12,496 / 6,120; Wells Fargo 1,092 / 1,092; Jefferies
1,217 / 602. No route added; `authz1_routes.py --check` CURRENT.

**Weekday automatic index refresh** (`services/edgar_index_schedule.py`, called
from `apps/api/workflow_scheduler_tick.py` AFTER the workflow tick — not inside
`run_scheduler_tick`, which verify scripts call directly). Monday–Friday, the
first tick at or after 10:30 UTC refreshes the index through yesterday and
writes an `edgar_pipeline_runs` row (run_kind refresh_index, trigger_source
**'schedule'**, target 'render', counts in details). Once per UTC day by a
partial unique index on `details->>'schedule_date'`; a failure is recorded on
the row (and the tick exits 1 so Render shows it) and holds only that day. Off
switch: `EDGAR_INDEX_AUTO_REFRESH=0` (default on). SEC rate capped at 2/s. The
runs table shows it as "Refresh index (Scheduled)" with no Stop / Requeue.
Migration `apps/api/migrations/bulkfetchui3_scheduled_index_refresh.sql` —
**APPLIED live** by the sprint (CHECK widened + the unique index).

**OPEN — operator:**
1. Deploy this commit to the Render cron service
   `2ndactcapital-workflow-scheduler` (Manual Deploy). Whether that service is
   actually running is NOT provable from the repo or the database (no scheduled
   trigger has ever fired) — check its recent runs in the Render dashboard; if
   it does not exist, create it from `render.yaml` and give it its OWN Doppler
   sync (hollisworks / prd).
2. Confirm `EDGAR_USER_AGENT` and `DATABASE_URL` are set on it.
   `EDGAR_INDEX_AUTO_REFRESH` is optional (unset = on).
3. Next weekday after 10:30 UTC, look for a "Refresh index (Scheduled)" row on
   the Bulk download tab. None = the tick is not running.

**Classifier.** Goldman Sachs finals render the cover title "Pricing
Supplement" as an image, so the opening had no title phrase → 'other' →
not_pricing_supplement. New rule (`edgar_pipeline._classify_cover_line`): when
the opening has no title phrase, a `(pricing|prospectus) supplement no. <n>
dated <Month D, YYYY>` line in the first 12,000 characters, with no preliminary
marker in that window, makes it a `pricing_supplement`. Measured on all 3,243
stored 'other' rows: 3,129 GS match (2,210 "Pricing Supplement No.", 919
"Prospectus Supplement No." — the equity-linked series), 55 GS FWPs / summary
supplements stay other, zero matches from any other issuer.

**Reclassify command** (stored text only — no HTTP client on this path):

    # dry run (default): counts by issuer and old→new kind, 10 example accessions
    cd /mnt/c/Users/Joe/2ndActCapital && doppler run -p hollisworks -c prd -- \
        apps/api/venv/bin/python -u apps/api/scripts/run_bulk_fetch.py --reclassify-other

    # write
    cd /mnt/c/Users/Joe/2ndActCapital && doppler run -p hollisworks -c prd -- \
        apps/api/venv/bin/python -u apps/api/scripts/run_bulk_fetch.py --reclassify-other --apply

Optional `--issuer-cik 1419828`, `--accessions A,B`, `--from` / `--to`. Each
changed row moves to the status the normal decision gives it (active policy
version stamped), `status_reason 'reclassified:other→<kind>; …'`; rows with
no stored text are listed and left alone; a second `--apply` changes nothing.
NOT run on real rows by the sprint — the operator runs it.

**Zero-byte downloads.** Before: recorded fetch_failed with `next_attempt_at
NULL` — never retried. Now `EmptyDocumentError` → attempt_count + 1,
`next_attempt_at = now() + 15 min`, gives up after 5 attempts; the bulk scope
takes fetch_failed rows whose retry is due (the nightly queue already did).

**Regression runner.** `scripts/run_regressions.sh` runs every script in
`scripts/regression_set.txt` one after another (`VERIFY_NESTED=1`, 1200 s
timeout, doppler prd, logs in `sprint_prompts/logs/regressions/<date>/`), one
line per script and a summary; exits 1 on any failure, 2 (nothing run) if a
laptop bulk worker is heartbeating. Policy (docs/SPRINT_WORKFLOW_STANDARD.md):
no full regression inside a sprint verify; one directly-affected prior script
only when shared code changes.

    bash scripts/run_regressions.sh

**Verify:** `apps/api/scripts/verify_bulkfetchui3.py` — WRITTEN, not run (the
sprint does not run verifies or `npm run build`). It re-runs
`verify_bulkfetch.py` once (shared classifier). `node --test tests/` was run in
the sprint: 323 / 323 pass.

## 0000000000000000000000000000000000000000000000000000000. DEALROOM2 — Marketplace Deal Room Phase 2: immutable agreement versions (keep signers / require re-signing → resign_required, prior level restored), uploaded PDF agreements read page by page before Sign, the firm template library, the master NDA, the signed copy as a PDF with a certificate page; verify WRITTEN, not yet run (2026-10-22)

**Run:** `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_dealroom2.py 2>&1 | grep -E "^\[FAIL\]|^\[FIND\]|^TOTAL"`.
Written, **not run** (standing rule). R2 is mocked in memory. The regressions
(`verify_dealroom1.py`, `verify_authz1.py`) run ONCE each, flat, after teardown,
with `VERIFY_NESTED=1`. The teardown needs the operator DSN
`DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP` for signatures, agreement versions
and non-draft templates. Design: `docs/DEAL_ROOM.md` § Phase 2. Discovery:
`docs/DEALROOM2_DISCOVERY.md` (1a–1d).

**Applied live (2026-10-22):** `docs/dealroom2_part1.sql` via
`apps/api/scripts/_apply_dealroom2.py --schema`, on the operator DSN, because
the MCP refuses NOT NULL relaxations. Before it was applied, the live DB held 0
deal agreements, 0 signatures and 0 access rows, so there was nothing to
backfill. It added:
- `agreement_versions` (immutable) and `deal_master_ndas` / `_deals`
- `deal_agreements.kind/title/document_id/file_sha256`
- `deal_signatures.agreement_version_id/document_sha256/record_version`
- `deal_access.prior_level/resign_after` and status `resign_required`
- the template guard trigger
- 8 label rows per org

**Run in this session:**
- `node --test tests/` 314/0 (8 new in `tests/dealroom/dealroom2.test.mjs`).
- `authz1_routes.py` regenerated: 14 new `/deal-room` routes, each gated;
  `--check` CURRENT; no existing gate changed.
- reportlab 4.4.4 + pypdf 6.1.1 installed into `apps/api/venv` and pinned in
  requirements.

**`npm run build` NOT run** (laptop memory; standing rule) — the verify runs it.

**Findings:**
- [FIND] The template body was read LIVE at every render, and
  `agreement_templates` was UPDATE-able. A body edit would have silently changed
  what later signers saw, under the same `template_version`. Two fixes:
  - Versions now snapshot the body.
  - A trigger fixes the text of every non-draft template.
- [FIND] The Phase 1 canonical record is fixed, because every existing row's
  hash depends on it. Phase 2 rows hash a wider record, selected by
  `record_version`. The verifier handles both.
- [FIND] DOCX → PDF has no pure-Python path that keeps layout, so uploads are
  **PDF only** (415 otherwise).
- [FIND] An upload's signature still needs a `rendered_text`, because the column
  is NOT NULL and the verifier hashes it. It stores a statement naming the
  document and its file hash, and `document_sha256` holds the file hash.
- [FIND] A master NDA signed on a covered deal renders for its HOME deal, so the
  text names the deal it was written for, not the covered one.
- [FIND] The bulk grid "require re-sign" needed a cut-off (`resign_after`), not a
  version: re-signing the SAME version must count again only when it is signed
  after the cut-off.
- [FIND] `verify_dealroom1.py` needed two named changes:
  - Its privileged purge also deletes fixture `agreement_versions`. Publishing
    now writes them, and app_service cannot delete them, so its teardown would
    otherwise fail.
  - Its grid-actions check accepts the Phase 2 superset (`require_resign`
    appended).
- `docs/schema_snapshot.sql` was not refreshed. The refresh skill pushes, so
  that is left to the operator.

**Follow-ups:**
- Counsel review of the templates still gates outside publishers.
- The master NDA UI sets scope `all`, or "selected = this deal". The API takes
  any list of the publisher's deals.
- Phase 3 is unchanged.

---

## 000000000000000000000000000000000000000000000000000000. DEALROOM1 — Marketplace Deal Room Phase 1: publishers and delegates, a nine-folder data room with access levels, the platform NDA + joinder (DRAFT) with a typed-name e-signature and a hash chain, eligibility from the CRM, automatic approval by default, the access grid; verify WRITTEN, not yet run (2026-10-22)

**Run:** `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_dealroom1.py 2>&1 | grep -E "^\[FAIL\]|^\[FIND\]|^TOTAL"`.
Written, **not run** (standing rule). R2 mocked. Regressions (`verify_authz1.py`,
`verify_bugfix1.py`, `verify_navregroup.py`) run flat after teardown with
`VERIFY_NESTED=1`. The signature teardown needs the operator DSN
`DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP` (postgres) — the only path that can
remove a (test-chain) signature. Full design: `docs/DEAL_ROOM.md`; discovery
`docs/DEALROOM1_DISCOVERY.md` (1a–1e).

**Applied live (2026-10-22):** `docs/dealroom1_part1.sql` (10 tables, RLS on all;
signatures append-only by RLS + trigger; chain-head / link-check / folder-open /
config-seed functions; 36 label rows per org) via
`apps/api/scripts/_apply_dealroom1.py --schema`; `--templates` seeded
`platform_nda` v1 and `platform_joinder` v1 (DRAFT) from
`docs/templates/deal_room_templates_v1.md`; `--backfill` gave all 9 live deals a
publisher, nine folders and settings, and moved the 1 existing document
(`visible_to_members` true) into 09 Other at after_agreements —
`docs/DEALROOM1_MIGRATION.json`. Re-read via MCP: 9/9 publishers, 81 folders,
0 documents without a folder.

**Run in this session:** `node --test tests/` 306/0 (8 new in
`tests/dealroom/`), `authz1_routes.py` regenerated (32 new `/deal-room` routes,
each gated; `--check` CURRENT; no existing gate changed), the menu-visibility
harness green for all six personas. **`npm run build` was NOT confirmed**: the
machine crashed twice while it ran; the verify runs it.

**Findings:**
- [FIND] No live deal had a sponsor or a creator with a CRM person (all 9
  `sponsor_entity_id` and `created_by` NULL), so the prompt's migration rule gave
  no publisher; every deal took its org's new **house publisher** (one firm
  entity per org, named from `organizations.name`, `created_via
  dealroom1_house_publisher`).
- [FIND] There is no users→CRM column; a user's CRM person is their oldest
  active `delegate_grants` principal of type individual.
- [FIND] "Never change an existing route's gate" vs "publishers may edit":
  existing marketplace routes are untouched (`PUT /deals/{id}` still
  `manage_deals`); publisher/delegate editing is the new `/deal-room` surface.
  `GET /deals/{id}` and Document Search now apply the folder rule to members
  (strictly narrower; documents with no folder keep the old rule).
- [FIND] A `declined` status was added (the prompt listed no status for
  "decline with reason"). A revoked or declined investor cannot re-request (their
  signatures would otherwise auto-grant again).
- [FIND] `compliance_records` has no QP / QC column; those rules read
  `accreditation_basis`.
- [FIND] The menu cannot express "publisher of some deal", so the Deal access tab
  is listed for `manage_deals` (strict); others reach it from the deal page and
  My deal access. The menu-visibility harness's legacy-rule regression already
  failed at b07c839 (BUGFIX1's comingSoon items); it now excludes them, named.
- [FIND] `verify_bugfix1` asserted "exactly 4 new routes since e816797" — any
  later route broke it; `/deal-room` routes are now excluded there, named.

**Follow-ups:** counsel review of both templates before outside publishers;
Phase 2 / 3 per `docs/DEAL_ROOM.md`.

---

## 00000000000000000000000000000000000000000000000000000. BUGFIX1 — HollisMuse rounds 1–2: one SPV-totals function, one deal lifecycle, Document Search over attachments, dead menu items flagged, CRM and Portfolio agree, EDGAR peek decides before downloading, polish; verify WRITTEN, not yet run (2026-10-21)

**Run:** `doppler run -- python3 apps/api/scripts/verify_bugfix1.py 2>&1 | grep -E "^\[FAIL\]|^\[FIND\]|^\[INFO\] real|^TOTAL"`.
Written, **not run** (standing rule). SEC and R2 are mocked. Regressions
(`verify_authz1.py`, `verify_navregroup.py`, `verify_bulkfetchui2.py`) run
flat after teardown with `VERIFY_NESTED=1`. Run in this session: `node --test
tests/` 298/0 (10 new in `tests/bugfix1/`), `npm run build` exit 0,
`authz1_routes.py` regenerated (4 new routes, each gated; no existing gate
changed), the two operator scripts below as dry runs. Discovery:
`docs/BUGFIX1_DISCOVERY.md` (1a–1f).

**Real records the verify run changes (each printed):** the repairs in
`apps/api/scripts/_bugfix1_repairs.py` (dry run in this session: 9 deals'
`deal_stage` mirror — e.g. Stonegate `'active'` → `'deal_stage_4'`, Project
Atlas (Hollisworks, no config) `'sourced'` → `'draft'` — each a system-axis
restatement with an audit row, `deal_status` untouched; and the duplicate
James Hargrove SSN row 9cff06ec closed with `valid_to = now()`), and
TEST-Submit Test restored to `submitted` through `POST
/deals/{id}/unarchive`. Not applied in this session — the verify run (or
`_bugfix1_repairs.py --apply`) applies them.

- **SPV-01 / DASH-03 — the ONE SPV-totals function:**
  `services/spv_rollup.spv_commitment_summaries` (pure core
  `commitment_summary`). Committed = live subscriptions excluding
  `cancelled`; soft = `soft`, hard = every other live status; percent of
  target never capped (`over_target` flag, labels published with it). Used by
  `GET /spvs` (list card), `GET /spvs/{id}` and `/captable` (cap table), and
  the dashboard SPV card (now one line per SPV with its current state, not
  the last five status events). One renderer: `components/spv/
  CommitmentProgress.jsx` + `lib/spvCommitment.mjs`. [FIND] the ledger /
  deal roll-up (`_SUBSCRIPTION_TOTALS`) and the assistant's SPV list still
  count cancelled subscriptions — not changed (ledger semantics).
- **MKT-01 / DASH-01 / MKT-03 / R2-MKT-05 — the deal lifecycle source:**
  `services/deal_lifecycle.py`. State = `deals.deal_status` (enum); order and
  labels = the org's `deal_stages` config paired with the enum by
  `display_order` (Sourced = draft … Archived); `deals.deal_stage` is a
  mirror holding the config key, written in the same statement. Every move is
  `apply_status` (transitions, a system-axis version via `archive_version`,
  audit with `from` and `version_id`). Badge, Stage column
  (`DealResponse.stage_label`), Marketplace summary and dashboard snapshot all
  read `deal_lifecycle.summary`/`lifecycle`. Submit for Review requires
  `confirm=true` (428) and the fields in the new org setting
  `marketplace.submit_required_fields` (422 naming each). Archive requires
  `confirm=true`; `POST /deals/{id}/unarchive` (manage_deals, same as
  Archive) restores the prior stage from the version history, else the audit
  trail. `GET /deals/{id}/lifecycle` drives the pipeline controls. `PUT
  /deals/{id}` no longer edits `deal_status`/`deal_stage` (it was a backdoor;
  its history copy also failed on the UNIQUE slug — fixed via
  `archive_version`, `slug` NULL on versions). Members' investments show
  `member_name`. `DELETE /deals/{id}/vote` withdraws the caller's own vote
  (vote_deal; keyed on the caller's users.id, so owner-only by construction).
  Hollisworks has no `deal_stages` config: its labels fall back to the status
  value. The config's first label is "Sourced" (the draft state) — a config
  choice, now shown consistently.
- **R2-DOC-03 — search coverage and visibility rule:** keyword search covers
  `documents` + `deal_documents` + `entity_documents` + `spv_documents`
  (`document_linkage._search_attachments`): file name / title, category /
  type, and text where it exists (`deal_documents.extracted_data` only). Each
  result carries `source`/`source_label`/`parent_name`/`href`. Rule per row,
  in SQL: deal — the deal's own read rule (staff any status; others only
  `deal_lifecycle.MEMBER_VISIBLE_STATUSES` and only approved +
  visible_to_members documents); entity — `visible_entity_ids` (+ restricted
  access); SPV — `spv_visibility.MEMBER_VISIBLE_SPV_STATUSES` AND the
  anchor entity (subscription entity, else vehicle) in `visible_entity_ids`
  (most restrictive). Semantic search adds `not_indexed` (the caller's
  keyword matches with no embedding — every attachment) labelled "Not yet
  indexed for meaning search"; no embedding pipeline added. Documents moved
  out of /admin: `/documents` (Search) and `/documents/semantic`; the old
  `/admin/document-search` and `/admin/semantic-search` redirect. [FIND]
  entity and SPV attachments have no extracted text anywhere — searchable by
  name and category only.
- **Dead links — the feature flag:** `comingSoon: true` on a MENU_ITEMS entry
  in `apps/web/lib/menuVisibility.mjs` (`COMING_SOON` lists them). Flagged:
  Community, Insurance, Portfolio → Reporting — never listed in any menu;
  their URLs render `components/ComingSoon.jsx` via `lib/comingSoonPage.js`.
  To launch a page, build it and delete the flag. `/fee-schedules` redirects
  to `/fee-schedules/chat`. navregroup's harness, node test and URL check
  carry the BUGFIX1 removals/pages as named allowances.
- **CRM-02:** every CRM ownership read goes through
  `services/ownership_read.ownership_edges` (both `entity_ownership` and
  `entity_relationships`, one edge per pair) — Overview tree, `/full`
  owners/holdings, and the Ownership tab's lists; the tab renders the
  Overview's own tree (the `/ownership-graph` path is defined twice and
  `routers/ownership_tree.py`'s copy is unreachable). Rows from
  `entity_ownership` are read-only on the tab. [FIND] two ownership tables
  remain — consolidating them is a data-model change, not done here.
  **CRM-04:** true duplicates — repaired (above); `POST
  /entities/{id}/tax-ids` now 409s an identical current value.
- **PORT-01:** `GET /portfolio/positions` and `/securities` return
  `empty_hint` (`services/holdings_hint.py`) when the grid is empty and the
  caller has investments: "Holdings appear here once account data is
  imported from the custodian." + link to `/portfolio`. **PORT-02:** new `GET
  /portfolio/commitments` (view_portfolio — the projection's own gate;
  filtered to `visible_entity_ids` via the position owner): name, fund,
  commitment, called, remaining; rows open the existing projection; the id
  lookup stays below.
- **EDGAR — the peek decides first:** `peek` streams (`SecClient.
  open_stream`) and classifies the first 16 KB for a 206 AND a 200; a skip
  closes the stream unread (no full read, no R2 write); a wanted 200 is read
  to the end in place (no second GET). A cover the peek cannot read that
  turns out (full text) to be an unwanted kind is recorded
  (`record_kind_skip`) and not stored. **Preliminary R2 cleanup command:**
  `doppler run -p hollisworks -c prd -- apps/api/venv/bin/python
  apps/api/scripts/cleanup_preliminary_r2.py` (dry run: 910 filings, 1,820
  objects, 34,468,879 bytes on 2026-10-21) then `--apply` (deletes both
  objects, clears `r2_key`/`text_r2_key`; skips any filing referenced from a
  table other than edgar_index_filings). Institutions table:
  `components/admin/InstitutionPicker.jsx` — header row (Institution,
  Entities, years shown, Total), right-aligned numbers. Laptop worker default
  now `apps/api/venv/bin/python` (`edgar_bulk_queue.LAPTOP_COMMAND`;
  verify_bulkfetchui2's expected default updated).
- **DASH-02:** every to-do carries `href` (`services/todo_links.todo_href`);
  stub to-dos now store `related_type='entity'`; older ones link to the CRM
  search for their name. **R2-ASSIST-03:** `services/assistant_transcript.py`
  — only the final turn (or a READ handler's text) is the reply;
  tool-calling turns are stored `_display.internal` with their narration and
  never formatted; `disclosures` are no longer sent; italic / tool-phrase
  lines are stripped. **FIRM-01:** `/firm-search` title and masthead come
  from `lib/firmSearchBrand.js` — the host tenant's `brand.name` (2nd Act on
  its own host); the platform org's brand only on the marketing apex.

**Note:** this branch's working tree also holds USERMGMT1's implementation
files, uncommitted — the usermgmt1 "merge" (62364b4) contains only its verify
fixes. Commit both together or separately with care.

---

## 0000000000000000000000000000000000000000000000000000. USERMGMT1 — People & Access: one row per person, how each person signs in, password reset / resend invite / link a pending login / edit email, test accounts hidden, 19 orphan fixtures removed; verify WRITTEN, not yet run (2026-10-21)

**Run:** `doppler run -- python3 apps/api/scripts/verify_usermgmt1.py 2>&1 | grep -E "^\[FAIL\]|^\[FIND\]|^TOTAL"`.
Written, **not run** (standing rule). Auth0 is mocked entirely (JWT,
`/userinfo`, `/dbconnections/change_password`); SES is replaced by a recorder
that fails the run if called. Regressions (`verify_authz1.py`,
`verify_navregroup.py`) run flat after teardown with `VERIFY_NESTED=1`. Run in
this session: `node --test tests/admin tests/nav` 30/0, `npm run build` exit 0,
`authz1_routes.py --check` CURRENT.

**Why the list was confusing.** `GET /admin/users` JOINed `user_roles`, so a
person with N roles was N rows (and LIMIT paged over the duplicates). It now
returns one row per person with `roles: [{id, name}]`, `sign_in` (one entry
per login: tenant "2nd Act" / "Platform", method from the Auth0 sub prefix —
`auth0|` = "Email & password", `google-oauth2|` = "Google" — and how it was
linked), `never_signed_in`, `email_is_placeholder`, `is_test`,
`contact_email`, `likely_match` (pending rows) and `actions` — the controls
THIS caller may use on THIS row, decided server-side; the screen renders a
control only when its key is there (fails closed). Filters: `status`
(active / pending / deactivated / retired / rejected), `role` (EXISTS, so the
person keeps every role), `include_test`, `include_retired` (both off by
default). The response stays a bare list (unchanged shape for verify_authz1).

**The actions** (all behind `_require_manage_members` — the same gate as every
other user edit — the same org rule, and only a Super Admin acts on a Super
Admin; each audited in `audit_log`):
- `POST /admin/users/{id}/password-reset` — see the mechanism below.
- `POST /admin/users/{id}/resend-invite` — invited and never signed in only;
  rotates `invite_token` (the old link stops working), re-opens the invite with
  the org's `invite.expiry_days`, returns the new enrollment link. Delivery goes
  through the existing SES mailer and is reported honestly; SES is still
  blocked, so the screen shows the link to copy.
- `POST /admin/users/{id}/link {target_user_id}` — see "linking" below.
- `PATCH /admin/users/{id}/email` — only for a placeholder email or an account
  that never signed in; unique case-insensitively ACROSS orgs (`users.email` is
  platform-wide; the check uses the super-admin RLS carve-out transaction-locally
  and restores it). **It does not change the Auth0 login's email.**
- Approve / Decline (existing endpoints) are surfaced with an explanation of
  what pending means and a "Looks like <person>" suggestion (same email, or the
  pending row's name / contact address equal to a member's email).
- DELETE is still anonymization, now labelled "Retire (keeps history)", and it
  also sets `status = 'retired'` so the row leaves the default list.

**Password reset — the mechanism and its limits.** There is no Auth0
Management API grant on either tenant. The API calls the tenant's PUBLIC
`POST https://<tenant>/dbconnections/change_password {client_id, email,
connection}` (`services/user_admin.py`; the one outbound call is
`post_change_password`). Client ids: Doppler `AUTH0_CLIENT_ID` (2nd Act) and
`HOLLISWORKS_AUTH0_CLIENT_ID` (Platform). Connection: `AUTH0_DB_CONNECTION` /
`HOLLISWORKS_AUTH0_DB_CONNECTION`, **neither is set**, so it defaults to Auth0's
standard `Username-Password-Authentication` — set them if a tenant's database
connection has another name. Limits: Auth0 answers 200 whether or not anyone
matched, so the screen says "requested", never "sent"; it only reaches a login
whose Auth0 email equals `users.email`; a Google login is refused ("reset the
password in Google"); a placeholder email ("No real email on file") and a
never-signed-in account ("Resend their invitation instead") are refused too.
**Operator check:** confirm the API's Render service actually receives
`AUTH0_CLIENT_ID` / `HOLLISWORKS_AUTH0_CLIENT_ID` from Doppler — without them
the action answers 503 naming the missing variable.

**How to link a duplicate login.** When someone signs in a way that does not
match their account (e.g. without the invite link, or with Google instead of
email), they land in "Awaiting approval". In People & Access, pick the existing
person in that row's "Link to an existing person…" list (pre-selected when the
row says "Looks like …") and press **Link sign-in**. The login's sub moves onto
that person — into `users.auth0_sub` if it is empty (an invited person who
never used the link), otherwise as a `user_identities` row with
`linked_via = 'operator'` — the pending row is retired (kept, never deleted)
and its to-dos close. Their next request resolves to the existing account.
Refused across orgs and, for anyone but a Super Admin, onto a Super Admin.

**Pending page.** It now explains the wait, and when the account has no real
email it asks for one: `PUT /users/me/contact-email` (added to
`PENDING_ALLOWED_PATHS`; own row only; refused when a real email exists) stores
an UNVERIFIED `contact_email` shown to admins — `services.users` never reads it,
so typing someone else's address links nothing. The pending to-do already links
to `/admin/users?status=pending`; the page now honours that filter.

**Test accounts.** `users.is_test` + `contact_email` (`docs/usermgmt1_part1.sql`,
applied via MCP): a trigger flags every `@test.local` address on insert / email
change (the domain every verify script seeds with), so new fixtures are hidden
with no per-script change; `services.test_accounts.mark_test_user` flags any
other. `scripts/_usermgmt1_hygiene.py` classifies each fixture by FINDING the
script that seeds it and removes orphans with every dependent row (plan read
transitively from pg_constraint, refused if any row touches a user outside the
set, one transaction, exact per-table counts). Run live 2026-10-21
(`docs/USERMGMT1_FIXTURE_PURGE.json`): 26 fixtures → **19 orphans removed**
(rlscutover 1, udf01b 6, udf02a 4, wfmgr3 2, wfmgr4 6; with them 5 member_todos,
2 user_permission_sets, 6 user_roles, 1 workflow definition + 2 versions +
2 steps + 3 runs + 2 run steps + 1 trigger); **7 chancery seeds kept**, flagged
`is_test`; real users 9 → 9, checksum unchanged.

**Findings.**
- [FIND] `verify_rlscutover.py` seeded `auth0_sub = 'rlscutover_creator'` but
  tore down (and looked up `created_by`) by `'rlscutover_creator@test.local'`,
  so its user leaked on EVERY run. Fixed.
- [FIND] The invited jpl99172x rows kept `auth0_sub` NULL because signing in
  without the invite link never touches the invite row: the login matched no
  VERIFIED email, so provisioning created a new pending placeholder row (which
  is what carried the `last_login_at`). Link now fixes this in one click.
  jpl99172's row was linked by hand and still reads `invite_status = 'pending'`;
  the list ignores invite status once a person has signed in.
- [FIND] An org admin holding only `manage_org_settings` can approve / decline
  but not use the new actions (they need `manage_members`, like every user
  edit). Unchanged rule; the list publishes no edit action for such a caller.
- [FIND] Role assignment (`PUT …/role`) still REPLACES all roles with one; the
  screen now warns when a person holds several.
- [FIND] Old source-string checks in `verify_usermanagementfinish.py` (T7) look
  for `lastLoginLabel(u.last_login_at)` in UserManagement.jsx; that rendering
  moved to `components/admin/PeopleParts.jsx`, so that stale check will fail.

---

## 000000000000000000000000000000000000000000000000000. AUTHZ1 — one database-backed, fail-closed authorization path for every API route; no default-allow anywhere; safe provisioning (link by verified email or hold as pending — never super admin by issuer); duplicate accounts linked; all verify sections RUN green (2026-10-21)

**Run:** `doppler run -- python3 apps/api/scripts/verify_authz1.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"`.
Doppler-hydrated; `/userinfo` mocked. Every section was run against the live
database in this session, in parts (the 10-minute foreground limit): task 1 /
scan / reproduce / after / is_staff 28/0 · provisioning / real users / test mode /
isolation 73/0 · route matrix 23/0 (2,559 requests over 7 personas) · front end
incl. `npm run build` 45/0 · teardown 16/0 each time. Regressions run flat with
`VERIFY_NESTED=1`: verify_navregroup 128/0, verify_bulkfetchui2 78/0,
menu-visibility harness clean. `AUTHZ1_ONLY="a,b"` runs named sections only.

**What was open, in production, before this sprint** (docs/AUTHZ1_ROUTES.md,
439 routes, **197 writes marked [FIND]**):
- token-claim helpers (`services.permissions`) allowed whenever the claim was
  absent — real Auth0 access tokens never carry `roles`/`permissions`, so every
  spv / marketplace / entity_graph / vdr / investment_profile / portfolio gate
  was open and every caller was "staff";
- **every `rbac` gate fed by `get_user_id` was open too**: `get_user_id`
  returned `uuid5(sub)`, a phantom id with no `users` row, so `has_permission`
  saw "no roles" → default-allow (billing groups, fee schedules, custody import,
  altruist, portfolio positions/securities/transactions, udf, profitability,
  modeling TA, ledger). Gates fed by `ensure_user` default-allowed any role-less
  user (People & Access, invites, households, staff assignments);
- ~60 writes had no gate at all (CRM, entity documents, document intake/linking/
  review, ledger, investment profile, SPV subscriptions);
- the middleware made any Hollisworks-issued token super admin, and
  `ensure_user` created every unknown login as a member (or super admin).

**What is true now**
- `services/authz.py`: ONE `Principal` per request (user, org, status,
  super/staff flags, roles, permission set) loaded from the DB by
  `main.rls_context_middleware`, cached on `request.state.principal`. Helpers:
  `require_authenticated` / `require_active` / `require_permission` /
  `require_any_permission` / `require_staff` / `require_super_admin` /
  `require_entity_access`. Super Admin first, then exact; no default-allow.
  Staff = Super Admin or a role in `STAFF_ROLE_NAMES` (investment_staff, admin,
  super_admin, owner) — DB role membership, never a claim.
- `services.permissions` is a shim over it; `get_user_id` returns the real
  `users.id` (401 when none). `services.rbac.has_permission` lost its no-roles
  branch; `load_principal` blanks the role of an unadmitted row.
- Middleware: no resolvable user → 401; deactivated → 403; `status` pending /
  retired / rejected → 403 everywhere except `GET/PATCH /users/me` (PATCH still
  refuses). `/enroll/accept` is provisioning-exempt (the invite token claims the
  pre-created row).
- Gates added (stated per route in AUTHZ1_ROUTES.md): CRM writes →
  manage_members (compliance read/write → view_/manage_compliance); entity
  documents, intake, linking, proposal decisions → manage_documents; document
  review → per-user `document_visible` (+ manage_documents for corrections and
  confirm); GL writes → **new `manage_ledger`** permission; investment profile →
  entity access; SPV subscriptions → entity access + indicate_interest.
- Schema (`docs/authz1_part1.sql`, applied via MCP, confirmed live):
  `users.status` (active / pending / retired / rejected, CHECK) and
  `user_identities` (user_id, org_id, auth0_sub UNIQUE, issuer, email,
  linked_via; RLS with a policy for all four operations).
- Provisioning (`services/users.py`): linked sub → primary sub (a placeholder
  row with a verified email matching exactly one admitted user in the same org
  is MERGED: sub linked, placeholder retired) → test-mode UUID stub → unknown
  login: verified-email match links, Hollisworks issuer + verified email in
  `PLATFORM_STAFF_EMAILS` → super admin, anything else → **pending** with an
  alert (member_todos) to the org's manage_members / manage_org_settings
  holders. `touch_last_login` on every path. People & Access lists pending
  sign-ins with Approve (grants one role, admits) / Decline
  (`POST /admin/users/{id}/approve|reject`).
- Task 3 seed (`scripts/_apply_authz1.py`, run live, additive only): baseline
  member / investment_staff / admin / super_admin roles in both real orgs
  (admin gained the six permissions "staff implies all" used to grant, plus
  manage_ledger); every real user granted the role matching `users.role`.
  Report: `docs/AUTHZ1_REAL_USERS.json` — the only loss is the Google
  placeholder row's default-allow (34 → 10, member).
- Front end: `/users/me` returns the Principal's permissions, `status`,
  `is_staff`, `is_super_admin`; `menuVisibility.canPerm` fails closed (Super
  Admin first); `AccessGate` renders the "Access pending" page for an
  unadmitted account.
- `AUTHZ_TEST_MODE=1` (set in-process by all 190 verify scripts before `main`
  is imported) is the only switch for the UUID-sub stub, claim-based test
  grants (UNIONED onto DB grants, never a default) and the DEV_USER_ID
  fallback. The API prints a warning at startup if it is set.

**Operator steps (not done by the sprint)**
1. Doppler `PLATFORM_STAFF_EMAILS` already exists in prd (2 entries); confirm
   it is `jlarizza@gmail.com,jlarizza@culmina.io` and synced to the Render API.
2. Auth0, BOTH tenants (dev-smmrfubsfscif3t1 = 2nd Act, dev-gy85vzuf6mruzv3j =
   Hollisworks): disable public sign-ups on the database connection, and disable
   every social connection that is not in use.
3. Never set `AUTHZ_TEST_MODE` on Render.
4. Joe's Google login merges into jlarizza@gmail.com by itself on its next
   sign-in (needs its verified email from `/userinfo`; neither tenant's client
   has a Management API grant — both returned 403). Or run
   `python3 apps/api/scripts/_apply_authz1.py --operator-link-google` after
   confirming the email in the Auth0 dashboard.

**[FIND]s left open**
- CRM, ledger and document-link READS are visible to every admitted user in the
  org (writes are gated); a product decision.
- `users.email` is UNIQUE platform-wide, so the same person cannot hold rows in
  two orgs under one address; a second-org login keeps a placeholder email.
- Joe's Hollisworks row still has its placeholder email, likely for that reason
  (the 2nd Act row already holds the address). Before AUTHZ1 its back-fill
  UPDATE would have raised inside `ensure_user`'s try and fallen back to the
  uuid5 phantom id.
- An invitee who signs in BEFORE redeeming the invite gets a pending row, and
  `/enroll/accept` then reports sub_conflict. Not fixed.
- The /admin/users menu tab is still gated on manage_members; an org admin
  (manage_org_settings only) reaches pending approvals through the alert link.
- `resolve_tab_visibility` defaults a tab with no grant row to visible (a
  display default, not a capability grant).
- Rows written by real callers before AUTHZ1 carry the uuid5 phantom id in
  non-FK actor columns (created_by and similar).
- verify_superadminmenu and any older verify script that relied on
  default-allow for a role-less fixture will now see 403s. Only navregroup,
  bulkfetchui2 and the harness were updated and re-run. verify_navregroup's
  router-scope check is now pinned to its own commit (e97718f).

---

## 0000000000000000000000000000000000000000000000000. NAVREGROUP — navigation regrouped into master pages with tabs, rendered from ONE gated menu definition; Document Search filtered by the caller's visibility; VDR proposals list gated on manage_deals; verify WRITTEN, not yet run (2026-10-20)

**Run:** `python3 apps/api/scripts/verify_navregroup.py`. Doppler-hydrated. Its file-based sections were
dry-run offline during the sprint (no DB, no build): all PASS. The DB sections, the build and the
regressions have NOT been run. After its own teardown it runs, once each as flat subprocesses
(VERIFY_NESTED=1): `verify_bulkfetchui2.py` and the menu-visibility harness's existing assertions. Task 1's
findings are in **docs/NAVREGROUP_DISCOVERY.md**, covering every admin/portfolio route, its API, its real
server gate and every [FIND].

**The single-definition rule.** `apps/web/lib/menuVisibility.mjs` is the only menu definition:
`SECTIONS`, `GROUPS` (master pages: id, label, section, order, landing, description) and `MENU_ITEMS`
(section, group, tab_order, label, href, gate, adminIndex). Pure helpers: `visibleGroups`, `visibleTabs`,
`landingFor`, `visibleSections`, `tabForPath`, `landingDecision`, `visibleAdminGroups`, `labelFor`. **The
Sidebar must never hard-code items again.** `components/Sidebar.jsx` used to keep its own item list and
gate checks, and they had drifted: Positions, Securities and Commitments were in the sidebar only, and
Model Catalog was in `MENU_ITEMS` only. It now renders `visibleSections(me)` and keeps only the icon
registry. `components/TabbedSection.jsx` (rendered by `AppShell`, so no tab page was edited) draws a
group's tab bar on each of its tab routes. `components/GroupLanding.jsx` backs each group's own route.
After the session check it redirects server-side to the first visible tab. With no visible tab it renders
the refusal panel, never a redirect. The `/admin` landing is now one card per visible group (its
description and visible tabs). Page headings that repeated a renamed label read it via `labelFor`.

**The structure.**

- **Main:** Dashboard · CRM · Marketplace · **Portfolio** (Overview, Positions, Transactions, Securities,
  Commitments, Allocation, Ownership tree, Reporting) · Market Indicators · SPV Manager · Insurance ·
  Community · **Documents** (Search, Semantic search; landing `/documents`) · Notifications.
- **Admin — Organization:** Admin · **People & Access** (Users, Teams & Coverage, Profiles, Permission
  Sets; `/admin/people`) · **Settings** (Organization, TA Model Defaults; `/admin/configuration`) ·
  **Workflows** (Workflows, Runs, Triggers; `/admin/automation`) · **Billing & Custody** (Billing Groups,
  Custody Import; `/admin/billing`) · **Deal Intake** (VDR Proposals; `/admin/deal-intake`).
- **Admin — Platform:** **Tenants** (`/admin/platform`) · **Controls** (Restricted Access, Trading
  Authority; `/admin/controls`) · **AI Models** (Model Catalog, Model Research; `/admin/ai-models`) ·
  **Structured Notes** (Download = `/admin/edgar-pipeline` with its own inner tabs, Review queue, Gold
  answers, Accuracy results, Volatility surface; `/admin/structured-notes`).
- Every existing URL is unchanged. The new routes are only the nine landings above.
- Renames: Note Gold Set → Gold answers, Extraction Results → Accuracy results, Note Terms Review → Review
  queue, Staff Visibility → Teams & Coverage, Platform → Tenants, Investments → Portfolio (the group).

**Baseline / after snapshot.** `docs/NAVREGROUP_BASELINE.json` is generated by
`apps/api/scripts/navregroup_baseline.mjs` from the PRE-sprint Sidebar.jsx (parsed) and menuVisibility.mjs
(imported), pinned to `e581844`. It uses six personas holding the live role grants
(`navregroup_personas.mjs`). Reachable counts before → after:

| Persona | Before | After |
|---|---|---|
| plain member | 13 | 19 |
| staff | 13 | 20 |
| manage_members only | 16 | 19 |
| manage_org_settings | 19 | 22 |
| super admin | 31 | 41 |
| no roles | 21 | 28 |

No href is removed for any persona. Every addition is listed explicitly in
`menuvisibility_harness.mjs` (INTENDED_ADDITIONS) and checked exactly. The harness also cross-checks every
visible href against the page's real server gate (`navregroup_server_gates.mjs`): there are no new
refusals, and no added href is refused.

**Consequences worth knowing.**

- Billing Groups / Custody Import are listed on their real server read gate, `view_portfolio`. The live
  `member` role holds it, so **a plain member now sees "Admin — Organization → Billing & Custody"**. The
  server already served them; tightening the read to `manage_billing` is a separate decision.
- Model Research keeps its strict `manage_org_settings` gate, so an org admin sees "Admin — Platform → AI
  Models" with that one tab.

**Server fixes (Task 3).**

- **Document Search:** `GET /document-search` used to list every matching document in the org to any
  caller. `document_linkage.search_documents` now takes the caller (`pool`, `user_id`, `is_staff`, all
  required). It applies the SAME rule as semantic search: `document_embedding.visible_entity_ids` (made
  public; the old private name is kept as an alias) and the per-document rule `document_visible`. The
  pre-fix query survives as `search_documents_org_wide` for internal matching checks only.
  `verify_chancery9.py`'s call sites were renamed to it; that script was not re-run.
- **VDR proposals list:** `GET /vdr-proposals` had no check. It now requires `manage_deals` via the
  DB-backed `rbac.require_permission`, the same rule `/users/me` and the menu use.

**[FIND]s.**

- **Token-claim gates are inert for real callers.** Real API-audience tokens carry only
  sub/iss/aud/azp/scope (`org_id_from_claims`). So `services.permissions.is_staff` is True for every real
  caller, and `require_permission` allows every real caller.
  - VDR approve/reject and every `manage_deals` write in marketplace/spv are open to any signed-in user.
    Not changed here.
  - Both searches put every real caller on the staff path. A member with no staff assignments sees
    org-general documents but not their own entities' documents. That is narrower than the old org-wide
    listing, but the member path only works once the staff decision reads user_roles. Recommended next
    sprint: one DB-backed staff resolver for both searches.
- `filter_restricted` has no Super Admin bypass, so restricted entities' documents are hidden from
  non-allow-listed staff and Super Admin in both searches.
- Pages with no effective server gate:
  - `/portfolio/allocation`: allocation-lens accepts any org entity, and the page has no gate. Listed for
    Super Admin only.
  - `/admin/document-review/[documentId]` and its corrections/confirm/download endpoints: org filter
    only. Not on the menu (a detail page) and not fixed.
- Workflows Runs/Triggers gate on profile-axis keys that `/users/me` does not publish, so they are listed
  for Super Admin only until it does.
- Pre-existing mismatches, carried over unchanged (the rule is that no href disappears):
  - `/portfolio-reporting`, `/insurance` and `/community` have no page (404 for everyone).
  - Positions/Securities are shown ungated, but their API needs view_portfolio, which the
    `member_manager` and `org_admin` roles lack.
  - `/admin/workflows` is shown on manage_org_settings, but its API needs profile-axis author_workflows.
- Seen in discovery, not fixed: `/deals` and `/spvs` skip the member status filter when `?status=` is
  given, and `GET /entities` is an org-wide list.

---

## 000000000000000000000000000000000000000000000000. BULKFETCH UI 2 — "Run on your laptop" dialog: queuing a Laptop run opens the exact steps to start (or check) the laptop worker, with Copy buttons and a live worker-status check; the start command becomes the setting `EDGAR_BULK_LAPTOP_WORKER_COMMAND`; verify WRITTEN, not yet run (2026-10-20)

**Run:** `python3 apps/api/scripts/verify_bulkfetchui2.py`. Doppler-hydrated; SEC, R2, Render and every model
call mocked; real spend $0. **Stop any real laptop worker first** (`tmux attach -t bulkworker`, Ctrl-C):
the script refuses its worker sections while one heartbeats. After its own teardown it runs
`verify_bulkfetchui.py` once as a flat subprocess (1200 s timeout; that script runs its own three
regressions). Task 1's findings: **docs/BULKFETCHUI2_DISCOVERY.md**.

**The setting.** `EDGAR_BULK_LAPTOP_WORKER_COMMAND` is read per request (`os.environ`, i.e. Doppler), like
`EDGAR_BULK_RENDER_WORKER_SERVICE_ID`. **Not set in Doppler today**; unset (or blank) means the default,
which is the command that actually works on the laptop:

    cd /mnt/c/Users/Joe/2ndActCapital && doppler run -p hollisworks -c prd -- apps/api/venv/bin/python -u apps/api/scripts/run_bulk_fetch.py --worker --target laptop

(BUGFIX1, 2026-10-21: corrected from the repo-root `venv/bin/python`, which did not work; the API's own
`apps/api/venv` is the working interpreter.)

The old default `doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --worker --target laptop` did not
work there: wrong folder, no explicit Doppler project/config, and not the repo venv. One source,
`services/edgar_bulk_queue.py`: `laptop_start_command()`, `laptop_tmux_command()` (shell-quoted) and
`laptop_instructions()`.

**API.** `GET /admin/edgar/bulk/workers` adds `laptop_start_command` and `laptop_instructions`: an ordered
list of `{n, key, section, title, detail, command?, keys?}`, where `section` is `start` or `ongoing`. The
steps: open the Ubuntu (WSL) terminal · `tmux new -s bulkworker` · the start command · detach with
Ctrl-B then D · keep the laptop awake (Windows: Settings → System → Power → Sleep "Never" when plugged in)
· check later with `tmux attach -t bulkworker` · stop with Ctrl-C inside tmux, and stop it before any
verify script · a stopped or crashed run can be requeued and continues where it stopped. `POST
/admin/edgar/bulk/runs` (and refresh-index) add `laptop_start_command`, and when a laptop worker is online,
`worker` and `worker_host`. Render behaviour is unchanged (409, nothing queued). The permission gate is
unchanged (super-admin only).

**The dialog** (`components/admin/LaptopWorkerDialog.jsx`, logic in `lib/edgarBulkDownload.mjs`, node
tests `tests/edgar/laptopDialog.test.mjs` rendering the real component). It opens after a confirmed
Laptop queue, titled "Run on your laptop". At the top is the worker status, re-checked every 5 s while it
is open: green "Laptop worker running on <host> — your run will start automatically", or amber "No laptop
worker running — your run is queued and will start when you start one". Below are the numbered steps from
`laptop_instructions`: every command sits in a monospace box with a Copy button, and keystrokes show as
keys. While a worker is running, the start steps collapse under "How to start the worker". Buttons: "I've
started it — check again", "View run" (closes and highlights the run's row in the runs table), "Close".
"Show these steps" beside the Laptop radio opens the same dialog without queuing. "Don't show this again
when the worker is running" is stored per browser (localStorage key
`hollisworks.edgarBulk.hideLaptopDialogWhenOnline`, try/catch). The dialog always shows when no worker is
running. Render: no dialog. Focus moves in, Tab stays inside, Esc closes, and focus returns to the Queue
button (or to "Show these steps"). The component holds no copy of the command.

## 00000000000000000000000000000000000000000000000. BULKFETCH UI — "Bulk download" tab on the EDGAR Pipeline screen: pick institutions / dates / kinds, live preview, QUEUE a run for a laptop (default) or Render worker, watch / stop / requeue; `run_bulk_fetch.py --worker`; verify WRITTEN, not yet run (2026-10-18)

**Run:** `python3 apps/api/scripts/verify_bulkfetchui.py`. Doppler-hydrated; SEC, R2, Render and every model
call mocked (no network); real spend $0. Runs `verify_bulkfetch.py`, `verify_edgarpipelinea.py` and
`verify_edgarcohorts.py` once each, as flat subprocesses, AFTER its own teardown (600 s timeout each).
Task 1's five findings: **docs/BULKFETCHUI_DISCOVERY.md**.

**The screen.** EDGAR Pipeline → **Bulk download** (`components/admin/EdgarBulkDownload.jsx`, logic in
`lib/edgarBulkDownload.mjs`, node tests `tests/edgar/`). Institutions (search, expand to legal entities and
CIKs with selected counts by year, Select all / none); time frame (From / To, presets 2025–2026 · Last 12
months · Year to date · Custom; the last indexed date and **Refresh index**, which queues a refresh run);
document kinds (Final pricing supplements on by default, Product supplements, Preliminaries, Other; "Skip
preliminaries by peeking" on); **Run on** Laptop (default) / Render with a status dot from GET workers;
Advanced (concurrency, max requests/second capped at 8); a debounced preview (totals, institution × year,
entities included, ETA, storage and monthly R2 cost); **Queue run** with a confirm dialog; the runs table
(progress bar done/total, rate, ETA, failures, Stop / Cancel / Requeue, auto-refresh every 10 s while a run
is active). Super Admin only (the same `_require_super_admin` gate as every EDGAR screen).

**The API never downloads.** It QUEUES a row in `portfolio.edgar_pipeline_runs` (run_kind `bulk_fetch` or
`refresh_index`, target `laptop` | `render`, params, queued_at). A worker executes it with the SAME
`edgar_bulk.Runner` as a CLI run (`RunOptions.run_id` adopts the queued row).

**Start the laptop worker (tmux):**

    tmux new -s bulkworker 'doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --worker --target laptop'

*Superseded by BULKFETCH UI 2 (above): that command does not work on Joe's laptop. The working one is the
default of `EDGAR_BULK_LAPTOP_WORKER_COMMAND`, and the "Run on your laptop" dialog shows the full steps.*

Detach with Ctrl-b d; reattach with `tmux attach -t bulkworker`. The worker heartbeats every 30 s,
polls for queued runs every 15 s (`--poll-seconds`), writes progress every 100 filings, and serves ONE run
at a time. `--once` claims at most one run and exits. Ctrl-C finishes the in-flight filings, marks the run
`stopped`, then exits. The laptop must stay awake: a sleeping laptop stops heartbeating, the screen shows
"worker lost" after 2 minutes, and Requeue resumes the run.

**Queued-run lifecycle.**

    queued ──worker claims (FOR UPDATE SKIP LOCKED)──▶ running ──▶ succeeded | failed | stopped
    queued ──Cancel──▶ cancelled
    running ──Stop (stop_requested)──▶ in-flight filings finish, unstarted claims released ──▶ stopped
    running, heartbeat older than 2 min ──▶ shown as "worker lost" (Stop closes it at once)
    stopped | failed | worker lost ──Requeue──▶ queued (same row; resumes: only rows still 'selected' are taken)

- **One run at a time across ALL workers.** The claim is refused while any queued-kind run is running with
  a fresh heartbeat (check + claim under one advisory lock), so the SEC budget stays at 8/s. The
  Runner's lease division is computed once at start and would let two concurrent runs reach 12/s (FIND,
  discovery doc).
- **Fencing.** Every heartbeat updates the run `WHERE worker_id = me`. A worker whose run was requeued
  stops, and leaves the row, its claims and its lease to the new owner.
- **Laptop with no worker:** the run is still queued. The response carries `worker_online: false` and
  the exact start command (shown with a Copy button).
- **Render not set up:** HTTP 409 "Render worker is not set up — run on Laptop, or see setup steps",
  and NOTHING is queued.

**Render worker setup (later; nothing created on Render this sprint).** `render.yaml` carries the block,
commented out.
1. Render → New → **Background Worker** from this repo, root directory `apps/api`, build
   `pip install -r requirements.txt`, **start command
   `python3 scripts/run_bulk_fetch.py --worker --target render`**, plan Starter (**~$7/month**; a worker
   runs continuously, unlike the free web plan that spins down).
2. Doppler → project `hollisworks`, config `prd` → **its own sync connection** to the new service. A
   Render service gets nothing from another service's sync, and a sync can overwrite manual values. It
   needs DATABASE_URL, EDGAR_USER_AGENT and R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
   R2_BUCKET_NAME.
3. Doppler: set **`EDGAR_BULK_RENDER_WORKER_SERVICE_ID`** to the new service's id (the API reads it, and
   makes one read-only `GET /v1/services/{id}` with RENDER_API_KEY to confirm a non-suspended
   background worker).
4. Deploy. The tab's Render dot shows "online" after the first heartbeat (alive = a heartbeat within
   5 minutes).

**Measured preview time (live, EXPLAIN ANALYZE, 2026-10-18):** JPMorgan 2025 scope query **82.7 ms**.
Institutions catalog: **10,114 ms** (seq scan) → **299 ms** after the new partial index
`edgar_index_filings_selected_count_idx` + VACUUM, and cached 60 s. The verify prints the end-to-end
API time for one real group/year (must be < 2 s).

**Schema (`apps/api/migrations/bulkfetchui_queued_runs.sql`, applied live):** run_kind + `bulk_fetch`,
`refresh_index`; status + `queued`, `stopped`, `cancelled` (every old value kept); columns target, params,
queued_at, worker_id, worker_host, heartbeat_at, stop_requested, progress; partial index on queued runs by
target; `portfolio.edgar_fetch_workers` (global read, super-admin write); the selected-count index.

**API (`routers/edgar_pipeline_admin.py`, service `services/edgar_bulk_queue.py`):** GET
`/admin/edgar/bulk/institutions` · POST `/bulk/preview` (read-only; a To after the last indexed date → 409
"Refresh the index first" with the date) · POST `/bulk/runs` · POST `/bulk/runs/refresh-index` · GET
`/bulk/runs`, `/bulk/runs/{id}` (derived "worker lost") · POST `/bulk/runs/{id}/stop` | `/cancel` (queued
only) | `/requeue` · GET `/bulk/workers`. No AI model anywhere.

## 0000000000000000000000000000000000000000000000. BULKFETCH — bulk EDGAR downloader (CLI, peek, shared rate limit, resumable), sources as configuration, index refresh, product supplements once per program, WF/DB prefilter v2, stuck 'fetched' rows; review screen: list tables, labels, CUSIP/ISIN, calculated totals, normalised names; verify WRITTEN, not yet run (2026-10-16)

**Run:** `python3 apps/api/scripts/verify_bulkfetch.py`. Doppler-hydrated; SEC, R2 and every model call
mocked (no network); real spend $0. Runs `verify_edgarpipelinea.py`, `verify_edgarcohorts.py` and
`verify_notefix2.py` once each, as flat subprocesses, AFTER its own teardown (600 s timeout each).
Task 1's nine findings: **docs/BULKFETCH_DISCOVERY.md**.

**Migrations applied live via MCP (2026-10-16):**
- `bulkfetch_sources_claims_supplements.sql` (additive): `portfolio.edgar_sources`;
  `edgar_index_filings.claimed_by` / `claimed_until`; `portfolio.reference_filing_supplements`. Both
  new tables get the global 4-policy RLS pattern.
- `bulkfetch_widen_reference_form_type.sql`: `reference_filings.form_type` now also allows
  424B3/424B5, because product supplements are often filed under those forms.
- `bulkfetch_review_fields.sql` (registry DML): the agent_commission_pct label, plus the two
  calculated rows.

### Part A — bulk download

**Sources are configuration.** `portfolio.edgar_sources` has a name, form types, a filer source
(`structured_note_issuers` filtered by include_status, or an `explicit` CIK list) and a NAMED
classifier from `services.edgar_bulk.CLASSIFIERS`. The seeded row is `structured_notes`: 424B2,
issuer table yes/review, `structured_note_cover` (= `edgar_pipeline.classify_document`). The runner
takes `--source` (default structured_notes); `--refresh-index` loads the union of the active sources'
form types.

**Adding a `defined_outcome_etfs` source later (NOT done this sprint):**
1. Insert an `edgar_sources` row: forms `497`, `497K`, `N-1A` (485BPOS too, if prospectus updates
   matter); `filer_source 'explicit'` with the trust CIKs of Innovator ETFs Trust, First Trust
   Exchange-Traded Fund VIII (FT Vest) and AllianzIM's Advisors Series Trust; a new classifier name,
   e.g. `defined_outcome_prospectus`.
2. Add that classifier to `CLASSIFIERS`. Rules only: outcome period, cap, buffer, series name from
   the cover.
3. Widen `reference_filings.form_type` again for those forms (it is a CHECK, now 424B2/FWP/424B3/424B5).
4. Add a selection policy rule for the ETF forms. Today's policy decides only by `form_types` +
   issuer include_status, and `select_stage` is keyed to the structured-note issuer table, so the ETF
   source needs either its own policy key or a source-scoped selection.
5. Run `--refresh-index` over the wanted history, then `--source defined_outcome_etfs`. The ETF
   trusts are not issuer groups, so `--banks` takes the source's own name for an explicit source.

**Index refresh.** `run_bulk_fetch.py --refresh-index [--to YYYY-MM-DD] [--dry-run]`:
- reads EDGAR's DAILY index from the last loaded filing date (inclusive; a re-read is a no-op)
  through `--to` (default yesterday, UTC), with the existing loader;
- then applies the ACTIVE selection policy to exactly the accessions it inserted.

The dry run prints the date span and the number of index files. Today that is 2026-10-01 …
2026-10-15, 11 files.

**The runner.** `services/edgar_bulk.py` + `apps/api/scripts/run_bulk_fetch.py`.

Flags:
- `--banks` takes issuer-group names (case-insensitive). An unknown name errors and lists the valid
  ones. The dry run prints every filer entity, its CIK and its count per institution, then per year.
- `--from` / `--to` are inclusive filing dates. A `--to` past the last indexed date stops and says to
  run `--refresh-index`.
- `--kinds`: final / preliminary / product_supplement / underlying_supplement / term_sheet /
  prospectus_supplement / other. The classifier has no prospectus-supplement kind, so that option
  maps to `other`; see the discovery doc, 1c.
- Also: `--concurrency` (6), `--max-rps` (8; above 8 needs `--allow-above-8`, never above 10),
  `--limit`, `--order newest|oldest`, `--stop-after-failures` (50), `--no-peek`,
  `--keep-preliminaries`, `--retry-failed`, `--refetch-peek-skipped`, `--dry-run`, `--peek-test N`.

Scope and order: selected rows of the source's forms for those groups and dates, institution by
institution in the order given, newest first.

Claims: `FOR UPDATE SKIP LOCKED` inside one UPDATE, keyset-paged. A crashed run's claims lapse after
30 min, and the nightly `_queue` now skips claimed rows. Ctrl-C finishes in-flight filings, releases
every unstarted claim and closes the run row.

Retries and failures:
- 429 / 5xx / connection errors back off exponentially, honouring Retry-After (capped at 60 s).
- A request failing 3 times makes the filing `fetch_failed` with the reason. It is not
  nightly-retried; `--retry-failed` brings it back.
- N consecutive failures stop the run and say why.

Output: a progress line every 100 filings (done / remaining, rate, ETA, kinds, peek-skipped). The
final summary covers each institution × document_kind, R2 bytes uploaded, bytes saved by peeking,
and **seconds per filing by step (sec / extract / upload / db)**. That last figure is the first real
per-step measurement; Task 1a's breakdown is only an inference.

Each run is a row in `edgar_pipeline_runs`: trigger_source `cli`, scope in `details.bulk`, result in
`details.bulk_result`. Several runner processes share the 8/s budget. Each holds a lease row
`bulk_fetch:<run id>`, and at start a runner divides `--max-rps` by the number of live runners (the
nightly job's lease counts as one).

**The peek.**
- A Range GET of the first 16 KB with `Accept-Encoding: identity`, then the existing classifier.
- A recognised kind outside `--kinds` is recorded as document_kind + `not_pricing_supplement` with
  status_reason `peek: …`. There is no full GET and no R2 write.
- `other` (unrecognised) and a 200 (Range ignored) both fall through to the full fetch; on a 200 the
  body already received is used, with no second GET.
- Peek requests go through the same limiter as every other request.
- **The peek-test result is to be filled in by the operator** (`--peek-test 20`): Range honoured
  ______ / 20 (206 vs 200); cover recognised from 16 KB ______ / 20.

**Measured per-filing cost (Task 1a):**
- Today: 3 SEC requests and 1.91 s per filing, serial. The gold cohort run (308 filings, 924
  requests, 588 s) took 2.7 days for 125k.
- Bulk mode with the peek: 3.33 requests per filing averaged (4 kept, 2 peek-skipped), 416k requests
  at 8/s = **≈14.4 h** for ~124,800 filings; R2 ≈8.3 GB new, so $0/month under the 10 GB free tier.
- Without the peek: ≈13.0 h and ≈12.5 GB (≈$0.04/month).
- **[FIND]** The peek saves storage and upload, not time: at the rate cap it costs one extra request
  per kept filing.

**Product supplements once per program.** With `--kinds …,product_supplement`, every final's
supplement is resolved:
1. by its sec.gov link, where the URL's own label line says "product supplement";
2. otherwise by name + date against manifest rows already classified product_supplement within
   ±3 days.

Each distinct supplement is fetched ONCE: a per-accession lock, and never if a `reference_filings`
row already holds it. Every note gets a row in `reference_filing_supplements` (link / name_date /
unresolved). The run summary reports distinct found / fetched / already stored / unresolved.

Measured on the 171 stored 2025Q1 424B2 texts: 76 cite a supplement by link and 41 by name + date
only. The 76 links point to just 20 distinct supplements.

**Selection v2 (WF / DB).**
- **[FIND] The prompt's premise was wrong.** Selection v1 has no keyword filter, and zero WF/DB 424B2s
  are `not_selected`. Their real pricing supplements are dropped AFTER fetch by the keyword
  prefilter: 19 sit at `prefilter_skipped` with zero hits.
- v2 = v1's rules unchanged, plus
  `prefilter: {version: 2, issuer_groups: ["Wells Fargo", "Deutsche Bank"]}`. The extra STRONG terms
  (`edgar_fetch.PREFILTER_V2_STRONG`: market linked, threshold / starting / ending value, maximum
  return, upside participation, trigger level / value, knock-in, digital return, linked to the
  performance of) apply to those two groups ONLY, so no other bank's outcome can change.
- `run_bulk_fetch.py --selection-v2 --dry-run` prints, per year, what moves:
  `not_selected → selected` (expected 0) and `prefilter_skipped → ready_for_extraction`. The second
  needs an R2 read of the stored text; operator to fill in: ______.
- Without `--dry-run` it activates policy v2 (v1 retired, its rules never edited) and moves exactly
  those rows, fenced by CIK and accession.
- **[FIND] WF's 2021–23 lows (11 / 106 / 156) are in the SEC index itself.** Nothing was excluded.
  Check on EDGAR which form WF used for its 2021 notes.

**Stuck filings.** `--reprocess-fetched [--dry-run]` classifies the 200 'fetched' rows (the 2025Q1
corpus) from their database text: zero SEC requests and zero R2 reads.
- 169 are 424B2 from listed issuers and get `decide_status`'s answer.
- 31 are FWP or have no listed issuer, so they become `not_selected` with document_kind recorded.
- All are stamped with the active policy version (the decided-has-policy CHECK requires it).

**Operator commands (tmux):**
```
tmux new -s bulkfetch
cd ~/2ndActCapital && doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --refresh-index --dry-run
doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --refresh-index
doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --reprocess-fetched
doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --selection-v2 --dry-run      # then without --dry-run
B="JPMorgan,UBS,Goldman Sachs,Morgan Stanley,Wells Fargo,Bank of America"
doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --banks "$B" --from 2025-01-01 --to 2026-10-01 --dry-run
doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --banks "$B" --from 2025-01-01 --to 2026-10-01 --peek-test 20
doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --banks "$B" --from 2025-01-01 --to 2026-10-01 \
    --kinds final,product_supplement --concurrency 6 --max-rps 8
# detach: Ctrl-b d · reattach: tmux attach -t bulkfetch · stop cleanly: Ctrl-C (re-run resumes)
```

### Part B — gold review screen

- **List fields as tables (Task 8).** Underlyings, distribution and observation schedule ALWAYS show
  as tables: one row per member, one column per member field from the server's member model.
  Pre-fill rows come first, then the gold rows once reviewed. Each row shows its quote when the
  member's name is in the field's quote.
  - The editor was already a table, but hidden behind E; it gains a quote column. Tab moves across
    cells, and C / E / A act on the whole list. Saving writes the whole list as one gold value,
    validated by `schema.validate_list`, so an invalid `kind` is a 422.
- **Label (Task 9).** agent_commission_pct now displays "Underwriting discount / agent commission"
  (registry + NOTE_FIELDS.md); the key is unchanged.
- **CUSIP / ISIN (Task 9).** `services/note_extraction/identifiers.py` reuses the existing
  check-digit code.
  - The review API refuses a CUSIP that is not 9 characters or fails the check digit (422, naming
    the right digit), and an ISIN that is not 12 characters or fails its Luhn digit.
  - A US/CA ISIN without the note's CUSIP (gold value, else the newest reading) is SAVED, with a
    warning and `metadata.needs_review_reasons` on the gold row.
  - The screen runs the same check as you type (`checkIdentifier`).
- **Calculated totals (Task 9).**
  - `total_fees_usd` and `proceeds_to_issuer_usd` are new registry rows with extraction_method
    `derived`, computed as issue_size × pct / 100, each range end separately
    (`derive.dollar_totals`). The cascade and pre-fill produce them too.
  - New self-check `dollar_totals_match`: `rules.find_stated_dollar_totals` reads the cover table's
    Total row (or the "Underwriting discount $32.50 $27,950" layout) and compares the result (±$1 or
    ±0.01%). A mismatch is needs_review naming both numbers.
  - The review screen shows the totals in the economics section as calculated, with no controls. The
    API refuses a gold write to them, and they never count toward a note's fields-to-review total.
- **Names normalised (Task 10).** `services/note_extraction/normalise_names.py`.
  - Beside issuer_entity / guarantor_entity, the screen shows the matched `structured_note_issuers`
    row (filer name or credit entity, with EDGAR's "/DE/" dropped). Beside calculation_agent and each
    distribution row it shows the matched `distribution_participants` row. Each shows whether the
    match is an affiliate of the note's issuer group, or "no match".
  - The reviewer's value is what is saved. An issuer or guarantor that does not match the issuer
    table's credit entity for the note's group is a needs_review reason on the gold row. There is no
    CRM linkage.

---

## 000000000000000000000000000000000000000000000. NOTEFIX2 — seven systematic errors from checking all 50 gold-1 notes against their own filings; second-reader findings in the review screen; verify WRITTEN, not yet run (2026-10-14)

**Run:** `python3 apps/api/scripts/verify_notefix2.py`. Doppler-hydrated, every model call mocked, real
spend $0. Runs `verify_notefields.py`, `verify_goldset.py` and `verify_notefix1.py` once each, as flat
subprocesses, AFTER its own teardown.

**Trigger.** The gold-1 pre-fill (run `683e1565-db22-400f-bdd6-cb5e3f04bfe9`, gpt-5-mini, 50 notes) was
checked field by field against each SEC filing by hand (Joe, with a second-reader pass, agreed
2026-10-14). ~1,440 fields matched; 237 were flagged (54 critical) — recorded in
`docs/gold/gold1_second_reader_v1.json`. The errors are mostly missing definitions, missing rules and
missing checks, not model capacity — this sprint fixes those so cheap models can pass. It does NOT write
gold values, does not run any model for real, and does not touch `securities_global_note_terms`.

**Task 1 (discovery) — confirmed live:**
- **1a.** Junk list members come from TWO places, neither hygiene-checked before this sprint: the MODEL
  reader itself (gold-1 note `baf65e2d…`, BofA: `model_1`/gpt-5-mini merged two real underlyings into one
  descriptive sentence as a single list entry, despite the prompt's own instruction against merging), and
  `edgartools_reader.map_terms`'s `underlying` mapping (note `08da83c9…`: `source='edgartools'` produced
  `[{"name": "For each underlying on any valuation date, (i) its closing value..."}]` — a definition
  sentence, with only a `len < 300` guard). `schema.Underlying` validated shape only (a non-empty string),
  never content.
- **1b.** `portfolio.distribution_participants` had 0 rows live. `participants.match_distribution` only
  annotated whatever role the model had already picked — it never corrected one. Role was entirely the
  model's own judgment (gold-1 note `18e37394…`: MS & Co. labeled `distribution_agent` despite its own
  quote reading "an affiliate of MSFL and a wholly owned subsidiary of Morgan Stanley").
- **1c.** `cascade.py`'s real cascade already derives from `resolved` (the agreed/Jev/escalation value) —
  correct. `prefill.py` (what gold-1 actually ran) built its `values` dict straight from model_1's single
  raw reading with no cross-check against an independent rules reading. This is the exact, confirmed
  mechanism behind the gold-1 `tenor_months` bug (note #1): rules correctly read "Pricing Date: January 28,
  2019" via its own label match; model_1 read the filing's own typo sentence ("January 28, 2018 (the
  pricing date)"); that unverified single reading is what `derive.tenor_months` used.
- **1d.** `rules.find_hypothetical_rows` already recorded a character span, but only when it successfully
  parsed ≥1 numeric row — nothing used the span to reject OTHER fields' quotes. Widened (Task 5 below) to
  record the span (`extras["hypothetical_span"]`) whenever a "hypothetical" heading is found at all, from
  the first heading's start to the last heading's 6000-char window end.
- **1e.** A registry field's `description` reaches the reader prompt verbatim via `schema.json_schema`'s
  per-field `"description"` (embedded in the system message). No few-shot examples existed anywhere in
  `readers.py` before this sprint.
- **1f.** A payoff self-check already existed (`checks.payoff_matches_examples`/`payoff_per_1000`), called
  from both `cascade.py` and `prefill.py`, comparing the recomputed payoff against the filing's OWN
  hypothetical table rows. It did not yet synthesize rows at the protection level ± a small epsilon.

**F1 — affiliated agents (Task 2).** `distribution_participants` gained `affiliated_issuer_group`
(migration `notefix2_affiliated_agents.sql`, applied via the pre-cutover postgres-owner connection — DDL
on an existing table needs table ownership, confirmed live: `app_service` got `InsufficientPrivilegeError`
on a bare `ALTER TABLE`) and 10 real, seeded participants (BofA Securities/BofAS, MLPF&S, J.P. Morgan
Securities LLC/JPMS, Morgan Stanley & Co. LLC/MS & Co., Morgan Stanley Smith Barney LLC/Morgan Stanley
Wealth Management/MSSB, Goldman Sachs & Co. LLC/GS&Co., Barclays Capital Inc., UBS Securities LLC, UBS
Financial Services Inc., Citigroup Global Markets Inc./CGMI), each `status='active'` (real reference data,
not a proposal) with `affiliated_issuer_group` matching live `structured_note_issuers.issuer_group` values.
`participants.resolve_distribution_roles` overrides a matched member's role to `issuer_affiliated_agent`
when its affiliated group equals THIS note's own issuer group (via the new `participants.load_issuer_groups`,
keyed by `reference_filings.cik`) — regardless of what any reader answered — recording `role_source` and
`role_corrected_from` when it actually changed something. `participants.calculation_agent_affiliation` runs
the identical match for the scalar `calculation_agent` field. Both wired into `cascade.py` (BEFORE staging,
not after — the correction must be what gets staged) and `prefill.py`. An unknown or unaffiliated name is
still reported, never dropped.

**F2 — list hygiene (Task 3).** `rules.filter_list_members` (and per-list `hygiene_underlying` /
`hygiene_distribution`) drops a known table header, a definition sentence, or a single entry naming more
than one real member — recording the reason, never silently discarding. Wired into `compare.evidence`
(the ONE choke point every source — rules, both model readers, EdgarTools, Jev, escalation — already calls
to build its evidence), so filtering covers every source at once; dropped members land on
`Evidence.dropped` → `ReadingRow.metadata.dropped_members` → `NoteOutcome.detail['list_hygiene_dropped']`.
`rules.cross_check_initial_levels` exists for the "cross-check against the key-terms table" requirement but
is not yet wired to a live source — EdgarTools' own parsed terms do not expose per-underlying initial
levels distinctly, so this is a real, honest gap, not a silent no-op dressed up as done.

**F2/F7 — hypothetical-section rejection (Task 5).** `compare.evidence` gained `hypothetical_span`: a
reading for `coupon_rate_pa`, `pricing_date`, `issue_date`, `maturity_date`, `barrier_pct` or `buffer_pct`
whose quote lies ENTIRELY inside the span is rejected (`drop_reason: 'hypothetical_section'`); for
`underlyings` it strips just `initial_level` from affected members (the names are typically still real).
The self-checks (`payoff_matches_examples`, `payoff_matches_at_protection_levels`) still read the section's
own rows directly — a reading is rejected FOR EXTRACTION, never for the payoff check that needs those rows
on purpose.

**F3 — buffer vs barrier.** Field definitions rewritten live (migration `notefix2_field_definitions.sql`,
DML only — `app_service` has UPDATE on this table) to give a TEST rather than relying on a filing's own
word: `barrier_pct` now says to use it "even when the filing calls it a 'threshold value', 'trigger value',
'trigger buffer level' or 'downside threshold'" and gives the breach test; `buffer_pct` says explicitly
"never the buffer rate or downside gearing (e.g. 111.11%)". **Real finding fixing this surfaced a false
positive in `schema.assert_no_shared_protection_field`**: the literal alias `'trigger buffer level'` inside
`barrier_pct`'s own description tripped the "admits a buffer level" guard, because the guard did a bare
substring check with no awareness that quoted filing-wording aliases are not semantic admissions. Fixed by
stripping single-quoted spans before the bare-word check (`_QUOTED_PHRASE`) — a real existing invariant
that needed to get smarter, not relaxed. `checks.payoff_matches_at_protection_levels` (Task 7) recomputes
the payoff at each stated level ± 0.01% and compares against the nearest real filing row within that
tolerance — catching a barrier read as a buffer even when the filing's own table doesn't happen to
tabulate a row at exactly that level; proven live against the exact "Threshold Value 60%, $599.90 at
59.99%" shape from gold-1 note #6.

**F4 — digital payouts.** `participation_rate` / `cap_pct` descriptions now say explicitly "never a digital
or fixed percentage" / "not a fixed premium or digital return"; `fixed_payout_amount` /
`fixed_payout_threshold_pct` descriptions state the digital/step/fixed-final-date-premium shape plainly.

**F5 — blank defaults (Task 4).** New derivations in `derive.py`, each a `Derived` with its own rule name,
applied ONLY when the field is unresolved (the existing decision-B guard already refuses to overwrite a
stated value, including a non-USD currency): `default_currency` → USD; `default_call_type` → 'none', gated
on a full-TEXT signal (`rules.has_call_language`, never a model's reading) finding zero call/redemption
wording anywhere in the filing; `default_coupon_memory` → false, gated the same way on
`rules.has_memory_language` and only when `coupon_type == 'contingent'`; `no_call_months` → the first
observation-schedule date that checks a call, minus `issue_date`.

**F6 — fee totals.** `derive.default_total_fees`: when only `agent_commission_pct` is stated and no other
fee component is, `total_fees_pct` is derived as that same range. `checks.fees_reconcile`'s Finding now
also proposes the corrected total (`detail.proposed_total_fees_pct = price - proceeds`) when the stated
total fails the price − fees ≈ proceeds check, instead of merely flagging the disagreement.

**F7 — wrong-reading numbers.** `checks.coupon_cross_check`: when the observation schedule states a fixed
per-period coupon amount and `coupon_frequency` is known, `coupon_rate_pa` must equal amount × periods/yr
÷ denomination × 100 (±0.01%) — catches the gold-1 "$18.75 quarterly on $1,000" (7.50%) vs a stated 6%.
`checks.maturity_after_pricing`: `maturity_date`/`final_valuation_date` must be strictly after
`pricing_date` (a value literally equal to `pricing_date` is already rejected earlier, at the evidence
level, via `reject_if_equals` sourced from the RULES' own independently-read pricing_date — never a
model's). Both the typo-date tenor bug and the maturity-equals-trade-date case are the SAME root cause
(1c) and are fixed by the same mechanism: `prefill.prefill_note` now compares model_1's reading against an
independent rules reading when both exist, and leaves the field UNRESOLVED (`PrefillNote.
input_disagreements`) rather than trusting either one alone when they disagree — so `tenor_months` /
`no_call_months` correctly come back null + needs_review instead of computing from the wrong date.

**Task 6 — few-shot block.** `readers.FEW_SHOT` (PROMPT_VERSION bumped to `notefields.reader.v4`): four
real corrected cases from the second-reader review — the BofA "Threshold Value" barrier, the Goldman
buffer-RATE, the Barclays digital payout, the BofA issuer-affiliated agent — each with its filing quote and
correct values, under ~1,500 tokens (measured 522, printed by the verify script).

**Task 8 — second-reader findings in the review screen.** New table `portfolio.note_gold_second_reader`
(migration `notefix2_second_reader.sql`, the usual global 4-policy RLS pattern) keyed on
`(reference_filing_id, field_key, seq, source)` — `seq` is the 0-based position of a finding among
findings sharing the same field within the same note (confirmed live: 3 of gold-1's 50 notes flag the same
field_key twice with distinct issues, so `(filing, field)` alone collides). Loaded by the idempotent
`scripts/load_second_reader_findings.py` — run live: 237 findings inserted, then a second run inserted 0.
`routers/note_extraction_admin.py`'s `gold_note` response now carries `second_reader.by_field` (grouped,
ready for the UI) plus `critical_count`/`total_count`; `gold_candidates` carries each note's
`second_reader_critical`/`second_reader_total` and accepts `?critical_first=true` to sort flagged notes
first. `GoldReviewScreen.jsx`'s `FieldRow` renders a "Second reader" note (suggestion + evidence quote) on
any flagged field via the new `noteGoldReview.secondReaderFor` (covered by `node --test`); a checkbox in
the batch toolbar toggles the critical-first sort. The whole response stays behind the existing
`_require_super_admin` gate — no new permission surface was added, so the gate proof is on the SAME
request, now carrying second-reader data. `note_gold_values` is never written by any of this.

**Operator step after merge:** re-run an EVALUATION (not a pre-fill — `run_note_extraction_eval.py`, not
`prefill_gold.py`) on gold-1 once real gold values exist, then `score_against_gold.py`, to measure whether
the fixes in this sprint actually move a cheap model's accuracy — this sprint fixed the engine, not the
gold-1 batch's own readings (those are superseded by a future prefill/eval run, same as notefix1's own
operator step).

**Not done in this sprint (by instruction):** no model was called and nothing was run against gold-1 for
real; `securities_global_note_terms` was not touched; no gold values were written.

---

## 00000000000000000000000000000000000000000000. NOTEFIX1 — four fixes from the first real gold pre-fill: fee fields become ranges, per-field validation, a wider hypothetical detector, goldset regression ordering; verify WRITTEN, not yet run (2026-10-13)

**Run:** `python3 apps/api/scripts/verify_notefix1.py`. Doppler-hydrated, every model call mocked, real
spend $0. Runs `verify_notefields.py` and `verify_goldset.py` once each, as flat subprocesses, AFTER its
own teardown.

**Trigger.** The first real gold pre-fill (run `e6da8fcd-b4e7-4300-9bac-48dee6e54697`, batch `gold-1`,
gpt-5-mini, $1.00) filled 49 of 50 notes. The 50th, `6380efa7-5a87-4202-8b6c-3bcaf34030aa`, failed with
"schema validation failed: 1 validation error for NoteTermsReading structuring_fee_pct.value Decimal input
should be ... input_value={'min': None, 'max': 0.4, 'bound': 'up_to'}" — the model correctly read "a
structuring fee of up to 0.85%" for a field the v3 registry still called `scalar`. Only the note's 5
rules/derived values were saved; every model-read field was lost. 0 gold values exist on `gold-1` (all 50
candidates `proposed`), so the fix lands before any human review depends on the old shapes.

**Task 1 (discovery), reported in full in `verify_notefix1.py`'s own [Y] output:**
- **1a.** The whole-reading failure lived in exactly one place: `schema.parse_reader_output` validated the
  reader's ENTIRE JSON response as one combined Pydantic model (`pydantic_model(specs).model_validate_json`)
  — one field's `ValidationError` raised for the whole object, which `readers.read` turned into
  `call.status = 'invalid'`, discarding every other field's value. Fixed by validating per field (below).
- **1b.** The review screen already picks the newest `gold_prefill` run per note correctly, with no code
  change needed: `gold.prefill_runs_for` orders candidate runs `ORDER BY r.started_at DESC`, and
  `routers/note_extraction_admin.py`'s `gold_note` takes `prefill_runs[0]`. A second (superseding) run is
  "picked up cleanly" automatically, because every note in the batch gets a fresh reading under the new
  run's id.
- **1c.** `checks.fees_reconcile` read `price_to_public_pct`, `total_fees_pct` and `proceeds_to_issuer_pct`
  as three scalars. With the latter two now ranges, it compares the IMPLIED proceeds interval (`price -
  fees`, varying fees across its own min/max) against the stated proceeds range, and fires only when the
  two intervals share no feasible point (within `FEES_TOLERANCE_PCT`) — see `NOTE_FIELDS.md`'s Self-checks
  section for the exact rule. `estimated_value_below_price` already used `as_range` and needed no change.
- **1d.** Ran the detector over the 138 fetched finals in cohort `54024e47-096c-4120-bcf3-e3403ad2ef7b`
  (read-only; real R2 text, no model call). The old `hypothetical_example_table` trap (which required
  `rules.find_hypothetical_rows` to parse ≥2 numeric "% ... $" rows from a 6000-char window after the FIRST
  "hypothetical" occurrence anywhere in the filing) hit **1 of 138**. Real per-bank headings: Morgan
  Stanley / Goldman Sachs / UBS use "Hypothetical Examples" (UBS also "...of How the Notes Might Perform");
  Bank of America uses "Hypothetical Payout Profile and Examples of Payments at Maturity"; Citigroup and
  Barclays use column-header wording ("Hypothetical final underlying value", "Hypothetical underlying
  return", "hypothetical Initial Value of each Reference Asset"); JPMorgan's filings in this cohort contain
  no inline worked table at all in the extracted text — only risk-factor boilerplate ("hypothetical
  components: (1) a fixed-income debt component...") — so a miss on JPMorgan is a correct quiet result, not
  a bug. Also checked bound ("up to" / "at least") wording near the five economics fields: confirmed on
  `structuring_fee_pct` (6 hits) and `other_fees_pct` (24 hits, mostly Bank of America referral fees); zero
  on `total_fees_pct` and `proceeds_to_issuer_pct` in THIS cohort (widened anyway, per the task — the real
  incident field, `structuring_fee_pct`, confirms the shape exists in the wild). `price_to_public_pct`'s
  only 2 "up to" hits (both Goldman Sachs) describe a DEALER RESALE DISCOUNT off the price, not the field's
  own value — it correctly stays scalar.

**Task 2 — fee fields become ranges.** `value_shape` is now `'range'` for `structuring_fee_pct`,
`other_fees_pct`, `total_fees_pct` and `proceeds_to_issuer_pct` (migration
`notefix1_economics_field_ranges.sql`; `NOTE_FIELDS.md` updated to match). `price_to_public_pct` stays
scalar (1d). **No data migration was needed**: `schema.as_range()` already reads a bare scalar as `{min:
v, max: v, bound: 'exact'}`, so every existing reading, staged field and (there are currently zero) gold
value on these four keys reads correctly under the new range kind with no rewrite — readings are
append-only and this migration never touches them.

**Task 3 — one bad field never rejects a note.** `schema.parse_reader_output` now parses the response's
JSON once, then validates each field's own `{value, quote}` object against its own per-field Pydantic
model (`_field_model`, cached per `(key, kind, enum)` so a shape change invalidates only its own entry). A
field that fails validation is DROPPED from the result and recorded, with its error and raw value, in a new
`field_issues` return value — every other field is kept. The whole reading only fails (`fatal_error` set,
`fields is None`) when the response is not parseable JSON, or not a JSON object, at all. Additionally, an
unambiguous shape mismatch is COERCED rather than dropped: a bare scalar answered for a range field becomes
an exact range (`min = max`, `bound: 'exact'`); a range answered for a scalar field becomes that scalar only
when its `min` and `max` already agree (there is no ambiguity about which end is meant) — recorded in
`field_issues` with `coerced` set and `error: None`. `readers.ReaderCall` carries the new `field_issues`;
`cascade.run_note` turns a real field error into a `needs_review` reason (both at the note level and on
that field's own staged metadata, `review_reasons`) while leaving every other field's resolution untouched;
`prefill.prefill_note` records the same `field_issues` on the gold-prefill run's per-note report. The
original incident (note `6380efa7...`) now stores `structuring_fee_pct` as a range with the bound wording
as its quote instead of losing the whole note.

**Task 4 — wider hypothetical-example detector.** `traps._hypothetical` now ALSO fires on real heading and
column-header wording (`traps._HYPOTHETICAL_WORDING`): "Hypothetical Examples" (any case), "Examples of
Hypothetical Payments", "Hypothetical Payout Profile", "Hypothetical Payment/Return at Maturity", "What Is
the Total Return ... Assuming", and the "hypothetical initial/starting/ending/final value/level" /
"hypothetical underlying return" column-header family — in ADDITION to the original numeric-row parser
(`rules.find_hypothetical_rows`, kept unchanged since the self-check `checks.payoff_matches_examples` needs
its strict, parseable rows for real). New hit rate over the 138 fetched finals: **132/138** (up from 1/138)
— JPMorgan 18/18, Morgan Stanley 27/30, UBS 13/13, Goldman Sachs 7/9, Citigroup 17/17, Bank of America
29/29, Barclays 21/22. The module's own "a false positive just costs one extra sampled note" tolerance
means firing on a bank's boilerplate "hypothetical" disclaimer sentence (no inline table) is acceptable;
the detector still stays quiet on a document with zero "hypothetical" wording at all.

**Task 5 — gold verify discipline.**
- `verify_goldset.py`'s nested `verify_notefields.py` regression used to run INSIDE the main try block,
  before its own `finally`-block teardown — meaning it saw goldset's own live fixtures while they still
  existed. This was the exact, already-known cause of the single historical FAIL (127/128). Moved: the
  regression now runs only after teardown completes AND `fixture_counts` confirms zero rows remain (a
  `teardown_clean` flag gates it; a dirty teardown skips the regression and reports its own FAIL instead
  of running a contaminated check).
- `prefill_gold.py` gained `--supersede`: a second `gold_prefill` run for a batch that already has one is
  refused (`prefill.PrefillRefused`) unless `--supersede` is passed, and refused even then once the batch
  has any human gold value (`prefill.batch_has_gold_values`, `note_gold_values` with `valid_to IS NULL`).
  Nothing is written either way when refused. A batch with no prior run never needs the flag. Per 1b, the
  review screen serves the newest run automatically once a superseding run is allowed to exist.
- **Operator step:** re-run the gold-1 pre-fill with `--supersede` (e.g. `python3
  apps/api/scripts/prefill_gold.py --batch gold-1 --model gpt-5-mini --max-tokens 32000 --spend-cap 5
  --supersede`) to pick up the range-shaped fields and per-field validation on the note that previously
  failed, before Joe's review starts.

**Not done in this sprint (by instruction):** no model was called and nothing was run against gold-1 for
real; the re-prefill is an operator step, listed above.

---

## 0000000000000000000000000000000000000000000. GOLD SET — stratified top-7-bank sample with trap cases, gpt-5-mini pre-fill, a fast review screen, and a scorer; verify WRITTEN, not yet run (2026-10-08)

**Run:** `python3 apps/api/scripts/verify_goldset.py`. It hydrates from Doppler and mocks every model call
and every R2/EDGAR read, so it costs $0. It runs `verify_notefields.py` once as a regression subprocess.
Pure pieces were dry-run in memory before handover:
- the nine trap detectors: each fires on its crafted snippet and on nothing else;
- the propose choice: minimums met on a full pool, shortfall reported on a thin one, deterministic per seed;
- the scorer: counts, range agreement, a reordered list scored correct, "too few", and gate FAIL.
The web helpers have `node --test tests/goldset/` (7/7). The DB sections have NOT been run.

**Task 1 findings** are in `docs/GOLDSET_DISCOVERY.md` (1a UI/API and the v3 changes, 1b reusable
comparison, 1c cohort build/fetch/classify, 1d the trap detectors).

**The workflow** — six steps, each its own command:
1. **Plan.** `python3 apps/api/scripts/build_gold_set.py --plan --size 300 --name "<name>" [--seed N]`
   - Freezes an edgar cohort over 28 strata: the seven banks (JPMorgan, Morgan Stanley, UBS, Goldman Sachs,
     Citigroup, Bank of America, Barclays) × four eras (2019-20, 2021-22, 2023-24, 2025-26).
   - Equal allocation: `ceil(size/28)` per stratum, so 11 each for 300 → 308.
   - Order within a stratum is the seeded `hashtextextended`. The seed, banks, eras and per-stratum count
     are stored in the cohort definition (`source: 'gold_plan'`, kind `custom`).
   - Only policy-selected 424B2s are planned, and members are deduplicated by accession.
   - Re-running with the same name writes nothing. `--dry-run` prints the plan.
2. **Fetch.** Run the cohort through the existing pipeline (Cohorts tab → Run). About 25% come back as
   FINAL pricing supplements; that is why the plan over-fetches.
3. **Propose.** `build_gold_set.py --propose <cohort_id> --target 50 --batch "<batch>"`
   - Draws only from the cohort's FETCHED `document_kind = 'pricing_supplement'` members.
   - Minimums: ≥ 6 per bank, ≥ 1 per bank × era, ≥ 3 per trap tag where the pool allows.
   - Shortfalls are reported per stratum, per bank and per tag, never padded.
   - A filing already in any batch is never re-proposed (code filter + the table's UNIQUE).
4. **Pre-fill.** `python3 apps/api/scripts/prefill_gold.py --batch <batch> --model gpt-5-mini --max-tokens 32000 --spend-cap <usd> [--dry-run]`
   - One run per batch, `run_kind 'gold_prefill'`.
   - Each note gets rules + EdgarTools + ONE reader + rules-only fields + derivations + self-checks. Every
     reading carries the run id, and nothing is staged.
   - Refused before any write: Claude, OpenRouter, an unpriced model, and `--max-tokens < 32000`.
     public_data_only is accepted, because the task is `note_terms_extraction` with no org.
5. **Review.** `/admin/note-extraction/gold`, Super Admin only (the same `rbac.is_super_admin` gate).
   - One note at a time: the document text on the left with the field's quote highlighted, and fields by
     section on the right. Critical, economics and distribution sections are expanded; the rest collapsed.
   - Each field shows the pre-fill value, its quote, and whether it came from the rules, the model or a
     derivation.
   - Confirm / Correct / Absent: a range is corrected as min / max / bound, and a list by editing rows.
   - Keys: J/K, C, A, E, N. There is no confirm-all.
   - The candidate moves proposed → in_review (first save) → done (every live field has a current gold
     value). Skip needs a reason.
   - A correction closes the old row (valid_to) and inserts the new one, so the history stays readable.
6. **Score.** `python3 apps/api/scripts/score_against_gold.py --run <run_id> [--batch <batch>] [--fields critical|core|all]`
   - Each field is scored correct / wrong / missed / false, plus accuracy, on notes both cover.
   - The cascade's `normalize` is used throughout: ranges compare min and max, and lists use the notefields
     list comparison.
   - Fewer than 10 notes → "too few".
   - GATE: every critical field ≥ 95% on ≥ 40 reviewed notes.
   - The result is merged into `note_extraction_runs.report->'gold_score'`.

**ANCHORING CAVEAT.** The reviewer sees gpt-5-mini's pre-filled values first. gpt-5-mini's own scores
against this gold set are therefore biased upward and must always be reported with that caveat. The review
screen shows this note, the pre-fill run's config records it, and the scorer prints it, marking a run as
`anchored` when it is the pre-fill or names a pre-fill model.

**Migrations.**
- `goldset_prefill_run_kind.sql` adds `'gold_prefill'` to `note_extraction_runs.run_kind`. It is a CHECK
  swap and the only DROP-containing file, so it was split out.
- `goldset_candidate_review.sql` is additive: it adds `note_gold_candidates.skip_reason` and `skipped_by`,
  plus a CHECK that a skipped row has a reason. There is no new table, so no new policies; the table already
  has the four global-table policies.
- Both were **applied live via MCP on 2026-10-08** and re-read from `pg_constraint`.
- `goldset_drop_edgar_index_filings_indexes.sql` runs `DROP INDEX IF EXISTS` on
  `edgar_index_filings_form_date_idx` and `edgar_index_filings_status_idx`. Both were dropped LIVE on
  2026-10-08 to save space and are covered by `form_sort_idx` and `queue_idx`. This file brings the repo in
  line; it is idempotent and already applied.

**Not done in this sprint (by instruction):** no document was fetched and no model was called. The plan,
fetch, propose, pre-fill and review are all operator steps.

---

## 000000000000000000000000000000000000000000. NOTE FIELDS v3 — the approved structured-note schema: registry-only reader schema, three structured lists, generated per-bank label dictionary, ranges, derivations, self-checks, two model guardrails; verify WRITTEN, not yet run (2026-10-08)

**Run:** `python3 apps/api/scripts/verify_notefields.py` (Doppler-hydrated, all
model calls mocked, $0). Before it was handed over, its pure sections (lists,
rules, derivations, ranges, self-checks) were dry-run in memory: 73 PASS / 0 FAIL.
The two cascade fixtures were also run in memory: the consistent one comes out
verified, and the inconsistent one fires all five self-checks. The DB-writing
sections have NOT been run.

**The approved field list.** `docs/NOTE_FIELDS.md` (v3, approved by Joe
2026-10-08) is loaded into `portfolio.note_terms_field_registry`. The registry
gained `description` (used verbatim in reader prompts and in the inventory's
mapping check), `section`, `sort_order`, `is_critical`, `value_shape`
(scalar/range/list), `unit`, `enum_values`, `synonyms`, `trap_rule`,
`extraction_method` (model/rules/derived), `derived_from`, `former_keys`,
`retired_at`, `replaced_by` and `replacement_rule`. It now has **59 live rows**:
47 are answered by the readers, 10 rules-only (the 9 section-9 fields plus ISIN)
and 2 derived. Another **14 rows are retired**. **27 are critical, exactly the ★
set**: the ★ top-level fields, plus `underlyings` and `distribution`, which each
have a ★ member. `schema.build_field_specs` reads ONLY the registry. B1's
`_EXTENSIONS` list, the `_REGISTRY_OVERRIDES` table and `CRITICAL_FIELDS` are
gone. Migrations: `notefields_widen_checks.sql` (CHECKs widened: data_type
+json, readings source +derived, staged resolution +rules/+derived) and
`notefields_registry_v3.sql`. Both were applied live via MCP on 2026-10-08.

**Renames and migrated rows.** Every renamed key keeps its old row, retired
with its mapping: coupon_rate→coupon_rate_pa,
autocall_barrier_pct→autocall_level_pct, tenor_years→tenor_months (×12),
notional_currency→currency, initial_valuation_date→pricing_date (decision A),
and protection_pct→buffer_pct/barrier_pct (split by protection_type). B1's
former extension keys principal_conditional, total_commissions_fees_pct,
fee_based_account_price_pct (×10, now a range) and denomination_amount were
renamed the same way. has_no_call_period, is_decrement_index, return_basis
(moved into `underlyings[]`) and terms_status are retired with a stated mapping.
**Migrated row count: 0.** Counted live: no reading, staged field or gold value
sat on a renamed key. The 58 migrated legacy readings are on protection_type,
autocall_frequency and basket_type (all still live) and on is_decrement_index,
return_basis and terms_status (retired, mapped, values left as stated). There are
no orphans. Both BEFORE UPDATE triggers (readings immutability, gold human guard)
were disabled only for the migration's UPDATE and are re-enabled. **The legacy
Claude extractor** (`services/note_terms_extraction.py`) writes fixed
`securities_global_note_terms` columns. It now reads only its 19 original keys
(`LEGACY_FIELD_KEYS`, live or retired), so no v3 change alters what it writes.
securities_global_note_terms has 54 rows, untouched (B2).

**Three structured lists** (`underlyings`, `observation_schedule`,
`distribution`) are jsonb values: readings, staged fields and gold values were
already jsonb, so no new column was needed. Each member is validated by Pydantic
(`schema.LIST_MEMBER_MODELS`). Comparison is member-by-member with scalar
normalisation. Underlyings and participants are order-insensitive; the schedule
is canonically ordered by date. Distribution members are matched to
`distribution_participants` by canonical name or alias. An unmatched member stays
in the list with no id and is reported in `unmatched_participants`.

**The generated label dictionary.**
`apps/api/services/note_extraction/label_dictionary_v1.json` is generated by
`scripts/generate_note_label_dictionary.py` from inventory run `aa1c8c7c`. It
covers 1,417 items, 50 fields and 22 issuer groups. 454 labels were dropped
because they never appear verbatim in their own quote (model annotations like
"(implied)", or labels carrying a value). The rules try dictionary labels
most-used-bank first, then registry synonyms, and record which label and which
banks on every hit. The hand-written date-label list is gone. The inventory
mapping check's `_SYNONYMS` table is replaced by the registry's `synonyms` column
(`edgar_inventory.synonym_table`).

**Rules, derivations, self-checks.** The rules now also extract ISIN (check
digit), issue date, issue size, proceeds (the third column of the "Per Note"
table) and the section-9 fields. "Up to", "as low as", "not less than" and
"between" amounts become `{min, max, bound}` ranges, with the bound wording kept
in the quote (decision C). Derived values (`services/note_extraction/derive.py`)
are staged with resolution 'derived' and get a 'derived' reading. They are
tenor_months, max_principal_loss_pct, and decision B's unstated estimated-value
unit; a stated value is never overwritten. Five self-checks
(`services/note_extraction/checks.py`) set needs_review with a reason and never
change a value:
- fees reconcile with proceeds;
- estimated value is below the price;
- schedule dates fall within the term;
- the initial valuation date equals the pricing date (decision A);
- the recomputed payoff matches the hypothetical table.
**Behaviour change in compare:** when both readers agree a field is absent but
a rule quotes a verified value, the field is now *disputed* instead of
agreed_null, so labeled-field rules are never silently outvoted.

**Guardrail 1 — `platform_model_catalog.public_data_only`** (default false; set
true for gpt-5-mini and gpt-5-nano, the OpenAI models). Such a model may serve
only `model_catalog.PUBLIC_DATA_TASK_KEYS` (`note_terms_extraction`,
`edgar_inventory`), and only with no org in scope. Four places refuse it:
- the org picker hides it;
- `set_org_selections` refuses it;
- `validate_assignable_model` refuses it;
- `services.extraction._execute_chain` drops it from every org-scoped or
  non-public call, and fails CLOSED if the flag can't be read.

Checked live: no org had selected or assigned an OpenAI model.

**Guardrail 2 — bulk-run price guard.** The catalog gained
`manual_input_cost_per_mtok` and `manual_output_cost_per_mtok` (both or neither,
≥ 0). Bulk runs resolve their models through `spend.priced_catalog_for_bulk`:
the proxy's price wins, a manual price fills a missing one, and a model with
neither raises `UnpricedModelError` before any run row is created. This covers
`runner.run_notes` (pilot, B2), the evaluation script (the candidate is
BLOCKED) and the inventory (not eligible). A manual-priced call is costed from
the manual rate even when the proxy's cost header says $0. **Today every catalog
entry has a proxy price** (Task 1d). Jev (System One) is not a catalog model and
keeps its explicit per-call estimate.

**Prior verify scripts this sprint makes stale (expected, not regressions):**
`verify_noteextractb1.py` is CONFIRMED stale: it builds specs from a 3-column
registry select, and the builder now refuses a row with no description.
`verify_edgarcohorts.py` and `verify_edgarinventory_v2.py` are UNCONFIRMED.
They read the live registry or hand-built FieldSpecs and may assert on pre-v3
keys such as `initial_valuation_date`, now retired. Check them when they are
next run.

## 00000000000000000000000000000000000000000. Market data mkt04c — key dates, My dates, saved views and the "What moves with it" correlations panel (front end); verify WRITTEN, not yet run (2026-10-07)

`mkt04c.structural`. Finishes the `/market` page for internal use. It adds
the Key dates and My dates dropdowns under the chart (add and delete your
own dates), a "Saved views" card at the top of the left column (presets,
your own views, save / update / delete) and the "What moves with it"
correlations card between the chart and the trends table. Front end only: no
backend change, no DDL, no new dependency, and the Grid tab is unchanged.
Design: `docs/MARKET_DATA_DESIGN_V1.md`, "Key dates, views and correlations
(front end)".

**Built:**
- Pure modules `apps/web/lib/market/{keyDatesModel,customDates,viewsModel,
  correlationModel,viewContract}.mjs`. `viewContract.mjs` is the scan's third
  pinned exemption: the config version, the two anchor shapes and the month
  precision.
- Components `apps/web/components/market/{KeyDatesBar,KeyDatesPanel,
  CorrelationsView,CorrelationsPanel,SavedViewsView,SavedViewsPanel}.jsx`
  and `marketClient.mjs` (the one browser request helper). Each `*View` /
  `*Bar` is hook-free, so tests call it and fire its handlers. Each `*Panel`
  holds state and sends what the pure modules build.
- Edits to mkt04a/b files, all required by the features:
  - `MarketIndicatorsView`: lifts the picked date, the view notices, `end`
    and `anchorYears`, and wraps the left column so Saved views sits on top.
  - `ChartPanel`: key-dates reload counter. Still exactly one
    `fetchJson(KEY_DATES_ROUTE)`.
  - `ChartView`: the two new panels, and the "5 years ago" button marks the
    anchor as relative.
  - `MarketChart`: selected-period band, personal-date lines, flag width,
    and two slots.
  - `chartModel`: flag text, band, personal lines and two legend entries.
  - `chartRequest.interpretKeyDates`: also returns `customDates`,
    `permissions` and `limits`.
- Tests `apps/web/tests/market/{keyDatesModel,customDates,viewsModel,
  correlationModel,mkt04cRender}.test.mjs` plus `mkt04cFixtures.mjs`: 48 new.
  The whole market suite is 231/231.
- `apps/api/scripts/verify_mkt04c.py`.

**Task 1 discovery:**
- All page state already lived in `ReadyView`. The plan was to keep that one
  owner, so a view sets selection and chart settings in one step. Any anchor
  patch from the chart drops the picked date. A pick sets anchor and
  selection together.
- `snapAnchorIndex` snaps to the period **on or before** a date. At month-end
  periods, 2020-03-16 would land on FEBRUARY. A picked date therefore moves
  the anchor to the period that CONTAINS it (the first period ending on or
  after it), which the existing snap then keeps exactly. That gives "a
  day-precision date lands on its month".
- Backend contract (read-only) matches CONFIRMED REAL FACTS. The differences
  are additive:
  - views also publish `selection_max`, `config_max_bytes` and
    `relative_years` (1..60), plus `anchor_types` and `selection_kinds`;
  - custom dates also carry `updated_at`;
  - correlations also return `lag_convention`, `warnings` and
    `change_method`;
  - the lag range is published at `catalog.vocabularies.limits.lag_months`.

**[FIND] The personal endpoints repeat their message.** A refusal arrives as
`{message: m, errors: [{msg: m}]}`, so mkt04a's `errorMessage` would show
"m: m". `customDates.refusalMessage` shows a field message only when it
differs from the headline. Text is still the server's, verbatim.

**[FIND] No reason labels for correlations.** The server publishes none for
correlation `unavailable_reason` (e.g. `insufficient_overlap`). The row
shows the code as text through `vocabText`, which will use a server list
if one ever appears.

**[FIND] The chart has no end control.** A view's `end` is carried in page
state, saved back unchanged and sent as the correlation window's end, but
the chart itself is not truncated by it. No preset sets an end today.

**[FIND] Views are refused on write when a key is unavailable.** POST/PUT of
a view whose selection holds an unavailable key is a 422. Loading such a
view skips the key, so a later Update saves the reduced selection. That is
the user's explicit act, and loading alone never writes.

**[FIND] verify_mkt04b required every `tests/market/*.test.mjs` to be in its
own list,** so any later sprint's suite failed it. Re-pinned minimally: the
suites on disk ∩ the suites in mkt04b's own commit (`mkt04b_suites`). A
missing mkt04b suite still fails, and its 277 total is unchanged.
verify_mkt04c asserts that `node_tests` and the new helper are the only
top-level changes.

**Interpretations recorded:**
- The correlation focus defaults to the first selected series. A security
  linked to a selected indicator is one series.
- Requests go 300 ms after the last change of focus, lag, selection or
  anchor. `min_periods` is left to the server.
- r is never parsed. The bar width is the same digits read as a percentage
  by moving the decimal point in the text ("0.8312" → 83.12%).
- An anchor still equal to "5 years ago" (the page default, the quick button
  or a loaded relative view) saves as `{"type": "relative", "years": N}`.
  Any other anchor saves as a date.

**Not run by this sprint (by rule):** the dev server, the production build
and the verify. What did run: the node:test suites (231/231), and ESLint on
lib/market, components/market and tests/market (clean). verify_mkt04a/b's
static-scan rules were also applied to every new and changed file (clean).

**OPERATOR ACTIONS:**
1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04c.py`
   (tests, scans, scope, lint and the first production build with these
   panels).
2. `verify_mkt04b.py` and `verify_mkt04a.py` as regressions.
3. Signed in at `/market`, Chart tab:
   - pick a range key date and switch Start / End;
   - add a personal date: a duplicate shows the 409 message, and the inputs
     stay;
   - delete it (two steps);
   - save, load, update and delete a view;
   - load a preset;
   - change the correlation focus and lag.
4. **Launch blocker still open:** per-tenant gating of Yahoo-sourced and
   third-party-licensed series.

---

## 0000000000000000000000000000000000000000. Market data mkt04b — market indicators chart: anchor bar, client-side rebasing, overlays, and the series `stddev` field; verify WRITTEN, not yet run (2026-10-07)

`mkt04b.structural`. The Chart tab of `/market`, replacing mkt04a's
placeholder: a multi-line SVG chart with a draggable anchor bar that
re-measures every line in the browser, a log or linear axis, hover, regime
bands and key-date lines, a cohort band with outlier highlighting, notices,
and the "Trends and outliers" table. The one backend change is the additive
`stddev` field on `GET /market/series`. No DDL, no new dependency, and the
Grid tab is unchanged. Design: `docs/MARKET_DATA_DESIGN_V1.md`,
"Chart (mkt04b)" and "API contract".

**Built:**
- `apps/api/services/market_data/read_service.py`: `stddev_text` and one
  `stddev` key per series in `read_series`, from `read_repository.series_stats`
  (the same `stddev_samp` the grid's sigma uses; 6 dp half-even text, null
  under two points or at zero variance). Read live, read-only:
  `fred.dgs10` → `2.927482`, `fred.sp500` → `1559.763221`.
- Pure modules `apps/web/lib/market/{chartContract,rebase,scales,hitTest,
  overlays,labelLayout,chartModel,chartRequest}.mjs`.
- Components `apps/web/components/market/{ChartPanel,ChartView,MarketChart,
  AnchorSlider,TrendsTable}.jsx`. `MarketIndicatorsView` swaps the placeholder
  for `ChartPanel` and holds the chart's own settings beside the grid's
  untouched controls.
- `apps/api/scripts/gen_mkt04b_golden.py` → `apps/web/tests/market/
  golden_rebase.json` (9 cases × index and sigma, from the PRODUCTION
  transforms).
- Tests `apps/web/tests/market/{rebase,chartGeometry,chartModel,chartRequest,
  chartRender}.test.mjs` (74 new; the whole market suite is 183/183).
- `apps/api/scripts/verify_mkt04b.py`.

**Task 1 discovery:**
- Page state lives in `MarketIndicatorsView`'s `ReadyView`
  (tab, selection `[{kind,key}]`, grid controls). `GridPanel` fetches through
  `createDebouncer` and `createLatestGate`. The placeholder was a `<p>` in
  the chart branch.
- `stddev_samp` was already computed for the grid in
  `read_repository.series_stats`. `read_series` used `series_bounds` (the same
  scan without it), so switching to `series_stats` was the whole change.
- `verify_mkt03.py` checks series fields one by one, with no strict key set,
  so it needed **no edit**.
- Components render in tests through `tests/market/jsxLoader.mjs` with
  `react-dom/server`. There is no jsdom, so the anchor input became a
  hook-free `AnchorSlider` whose real `onKeyDown` the test calls directly.

**[FIND] verify_mkt04a was green only while mkt04a was uncommitted.** It
compared `merge-base main HEAD` with the working tree and globbed every file
in `lib/market` and `components/market`. With mkt04a on main
(main == HEAD at mkt04b start), its diff-scope checks measured the NEXT
sprint, and its Sidebar check failed with no change at all. Its
no-hardcoding scan would also have rejected the chart's contract codes,
which are confined to `chartContract.mjs`. Fixed by pinning it to mkt04a's
own commit (`git log --diff-filter=A` on its route core). Its static
sections give 105/0 under the pin. Its total will no longer be 225, because
render.test.mjs's Chart test was replaced (below).

**[FIND] render.test.mjs's "the Chart tab shows only the placeholder"** was
replaced by "the Chart tab renders the chart's controls, not the grid's and
not the old placeholder". The other mkt04a render tests are unchanged, and
verify_mkt04b asserts exactly that.

**[FIND] stddev precision.** Chart sigma divides by the 6 dp `stddev`, while
the grid divides by the unrounded value. The relative difference is at most
5e-7 / sd: negligible for every live series today, material only below an
sd of about 0.001.

**[FIND] The big-move rule as specified (|period change| / its own sd)**
scores every period of a smooth, steady trend highly. On real, noisy series
it marks genuine shocks (2008, 2020 in the live render). Kept as specified,
with the top three per series.

**[FIND] The log-index clamp (1..10,000) clips long histories anchored
late.** For example, Nasdaq 100 anchored in 2021 falls below index 1 before
about 1987. This is by the spec's clamp. Linear is the alternative.

**Interpretations recorded:**
- Lines are plotted per PERIOD by the as-of rule, never past a series' own
  last observation. The anchor snaps to period ends, where as-of on the
  downsampled points equals as-of on the full data.
- Fences, the median and table distance are computed in axis space (log10 on
  a log axis). "12 mo" is a percent change for Index and a sigma difference
  for Sigma.
- Key dates are fetched once per chart mount and fail closed on their own.
  The chart still draws without them.
- Chart settings are separate from the Grid's controls. Both default to an
  anchor five years back. The chart defaults to monthly resolution, the
  Index measure and a log axis, with every overlay on.

**Not run by this sprint (by rule):** the dev server, the production build
and the verify. What did run: the node:test suites (183/183); ESLint on
every market file (clean, including `react-hooks/refs`); the golden
generator (`--check` current); the verify's offline sections in-process
(59/0); verify_mkt04a's static sections (105/0); and a visual check. That
check rendered the real `MarketChart` server-side with live catalog, series
and key-date data read read-only, then screenshotted it with headless Chrome
from a temp file outside the repo, in both Index-log and Sigma.

**OPERATOR ACTIONS:**
1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04b.py --live`
   (runs the tests, lint, the first `npm run build` with the chart, and the
   live stddev proofs; Phase B's single-point fixture is rolled back).
2. `verify_mkt04a.py` and `verify_mkt03.py --live` as regressions.
3. Signed in at `/market`: drag the anchor, use the arrow keys and Page
   Up/Down on the slider, hover a line, and toggle each overlay.
4. **Launch blocker still open:** per-tenant gating of Yahoo-sourced and
   third-party-licensed series.

`UPDATE 2026-10-07` (mkt04c): with mkt04c the `/market` page is
feature-complete for INTERNAL use: grid, chart, key dates and My dates,
saved views and the correlations panel. Before any external customer sees
it, these remain:
1. The per-tenant gate for restricted series: Yahoo-sourced (including the
   Yahoo-sourced S&P 500 rows before F0) and third-party-licensed series,
   including those a saved view or preset opens. It must run before
   external access (see `docs/MARKET_DATA_DESIGN_V1.md`, "Launch
   blockers").
2. The licensing questions behind that gate (launch blockers 1 and 3).

---

## 000000000000000000000000000000000000000. Market data mkt04a — market indicators page: Next.js routes, page shell, selection panel and grid; verify WRITTEN, not yet run (2026-10-07)

`mkt04a.structural`. The first front-end slice over the finished market data
API: the Next.js routes for all eleven backend routes, a new page at
`/market` ("Market indicators"), the selection panel and the Grid tab. The
chart (mkt04b) and the key-date, saved-view and correlations controls
(mkt04c) are not built; the Chart tab is a placeholder. No backend change, no
DDL, no new dependency. Design: `docs/MARKET_DATA_DESIGN_V1.md`, "Front end
(mkt04a)".

**Built (all in `apps/web`):**
- `lib/market/marketRoutes.mjs` (pure forward core + the eleven-route table),
  `lib/marketForward.js` (binds it to the host-aware
  `getRequestAuthClient`), and nine route files under `app/api/market/**`
  exporting the eleven handlers. Mirrors `lib/apiForward.js` (session check,
  token, the same two 401 bodies, Bearer header) except: the backend's status
  and body pass through byte-for-byte, every response is `no-store`, nothing
  is logged, and a body is forwarded as the caller's own text after a JSON
  parse check.
- `app/market/page.js` (host-aware `getHostSession`, AppShell),
  `components/market/` (MarketIndicators, MarketIndicatorsView, SelectionPanel,
  GridPanel, MarketGridTable, marketStyles).
- Pure modules `lib/market/{catalogModel,selection,gridRequest,gridView,marketDefaults}.mjs`.
- One nav entry, "Market Indicators" → `/market`, in `components/Sidebar.jsx`
  NAV_ITEMS and `lib/menuVisibility.mjs` MENU_ITEMS (`gate: null`).
- `tests/market/*.test.mjs` (node:test, 109 tests, all passing during the
  sprint) and a `test` script (`node --test tests/`) in `apps/web/package.json`.
- `apps/api/scripts/verify_mkt04a.py`.

**Task 1 discovery:**
- `apps/web` is JavaScript (jsconfig `@/*` alias, no TypeScript, no typecheck
  script), Next 16.1, React 19.2, Tailwind v4 with `--2a-*` CSS variables
  injected from org_settings; fonts are loaded by the root layout from
  settings. There was NO test runner: earlier UI sprints proved their logic
  with pure `.mjs` modules exercised by Node harnesses in `apps/api/scripts`.
- Forward pattern copied from `lib/apiForward.js` (+ `lib/authServer.js`
  `getRequestAuthClient`); base URL variable `NEXT_PUBLIC_API_URL`.
- Envelope rendering mirrored from `components/portfolio/PositionsGrid.jsx`.
  `components/ui/DataGrid.jsx` was NOT reused: it sorts, filters, paginates
  and drag-reorders client-side and has no sticky header or rich header cell;
  the grid must keep the server's order and show every row.
- Navigation: `Sidebar.jsx` NAV_ITEMS render unfiltered for any signed-in
  user; MENU_ITEMS `gate: null` means the same. That matches the API's
  session-only gate.
- Dates use the native `<input type="date">` (the existing convention); no
  `/market` route existed (`/marketplace` does, no collision).

**[FIND] `npm run lint` already fails on main** — 98 errors in 67 files this
sprint did not touch (64 are `react-hooks/set-state-in-effect`). The new and
changed files lint clean. The verify gates on this sprint's files and reports
the baseline as a [FIND]; fixing it would change existing pages, which the
sprint forbids.

**[FIND] The server publishes no labels for license classes, grid warnings,
unavailable reasons or the unselectable reason.** The page shows the code
text with underscores as spaces; `vocabText` will use a `[{key, label}]`
vocabulary if the API ever adds one.

**[FIND] The prompt's own rules name vocabulary values** (default mode
`default`, frequency `monthly`, the `public_domain` exception, the kinds
`indicator`/`security`). They live in ONE file, `lib/market/marketDefaults.mjs`,
validated against the server's vocabulary at runtime; the verify's
no-hardcoding scan exempts only that file and pins its exact contents.

**Interpretations recorded:**
- The page opens on the Grid tab (the only working view in this release).
- The 40-series limit counts selections (as the saved-view `selection_max`
  does); an indicator and the security priced by it are requested once.
- A category chip that would cross the limit adds nothing (all or none).
- Security groups come from `security_type`; a group with nothing selectable
  (the structured notes) starts collapsed with its count.
- An unavailable series shows its reason in one cell spanning the column.
- JSX in tests is compiled by `tests/market/jsxLoader.mjs` with the
  `typescript` package already in node_modules (transitive, ESLint
  toolchain); nothing was added to package.json dependencies.

**Not run by this sprint (by rule):** the dev server, the production build
and the verify. What did run: the node:test suites (109/109), ESLint on every
new and changed file (clean), and the verify's static checks alone (97/0),
plus a probe proving the no-hardcoding scan catches a planted violation of
each kind.

**OPERATOR ACTIONS:**
1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt04a.py`
   (runs the tests, lint and `npm run build`).
2. The manual checklist in the sprint log, signed in.
3. **Launch blocker still open:** per-tenant gating of Yahoo-sourced and
   third-party-licensed series is unbuilt. The page shows them with their
   source; it must not reach an external customer before that sprint.

**UPDATE 2026-10-07 — the chart is built (`mkt04b.structural`, entry above).**
The Chart tab now draws the chart; the placeholder is gone and render.test's
Chart test asserts the chart. This entry's verify is now pinned to its own
commit, so its scope checks no longer measure later sprints. Still to come:
mkt04c (key dates, saved views, correlations).

---

## 00000000000000000000000000000000000000. Market data mkt03b — key dates, regimes, personal dates and saved views: API, seeds, loader; verify WRITTEN, not yet run (2026-10-07)

`mkt03b.structural`. What the mkt04 chart needs around the data: reference
key dates and regimes every member sees, a member's own dates and named
views, and platform starter views. Design is in
`docs/MARKET_DATA_DESIGN_V1.md`, decisions 20–24 and "Key dates, regimes and
saved views (mkt03b)". No DDL (Part 1, migration
`mkt03b_key_dates_regimes_views`, was applied before the sprint), no UI, no
Next.js route.

**Built:**
- `routers/market_data_personal.py`, mounted at `/api/v1/market` (registered
  in `main.py`):
  - `GET /key-dates`, `POST /key-dates/custom`, `DELETE /key-dates/custom/{id}`
  - `GET /views`, `POST /views`, `PUT /views/{id}`, `DELETE /views/{id}`
- `services/market_data/`:
  - `key_dates.py`: the GET body, personal-date create/delete, the live
    data range, kind and regime-type labels.
  - `saved_views.py`: the canonical config schema (`extra='forbid'` at every
    level), key resolution, the stale-selection `unavailable` list, CRUD.
  - `personal.py`: the shared envelope, name cleaning, the one 404, and a
    sanitiser that never names an undeclared field or repeats a
    discriminator value.
  - `reference_seed.py`: `load_reference(conn, key_dates=, regimes=, views=,
    dry_run=)` — takes rows; upserts inside `platform_scope()`.
- `scripts/market_data_seed_reference.py load [--dry-run]`.
- Seeds: `docs/market_data/market_key_dates_v1.json` (32),
  `market_regimes_v1.json` (16), `market_view_presets_v1.json` (4), copied
  verbatim from the sprint prompt.
- `scripts/verify_mkt03b.py`: Phase A always runs; Phase B runs with `--live`
  after the loader.

**Task 1 discovery (live, read-only):**
- Every CONFIRMED REAL FACT matched: the four tables, their columns,
  defaults, CHECK bodies, unique indexes (`uq_user_key_dates`,
  `uq_saved_views_user_name`, `uq_saved_views_platform_name`), policies,
  grants (app_service SELECT/INSERT/UPDATE/DELETE; anon/authenticated none)
  and RLS enabled. All four were empty.
- None of the four tables has a trigger, so `updated_at` is set by the code.
  The owner FKs (`org_id` → organizations, `user_id` → users) are NO ACTION,
  so teardown must delete personal rows before users and organizations.
- Identity: the org comes from `get_org_id(request)` (through the gate) and
  the user from `services.users.ensure_user(conn, request)` — exactly the pair
  `routers/notifications.py` (`_resolve_user`, preferences) and
  `routers/dashboard.py` (`patch_todo`, filters `user_id AND org_id`) use.
  `services.permissions.get_user_id` was NOT used: it is a uuid5 of the sub
  and does not match `users.id`, which both owner FKs need.
- mkt03 conventions mirrored: `require_market_data_read`
  (`services/market_data/access.py`), the envelope shape, raw-body parsing
  with `read_service._parse_body` and `MarketDataRequestError` so a 422 never
  echoes input. The data range reuses `read_repository.active_series` +
  `series_bounds`.
- Fixtures follow `verify_edgarcohorts.py` (fixed-UUID organizations and
  users with `auth0_sub`, httpx.ASGITransport, `main.verify_token` replaced
  with an `org_id` claim). RLS proofs run on DATABASE_URL, which is
  `app_service` (rolbypassrls = false, asserted).

**[FIND] The platform has THREE RLS session settings, not two.**
`app.current_auth0_sub` exists (RLS Phase 2). It is read by exactly one
policy, `users_bootstrap_and_org_visibility`, so a brand-new user can read and
insert their own row. It does not identify a `users.id`, and no other table
uses it. The prompt's decision stands: per-user privacy is enforced in the
service layer, and no new setting was added. Recorded as a next candidate in
the design doc.

**[FIND] The available data range starts in 1919, not 1970.** The earliest
first observation across ACTIVE series is 1919-01-01 (an early macro
macro series), and the latest last is the latest trading day (2026-10-07 at
discovery). The prompt's "1970-01-02" is the S&P 500's own first date. The
range is read per request, so the "outside the available data" message
currently reads "(Jan 1919 to Oct 2026)". All 32 key dates and 16 regimes
fall inside it.

**[FIND] `docs/market_data/KEY_DATES_SPEC_V1.md` is not in this
repository.** The prompt calls it the original spec. It was not created
(by rule); the design doc's new section is the record of what was built.

**[FIND] A 401 cannot prove a route exists here.** `auth0_jwt_middleware`
answers 401 before routing, so "every route refuses an anonymous caller"
passes even for a path that is not mounted. The verify's gate check sends the
IDENTICAL request with a session too, and requires a real route answer (not
401/405, not FastAPI's `{"detail": "Not Found"}`).

**Interpretations recorded (not in the prompt):**
- "Outside the available data month": a date is accepted from the first day
  of the first data month to the last day of the last data month.
- POSTs answer 201; PUT and DELETE answer 200.
- An unknown field anywhere is reported at its PARENT object
  (`["body", "config", "overlays"]`), never by its own name.
- A view's security key must be the canonical lowercase UUID. Malformed keys
  are counted, not echoed.
- The control-character rule also applies to view names.
- `end` is required in a view config and may be null (every preset sends
  it), so a config round-trips exactly.
- The loader also validates each preset against the view config schema;
  a preset that fails is skipped with a [FIND], like an unresolvable key.
- A write that cannot resolve a real `users` row (ensure_user's fallback id)
  answers 403 "Your account could not be resolved." instead of a 500.

**Not run by this sprint (by rule):** the loader, the verify, and every live
external call. What did run: read-only discovery queries; an offline check
that the four presets pass the config schema unchanged; an offline exercise
of the services against a fake connection (messages, range edges, 409, the
50th/51st, key resolution); route enumeration of the real app (all seven
routes mounted); a compile and undefined-name scan of every new module.

**OPERATOR ACTIONS:**
1. `market_data_seed_reference.py load` — expect 32 / 16 / 4 inserted and no
   [FIND].
2. `verify_mkt03b.py --live`.
3. Re-run `verify_mkt03.py --live`, `verify_mkt02.py --live` and
   `verify_mkt01.py --live` as regression baselines: 68/0, 52/0 and 57/0.
4. The owner verifies the eight Fed tightening dates against the FOMC record
   (design decision 24).

The commands are at the end of the sprint log.

**UPDATE 2026-10-07 — the front end has started (`mkt04a.structural`).** The
Next.js routes for these seven personal routes (and the four mkt03 read
routes), the `/market` page, the selection panel and the grid are built (see
the mkt04a entry above). Next: mkt04b (the chart) and mkt04c (key dates, saved
views and correlations — the first UI over this sprint's endpoints).

---

## 0000000000000000000000000000000000000. Market data mkt02c — long history: S&P 500 splice, credit spread, note underlyings, Yahoo units; verify WRITTEN, not yet run (2026-10-04)

`mkt02c.structural`. Closes the "not far enough back" gap that real data
exposed. The design is `docs/MARKET_DATA_DESIGN_V1.md` decision 19, plus
Launch blocker 4. No DDL: Part 1 (`mkt02c_observation_source_provider`, a
nullable, CHECKed `indicator_observations.source_provider`) was already
applied.

**Built:**
- `services/market_data/splice.py` + `scripts/market_data_splice_history.py`:
  splices `^GSPC` from Yahoo into `fred.sp500`, before F0 (the first
  NULL-provider row).
  - An overlap gate runs first: >= 99.0% of days within 0.02, no day over
    1.00, at least 20 days compared.
  - Insert-only. Rows are tagged `source_provider='yahoo'`. FRED rows are
    never touched.
  - `--dry-run` runs the gate and reports, without writing.
- `scripts/market_data_ingest.py load --seed <path>`: the default is still
  the v1 seed.
- `services/market_data/registry.py`: a link step (`link_security`). It sets
  `security_global_id` only when every one of these holds:
  - the id names a live `index` security with exactly the given name;
  - the series has no link yet;
  - no other series holds that security.
  Otherwise it prints a [FIND] and leaves the link alone. It never writes
  `portfolio.*`.
- `docs/market_data/market_indicator_registry_additions_v2.json`: six rows.
  - `fred.baa10y` (`unreviewed`).
  - `yahoo.dji`, `yahoo.ftse`, `yahoo.stoxx50e`, `yahoo.ssmi`, `yahoo.axjo`,
    each carrying its security link.
- `services/market_data/yahoo.py`: `meta.instrumentType == 'INDEX'` → units
  `'index points'`; otherwise the currency, as before.
- `scripts/verify_mkt02c.py`: Phase A (fixtures `verify.mkt02c.*`, fake
  transports) and Phase B (`--live`).

**Task 1 discovery (live, read-only):**
- Every CONFIRMED REAL FACT matched exactly:
  - 75 series, 63 active (57 fred / 6 yahoo), 313,156 active observations;
  - the `source_provider` column + CHECK, with 0 rows set and 4 policies;
  - the 3 links;
  - the 5 securities (ids, names, `index`, live, unlinked);
  - `fred.sp500` starts 2016-10-03 with 2,514 rows.
- **The write path never closes, deletes or touches an active row whose date
  is absent from a fetch.** `ingest.plan_writes` iterates only the fetched
  points. So the nightly cannot erase the splice. Proven by verify S6.

**[FIND]s — prior verify edits (Task 1d, minimal, nothing weakened):**
- `verify_mkt02.py` B1.
  - Before: BOTH latest nightly batches had to have per-series rows ==
    today's active count.
  - After: the LATEST batch == today's active count (runtime), and EACH
    batch's per-series rows == the `attempted=` count in its own summary
    row.
  - Why: after mkt02c the previous batch attempted 63 and today's count is
    69, so the old check would fail on correct data.
- `verify_mkt03.py` LINKS.
  - Before: a hardcoded dict of 3 links. C7 ("every other security is
    unselectable") would fail once the 5 new links exist.
  - After: `BASELINE_LINKS` (the original 3, still required) plus `LINKS`,
    read at runtime by `load_links()` (non-fixture, ACTIVE series with a
    security — the catalog's own predicate). C6, C7 and B1 use it.
- `verify_mkt03.py` B2a/B2b.
  - Before: grid end = `sp_first`, and the reference value = the first
    observation.
  - After the splice, `sp_first` falls before the 2000-03-10 anchor, so the
    old request would have been a 422 ("anchor after end").
  - After: the reference is the SQL as-of observation for 2000-03-10 when
    one exists, else the first observation. The end is the anchor, else
    `sp_first`. B2a now also checks `anchor_observation_date`.
- `verify_mkt01.py`: no change needed. Its seed checks read v1, and B6 is
  `>= 75`.

**Other [FIND]s:**
- `fred.sp500`'s notes are a loader definition field. A later v1 `load`
  removes the splice sentence. A splice re-run restores it, with no
  observation writes.
- If FRED ever returned a date before F0, the nightly would revise that
  Yahoo row into a FRED row. That is acceptable (FRED is the source of
  record), and F0 would move earlier.
- The gate needs at least 20 overlap days. The sprint did not specify a
  minimum; without one, zero overlap passes vacuously.

**Not run by this sprint (by rule):** the splice, the ingest, the nightly,
the verify, and any Yahoo or FRED call. Discovery was read-only SQL. Offline
only: the gate maths on the verify's own fixtures (pass / >1% / >1.00 / no
overlap), Yahoo units over a fake transport (INDEX / ETF / missing), and the
parser default. Every file compiles.

**OPERATOR ACTIONS** (in order; the full commands are at the end of the
sprint log):
1. `market_data_ingest.py load --seed docs/market_data/market_indicator_registry_additions_v2.json`
2. `validate` (activates `fred.baa10y`)
3. `backfill --series fred.baa10y`
4. `validate --provider yahoo` (activates the five, refreshes units)
5. `backfill --provider yahoo`
6. `market_data_splice_history.py --dry-run`, then without `--dry-run`
7. `market_data_nightly.py`
8. `verify_mkt02c.py --live`, then `verify_mkt03`, `verify_mkt02` and
   `verify_mkt01` with `--live`.

**UPDATE 2026-10-04 — splice gate rule changed (`mkt02c2.structural`).**
Supersedes the gate rule in "Built" above.
- **Why.** The operator's live dry run (steps 1–5 already done) REFUSED,
  correctly applying the rule as written. 2,514 overlap days, 99.76% within
  0.02. But two isolated days broke the absolute "no day over 1.00" limit:
  2021-08-11 (diff 5.29, about 0.12%) and 2019-08-12 (1.05, about 0.04%).
  These are single-day disagreements between two feeds of the SAME index,
  not a different or mis-scaled series. An absolute 1.00 is far too tight
  for an index near 5,000.
- **New rule** (`services/market_data/splice.py`, one constant each): passes
  only if (1) at least 20 overlap days; (2) at least 99.0% within 0.02,
  unchanged and still the main guard against a wrong series; (3) no day off
  by more than 0.5% of the series' own value. Rule 3 replaces the 1.00
  limit. A zero series value fails the gate with a message, never raises.
- **Output.** The script prints the three rules, the count of days beyond
  0.02 ("tolerated" on a pass), and the worst ten as [FIND] whether it
  passes or fails. The gate line names the failed rule(s) and, for rule 3,
  the offending day.
- **Unchanged.** The splice still never writes, revises or closes a row
  dated on or after F0. On the two disagreeing days `fred.sp500` keeps its
  FRED value. Recorded as a data-quality note in
  `docs/MARKET_DATA_DESIGN_V1.md` decision 19.
- **Verify.** `verify_mkt02c.py` S3a/S3b/S3c/S3d now use a ~262-day gate
  fixture. A single bad day fails rule 2 on the old 42-day overlap, so
  rule 3 could not be proven alone there. S3b now proves rule 3 (one day
  ~0.62% off; rule 2 holds). New focused `verify_mkt02c2.py`
  (`verify.mkt02c2.*` fixtures) re-proves only the gate: S3a–S3e (S3e = the
  live shape passes, both days listed, own rows unchanged), the constants,
  the zero value, and dry-run parity.
- **Not run by this sprint (by rule):** the splice, the nightly, and any
  verify. Only offline checks ran: the gate function on in-memory data and
  a compile of every file.
- **Operator next:** splice `--dry-run`, then the splice, then the nightly
  (twice), then `verify_mkt02c2`, then `verify_mkt02c --live` and the
  mkt03/02/01 live verifies. Do NOT repeat load/validate/backfill.

---

## 000000000000000000000000000000000000. Market data mkt03 — read API: catalog, series, grid, correlations; verify WRITTEN, not yet run (2026-10-03)

`mkt03.structural`. This is the READ side the mkt04 chart and grid will sit
on. It writes no market data, adds no tables, and runs no DDL. Decisions,
the API contract, the transform definitions and the correlation method are
in `docs/MARKET_DATA_DESIGN_V1.md` (decisions 14–18, "API contract",
"Transform definitions", "Correlation method", "Caveats").

**Built:**
- `routers/market_data.py`, mounted at `/api/v1/market`:
  - `GET /catalog`
  - `GET /series`
  - `POST /grid`
  - `POST /correlations`
- `services/market_data/`:
  - `read_repository.py`: the read queries, with one observation query per
    call over `series_id = ANY($1)`.
  - `transforms.py`, `resample.py`, `correlation.py`, `palette.py`: pure
    functions, no database.
  - `access.py`: THE gate, plus the envelope.
  - `read_service.py`: the endpoints' logic and request parsing.
- `scripts/verify_mkt03.py`: Phase A always runs; Phase B runs with `--live`.

**Task 1 discovery (live, read-only):**
- Every CONFIRMED REAL FACT matched:
  - 63 active series (57 fred, 6 yahoo) and 313,156 active observations.
  - `uq_indicator_series_security` is present, with its 3 links.
  - 13 index securities, 3 of them linked, plus 54 structured notes.
  - `securities_global` has 67 rows and `securities_global_prices` has 0.
- The largest series is fred.dff, with 26,391 active observations. That
  sets the per-series point cap at 30,000. The per-request total cap is
  300,000.
- The pattern mirrored is `routers/model_research.py`: the org from
  `get_org_id(request)`, and an envelope with `can_write: false`.
- The verify follows `verify_portfolioux3`/`verify_modelresearch`: one shared
  TestClient, `main.verify_token` replaced, and `REGISTRY.sync_catalog`
  no-op'd.

**[FIND] No existing permission means "may read market data".**
- The candidates were `view_dashboard` and `view_portfolio`. Neither is
  about platform reference data, so none was invented.
- The gate is a valid, active session, and `read_permission` is `null`.
- To add a permission later, set
  `access.MARKET_DATA_READ_PERMISSION`. That is one line.

**[FIND] fred.sp500 starts on 2016-10-03.** FRED carries only about 10
years of S&P 500.
- An anchor of 2000-03-10 therefore FLOATS: v0 is the first observation.
- Phase B checks "100 at the anchor" at that first observation. It also
  proves a real, non-floating anchor on fred.nasdaq100, whose history
  starts in 1986.

**[FIND] Licensing is exposed, not enforced.**
- 15 active series are `third_party_licensed` (10 FRED, 5 Yahoo) and 5 are
  `unreviewed`.
- The API returns `license_class` and `source_provider` on each one
  (launch blocker 2, met at the API level). It does NOT filter them, and any
  authenticated session can read them.
- mkt04 must gate them per tenant before any tenant UI ships (design
  decisions 4 and 16).

**[FIND] FastAPI's default 422 echoes the caller's input.** It includes
pydantic's `input` field. So these routes read raw bodies and validate in
the service. A refusal names the field and the rule, never the value, and
never the name of an undeclared field such as `org_id`.

**[FIND] The palette's clamp is applied to the ENDS of the lightness range,
not to each shade.** Clamping each shade separately would put most of the 54
gold notes on the same colour, which breaks "distinct within a category".
See design decision 18.

**Interpretations recorded (not in the prompt):**
- The newest grid row is `end` itself when `end` is not a period end
  (`is_period_end: false`).
- Daily grid rows are weekdays.
- A lag on a daily or weekly correlation pair promotes the pair to monthly.
- A quarterly pair needs a lag that is a multiple of 3.

**Not run by this sprint (by rule):** the verify, and every live external
call. What did run:
- Read-only discovery queries.
- The pure modules, offline.
- An offline dry run: the production service functions, with the
  repository patched to in-memory fixture data, scored by the verify's own
  assertion functions. 42/42.
- The verify's static AST check. 1/1.

**OPERATOR ACTIONS:**
1. `verify_mkt03.py --live`.
2. Re-run `verify_mkt02.py --live` and `verify_mkt01.py --live` as
   regression baselines: 47/0 and 57/0.

The commands are at the end of the sprint log.

UPDATE 2026-10-03: final verify results — `verify_mkt03.py --live` 68 passed,
`verify_mkt02.py --live` 47 passed, `verify_mkt01.py --live` 57 passed, all 0
failed. "verify WRITTEN, not yet run" above is superseded.

UPDATE 2026-10-07: mkt03b (key dates, regimes, personal dates, saved views —
see the top entry) is built on these routes. The remaining plan is mkt04: the
chart and grid UI, the Next.js routes in front of this API, and the
per-tenant gating of licensed, unreviewed and Yahoo-sourced series.

---

## 00000000000000000000000000000000000. Market data mkt02 — nightly refresh: adapter registry, Yahoo adapter, orchestrator, staleness, Render cron entrypoint; verify WRITTEN, not yet run; NO Render service created (2026-10-03)

`mkt02.structural`. This makes the mkt01 data stay current. Decisions are in
`docs/MARKET_DATA_DESIGN_V1.md` §8–13 and "Launch blockers". Operator setup
is in `docs/market_data/RENDER_CRON_SETUP.md`.

**Discovery (Task 1): the live state matched the sprint's confirmed facts
exactly. No `[FIND]`, no stop.** Checked:
- `batch_id uuid` and `idx_indicator_runs_batch` exist.
- Registry: 57 fred/active; 12 deferred (yahoo 6, shiller, worldbank, imf,
  bis, oecd, none); 6 none/deferred_paid.
- 265,737 active observations, which is also the total: there is no history
  row yet.
- 114 runs rows, all with `batch_id` NULL.
- Policies, grants and CHECKs are unchanged. `run_trigger` already allows
  `nightly` and `manual`. `app_service` has `rolbypassrls = false`.
- Untouched tables: `securities_global` 67, `securities_global_prices` 0,
  `fx_rates` 5.
- The six Yahoo rows: `source_code` is the ticker, `deferred`, `daily`,
  `default_transform = rebase_100`. `license_class` is `third_party_licensed`
  for five of them and `unreviewed` for GC=F.

**Built:**
- `services/market_data/adapters.py`: the registry (`fred`, `yahoo`),
  `FredAdapter`, and `scrub_error`.
- `yahoo.py`: Decimal-only parsing, 4dp ROUND_HALF_EVEN, raw close,
  exchange-local dates, today's bar dropped. Only the explicit "Not Found"
  error sets `invalid_code`.
- `nightly.py`: `run_nightly(conn, registry, series_selection=None,
  trigger='nightly')`.
- `staleness.py`.
- `scripts/market_data_nightly.py`: the cron entrypoint. Exits 0, 1, or 2
  (missing variable).
- `market_data_ingest.py validate|backfill --provider <name>`, default
  `fred`.
- `scripts/verify_mkt02.py`.
- One new alert kind in `services/workflow_todos.py`. No DDL. `render.yaml`
  untouched.

**Decisions made in the build, worth knowing:**
- **mkt01's `backfill` selected every active series regardless of
  provider.** That was harmless with only FRED active. The moment Yahoo goes
  active, the default (FRED) backfill would have sent `^RUT` to FRED. It now
  filters on `--provider`.
- **The write path is mkt01's, made race-safe in place.**
  `write_observations` = read → plan → apply.
  - The close is an id-ordered `FOR UPDATE` plus an `UPDATE … RETURNING id`
    that repeats the active predicate. A row it did not close was closed by a
    concurrent run, so that point is NOT inserted and counts as unchanged.
  - The insert is `ON CONFLICT … DO NOTHING` on `uq_indicator_obs_point`
    (confirmed as the arbiter with EXPLAIN).
  - Zero rows can only mean "lost the race" because the function first
    refuses to run unless `app.is_super_admin = 'true'` in the transaction.
    Without that check, zero rows could equally be RLS silently refusing the
    UPDATE (CLAUDE.md "row not found").
  - mkt01 used to raise on a short close; it now counts it unchanged.
- **The batch summary row has no columns for series counts.** No DDL was
  allowed, so they go in its `error` text:
  `batch summary: attempted=… succeeded=… partial=… failed=…
  skipped_no_adapter=…[; failed: keys]`. The `rows_*` columns carry the
  batch's row totals.
- **Yahoo "maximum range" is `period1=0…now`, not `range=max`.** `range=max`
  can silently coarsen the granularity, and the parser refuses anything but
  `1d`.
- **Yahoo 401 and 403 are transient,** as the sprint says. A bare 404
  without Yahoo's "Not Found" error body is also not treated as not-found.
- **Run timestamps come from the database clock.** `started_at` is
  `clock_timestamp()` at the start of each series, and `finished_at` is
  `clock_timestamp()` at the write. mkt01's rows had `started_at =
  finished_at` (both were the transaction's `now()`), and a laptop clock
  would skew against the server.
- **Errors are scrubbed by the orchestrator itself,** not just by the
  adapter. `scrub_error` removes any set `FRED_API_KEY` / `DATABASE_URL` /
  `DB_PASSWORD` value, every URL's userinfo and query string, and FRED's
  `api_key=` fragment. The verify proves this with a fake adapter whose
  scrub is the identity.
- **Alerting (Task 1g):** an existing mechanism fits. It is
  `services/workflow_todos.py`, the same platform-level path as the AI
  platform-ceiling alerts: `member_todos` for the Hollisworks org's
  `manage_org_settings` holders, with an `audit_log` row when there are none.
  - One alert is raised per batch with failures.
  - That org has had zero holders, so expect `audit_log` rows
    (`market_data_batch_failed_alert_undelivered`) until someone holds the
    permission.
  - Exit code 1 is the primary signal.
- The staleness report flags a NULL `last_observation_date` on an active
  series even for `per_meeting` frequency. "Always flagged" was taken
  literally.

**OPERATOR ACTIONS, in order:**
1. `market_data_ingest.py validate --provider yahoo`, then
   `backfill --provider yahoo`. This makes the six Yahoo series active.
2. `market_data_nightly.py` twice, locally.
3. `verify_mkt02.py --live`, then re-run `verify_mkt01.py --live`. Its
   Phase B B4 now also covers the active Yahoo series: units and a backfill
   success row. The commands are at the end of the sprint log.
4. Create the Render Cron Job from `docs/market_data/RENDER_CRON_SETUP.md`.
   It needs its own Doppler sync, and no variable set by hand. Then trigger
   one manual run.
   - Yahoo may be blocked from Render's IPs. That fails only those series,
     but makes every night exit 1 until they are paused.
5. Optional: declare the new cron service in `render.yaml` (that manifest's
   own invariant). This sprint was told not to edit it.

**Launch blocker (recorded, not resolved):** the six Yahoo series run under
the owner's personal/internal-use assumption. Before any external customer
sees the platform:
- they must be replaced with a licensed source, or removed;
- mkt03's API must expose `source_provider` so they can be gated per tenant.

**Not run by this sprint (by rule):** the nightly, the verify, and any
Yahoo or FRED call. Discovery was read-only. The new write SQL was checked
with plain `EXPLAIN`, which does not execute. Pure parsing, encoding and
classification were smoke-tested offline against fake and mock transports.

**UPDATE 2026-10-03 (mkt03.structural):** the operator ran the verifies.
`verify_mkt02.py --live` = 47 passed, 0 failed; `verify_mkt01.py --live` =
57 passed, 0 failed. The last two nightly batches each succeeded on all 63
active series, with 0 revisions. Part 1 of mkt03 linked fred.sp500,
fred.nasdaq100 and yahoo.rut to their index securities.

The remaining plan:
- **mkt03** = the read API (catalog, series, grid, correlations). See the
  entry above.
- **mkt03b** = key dates and saved views.
- **mkt04** = the chart and grid UI. It also gates licensed and Yahoo series
  per tenant.

UPDATE 2026-10-03: final verify results — `verify_mkt03.py --live` 68 passed,
`verify_mkt02.py --live` 47 passed, `verify_mkt01.py --live` 57 passed, all 0
failed. "verify WRITTEN, not yet run" above is superseded.

---

## 0000000000000000000000000000000000. EDGAR cohorts + template study — frozen cohorts, cohort-targeted fetch, --cohort for B1, inventory pass; verify WRITTEN, not yet run (2026-10-03)

`edgarcohorts.structural`. Choosing what to run moved to the Filings tab; the
schema for extraction will be settled from an inventory of real filings, not
one surprise at a time. No extraction schema change in this sprint.

**Schema (applied via MCP, each part verified by a follow-up query):**
`migrations/edgarcohorts_cohorts_inventory.sql` — `portfolio.edgar_cohorts`
(name, purpose, kind, definition JSON, member_count 1..50,000, copied_from,
sealed_at) and `portfolio.edgar_cohort_members` (PK cohort_id + accession —
deduplicated by accession; position; stratum). FROZEN by trigger: the only
permitted cohort UPDATE is sealing it (in the same transaction that inserts
its members); after that every UPDATE of the cohort and every INSERT / UPDATE /
DELETE of a member is refused. `edgar_pipeline_runs.cohort_id` + `run_kind`
('fetch' | 'extract'), `edgar_index_filings.selected_by_cohort_id`, and the
inventory tables `edgar_inventory_runs / _documents / _items / _concepts`.
Four RLS policies on every new table (global read; super-admin writes).
**The MCP cancelled the migration as one script** (it contains DROP
CONSTRAINT / DROP TRIGGER); it was applied in five parts, the constraint swap
on its own. The file is the full, idempotent definition.

**Cohort selection vs policy selection.** The lifecycle CHECK
`edgar_index_filings_decided_has_policy_chk` demanded a policy version on
every decided row; it now accepts `selected_by_cohort_id` instead, and a new
CHECK forbids both at once. A cohort run marks a member the policy left
'discovered' or 'not_selected' (e.g. an FWP) as selected by that cohort and
CLEARS its policy version — never mistaken for a policy decision. A member the
policy selected keeps its policy provenance.

**Code.** `services/edgar_cohorts.py` (definitions; sampling all / newest N /
oldest N / random N seeded / stratified N per issuer group x year or era
2019-2021, 2022-2023, 2024-2026; preview; frozen save; copy-and-edit; reads;
launch). Seeded order is `hashtextextended(accession, seed)` — md5 measured 2x
slower; the stratified sort sets `work_mem` LOCAL (it spilled 40MB to disk).
The template-study preset previewed on live data: 417 members in ~4s.
`services/edgar_pipeline.py` — cohort runs: fetch stage walks the cohort in
position order, skips members already past fetch, retries failed-not-given-up
members, same lease / rate limit / runtime cap; 'extract' is refused
(`RunKindNotAvailable`, reserved for B2). `edgar_pipeline_job.py --cli
--cohort`. Routes under `/admin/edgar/cohorts...` and
`/admin/edgar/inventory/{run_id}` (super-admin; PATCH/PUT on a cohort is always
409). Web: Filings tab ticks (kept by accession, survive paging) + cohort
builder with preview; Cohorts tab (definition, members by status, strata,
runs, "Fetch this cohort", copy-and-edit, template-study preset, inventory
concepts grid + per-issuer label dictionary); the Progress button is now
"Run default policy now (newest first)" (label from the envelope).
B1: `run_note_extraction_pilot.py`, `run_note_extraction_eval.py` and
`sample_gold_set.py` take `--cohort <id>` (exactly the cohort's
ready_for_extraction members, in cohort order — `selection.cohort_notes`).

**Inventory pass** — `services/edgar_inventory.py` +
`scripts/run_edgar_inventory.py --cohort <id> --spend-cap X [--dry-run]`.
Terms pages only (start through payout examples; stops before risk factors,
index methodology, licence text, tax). 4-6 pricing supplements per issuer
chosen greedily for product-family variety, plus 1-2 product supplements.
Every quote is checked against the filing text; a quote that is not found
rejects the item (recorded and counted). Concepts by normalised label then ONE
model-assisted grouping call. Output: the grid, `docs/TEMPLATE_STUDY.md`
(`--write-doc <run>`), the per-issuer label dictionary. Calls only through
`services.note_extraction.proxy` (no fallbacks; provider-reported model
recorded). **BLOCKED today:** `platform_model_catalog` has NO available
non-Claude chat model (only claude-haiku, claude-sonnet, voyage-3.5), so a real
run reports BLOCKED until one is registered there and served by the proxy.

**Not done here (operator):** fetch the template-study cohort, register a
non-Claude model in `platform_model_catalog`, then run the inventory pass —
dry run first. Schema snapshot not regenerated in this session.

**Verification:** `apps/api/scripts/verify_edgarcohorts.py`, WRITTEN, NOT RUN
(per the sprint). Fixtures: accessions `9999999999-58-*`, CIKs
9999999581..3, cohorts named `VERIFY edgarcohorts*`. No Render launch, no
SEC/R2 traffic, all model responses mocked — $0. `npm run build` exited 0 in
this session. Run: `python3 apps/api/scripts/verify_edgarcohorts.py`.

---

## 000000000000000000000000000000000. Market data mkt01 — indicator registry, FRED adapter, historical backfill; ingest + verify WRITTEN, not yet run (2026-10-03)

`mkt01.structural`. Platform-global reference data in schema `market_data`
(no org_id; deliberately separate from `portfolio.securities_global_prices`).
Decisions: `docs/MARKET_DATA_DESIGN_V1.md`.

**Schema:** Part 1 DDL (`mkt01_market_data_schema`) was already live. Task 1
re-checked it against the live database (columns, CHECK bodies via
`pg_get_constraintdef`, `pg_policy`, grants, indexes, RLS flags): **it matches
the sprint's confirmed facts exactly; no drift.** All three tables were empty
at discovery; `securities_global` = 67, `securities_global_prices` = 0,
`fx_rates` = 5.

**Code:** `apps/api/services/market_data/` (`registry.py`, `fred.py`,
`ingest.py`) and the operator script `apps/api/scripts/market_data_ingest.py`
(`load | validate | backfill | all [--series KEY]`). All writes run in
`platform_scope()`; Decimal end to end. httpx is the HTTP client (already
declared; no new dependency).

**OPERATOR ACTION 1 — `FRED_API_KEY` is ABSENT** from the Doppler config this
environment's token reads (checked names only, 2026-10-03). Add it to
`hollisworks` `prd` (and `dev` if used), then run
`doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py all`.
The script exits with one line if the key is missing.

**OPERATOR ACTION 2 — run the verify after the ingest:**
`doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py --live`.
Phase A (fixtures `verify.mkt01.*` + a fake FRED, no network) always runs;
Phase B (`--live`) checks the real data and spot-checks DGS10 / UNRATE /
CPIAUCSL against FRED. Any `invalid_code` series prints as `[FIND]` with its
error: those are the seed's from-memory FRED codes that FRED rejected, and
each needs a corrected `source_code` in the seed file.

**Decisions made in the build, worth knowing:**
- **The loader keeps a validated FRED row's frequency.** The sprint lists
  `frequency` among the loader-owned definition fields, but it also makes FRED
  authoritative. Taken literally, `load` writes the seed value back and
  `validate` corrects it again on every `all` run, with a fresh `[FIND]` and an
  "updated" count each time. So once `last_validated_at` is set on a FRED row,
  the loader leaves frequency alone. Fix the seed file to clear the `[FIND]`.
  Proven by verify A5.5.
- **A 400 counts as "series does not exist" ONLY when FRED's message says
  so (or the response is a 404).** FRED returns 400 for a bad API key too.
  Without this rule, a key typo would mark all 57 series `invalid_code`.
  Proven by verify A5.9.
- **A point that disappears from FRED is left in place.** The sprint defines
  no semantics for deleting observations, and silently deleting history is
  the worse failure.
- `backfill` clears `last_error` even on a `partial` run, as the spec says.
  The rejected values are recorded in that run's `indicator_ingest_runs.error`.
- Phase B's B4 requires a *backfill* `success` run for every active series. A
  series whose only backfill was `partial` (FRED sent a non-numeric value)
  FAILS B4 on purpose, so a human sees it.

**Not run by this sprint (by rule):** the ingest and the verify. Read-only
discovery queries ran against the live database as `app_service`; every write
statement was PREPAREd (parsed and type-checked, never executed) inside a
rolled-back transaction. Nothing called FRED.

**Next:** mkt02 (nightly Render cron + non-FRED adapters), mkt03 (API, chart,
grid, sec-master links for the six benchmark series; the licensing question
for `third_party_licensed` series must be answered first).

**UPDATE 2026-10-03 (mkt02.structural):** the operator ran the verify:
`verify_mkt01.py --live` = 57 passed, 0 failed. All 57 FRED codes are valid;
265,737 active observations. The planned scope "mkt02 = nightly cron +
non-FRED adapters" was **split**:
- **mkt02** = the nightly refresh plus the **Yahoo** adapter (6 series).
- The other non-FRED adapters (**Shiller, World Bank, IMF, BIS, OECD**) moved
  to **mkt02b**, after the cron is proven live.
- **EIA was dropped.** FRED already covers WTI, Brent and Henry Hub.

See the mkt02 entry above.

**UPDATE 2026-10-03 (mkt03.structural):** mkt01 and mkt02 are verified
live, with 57/0 and 47/0. The remaining plan:
- **mkt03** = the read API.
- **mkt03b** = key dates and saved views.
- **mkt04** = the chart and grid UI.

See the mkt03 entry at the top.


UPDATE 2026-10-03: final verify results — `verify_mkt03.py --live` 68 passed,
`verify_mkt02.py --live` 47 passed, `verify_mkt01.py --live` 57 passed, all 0
failed. "verify WRITTEN, not yet run" above is superseded.

---

## 00000000000000000000000000000000. Note extraction B1 — engine, gold set, evaluation harness, pilot runner; results STAGED; verify WRITTEN, not yet run (2026-10-02)

`noteextractb1.structural`. Nothing writes to `securities_global` or
`securities_global_note_terms` — B2 promotes. Neither the evaluation nor the
pilot has been run (both built with `--dry-run` and a required `--spend-cap`).

**Live schema (applied via MCP, verified with follow-up queries; four RLS
policies each — global read, super-admin writes):**
`portfolio.note_extraction_runs`, `note_term_readings` (append-only: every
value any source produced, with deployment, provider-reported model, proxy
deployment id, ensemble config, prompt version + prefix hash, quote, raw-HTML
offsets, tokens, cost, latency; one `__call__` row per provider call carries
the cost so SUM(cost_usd) = real spend), `note_extraction_staging` +
`note_extraction_staged_fields` (resolved values per note), `note_gold_values`
(bi-temporal; a BEFORE trigger refuses any write without the transaction-local
`app.gold_reviewer_id` matching `reviewer_id` — only humans write gold),
`note_gold_candidates`, `distribution_participants`.
Files: `migrations/noteextractb1_readings_staging_gold.sql`.

**OPERATOR ACTION 1 — the 29 disagreements are NOT moved yet.**
`migrations/noteextractb1_move_disagreements.sql` (atomic: 58 readings, a
count proof, then the delete) was CANCELLED by the MCP tool, which refuses a
DELETE non-interactively. Run
`python3 apps/api/scripts/move_note_terms_disagreements.py --apply`
(dry-run verified: 29 found). The pre-move state is frozen in
`scripts/fixtures/noteextractb1_disagreements_premove.json`; the verify's
"29 disagreements" section fails until this runs.

**OPERATOR ACTION 2 — all 7 candidate models are BLOCKED on keys.**
`prd_lite_llm` has no `DEEPINFRA_API_KEY` (gpt-oss-120b, Mistral-Small-3.2-24B,
DeepSeek-V3.2, Qwen2.5-7B), `GEMINI_API_KEY` (gemini-2.5-flash-lite) or
`OPENAI_API_KEY` (gpt-5-nano, gpt-5-mini). Add them, confirm `prd_lite_llm`
syncs to the proxy's Render service, then
`python3 apps/api/scripts/register_note_extraction_models.py --apply` registers
each deployment (`os.environ/NAME`, never a value) and adds it to
`platform_model_catalog` as 'available' ONLY after a real call succeeds.

**Engine:** `apps/api/services/note_extraction/` — rules (label-anchored,
CUSIP check digit, dates, estimated value, price/fee table, fee-based price —
ranges refused, participant names), EdgarTools 5.59.1 (MIT, pinned) on the
STORED HTML with sockets/DNS blocked and counted, heading-rule trimming (PoD
kept in full), two readers via `proxy.py` (ONE chokepoint: `disable_fallbacks`
+ the raw-model metadata flag; provider model must match the deployment),
compare-in-code, one Jev call per note with general criteria, escalation,
needs_review for unresolved critical fields, the hard per-run spend cap
(reserve-before-call, settle from recorded cost; an interrupted note's calls
are still recorded). Schema generated from `note_terms_field_registry` plus
the B1 extensions; `ai_ensembles.KNOWN_TASK_KEYS` gains `note_terms_extraction`.

**Screens:** `/admin/note-extraction/gold` (gold review), `/admin/note-
extraction/results` (results grid); router `routers/note_extraction_admin.py`.

**Scripts:** `sample_gold_set.py` (dry run: 74 eligible notes, ALL 2025, 10
issuers, every trap kind present), `run_note_extraction_eval.py`,
`run_note_extraction_pilot.py` (needs an ACTIVE `note_terms_extraction`
ensemble — none exists), `seed_distribution_participants.py`.

**Findings:**
- ZERO manifest rows are `ready_for_extraction`; the 200 `fetched` rows are
  the 2025Q1 corpus, never classified. Runs take `--include-corpus`, applying
  sprint A's own `decide_status` in memory (manifest untouched). A ~2,000-note
  pilot is not possible until the nightly job fetches more.
- LiteLLM 1.96.2 REWRITES the response `model` to the requested alias; only the
  internal `_complexity_router_return_raw_model_name` metadata flag returns the
  provider's model. It also ACCEPTS a raw upstream id (the "400 on raw ids"
  premise is false).
- `services.extraction.call_claude_json` walks the app fallback chain, so it
  cannot serve an ensemble slot; ensemble calls go through
  `services/note_extraction/proxy.py` instead (still proxy-only, no SDK).
- The payoff DSL's `protection_type` vocabulary (buffer/floor/none) has no
  'barrier' and its 'floor' is not a barrier; the readers use
  full/buffer/barrier/none. B2 must map before promoting.
- `pip install edgartools` pulled pandas 3.0.6 into apps/api/venv
  (requirements say `pandas>=2.0`); watch the scheduler/pricing code on deploy.

**Verification:** `apps/api/scripts/verify_noteextractb1.py`, WRITTEN, NOT RUN
by the sprint. Mocked providers except one ~16-token claude-haiku call and one
call to a nonexistent deployment (< $0.01). Run after OPERATOR ACTION 1:
`python3 apps/api/scripts/verify_noteextractb1.py`.

---

## 0000000000000000000000000000000. EDGAR pipeline A — lifecycle, selection, discovery, fetch-to-R2, nightly job, monitoring; verify WRITTEN, not yet run (2026-10-02)

`edgarpipelinea.structural`. No model is called anywhere; term extraction is sprint B.

**Schema (applied via MCP, recorded in `apps/api/migrations/`):**
- `edgarindex_discovery_tables.sql` — the four objects created by hand before this
  sprint (`edgar_index_filings`, `edgar_index_filing_filers`, `structured_note_issuers`,
  `v_edgar_filings_explorer`), generated from the live catalog. The repo had no file for them.
- `edgarpipelinea_lifecycle.sql` — the FULL status CHECK (sprint B's statuses
  included); `document_kind`, `selection_policy_version`, `attempt_count`,
  `last/next_attempt_at`, `reference_filing_id`, `detected_cusip`, `fetched_at`,
  `primary_issuer_cik` (function `edgar_primary_issuer_cik` + statement triggers on
  the issuer table that recompute it on ANY change); `edgar_selection_policies`
  (versioned, retire-only trigger, one active); `edgar_pipeline_runs`;
  `edgar_pipeline_lease`; gzip columns on `reference_filings`; sort indexes; four
  RLS policies on every new table.
- DATA: policy v1; `primary_issuer_cik` backfilled (690,324 of 716,953; 537,910 of
  546,013 424B2s — exactly the stated coverage); the 200 pre-existing documents
  linked and marked `fetched` without re-download (201 minus the fixture); the
  `VERIFY FIXTURE` row deleted (nothing referenced it; it had no R2 objects).

**Code:** `services/edgar_index.py` (parser + loader, now shared with
`scripts/load_edgar_index.py`), `services/edgar_pipeline.py` (discovery over the
daily index, selection, fetch, classification, CUSIP check digit, lease, runs, Render
launch, the job), `edgar_pipeline_job.py` (Render job entrypoint),
`services/assistant_actions/edgar_ops.py` (`edgar.launch_pipeline_job`, tier 2),
`services/edgar_pipeline_admin.py` + `routers/edgar_pipeline_admin.py` (super-admin
API), `/admin/edgar-pipeline` (Filings / Progress / Issuers), and `DataGrid`'s new
opt-in `serverSide` mode. `edgar_fetch.py` gained only `set_rate_limit()`.

**Decisions made inside the sprint:**
- New `reference_filings` rows are `extraction_status = 'fetched'`, `extracted_text`
  NULL, text gzipped in R2 at `text_r2_key`. Not `'extracted'`: the existing
  note-terms script selects that status and reads `extracted_text`.
- The offset map is NOT uploaded for new rows (bandwidth); it is deterministic from
  the raw HTML with the same extractor, so sprint B can recompute it.
- The launch step NEVER raises: a raised Service Task HOLDs the run, `held` is
  non-terminal, and the scheduler would then skip every following night. Refused /
  failed launches are recorded on the run row instead.
- Issuer group is a filter, not a sort key (no index can sort 717K rows by a joined
  name; measured 4 s). Category sorts break ties in the index's mirror order.
- An issuer edit that changes `include_status`, or a new issuer, sends that
  issuer's still-undecided (`selected`/`not_selected`) filings back to `discovered`
  so the next job re-selects them. Fetched rows are never touched.
- Default fetch cap 5,000 per job (`EDGAR_PIPELINE_FETCH_CAP`), runtime cap 4 h,
  8 requests/s, retries at 6/12/24/48 h then give up, stop after 25 consecutive
  failures. Base service for the one-off job: `2ndactcapital-workflow-scheduler`
  (paid instance, apps/api, Doppler-synced; the API web service is on the free plan).

**OPEN — needs Joe:**
1. **`R2_BUCKET_NAME` in Doppler `prd` is `'docs_readwrite_hollisworks'` — not a legal
   bucket name** (it looks like a token name). The EDGAR corpus lives in
   `hollisworks-docs`. `services/storage.py` uses this value as its DEFAULT bucket, so
   every default-bucket R2 call on any service synced from Doppler should currently be
   failing with InvalidBucketName — check document uploads. The pipeline refuses to
   start with it (loud config error); set it to `hollisworks-docs` (or set
   `EDGAR_R2_BUCKET`). The verify reports this as a FAIL and then runs its fetch
   tests against the bucket that actually holds the corpus.
2. The nightly trigger (`0 6 * * *` UTC, Hollisworks platform org) was created
   **inactive**: the deployed build does not register `edgar.launch_pipeline_job`, and
   the engine resolves an unknown key to a silent no-op step. Activate it on
   /admin/workflows/triggers after deploy.
3. The first job selects the whole manifest (~717K rows, one quarter per
   transaction) and then fetches up to the cap. At 5,000/night the ~538K selected
   backlog takes ~110 nights; raise the cap for the first runs if wanted (watch the
   Render bandwidth allowance — gzipped raw + text is roughly 1/5 of the HTML).
4. `edgar_index_filings_form_date_idx` is now a redundant prefix of the new
   `form_sort_idx`; it stays because the MCP apply path cancels any DROP.

**Verification:** `apps/api/scripts/verify_edgarpipelinea.py`, WRITTEN, NOT RUN
(operator runs it). It makes real calls: ~40 SEC requests, R2 writes under
`reference/edgar-verify-edgarpipelinea/` (deleted in teardown), and ONE Render
one-off job launched through the real nightly workflow with `stages=[]`,
`fetch_cap=0`. `npm run build` exited 0 during the sprint.

---

## 000000000000000000000000000000. Model Research page — read-only grid of every model LiteLLM prices; verify WRITTEN, not yet run (2026-10-01)

`modelresearch.structural`. An admin page that lists every model LiteLLM has
a price for, flagged against what this platform runs. It is read-only: the
endpoint and the page write nothing, and Refresh only re-reads the sources.

**Where it lives.** `GET /api/v1/admin/model-research[?refresh=true]`
(`routers/model_research.py`, `services/model_research.py`) →
`/api/admin/model-research` (Next forward) → `/admin/model-research`
(`components/admin/ModelResearchGrid.jsx`, on the shared `DataGrid`). Menu:
sidebar + `/admin` index, from `lib/menuVisibility.mjs`.

**Access.** Super admin, or `manage_org_settings` in the caller's own org, via
`services.rbac.can_manage_org_settings` (super-admin first). A member gets
**403** from the API. The menu entry uses a new **strict** gate,
`GATE_MANAGE_ORG_SETTINGS_STRICT` / `canPermStrict`. It fails CLOSED: no
envelope, a malformed one, or the `usePermissions` fallback all hide the
entry. The response carries the standard envelope: `can_write: false`, and
`editable` / `inline_editable` as empty arrays.

**Task 1 findings (probed live on the pinned v1.96.2 proxy, 2026-10-01):**
- **1a — the proxy CAN return the full price list.**
  `GET /public/litellm_model_cost_map` returns 4,455 keys (4,451 models). That
  is more than double the "roughly two thousand" the prompt assumed. So the
  proxy is the source, and the GitHub fallback is only used when the proxy
  fails. `/model/info`, `/v2/model/info`, `/model_group/info` and
  `/v1/models` return only the 3 registered deployments, and
  `/public/model_hub` returns `[]`. `GET /model/cost_map/source` reports
  `source: remote` (GitHub main), `model_count: 4455`, no fallback. Two side
  findings:
  - The cost-map route needs **no key**. Besides public price data, it
    exposes one `{id, db_model, blocked}` stub per registered deployment UUID.
    It holds no credential, but the deployment ids are readable
    unauthenticated.
  - Those 3 stubs are not models. They are excluded and counted.
- **1b — the proxy's list is NOT materially stale.** Compared with the
  published `model_prices_and_context_window.json` the same day: proxy 4,451
  models, published 4,452. One key is only in the published list
  (`fallback_generalizations`). 10 shared entries have different
  input/output prices, all `openrouter/*`. Every recent Claude/GPT/Gemini
  model checked is present on the proxy. The proxy loaded the list from
  GitHub main when it last started.
  **The real gap is that its age is unknowable:**
  - v1.96.2 does not report its load time.
  - `/schedule/model_cost_map_reload/status` shows no scheduled reload ever
    ran (`last_run: null`).

  So "Prices as of" says exactly that, rather than inventing a date. The
  list goes stale as long as the proxy stays up without a restart or a
  `litellm.reload_model_cost_map` run.
- **1c — `/model/info` fields:** top level `model_name`, `litellm_params`,
  `model_info`.
  - `litellm_params` today holds `model`, `use_in_pass_through`,
    `use_litellm_proxy`, `use_xai_oauth` and
    `merge_reasoning_content_in_choices`. It can also hold `api_base` /
    `api_key` / credential refs.
  - `model_info` is the price-list schema (~110 fields) plus `id`, `key`,
    `db_model`, `blocked`, `access_via_team_ids`, `direct_access` and
    `supported_openai_params`.
  - The service reads only `model_name`, `model_info.key`, `model_info.mode`
    and `litellm_params.model` (for matching; never returned). Every row is
    built from an explicit allow-list.
- **1d — DataGrid needs no server paging.** It is TanStack, client-side, and
  paginated, so only one page (50 rows) is in the DOM. Its column filters
  are text-only, so the multi-select, range and tri-state filters run in the
  page before rows reach the grid. The response is ~1.6 MB with null fields
  omitted (~3.2 MB without), which stays under Vercel's 4.5 MB function-response limit.
- **1e — menu + envelope.** Org-admin pages sit in Sidebar.jsx's
  `canAccess(me, GATE_MANAGE_ORG_SETTINGS)` block and in
  `visibleAdminSections`. **FLAGGED, NOT FIXED:** the existing gates fail
  OPEN on a lost envelope. `canPerm` default-allows when `roles` is empty,
  and `lib/usePermissions.js` substitutes `{roles: [], permissions: []}`
  whenever `/users/me` fails. So a failed `/users/me` shows every
  `manage_org_settings` and `manage_members` item; the API still refuses the
  pages themselves. Only the new entry is strict. Changing the old gates
  would alter every role-less account's menu, which is a separate decision.

**Matching rules (how the flags are computed).**
- **Live on our proxy:** a deployment resolves to `model_info.key`, else
  `litellm_params.model`, else that route without its `provider/` prefix.
  Today `claude-sonnet` → `claude-sonnet-4-6`, `claude-haiku` →
  `claude-haiku-4-5-20251001`, and `voyage-3.5` → `voyage/voyage-3.5`.
- **In platform catalog:** `platform_model_catalog.model_id` is a proxy
  alias, so it resolves through that deployment, else by a direct key match.
- **Unmatched entries:** a deployment or catalog entry that matches nothing
  gets its own row, so no flag is ever dropped silently.
- **System One:** each `ai_system_one_models` row (Jev) is its own row, kind
  `System One`.
- **Version:** parsed only from a real dated suffix (`-20250807`,
  `-2025-08-07`, `@20240620`, optionally followed by `-v1:0`); otherwise
  blank. `gpt-4-0613`'s MMDD is not treated as a date.
- **Cache:** the proxy reads are cached in-process for 1 hour.
  `refresh=true` bypasses the cache; the catalog tables are read fresh on
  every request.

**One accepted asymmetry (a FIND in the verify, by design).** A user holding
NO roles gets 200 from the API, because `has_permission` default-allows
role-less users (unchanged). The strict menu gate hides the entry for that
user anyway.

**Also touched:** `apps/api/scripts/menuvisibility_harness.mjs` — its
legacy-rule regression comparison now skips strict-gated items. Those items
postdate the legacy rule and deliberately do not inherit its default-allow.
Without the skip, `verify_superadminmenu.py`'s `no_roles_yet` regression
would flag the new entry as a changed menu.

**Verification:** `apps/api/scripts/verify_modelresearch.py` (with
`modelresearch_menu_harness.mjs`, which feeds the fixture users' REAL
`/users/me` envelopes into the shipped menu rule). WRITTEN, NOT RUN — the
operator runs it. `npm run build` exited 0 during the sprint.

---

## 00000000000000000000000000000. Ensemble = two LLMs + one System One model; /typesafe pass-through LIVE; schema migration NOT YET APPLIED; verify WRITTEN, not yet run (2026-10-01)

`ensemblesystemone.structural` overwrites `ensemblemodels.structural` (WIP commit
`bd7cc13`). Decisions from Joe, 2026-10-01: an ensemble for a task is exactly
**Model 1 (LLM) + Model 2 (LLM) + one System One model**. The third slot is
always a System One model; there is no LLM comparator and no `comparison_kind`.

**ACTION FOR JOE: apply `apps/api/migrations/ensemblesystemone_reshape.sql`.**
The sprint's `apply_migration` call was cancelled by the tool, which needs a
confirmation for destructive DDL (`DROP COLUMN comparison_kind`, the renames).
Nothing in the database has changed, so live state is still the previous
run's shape: `ai_ensemble_configs` with `review_model_*`/`comparison_*` and 0
rows, and `ai_judgment_models` with Jev disabled. The new code expects the
reshaped schema. Until the file is applied, the picker and the System One
catalog will 500, and the verify script stops at its first assertion. The
file includes a `DO` block that re-checks the four RLS policies per table, RLS
enabled, and the retire-only trigger, and raises if any is missing.

**The design.**
- **LLMs** come from the existing `platform_model_catalog` (Phase D2). No
  second LLM catalog. A Model 1/2 option is selectable only when it is
  `'available'` and the live proxy reports `mode: 'chat'`. The mode check is
  Phase E's `litellm_credentials.chat_capable_models`, reused, so
  `voyage-3.5` (embedding) is refused. The slot stores
  `platform_model_catalog.model_id`, the proxy deployment name (e.g.
  `claude-haiku`); the proxy rejects raw upstream ids with HTTP 400. The
  upstream id (`claude-haiku-4-5-20251001`) goes only into `model_1_version` /
  `model_2_version`.
- **System One models** live in `public.ai_system_one_models`
  (`ai_judgment_models`, renamed; its data is kept). New columns:
  `model_route` (what is sent to TypeSafe, pinned `jev-1.13.0`),
  `is_default` (partial unique index `ai_system_one_models_one_default`, so at
  most one default), and `last_check_detail` (the reason shown in the
  picker). `model_version` is now NULL until a real call reports one: it
  records what TypeSafe said ran, never what was asked for. Jev
  (`typesafe-jev`) is the default and stays `disabled` until verified.
- **Availability is earned.** `'available'` is reachable only through
  `services.system_one.verify_system_one_model`. That runs a real models
  listing and one real `systemone` decision through the proxy, and checks the
  probabilities are a float per option summing to 1. A pinned route must come
  back as exactly itself. A super admin can set `deprecated`/`disabled` by
  hand, never `available`. The DB CHECK
  `ai_system_one_models_available_requires_verification_chk` requires
  `last_verified_at` and `model_version` for `'available'`.
- **`ai_ensemble_configs`**: columns renamed to `model_1`, `model_2`,
  `system_one_model` (plus `*_version`); `comparison_kind` dropped. CHECKs:
  slots non-empty, and `model_1 <> model_2`. New FK `system_one_model →
  ai_system_one_models(key)` (RESTRICT), so an LLM can never sit in the
  System One slot, and an entry that history used cannot be deleted (disable
  it instead). Kept unchanged: the retire-only trigger, the
  one-active-per-task index, and the four RLS policies.
- **Platform-wide, by design.** The note-term corpus is global (one corpus,
  every org), so there is one active ensemble per task across the platform
  and no per-org selection. No `org_id` column; request bodies use
  `extra='forbid'`.

**Code.** `services/system_one.py` (catalog + live check);
`services/ai_ensembles.py` (picker + `activate_ensemble`: validate, then
retire and insert in one transaction, with version snapshots; the System One
row is re-read `FOR SHARE` inside it); `routers/ai_ensembles.py`
(`GET/POST /api/v1/admin/ai/ensembles`, `GET/POST
/api/v1/admin/system-one-catalog`, `DELETE …/{key}`, `PUT …/{key}/availability`,
`PUT …/{key}/default`, `POST …/{key}/verify`, all writes super_admin
only); `litellm_credentials.proxy_request` (a public wrapper for calls
through the proxy). UI: `SystemOneCatalogManager.jsx` under the LLM
catalog on `/admin/model-catalog` (add, remove, set availability, make
default, Verify now). The reshaped `EnsemblePanel.jsx` on
`/admin/pricing/note-terms-queue` has Model 1, Model 2 and System One, with
the System One slot preselected by the server to the default. Unavailable
options are shown with their reason, and a server-computed `blocker` states
exactly why no valid ensemble is possible.

**Removed (from `bd7cc13`).** `services/ai_model_catalog.py` (a parallel LLM
catalog unioning `/model/info` with judgment models, plus the comparison-kind
logic); `GET /api/v1/admin/ai/model-catalog` and the `comparison_kind` /
LLM-comparator branches of `routers/ai_ensembles.py`; `getAiModelCatalog` /
`listAiEnsembles` in `lib/api.js`; the three-slot review/comparison UI in
`EnsemblePanel.jsx`; `scripts/verify_ensemblemodels.py`. The old migration
file stays as the record of what was applied.

**Jev through LiteLLM: LIVE.** The proxy is v1.96.2 (`/openapi.json`).
Native Jev support (`/typesafe/{endpoint}`, registry-priced) first shipped in
v1.102.1 and is not available here. The generic pass-through IS available, so
no upgrade was needed. Proxy config is DB-stored (`STORE_MODEL_IN_DB`): models
and pass-through endpoints change through the admin API with the master key,
not a config.yaml. `scripts/configure_typesafe_passthrough.py` (idempotent)
created path `/typesafe` → `https://api.typesafe.ai`, `include_subpath`,
`auth: true`, header `Authorization: Bearer os.environ/TYPESAFE_API_KEY`. That
header is a reference the proxy resolves from its own env (Doppler
`prd_lite_llm`), never a value. Probed live: no LiteLLM key → 401. `GET
/typesafe/v1/models` → 200, listing `jev-latest` and `jev-preview`. `POST
/typesafe/v1/systemone` with `model: "jev-1.13.0"` → 200, response `model:
"jev-1.13.0"` with a `probabilities` map intact. `jev-latest` also resolved to
`jev-1.13.0` on 2026-10-01. **The listing names aliases only**: the pinned
`jev-1.13.0` is callable but not listed, so the check accepts a pinned route
only if the response echoes it exactly. `TYPESAFE_API_KEY` exists in
`prd_lite_llm` and NOT in root `prd`; the app never holds it.

**KNOWN GAP (recorded, not fixed): Jev spend is invisible to Phase G
budgets.** On v1.96.2's generic pass-through, spend is not token-priced
(`cost_per_request` defaults to 0), so `/global/spend/tags` never sees Jev.
Exposure is small: about $42 per billion input tokens. The fix is the native
integration.

**FUTURE SPRINT: upgrade LiteLLM to ≥ v1.102.1.** The proxy runs with
`DISABLE_SCHEMA_UPDATE=true`, so an upgrade needs a supervised Prisma
migration of the `litellm` schema. That is its own sprint. After it, move Jev
to the native `/typesafe` route so it is registry-priced.

**Not yet wired.** Extraction does NOT read the active ensemble
(`services/note_terms_extraction.py` still resolves `ai.model.default` /
`ai.model.assistant`). Nothing calls Jev to score real note terms. Both are
the next sprint's work.

**Per-request "no fallback" IS honored on v1.96.2. Proven live.** The
mechanism is the body field `"disable_fallbacks": true`
(`router.py`: `async_function_with_fallbacks_common_utils` re-raises
immediately). `/v1/messages` (call_type `anthropic_messages`) goes through
the same `_ageneric_api_call_with_fallbacks` path. Proof against the live
proxy: Haiku with `max_tokens=100000` (a real upstream 400) plus per-request
`fallbacks:["claude-sonnet"]` was served by Sonnet
(`x-litellm-attempted-fallbacks: 1`). The identical request with
`disable_fallbacks: true` returned the original 400. The proxy refuses
`mock_testing_*` params, which is correct and was left alone.
**The bigger collapse risk is not LiteLLM.** The proxy has no router-level
fallbacks configured: `GET /fallback/{model}` returns 404 "No ... fallbacks
configured" for every group and every type (general, context_window,
content_policy). The fallback that collapses the ensemble is the APP's
own chain in `_execute_chain` (`[primary, *ai.model.fallback_chain]` =
`[claude-sonnet, claude-haiku]`). The next sprint must call each ensemble
slot with exactly one attempt and no app-level chain, plus
`disable_fallbacks: true` for defence in depth. It should read the served
model from the `x-litellm-model-name` response header rather than
re-querying `ai_decision_log`.

**Master-key role (1b).** `GET /key/info` → 404 "Key not found in database",
because the master key is env-configured, not a DB virtual key. `GET
/user/info` resolves to the `default_user_id` row with `user_role:
internal_user`. That is the default user's record, and the source of the
old "internal_user" scare. Functionally the key IS proxy admin: `/user/list`,
`/key/list` (lists a key it does not own), `/settings`,
`/get/config/callbacks` and `/model/info` all return 200.
`litellm_diagnose.py` no longer exists in the tree (untracked in `f2a64ab`).

**`org_settings` cannot hold a platform row (1e).** `org_id` is `NOT NULL`
with an FK to `organizations`, so a NULL row is refused by the schema before
RLS applies. Its single `FOR ALL` policy (`org_settings_org_isolation`:
`org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid OR
current_setting('app.is_super_admin', true) = 'true'`) would also hide such a
row from every non-super-admin read. Policies left unaltered.

**OPEN: Doppler `prd` `APP_SERVICE_DATABASE_URL` has a stale password.** It
fails with `InvalidPasswordError`. It has the same user, host, port and
database as `DATABASE_URL`, but its embedded password differs from the
current `DB_PASSWORD` secret, while `DATABASE_URL`'s matches. Fix: rebuild it
in Doppler as a `${DB_PASSWORD}` reference (CLAUDE.md "secret referencing").
Not changed by this sprint. `verify_ensemblesystemone.py` does not depend on it: it
uses `DATABASE_URL` through `_db_bootstrap`.

**Out of scope, untouched.** Upgrading LiteLLM; per-org selection; the 29
`document_field_corrections` rows (`target_type='note_terms'`); 2nd Act's
`ai.model.*` settings.

**Verification.** `apps/api/scripts/verify_ensemblesystemone.py` is WRITTEN
but not run (the operator runs it after applying the migration). It covers
the 12 required assertion groups. The Jev group runs a real call and Verify
now when `TYPESAFE_API_KEY` is present; if the key is absent it reports
BLOCKED, never FAIL. Fixture configs use a fixture task key, so the real
`note_terms_hazard` history is never touched. The one deliberate change to a
real row: Jev's four verification columns, through the real Verify now.

---

## 0000000000000000000000000000. Verb-tier gating — a Tier-1 Service Task genuinely suspends; verify WRITTEN, not yet run (2026-09-30)

**The gap, confirmed live by `wave2discovery.lowrisk` Task 5 and unresolved
until this sprint.** `services/workflow_engine.py`'s `_drive` loop
auto-completed every `bpmn:serviceTask` the instant SpiffWorkflow parked it
in `STARTED`, regardless of tier. `_execute_service_task` read only
`action_registry_key`; `workflow_steps.autonomy_tier` was fetched by
`_load_steps` and used ONLY for `routers/workflows.py`'s
`approval_step_count` display count. A real, already-registered,
`workflow_invocable=True`, Tier-1 action
(`spv_carry.propose_from_realization`) would have executed unattended from a
Service Task with no suspension at all. Worse, the bpmn-js diagram editor's
properties panel already offered "Tier 1 — approval required" as a
governance option with no engine effect whatsoever — a promised gate that
did nothing.

**TWO TIER CONCEPTS, NOW CONNECTED.** `assistant_action_catalog.tier`
(intrinsic to the verb, `actionregistryfix.structural`) and
`workflow_steps.autonomy_tier` (the diagram author's own choice,
`workflow_steps_deriver._default_tier`) previously never met. Effective tier
is now `min(registry_tier, diagram_tier)`
(`services.workflow_engine.compute_effective_tier`), computed once and
consumed identically by the engine (to decide suspension) and the run
console (to DISPLAY it, replacing the raw diagram value the pane used to
show). Tier 1 is the highest-stakes end (COUNTERINTUITIVE ON PURPOSE, same
direction `AssistantAction.tier`'s own docstring already documents), so
`min()` is the MORE restrictive of the two, not the more permissive — an
author may UPGRADE a Tier-3 verb by marking its diagram element Tier 1 (it
now suspends even though the verb itself is low-stakes), but can never
DOWNGRADE a Tier-1 verb by marking it Tier 3 (it still suspends regardless).
This is the identical most-restrictive-wins rule CLAUDE.md documents for
dual-path permission resolution (`resolve_field_access_bulk` /
`resolve_tab_visibility`) — reused, not reinvented.

**SUSPEND AND RESUME.** `_drive` now returns `(executed, suspended_step_key)`
instead of just `executed`: when the next `STARTED` Service Task's effective
tier is 1 AND the action is `workflow_invocable`, the loop returns
immediately WITHOUT calling `_execute_service_task` — the task stays parked
`STARTED`, unexecuted, in the (now-serialized) SpiffWorkflow state.
`_suspend_step` (shared by `start_workflow_run`, `complete_user_task`'s
post-approval continuation, and the new `resolve_tier_approval`) then, in
one transaction: creates an `agent_proposals` row (`agent_key=
'workflow_engine'`, a new key documenting the deterministic gating engine
itself as the "maker" mechanism, distinct from the seven LLM-agent keys —
`agent_key` has no CHECK/FK, confirmed against
`agenticmakerchecker_substrate.sql`, so this is additive) with
`proposed_by` = the run's own `started_by`; sets the `workflow_run_steps`
row to `status='suspended'`, linked to the proposal via a NEW column,
`workflow_run_steps.agent_proposal_id` (migration
`tiergating_agent_proposal_link.sql`, applied live via the
supabase-2ndact-dev MCP `apply_migration` tool — nullable, since the
overwhelming majority of steps never suspend); sets
`workflow_runs.status='awaiting_approval'` with the state serialized so the
run is resumable; and alerts every `review_agent_proposals` holder via a
SEVENTH `member_todos` alert kind
(`workflow_todos.create_tier_approval_alerts`, same `_upsert_todo` /
`_record_undelivered_alert` machinery every prior alert kind reuses) — never
`org_admin` broadly, because the recipient set for THIS alert must be
exactly the set of people who could actually act on it.

**APPROVAL REUSES `agent_proposals`, NOT A SECOND MECHANISM.** The new
`services.workflow_engine.resolve_tier_approval(pool, workflow_run_step_id,
*, reviewed_by, decision, review_notes=None, self_approval_reason=None)`
delegates eligibility entirely to `services.agent_proposals.review_proposal`
— maker-checker (never the step's own maker, unless
`has_other_eligible_checker` finds the org's checker set genuinely empty, in
which case disclosed self-approval applies with a mandatory
`self_approval_reason`) is not reimplemented here in any form.
`MakerCheckerError` / `NotEligibleError` / `SelfApprovalReasonRequiredError`
all propagate to the new endpoint unchanged. On REJECTION the verb never
executes and both the step and the run move to a terminal `'rejected'`
status; SpiffWorkflow's serialized state is left exactly as it was
suspended (there is no "skip and continue" — like a held run, a rejected one
does not silently resume from elsewhere). On APPROVAL, `_execute_service_task`
is called directly, EXACTLY ONCE — never through `_drive`'s own loop, which
would recompute the effective tier and suspend on the very same task again
— then the run resumes driving, which may hit another Tier-1 suspension
downstream (handled the same way, recursively), a User Task pause, or
completion.

**THE SCHEDULED-RUN MAKER QUESTION WAS ALREADY ANSWERED, NOT LEFT OPEN.**
The sprint prompt raised "a scheduled run has `started_by = NULL` — who is
the maker?" as an open question to settle. Reading `services/
workflow_scheduler.py::_fire` shows it is not actually open:
`_fire` already passes `trigger["created_by"]` as `start_workflow_run`'s
`started_by` argument, and `workflow_triggers.created_by` is populated from
the authenticated caller on every trigger the API creates (`routers/
workflows.py::create_workflow_trigger`, never NULL in practice despite the
column being nullable). So `workflow_runs.started_by` was ALREADY the
correct maker for a scheduled run, identical in shape to a manual run — no
engine change was needed for this decision, only recording it here and
adding an explicit refusal (`_suspend_step` raises `WorkflowEngineError`
rather than proceeding) for the theoretical case of a run with no resolvable
maker at all, so a NULL is never silently read as "anyone may approve."

**THE DIAGRAM EDITOR'S "TIER 1 — APPROVAL REQUIRED" OPTION IS NOW REAL.**
`wave2discovery.lowrisk` flagged this UI as "worse than no gate" while
selecting it changed nothing about execution. It now does exactly what it
says.

**NEW SURFACE.** `POST /admin/workflow-runs/{run_id}/steps/{step_id}/decision`
(`routers/workflows.py`), gated on `review_agent_proposals` — a permission
SEPARATE from the three existing workflow keys (`author_workflows` /
`view_workflow_runs` / `configure_workflow_triggers`), because "may see
runs" and "may decide a suspended step" are different questions, the same
separation `agent_proposals.is_eligible_reviewer` already assumes.
`GET /admin/workflow-runs/{run_id}` now also returns `effective_tier` per
step (computed the same way the engine computes it) and
`permissions.can_review`; `RunDetailPane.jsx` renders Approve/Reject
controls ONLY inside a `can_review` check (no truthy fallback) and shows the
effective tier, not the diagram's raw value.

**STAFFING FACT, stated plainly per CLAUDE.md's Verify Script Discipline.**
`review_agent_proposals` is granted to six roles
(`support_staff`/`advisor`/`investment_committee`/`fund_finance`/
`compliance`/`org_admin`) and, in the live 2nd Act org, only `org_admin` has
a real holder (one user) — unchanged by this sprint, already true since
`selfapproval.structural`. So in practice, most Tier-1 approvals today will
take the disclosed self-approval path, not a genuine second-reviewer path.
This is a staffing fact, not a bug in this sprint's mechanism.

**Wave 2's remaining item after this sprint is the NL-to-workflow-template
library only** (a reusable-template picker, distinct from the
definitions-list "library" screen that already exists) — RestrictedPython
is separately and permanently superseded (`scripttaskrefusal.structural`).

Changed: `apps/api/services/workflow_engine.py` (`compute_effective_tier`,
`_suspend_step`, `_run_step_id_for`, `_started_service_task`,
`resolve_tier_approval`, new status constants); `apps/api/services/
workflow_todos.py` (`create_tier_approval_alerts`,
`complete_tier_approval_todos`); `apps/api/routers/workflows.py`
(`effective_tier` in the run-step response, `can_review` in the permission
envelope, `POST .../decision`); `apps/api/migrations/
tiergating_agent_proposal_link.sql` (applied live); `apps/web/components/
admin/RunDetailPane.jsx` (Approve/Reject UI, effective-tier display);
`apps/web/lib/workflowFormat.js` (`awaiting_approval`/`suspended` status
pill styling); `docs/schema_snapshot.sql` (refreshed for the new column);
`docs/WORKFLOW_WAVE2_DISCOVERY.md` (Task 5 section updated to point at this
entry); `apps/api/scripts/verify_tiergating.py` (WRITTEN, not yet run by the
operator).

---

## 000000000000000000000000000. Script Task refusal — closes the arbitrary-code-execution gap; verify WRITTEN, not yet run (2026-09-18)

**The gap, confirmed live by `wave2discovery.lowrisk` (read-only discovery,
same day).** SpiffWorkflow's stock `PythonScriptEngine` is in use with NO
subclass — its own docstring says to subclass it if you are uncomfortable
with `eval()`/`exec()`. A `bpmn:scriptTask` therefore runs arbitrary Python
IN PROCESS, with the application's own database credentials, outside the
action registry, outside every permission check, outside audit, and outside
the custody cliff. `TaskSpec.manual` defaults `False` and `ScriptTask` never
overrides it, so `do_engine_steps()` runs a Script Task to completion
automatically — it never reaches `_drive`'s `ServiceTask`-only
auto-complete hook, so none of this codebase's permission re-check,
registry resolution, or hold-on-exception logic ever sees it. The NL
generator never offers `scriptTask` to the model, but the hand-edit path
had no such restriction: an org admin could drag one from bpmn-js's stock
palette, write arbitrary Python, save it (validation did not object), and
the next run executed it — a live capability of already-merged code, not a
future risk.

**Task 1 findings.**
- **1a — live database check**: queried `workflow_versions.bpmn_xml` for any
  `scriptTask` element, any namespace prefix. **Zero matches — the table
  held ZERO rows of any kind at discovery time.** This sprint is preventive,
  not remedial; nothing already-stored needed remediation or deletion.
- **1b — one shared validator, not two**: both writers
  (`workflow_nl_generator.generate_workflow`'s `_generate_once` and
  `workflow_editor.save_new_version` via `validate_workflow_bpmn`) call the
  same private `_validate` function in `workflow_nl_generator.py`. Fixing
  `_validate` once closes the gap for both writers simultaneously — proven
  independently for each path in the verify script rather than assumed from
  the shared code.
- **1c — parser-customization mechanism**: `_BusinessRuleTaskParser`
  (`services/workflow_engine.py`) is registered via
  `parser.OVERRIDE_PARSER_CLASSES[full_tag("businessRuleTask")] =
  (_BusinessRuleTaskParser, NoneTask)` inside `_make_bpmn_parser()`. The new
  `_ScriptTaskParser` follows the identical mechanism — same
  `OVERRIDE_PARSER_CLASSES` dict, same `full_tag()` helper, same base
  `TaskParser` class — registered for `full_tag("scriptTask")`, so its
  `create_task()` raises `ScriptTaskRefusedError` (naming the element)
  instead of returning `NoneTask`. This replaces SpiffWorkflow's own
  DEFAULT entry for `scriptTask` (`ScriptTaskParser` → `ScriptTask`, which
  runs unrestricted), confirmed directly against the installed
  `SpiffWorkflow==3.1.2` package's `BpmnParser.PARSER_CLASSES`.
- **1d — the bpmn-js palette**: cannot be the enforcement boundary — a
  hand-crafted XML POST bypasses any client-side restriction entirely. It
  CAN be trimmed for usability: `WorkflowDiagramEditor.jsx` now listens for
  `commandStack.shape.create.postExecute` /
  `commandStack.shape.replace.postExecute`, and the instant a `bpmn:
  ScriptTask` lands on the canvas (from the palette OR the context-pad
  "change type" replace menu — bpmn-js's stock palette itself only offers a
  generic Task; type-specific choice happens via that replace menu), the
  command is undone and an inline message explains why. This is UI-nicety
  only; the verify script proves the server refuses regardless of what the
  client sends.

**The fix.** `services.workflow_steps_deriver.find_script_tasks` does a raw
lxml scan (QName localname, any namespace prefix — the same technique
`derive_steps` already uses to identify BPMN element types) for any
`scriptTask` element and returns its ids; `_validate` calls it BEFORE
attempting a SpiffWorkflow parse and returns one specific, named error per
element if any are found — loud and specific, not folded into a generic
parse failure. Both HTTP routes (`POST /admin/workflows`,
`POST /admin/workflows/{id}/versions`) already surface
`WorkflowValidationError`/`WorkflowGenerationError` as `422` with
`str(exc)`, so the named error reaches the caller unchanged. Nothing is
persisted on refusal — both writers validate before either INSERT/UPDATE
statement runs, unchanged from the existing validate-then-persist
structure. `services/workflow_engine.py`'s `_ScriptTaskParser` (1c above) is
defense in depth at the SpiffWorkflow parse layer itself, for any future
code path that calls `parse_bpmn` directly on BPMN that bypassed
`_validate`.

**RestrictedPython is SUPERSEDED, not deferred.** It was named in earlier
design work (`docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md` §5.3) as the
presumed eventual fix. That recommendation is retired: a sandbox is a
weaker guarantee than an outright refusal and costs materially more to
build and maintain, and an escape hatch that runs author-supplied code,
however sandboxed, reintroduces exactly the bypass the action registry
exists to prevent. A workflow step that needs logic is a Service Task
calling a registered verb — that is where permission checks, tiering,
audit, and the custody cliff already live.

**Status correction, same sprint family.** `wave2discovery.lowrisk` also
found and corrected a standing inaccuracy this file and both cross-project
reconciliation docs shared: five structural sprints (`workflowmgr1`–
`workflowmgr5`, plus `workflowbpmnfix`) building the real NL generator,
SpiffWorkflow execution engine, bpmn-js diagram editor, run console, and a
granular three-permission model were already merged to `main` and had never
been recorded anywhere in status tracking. See
`docs/WORKFLOW_WAVE2_DISCOVERY.md` for the full read and
`docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md` §5.1/§5.3 for the corrected
entries. What remains genuinely unbuilt for Wave 2: the verb-tier gating
engine (`workflow_steps.autonomy_tier` is computed and stored but never
consulted by `_execute_service_task` — a Tier-1 Service Task runs
unattended exactly like Tier-3) and the NL-to-workflow-template library
(a reusable-template picker, distinct from the definitions-list screen that
already exists). Neither is in scope for this sprint.

`apps/api/scripts/verify_scripttaskrefusal.py` is WRITTEN, not yet run by
the operator.

## 00000000000000000000000000. Self-approval under a genuinely empty checker set; verify WRITTEN, not yet run (2026-09-17)

**The problem.** `agenticmakerchecker.structural`'s maker-checker rule (a
user may check a proposal iff they hold `review_agent_proposals` AND are
not its own maker) is correct on its own terms, but it makes a
single-privileged-user org UNABLE TO EVER APPROVE ANYTHING once the maker
IS that org's only holder of `review_agent_proposals` — excluding the maker
leaves zero eligible checkers, and the proposal can never be routed. This
is not hypothetical: confirmed live against the real org (queried fresh by
this sprint, not assumed carried over), `review_agent_proposals` is granted
to six roles and exactly one of them (`org_admin`) has a real holder (one
user); the other five (`advisor`, `compliance`, `fund_finance`,
`investment_committee`, `support_staff`) have zero. A proposal made by that
one `org_admin` user has, today, a genuinely empty checker set.

**Per SPRINT_WORKFLOW_STANDARD.md's "sprint writes, operator runs" rule,
this sprint is NOT reporting a PASS/FAIL count.** `apps/api/scripts/
verify_selfapproval.py` is written and ready; the schema and code changes
below are confirmed live against the dev database by direct query during
this sprint, but the sprint itself never executes its own verify script.
Run it via:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_selfapproval.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"

**Task 1 findings:**
- **1a.** The real, live CHECK constraint (`agent_proposals_maker_checker_chk`)
  was unconditional: `reviewed_by IS NULL OR reviewed_by <> proposed_by`.
  The real eligibility function (`services.agent_proposals.
  is_eligible_reviewer`) is: holds `review_agent_proposals` (or is
  `super_admin`) AND is not the maker — unchanged by this sprint.
- **1b.** `review_agent_proposals` holder counts in the real org, queried
  live: `org_admin` = 1, every other granted role (`advisor`, `compliance`,
  `fund_finance`, `investment_committee`, `support_staff`) = 0. **Two real
  `super_admin` accounts also exist**, both in the same org as most real
  proposals — `is_eligible_reviewer` treats them as universally eligible,
  but the new emptiness gate (Task 3) deliberately does NOT count them (see
  below), so their existence does not make the gate unreachable. The
  non-empty path is genuinely reachable today for anyone other than the one
  `org_admin` holder; the genuinely-empty path is reachable for that one
  user's own proposals specifically — the real, current single-privileged-
  user case the sprint prompt describes, not an edge case.
- **1c.** `agent_proposals.proposed_by` is still a real, `NOT NULL` FK to
  `users(id)` — confirmed against the live constraint definition — and
  `services/assistant_actions/propose.py`'s `create_proposal` call still
  passes `proposed_by=user_id`, the real authenticated human principal an
  agent runs as. Unchanged; an agent never writes its own name here.

**Decision implemented exactly as specified, not re-litigated:** ALLOW WITH
DISCLOSURE. Escalating to `org_admin` was rejected — in a lean org that
target is frequently the same human wearing a second hat, which looks like
separation of duties while providing none. Blocking outright was rejected
as unusable for a single-advisor tenant.

**The conditional constraint.** `agent_proposals_maker_checker_chk` is now:

    reviewed_by IS NULL
    OR reviewed_by <> proposed_by
    OR (self_approved = true AND self_approval_reason IS NOT NULL)

Two new columns: `self_approved boolean NOT NULL DEFAULT false`,
`self_approval_reason text` (nullable). A raw UPDATE setting `reviewed_by =
proposed_by` still fails unless the row ALSO carries both — the guarantee
that a self-check can never slip through silently, even bypassing
`services.agent_proposals` entirely, survives; only the deliberate,
disclosed case opens. Proven at the database level, independent of
application code, by both a negative raw UPDATE (still fails without the
flag/reason) and a positive one (succeeds with both).

**The emptiness gate — computed, not a caller-supplied flag.** A new
`services.agent_proposals.has_other_eligible_checker(pool, org_id,
maker_id)` returns whether any OTHER user holds `review_agent_proposals`
within that org. `review_proposal`'s self-review branch now: (1) requires
the reviewer hold `review_agent_proposals` themselves — a non-holder is
refused regardless of how empty the org's checker set is; (2) requires
`has_other_eligible_checker` to be `False` — a maker with an available
reviewer is refused exactly as before, even if they supply a reason;
(3) only then requires a non-empty `self_approval_reason`, raising a new
`SelfApprovalReasonRequiredError` if it's missing. `self_approval_reason`
being present in the call is never itself sufficient — emptiness is
checked independently of it.

**Deliberately excludes `is_super_admin`** — unlike `is_eligible_reviewer`'s
own per-candidate check, which is unchanged. Both real `super_admin`
accounts sit in the same org as most real proposals; counting them in the
emptiness gate would make "genuinely empty" almost unreachable in the one
org that actually needs this mechanism, and would conflate a platform
operator's standing bypass with an org's own fiduciary staffing question —
the two are properly answered by different people. See
`services/agent_proposals.py`'s `has_other_eligible_checker` docstring for
the full reasoning.

**Honest state, staffing not design intent.** With today's real holder
counts, self-approval is the NORMAL path for the one `org_admin` user's own
proposals — not a rare accommodation. It stops being normal the moment a
second person is granted `review_agent_proposals` in that org; nothing
about the mechanism assumes it stays this way.

**Files:** `apps/api/migrations/selfapproval_conditional_maker_checker.sql`
(applied live via the supabase-2ndact-dev MCP `apply_migration` tool —
`app_service` has no `CREATE` on `public`); `apps/api/services/
agent_proposals.py` (`_holds_review_permission`, `has_other_eligible_checker`,
`review_proposal`, `SelfApprovalReasonRequiredError`);
`docs/SPRINT_WORKFLOW_STANDARD.md` (new teardown gotcha: `audit_log` is a
child of `users`, clear it — and `assistant_activities`/`agent_proposals` —
before deleting fixture users); `docs/schema_snapshot.sql` (refreshed);
`apps/api/scripts/verify_selfapproval.py`.

---

## 0000000000000000000000000. Action registry defects, tiers, propose() — verify WRITTEN, not yet run (2026-09-17)

**Not blocked on anything external.** Schema + code shipped and confirmed
live against the dev database in this session; recorded here (like the
`agenticmakerchecker.structural` entry immediately below) because the
verify script is written but not yet run by the operator, per
SPRINT_WORKFLOW_STANDARD.md's "sprint writes, operator runs" rule — this
entry makes no PASS/FAIL claim.

**Scope: `apps/api/services/action_registry.py` +
`apps/api/services/assistant_actions/*.py` + `spv_carry_runs.py`'s one
registration.** Four real defects, a new `tier` column, a module collapse,
and one new verb.

**Task 1 findings (also printed live by the verify script, not just
asserted here):**

- **1a — module collapse blast radius.** `entities`/`entity`/`entity_graph`
  collapse to one `module="entity"` value. Only the `module` field changes;
  every `action_key` is untouched, so there is no rename blast radius at
  all — the one live `workflow_steps.action_registry_key` row
  (`marketplace.show_new_deals`) was never in the collapsed set anyway.
  `module` re-confirmed write-only metadata (zero readers outside
  `ActionRegistry.sync_catalog`'s own upsert — the finding the prior
  `registryfix.structural` sprint already made still holds).
- **1b — `spv.subscribe`'s permission.** Had `required_permission=None` —
  the only write in the registry that commits capital with no gate at all.
  The real HTTP endpoint (`POST /spvs/{spv_id}/subscriptions`,
  `routers/spv.py`) never required `manage_deals` either — every OTHER
  route in that file does; this one deliberately doesn't, because
  subscribing is member-initiated, not staff-only. Fixed to
  `indicate_interest` (resource `deals`, action `interest`) — the real,
  seeded permission for exactly this, held by `member` and
  `investment_staff` in org 1. `spv.show_captable`'s `manage_deals` gate
  was checked and is CORRECT, not a second defect: it mirrors the real
  `GET /spvs/{spv_id}/captable` endpoint's own gate exactly.
- **1c — `reversible` on reads.** Grepped every real reader: exactly two,
  both in `routers/assistant.py` (`confirm_action`, `undo_activity`), both
  unreachable unless `access_type == "write"` (reads execute inline in the
  LLM loop and never produce an `assistant_activities` row). Confirmed
  meaningless on all 10 reads — left `False` (schema-level consistency,
  not a decision) rather than made nullable, since a three-state column
  would make both real consumers handle a case that can never occur for
  them. `crm.draft_note.reversible`: reported, not changed — already
  correctly `False` since commit `72ba8c0` (reverting `2999846`'s earlier
  flip to `True`), because the `/undo` path stays hollow (no `undo_token`
  stored by `_save_note`, `entity_notes` has no soft-delete column —
  reconfirmed against the current schema snapshot).
- **1d — module is still write-only metadata.** Re-confirmed live, not
  assumed carried over from the prior sprint.

**The four data defects, fixed:**
1. `spv.subscribe.required_permission`: `None` → `'indicate_interest'`.
2. `crm.draft_note.reversible`: already `False` (see 1c) — no change made;
   the sprint prompt's own instruction was to report the conflict rather
   than silently re-flip it, since the underlying `/undo` gap is unclosed.
3. `entity.link_ownership.required_permission`: `'staff'` (a ROLE string —
   never returned by `rbac.get_user_permissions`, so this action was
   silently unreachable via `/assistant/confirm` for EVERY caller, forever)
   → `'manage_deals'` (this registry's existing de facto staff gate, used
   by `spv.show_captable`/`show_ledger`/`record_transaction`).
4. Module collapse: `entities`/`entity`/`entity_graph` → `entity`.

**`tier` (int, 1/2/3) replaces `default_autonomy`.** `default_autonomy`
correlated 1:1 with `access_type` across all 16 pre-existing rows (every
read `'auto'`, every write `'confirm'`) and never encoded a real decision —
dropped from the dataclass, every registration site, and the DB column
(`apps/api/migrations/actionregistryfix_tier.sql`, applied live via the
supabase-2ndact-dev MCP `apply_migration` tool — `app_service` has no
`ALTER` on `public`). `tier` assigned by the three-test rule (does it move
money or create an obligation; does it produce an artifact a third party
relies on; does it mutate ownership, economic terms, or a posted ledger
line): **Tier 1** (highest stakes) = `spv.subscribe`,
`spv.record_transaction`, `entity.link_ownership`,
`spv_carry.propose_from_realization` (proposes rather than posts, but carry
economics ARE test three and the proposal is the artifact the GP relies
on); **Tier 2** = `crm.draft_note`, `litellm.reload_model_cost_map`,
`propose()`; **Tier 3** (lowest stakes) = all 10 reads.

**Tier direction is counterintuitive on purpose, and is now written down
where a future reader will actually see it** — `AssistantAction.tier`'s
docstring in `services/action_registry.py`. Tier 1 is the HIGHEST-stakes
class, not a small allowance. A future agent-side `max_tier` column names
the DEEPEST (numerically LOWEST) tier that agent may reach: `max_tier=1` is
the MOST permissive agent (can reach Tier 1), `max_tier=3` the LEAST
(reads only). Eligibility is `action.tier >= agent.max_tier`, never `<=` —
reading "ceiling" as "a small number means a small allowance" is backwards
and would hand a low-trust agent the highest-stakes actions.

**`propose(agent_key, object_type, payload, rationale)`** —
`apps/api/services/assistant_actions/propose.py`, the one new verb this
sprint adds, and the only one: the universal write surface every agent
depends on instead of a domain-specific action. Lands as a row in
`agent_proposals` (built by `agenticmakerchecker.structural`, above).
Deliberately `required_permission=None` and Tier 2 — the real safety gate
is the maker-checker REVIEW step that table already enforces
(`review_agent_proposals` + not-the-maker, both in Python and via a
database CHECK constraint), not the propose call itself; gating who may
propose would just re-litigate access control that already lives,
correctly, on the approve/reject side. Still goes through the standard
draft → confirm flow every other registry write uses — a wrong proposal is
low-stakes and easily discarded, but should still be a deliberate action,
not a silent side effect of a conversation. No other verb added: the
registry stays thin against five of the seven named agents (Document &
Custodial Ops and Compliance Analyst have zero verbs today) — by design,
per this sprint's own instruction, verbs arrive with the agent that needs
them, not ahead of it as a speculative menu.

**Files:** `apps/api/services/action_registry.py` (`AssistantAction.tier`,
`sync_catalog`); `apps/api/services/assistant_actions/{crm,entity_graph,
litellm_ops,marketplace,portfolio,propose,queries,spv,tasks}.py`;
`apps/api/services/spv_carry_runs.py` (one registration); `apps/api/
migrations/actionregistryfix_tier.sql`; `docs/schema_snapshot.sql`
(refreshed); `apps/api/scripts/verify_actionregistryfix.py`.

---

## 000000000000000000000000. Agentic substrate — maker-checker rule + escalation enum wiring; verify WRITTEN, not yet run (2026-09-17)

Builds the two genuinely-unblocked items from the 15-item agentic design
(`docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md` §5.1, and see
`docs/AGENTIC_SUBSTRATE_DISCOVERY.md` for the prior discovery pass this
sprint builds on). Item #3 (capability annotation on the action registry)
remains explicitly OUT OF SCOPE — its vocabulary was never defined, and the
assumption that SOC's three partial vocabularies would serve was disproven
by that discovery pass.

**Per SPRINT_WORKFLOW_STANDARD.md's "sprint writes, operator runs" rule,
this sprint is NOT reporting a PASS/FAIL count.** `apps/api/scripts/
verify_agenticmakerchecker.py` is written and ready; the schema and code
changes below are confirmed live against the dev database by direct query
during this sprint, but the sprint itself never executes its own verify
script. Run it via:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_agenticmakerchecker.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"

**Task 1 findings:**
- **1a.** `permissions.name` convention is `verb_resource`, backed by a real
  `(resource, action)` pair — confirmed against all 32 pre-existing rows.
  No existing permission covered "may check an agent's proposal";
  `review_agent_proposals` (`resource='agent_proposals', action='review'`)
  is new.
- **1b.** No generic agent-proposal table existed. The prior
  `agenticdiscovery.lowrisk` sprint had already ruled out the three real
  candidates: `assistant_activities` (the right shape — `proposed_by`/
  `approved_by` plus a real maker-checker CHECK constraint — but zero live
  rows, HELD/unwired, purpose-built for a member confirming their OWN
  assistant's action rather than a permission-gated third party),
  `member_todos` (the only one with real data, but a flat per-user queue
  with no reviewer-eligibility concept), `workflow_run_steps` (same shape
  as `assistant_activities`, zero rows). `agent_proposals` is new, reusing
  `assistant_activities`' proven `proposed_by`/CHECK-constraint shape
  rather than inventing a new one.
- **1c.** `compliance_sr`/`compliance_jr` had exactly one real code
  reference (`routers/marketplace.py`'s `_safe_notify_roles` compliance-
  override notification list) — confirmed live to hold **zero**
  `role_permissions` grants and **zero** `user_roles` holders on both
  before deletion. A SECOND reference was found DURING this sprint, not by
  the prior discovery pass: `apps/web/components/assistant/
  AssistantPanel.jsx`'s `ROLE_POSTURE` map keyed both literals to
  `"collapsed"` — genuinely dead code, since `users.role` (a free-text
  column, confirmed live to hold only `member`/`org_admin`/`super_admin`)
  never actually carried either literal, but repointed at `compliance`
  anyway for honesty going forward.

**Decisions implemented exactly as specified, not re-litigated:**
- **Boundary-rule correction.** The original agentic design said agent
  boundary = tool allowlist × reviewer role. Reviewer is NOT fixed per
  agent (see below), so it defines nothing — the boundary is the TOOL
  ALLOWLIST alone. No table in this sprint encodes reviewer as a boundary.
- **Seven-agent merge.** Chancery and Custodial Ops are one "Document &
  Custodial Ops" agent (shared reviewer, adjacent verbs) — not built as a
  row anywhere yet (no agent-definition table exists), but recorded here
  and in `services/agent_proposals.py`'s `AGENT_KEYS` for when one does.
- **Maker-checker is COMPUTED, never stored.** No `review_role` column
  anywhere. `agent_proposals` records `proposed_by` (the maker); eligibility
  to check is computed by `services.agent_proposals.is_eligible_reviewer`
  as: holds `review_agent_proposals` (or is `super_admin`) AND is not the
  maker AND is in the proposal's own org. The database backstops the
  self-check half independently via `agent_proposals_maker_checker_chk`
  (`reviewed_by IS NULL OR reviewed_by <> proposed_by`, mirroring the
  existing `assistant_activities_maker_checker_chk` pattern) — bypassing
  `services.agent_proposals` does not bypass the rule.
- **compliance_sr/compliance_jr → `compliance`.** Additive-then-remove
  (both were empty), not a grants migration. `docs/reference.md` updated;
  the real role hierarchy line collapsed accordingly.
- **The review permission's real grants**, six roles exactly (Hollis maps
  to no role — it never proposes, so it never checks either): `support_staff`,
  `advisor`, `investment_committee`, `fund_finance`, `compliance`,
  `org_admin`. Deal & SPV and Fund Admin & Billing both route to
  `fund_finance` by design (a routing default, not a boundary) — granting
  once covers both.
- **escalation_reason wired for real.** The pre-existing enum
  (`budget|max_steps|tool_error|low_confidence|refused|ambiguous`, created
  earlier and confirmed still unwired as of the `registryfix.structural`
  sprint) now lives on `agent_proposals.escalation_reason` — nullable,
  since the ordinary propose-then-check flow is not itself an escalation.

**Honest state of the mechanism, restated per the sprint prompt's own
instruction not to overstate this:** NO agent runs exist yet — Workflow
Manager Wave 2 (which would give an agent a workflow instance to execute
as) is unbuilt, so nothing calls `services.agent_proposals.create_proposal`
from real agent traffic today. This sprint is substrate — the table, the
permission, and the rule — not a running agent. The reviewer roles
themselves are mostly empty: `advisor` (11 perms), `support_staff` (7), and
`org_admin` (1, now 2) have real holders; `investment_committee`,
`fund_finance`, and the new `compliance` role have **zero** holders across
the board. The maker-checker rule is therefore UNEXERCISED in production
until staffing catches up — every proof in the verify script runs against
fixtures, not real traffic.

**Files:** `apps/api/migrations/agenticmakerchecker_substrate.sql` (applied
live via the supabase-2ndact-dev MCP `apply_migration` tool — `app_service`
has no `CREATE` on `public`); `apps/api/services/agent_proposals.py`
(`create_proposal`, `is_eligible_reviewer`, `review_proposal`); `apps/api/
routers/marketplace.py` and `apps/web/components/assistant/
AssistantPanel.jsx` (both `compliance_sr`/`compliance_jr` references
repointed); `docs/reference.md` (role UUID list + hierarchy line).

**#3 capability annotation on the action registry — DISSOLVED, not solved**
(`actionregistryfix.structural`, above, 2026-09-17). At 16 registered verbs,
none of the three candidate vocabularies this discovery ruled out
(`profiles.name`, `permissions`, `trading_authority_grants.authority_tier`)
was ever going to be the fix — a dedicated capability column against a
16-row table would just be a SECOND parallel vocabulary next to
`required_permission`, which is exactly the registry-duplication problem
this codebase already suffers from elsewhere. `required_permission` IS the
capability vocabulary at this scale; the sprint fixed its two real defects
(`spv.subscribe`'s missing gate, `entity.link_ownership`'s role-not-a-
permission gate) directly instead. Revisit only if verb count genuinely
outgrows what `required_permission` can express — not before.

---

## 00000000000000000000000. LiteLLM design-doc §7.5 — Hollisworks force-Anthropic emergency bypass (2026-09-17)

`apps/api/scripts/verify_litellmphasef.py` (results below reflect the actual
run). Full design and proof summary in
`docs/LITELLM_INTEGRATION_DESIGN_V1.md` §7.5. Note: internally sprint-named
`litellmphasef.structural`, but this is design-doc section **§7.5**, not
roadmap-table row **F** (§8, budget-threshold UX — still unbuilt and
untouched by this work; the two "F"s are an unfortunate but harmless naming
collision).

- **Reuses, does not duplicate, the existing rollback.**
  `LITELLM_ROUTING_DISABLED=1` (litellmphaseb, 25/25) already is the
  direct-Anthropic branch of `services.extraction._build_ai_client`. This
  sprint adds a SECOND driver of that exact same branch — a new table,
  `platform_ai_controls` (one row, `force_anthropic_bypass`), read via the
  new `services.extraction.resolve_text_transport` /
  `_build_text_ai_client` — checked only when the pre-existing env var did
  NOT already force Anthropic. `_build_ai_client` itself is untouched
  (still sync, still env-var-only), so every older verify script that calls
  it directly keeps working byte-for-byte.
- **Genuinely platform-scoped.** `org_settings` has no `owner_scope` column
  and cannot hold a platform-scope row (the same real gap D2 found and
  solved with `platform_model_catalog`) — confirmed live again this sprint.
  `platform_ai_controls` follows the identical convention: no `org_id`
  column at all. Read is open to any caller (every real AI call site needs
  it regardless of who's calling); write is `super_admin` only, enforced at
  BOTH the RLS layer (a policy for all four operations, per the
  platform_model_catalog missing-UPDATE lesson) and the router — and this
  sprint proved the RLS layer independently, not just the app-level 403: a
  non-super-admin RLS context updates zero rows at the database layer.
- **Blunt, deliberately.** Engaging the bypass makes `_execute_chain` skip
  per-task model resolution, the org's fallback chain, D2
  authorization/disabled-model filtering, and Phase-E effort entirely, and
  hardcodes the single attempt to `FORCE_ANTHROPIC_BYPASS_MODEL`
  (`claude-haiku-4-5-20251001`). Proven live: a call explicitly requesting
  `claude-sonnet` via `model_override` still resolves to the fixed model —
  the override is genuinely ignored while the bypass is on.
- **Real finding that shaped the fixed-model choice**: the pre-existing
  rollback branch does ZERO deployment-name translation — an org's real
  default model string (`'claude-haiku'`, a PROXY deployment name) sent
  straight to `api.anthropic.com` would 404. Every prior rollback proof
  (`verify_litellmphasebproof.py`) only worked because it hand-passed a
  real dated id via `model_override`. The fixed constant is what makes this
  new admin-facing toggle safe without an operator also remembering a real
  model id.
- **THE EMBEDDING DECISION (the non-obvious part)**: embeddings KEEP
  ROUTING THROUGH LITELLM, completely unaffected, even while the bypass is
  engaged. Voyage has no direct-Anthropic equivalent, and the toggle exists
  for an Anthropic-specific incident — degrading a healthy embedding path
  too would be an unrelated blast-radius increase. `services.
  document_embedding` calls the ORIGINAL `resolve_transport()` only; the
  two new Phase F entry points are structurally absent from that module.
  Proven live: a real Voyage call succeeds while the text bypass is
  engaged, and its own `ai_decision_log` row shows `litellm_bypassed=false`
  — the opposite of the concurrent text call's row.
- **Observability while dark.** Two new nullable/defaulted `ai_decision_log`
  columns, `litellm_bypassed boolean` and `bypass_reason text`
  (`migrations/litellmphasef_force_anthropic_bypass.sql`), mark every call
  that did not go through LiteLLM (for any of the three reasons — the env
  var, an unconfigured proxy, or this new toggle), so an incident leaves a
  queryable record independent of LiteLLM's own spend log, which stays
  completely dark while bypassed (proven by absence after the full flush
  window).
- No blockers.

## 0000000000000000000000. LiteLLM Phase E — per-task model assignment + effort (2026-09-16)

`54/54 PASS, 0 FAIL, 4 FIND` — `apps/api/scripts/verify_litellmphasee.py`.
D2 built the curated/authorised model lists; nothing yet decided WHICH task
uses WHICH of an org's authorised models, or how hard it thinks. This
sprint adds that layer. Full accounting in
`docs/LITELLM_INTEGRATION_DESIGN_V1.md` §14.6 and
`docs/LITELLM_D2_E_SPEC.md` — summary:

- **Task 1 finding — granularity mismatch.** 19 real `task_type` values
  exist across the platform's `call_claude_json`/`call_claude_text`/
  `call_claude_with_tools` call sites (grepped live, not assumed), but only
  THREE assignable dials have ever existed
  (`ai.model.default`/`ai.model.assistant`/`ai.model.document_classifier`)
  — a task with no dedicated key shares whichever dial its call site's
  `model_key` defaults to. This sprint assigns at the REAL granularity
  (the three dials), not an invented per-task-type one — see the spec's §7
  for why. `services.extraction.MODEL_TASK_REGISTRY` is now the one list
  the settings API and the frontend both read; a NEW dial still needs a
  code change (constant + registry entry + `model_key=` threaded at its
  call site) — reported honestly, not automatic.
- **Effort is `thinking.budget_tokens`, not `reasoning_effort`.** Probed
  live: this module's calls are Anthropic-shaped (`/v1/messages`), so the
  real, accepted parameter is the native `thinking={"type":"enabled",
  "budget_tokens":N}` int, confirmed by a real call returning a genuine
  `thinking` content block plus `usage.output_tokens_details.
  thinking_tokens > 0`. LiteLLM's `/model_group/info` also lists OpenAI's
  `reasoning_effort` string enum under `supported_openai_params`, but that
  describes LiteLLM's OpenAI-shaped route, which this module never calls.
  A small local `EFFORT_LEVELS` map (`low`=1024, `medium`=4096,
  `high`=12000 budget tokens) supplies the values LiteLLM doesn't report.
- **THE SETTLED DECISION (design doc §4's open question): fallback drops
  effort silently, and logs it.** When a task with an effort setting falls
  back to a model that does NOT report `supports_reasoning: true`,
  `_execute_chain` sends the call WITHOUT the `thinking` parameter rather
  than failing it — gated per ATTEMPT (not once for the whole chain), so a
  primary that supports reasoning and a fallback that doesn't behave
  correctly in the same call. Two new nullable columns,
  `ai_decision_log.effort_requested`/`effort_used`
  (`migrations/litellmphasee_effort_columns.sql`), make the choice
  queryable after the fact: `effort_requested` set + `effort_used` NULL on
  a `success=true` row is exactly the dropped case. Proven live with a
  forced-failure primary + real fallback call (the reasoning-support
  *lookup* was patched for one assertion only, since no real non-reasoning
  CHAT deployment exists on the live proxy today — the provider call itself
  was 100% real and unpatched).
- **A real, pre-existing bug found and fixed before it shipped**:
  `call_claude_json`/`call_claude_text` both extracted the response as
  `message.content[0].text`, which would have raised `AttributeError` the
  moment ANY task's effort was ever set — Anthropic's real content order
  with thinking enabled is `[thinking, text]`, and a thinking block has
  `.thinking`, not `.text`. Fixed with a shared `_response_text()` helper
  that finds the real text block regardless of position.
- **A guard D2 didn't need but Phase E does**: `platform_model_catalog` has
  no `mode` column, so nothing previously stopped assigning an
  embedding-only model (`voyage-3.5`) to a chat dial. New
  `services.litellm_credentials.chat_capable_models()` (live
  `/model_group/info`, `mode == "chat"`) gates
  `services.model_catalog.validate_assignable_model` — proven refused
  (400) live.
- **Two real, unrelated bugs found and fixed in `verify_litellmseedfix.py`**
  while re-running it for regression: an off-by-one in its own
  `repo_root = HERE.parents[2]` (should be `[3]`) silently turned three of
  its grep-based checks into false failures against empty search paths;
  and a line-number-pinned allowlist entry went stale because this
  sprint's own edits to `extraction.py` shifted an unrelated comment's line
  number. Both were pre-existing, confirmed via `git diff` to predate this
  sprint's own changes, and both are now fixed (`31/31 PASS`).
- New endpoints: `GET`/`PUT /orgs/{org_id}/settings/ai-tasks[/{task_key}]`
  (reads open to any org member, writes gated on `manage_org_settings` —
  the identical envelope D1a/D2 established). New frontend:
  `ModelTaskAssignment.jsx`, embedded in the existing `/admin/settings`
  screen; the six `ai.model.*`/`ai.effort.*` keys are hidden from the
  generic free-text settings editor (one widget, not two that can
  disagree). `npm run build` exits 0.
- Cross-org isolation, org-admin-vs-member 403, and an unassigned task's
  byte-for-byte no-regression are all proven live — see the verify script's
  own docstring for the full accounting.

## 000000000000000000000. LiteLLM Phase D2 — the model pick-list UI (2026-09-16)

`47/47 PASS, 0 FAIL, 4 FIND` — `apps/api/scripts/verify_litellmphased2.py`.
D1a-c built per-provider BYO credentials; nothing yet decided WHICH models
exist at all or which of them an org may use. This sprint adds that layer.
Full accounting in `docs/LITELLM_INTEGRATION_DESIGN_V1.md` §14.5 — summary:

- **Two new tables**, not an org_settings key: `platform_model_catalog`
  (Hollisworks-curated, no org_id column — genuinely platform-wide) and
  `org_model_selections` (an org's authorised subset, row presence =
  authorised). Forced by a real finding: `org_settings.org_id` is `NOT
  NULL` with no `owner_scope` column, re-confirmed live — a platform-scoped
  row is not possible there. Mirrors the SAME live global-vs-org split
  `public.reference_data` already uses, not a new pattern.
- **Enforcement is real**, not merely recorded: `services.extraction.
  _execute_chain` filters its resolved model chain against the org's
  authorised set BEFORE any provider call; empty result raises the new
  `AIModelNotAuthorizedError`. Proven bidirectionally with real live calls
  (refused before any network call; then succeeds once authorised) and for
  cross-org isolation and the real production orgs (both still genuinely
  unrestricted, read-only checked, untouched).
- **Two real bugs found and fixed in this pass**: `call_claude_json`'s
  exception handling would have silently swallowed the new refusal into its
  generic `None` contract (fixed — added to the same re-raise tuple
  `AIOrgCredentialError`/`AILiteLLMAuthError` already use); and a Starlette
  route-registration-order bug where the generic
  `PUT /orgs/{org_id}/settings/{key}` route silently absorbed
  `PUT .../model-selections` (fixed — the specific route now registers
  first, with a docstring warning against regressing it).
- **[FIND]**, orthogonal to this sprint, not fixed here: neither of
  `org_settings`' own real default-chain model strings
  (`claude-sonnet-4-6`, `claude-haiku-4-5-20251001`) is actually callable
  against the live LiteLLM proxy today (`Invalid model name`, HTTP 400) —
  only the registered deployment name `claude-sonnet` is. Every prior
  sprint's real successful call already used an explicit override for this
  reason; this sprint's own Task 4 proof does too, and records the gap
  rather than silently routing around it.
- Frontend: `/admin/model-catalog` (new `ModelCatalogManager.jsx`,
  `components/ui/DataGrid` + right-pane, super_admin only) and a new
  `OrgModelSelector.jsx` checkbox-list section embedded in the existing
  `/admin/settings` screen (`manage_org_settings`, reusing the ai-credentials
  envelope shape). `npm run build` exits 0.

**Phase E — per-task model assignment + effort — shipped**, see the entry
above.

## 00000000000000000000. LiteLLM Phase D1b — routing + spend attribution (2026-09-15)

`54/54 PASS, 0 FAIL, 3 FIND` — `apps/api/scripts/verify_litellmphased1b.py`.
Backend only. D1a proved an org could store its own provider key and get a
dedicated LiteLLM deployment; nothing read `ai.credential_source.*` at call
time and every call sent LiteLLM no metadata at all, so every real call
landed in `LiteLLM_SpendLogs` attributed to the master key with a null
team — "which org incurred this, against whose key" was unanswerable. This
sprint closes both gaps.

**Task 1 findings, all re-probed live against the real proxy:**

- **1a — resolver location.** `services/extraction.py`'s `_execute_chain`
  (text/tools) and `services/document_embedding.py`'s
  `_execute_embedding_chain` (embeddings) are the two real chain executors.
  D1b's per-org resolution slots into the SAME point in both: immediately
  after the model/fallback chain is computed, immediately before
  `make_call()`/`_embed_litellm()` is invoked, per attempt. It never touches
  `resolve_model`/`resolve_fallback_chain` (still resolve the LOGICAL model
  name from `org_settings`) — it only decides which DEPLOYMENT answers that
  logical name, via the two new `services.litellm_credentials` functions
  (`resolve_credential_source`, `resolve_deployment_model`), gated on
  `transport == TRANSPORT_LITELLM` so the direct-Anthropic rollback path is
  provably untouched.
- **1b — real metadata fields, probed, not assumed.** `metadata.tags` (a
  native Anthropic-SDK/LiteLLM-proxy field) lands in
  `LiteLLM_SpendLogs.request_tags`. The top-level `user` field (not part of
  the SDK's typed surface — sent via `extra_body`) lands in
  `LiteLLM_SpendLogs.end_user`, NOT the `user` column (which stays the fixed
  `'default_user_id'` for a master-key-authenticated call regardless of
  request content). **[FIND]** `metadata.user_id` and
  `metadata.spend_logs_metadata` do NOT land anywhere readable back via `GET
  /spend/logs` for a master-key call — a real dead end, probed and
  discarded rather than assumed to work from the field name alone.
- **1c — text and embeddings do NOT share an identical mechanism.**
  `metadata.tags` works identically on both paths (same field, same
  column). The top-level `user` field is Anthropic-only: Voyage's
  embeddings route rejects it outright
  (`litellm.UnsupportedParamsError: voyage does not support parameters:
  {'user': ...}`, probed live, HTTP 400) — a real, provider-specific
  difference, not an oversight. `services/document_embedding.py`'s
  `_embed_litellm` sends ONLY `metadata.tags`; `services/extraction.py`
  sends both.

**Task 2 — routing.** `services/litellm_credentials.py` gained
`resolve_deployment_model(model_id, org_id, provider, credential_source)`:
translates `model_id` to the org's own deployment
(`_org_deployment_name(provider, org_id)`) ONLY when `model_id` is exactly
the platform deployment that provider mirrors (`claude-sonnet` /
`voyage-3.5`) AND `credential_source == 'org'` — every other `model_id`
(an unregistered/dated string, or a provider still on `'platform'`) passes
through unchanged. The caller-facing logical model name never changes:
`ai_decision_log.model_used`/`model_requested` record `'claude-sonnet'` for
an org-routed call exactly as they do for a platform-routed one — proven
live, and separately grepped for the internal deployment name (zero
matches).

**Task 3 — attribution.** `build_attribution(org_id, provider,
credential_source)` returns `{tags, end_user, usage}`. `usage` is one of
THREE labels, not two — `'org_owned_key'` (org supplied its own key),
`'hollisworks_platform'` (platform key, Hollisworks' own org), or
`'platform_on_behalf_of_org'` (platform key, any other org) — because
Hollisworks' own usage and "platform key on someone else's behalf" are both
distinct from "the org owns the key," and conflating the first two would
have made Hollisworks' own AI usage invisible in its own spend log. Both
`extraction.py`'s three `call_claude_*` wrappers and
`document_embedding.py`'s `_embed_litellm` attach this per call.

**Task 4 proof, all live, all via the spend log's OWN record of which
deployment ran the call (never inferred from config):** 2nd Act's real call
(`'platform'`) landed on the platform deployment id, unchanged from before
this sprint — the no-regression case every existing org is in. A real
before/after: an unattributed raw call has an empty `end_user` and no
`org:` tag; the identical call shape through the fixed code carries both.
Hollisworks' own real call is tagged `usage:hollisworks_platform`, 2nd
Act's `usage:platform_on_behalf_of_org` — same shared platform key,
distinguishable in the log. Two fixture orgs each provisioned their own
real Anthropic deployment (D1a's mechanism, a real key): each org's real
call landed on ITS OWN deployment id — three mutually distinct ids
observed (platform, org A, org B) across three real calls, proving
cross-org isolation the same way (org A's call never carries org B's or the
platform's deployment id, and vice versa). Embeddings carry the same
before/after attribution proof (2nd Act, platform-routed) with the
Task-1c-correct absence of `end_user`. Org-routing for the embedding side is
proven at the function level only (`resolve_deployment_model` correctly
translates `voyage-3.5` when `'org'`-sourced) — a second live 'org'-routed
Voyage call was deliberately not made, to respect the documented free-tier
pacing budget (3 req/min; 25s previously proven insufficient, this sprint
paced 68s). No deployment name appears in any org-facing HTTP response
(re-grepped `GET /orgs/{id}/settings/ai-credentials`, unchanged from D1a) or
in `ai_decision_log`.

Teardown: zero leftover fixture rows (organizations/users/org_settings),
zero leftover `ai_decision_log` rows (`task_type LIKE 'verify_d1b_%'`), and
the live proxy back to exactly its pre-run deployment set (`claude-sonnet`,
`voyage-3.5` only).

**Next: D1c — credential-failure alerting** (explicitly out of scope here,
per the sprint prompt). See `docs/LITELLM_INTEGRATION_DESIGN_V1.md` §14.3
for the updated design-doc accounting.

---

## 0000000000000000000. LiteLLM Phase D1a — per-org AI provider credential storage (2026-09-15)

`58/58 PASS, 0 FAIL, 3 FIND` — `apps/api/scripts/verify_litellmphased1a.py`,
re-run clean three consecutive times against the live proxy and database.
Backend only; scope was deliberately narrow (per the sprint prompt): can an
org store its own provider key, and does a dedicated LiteLLM deployment get
created for it? Routing, spend attribution, and failure alerting are
explicitly NOT built here — later phases.

**Task 1a — THE central finding, proved live, not inferred:** `POST
/model/new`'s `litellm_params.api_key` accepts a LITERAL credential value,
not only `os.environ/<NAME>` indirection. Proved by creating a real
deployment with a literal `ANTHROPIC_API_KEY` value, making a genuine call
through it (HTTP 200, real model output "OK"), then deleting it. LiteLLM
encrypts the value at rest (`LITELLM_SALT_KEY`) and **never** echoes
`api_key` back through `GET /model/info` — confirmed true for the two
pre-existing platform deployments (`claude-sonnet`, `voyage-3.5`) AND for
the literal-key probe deployment alike, regardless of which mechanism
supplied the credential. This is what every later phase depends on: an
org's own key crosses the wire once, to LiteLLM, and is never stored in our
own database, logged, or readable back out of LiteLLM's own admin API.

**Task 1b — `org_settings` needs no schema change.**
`ai.credential_source.<provider>` (`'org'` | `'platform'`), validated with
the exact same enum-precedent shape `ai.embedding.provider` already
established. `DEFAULT_SETTINGS['ai.credential_source.anthropic']` and
`['ai.credential_source.voyage']` both default `'platform'` — which is what
makes "every existing org's behavior is unchanged" true for free, with no
migration: no org has ever had a row for either key, confirmed live for
both real orgs (2nd Act, Hollisworks).

**Task 1c — confirmed live:** `claude-sonnet` → `anthropic/claude-sonnet-4-6`,
`voyage-3.5` → `voyage/voyage-3.5` (`litellm_params.model`, read fresh via
`GET /model/info`). `api_key` is never exposed by that endpoint for either
existing deployment either, so this script cannot visually distinguish
`os.environ/` indirection from a literal value on these two pre-existing
rows — `docs/LITELLM_INTEGRATION_DESIGN_V1.md` §14.2 already recorded
voyage-3.5's real creation payload directly
(`"api_key": "os.environ/VOYAGE_API_KEY"`).

**Task 2/3 — the mechanism.** `services/litellm_credentials.py` (new):
`set_org_provider_credential` / `clear_org_provider_credential` provision or
deprovision a dedicated, deterministically-named internal deployment
(`org-{provider}-{org_id}` — never the platform's logical name, since
same-`model_name` deployments load-balance rather than route
deterministically by owner, confirmed in the Phase D discovery doc) and flip
the org's `ai.credential_source.{provider}` setting in lockstep. Provision
runs BEFORE the setting flips (never claim `'org'` with no real deployment
behind it); deprovision runs BEFORE the setting reverts (never silently
report `'platform'` while a stale, still-keyed deployment sits on the proxy
— the exact hazard the sprint prompt named). The org<->deployment mapping is
deliberately NOT duplicated into our own schema — `app_service` cannot read
the `litellm` schema regardless (CLAUDE.md), so the deployment name is
recomputed deterministically and LiteLLM's own `GET /model/info` is treated
as the single source of truth for "does this org currently have one."

New routes on `apps/api/routers/org_settings.py`: `GET`/`PUT`/`DELETE
/orgs/{org_id}/settings/ai-credentials[/{provider}]`. Same permission gate
every other write on this router uses (`can_manage_org_settings` —
super_admin anywhere, org_admin at home); reads open to any org member,
matching the rest of the settings router.

**Task 4 proof, all live:** a test org's own key creates a real, distinct
deployment (confirmed via `GET /model/info` read-back, not just "the call
didn't error") whose `litellm_params.model` mirrors the platform's live
`claude-sonnet` upstream string and whose `model_info.id` is genuinely
different from the platform deployment's own id. Removing it deletes that
exact deployment, confirmed the same way. Every response body across the
full lifecycle (`PUT`, `DELETE`, final `GET`, and the general `GET
/orgs/{id}/settings?detail=true`) was grepped as raw text for the internal
deployment name — zero matches, every time. Cross-org: two orgs' own
deployments coexist independently (different names, different ids); org B's
admin is refused (403) on the IDENTICAL read/write/delete requests org A's
own admin succeeds on; a real, non-zero-role non-admin member of org A is
refused (403) on the identical write request org A's admin succeeds on
(reads stay open to any org member — same convention as the rest of this
router).

**[FIND]** GET /model/info showed brief eventual-consistency lag
immediately after a `POST /model/new` / `POST /model/delete` in ad hoc
repeated runs (same class of propagation lag already documented for
`LiteLLM_SpendLogs` elsewhere in this project, just metadata-only and much
shorter) — the verify script polls (up to 12s) on the existence-transition
assertions rather than assuming a single immediate read is authoritative.

**[FIND]** A zero-`user_roles` fixture user default-ALLOWs permission checks
(`has_permission`'s documented single-admin bootstrap posture) — the
non-admin fixture needed a REAL, granted role that excludes
`manage_org_settings`, not merely an ungranted user, to prove the refusal
path meant anything (the same lesson `verify_orgadminrole.py` already
learned).

Teardown: zero leftover fixture rows (organizations/users/roles/
org_settings) and the live proxy back to exactly its pre-sprint deployment
set (`claude-sonnet`, `voyage-3.5` only) — confirmed after every run.

See `docs/LITELLM_INTEGRATION_DESIGN_V1.md` §14.3 for the full design-doc
accounting, and `docs/LITELLM_PHASE_D_DISCOVERY.md` for the discovery this
sprint built on.

---

## 000000000000000000. org_admin role reconciliation — SHIPPED; two call sites still owed a migration (2026-09-14)

`35/35 PASS` — `apps/api/scripts/verify_orgadminrole.py`. Makes `org_admin` a
real row on the RBAC role axis instead of a free-text `users.role` string with
no mapping to `roles`/`role_permissions`/`user_roles` at all.

**What changed.** `roles` (per-org: `roles.org_id` is part of a
`(org_id, name)` UNIQUE key, there is no global role catalog) gained an
`org_admin` row for 2nd Act — the only org with a real holder. A new
`manage_org_settings` permission (`resource='org_settings', action='manage'`)
was created — `routers/modeling_ta.py` already referenced this exact string in
its envelope's `write_permission` field before any real permission row backed
it — and granted to `org_admin` via `role_permissions`. The single existing
holder (`jpl99172@gmail.com`, 2nd Act) got an additive `user_roles` grant;
`users.role` was never cleared or written to by this sprint.

Every org-admin gate now resolves by PERMISSION, not by the `users.role`
string: `services.rbac.is_org_admin` / `can_manage_org_settings` (now async,
take `pool`) call the same `has_permission` path every other permission check
in this app uses — no second mechanism invented. Callers migrated:
`services/org_settings.py` (`set_setting`/`set_settings`),
`routers/profiles.py` (`_require_admin`), `routers/modeling_ta.py`
(`_ta_permissions`/`_defaults_envelope`), `services/delegate_grants.py`
(`activate_springing_delegate`). `services/workflow_todos.py`'s alert
recipient resolution (`create_held_run_alerts`,
`create_trigger_expiring_alerts`) dropped its raw
`SELECT id FROM users WHERE role = 'org_admin'` scan for a new
`rbac.get_users_with_permission(org_id, 'manage_org_settings')` — same
resolved set, proven set-to-set against the old query for 2nd Act. On the
frontend, `apps/web/lib/menuVisibility.mjs`'s 5 org-admin menu items moved
from `{ roles: ['org_admin', 'super_admin'] }` to `{ perm:
'manage_org_settings' }` (`GATE_MANAGE_ORG_SETTINGS`), and
`apps/web/app/admin/settings/page.js` — which had its OWN independent
`theme.role === 'org_admin' || 'super_admin'` copy of the gate, a real,
confirmed inconsistency with every other org-admin page's server-enforced
pattern — now calls the same `canPerm()` the sidebar uses.

**The silent-no-recipient bug is fixed.** An alert whose recipient set
resolves to empty (confirmed live: the Hollisworks org has real users but
zero `manage_org_settings` holders) used to write nothing and fail silently.
It now writes a findable `audit_log` row (`action =
'workflow_alert_undelivered'`, `resource_type`/`resource_id` = the run or
trigger, `payload` states why) — reusing the existing table
(`audit_log.user_id` is nullable) rather than a schema change.

**Proven, not assumed, in both directions.** The real org_admin reaches every
previously-reachable org-admin page/endpoint through the real ASGI app; a real
non-admin fixture holding a REAL deployed role (the seeded `member` role — a
zero-role fixture would default-allow via `has_permission`'s single-admin
bootstrap posture and prove nothing) is refused the identical requests;
cross-org isolation holds both for alert recipients and admin page access; the
real super_admin account with ZERO `user_roles` grants (`jlarizza@gmail.com`)
still resolves correctly through the `is_super_admin` bypass, checked first.

**Known, accepted gap — inherited, not introduced.** `has_permission`
default-allows a user with zero `user_roles` rows at all (documented
single-admin-bootstrap posture, already governing `manage_members` and every
other permission in this app). Any REAL user who still has zero role rows
would therefore also default-allow `manage_org_settings` once it's checked via
`has_permission` — this is the same systemic characteristic that already
applies to every other permission-gated surface, not a new risk created by
routing org_admin through it. Closing it (assigning every real user a real
role) is separate, unscoped work.

**NOT migrated — two real call sites still WRITE `users.role='org_admin'`
directly with no corresponding RBAC grant, by name, so this is scoped follow-up
rather than vague:**
- `services/invites.py` (`ALLOWED_INVITE_ROLES = ('member', 'org_admin')`) —
  inviting someone as org_admin sets `users.role` only.
- `apps/web/components/admin/UserManagement.jsx` (the "Organization Admin"
  role dropdown) — the UI that sets `users.role`, same gap.

Until one of these also grants the RBAC role/permission (reusing this
sprint's migration logic, keyed off the same `(org_id, 'org_admin')` role
row), a user promoted through either path will show `users.role='org_admin'`
but be refused every permission-gated org-admin surface until someone re-runs
the migration for them. **`users.role` must not be dropped** until these two
call sites (plus `services/rbac.py`'s `is_super_admin`/`load_principal`,
`routers/admin.py`'s super-admin checks, and `routers/users.py`'s
`account_role` field — all still legitimately read it for `super_admin`, out
of this sprint's scope) are migrated too.

**UPDATE 2026-09-14 (orgadminwrites.structural): CLOSED.** `32/32 PASS` —
`apps/api/scripts/verify_orgadminwrites.py`. Both real gaps above are fixed,
plus one discovered in the same class while fixing them:

- `services/invites.py` `create_invite` — now calls the new
  `services.rbac.grant_org_admin` inside the SAME transaction as the insert
  when `role='org_admin'`. `ensure_org_admin_role` creates the per-org role +
  `manage_org_settings` grant on demand, so an org with zero org_admin
  holders today (Hollisworks, confirmed live, still zero) gets one the first
  time it needs it — no second manual migration required.
- `routers/admin.py` `assign_role` (`PUT /admin/users/{id}/role`) — this
  endpoint actually had the OPPOSITE half of the same bug: it always wrote
  the real `user_roles` grant correctly but never touched `users.role` at
  all, in either direction. A promotion via the RBAC role dropdown left the
  string stale; a demotion away from org_admin correctly revoked the
  permission but left `users.role='org_admin'` behind — a real
  privilege-ratchet-shaped inconsistency even though the actual access WAS
  correctly revoked. Now syncs the string both ways, guarded to never
  rewrite a `'super_admin'` string.
- `routers/admin.py` `delete_user` (anonymization) — already revoked
  `user_roles` unconditionally but left `users.role` untouched, which would
  have created NEW drift going forward. Fixed with the same guarded rule.
- **New defect found and fixed in the same pass, not previously flagged**:
  `GET /admin/roles` and `assign_role`'s role lookup were both **org-blind**
  (`SELECT ... FROM roles`, no `org_id` filter at all; `WHERE id = $1` with
  no org check) — live data happened to mask it (every `roles` row today
  belongs to 2nd Act, so the leak/escalation path was structurally live but
  not yet exploitable). Both now scope to the caller's own org.
- **Real finding, not just a fix**: the prompt's framing ("will be REFUSED")
  undersold the actual pre-fix failure mode. `has_permission`'s zero-role
  default-allow bootstrap means a freshly invited org_admin with NO grant
  was not immediately refused — they were silently OVER-privileged
  (default-allow to every permission, not just `manage_org_settings`) until
  the first time ANY role was ever assigned to them, at which point the
  strict per-permission check engaged and, absent the real grant, refused
  them at every org-admin surface despite `users.role` still saying
  `'org_admin'`. Proven live with a controlled fixture in both states.
- Zero drift existed in live data before this sprint (`_reconcile_
  orgadminwrites_drift.py`, run for real: 0 found) — the single real
  org_admin holder was already in sync. The reconciliation function itself
  is proven against injected, fully-torn-down fixtures covering both
  directions, since live data had none to exercise it against.
- Hollisworks still has **zero** `manage_org_settings` holders — unchanged by
  this sprint (closing that gap means inviting/promoting an actual
  Hollisworks admin, which is a real operational action, not a code fix).
- `users.role` remaining READ call sites are unchanged from the list above
  (`is_super_admin`/`load_principal`, `routers/admin.py` staff checks,
  `routers/users.py` `account_role`) — still legitimately out of scope, still
  not dropped.

---

## 00000000000000000. Registry defects + escalation_reason enum — 5 fixes SHIPPED; nothing blocked (2026-09-13)

`30/30 PASS` — `apps/api/scripts/verify_registryfix.py`. Corrects the five
confirmed-wrong `assistant_action_catalog` rows found by
`agenticdiscovery.lowrisk` (`docs/AGENTIC_SUBSTRATE_DISCOVERY.md`, Task 5) and
asserts the `escalation_reason` enum (already created, deliberately unwired).

**Module/action_key prefix drift — resolved per-key, not uniformly.** Of the
five drifting rows, only `entity.show_hierarchy` had zero references to its
key string anywhere outside its own registration — it alone was renamed, to
`entity_graph.show_hierarchy`. The other four
(`entity.link_ownership`, `entities.count`, `investments.count`,
`litellm.reload_model_cost_map`) have real code references to their current
key (verify scripts, and for `litellm.reload_model_cost_map` a live BPMN
fixture, `apps/api/fixtures/litellm_cost_map_reload.bpmn`) that a rename would
silently break — for those four, `module` was corrected instead
(`entity_graph`→`entity`, `queries`→`entities`/`investments`,
`litellm_ops`→`litellm`). `module` is write-only metadata (upserted by
`sync_catalog`, read back by nothing) so correcting it can never break a
lookup-by-key. Fixed in both the source files
(`services/assistant_actions/entity_graph.py`, `queries.py`, `litellm_ops.py`)
and the live DB row; a real `sync_catalog` run (identical to `main.py`
`_startup`) was proved not to revert any of them, the registry still has
exactly 16 rows (no orphan left behind by the one rename), and every
`workflow_steps.action_registry_key` still resolves via a real join.

**`crm.draft_note.reversible` is now `true`**, matching its own
draft-and-confirm description. **New finding surfaced by the flip, not fixed
here:** `reversible` gates a real code path
(`POST /assistant/activity/{id}/undo`, `routers/assistant.py`) that now
returns HTTP 200 `"undone"` for this action without actually reversing
anything — `_save_note` never sets an `undo_token`, and `entity_notes` has no
soft-delete column in the deployed schema. Closing this for real needs a
schema change; out of scope for this sprint, recorded here so it isn't lost.

**`escalation_reason` enum** — created in an earlier Part-1 sprint, verified
live again here: exactly the six expected labels
(`budget, max_steps, tool_error, low_confidence, refused, ambiguous`), in
order. Still deliberately unwired — no column uses it yet, pending a real
agent-run table.

**Recorded, not resolved — open design question.** Across all 16 live
registry rows, `default_autonomy` currently correlates 1:1 with
`access_type` (every `write` row is `confirm`, every `read` row is `auto`,
zero exceptions). Whether that is a deliberate invariant this catalog should
enforce, or simply an artifact of a 16-row sample, is an open question this
sprint does not resolve.

---

## 0000000000000000. Secret exposure — `app_service` DB password printed to a tool-output transcript; rotated (2026-09-10)

**What happened.** While setting `DATABASE_URL` in Doppler (`hollisworks`/`prd`)
to `app_service`'s connection string as part of the RLS enforcement cutover
(see the entry immediately below), the command used to verify the write's
success — `doppler secrets set` followed by displaying its own confirmation
output — printed `app_service`'s database password (at least a substantial
fragment of it, host-adjacent) into the visible tool-call output for this
session. The filtering used to redact it (`grep -v "postgres\|app_service"`)
was wrong: it matched on ROLE NAMES, not on the secret VALUE, so the
confirmation table's `VALUE` column passed straight through. This violates
this project's own standing rule (`CLAUDE.md` / `docs/PROJECT_STATUS.md` —
"no secret value is ever printed... refers to secrets by name only").

**Response.** Flagged immediately, before any further action. Joe rotated
`app_service`'s password directly in Supabase and updated both `DATABASE_URL`
and `APP_SERVICE_DATABASE_URL` in Doppler to the new value. The exposed
password is therefore no longer valid — whatever leaked into this session's
output can no longer authenticate against the database.

**Real complication, not yet resolved as of this entry:** the ROTATED
credential itself does not currently authenticate either
(`InvalidPasswordError`, confirmed twice, five seconds apart — see the
cutover entry below for the full sequence). This is a separate, second issue
from the exposure — under investigation, not yet root-caused. Candidate
causes: a Supabase connection-pooler credential-cache propagation delay
after a role password change (the DSN hits `aws-1-us-east-1.pooler.
supabase.com:6543`, a pooled connection, not a direct one), or a
transcription mismatch between the value set in Supabase and the value that
landed in Doppler (e.g. special-character encoding). Not yet distinguished.

**Process fix applied going forward:** every subsequent Doppler write in this
session uses `--silent` and never displays the command's own confirmation
output. Verification of a secret's value is done only via structural
comparison (role/host/port/length) or a SHA-256 hash prefix — enough to prove
two secrets match or that a rotation landed, without the value itself ever
touching visible output.

---

## 000000000000000. RLS enforcement cutover — DATABASE_URL now `app_service`, not `postgres` (2026-09-10)

**STATUS: LIVE IN DOPPLER, CODE-LEVEL PROOF COMPLETE — Render restart
confirmation still owed (see below).** `DATABASE_URL` genuinely connects as
`app_service` (`rolbypassrls=false`), confirmed by a real connection
immediately before every proof in this entry. This is the actual, current
value of the real, live Doppler secret — not a per-process override.

**Full sequence, same day:**
1. `DATABASE_URL` set to `app_service`'s connection string. While confirming
   the write, `app_service`'s password was accidentally printed to this
   session's tool output (see the exposure entry directly above this one).
2. Joe rotated `app_service`'s password in Supabase and updated Doppler.
3. The rotated credential did not authenticate at first — rolled back
   immediately to the `postgres` backup per this sprint's own "if anything
   breaks, roll back immediately" rule, confirmed working.
4. Joe confirmed directly that `app_service` now authenticates
   (`SELECT current_user` → `app_service`).
5. `DATABASE_URL` re-checked before trusting that: it had drifted to a THIRD,
   broken value (neither the postgres backup nor the working app_service
   value) — cause not identified, but caught by re-verifying rather than
   assuming. Set explicitly to `APP_SERVICE_DATABASE_URL`'s current
   (confirmed-working) value and re-verified live.
6. Tasks 4–6 run for real against that live value (detail below). A genuine
   second bug was found and fixed along the way — `main.py`'s own FastAPI
   startup hook (`sync_catalog`, seeding `assistant_action_catalog`) had
   never set RLS context either, same root shape as the Task 2 scheduler
   bug, invisible until `app_service` actually enforced it. See "A second
   real bug, found live" below.

**Task 3 step 3 (Render redeploy) — NOT achievable from this environment,
disclosed rather than skipped.** See "Render redeploy — a real, disclosed
gap" below; unchanged from the earlier attempt, re-confirmed this pass (no
Render API key, CLI, MCP connector, or deploy-hook secret anywhere in
Doppler).

### The finding

The deployed application's `DATABASE_URL` connected as the `postgres` role
(`rolbypassrls = true`), not `app_service` (`rolbypassrls = false`). Every RLS
policy across public + portfolio was therefore not enforced by the running
application — confirmed live, not assumed. `services/database.py`'s own
module docstring had documented this as a known, deliberate, not-yet-taken
step since the RLS Phase 1 sprint.

### Rollback — the one real, exact step

The pre-cutover `DATABASE_URL` value (the `postgres`-role connection string)
is preserved, byte-for-byte, under its own Doppler secret:
**`DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`** (`hollisworks` project, `prd`
config) — verified to match the pre-cutover `DATABASE_URL` exactly (same
role, host, port, and length) before the cutover was applied.

To roll back, run:

```bash
doppler secrets set DATABASE_URL \
  --project hollisworks --config prd \
  "$(doppler secrets get DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP --plain --project hollisworks --config prd)"
```

That is the entire rollback — one command, restores the exact pre-cutover
value, no guessing at a prior Doppler secret version. After running it, the
Render services (`2ndactcapital-api`, `2ndactcapital-workflow-scheduler`)
need to actually pick the reverted value up — see "Render redeploy — a real,
disclosed gap" below; the same gap that applies to the forward cutover
applies to the rollback.

### The one real code fix this cutover required (Task 2, already merged)

Discovery found exactly one legitimate cross-org code path that would have
broken silently under `app_service`: `apps/api/workflow_scheduler_tick.py`
opens a raw `asyncpg` connection with no RLS context, used for the
scheduler's platform-wide (all-orgs) due-trigger scan. Every table it
touches already carried an `OR is_super_admin` RLS carve-out; the raw
connection just never set it. Fixed via `services.database.platform_scope`
— a small helper that sets `SET LOCAL app.is_super_admin = 'true'` fresh,
inside its own transaction, for each individual platform-scope query
(`load_due_candidates`, `_workflow_in_progress`, `_claim`,
`dismiss_orphaned_run_alerts`).

**A first attempt at this fix was wrong, and testing (not review) caught
it**: a single session-level `set_config(..., false)` issued once when the
connection opened passed a single-org smoke test, then failed a two-org one.
Under Supabase's transaction-mode pooler, this raw connection's physical
backend was observably the SAME backend shared with the ordinary RLS-aware
pool (`get_pool()`) used to fire each trigger's run — and the moment that
pool's transaction committed, the pooler reset the shared backend's session
GUCs, silently wiping the session-level setting mid-tick. `platform_scope`'s
`SET LOCAL`-per-transaction approach is the same pattern the app's own
per-request RLS context already uses (`_apply_rls_settings`), applied here
for the first time to a platform-scope job. Proven via
`apps/api/scripts/verify_schedulerappservicefix.py` (15/15 PASS): the exact
two-orgs-one-tick shape that caught the bug, run again against the fix.

### A second real bug, found live (fixed same session)

`main.py`'s `@app.on_event("startup")` hook calls
`REGISTRY.sync_catalog(pool, "00000000-0000-0000-0000-000000000001")` to seed
`assistant_action_catalog` on every app start — through the ordinary
RLS-aware pool, but WITHOUT ever calling `set_rls_context` first. Under
`postgres` this always silently worked (bypass). The very first time this
code path ran against `app_service` (this sprint's own smoke test starting a
real `TestClient` against `main.app`), it failed:
`new row violates row-level security policy for table
"assistant_action_catalog"` — caught only because the app logs it as
"non-fatal" rather than crashing, meaning this would have started silently
failing on every real Render restart post-cutover with no visible error.
Fixed by scoping the write's RLS context to the same org the row's own
`org_id` already targets (`set_rls_context(seed_org_id, False)` /
`reset_rls_context`, mirroring `_fire()`'s existing pattern in
`services/workflow_scheduler.py`). Re-ran the smoke test after the fix — the
failure line is gone, `sync_catalog` succeeds.

This is the second time in this same cutover that a code path's dependence on
the `postgres` bypass was invisible until testing — not review — exercised it
for real. Worth noting for any future work in this area: grepping for raw
`asyncpg.connect()` (what Task 1c's discovery did) does NOT catch this shape
of bug, because it goes through the normal RLS-aware pool; the gap is a
*missing* `set_rls_context` call, not a bypassed pool. Any other startup-time
or system-triggered write should be checked for the same shape before being
trusted post-cutover.

### Tasks 4–6 — real proof, live `app_service`, `apps/api/scripts/verify_rlscutover.py`

**21 PASS, 0 FAIL, 2 FIND.**

- **Task 4, smoke test, reads**: all 5 named modules — portfolio
  (`GET /portfolio/positions`), workflow (`GET /admin/workflows`), fee
  (`GET /fee-schedules`), TA model (`GET /modeling/ta/defaults`), UDF
  (`GET /udf/definitions`) — HTTP 200 through the real ASGI app, real
  `app_service` connection.
- **Task 4, smoke test, writes**: a real UDF definition created
  (`POST /udf/definitions`, HTTP 201), re-read on an INDEPENDENT connection
  to confirm genuine persistence (not just trusting the 201), then
  deactivated (a second real write, HTTP 200). Portfolio/fee/TA-model write
  coverage was explicitly deferred and reported as a `[FIND]`, not silently
  skipped: org 2nd Act has zero rows in `portfolio.assets`, so a position
  `POST` has no valid `asset_id` to reference without also fabricating
  asset/security fixtures — out of scope for this pass. Workflow's write
  path is proven by Task 6 below and by `verify_schedulerappservicefix.py`.
- **Task 5, cross-org isolation, contrasted with the old bypass**: two
  fixture `entities` rows created, one per org, confirmed to genuinely exist
  at the DB level. A real 2nd Act `org_admin`, through the real app: CAN
  read their own org's fixture (HTTP 200 — not a blanket refusal) and CANNOT
  read Hollisworks' fixture (HTTP 404) — the exact same query that would
  have returned the row under the old `postgres`-bypass `DATABASE_URL`.
- **Task 6, real scheduler tick, post-cutover, multi-org**: two due
  triggers, one in each org, examined in the SAME tick, with `DATABASE_URL`
  NOT overridden — this ran against whatever was really live. Both fired
  through the real engine, each got its own `workflow_runs` row
  (`status='completed'`), each isolated to its own `org_id`, each trigger's
  `occurrence_count` incremented exactly once. This is the exact two-org
  shape that caught Task 2's original bug, now proven live.
- Teardown: zero leftover fixture rows, by id.

### Render redeploy — a real, disclosed gap

This environment has no Render API credential, no Render CLI, and no Render
MCP connector (a standing, previously-documented gap — see
`docs/DEVELOPMENT_ENVIRONMENT.md` and `docs/LITELLM_DISCOVERY_FINDINGS.md`;
re-checked at cutover time and still absent). Updating the `DATABASE_URL`
secret in Doppler is the real, complete action on the Doppler side — Doppler
is this project's declared source of truth (see `CLAUDE.md`) — but whether
Render's two live services (`2ndactcapital-api`,
`2ndactcapital-workflow-scheduler`) actually restart and pick up the new
value depends on a Doppler→Render sync integration this environment cannot
inspect, trigger, or confirm. `render.yaml` declares `DATABASE_URL` as
`sync: false` for both services, meaning the blueprint itself does not set
the value — whatever Render is actually running is whatever the Render
dashboard holds, synced or manually set, outside this environment's
visibility.

**What this sprint could verify, and did**: the real application code
(`services/`, `routers/`), against the real deployed database, with
`DATABASE_URL` genuinely pointed at `app_service` for the verifying
process — the same method every RLS-related sprint in this project's history
has used, because this gap has been standing since before any of them.
**What this sprint could NOT verify**: that the live Render services picked
up the change automatically, or even that they have deployed the code fixes
in this entry (Task 2's `platform_scope` fix, and this entry's
`sync_catalog` fix) at all.

**Action needed from Joe, in this order:**
1. Confirm `apps/api/main.py`, `services/database.py`,
   `services/workflow_scheduler.py`, and `services/workflow_todos.py` on
   `origin/main` include both fixes (git log should show them; check before
   assuming a redeploy would even include them).
2. Manually trigger (or confirm) a restart of both `2ndactcapital-api` and
   `2ndactcapital-workflow-scheduler` from the Render dashboard.
3. Confirm each service's live `DATABASE_URL` shows `app_service` as the
   connecting role — a raw `SELECT current_user` from either service's own
   context is the simplest real check.
4. Watch the workflow-scheduler's next few real ticks (every 5 minutes) for
   the `[scheduler] FIRED` / `skip` / `SKIP-OVERLAP` log lines this sprint's
   proof relied on — a platform-wide scan reporting zero examined triggers
   on a tick where triggers are known to be due is the exact silent-failure
   shape Task 1c/Task 2 exist to prevent.

### Verification

`apps/api/scripts/verify_rlscutover.py` — **21 PASS, 0 FAIL, 2 FIND** (see
Tasks 4–6 above for the breakdown). `apps/api/scripts/verify_schedulerappservicefix.py`
— 15/15 PASS (Task 2's own proof, run separately, earlier in this sprint).

---

## 00000000000000. Altruist Sprint 6 — Realtime API webhook receiver: DISCOVERY ONLY, STOPPED per standing rule (2026-09-07)

`0/0 PASS + 6 FIND + 1 BLOCKED (design assertions) via
`apps/api/scripts/verify_altruist_sprint6_realtime_webhook_receiver.py`
(discovery-only script; see its own header for the full breakdown of what it
*could* still verify live). **No webhook receiver code was built this
sprint** — the sprint prompt's own standing rule required stopping after
Task 1 if no existing table is suitable for webhook event-id
deduplication/logging, and Task 1 confirmed, live, that none is.

**What Task 1 found.** Sprints 1-5 built the Open API surface (OAuth2
connection, identity resolution, positions/transactions sync, connection
lifecycle, sync orchestration) — all authenticated, app-user-triggered. A
Realtime API webhook receiver is fundamentally different: it is called BY
Altruist, not by a logged-in app user, so it needs signature verification
against a shared secret, not `rbac.require_permission`. This app has no
existing precedent for that — the only unauthenticated-route mechanism is
`PUBLIC_PATHS` (pre-auth, but genuinely UNSIGNED: `theme/public`,
`tenant/resolve`, `marketing/*`, `enroll/validate` all serve only public
metadata or single-use tokens, none verify a cryptographic signature).

A live `information_schema` search for any table shaped for inbound webhook
event dedup/logging (`webhook`, `event`, `integration_log`, `dedup`,
`inbound` name patterns) found no genuine match:

  * `domain_events` / `domain_event_deliveries` (Domain event emission
    sprint) is the closest name match but the wrong shape: `org_id` and
    `source_id` are both NOT NULL, and it is the OUTBOUND publish side for
    `workflow_triggers` — an inbound webhook for an unrecognized identifier
    must be logged with NO org, which this table cannot represent, and there
    is no unique constraint to dedupe an external event id against.
  * `cost_events` / `revenue_events` / `v_profitability_events` — real, but
    fee/billing domain, unrelated.
  * `litellm."LiteLLM_WorkflowEvent"` — a third-party (LiteLLM) schema, not
    ours to write into for an unrelated integration.

**Needed shape, owed as a real schema decision before Task 2 can run:**

```sql
CREATE TABLE public.altruist_webhook_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        text NOT NULL,       -- Altruist's own event id; the dedup key
    org_id          uuid,                -- NULL when the payload's identifier is unrecognized
    event_type      text,                -- best-effort, payload shape unconfirmed
    received_at     timestamptz NOT NULL DEFAULT now(),
    processed_at    timestamptz,
    raw_payload     jsonb NOT NULL,
    UNIQUE (event_id)
);
```

`org_id` nullable is deliberate (unlike every other table in this app, per
CLAUDE.md's org_id rule) — an unrecognized payload must still be logged
without guessing an org, and a NULL org_id must never be silently defaulted
to a real one as a stand-in for "unknown."

**Also confirmed live, independent of the STOP condition:** `resolve_
identity`/`sync_resolved_accounts` (Sprints 2/3) are callable as
`(conn, *, org_id, environment, ...)` — real, introspected signatures — so
once the schema exists, Task 2's dispatch logic can call them directly for
one identified org without re-deriving their behavior. No reference to
Altruist's real Realtime API webhook signature scheme or payload shape
exists anywhere in this repo (checked, confirmed live) — Task 2, when it
runs, should use the HMAC-SHA256-over-raw-body default already specified in
the sprint prompt, explicitly flagged unconfirmed.

**Sandbox smoke test — BLOCKED, re-confirmed live** (same `doppler secrets
--only-names` check as Sprints 1-5). No `ALTRUIST_*` secrets exist,
including any webhook secret.

**Regression — Sprints 1-5's own verify scripts were re-run and all report
clean**, unaffected, since this sprint made no code changes.

**Sprint 0's outreach should now also explicitly request Altruist's real
webhook documentation and signature scheme** — not just sandbox
credentials — since this sprint proved that gap blocks Task 2 from even
starting, independent of the schema decision above.

**Not held for merge in the usual `.structural` sense — there is no code to
merge.** This entry, the verify script (which re-confirms the STOP
condition live and will need re-running once the schema question above is
answered), and the schema decision itself are the actual deliverable.

---

## 0000000000000. Altruist Sprint 5 — sync orchestration endpoint + auto-trigger on connect BUILT; sandbox smoke test BLOCKED (2026-09-07)

`32/34 PASS, 1 FIND, 0 FAIL, 1 BLOCKED` —
`apps/api/scripts/verify_altruist_sprint5_sync_orchestration.py`. HELD for
manual review (`.structural`). Sprint 4 wired the connection lifecycle
(connect/callback/status/disconnect) into real HTTP endpoints but
deliberately left identity resolution (Sprint 2) and positions/transactions
sync (Sprint 3) unreachable from the app — callable only by their own verify
scripts. This sprint closes that gap.

`POST /api/v1/altruist/sync` (`routers/altruist_connection.py`) — admin-only
(reuses `manage_custody_connections`, the same permission connect/disconnect
already require; no new permission — see below), calls
`require_active_connection` then `resolve_identity` (Sprint 2) then
`sync_resolved_accounts` (Sprint 3) for the caller's org, and returns a real
summary pulled from what those functions actually reported:
`households_seen`, `accounts_resolved`, `accounts_unmatched`,
`accounts_synced`, `positions_created`/`positions_skipped_duplicate`,
`transactions_created`/`transactions_skipped_duplicate`.

**Auto-trigger on connect.** The same sync now fires automatically right
after `altruist_callback` stores a new connection, via FastAPI
`BackgroundTasks` — a genuine, already-used mechanism in this app
(`routers/entities.py`'s note-extraction background task, `routers/
investment_profile.py`'s profile-extraction background task), not a new
pattern. The background task (`_run_auto_sync`) acquires its own pool
connection (the request's own connection is already released by the time it
runs) and never raises — a sync failure is swallowed and logged, never
visible to or blocking the caller who merely connected; the connection
itself is always correctly stored regardless of how the auto-sync goes.

**No new permission.** Task 1 checked this app's live permission catalog:
most resources (`portfolio`, `documents`) use a 2-tier view/manage split;
`workflows` is the only 3-way split, and that's for genuinely distinct
capabilities (author vs configure-triggers vs view-runs). Triggering a sync
is an operational action on the same `custody_connections` resource
connect/disconnect already gate, not a distinct capability —
`manage_custody_connections` is reused, not a new one invented.

**Sprint 1's stale verify-script assertion (flagged as a FIND in Sprint 4's
own entry below) is now fixed.** `check_task4a_guard`'s `4a-4` previously
asserted `call_households` on a connected fixture raises
`NotImplementedError`; Sprint 2 made that assertion stale once it
implemented the function for real (a genuine, reachable-host HTTP attempt
now raises `AltruistOAuthError` instead — confirmed live, the sandbox host
resolves and responds 401 on synthetic credentials). Sprint 1's verify
script now reports clean (24/26 PASS + 1 BLOCKED + 1 FIND) on an unmodified
checkout.

**[FIND] `sync_resolved_accounts`'s summary is not environment-scoped.**
`portfolio.external_references` (the resolved-account crosswalk) has no
environment column, so calling `/altruist/sync` for one environment
re-syncs EVERY resolved account for the org, including ones originally
resolved under a different environment's connection — harmless in practice
(an already-synced account is a no-op skip-duplicate), but the response's
`accounts_synced`/position/transaction counts can include more than just
the environment named in the request. Confirmed live in this sprint's own
round-trip test (assertion `5-6g`).

**Sandbox smoke test — BLOCKED, not attempted, re-confirmed live** (same
`doppler secrets --only-names` check as Sprints 1-4). The permission
refusal/control pair, the full round trip (connect → synthetic callback →
auto-sync fires → both the endpoint's own response and an independent DB
re-read show correct resolved account/positions/transactions), idempotency
(called twice through the real endpoint, DB row counts diffed — not
asserted), the no-active-connection 409 (reproduced first, before any fix
was proven), and cross-org isolation are all exercised end-to-end through
the real ASGI app (`starlette.testclient.TestClient` against `main.app`),
with `call_households`/`fetch_accounts_for_household`/`call_positions`/
`call_transactions` monkeypatched on the CALLING modules' own bound names
(`services.altruist_identity`/`services.altruist_positions_sync` — the same
lesson Sprint 4 documented for `exchange_code_for_tokens`: a bare import
means patching `services.altruist_oauth` directly would be a silent no-op).
Sprints 1-4's own verify scripts were re-run as a regression check
afterward, against a clean DB, and all report clean.

**Everything through Sprint 5 is still proven only against synthetic/
monkeypatched Altruist responses, pending Sprint 0's real sandbox
credentials** — no live Altruist call has been made by any sprint in this
integration yet.

---

## 000000000000. Altruist Sprint 4 — connection lifecycle API + automatic token refresh BUILT; sandbox smoke test BLOCKED (2026-09-06)

`26/27 PASS, 0 FIND, 0 FAIL, 1 BLOCKED` —
`apps/api/scripts/verify_altruist_sprint4_connection_api.py`. HELD for manual
review (`.structural`). Sprints 1-3 built the full OAuth/identity/sync engine
as service-layer Python only, called directly by verify scripts — nothing was
reachable over HTTP. This sprint exposes exactly the connection lifecycle (not
identity resolution or positions/transactions sync, which stay internal) via
a new router, `routers/altruist_connection.py`:

  * `POST /api/v1/altruist/connect` — admin-only, creates an OAuth state row
    and returns Altruist's `authorize_url` for the frontend to redirect to.
  * `GET /api/v1/altruist/callback` — the route Altruist redirects back to;
    consumes the state, exchanges the code for tokens, persists the connection.
  * `GET /api/v1/altruist/status` — the caller's org's connection status
    (`connected`, `environment`, `status`, `token_expires_at`,
    `last_refreshed_at`, `scope`, masked `*_last4` markers) — proven, by
    parsing the actual response JSON (not by eye), to never contain a raw
    token/secret value in any field.
  * `POST /api/v1/altruist/disconnect` — admin-only, bitemporal close
    (`system_to`, matching `store_new_connection`'s own archival convention
    for this table — never a hard delete).

**Permission check matches an existing, already-shipped feature exactly, per
the sprint's own instruction not to invent a new pattern**: `custody_import.py`
(the file-upload custodian ingestion path) is the closest real analog — same
`services.permissions.get_user_id` (claims-derived) + `services.rbac.
require_permission`/`has_permission` (database-backed RBAC, `is_super_admin`
bypass checked first inside `rbac.has_permission` itself) combination, not
`admin.py`'s DB-lookup `ensure_user` chain (a user-management-shaped pattern,
not a custodian-connection-shaped one). Two new permissions were added to the
existing `permissions` catalog — data only, no DDL, matching how every other
resource (`spv`, `workflows`, `roles`) was added incrementally over time:
`view_custody_connections` / `manage_custody_connections`, both granted to the
real, already-deployed `admin` role.

**org_id** comes from `routers.entities.get_org_id` (JWT claims) on every
route, never the body or a path parameter — every request body is
`ConfigDict(extra="forbid")` so a body that even declares an `org_id` field
fails validation mechanically.

**[FIND] No existing "connect a custodian" / integration-settings UI or API
pattern exists anywhere in this app**, for Altruist or any other custodian —
confirmed by a thorough search of `apps/api`, `apps/web`, and `docs/`. The one
custodian-adjacent UI, `CustodyImportWizard.jsx`, is a file-upload wizard, a
different shape entirely from a live OAuth connect/disconnect/status flow.
This sprint's routes are new, not matched to any prior UI/API convention
beyond Sprint 1-3's own service-layer design.

**[FIND] No generic recurring-job/scheduler mechanism exists in this app.**
The one real cron process (`workflow_scheduler_tick.py`, Render cron, every
5 minutes) is purpose-built to scan `workflow_triggers` for BPMN workflow
runs — it has no generic "run this job on a timer" registry a different
subsystem could hook into. `docs/WORKFLOW_SCHEDULER_DESIGN_V1.md`, referenced
by CLAUDE.md as a built design doc, **does not exist** (confirmed by
`docs/OUTSTANDING_TODO_LIST.md`, a real, separate gap this sprint did not
introduce). Per the sprint's own authorized interim answer, automatic token
refresh ships as an **on-demand, just-in-time check** instead: a new
`ensure_fresh_token` (`services/altruist_oauth.py`) refreshes in place
whenever a token is within 5 minutes of `token_expires_at`, wired into BOTH
`require_active_connection` (default `auto_refresh=True` — every future real
Altruist API call already goes through this guard) and the `/altruist/status`
endpoint directly. A failed refresh on a token that has NOT yet actually
expired is swallowed and the still-valid token is returned as-is (a
transient refresh failure must not turn a working connection into a hard
failure); a failed refresh on an ALREADY-expired token re-raises. Proven via
an independent re-read (a connection distinct from the one that triggered
the refresh) that `token_expires_at`/`last_refreshed_at` both actually
advanced — not just "the call didn't error". **Swapping this for a real
timer later needs no caller change** — `ensure_fresh_token` is the only
function that would move. Sprint 5 (or a dedicated follow-up) owns actually
building that timer if/when it's judged worth a second Render cron service.

**[FIND] Sprint 1's own verify script has a pre-existing, unrelated failure**,
confirmed identical on `HEAD` before this sprint's changes (`git stash` +
re-run): `check_task4a_guard`'s assertion `4a-4` expects
`call_households(...)` on a connected fixture to raise `NotImplementedError`
— true when Sprint 1 wrote it (the function was a stub), false since Sprint 2
actually implemented `call_households` for real. The call now reaches a real,
live `httpx` request to Altruist's sandbox host (which resolves and responds
HTTP 401 — network egress to Altruist's stage1 host is reachable from this
environment, just unauthorized on synthetic credentials) and raises
`AltruistOAuthError` instead, aborting the rest of that script's run. Not a
regression from this sprint — a stale assertion Sprint 1 left behind once
Sprint 2 shipped. Sprint 2 and 3's own verify scripts re-ran clean at their
original counts (19/20 PASS + 1 BLOCKED; 21/22 PASS + 1 BLOCKED) — this
sprint's `require_active_connection` signature change (`auto_refresh=True`
default) is backward-compatible with every existing fixture, since none of
Sprint 1-3's fixture tokens fall within the 5-minute refresh threshold.

**Sandbox smoke test — BLOCKED, not attempted, re-confirmed live** (same
`doppler secrets --only-names` check as Sprints 1-3). The full connect →
callback → status → disconnect round trip, the four state-rejection cases,
the permission refusal/control pairs, cross-org isolation, and the token-
refresh proof are all exercised end-to-end through the real ASGI app
(`starlette.testclient.TestClient` against `main.app`, only `verify_token`
stubbed) with `exchange_code_for_tokens`/`refresh_access_token` monkeypatched
at the module level — never a live Altruist call.

---

## 00000000000. Altruist Sprint 3 — positions/transactions sync for resolved accounts BUILT; sandbox smoke test BLOCKED (2026-09-06)

`21/22 PASS, 1 FIND, 0 FAIL, 1 BLOCKED` —
`apps/api/scripts/verify_altruist_sprint3_positions_transactions_sync.py`.
HELD for manual review (`.structural`). Adds `call_positions`/
`call_transactions` (`GET /v2/positions?account_id=...`, `GET /v2/
transactions?account_id=...`) to `services/altruist_oauth.py`, and the new
`services/altruist_positions_sync.py` — the sync pass itself, for every
Altruist account Sprint 2 already resolved.

**Live.** For every Altruist account with a real `portfolio.external_
references` row (`source_system='ALTRUIST'`, `record_type='account'`) —
enumerated directly from that table, not by re-running Sprint 2's resolution
— this pulls current positions and transaction history and writes them into
the SAME tables `services/portfolio_import` (Phase B's flat-file importer)
already uses for every other custodian/reporting-tool source:
`portfolio.positions` / `portfolio.transactions`. No new table. An account
still sitting in `public.account_import_exceptions` (unresolved) is never
touched — the enumeration query has no path to it — proven against a real
mixed fixture set (one resolved, one unresolved) rather than assumed by
construction; the synthetic transport additionally asserts it is NEVER
called with the unresolved account's id. Idempotent by the same pre-insert-
read-of-`external_references` pattern Phase B established: a position's
external id is Altruist's own id (or a content hash) suffixed with the
sync's as-of date, so a same-day re-sync is a no-op while a later day's
sync is a legitimate new bitemporal snapshot; a transaction's external id
has no date suffix, since a real-world trade only happens once.

**[FIND] — neither `portfolio.positions` nor `portfolio.transactions` has a
`custodian_code`/`custodian_system` column.** The sprint's instructions said
to tag synced rows with the seeded `reference_data` codes `custodian='ALT'`
/ `custodian_system='ALT-DEF'`. Measured against the live schema: neither
table has anywhere to put those literal codes. The only provenance column
either table has is `source_system`, and on `portfolio.positions` that
column carries a real, deployed CHECK constraint (`positions_source_chk`)
whose vocabulary already contains `'altruist'` (lowercase) — added ahead of
this sprint for exactly this integration. Per the standing rule against
improvising ledger-adjacent DDL unsupervised, this sprint does NOT add a
column: it uses `source_system='altruist'`, the one already-live, DB-legal
token, as the real tag on both tables, and exposes `CUSTODIAN_CODE`/
`CUSTODIAN_SYSTEM_CODE` (`'ALT'`/`'ALT-DEF'`) as module constants a future
API envelope can attach for display, per CLAUDE.md's Rule 1 — not by
duplicating the config-table code onto every ledger row. If a future sprint
wants the code stored literally on each row, that is a real, known column
addition still owed, not a decision this sprint made silently.

**A transaction with no matching position synced this run, and no existing
current position to attach to, is skipped and recorded as a row error** —
never given a fabricated zero-quantity position to satisfy the FK. This is a
real, deliberate scope boundary: Altruist's transaction history can reference
a security that was fully liquidated before the sync window, and inventing a
position for it would misrepresent what was actually held.

**Sandbox smoke test — BLOCKED, not attempted, re-confirmed live** (same
`doppler secrets --only-names` check as Sprint 1/2). No live Altruist call
was made; `call_positions`/`call_transactions` were instead exercised
end-to-end against an injected `httpx.MockTransport` returning a synthetic,
documented-or-best-guess-shape payload, exactly like Sprint 2's precedent.

**Still unconfirmed, prominently flagged:** the `/v2/positions` and
`/v2/transactions` paths (and every field name in their response shape) are
a best-guess convention, not confirmed against a live Altruist spec — no
reference doc exists in this repo (checked) and no sandbox access exists to
confirm against a live `/reference` page. Isolated in `_API_PATHS` in
`services/altruist_oauth.py` for a one-edit fix once real access exists,
same as every other Altruist path in this codebase.

---

## 0000000000. Altruist Sprint 2 — household/account identity resolution BUILT; sandbox smoke test BLOCKED (2026-09-06)

`19/20 PASS, 0 FIND, 0 FAIL, 1 BLOCKED` —
`apps/api/scripts/verify_altruist_sprint2_identity_resolution.py`. HELD for
manual review (`.structural`). Replaces Sprint 1's `call_households`
`NotImplementedError` stub with a real `GET /api/v2/households` call, adds
`fetch_accounts_for_household` (`GET /v2/accounts?household_id=...`), and adds
`services/altruist_identity.py`'s `resolve_identity` — the identity-resolution
pass itself.

**Live.** For each Altruist account under each Altruist household: a lookup
against `portfolio.external_references` (`source_system='ALTRUIST'`,
`record_type='account'`) either resolves to an existing Hollisworks
`public.accounts.id` (and refreshes `last_seen`), or — per the Architecture
Decisions' "Hollisworks' own households/entities stay authoritative" rule —
is routed to `public.account_import_exceptions` for manual review.
Hollisworks households/entities/accounts are never auto-created. Both writes
are idempotent by content (re-running the identical pass twice, proven live
in the verify script, produces zero new `external_references` rows and zero
new `account_import_exceptions` rows on the second pass).

**[FIND] — households are not, and structurally cannot be, a crosswalk
target.** Both `portfolio.external_references.record_type` and
`public.account_import_exceptions.record_kind` carry deployed CHECK
constraints admitting `'account'` (plus asset/position/transaction on the
former, balance/flow on the latter) but never `'household'`. This mirrors an
existing precedent in `services/portfolio_account_link.py` (fee32), which
built an entirely separate table rather than force-fit
`account_import_exceptions` for the same NOT-NULL-`batch_id` reason. Per this
sprint's standing rule against improvising DDL, household identity is not
persisted anywhere: `GET /api/v2/households` is used purely to enumerate
household ids to traverse into `GET /v2/accounts?household_id=...` — once an
account resolves, its Hollisworks household is already known via
`public.accounts.household_id` on the resolved row, so a second crosswalk
entry would be redundant even if the constraint allowed one.

**A second, real, previously-undocumented constraint discovered live:**
`account_import_exceptions.batch_id` is NOT just a NOT NULL column — it is a
real foreign key to `public.account_import_batches`, a table shaped for a
custodian CSV upload (`custodian_code`, `source_filename`, `row_count`), not
an API sync pass. No new table was needed: one `account_import_batches` row
per org is reused (`custodian_code='ALTRUIST'`, looked up before being
created), exactly like any other existing-table write.

**Sandbox smoke test — BLOCKED, not attempted, re-confirmed live** (same
`doppler secrets --only-names` check as Sprint 1). No live Altruist call was
made; the HTTP-call code paths in `call_households`/`fetch_accounts_for_
household` were instead exercised end-to-end against an injected
`httpx.MockTransport` returning a synthetic, documented-shape payload — a
stronger proof than Sprint 1's own verify script, which tested token
persistence directly and never actually drove an HTTP-calling function
through a transport.

**Sprint 3 (positions/transactions sync) is unblocked for the parts that
don't require a live call** — an Altruist account can now be resolved to its
Hollisworks `accounts.id` (or routed for manual review) without ever needing
a real sandbox response; whatever Sprint 3 does with `portfolio.positions`/
`portfolio.transactions` per resolved account remains blocked on the same
sandbox-credential gap as this sprint's Task 3.

---

## 000000000. Altruist Sprint 1 — OAuth2 connection scaffold BUILT; sandbox smoke test BLOCKED (2026-09-06)

`24/26 PASS, 1 FIND, 0 FAIL, 1 BLOCKED` —
`apps/api/scripts/verify_altruist_sprint1_openapi_connection.py`. HELD for
manual review (`.structural`). This is the first real Altruist Open API
integration code in the repo — everything prior
(`services/portfolio_altruist.py`, fee38's `services/altruist_one.py`)
either read a single global env-var credential or evaluated Altruist One
subscription heuristics; neither implements OAuth2 or per-org credential
storage.

**Live.** `altruist_oauth_states` (short-lived, single-use CSRF state for the
authorization-code redirect) and `altruist_connections` (per-org,
per-environment OAuth2 credential + token storage) — both org-isolation RLS,
`altruist_connections` bi-temporal (system-axis: a genuine reconnect archives
the old row via `system_to`; a routine hourly token refresh updates the
current row in place, see the migration's comment for why these are two
different axes of the same table). `services/altruist_oauth.py` implements
the full authorization-code flow: state generation/consumption,
`build_authorize_url`, `exchange_code_for_tokens`, `refresh_access_token`,
`store_new_connection`/`persist_refresh`/`get_active_connection`, and
`require_active_connection` — the pre-connection guard any future Altruist
API call site must sit behind (`call_households` was a stand-in stub in this
sprint that raised `NotImplementedError` once past the guard; Sprint 2
replaced it with a real implementation, see below).

**Sandbox smoke test — BLOCKED, not attempted.** No `ALTRUIST_*` secrets
exist anywhere in this project's Doppler config
(`doppler secrets --only-names`, project `hollisworks`) — confirmed live by
the verify script itself at run time, not assumed. No HTTP call to Altruist
has ever been made from this codebase. This blocks Task 3 only; Tasks 1, 2,
4, and 5 do not depend on it and are fully proven.

**Two real, separate blockers recorded as [FIND] in `services/altruist_oauth.py`,
neither of which is this sprint's to fix:**

1. No AWS KMS key or any existing encryption-at-rest helper exists in this
   codebase. The interim path — application-layer Fernet symmetric
   encryption keyed by `ALTRUIST_TOKEN_ENCRYPTION_KEY` — is what shipped.
   That Doppler secret does not exist yet either; this is a separate
   blocker from the sandbox-credential one. KMS envelope encryption remains
   the intended production path and should replace `encrypt_secret`/
   `decrypt_secret` without changing any caller.
2. The exact `/oauth/authorize` and `/oauth/token` path segments are an
   assumption (standard OAuth2 authorization-code convention), not confirmed
   against a live Altruist spec — none exists in-repo. Isolated to
   `_OAUTH_PATHS` for a single-point fix once real sandbox docs/access exist.

**One design nuance recorded as [FIND] by the verify script itself:**
`consume_oauth_state` raises the same exception class
(`AltruistOAuthError`) for all four rejection reasons (missing, expired,
already-used, cross-org) as designed — a caller cannot branch on exception
type to learn which reason applied. The exact message TEXT is not uniform
across all four, though: missing and cross-org happen to share one generic
message (both hit the same "row is None" branch), but expired and
already-used each carry their own distinct text. A caller that logs or
surfaces the message string verbatim, rather than catching the class and
emitting one fixed response, would leak more than the class-level design
intends. Worth a follow-up if a real callback endpoint is built directly on
top of this message text rather than the exception class.

**Sprint 2 (identity resolution) is unblocked for the parts that don't
require a live call** — the connection/credential storage layer, the
pre-connection guard, and the encryption plumbing are all proven. Anything
that needs a real Altruist API response shape (the actual `GET /v2/households`
call, real refresh-token rotation behavior) remains blocked on the same
sandbox-credential gap as Task 3.

---

## 00000000. Fee module fee43 — GL posting SHIPPED; ONE gap owed (2026-09-03)

`68/68 PASS, 1 FIND, 0 FAIL, 0 BLOCKED` — `apps/api/scripts/verify_fee43.py`.
HELD for manual review (`.structural`). Nothing here is blocked on anything
outside the codebase; this entry records the one real gap the sprint found and
deliberately did not invent its way around.

**Design-doc open question #3 is CLOSED.** RIA fee revenue posts to the
`RIA_OPERATING` ledger book and club dues to `CLUB_DUES`, via the new
`journal_entries.vehicle_kind='LEDGER_BOOK'`; SPV-scoped revenue and carry keep
posting inside their own SPV's books (`vehicle_kind='SPV'`, unchanged). This
closes fee36's F4o stub and fee42b's 6l.

**The one gap — no GP legal entity exists.** `entity_type` has a `gp` enum
value, but ZERO `entities` rows use it and `spvs` has no GP/manager/sponsor
column. Carry therefore books inside the SPV's own book as an expense
(`5500 Carried Interest`) credited to the existing `2100 Due to Affiliate` —
a payable to the manager, NOT an equity allocation to a GP capital account.
That is the correct achievable treatment today and it is what shipped. If the
GP ever needs its own capital account, that is a real modelling decision
(entity + capital-account plumbing), not a posting-template change.

**Two premises in the sprint brief were wrong, and the code follows what is
actually deployed, not the brief:**

1. The brief said `chart_of_accounts` "has no advisory-fee or club-dues revenue
   account". Four revenue accounts were needed, not the two it sketched —
   `4400/4500/4600/4700` — because fee39 already resolves fee lines to three
   different RIA revenue types (ADVISORY / PLANNING / PLACEMENT). Collapsing
   them would have made `revenue_events` and the GL impossible to reconcile
   line-for-line.
2. The brief expected the chart to have a `parent_code` hierarchy to slot into.
   It does not — all 20 pre-existing rows have `parent_code` NULL. The real
   convention is a flat 4-digit code banded by `account_type`, and the new
   accounts follow that.

**`v_capital_accounts` is still broken and fee43 did NOT fix it** — see the
entry at the bottom of this file, now updated with the measured answer.

**Not done, deliberately:** runs POSTED before fee43 have no journal entries
and do not acquire any. Backfilling history is an explicit decision, not an
oversight. `revenue_events.journal_entry_id` is also left NULL — that column is
exactly the revenue-to-GL link a reconciliation wants, and wiring it is fee39's
territory, scoped out of this sprint. Both are worth picking up.

---

## 00000000. Fee module fee42b — SPV carry BUILT; THREE items owed (2026-09-02)

`111/119 PASS, 8 FIND, 0 FAIL, 0 BLOCKED` —
`apps/api/scripts/verify_fee42b.py`. Nothing here is blocked on anything
outside the codebase. Recorded because it CLOSES the gap the event-emission
entry below opened, and OPENS three tracked follow-ups.

**What closed.** `spv_realization` now has its first real subscriber.
`apps/api/fixtures/spv_realization_carry_proposal.bpmn` + the
`spv_carry.propose_from_realization` registry action turn a posted `dist_gain`
into a DRAFT `spv_carry_run` with one priced line per allocated investor, with
no human in the loop and no path past DRAFT. `services/spv_carry.py` is the
pure four-tier waterfall (zero DB access, fee35's discipline);
`services/spv_carry_runs.py` is the DB half and the DRAFT → PREVIEW →
ADVISOR_APPROVED → COMPLIANCE_APPROVED → POSTED lifecycle, maker-checkered
through `assistant_activities` exactly as fee36 does it.

**A trigger row must still be created per org.** The verify script builds its
own `workflow_definitions`/`workflow_versions`/`workflow_triggers` fixture and
tears it down. **No production trigger row for `event_type='spv_realization'`
exists yet** — the BPMN and the action are shipped, the subscription is one
row, and creating it is a deliberate operational decision, not a code change.

### The three items owed

1. **A line can still be ADDED to a POSTED run.** Both deployed immutability
   triggers fire `BEFORE DELETE OR UPDATE` only; nothing covers INSERT.
   Measured live, not inferred (`verify_fee42b.py` FIND F9). A POSTED run's
   existing lines genuinely cannot be altered or removed (checks 7a–7e).
   Closing this is a Part-1 schema change — a `BEFORE INSERT` trigger on
   `spv_carry_run_lines` checking the parent run's status — and was outside
   this sprint's applied SQL.

2. **`v_capital_accounts` cannot supply cumulative capital, and structurally
   will not until GL posting ships.** It groups by
   `journal_lines.dim_member_series_id`: no `dim_member_series` table exists
   (nor any `dim_*` table), the column has no FK, it is NULL in every deployed
   row, and the view's own `WHERE` requires it NOT NULL — so it returns zero
   rows. Even populated there is no join path from that id to an SPV investor
   entity, and it also keys on `journal_entries.vehicle_id` while every
   deployed SPV has `vehicle_entity_id` NULL. fee42b reads the POSTED
   `spv_transaction_allocations` instead (not a second balance table — the
   actual transactions), and `spv_carry_runs.capital_account_probe` re-measures
   the view on every proposal so this finding goes stale visibly rather than
   silently. Depends on open question #3 (fee43).

3. **The preferred-return accrual convention is the simple one, deliberately.**
   `preferred_return_owed = hurdle_pct × cumulative_paid_in`, cumulative and
   NON-COMPOUNDING, not time-weighted — named in every `calc_detail` as
   `PREF_CONVENTION`. A time-weighted IRR-style accrual needs dated flows AND a
   compounding convention nobody has specified. `compute_carry` takes an
   explicit `preferred_return_owed` override so settling it later replaces one
   argument, not the waterfall.

### Two things worth knowing before extending this

**HARD vs SOFT was undefined anywhere in this repo and is now defined in one
place.** SOFT = the GP catches up on the WHOLE preferred return once the hurdle
clears (a *timing* preference); HARD = no catch-up tier at all, the GP carries
only above the hurdle (an *economic* preference, and strictly cheaper for the
LP). Stated once in `services/spv_carry.py` and proved in both directions on
one-field-apart fixtures.

**WHOLE_FUND is refused where it would be wrong, not approximated.**
`spv_transactions` carries no investment/position reference, so an SPV has no
grain below itself and on a standalone vehicle DEAL_BY_DEAL and WHOLE_FUND are
the same rows. On an `investment_series`/`member_series` vehicle under a
`master_entity_id` they are not, and no master-level rollup is deployed — that
case raises `WholeFundScopeError`.

---

## 0000000. Platform — domain event emission BUILT; nothing blocked (2026-08-31)

`54/55 PASS, 1 FIND, 0 FAIL, 0 BLOCKED` —
`apps/api/scripts/verify_event_emission.py`. Nothing here is blocked on
anything outside the codebase. Recorded because it CLOSES a standing
cross-module dependency and OPENS one new, deliberate gap.

**What closed.** `workflow_triggers` has carried `trigger_type='event'` since
the Workflow Manager shipped, and until now exactly one hard-wired publisher
existed (`services/chancery_workflow_bridge.py`, for `document_confirmed`).
`services/domain_events.py::publish_event` is now the generic publish side:
any code can record a fact and every active, matching trigger in that org gets
its own `workflow_runs` row and its own `domain_event_deliveries` audit row.
Adding a new event type requires no change to that module.

**The definition→version gap is resolved, not worked around.**
`workflow_triggers.workflow_definition_id` → `workflow_runs.workflow_version_id`
resolves via `workflow_versions.is_current = true`, scoped to
`(workflow_definition_id, org_id)`. That is the ONE mechanism already used by
both existing run-starters (`workflow_scheduler.load_due_candidates` and
`chancery_workflow_bridge`); this sprint reuses it rather than adding a second.
A trigger whose definition has no current version now produces a **`FAILED`
delivery naming that definition**, not a silent skip — verified with the broken
trigger deliberately ordered FIRST so the healthy one proves it was not aborted.

**The first emitter is SPV realization.** `services/spv_events.py` publishes
`spv_realization` from `spv_allocation.post_transaction` — the single writer of
`status='posted'` in the codebase, so every posting path emits and none of them
has to remember to. Payload carries per-investor
`spv_transaction_allocations` amounts as exact `Decimal`-valued strings,
because carry is owed per investor and any consumer re-deriving the split is a
mispayment waiting to happen.

### The one thing worth knowing before extending this

**Realization is matched on `transaction_types`' own accounting flags, not on
`code = 'dist_gain'`**: `category = 'distribution' AND performance_impact =
'gain'`. Both halves are load-bearing — `sell` also carries
`performance_impact='gain'` and is excluded only by `category='transfer'`. On
the live catalogue this resolves to exactly `['dist_gain']`, asserted by the
verify script so a future catalogue change that silently widens or narrows it
fails loudly. This follows `services/portfolio_commitments.py`'s standing rule
that these flag values are read, never re-derived.

### NEW, DELIBERATE GAP — nothing subscribes to `spv_realization` yet

**PARTLY CLOSED by fee42b (2026-09-02) — see the entry above.** The subscriber
now exists in code: the BPMN, the `spv_carry.propose_from_realization` registry
action and the whole carry engine shipped, and the end-to-end is proved against
a real posted `dist_gain`. What remains is operational, not a code gap — **no
production `workflow_triggers` row for `event_type='spv_realization'` has been
created**, so a posted `dist_gain` still writes its `domain_events` row and
fans out to nobody until somebody inserts that one row. The original reading
below still holds for that interval.

The mechanism is proven end-to-end against fixture triggers. Until a production
trigger row lands, a posted `dist_gain` writes its `domain_events` row — the
table is append-only and retains events with no subscriber, by design — and
fans out to nobody. That is the correct state, not a defect: a trigger added
later still finds the history intact.

---

## 000000. Fee module fee42 — SPV fee terms BUILT; FOUR items owed (2026-08-31)

`86/89 PASS, 3 FIND, 0 FAIL, 0 BLOCKED` — `apps/api/scripts/verify_fee42.py`.
`services/spv_fee_terms.py` is the module; `scripts/seed_fee42_backfill.py` is
the one-time migration and it has been RUN (1 SPV migrated). Nothing in fee42
is blocked on anything outside the codebase.

**The sprint's own premise was wrong, and this is the most important thing to
carry forward.** fee42's brief said fee36's `SPV_MGMT_FEE_OFFSET` basis
resolution reads `spvs.mgmt_fee_pct` and should be re-pointed at
`spv_fee_terms`. It does not, and never did.
`fee_run_inputs.resolve_credit_basis` resolves the basis from
`spv_transaction_allocations.allocated_amount` over POSTED `call_mgmt_fee`
transactions — an amount actually charged, not a rate. There was nothing to
re-point and **no fee36 code was modified.** `spv_fee_terms` is the truth about
what an SPV *will* charge; `spv_transaction_allocations` remains the truth about
what it *did*. Do not conflate them in a later sprint.

**Owed, in priority order:**

1. **`spv_fee_side_letters` has ZERO check constraints and no uniqueness index.**
   `overrides` is unconstrained jsonb, so the database will store an override
   that violates the invariants `spv_fee_terms`' own CHECKs enforce (e.g.
   `carry_pct` with no `hurdle_type`), and two overlapping active letters for
   one `(spv_id, entity_id)` are reachable. Both are closed in the application
   layer only (`apply_overrides` validates the MERGED row;
   `load_side_letter` raises `AmbiguousSideLetterError`). A partial unique index
   on `(org_id, spv_id, entity_id) WHERE system_to IS NULL` and a CHECK on
   `effective_to > effective_from` are the schema-level fixes.

2. **No wound-down status exists for an SPV.** The deployed vocabulary
   (`routers/spv.py`'s `SPV_STATUS_TRANSITIONS`) is
   `forming → open → closing → closed` plus `cancelled`. `closed` means the
   RAISE closed, which is when a management fee STARTS — so a fund sits at
   `closed` for its whole life and there is no way to say it has finished.
   `mgmt_fee_term_years` is currently the only thing that stops the clock.

3. **`mgmt_fee_basis`: only two of four are computable.** `COMMITTED`
   (`spv_subscriptions.commitment_amount`) and `FUNDED` (`funded_amount`, which
   is `0.00` on every deployed row today, so a FUNDED fee currently bills
   nothing). `NAV`'s path exists — Portfolio D's
   `portfolio.spv_derived_positions` → `portfolio.assets.internal_spv_id` →
   `portfolio.valuations` — but zero assets carry `internal_spv_id` and
   `portfolio.valuations` is empty, so it resolves to nothing.
   `INVESTED_COST` has no source at all: it would have to be summed from
   `spv_transactions`, whose `txn_type` has no CHECK constraint. fee42 stores
   and resolves the basis; it deliberately does not compute the basis AMOUNT.

4. **`offsets_advisory_fee` is a boolean; `fee_credits.offset_pct` is a
   fraction.** The boolean cannot supply the fraction, so
   `ensure_advisory_fee_offset_credit` takes `offset_pct` explicitly, defaulting
   to a FULL offset. A PARTIAL offset is a real term-sheet clause with nowhere
   to live in `spv_fee_terms` today. Related: fee34 shipped `fee_credits` and
   `validate_credit` but **no service or router ever inserted a credit** — this
   sprint's function is the first application write path the table has had.

**Carry is deliberately out of scope and stays that way.** fee42 stores carry
TERMS (`carry_pct`, `hurdle_pct`, `hurdle_type`, `catchup_pct`, `carry_basis`,
`clawback_applies`) so a future waterfall sprint has real data to read. It
computes no distribution. An active SPV with a known `carry_pct` and an unknown
`hurdle_type` is deliberately NOT backfilled — `SKIPPED_NEEDS_HURDLE`, for a
human to read the LPA, because `'NONE'` asserts the deal has no preferred return
and no deployed data supports that claim.

---

## 00000. Fee module fee39 — profitability views BUILT; ONE fix applied, FOUR items owed (2026-08-30)

`87/91 PASS, 4 FIND, 0 FAIL, 0 BLOCKED` — `apps/api/scripts/verify_fee39.py`.
`services/profitability.py` is the module; `routers/profitability.py` and
`apps/web/app/profitability/` are the read-only surface. Nothing in fee39 is
blocked on anything outside the codebase.

### [A] A REAL CROSS-ORG LEAK WAS FOUND AND FIXED — AND THE SAME BUG IS STILL OPEN ELSEWHERE

`v_profitability_events` was deployed by Part 1 with **no `security_invoker`**,
and its owner (`postgres`) has `rolbypassrls = TRUE`. A view without that
option evaluates its base tables' RLS as the VIEW OWNER, so the view handed any
`app_service` caller every org's revenue and cost rows — while both base tables
were correctly locked down and looked it. The sprint prompt asked for this to
be verified rather than assumed, and the assumption was false.

Fixed live with `ALTER VIEW public.v_profitability_events SET (security_invoker
= true)`. `verify_fee39` [7f] REPRODUCES the leak on a twin view built from the
original definition before [7h] proves the real view now refuses the same read,
so the fix is demonstrated against the actual defect rather than in isolation.

**Still owed, not fixed here:** `v_trial_balance` and the other GL views carry
the identical defect. They were flagged during the portfolio-D sprint and are
still `security_invoker: FALSE` in `docs/schema_snapshot.sql`, which now
records the flag per view. Anything reading them through a non-superuser
connection is reading across orgs today.

### [B] TWO PRODUCT TYPES SHARE ONE REVENUE TYPE

`revenue_events_type_check` admits eight values; three of them cannot come from
a fee run (`SPV_CARRY` is deferred and event-driven, `PASS_THROUGH_MARKUP` is
fee37's cost engine, `INTEREST_SHARE` has no fee-run source). That leaves five
revenue types for six `fee_schedules.product_type` values, so
`STRUCTURED_INVESTMENT` and `TRANSACTION` both map to `PLACEMENT_FEE`.

Nothing is lost for reporting — `revenue_events.product_type` is on the row and
the product cut still separates them — but the two cannot be told apart by
`revenue_type` alone, which matters the moment they need different GL
treatment. Fixing it means adding a value to the deployed CHECK first.

### [C] THE ADVISOR CUT GROUPS ON AN UNCONSTRAINED COLUMN

Neither `revenue_events.advisor_id` nor `cost_events.advisor_id` has a FOREIGN
KEY, unlike `account_id` / `household_id` / `billing_group_id` on both tables.
A typo'd advisor id inserts cleanly and appears in an advisor roll-up as its
own silent, empty-named bucket. Adding `REFERENCES users(id)` to both is a
small migration nobody has done.

### [D] cost_events IS STILL NOT PROVABLY DUPLICATE-FREE (inherited fee37 F4)

`cost_events_dedupe_uq` indexes `account_id`/`household_id`/`billing_group_id`,
and a UNIQUE index does not constrain rows where those are NULL — which is
exactly every firm-level and provider-level cost. `verify_fee39` [5o]
reproduces it: two byte-identical `cost_events` insert without complaint.

fee39 does not fix the index; it makes the consequence visible instead.
`profitability.duplicate_cost_scan` finds such groups and `profit_and_loss`
attaches a warning naming the surplus, so a doubled cost line is reported
rather than silently reducing a client's margin. The real fix is a partial
unique index per NULL-combination, or a generated discriminator column.

### [E] THE RATES BEHIND PASS-THROUGH COSTS ARE STILL UNVERIFIED (inherited fee37 F6)

Any P&L containing a pass-through cost carries
`profitability.UNVERIFIED_RATE_CAVEAT`, attached only when such a row is
genuinely in the cut ([5m] proves it fires, [5n] that it does not fire
otherwise). The underlying `cost_schedules` / `provider_benefit_schedules`
`source_url`s still have not been re-checked against a primary source. Until
they are, the cost side of every margin here is an order-of-magnitude figure.

---

## 0000. Fee module fee38 — Altruist One evaluator BUILT; FIVE items owed (2026-08-29)

`64/67 PASS, 3 FIND, 0 FAIL, 0 BLOCKED` —
`apps/api/scripts/verify_fee38.py`. `services/altruist_one.py` is the module.
Nothing in fee38 is blocked on anything outside the codebase. The section
numbering in this file has drifted (000 / 00 / 0 / 1); this entry follows the
established prepend-a-zero pattern rather than renumbering everyone else's
sections. **fee37 has no entry in this file at all** — its findings live in
`verify_fee37.py` and its sprint log, and fee38 did not back-fill them.

### [A] DECISION NEEDED — which reading of the Altruist One subscription line?

fee37 seeded BOTH readings of one ambiguous rate-card line and a guard rail
(`assert_no_ambiguous_overlap`) stopping anyone summing them. fee38 had to
pick one, and picked **FLOOR** — `max(0.0012 x household_value,
12 x account_count)` — because that is what the design doc states.

**fee37's own seeded note argues the opposite**: that ADDITIVE is the
conservative choice because it is the more expensive one. Both readings are
implemented, `subscription_reading` is a parameter, and every persisted
evaluation records which reading produced its number, so nothing is silently
resolved. But a human still has to read altruist.com and settle it. Until
then, every stored `annual_cost` is conditional on a coin-flip that has been
recorded rather than made. (Verify check `8h`/`8i`.)

### [B] THE DESIGN DOC'S "10% IN SWEEP CASH → ENROLL" HEURISTIC IS WRONG

At the seeded rates, sweep cash alone must be **48% of household value** to
break even against the subscription — 25 bps of uplift on cash against 12 bps
of cost on total value. Ten percent gets you 2.5 bps. A $2M household with
exactly 10% in sweep cash and nothing else recommends **DO_NOT_ENROLL**.

This is not a bug in the evaluator; it is a false premise in the doc, and the
sprint's own acceptance criterion was written from it. Verify check `2a`
computes the counterexample explicitly before `2b` builds a household that
genuinely does recommend ENROLL (cash plus margin plus model discount plus a
counted trade figure). **The doc's heuristic should be corrected or dropped**
— if it reaches an advisor as a rule of thumb it will produce wrong advice.

Note this conclusion is only as good as the rates behind it — see [C].

### [C] THE RATES ARE STILL UNVERIFIED (inherited fee37 F6, now wider)

fee38 seeds five NEW rate rows into `provider_benefit_schedules` (sweep uplift,
HY uplift, model-marketplace discount, per-ticket saving, TLH tax alpha). They
carry `source_url` and `source_verified_on` exactly as fee37's cost rows do,
and they carry the **identical limitation**: nobody has re-read the source.

The evaluator attaches `UNVERIFIED_CAVEAT` to the persisted
`benefit_breakdown` of every evaluation, so the caveat travels with the number
to whatever screen displays it. That is a mitigation, not a fix. **Someone has
to read altruist.com and stamp a real `source_verified_on`** before any of
these figures is quoted to a client.

Two of the five rows are fee37's `UNSEEDED_RATE_CARD_ITEMS` finally given a
home: `CASH_SPREAD` (a benefit, so it could not live in `cost_schedules`
without being summed as an expense) and the model-marketplace discount (whose
base was unstated — fee38 resolves that by CAPPING it at the fee actually
being paid, which is a defensible reading but still a reading).

### [D] DATA GAPS — three inputs the deployed schema cannot supply

Measured in Task 1, reported on every evaluation in `data_gaps` rather than
papered over:

1. **Sweep vs high-yield cash is not separable.** `account_balances_daily` has
   one `cash_value` numeric and no cash-type dimension. The split is a
   caller-supplied `sweep_share_of_cash`, defaulting to "all sweep".
2. **Model-allocated AUM does not exist.** `accounts.service_model` is free
   text with no allocated-value column behind it. Caller-supplied, default $0,
   so the model-discount benefit is simply absent unless someone types a number.
3. **No account-level trade count exists.** `portfolio.transactions` reaches an
   account only through `positions.account_id`. The ticket-savings line is
   **omitted** (not zeroed) when no counted figure is supplied — a zero would
   read as "counted, and it was nothing".

These are inputs a real deployment needs a source for. Until then the evaluator
is running on two and a half of its six intended inputs.

### [E] SCHEDULED RE-EVALUATION IS NOT WIRED — waiting on S29b

`next_review_on` is accepted, persisted, and covered by the deployed
`altruist_one_evaluations_review_idx`. `due_for_review()` is the query a
scheduled trigger will call. **Nothing calls it on a schedule.** That needs a
Workflow Manager trigger, which is the standing fee-module-external dependency
on S29b landing. Recorded as `NEXT_REVIEW_WORKFLOW_TODO` in the module.

### Two smaller things worth knowing

**MARGINAL can never be MATCHED by a decision.** The deployed `decision` CHECK
admits only `ENROLL` and `DO_NOT_ENROLL`, so the `override_requires_reason`
CHECK treats every decision on a MARGINAL evaluation as a divergence needing a
written reason and a named decider. That is correct — a near-breakeven call is
exactly the one that should carry a reason — but it is invisible from the
column list, so `record_decision` says it in the error text. (Check `1k`/`6h`.)

**`account_balances_daily` double-counts on a naive SUM.** Its primary key
includes `source_system`, so one account can hold several rows for the same
day. `load_household_inputs` takes one row per account via `DISTINCT ON`
restricted to `is_billing_source`, and separately counts accounts that still
have more than one billing-source row on their latest date, reporting that as
a data gap. The verify fixture plants a second AGGREGATOR feed on every account
specifically so the dedupe is exercised against a real duplicate — without it a
plain SUM would read $5,000,000 where the answer is $2,000,000. Same shape as
fee37's F4.

---

## 000. Fee module fee36 — runs & approvals BUILT; ONE decision owed, TWO findings for a later sprint (2026-08-28)

`87/90 PASS, 3 FIND, 0 FAIL` — `apps/api/scripts/verify_fee36.py`. Nothing in
fee36 is blocked; it closed fee35's F1 and F4 and fixed two live trigger
defects. Four items are recorded here.

### [A] DECISION NEEDED — which books does RIA fee revenue post to?

The GL hook in `services/fee_runs.py::post_to_ledger` is a **deliberate,
clearly-marked stub** that writes nothing and returns `posted: False` with the
reason. It was not guessed, for a concrete reason measured live:
`journal_entries.vehicle_id` is `NOT NULL`, every deployed `posting_templates`
row (including `MANAGEMENT_FEE`) posts *inside a vehicle's* books, and
`chart_of_accounts` has no advisory-revenue account — `5000 Management Fee
Expense` is the **payer's** side. Wiring it would either invent a vehicle id
for the firm's own revenue or book that revenue as somebody's expense.
`fee_run_lines` are emitted regardless; posting is additive when the answer
exists. Design doc open question #3.

### [B] FINDING F36-C — a POSTED run can never reach status `'REVERSED'`

`fee_runs_status_check` admits `'REVERSED'`, but
`fee_runs_immutable_once_posted` refuses every UPDATE on a POSTED row — by
design, and the sprint deliberately did not weaken it. The reversal link is
therefore read *backwards*, through `fee_runs.reverses_run_id` on the new run.
`'REVERSED'` is currently an unreachable value. Not a bug; worth knowing before
someone writes a screen that filters on it.

### [C] FINDING F36-D — a group minimum silently leaves the group when an account refunds

fee35's `_minimum_step` short-circuits on `run.amount < 0` ("applying a minimum
here would turn a refund into a charge") **before** it reaches the
HOUSEHOLD/BILLING_GROUP branch, so `minimum_deferred_to_group` is never set and
`calculate_group_fees` never puts that account in a bucket. In a group where
one account refunds (a credit exceeding its fee) and others bill, the group
subtotal is computed **without** the refund, so the shortfall charged to the
remaining accounts is too large. The per-account skip is right; dropping the
account out of the group aggregation is probably not. This is fee35's
arithmetic and fee36 deliberately did not change it — pinned by check `6f`/`6g`
in `verify_fee36.py` so a future edit has to decide about it on purpose.

### [D] Closed here, for the record

* **fee35 F1 (`fee_credits` has no amount column)** — resolved. Only
  `SPV_MGMT_FEE_OFFSET` has a real source in the deployed schema: the sum of
  the account's owning entity's `spv_transaction_allocations.allocated_amount`
  across *posted* `call_mgmt_fee` transactions dated inside the period — the
  investor's own share, not the vehicle-level amount. The other four sources
  (`12B1`, `SUB_TA`, `SI_EMBEDDED_FEE_OFFSET`, `MODEL_FEE_OFFSET`) have **no
  source table anywhere** and raise `CreditBasisUnavailableError` rather than
  crediting zero. **A trail/revenue-receipt table is still owed before those
  four credit sources can be billed.**
* **fee35 F4 (`accounts` has no `billing_group_id`)** — resolved via
  `billing_group_members` × `billing_groups(group_type='BREAKPOINT')`, as of the
  date billed. Absent → `None`, which lets the engine raise its own
  `GroupScopeMissingError`; ambiguous → `AmbiguousBillingGroupError`.
* **Two live trigger defects fixed** — `docs/fee36_part1_fix.sql`. See the
  fee35 section below for context on what Part 1 originally applied.

---

## 001. Fee module fee35 — calculation engine BUILT, three decisions owed by fee36 (2026-08-28)

**Status: built and verified, 22/22 PASS, 0 BLOCKED, 9 FIND.**
`apps/api/scripts/verify_fee35.py` — a pure unit suite that opens no database
connection and needs no credentials. `services/fee_calc.py` (the pipeline) and
`services/fee_calc_inputs.py` (the plain-data contracts). Nothing here is
blocked outside the codebase; the three items below are real decisions fee36
must make before a single invoice is produced.

1. **`fee_credits` has no amount column.** Its only numeric column is
   `offset_pct`, confined to `[0,1]`. A credit of "50% of the SPV management
   fee" has nowhere to record what the SPV management fee *was*.
   `CreditInput.basis_amount` is therefore a **required, caller-supplied**
   field with no column behind it. **fee36 must decide where that number comes
   from** — a `fee_runs` input, a second lookup, or a new column on
   `fee_credits`. Defaulting it to zero would make every credit silently
   worthless, which is why the engine refuses to construct a credit without it.

2. **`fee_discounts.value` has no scale and no CHECK constraint.** A `PCT_OFF`
   of `20` and one of `0.20` differ by 100x and both satisfy the column. The
   engine reads `PCT_OFF` as a **percent in [0,100]** and refuses anything
   outside that range. Note the deliberate contrast with
   `fee_credits.offset_pct`, which the deployed constraint confines to `[0,1]`
   — two adjacent tables express a proportion on two different scales and
   nothing in the schema says so. **A CHECK constraint on
   `fee_discounts.value`, scoped by `discount_type`, is the durable fix** and
   is not applied yet.

3. **No holiday calendar exists anywhere in this codebase.**
   `proration_method='BUSINESS_DAYS'` is a deployed, valid value and is
   currently calculated on a plain Mon–Fri count. A market holiday inside the
   period is counted as a business day, overstating the denominator and
   slightly understating a partial-period fee. The engine declares this in
   every affected result's `assumptions`, so it will appear on the fee line
   rather than only in a docstring — but a real NYSE calendar is owed before
   any client is billed on a BUSINESS_DAYS schedule.

**Two smaller things fee36 inherits rather than owes.** `POSITION_TAG` is a
deployed `basis_type` but `portfolio.positions` has no tag column (tags live
in `portfolio.udf_values`), so `PositionInput.tags` is caller-supplied; and
`accounts` has no `billing_group_id` (membership is `billing_group_members`),
so a `BILLING_GROUP`-scoped `minimum_fee` needs the caller to resolve it —
a missing one raises `GroupScopeMissingError` rather than silently degrading
to an account-scoped minimum.

**One bug this sprint's own suite caught and fixed.** An `ASSET_CLASS`
exclusion cannot use `startswith`. Under Rule 4's key scheme
`taxonomy_mc_3_2` is a child of `taxonomy_sc_3` and is *not* a string prefix
of it, while `taxonomy_sc_30` *is* a string prefix and is an unrelated class —
so prefix matching was wrong in both directions at once. Keys are now parsed
into numeric components and compared component-wise (`taxonomy_covers`), with
both directions asserted.

**Deliberately not built:** anything that writes (`fee_runs`/`fee_run_lines`
is fee36), any resolution of *which* schedule/exclusions/discounts/credits
apply (fee32/fee34 own that; the engine consumes their output), and SPV
carry/waterfall, which the design doc defers to its own sprint.

---

## 002. Fee module fee34 — schedule catalog BUILT, four follow-ups owed (2026-08-27)

**Status: built and verified, 49/49 PASS, 0 BLOCKED, 3 FIND.**
`apps/api/scripts/verify_fee34.py`. Nothing here is blocked on anything outside
the codebase — the four items below are real, deliberate gaps a later fee
sprint has to close, recorded so they are not rediscovered as bugs.

### What now exists

- `services/fee_validation.py` — pure, zero database access. Tier contiguity,
  the `minimum_fee`/`minimum_fee_scope` pair, the `REDUCED_RATE`/`FLAT`
  exclusion rules, non-empty `reason`, `approved_by`, and the
  `ordering_policy` permutation. fee35 must **re-run this module**, never
  re-implement the checks.
- `services/fee_schedules.py` — create (always DRAFT v1), edit, submit,
  retire, assign, end, and precedence resolution.
- `routers/fee_schedules.py` — registered in `main.py` at
  `/api/v1/fee-schedules`.

**Part 1 was genuinely applied this time.** Confirmed live on the app's own DSN
before any code was written (`scripts/discover_fee34.py`), not on the MCP
endpoint alone. This is worth stating because fee33's prompt carried the
identical "already applied by Joe" sentence and it was **not** applied.

### Follow-ups owed

1. **`fee_assignments` has NO unique index.** Nothing in the database stops two
   active assignments on the same `scope_id` with overlapping effective dates,
   which would make precedence ambiguous *within* a rung.
   `create_assignment(replace_existing=True)` closes the incumbent first, so the
   application never creates one — but an import path or a manual SQL fix can.
   A partial unique index on `(org_id, scope_type, scope_id) WHERE valid_to IS
   NULL AND system_to IS NULL` would close it properly.
2. **No CRUD for `fee_exclusions` / `fee_discounts` / `fee_credits`.**
   Deliberately out of scope for fee34, which builds the catalog only. The
   validators for all three exist and are tested; the write paths do not. A
   later sprint must call `validate_exclusion` / `validate_discount` /
   `validate_credit` at those rows' own write time — they are **not** reachable
   from the schedule-approval gate (see finding 3 below).
3. **`ENTITY` and `ORG` scopes are unreachable on three of the six tables.**
   `fee_exclusions.scope_type` admits `ORG` (not `ORG_DEFAULT`) and no `ENTITY`;
   `fee_discounts` and `fee_credits` admit neither. Three different scope
   vocabularies, kept as three constants in `fee_validation.py` precisely so
   they cannot be collapsed into one. If the fee engine needs an entity-level
   discount, that is a schema change, not a code change.
4. **Nothing reads a schedule to produce a dollar.** By design — that is fee35.

### The three findings

1. `fee_schedules_code_version_uq` is `UNIQUE (org_id, code, version)` with **no
   partial predicate**, so a Rule 3 valid-axis restatement of a schedule is
   impossible: closing a row and re-inserting the same `(code, version)`
   collides with the row just closed. Versioning goes through `version+1` and a
   DRAFT edit is an in-place `UPDATE`. Not a style choice.
2. `fee_assignments.precedence` is `NOT NULL` with no default and **no tie to
   `scope_type` anywhere in the database**. A body carrying `precedence: 1` on
   an `ORG_DEFAULT` assignment would outrank every account-specific agreement in
   the org, silently. It is derived server-side from `scope_type` and the
   request model declares no such field.
3. **The exclusion rules are not reachable from the schedule-approval gate, and
   the fee34 prompt assumes they are.** `fee_exclusions` has no
   `fee_schedule_id` — only `alt_fee_schedule_id`, the REDUCED_RATE *target*.
   There is no join path from a schedule to "its" exclusions because a schedule
   does not have any. Folding them into `validate_schedule` would have produced
   a gate that always passes vacuously on an empty list.

**Status: complete.** `docs/TA_MODEL_INTEGRATION_BRIEF.md` has the full design
writeup. In short: three pure-function modules
(`services/ta_model.py`, `services/ta_config.py`, `services/ta_calibrate.py`)
implement a Takahashi-Alexander PE cash-flow projection model with 8 seeded
strategy defaults and a frequency-aware minimum-history floor for calibration
(3 years of history required, converted to periods at the series' own
frequency — 12 quarters, not a flat 3). `services/ta_params.py` and two new
bi-temporal/append-only tables (`portfolio.ta_model_params`,
`portfolio.ta_calibration_results`, `docs/tamodel1_part1.sql`) persist
parameter overrides and calibration runs — never projected cash flows
themselves, which are computed at read time only. Five endpoints under
`/api/v1/modeling/ta/*` and `/api/v1/admin/modeling/ta/defaults`
(`routers/modeling_ta.py`).

The sprint prompt that requested this work asserted the three modules and an
integration brief already existed, "verified standalone (93/93)". Neither
existed anywhere in this repo or its git history — see the brief's own
opening section for the full discovery writeup. This sprint built all of it
for the first time rather than treating the false premise as blocking.

**Verification:** `apps/api/scripts/verify_tamodel1.py` — **77 PASS, 0 FAIL,
0 BLOCKED**, run against the real deployed database (Doppler-hydrated
credentials — see §2 below) through the real ASGI app, including a real
commitment's real data projected end-to-end, non-persistence of projected
cash flows proven by row-count before/after, a bi-temporal override
restatement proven by a closed `valid_to` on the superseded row, the
frequency-aware floor proven both ways (refuses 3 quarters, accepts 3 years)
through the real `/calibrate` endpoint, cross-org isolation, and the
view/write permission split on the admin endpoint. Three real bugs were
found and fixed during this sprint's own verification (not left as known
issues): a `Decimal` read back from Postgres for a round number can carry a
positive exponent and render as scientific notation (`"3.5E+5"`) through a
bare `str()` — fixed with fixed-point formatting in `ta_model.py`; a raw SQL
query in `ta_params.py` had a parameter-numbering gap (`$4` referenced with
no `$3` in the query text) that asyncpg cannot bind; and `GET
/modeling/ta/defaults` returned `None` for an org that had never been
explicitly seeded, because the 4 new settings keys were never added to
`org_settings.DEFAULT_SETTINGS`'s own fallback.

**Sprint 2 (admin settings UX) — complete.** A DataGrid + right-pane screen
at `/admin/modeling/ta` (`TaSettingsScreen.jsx`, gated `GATE_ORG_OR_SUPER_
ADMIN` in the nav — same tier as Organization settings) editing the 8
strategy defaults and the 3 platform-level settings, built on the real
Workflow-Triggers-style permission envelope (`permissions.can_write`, no
client fallback — a pattern `OrgSettingsEditor.jsx` still lacks, left
unfixed as out of scope). Two real backend gaps found and fixed in
`routers/modeling_ta.py` / `services/ta_config.py`:

1. Neither GET nor PUT published any signal for which of the 8 strategies an
   org had actually overridden vs. inherited from the seed — added
   `ta_config.strategy_overrides`, a real per-strategy Decimal-value
   comparison (the underlying org_settings row is ONE blob for all 8, so
   row-existence alone cannot answer this at strategy granularity).
2. **A real clobber bug**: PUT wrote `body.values` straight through with no
   merge step, so an admin editing just one strategy through the new screen
   would have silently discarded every other strategy's prior override.
   Fixed: the router now merges a partial per-strategy submission into the
   org's existing blob before writing — the reason this sprint is
   `.structural`, not `.lowrisk`, despite being "just a UI sprint" on paper.

A new read-only endpoint, `GET /modeling/ta/calibration-floor`, lets the
screen show the real, frequency-aware minimum-calibration-periods
requirement as an admin edits `periods_per_year`, by calling
`ta_calibrate.minimum_realized_periods` itself rather than re-deriving it in
the browser.

**Verification:** `apps/api/scripts/verify_tamodel2.py` — **31 PASS, 0 FAIL,
0 BLOCKED**, including a reproduction of the clobber bug's precondition and
proof of the fix, a real 400 confirmed as a plain string (verbatim-
renderable), view-only checked independently at both the API (403) and the
component source (every write control behind an unfallback-able `canWrite`
gate), cross-org isolation on both the settings values and the new
`strategy_overrides` signal, and `npm run build` exiting 0 with the new
routes present in the build output. `verify_tamodel1.py` re-run clean at
77/77 after this sprint's backend changes (no regression).

**Sprint 3 (commitment projection UX) — complete.** The member/staff-facing
projection view, at `/portfolio/commitments/[commitmentId]`
(`CommitmentProjectionScreen.jsx`), reached via a minimal id-lookup form
(`/portfolio/commitments`) rather than a tab on an existing screen — no
commitments list/detail screen, and no general list-commitments backend
endpoint, existed anywhere before this sprint (`services/portfolio_
commitments.py` had only `get_commitment` by id, `create_commitment`, and
`tax_chase_list` by tax year). Read-only: a real, saved projection (chart +
by-period table, both driven from the same API response) plus a live "what
if" panel against the real, non-persisting preview endpoint, clearly labeled
as an unsaved preview. Reuses `view_portfolio` verbatim — no new permission.
No charting dependency was added (`apps/web/package.json` has none); the
chart is a small inline SVG component. `lib/decimalString.js` formats every
monetary/rate value by string manipulation only (digit-grouping, decimal-
point shift) — no `Number()`/`parseFloat()` anywhere in the display path.

One real, additive backend fix: `GET /modeling/ta/projection/{commitment_id}`
now also publishes `committed_capital`/`called_to_date`/`distributed_to_date`
(already computed in the handler, never previously returned) — without them
the preview tool had no way to seed `committed_capital`, a required field on
`POST /projection/preview`.

**Verification:** `apps/api/scripts/verify_tamodel3.py` — **22 PASS, 0 FAIL,
0 BLOCKED**, including a real commitment's real projection end-to-end (chart
and table proven consistent by construction — both driven from the same
`periods` array), the preview tool proving a measurably different result for
a changed `bow_factor` (a uniform scale on the distribution ramp — NOT a
deferral, corrected from this sprint's own initial, wrong assumption once
measured against the real endpoint), preview non-persistence via a real
row-count check, permission enforcement proven with a REAL zero-permission
role grant (not a zero-roles fixture, which would default-allow and make the
refusal vacuous), cross-org isolation (404), an executed (not merely
grepped) proof that the exact money formatter preserves digits a JS `Number`
would corrupt, and `npm run build` exiting 0.

**Sprint 4 (calibration UX + obligation ledger integration) — complete. ALL
FOUR TA MODEL SPRINTS NOW DONE.** Task 1 found a second false premise of the
same shape as Sprint 1's own brief: the prompt asserted `ta_model.py` already
carried `contributions_between`/`contributions_in_years` (with docstrings
naming a "36-month visibility horizon" and calling themselves "the read-time
primitive the obligation ledger consumes") and that `ConfidenceTier`/
`weakest_confidence` were "real, live fields." A full grep found none of it —
no primitives, no obligation ledger consumer, no confidence-tier vocabulary
anywhere in the codebase. This sprint built the first real versions of both,
not a wire-up of prior work — see `docs/TA_MODEL_INTEGRATION_BRIEF.md` §9 for
the full writeup.

- **Obligation ledger:** two new pure functions on `ta_model.py`
  (`contributions_between`, `contributions_in_years`) and the first real
  consumer, `GET /modeling/ta/obligations/{commitment_id}` — a genuine
  36-month forward capital-call visibility view, computed at read time and
  never persisted (matches the same rule `services.spv_rollup` already
  applies to SPV-derived capital-call totals). Gated on `view_portfolio`.
- **Confidence tier:** a new module, `services/ta_confidence.py`, derives an
  HONEST, PARTIAL implementation of the prompt's 4-tier vocabulary from the
  one real signal already in the schema (`ta_model_params.source`):
  `STRATEGY_DEFAULT` / `ASSUMED` / `OBSERVED` are real; `PEER_CALIBRATED` has
  no backing data anywhere in this codebase (no cross-fund aggregation
  exists) and is deliberately never returned — a reported gap, not a faked
  tier. Surfaced on `CommitmentProjectionScreen` via `ConfidenceTierCard`
  (plain-language description, not just a color chip) — Sprint 3's screen
  published none of this.
- **Calibration UX:** `CalibratePanel`, rendered only when a new
  `permissions.can_calibrate` envelope on `GET /projection` (computed via
  `rbac.has_permission` against the real `manage_portfolio` gate) is `true`
  — fail-closed, no client-side default. A real preview-then-confirm flow
  via a new, additive `dry_run` field on the existing `POST /calibrate` body
  (default `False`; Sprint 1's own verify script is unaffected): the same
  fit and the same frequency-aware floor check run under `dry_run`, but
  neither persistence call fires, so a refusal is identical either way. On
  confirm, the screen re-fetches the projection fresh so the tier shown
  updates on the same load, not only after a manual refresh.
- **Task 1d, a genuine simplification found along the way:** the prompt
  assumed `/calibrate` needed a submitted list of period+amount pairs. It
  never did — `services.ta_params.realized_periods_from_transactions`
  already derives realized history server-side from
  `portfolio.transactions`. The Calibrate UX is therefore a strategy/
  frequency picker, not a data-entry form.

**Verification:** `apps/api/scripts/verify_tamodel4.py` — **48 PASS, 0 FAIL,
0 BLOCKED**, run against the real deployed database (Doppler-hydrated
credentials) through the real ASGI app. Covers: the confidence tier and
`can_calibrate` envelope before any override, the calibration permission
gate proven both ways with a REAL zero-permission role grant (independent of
the read gate), `dry_run` proven non-persisting by row count, the
frequency-aware floor's real refusal surfacing verbatim, a real calibration
persisting and a FRESH independent GET showing the upgraded `OBSERVED` tier
(with a genuinely different fitted `rate_of_contribution`), two real
commitments showing genuinely different confidence tiers AND genuinely
different obligation-ledger totals (driven by real, different
`committed_capital` — 2,000,000 vs 500,000), the ledger's read-time-only
guarantee proven by row count, cross-org isolation on both new surfaces, and
`npm run build` exiting 0.
---

## 00. LiteLLM Phase C — Voyage embeddings routed through the proxy (2026-09-14)

**Status: built and verified, 34/34, 0 BLOCKED.**
`apps/api/scripts/verify_litellmphasec.py`. Phase B (entry immediately below)
proved TEXT calls; this closes the parallel gap the original LiteLLM
discovery sprint found — Voyage embeddings bypassing the router entirely, no
fallback chain, no `ai_decision_log` row. Phase D (per-org model pick-list
UI) is next.

### What now exists

- **Voyage registered as a real, persisted proxy deployment**:
  `model_name='voyage-3.5' -> litellm_params.model='voyage/voyage-3.5'`,
  `api_key=os.environ/VOYAGE_API_KEY` (the same `os.environ/` indirection the
  Anthropic deployment uses — never a literal key). Confirmed via
  `POST /model/new` + a FRESH re-read on `GET /v1/models` and
  `GET /model/info` (`db_model=true` — persisted to LiteLLM's own DB, not an
  in-memory-only registration), not by trusting the POST's own response.
- `services/document_embedding.py` now calls LiteLLM's OpenAI-shaped
  `POST /v1/embeddings` (both `/v1/embeddings` and bare `/embeddings` answer
  identically live; `/v1/embeddings` is used to match the documented
  OpenAI-compatible surface) instead of calling Voyage directly. The
  transport resolver is the SAME one text calls use
  (`services/extraction.resolve_transport` / `LITELLM_ROUTING_DISABLED`) —
  **one rollback switch now covers both text and embeddings**, the correct
  blast radius for a platform-wide ops escape hatch. When LiteLLM is not the
  resolved transport, this module falls back to the SAME direct-Voyage call
  it always made — now the explicit fallback, not the only path.
- A real, embedding-specific fallback chain: `ai.embedding.fallback_chain`
  (its own org_settings key, deliberately NOT shared with
  `ai.model.fallback_chain` — a wrong-dimension fallback model would silently
  corrupt vector search, so embeddings need their own chain with a per-attempt
  dimension gate). Defaults to `["voyage-3.5"]`, mirroring
  `ai.model.fallback_chain`'s single-item default.
- Every embedding call now writes exactly one `ai_decision_log` row — the SAME
  table, the SAME shape text calls use (`task_type='embedding_document'` /
  `'embedding_query'`), via the identical `_safe_log`/`_write_ai_decision`
  helper `services/extraction.py` already uses. Embeddings are no longer
  invisible to per-org AI cost attribution.
- **The re-indexing friction dialog** (CLAUDE.md's embedding-compatibility
  rule — embeddings from different models are not comparable, and switching
  one without re-indexing silently degrades search). New endpoint
  `GET /orgs/{org_id}/settings/embedding-reindex-estimate?new_model=X`
  (`services/document_embedding.reindex_estimate`) returns the org's REAL
  corpus count (`COUNT(*) FROM document_embeddings WHERE org_id=$1` at call
  time, never an estimate) and a REAL cost estimate computed from the
  corpus's actual stored `content_chars` and the candidate model's LIVE
  LiteLLM price (`GET /model/info` → `input_cost_per_token`, never a
  hardcoded local price table). `OrgSettingsEditor.jsx` intercepts a genuine
  `ai.embedding.model` change and shows this before saving — Cancel, or
  "Change model anyway" (friction, not a lock; a confirming admin always
  proceeds).

### [FIND]s recorded, not silently routed around

- **The live `document_embeddings` corpus was genuinely EMPTY (0 rows, all
  orgs)** at the start of this sprint — Chancery's semantic INDEX had never
  successfully embedded a document in this environment before now, reported
  honestly rather than papered over. `verify_litellmphasec.py` seeds real
  fixture rows (via the full `embed_document` path AND a raw pre-seeded row,
  simulating genuinely pre-existing data) to prove both the friction dialog's
  live-count claim and the existing-embedding-survives-untouched claim
  against real data.
- **No re-indexing mechanism exists anywhere in this codebase** — no script,
  no endpoint, no scheduled job. Changing `ai.embedding.model` only changes
  what NEW documents embed with; every already-embedded document keeps its
  OLD-model vector until someone manually re-runs `embed_document` on it. The
  friction dialog's `note` field says this plainly rather than implying an
  automatic migration will run — the real gap the sprint prompt asked to be
  recorded honestly if found.
- **`ai_decision_log.cost_usd` is `numeric(10,6)`** — sized for Claude's
  per-call cost. Voyage's real live price ($0.06 / 1M input tokens, read from
  LiteLLM's own `GET /model/info`) means a short embedding call (a handful of
  tokens) silently floors to `0.000000` at that scale — not an error, a real
  precision gap. A migration exists
  (`migrations/litellmphasec_cost_precision.sql`, widens to
  `numeric(14,10)`) but is **BLOCKED**: `DATABASE_URL` now connects as
  `app_service` (the RLS-cutover role), which is not `ai_decision_log`'s
  owner (`postgres`) and cannot `ALTER` it, and no `postgres`-role credential
  is available in this environment. Until someone with owner access applies
  it, short embedding calls will log `cost_usd=0.000000` even though a real,
  non-zero cost was billed (correctly visible in LiteLLM's own spend log,
  which has no such precision limit). The verify script's own real-call proof
  uses a realistic multi-sentence fixture text specifically so its non-zero
  `cost_usd` assertion does not depend on this migration landing.
- **The Doppler `VOYAGE_API_KEY` is on Voyage's rate-limited free tier** (live
  error text: "reduced rate limits of 3 RPM and 10K TPM" — no payment method
  on file). `verify_litellmphasec.py` paces its real Voyage calls around this
  (a 65s pause between real calls; 25s measurably was not enough). Production
  usage at any real volume will need a paid Voyage tier or app-side request
  pacing to avoid the same throttling.
- `docs/schema_snapshot.sql`'s generator does not capture FOREIGN KEY
  constraints at all (confirmed: zero `FOREIGN KEY` occurrences in the whole
  file). `document_embeddings.document_id` has a real, live
  `REFERENCES documents(id) ON DELETE CASCADE` the snapshot is silent about —
  discovered building this sprint's verify fixtures the hard way. Worth
  fixing in the snapshot generator; out of scope here.

### What IS proven, against the live proxy

- A real `embed_document()` call through the FULL app path succeeds
  end-to-end through LiteLLM, stores a real 1024-wide vector, and both
  LiteLLM's own spend log (`GET /spend/logs`, non-zero spend,
  `call_type='aembedding'`) and `ai_decision_log` (`success=true`, real
  `latency_ms`, real non-zero `cost_usd`) record it.
- The fallback chain genuinely walks: a forced-bogus primary model fails with
  LiteLLM's own "Invalid model name" error (proof the request really reached
  the live proxy), and the real model recovers on the second attempt —
  `ai_decision_log` records `fallback_used=true`.
- The rollback (`LITELLM_ROUTING_DISABLED=1`) genuinely bypasses LiteLLM for
  embeddings too — the call still succeeds (via direct Voyage, the
  pre-Phase-C path), and LiteLLM's own spend log shows ZERO new rows after
  the full flush window.
- The friction dialog's `corpus_document_count` exactly matches a direct SQL
  count, both before and after a real write (proving it's live, not cached or
  hardcoded), and its price comes from LiteLLM's live `/model/info`, not a
  local table.
- A raw, pre-existing embedding row (inserted directly, never touched by any
  Phase-C code path) is still readable, byte-identical, at its original
  dimensionality after every maneuver above — this sprint did not silently
  invalidate the existing corpus.

---

## 00. LiteLLM Phase B — FULLY COMPLETE, first real billed call proven (2026-09-14)

**Status: built and verified, 68/68 (2026-08-26) + 25/25 (2026-09-14), 0
BLOCKED.** `apps/api/scripts/verify_litellmphaseb.py` proved the transport
layer; `apps/api/scripts/verify_litellmphasebproof.py` proves the real thing —
a genuine, billed, successful generation through the full chain, dual-logged,
with a genuine rollback-absence proof. All three blockers that stopped a first
real success (below) are closed. **Phase C (Voyage) is now complete too — see
the entry immediately above.**

### What now exists

- `services/extraction.py` — the platform's single AI chokepoint — now sends its
  HTTP calls to the self-hosted LiteLLM proxy instead of straight to Anthropic.
  It is a **transport swap at one function** (`_build_ai_client`): the fallback
  chain, retry walk, cost model, `ai_decision_log` writes and error handling are
  untouched, and `ai_decision_log` gained no columns.
- **How, and why this way:** the Anthropic SDK is *pointed at* LiteLLM's base URL
  rather than replaced. LiteLLM serves a real Anthropic-shaped
  `POST /v1/messages` (confirmed live). Keeping the SDK means the response
  objects reaching every `extract()` closure and `_compute_cost` stay genuine
  Anthropic types, so `message.content[0].text`, `stop_reason`,
  `block.model_dump()` and `usage.input_tokens` all keep working unchanged. The
  OpenAI-shaped `/v1/chat/completions` route is *also* live and was measured, but
  using it would have required hand-writing an OpenAI→Anthropic response adapter
  (including `tool_calls`→`tool_use`) on the most load-bearing path in the
  platform. That is a rewrite, not a transport swap.
- **`LITELLM_ROUTING_DISABLED=1` — a real, tested rollback path.** Set it and
  every AI call goes straight back to Anthropic, never contacting LiteLLM.
  Verified both ways: the client's real `base_url` becomes `api.anthropic.com`,
  and **zero rows appear in LiteLLM's own spend log** after waiting the full
  flush window. It is an **environment variable, not an org_settings key**, on
  purpose — it must keep working when the database is the unhappy thing, and an
  `org_settings` read would need a working DB to report that the DB-independent
  fallback is on. This is **not** design §7.5's future per-org `force_anthropic`.
- **A wrong master key now fails loud.** `AILiteLLMAuthError` names the variable,
  the endpoint, the HTTP status and the remedy, is still recorded in
  `ai_decision_log`, and does **not** walk the chain (every model shares the one
  key). It deliberately propagates through all three `call_claude_*` wrappers
  instead of being flattened into their usual `None`.

### GAP CLOSED — `LITELLM_BASE_URL` now exists in Doppler

The item below (and `render.yaml`'s note) recorded `LITELLM_BASE_URL` as the one
`LITELLM_*` variable genuinely missing from Doppler. **It was still missing, and
this sprint added it** to `hollisworks/prd`, pointing at the live
`https://hollisworks-litellm.onrender.com`. Verified by re-reading it back.

### RESOLVED (2026-09-14) — all three blockers closed, in the order predicted

1. **A real model deployment exists.** `GET /v1/models` returns `claude-sonnet`;
   `GET /model/info` confirms it persists as `litellm."LiteLLM_ProxyModelTable"`
   row `7fcd845c-0a47-413c-b77d-3da88d984425`, routing to
   `anthropic/claude-sonnet-4-6`. Real DB row, survives restarts.
2. **`LITELLM_MASTER_KEY` now authenticates as PROXY_ADMIN.** Root cause of the
   `role=internal_user` failure: a Doppler sync had silently overwritten
   `hollisworks-litellm`'s own `DATABASE_URL` with the shared root config's
   value — the exact "sync is destructive, not additive" hazard this project's
   CLAUDE.md already warns about, confirmed a third time. Fixed with a
   dedicated Doppler branch config (`lite_llm`) syncing `DATABASE_URL` only to
   that service. Full writeup in `docs/LITELLM_INTEGRATION_DESIGN_V1.md` §13.5.
3. **`ANTHROPIC_API_KEY` exists in Doppler and is real** — proven by a call made
   directly against `api.anthropic.com`, independent of LiteLLM entirely.

**The proof this unlocked**, via `apps/api/scripts/verify_litellmphasebproof.py`
(25/25, 1 FIND): a genuine `200` with real generated text through
`call_claude_text` → LiteLLM → Anthropic; `ai_decision_log` recording real
`success=true` with non-zero `cost_usd`/`latency_ms`; LiteLLM's own spend
ledger recording the SAME call with non-zero spend (**the first real, billed
call this proxy has ever routed** — every prior row was `status=failure,
spend=0`); the two logs agreeing on outcome; the rollback path succeeding via
direct Anthropic, proven by genuine absence of a new LiteLLM spend row after
the full flush window; and the fallback chain still walking correctly via
LiteLLM under a forced first-model failure.

**[FIND], recorded not silently routed around:** `app_service` (the role
`DATABASE_URL` now points at, post RLS-enforcement-cutover) gets
`InsufficientPrivilegeError: permission denied for schema litellm` on any
direct SQL against `litellm.*` — the earlier verify script's direct queries
only worked because they pre-dated that cutover, when `DATABASE_URL` was still
`postgres`. The obvious workaround (`LITELLM_DATABASE_URL`, the
`litellm_service` role's own connection string) is *also* currently blocked by
an `InvalidPasswordError` — same class of credential drift as the historical
`DB_PASSWORD` issue. The completion-proof script instead reads LiteLLM's own
admin HTTP API (`GET /spend/logs`) with `LITELLM_MASTER_KEY`, which is the
correct interface for this anyway and needs no schema-crossing grant.

### What IS proven, against the live proxy

- Requests genuinely reach LiteLLM. The error text returned — *"Invalid model
  name passed in model=…"* — is generated by LiteLLM itself; neither our code nor
  `api.anthropic.com` emits that string.
- The fallback chain still walks every model, in order, via LiteLLM, proven with
  a real forced-failure primary.
- **Dual visibility is real.** The same call appears in *both* `ai_decision_log`
  and LiteLLM's own spend log (read via `GET /spend/logs` post-cutover — see
  the [FIND] above), agreeing on outcome. They do **not** agree on model name
  literally: `ai_decision_log` records the request-facing name (`claude-sonnet`)
  while LiteLLM's log records the resolved deployment string
  (`anthropic/claude-sonnet-4-6`) — correlate by time window + the
  `/model/info` alias mapping, not a naive string match. **Measured, and it
  matters: LiteLLM flushes that table asynchronously, seconds after
  answering.** A before/after count taken around the call sees nothing — an
  earlier draft of the verify script reported a false negative for exactly
  this reason. Any assertion about that log, presence *or* absence, must poll
  across the flush window.
- `ai_decision_log`'s shape is unchanged, compared column-by-column and
  type-by-type against a real pre-sprint row.

### Deploy note

`LITELLM_ROUTING_DISABLED` is **not** declared in `render.yaml` — it is an
break-glass switch, and a declared-but-unset variable invites someone to set it
permanently. Set it directly in the Render dashboard if the proxy misbehaves.
**As of 2026-09-14, production is off the degraded path**: LiteLLM has a real
model deployment, a working PROXY_ADMIN key, and a real Anthropic credential —
calls route through it and succeed. The rollback switch remains available and
proven (see RESOLVED section above) if the proxy misbehaves in production.

---

## 0. Workflow scheduler — core engine BUILT (2026-08-26)

**Status: built and verified, 65/65.** `apps/api/scripts/verify_schedulercore.py`.
Not blocked on anything. Recorded here because it closes two items this file's
predecessors kept referring to, and because it opened one new deployment action.

### What now exists

- **`workflow_triggers.schedule_cron` is no longer dead code.** Before this
  sprint a repo-wide grep found exactly two readers — a `SELECT` in
  `routers/workflows.py` and a display cell in `WorkflowTriggerScheduler.jsx`.
  **Nothing fired anything on a schedule.** The one `scheduled` row in the
  database had been inserted by a verify script, because there was no API path
  to create one.
- `docs/schedulercore_part1.sql` adds six columns to `workflow_triggers`:
  `timezone` (IANA, `NOT NULL DEFAULT 'UTC'`), `start_date`, `end_date`,
  `max_occurrences`, `occurrence_count`, `last_fired_at`.
- `services/workflow_schedule.py` — pure recurrence evaluation. Translates the
  stored cron expression into a real `dateutil.rrule` (preserving cron's
  day-of-month **OR** day-of-week semantics, which rrule ANDs) and resolves it
  in the trigger's **own** timezone.
- `services/workflow_scheduler.py` — the firing loop. Scans all orgs, evaluates,
  checks workflow-level overlap, claims the occurrence atomically, and fires
  through the **real** `workflow_engine.start_workflow_run`.
- `apps/api/workflow_scheduler_tick.py` — the minimal process entrypoint.
- `POST /admin/workflow-triggers` now accepts `trigger_type='scheduled'` plus
  the recurrence fields, with the cron expression and IANA zone validated at the
  boundary. The pre-existing three-field event body is unchanged and still works.

**Per-org timezone lives in our code, not in Render.** Render cron schedules are
UTC-only and cannot be made timezone-aware. The service ticks every 5 minutes in
UTC and each trigger's local schedule is resolved in Python. The 5-minute cadence
and the evaluator's 60-minute lookback window are a matched pair — change one and
re-check the other.

### `render.yaml`'s LiteLLM section — CORRECTED

The blueprint asserted that "the LiteLLM proxy is NOT DEPLOYED … there is no
LiteLLM service in this blueprint, and Doppler holds no `LITELLM_*` secret."
**Both halves were out of date.** `hollisworks-litellm.onrender.com` answers HTTP
200 on `/health/liveliness`, and Doppler holds four `LITELLM_*` secrets against a
migrated 77-table `litellm` schema. `LITELLM_BASE_URL` and `LITELLM_MASTER_KEY`
are now declared on the API service. **`LITELLM_BASE_URL` is still absent from
Doppler**, which is why `litellm.reload_model_cost_map` still raises
`LiteLLMConfigError` even though the proxy is up — see item 2.

> **SUPERSEDED by item 00 (LiteLLM Phase B, same day).** `LITELLM_BASE_URL` has
> now been added to Doppler `prd`. And the inference in the last sentence was
> wrong: adding it does **not** unblock `litellm.reload_model_cost_map`. The
> stored `LITELLM_MASTER_KEY` is an `internal_user` virtual key, not the proxy's
> PROXY_ADMIN master key, so that admin endpoint still fails — now with HTTP 403
> instead of `LiteLLMConfigError`.

### ACTION REQUIRED — deploy the cron service

`render.yaml` now declares a third service, `2ndactcapital-workflow-scheduler`
(`type: cron`, `plan: starter`, `schedule: "*/5 * * * *"`). **Until the blueprint
is applied in Render, nothing fires in production** — the engine is built and
proven, but no process is running it. A Render cron job has no free plan and a
**$1/month minimum**, billed by the second of active runtime.

Also unresolved and recorded rather than papered over: the `hollisworks-litellm`
service is live but is **not** declared as a block in `render.yaml`. It was
created outside the blueprint, so adopting it is a migration, not an edit. That
is a real remaining gap in this manifest's coverage.

### Two bugs this sprint found and fixed

**1. A held run vanished entirely when started through the real pool.**
`workflow_engine.start_workflow_run` documents that the run row is persisted "in
their own committed transaction … rather than vanishing on rollback". It was not.
`services.database._RLSPool.acquire()` opens an **outer** transaction (that is
how the RLS `SET LOCAL` GUCs are scoped), so asyncpg nested the engine's
`conn.transaction()` as a **savepoint**. When `start_workflow_run` re-raised
after holding, the exception escaped `pool.acquire()`, the outer transaction
rolled back, and the `workflow_runs` row, its `error_detail` and every
`create_held_run_alerts` todo were erased together — a failed run left **no trace
at all**.

Every prior verify script built a **raw** `asyncpg.create_pool`, where the inner
commit is real; that is precisely why this never surfaced. The deployed
event-trigger path (`chancery_workflow_bridge`) passes the actual RLS pool and
has always been exposed to it. Fixed by `_independent_acquire()`: the up-front
persist and the hold/alert now run on a connection that is not enlisted in any
caller's transaction, with the RLS context re-applied.

**2. The scheduler process had an empty action registry.**
`services.assistant_actions.register_all()` was called from exactly one place —
`main.py`'s FastAPI `startup` hook. The cron process never starts FastAPI. An
empty registry does **not** fail loudly: `_execute_service_task` resolves every
action to `None` and the engine marks the step **completed**. Every scheduled
workflow would have reported success having invoked nothing. `run_scheduler_tick`
now registers the actions itself, once per tick.

### Deliberately out of scope

Cost/duration correlation to a run. Zero workflow run steps have ever invoked
AI, `ai_decision_log` carries no run identifier, and there is nothing to
correlate yet. **Still true after Sprint 4** — the Run History screen ships with
no cost column for exactly this reason.

### Sprint 3 (CRUD UX) and Sprint 4 (Run History) are since complete

- **Sprint 3 — scheduler CRUD UX**, 91/91 (`ec6ef24`). Not blocked.
- **Sprint 4 — Run History**, 82/82 (`schedulerhistory.structural`). Not blocked.
  Server-side status and time-period filters, a step timeline, scheduled-vs-manual
  origin read from the run's stored context, and a held run's real `error_detail`
  plus the exact `member_todos` alert set.

**One correction this file's readers should carry forward:** *per-run duration*
appeared in the Sprint 4 plan as though it were sound data. It is not. Postgres
`now()` is the transaction timestamp; the engine inserts the run row on an
independent connection and completes it on the caller's, whose transaction opened
first — so a run that finishes inside its own `start_workflow_run` call has
`completed_at` **before** `started_at`. Measured at **-0.36s** on a real run
during verification. Both the API and the screen now report "not measured" for any
non-positive interval instead of a number. Anything downstream that plans to
aggregate run durations needs to know this before it starts averaging.

### Next

**Sprint 5 — notifications.** Largely satisfied already: `create_held_run_alerts`
really fires, really reaches the starter plus every `org_admin`, and Sprint 4
verified the exact recipient set against `member_todos`. The genuinely missing
pieces are enumerated in `docs/OUTSTANDING_TODO_LIST.md` §2 — the largest being
that a **User Task with no `assigned_role_profile_id` notifies nobody, silently**,
and that the only out-of-band channel (email) is blocked on §1 of this file.

**Still the binding blocker for this whole subsystem: the Render cron service has
not been applied.** Everything above is proven in verification and fires nowhere
in production until the blueprint is applied.

---

## 1. Email sending (invites) — BLOCKED on two AWS-side actions

**Status: built, wired, verified — and NOT working end-to-end.** Real delivery
is blocked on AWS-account changes that cannot be made from this codebase.

### What now exists in the code (done, verified)

Before this sprint there was **no email-sending code anywhere in the API** — no
SES, SMTP, SendGrid, Postmark or Resend client in `services/` or `routers/`.
That blocked an already-shipped feature: `POST /admin/invites` mints a real
invite and returns an `enrollment_url` for the admin to share **by hand**,
because there was no way to mail it.

Now shipped:

- `apps/api/services/email.py` — the single AWS SES choke point. Credential gate
  (`credential_state()` / `probe()`, following the `portfolio_altruist.py`
  pattern), one `send_email()` that returns SES's own `MessageId`, and an error
  taxonomy that classifies a failure as `credentials` / `iam` /
  `identity_or_sandbox` / `paused` / `transport`.
- `render_invite_email()` — a plain-text + minimal-HTML invite carrying the
  **inviting org's own** name, that org's `enrollment_url`, and the expiry
  derived from that org's configurable `invite.expiry_days` setting.
- `services/invites.py` — `create_invite()` now attempts a real send and records
  the outcome in `result["email_delivery"]` on **every** path.
- `routers/invites.py` — the create response returns `email_delivery`, and
  `GET /admin/email/status` re-probes SES live so the AWS actions below can be
  confirmed done without a redeploy.

**The fallback is announced, not silent.** When a send cannot happen the invite
still succeeds and `enrollment_url` is still returned — but `email_delivery`
carries `status: "blocked"`, `manual_share_required: true`, and a reason naming
the exact AWS action to take. Returning the URL as though mail had gone out is
the failure mode this sprint exists to prevent.

### Why it does not work today (measured live, not assumed)

Verified against the credentials **Doppler** actually serves to Render, on
2026-08-26:

1. **The IAM permission does not exist.** The credentials are valid and live —
   `sts:GetCallerIdentity` resolves them — but they belong to
   `arn:aws:iam::645767464372:user/Texttrac-Ripasso`, the **Textract-only** IAM
   user. A real authorization probe on the send action returns:

   ```
   AccessDeniedException: User '…:user/Texttrac-Ripasso' is not authorized
   to perform 'ses:SendEmail'
   ```

   This is the **same gap as the earlier invite sprint**. Tonight's Doppler
   credential rotation restored *working keys*; it did not change *what those
   keys are allowed to do*. Rotating them again will not help.

2. **The SES sandbox state cannot even be read.** `ses:GetAccount`,
   `ses:GetAccountSendingEnabled` and `sesv2:ListEmailIdentities` are **all**
   denied for this principal, so this deployment cannot determine whether the
   AWS account is out of sandbox. The code reports that as *unknown* rather than
   guessing. This is an **independent second blocker**: a sandboxed SES account
   can only deliver to verified addresses, so granting `ses:SendEmail` alone
   could still cause invites to real prospective members to be rejected.

3. **No verified sender is configured.** Doppler holds `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION` — and **no** `SES_*`
   variable at all. `SES_FROM_EMAIL` is unset.

### ACTION ITEMS — Joe, outside this sprint

| # | Action | Where |
|---|--------|-------|
| 1 | Attach an IAM policy granting `ses:SendEmail` and `ses:SendRawEmail` (and `ses:GetAccount` so the status endpoint can report sandbox state) to the principal the deployment uses — either to `Texttrac-Ripasso` or, preferably, to a **new dedicated IAM user for mail**, whose keys then replace `AWS_*` in Doppler. | AWS IAM console |
| 2 | Verify a sender identity in SES — the address or, better, the sending domain (DKIM). | AWS SES console, `us-east-1` |
| 3 | **Request SES production access** (exit sandbox) for this account/region. Until this is done, delivery is restricted to verified addresses and real member invites will fail. | AWS SES console → Account dashboard |
| 4 | Set `SES_FROM_EMAIL` (and optionally `SES_FROM_NAME`, `SES_CONFIGURATION_SET`) in Doppler; confirm they reach Render. | Doppler |

**To confirm when done:** call `GET /admin/email/status` as an admin. It makes
one real SES call and reports `ok`, the gap, and `sandbox_known` /
`production_access`. Or re-run `apps/api/scripts/verify_smtpservice.py`, which
exits non-zero while this is blocked and will attempt a real send — and assert a
real `MessageId` — the moment the gate reports usable.

### Verification result (2026-08-26)

`apps/api/scripts/verify_smtpservice.py` → **9 PASS, 0 FAIL, 1 BLOCKED**, exit 2.

Everything that can be verified is: the discovery findings, the loud-failure
messages (including a **real** refused SES call proving the IAM message names
`ses:SendEmail`), the announced manual-URL fallback, cross-org content
correctness, output safety, and zero-leftover teardown. The single BLOCKED line
is real delivery, per the action items above. It is deliberately *not* reported
as a pass: "we correctly reported that we cannot send email" is not "email
works".

### One real bug this sprint found and fixed

`DEFAULT_SETTINGS["brand.name"]` is the literal string `"2nd Act Capital"`, and
**Hollisworks has no `brand.name` row**. Resolving the sender name with the
ordinary `get_setting()` would therefore have signed **every Hollisworks invite
email with 2nd Act Capital's name**. `resolve_org_display_name()` uses
`get_setting_with_origin()` instead and falls back to `organizations.name` (which
is per-org and `NOT NULL`), so the platform default is unreachable on this path.

This is the same "silently inherit the other tenant's value" shape as the Auth0
`domain ?? AUTH0_DOMAIN`, `appBaseUrl ?? APP_BASE_URL` and `audience` bugs — and
worse here, because the recipient sees it.

---

## 2. Notes for whoever runs the verify scripts

Working database credentials live in **Doppler**. The copies in `apps/api/.env`
and `~/.bashrc` are **stale** and their passwords are rejected by Postgres for
both the `postgres` and `app_service` roles; that stale copy is what produced
several sprints of false "blocked on credentials" results.

`apps/api/scripts/_doppler_env.py` hydrates `os.environ` from Doppler over its
**HTTPS API** using `DOPPLER_TOKEN` (stdlib only, no CLI, never prints a value).
`verify_smtpservice.py` uses it and overwrites the ambient values deliberately —
deferring to what is already set would preserve exactly the stale-copy bug.

## fee38 — subscription-reading decision (accepted, revisit before real reliance)

Altruist One subscription cost: FLOOR reading — max(0.0012 × household
value, 12 × account count) — accepted as the interim default for the
evaluator, per the design doc's own stated formula. fee37 also seeded
the ADDITIVE reading (subscription + per-account minimum, both apply)
and argued it as the more conservative choice. subscription_reading is
a parameter on every evaluation, so the choice is recorded per row,
not silently baked in.

Revisit before any recommendation from this evaluator is relied on for
a real client decision — the two readings can diverge meaningfully on
low-AUM/high-account-count households, and which one actually matches
Altruist's real billing behavior has not been confirmed.

## Security — cross-tenant RLS bypass on views without security_invoker

fee39 discovered and fixed a real cross-tenant data leak in its own
Part 1 view (v_profitability_events): a view owned by `postgres`
(rolbypassrls=TRUE) bypasses the RLS policies on its underlying
tables entirely unless security_invoker=true is set — the base
tables' own RLS is irrelevant once queried through such a view.

Auditing all views in public/portfolio found two more with the
identical exposure: v_capital_accounts and v_trial_balance (both GL
views). Fixed same-day: ALTER VIEW ... SET (security_invoker = true)
on both. All views in public/portfolio now carry security_invoker=true.

Any future view creation must set security_invoker=true explicitly —
this is not a Postgres default.

## scripts/refresh_schema.py — stale-DATABASE_URL bug, fixed at the root

Fixed in the same commit that added revenue_events/v_profitability_events
to the schema snapshot (34d9a7e). The script previously read DATABASE_URL
straight out of apps/api/.env, a file that has repeatedly gone stale
(password rejected by Postgres) — documented by its own commit message
as having bitten this repo three separate times (fee34's manual
DB_PASSWORD substitution, fee36's refresh confusion, and this one).

Now resolves via apps/api/scripts/_db_connect.admin_dsn() — the same
probe-before-use resolver every verify script already relies on. It
actually opens a connection to confirm a candidate DSN works before
using it, rather than trusting that a variable's presence means it's
correct. Fails loud with the provenance chain on failure; prints which
source actually worked on success.

No further workaround (manual DB_PASSWORD substitution, a separate
_run_refresh_schema.py helper, etc.) should be needed going forward —
if stale-DATABASE_URL symptoms recur after this fix, that's a signal
something upstream of resolve_dsn itself has changed, not a reason to
re-introduce a bespoke substitution.

## v_capital_accounts is structurally broken (found by fee42b)

Returns ZERO rows unconditionally. It groups on
journal_lines.dim_member_series_id, a column with no backing table, no
FK, and NULL on every deployed row — while the view's own WHERE clause
requires that column NOT NULL. It also keys on journal_entries.vehicle_id,
and every deployed SPV has vehicle_entity_id NULL. Fixed nowhere yet;
fee42b worked around it by reading cumulative investor figures directly
from posted spv_transaction_allocations instead of this view.

**UPDATE 2026-09-03 — fee43 shipped GL posting and did NOT fix this.**
Measured, not assumed: every posting template fee43 added declares
`dimension_source='none'`, so no posting path populates
`dim_member_series_id`. It cannot, because there is still no
`dim_member_series` table for that id to reference — populating it would be
inventing a key. The earlier expectation on this line ("real fix requires
fee43's GL posting work") turned out to be wrong: this view's brokenness is a
DIFFERENT dimension than the vehicle-routing problem fee43 solved, and closing
open question #3 did nothing for it.

The real fix is its own piece of work: either create the missing
`dim_member_series` table and have a posting path populate it, or rewrite the
view to key on something that exists. Until then, do not build anything else
against v_capital_accounts expecting real data — check for yourself first, this
note is not a substitute for re-verifying.

## CLAUDE.md lines proposed by mkt01 (for operator review — NOT applied)

- Under "Database Schema Namespacing": add `market_data` to the list of real
  schemas that are not on any role's search_path (alongside `portfolio` and
  `litellm`). Always write `market_data.indicator_series`.
- New short section: "Market/macro indicators live in `market_data`, never in
  `portfolio.securities_global_prices` or the security master. Only investable
  benchmarks link via `indicator_series.security_global_id`. See
  docs/MARKET_DATA_DESIGN_V1.md."
- Under "Verify Script Discipline": "Any API that takes its key in the query
  string (FRED) needs a sentinel-key no-leak proof. httpx exception text embeds
  the full URL, so catch and keep the type name only."

## CLAUDE.md lines proposed by mkt02 (for operator review — NOT applied)

- Under "Verify Script Discipline" or "RLS": "When a write treats zero
  affected rows as a benign race (lost to a concurrent writer), first assert
  in the same transaction that the RLS context the policy needs is set.
  Otherwise the 'race' branch silently swallows an RLS refusal."
- New short note: "A new Render cron service needs its own Doppler → Render
  sync, and a block in render.yaml (manifest invariant). The market data
  nightly (`scripts/market_data_nightly.py`, 11:15 UTC) is one such service;
  see docs/market_data/RENDER_CRON_SETUP.md."
- Under "Database Schema Namespacing" (repeating mkt01's proposal): add
  `market_data` to the schemas that are not on any role's search_path.

## CLAUDE.md lines proposed by mkt03 (for operator review — NOT applied)

- Under "Verify Script Discipline" or "Permission Envelope Pattern": "Never
  let FastAPI validate a body whose 422 must not echo input. Its default
  handler returns pydantic's `input`, which is the caller's own data. Parse
  the raw body and sanitise with
  `errors(include_input=False, include_url=False, include_context=False)`.
  For `extra_forbidden`, do not name the undeclared field either."
- Under "Rule 1 — Never Hardcode Display Data": "Chart colours, like labels,
  come from the API response. Market data colours are a server-side palette
  (`services/market_data/palette.py`). See docs/MARKET_DATA_DESIGN_V1.md
  decision 18."
- New short note: "Market data values cross the API as strings (exact
  Decimal text, never exponent notation). The only float is the correlation
  coefficient, which is never stored."

## CLAUDE.md lines proposed by mkt02c (for operator review — NOT applied)

- Under "Rule 3 — Bi-temporal Writes" or a market data note: "A series can
  mix sources. `market_data.indicator_observations.source_provider` is
  per-row provenance (NULL = the series' own source). Anything that must hide
  Yahoo-sourced data has to filter ROWS, not just series — fred.sp500 before
  2016 is Yahoo."
- Under "Verify Script Discipline": "Never hardcode a live data shape (a link
  list, a count, a 'first observation' date) in a verify's assertion. Read it
  at runtime, and keep the original values as a required baseline subset.
  mkt02c had to edit three prior verifies because they froze the registry's
  shape."
- Under "Verify Script Discipline": "A ratio or percentage gate needs a
  minimum sample. With zero comparisons, '>= 99% agree' passes vacuously."

## CLAUDE.md lines proposed by mkt03b (for operator review — NOT applied)

- Under "RLS Is Now Genuinely Enforced": "There are three RLS session
  settings: `app.current_org_id`, `app.is_super_admin`, and
  `app.current_auth0_sub` (read only by the `users` bootstrap policy). None
  identifies a `users.id`, so per-USER privacy (as opposed to per-org) cannot
  be enforced by RLS today. User-scoped tables isolate the org in RLS and
  filter `user_id` in EVERY service query; the user id comes from
  `services.users.ensure_user`, never `permissions.get_user_id` (a uuid5 of
  the sub that does not match `users.id`)."
- Under "Verify Script Discipline": "In apps/api a 401 proves nothing about a
  route: `auth0_jwt_middleware` answers 401 before routing, so a missing path
  also returns 401. Prove a gate with the IDENTICAL request both ways, and
  require the authenticated one to reach a real route."
- Under "Verify Script Discipline": "A count that excludes fixtures with
  `NOT (<tag predicate>)` silently drops every real row where the tag column
  is NULL. Use `NOT coalesce((<predicate>), false)`."
