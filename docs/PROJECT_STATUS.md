# Project Status — open blockers and tracked follow-ups
SECMASTER2 (2026-11-07, SEC mutual-fund share classes into the master (dry run: 22,789 new / 4,609 matched; 10 Olivo funds would relink, none relinked), the FinanceDatabase attributes load fixed (etfs/ASE.csv is not published) + US mutual-fund categories, US / international / EM / cap fund categories with REFINE proposals, the load summary with every count linked to its list; schema APPLIED, job seeded OFF; verify WRITTEN, not yet run): see the top entry.
VISIBILITY1 (2026-11-07, team-based staff visibility — org admins see every family, staff their assigned / team / reports' families, an unassigned family only its owner; one shared helper on every route, Team panel, preview and an audited org switch that is OFF everywhere; verify WRITTEN, not yet run): see the top entry.
BULKAPPROVE1 (2026-11-05, Approvals → Bulk approve for authority & custody changes and held-away entries — the SAME single-approval service per item, per-item results, cap 200, audit per item + per batch; verify WRITTEN, not yet run): see the top entry.
BUGFIX3 (2026-11-05, the Olivo-pass CRITICAL: a member read every family's positions — every portfolio read now takes the one shared visible-entity rule; private investments without a commitment, household Recalculate, provisional import rows live, editable addresses, permission-true CRM controls, manual-asset counts, option picker, fixed-point money; verify WRITTEN, not yet run): see the top entry.
PORTENTRY2 (2026-11-05, what HollisMuse hit onboarding the Olivo family — legal entities join an existing household and own accounts, account type / tax / billable fields + DVP reasons, permission-true CRM controls, the ownership views agree, picker / dates / dialogs / detail keys / seasonal addresses / Households nav, staff security search; schema APPLIED; verify WRITTEN, not yet run): see the top entry.
PORTENTRY1 (2026-11-04, the Olivo entry-plan gaps closed — CRM status / marital / sub-type vocabularies from the config table, ILIT / LP / foundation registry keys, ownership % for org admins, edit_crm_contact for support staff, manual positions for any holding (org-scoped manual assets, reported relink), direct assets + private investments with commitments, the integrated-account one-time import (Super Admin, maker-checker, superseded by the feed), family-scoped net worth refresh after every write, _verify_self fast-forward fix; schema APPLIED; verify WRITTEN, not yet run): see the top entry.
CRMDETAIL1 (2026-11-04, the CRM holds a multigenerational family — 29 governed relationship types with a Relationships panel, typed detail fields for trusts / charities / LLCs / LPs, maiden name + date of death + minor flag, portfolio.liabilities netting into net worth (gross / liabilities / net), permanent demo data protected from every clean-up; schema APPLIED; verify WRITTEN, not yet run): see the top entry.
PERMS1 (2026-11-04, ONE source of permissions — the authz Principal = role ∪ profile ∪ permission-set grants in one statement, client rule applied, the key "staff" grants nothing; org_admin baseline (+9 keys, no approve_*); org admins create deals and households (Joe's decisions 1-2); WF-06 / WF-03 / REP-10 / WF-04 / WF-05; HollisMuse re-run test rows removed; verify WRITTEN, not yet run): see the top entry.
REP12 (2026-11-04, Replication Studio sprint 12 — the note-versus-replica check across NEW issues: every new / restated terms version checked automatically per org, a DRAFT replica only for an eligible note, the comparison when quoted, the "New issues" feed + alerts, the Super Admin's underlying queue (one-click / multi-confirm through confirm_resolution); schema APPLIED; verify WRITTEN, not yet run): see the top entry.
STAFFROLES1 (2026-11-04, "see staff data" (is_internal: every internal firm role) split from "skip permission checks" (is_staff, unchanged); org admin / support staff read the CRM, approvals unchanged; verify WRITTEN, not yet run): see the top entry.
REP11 (2026-11-03, Replication Studio sprint 11 — PHASE 3 BEGINS: note replicability verdicts from the 424B2 terms (static 11 / approximate 3 / not replicable 40 over today's 54; 0 buildable — no underlying confirmed), replicas from rep8's options machinery plus a Treasury leg, the embedded-fee estimate only when quoted, the X-ray marker under the hypothetical-performance policy; schema APPLIED; verify WRITTEN, not yet run): see the top entry.
PREVIEWPERF (2026-11-03, EDGAR bulk-download live preview back under 2 s — round trips, not SQL; no index; verify WRITTEN, not yet run): see the top entry.
VHK2 (2026-11-03, verify housekeeping round 2 — no regression-set check frozen against live state; no application code; verify WRITTEN, not yet run): see the top entry.
Last updated: 2026-11-02 (rep10.structural — Replication Studio sprint 10, PHASE 2 COMPLETE: tilts (security / sector / factor, maker-checker, live active share and HYPOTHETICAL tracking error or "not estimated — needs prices"), completion portfolios around holdings elsewhere and overlap consolidation, charitable in-kind lot gifting (estimate comparison, manual transfer letter, received date = gift date, rebuy), the coverage-first backtest under the firm's hypothetical-performance policy; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-11-02 (vhk1.structural — verify housekeeping: every regression-set verify's git / route / changed-line self-checks end at its sprint's OWN merge, not the working tree; empty real data is a [FIND]; dates at the call; fast scoped cleanup and --self-checks-only / --cleanup-only in every script; no application code; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-29 (rep9.structural — Replication Studio sprint 9: block trading, allocation and DVP settlement, manual-first — blocks over approved account instructions under the firm's approved allocation policy, the allocation fixed at approval, a printed order execution sheet always offered (also for one instruction), fills at the computed average price, pro-rata partials with deterministic rounding and the minimum allocation, per-account DVP settlement and the close rule, corrections only with a reason and a second person, a FIX router interface not connected; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-28 (marketdata1.structural — the vendor-neutral security-data layer: provider registry with licence facts, ranks per data type, budgets, quality events, free adapters (custodian valuations, N-PORT, issuer files, FRED, CSV uploads with maker-checker, adviser entry), a not-connected vendor slot, every price reader switched; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-28 (rep8.structural — Replication Studio sprint 8, phase 2 starts: the options sleeve (buffer / income ETFs as core + options package), option contracts in the security master, adviser-entered quotes, the payoff lab, the options tax gate (pending-counsel straddle items labelled), options proposals → manual per-leg instructions, rolls and expiry; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-28 (dealroom3.structural — Marketplace Deal Room Phase 3: watermarked and view-only documents, access expiry and pause on a lapsed eligibility, the activity log, Q&A, investor advisors, notifications, the return-or-destroy notice, DRAFT 506(b) / 506(c) rules; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-28 (lotmethod.structural — the lot-relief method an adviser chooses: household default, account override, per-proposal override, maker-checker; a truer tax-optimized ranking by estimated tax per dollar raised; the custodian's own cost-basis method and average-cost holdings; harvest / transition / X-ray follow it; plus BUGFIX2: one shared scoped verify cleanup with --cleanup-only, B2–B6; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-27 (rep7b.structural — Replication Studio sprint 7b: account AUTHORITY (read_only | advise | managed — a household setting, overridable per account, maker-checker) and CUSTODY (integrated | other_custodian | outside) as two separate settings; rep7's held-away guard re-keyed on authority; advise one-off proposals → member / other-adviser instructions + letter; managed at another custodian → MANUAL DVP settlement instructions; schema APPLIED; migration writes no rows (every account behaves as before); verify WRITTEN, not yet run; see the top entry). Previously 2026-10-26 (rep7.structural — Replication Studio sprint 7: held-away accounts and the portfolio X-ray — read-only positions and lots for accounts held elsewhere via manual entry, file upload through rep3's mapping, Chancery statements and an aggregator interface (none connected), with source and as-of on every row; held-away data in household wash-sale control, the gain budget and the loss bank; the stale-data confirmation task; every trade path refuses a held-away account; the X-ray with its one-click join request; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-24 (rep6.structural — Replication Studio sprint 6: the tax engine — member tax profiles and carryforwards, the substitution table and its denials, household wash-sale CONTROL replacing rep5's flag, daily loss harvesting as proposals, the loss bank and tax-alpha ledger, declared outside trades, the custodian-harvest switch, the Tax screens; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-23 (rep5.structural — Replication Studio sprint 5: accounts join models with account-level settings and exclusions, the lot-level transition analysis under a gain budget, rebalance proposals from join / model change / drift / cash flow / manual, approval on human tasks, approved proposals → execution instructions through rep2; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-23 (rep4.structural — Replication Studio sprint 4: build a model from a fund — "Holdings of <TICKER>" models from approved terms + live holdings into rep3's registry, the five-question viability check, the fund-versus-replica cost comparison, the registry "join the existing model" offer, holdings / terms monitoring that drafts versions for review; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-23 (rep3.structural — Replication Studio sprint 3: model management — one model registry, Excel/CSV upload with column mapping, a permanent upload log, the model library (platform + org shelves, copy or subscribe), composite (sleeve) models, approvals on governed human tasks, rep1's terms approvals moved onto them; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-22 (rep2.structural — Replication Studio sprint 2: custodian capabilities, trade adapters, governed human tasks on the workflow engine, execution instructions; schema APPLIED; verify WRITTEN, not yet run; see the top entry). Previously 2026-10-22 (rep1.structural — Replication Studio sprint 1; previously bulkfetchui3.structural — Bulk download: sortable
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


## 000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000. REP13 — Replication Studio phase 4 begins: the tax-aware long/short extension (130/30, 150/50) on any custodian that declares margin, short selling and stock borrow; schema APPLIED; verify WRITTEN, not yet run (2026-11-07)

Branch rep13-work. Full description: docs/REPLICATION_STUDIO.md, "rep13". Task 1 findings: docs/REP13_DISCOVERY.md.

**What was built**
- **Schema.** `docs/rep13_part1.sql`, APPLIED (`_apply_rep13.py --verify`: SCHEMA OK).
  - New capability keys `stock_borrow`, `margin_initial_pct` and `margin_maintenance_pct`, seeded `unknown` for altruist / manual.
  - New tables: `suitability_records` (the margin agreement, maker-checker), `long_short_settings`, `borrow_rates` (platform + org) and `long_short_proposals`.
  - `portfolio.models`: kind, gross pair, signal source, as-of, never-short list. Negative weights are allowed only on a long/short model (enforced by a trigger).
  - Sides `sell_short` / `buy_to_cover` on proposal trades, instructions and blocks.
  - RLS on every operation.
- **Services.** `services/long_short_rules.py` (pure: both books, borrow, the cost comparison, tracking error, IRC §1233 short-term, §1259, §1091(e), disclosures) and `services/long_short.py`.
- **Hooks into existing code.**
  - rep5 generate / submit / approval precheck / execute.
  - rep3 upload / edit / submit.
  - rep2 `create_instructions`: the §1259 check and the eligibility check run on every SELL SHORT.
  - `route_trade_list`: the adapter refuses an undeclared short; the manual route is always available.
  - Order sheet; harvest and drift sweeps skip long/short accounts; the join gate.
- **Routes and UI.** 14 routes; AUTHZ1_ROUTES and VISIBILITY1_ROUTES regenerated (`--check` CURRENT). Web: Account → Long/short, and the long/short panel on a proposal (`lib/longShort.mjs` fails closed). `tests/replication/rep13.test.mjs`: 8/8.

**No live custodian declares margin, short selling or stock borrow.** Altruist is unknown; Schwab, Fidelity, Pershing and IBKR have no capability rows. So no real account can go long/short until a Super Admin records evidenced capabilities.

**verify_rep6.py changed** (it is the regression for this sprint):
- It counts audit_log, member_todos, domain_events, workflow_*, users, user_roles, roles, accounts, positions, lots, transactions, assets, balances, org_settings and securities by FIXTURE rows only. This is the visibility1 fix 1 rule.
- It seeds roles through `rbac.grant_named_role` / `grant_org_admin`, with a seed-time `check_fixture_roles`, and its teardown plans user_roles by fixture user. This is the secmaster2 fix 1 rule.

**Not run:** verify_rep13.py, verify_rep6.py, npm build (WSL memory).

**Open for Joe:** signal source (sub-adviser weights assumed); which custodian first; default limits for 2nd Act's members; overlay bps; counsel on the §1259 identical rule.

**Operator steps**
1. Run verify_rep13.py.
2. Add the merge sha to `_verify_self.SPRINT_MERGES`.
3. Refresh the schema snapshot.
4. Redeploy the API and the scheduler.

## 00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000. PRICES1 — end-of-day prices: the price receptor (a documented plug-in contract), the first free source (Yahoo, internal only), store + coverage with reasons, the operator backfill, the prices job, readers that compute; schema APPLIED, job SEEDED (OFF); verify WRITTEN, not yet run (2026-11-07)

**Branch** `prices1-work`. Discovery: `docs/PRICES1_DISCOVERY.md`; design: `docs/SECURITY_DATA_DESIGN.md` ("PRICES1").
Schema `docs/prices1_part1.sql` APPLIED (`scripts/_apply_prices1.py --all`: SCHEMA OK): price-store columns
`adjustment_factor` + `bar`, quality kinds `invalid_value` / `currency_mismatch`, table
`portfolio.security_price_coverage` (a policy for every operation), providers `yahoo_chart` (active) and `tiingo`
(not connected), eod_price rank 5 / 6. The job "system:Security data — prices" SEEDED with its trigger **inactive**.
Verify `apps/api/scripts/verify_prices1.py` WRITTEN, NOT RUN (in `scripts/regression_set.txt`). No price was fetched
into the store by the sprint (the backfill is the operator's; only its `--dry-run` ran: 29 securities, 29 calls, ~41 s).

**Findings (Task 1).**
* 1a — the provider contract had no price-fetching method at all (`fetch()` refreshes "what you cover today", and
  `nightly.coverage()` covers nothing for eod_price), no identifier an adapter keys by, no date range, no bar shape, no
  adjusted / unadjusted statement, no zero / currency quality rule, no reason for `price_coverage`, no checkpoint.
* 1b — Stooq answers every request with a JavaScript proof-of-work **bot wall** (not usable, not circumvented); Yahoo
  chart works keyless for stocks, ETFs, mutual funds and money market — its `close` is **split-adjusted** (NVDA
  2024-06-07 120.89, traded 1208.88) so the adapter undoes later splits; Tiingo / Polygon / Nasdaq Data Link need keys
  (none in Doppler); Alpha Vantage free = 25 calls/day. **`FMP_API_KEY` is already in Doppler** [FIND] — unused.
  Chosen: **primary yahoo_chart** (internal use only, member display not_allowed, a launch blocker), **fallback
  tiingo** (written, not connected until `TIINGO_API_KEY`).
* 1c — the coverage set is **29 securities** (28 held in live positions, all 2nd Act Capital; + ACWI benchmark); every
  model / replica / snapshot / option / note / substitution / backtest table is empty; no watch-list or benchmark
  table exists (benchmarks SPY / AGG / ACWI are a code constant).
* 1d — every reader goes through the layer (internal); the roll-up and drift NEVER asked it, and the X-ray's tracking
  error / harvest value were hard-wired to "needs prices". SSVI surface reads yfinance directly [FIND, untouched].

**Built.** `providers/eod.py` (the receptor: EodSpec / EodLicence / EodBar / EodSeries / EodPriceProvider /
contract_problems / RecordedHttpGet / RealHttpGet); `providers/yahoo_chart.py`; `providers/tiingo.py`;
`providers/vendor_stub.py` + `VendorTemplateProvider` (the paid vendor template — `vendor_slot` unchanged);
`services/security_data/prices.py` (coverage set, quality judge, ingest, backfill + checkpoint, the nightly step);
`writes.upsert_eod_bars` (system-axis restatement) + `writes.set_price_coverage`; `layer.get_market`;
`scripts/prices_backfill.py`; the job in `security_master_jobs`; readers: `networth.value_lines` (price else entered
value; member licence by default), `account_models.load_holdings` (fresher close, `security_price`), the X-ray's
tracking error + harvest value + `member_price_filter` (live and stored runs), `GET /security-data/lookup` (a non-staff
caller is always answered for member display; staff see `display: internal`).

**Findings while building.** [FIND] the grids (`position_current_value` called directly) still show the entered value —
the card / household / snapshot use the price; [FIND] `harvest_candidates_price_chk` admits only `account_position` /
`none`, so a harvest candidate priced from a fresher close is still labelled `account_position` there (the trade line
carries `security_price`); [FIND] Yahoo's adjusted close carries ~1e-8 binary noise, so the adjustment factor is kept
to 6 dp and as of first store — a later dividend does not restate history; [FIND] a spike up and back flags both the
jump and the reversion as outliers.

**Operator steps** — see `docs/SECURITY_DATA_DESIGN.md` "PRICES1 operator steps": backfill `--dry-run` → the real run
→ switch "Security data — prices" on (Security master → Platform jobs) → optional `TIINGO_API_KEY`.

**UPDATE (fix 1, 2026-11-07).** The first full run was 132 PASS / 3 FAIL. (1, CODE) `services/xray.py` `_priced_estimates` called `SD.get_market` through the `services.security_data` package, but the package re-exported only get / get_bulk / history, so the X-ray's priced path raised `AttributeError` for every fund that reached it (it would have crashed in production). Fix: `get_market` is now exported with the others. An AST sweep of every `services.security_data*` name used across services/, routers/ and prices_backfill.py (401 references) found no other missing name. (2) The licence section was skipped only because of 1; it now passes: the member is hidden from the Yahoo-only price, staff see 50 labelled internal for the identical request, and the X-ray reads "needs prices" for a member and "[internal]" for staff. (2b, VERIFY, newly reached once 1 was fixed) The backtest check compared the engine's text: rep10's `backtest.simulate` writes `str(Decimal)`, so an exact zero reads `'0E-10'` (the front end reads it with `Number()` → 0.00 %). It now compares exact Decimals at the same 1E-10 scale; the values were already right, and nothing was weakened. (3, VERIFY) Nested verify_marketdata1 seeded U_SUPER with the role STRING only, so since BUGFIX4 the app did not treat it as a Super Admin: 403 on the provider admin. Now every fixture role is a `user_roles` grant (`rbac.grant_named_role` for super_admin, `grant_org_admin` as before), `users.role` is its mirror, a seed-time `check_fixture_roles` asserts that grant, mirror, `rbac.load_principal` and the `services.authz` Principal agree, and teardown plans `public.user_roles` by fixture user. verify_prices1 already seeded this way (grant_named_role / grant_org_admin plus its own check_fixture_roles), which is why its admin checks passed. Yahoo licence row (live, read-only): `yahoo_chart` active, `lic_member_display = not_allowed`, `lic_redistribution = false`, platform eod_price rank 5; `tiingo` not_connected, rank 6. Proof: one-offs driving each verify's own sections on fixtures. verify_prices1 (schema, seed, contract, quality, store, readers, licence, teardown): 67 PASS / 0 FAIL; X-ray tracking error 0.1590997265 and harvest value 100.00 as hand-computed, "needs prices" with no series and no exception. verify_marketdata1 (seed with the role check, provider admin, teardown): 15 PASS / 0 FAIL; Super Admin GET 200 with can_write and editable = PROVIDER_FIELDS, viewer and org admin 403 "Super Admin required" on GET and on a valid PUT, the real csv_upload row unchanged. Both left zero fixture rows, counted tables at pre-test counts and real fingerprints unchanged. `--self-checks-only`: prices1 17 / 0, marketdata1 5 / 0. The full verify_prices1.py / verify_marketdata1.py / npm build were NOT re-run (WSL memory).

## 0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000. SECMASTER2 — the security master finished: SEC mutual-fund share classes, the FinanceDatabase load working again, sharper fund categories, the load summary; schema APPLIED, job SEEDED (OFF); verify WRITTEN, not yet run (2026-11-07)

**Branch** `secmaster2-work`. Discovery: `docs/SECMASTER2_DISCOVERY.md`; design: `docs/SECURITY_DATA_DESIGN.md`
("SECMASTER2"). Schema `docs/secmaster2_part1.sql` APPLIED (`scripts/_apply_secmaster2.py --all`: SCHEMA OK); the job
"system:Security data — mutual funds" SEEDED with its trigger **inactive**. Verify `apps/api/scripts/verify_secmaster2.py`
WRITTEN, NOT RUN (in `scripts/regression_set.txt`).

**Findings (Task 1).**
* 1a — the held attributes run (`12611760-…`, 2026-11-06) died on `etfs/ASE.csv`: FinanceDatabase publishes
  `equities/ASE.csv` but no ETF file for ASE. Every other file answered 200 and had already been applied. Fixed by the
  file list (ETF: ASE out, NCM in) + `funds/NAS.csv` (US mutual funds, new) + a clear held failure
  (`SourceFormatChanged`) for a missing file / column / empty load.
* 1b — `company_tickers_mf.json` = 28,647 share classes (12,304 series, 1,160 CIKs), public domain, **no names**, and it
  includes the ETF classes of open-end funds (VTI, VOO, BND, QQQ). The CHECKs admitted no SEC id and no
  `mutual_fund` type: ids added (`sec_series_id`, `sec_class_id`, unique live class id), type stays `fund`, structure
  is the new attribute `fund_structure`.
* 1c — `category` alone is ambiguous (Emerging Markets under Equities AND Fixed Income; Large Cap under Derivatives;
  Developed Markets holds US and international ETFs alike); ETFs / mutual funds carry no country; 0 N-PORT snapshots
  are loaded; `fund_category_taxonomy` has 0 rows. The group is now kept (`fund_category_group`) and the name splits
  region / cap.
* 1d — the admin page had no structure split, no "missing" counts, no attribute / price coverage, and no count was
  linked to a list (no master-wide list existed). Added: GET `/admin/security-data/master/summary` + `/securities`.
* [FIND] `scripts/secmaster_initial_load.py` trusted any `DATABASE_URL` already in the shell — the stale `~/.bashrc`
  copy — and died on "password authentication failed"; it now always resolves through Doppler and probes.
* [FIND] `_apply_secmaster1.py --activate-jobs` switched on EVERY job in `J.JOBS` — it would have switched the new
  mutual-fund job on as a side effect; now limited to its own four.
* [FIND] verify_secmaster1 fires every job through its own fixture transport and pinned the rank to exactly five
  providers: its fixture now records the two new files (funds/NAS.csv header; one share class on its ZZSMF ETF) and
  the rank check reads "the five, in order, first".

**Dry run — mutual funds** (live master, read only, 2026-11-07; `secmaster_initial_load.py --source sec_mutual_funds
--dry-run --would-relink 00000000-…-0001`): 28,647 rows, 28,646 kept (1 blank ticker); structures 21,854 mutual fund /
938 money market / 5,854 other classes; **new 22,789 · matched 4,609 (ticker on the US composite family) · conflicts 1
· unmapped 1,247** (not a mutual-fund ticker and not in the master); 3 review items; 77,585 identifiers and 100,377
attributes would be written. The live master was unchanged afterwards (15,970 securities, 0 SEC ids, 0
`sec_mutual_funds` attributes / reviews / files); only 2 `provider_call_log` rows (the real SEC requests) were written.

**Would relink — 2nd Act** (10 of 11 ticker-identified manual assets; NOTHING relinked): mutual funds FXAIX Fidelity 500
Index Fund · FFFGX Fidelity Freedom 2045 · FSKAX Fidelity Total Market Index · FXNAX Fidelity U.S. Bond Index · SWPPX
Schwab S&P 500 Index · SWTSX Schwab Total Stock Market Index · VWIUX Vanguard Intermediate-Term Tax-Exempt; money market
FDRXX Fidelity Government Cash Reserves · FGTXX Goldman Sachs Financial Square Govt MMF · SNOXX Schwab Treasury
Obligations MMF — each to a NEW share-class security. Not: "Olivo Core Equity" (SMA-CORE, not an SEC class). Bitcoin
/ Ether (digital assets) and the cash holding are never candidates.

**Refine proposals — 2nd Act** (`backfill_taxonomy_keys.py --funds --dry-run`, CHANGED: 0): refine 9, review 1,
unchanged 22.
  REFINE Invesco QQQ Trust · Schwab U.S. Broad Market · Schwab US Dividend Equity · Vanguard Morningstar Total Stock Market:
  taxonomy_sc_1 → taxonomy_mc_1_1 (US; no cap stated → large-cap weighted).
  REFINE iShares MSCI EAFE · Schwab International Equity · Vanguard FTSE Developed Markets: taxonomy_sc_1 → taxonomy_mc_1_3.
  REFINE Vanguard FTSE All World Ex US · Vanguard Total International Stock: taxonomy_sc_1 → taxonomy_sub_1_3_4.
  REVIEW Grayscale Bitcoin Mini Trust (category "Corporate Bonds", name bitcoin; keeps its hand key taxonomy_mc_7_3).
  Unchanged: Vanguard Total World Stock (global — no single region), the bonds / munis / TIPS / EM / small cap, and the
  7 mutual funds (no master link yet — they get categories after steps 2–5).

**Follow-ups.** Mutual-fund names (SEC series / class names are annual deltas; FinanceDatabase is internal-only);
OpenFIGI share-class FIGIs for mutual funds (budget); prices (prices1) next.

### SECMASTER2 operator steps (in order)

1. close the held attributes run — Security master → Platform jobs → "Close held run" on `12611760-…`.
2. Attributes re-load: `secmaster_initial_load.py --source finance_database --dry-run`, then real.
3. Mutual funds: `secmaster_initial_load.py --source sec_mutual_funds --dry-run --would-relink <org>`, then real; then
   step 2's real load once more (the new classes' categories).
4. `backfill_taxonomy_keys.py --org <org> --funds --dry-run`, then real, then `--refine`.
5. Let the nightly relink link the mutual funds (~07:00 UTC; or POST `/portfolio/manual-assets/relink`) and read its
   report (`portfolio.asset_relinks`); then step 4 again for the newly linked funds.
6. Household Recalculate.
7. Run the verify: `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_secmaster2.py`.

**UPDATE (fix 1, 2026-11-07).** The first full run was 126 PASS / 8 FAIL, all with one cause, all VERIFY (no code changed): since BUGFIX4 every role, Super Admin included, is decided from `user_roles` (`users.role` only mirrors it), but verify_secmaster2 and the nested verify_secmaster1 seeded their fixture Super Admin (also the platform-job runner, `…54d101` / `…53c101`) with the role STRING only, so it was neither Super Admin nor a holder of `run_platform_jobs` / `author_workflows`: 403 "Super Admin required" on /summary, /securities and the unknown-filter 422, the held-run close 403, the attributes run refused by the engine's permission check instead of holding on the 404 (so the re-fire skipped), the mutual-fund job errors=1, and secmaster1's 17 FAILs (platform-job delete 403 instead of 409, every job run refused). Discovery (read-only + a rolled-back fixture probe): a string-only `super_admin` resolves to role None / `is_super_admin` False in both `rbac.load_principal` and the `services.authz` Principal, and True once granted through `rbac.grant_named_role`; the REAL platform runner be8165db… holds `super_admin` in `user_roles` in its own org, so real platform jobs were never affected (unchanged). Fix (both scripts): every fixture role is a `user_roles` grant through the app's own helpers (`grant_named_role` for super_admin, `grant_org_admin` as before), `users.role` kept as the mirror; a new seed-time check asserts each fixture's role AS THE APP DECIDES IT (grant present, mirror agrees, both principal loaders agree) so a string-only seed fails at the seed, not 40 checks downstream; teardown plans `public.user_roles` by fixture user id, and the counts / leftovers include `public.user_roles` (by fixture user) and `public.roles` (by fixture org). No assertion was weakened; none of the 8 failed for a second reason. Proof: one-offs driving each verify's own DB sections (seed through jobs, isolation, real rows, teardown; no routes / node / npm / nested run): verify_secmaster2 107 PASS / 0 FAIL (Super Admin 200 on /summary and /securities?fund_structure=money_market, 422 on an unknown filter, the 404 HOLDS the attributes run with its reason and alerts only the Super Admin, the close 200, the trigger fires again and completes, the mutual-fund job completes through the engine); verify_secmaster1 138 PASS / 0 FAIL (= its 121 + 17; the platform-job delete 409, the nightly completes, and a member WITHOUT `run_platform_jobs` is still refused). Zero fixture rows remained, user_roles / roles at pre-test counts, real rows unchanged. `--self-checks-only`: secmaster2 18 / 0, secmaster1 8 / 0. The full verify_secmaster2.py / verify_secmaster1.py / npm build were NOT re-run (WSL memory). BACKLOG (catch-all, read-only grep, NOT fixed here; heuristic: inserts `users` with a role string and has no `user_roles` insert / `grant_named_role` / `grant_org_admin`; each will fail the same way when next run, a few may be false positives that only test the string): Super Admin by string only (36) — aibudget1, assistantquery, bulkfetch, bulkfetchui, bulkfetchui2, bulkfetchui3, ensemblesystemone, fixbugs, litellmavailability, litellmphased1b, litellmphased1c, litellmphasef, litellmphaseg, multitenant1, notefix1, notefix2, notetermsrouting, ownershiptreea, ownershiptreeb, portfolioa1, portfolioa2, portfoliob, portfoliod, portfolioe, portfoliof, portfoliog, previewperffix1, soc4, sprint24, staffvisbypass, superadminmenu, underlyingresolution, usermanagementfinish, workflowmgr4, workflowmgr5, workflowpermsfix; org admin by string only (21) — chancery11b, enrollurl, multitenant2, rls2, rlscutover, schedulerappservicefix, schedulercore, schedulerhistory, schedulernotify, schedulerux, scripttaskrefusal, smtpservice, sprint24, staffvisbypass, superadminmenu, tamodel1, tamodel2, usermanagementfinish, workflowmgr3, workflowmgr4, workflowpermsfix.

## 000000000000000000000000000000000000000000000000000000000000000000000000000000000000000. VISIBILITY1 — team-based staff visibility, enforced per org: the rule, the family closure, assignment screens, the preview and the switch (OFF everywhere); verify WRITTEN, not yet run (2026-11-07)

**What it does.** Discovery: `docs/VISIBILITY1_DISCOVERY.md`; the rule and how to turn it on: `docs/STAFF_VISIBILITY.md`;
every route and its treatment: `docs/VISIBILITY1_ROUTES.md` (generated, `apps/api/scripts/visibility1_routes.py --check`).
ONE implementation, `services/family_visibility.py`: super admin and org admins (`manage_org_settings`) see every family
of the org; a member sees their own family (BUGFIX2, unchanged); every other internal staff member sees the families
assigned to them, to a team they are on, or to anyone who reports to them (`users.manager_id`, transitively), plus the
families they are relationship manager of, plus the families they created that NO ONE is assigned to. An assignment to
any record of a family grants the whole family (a shared vehicle grants only itself and what it owns).

**The switch is OFF for every org.** `org_settings` `staff_visibility.enforced` (default false; no row exists). With it
off, behaviour is exactly as before this sprint — the switch rides on the Principal's one statement and every new hook
is a no-op without a statement. Only `PUT /admin/staff-visibility/enforcement` (org admin, confirmed against the preview,
audited) writes it; the generic settings route refuses the key.

**DECISION FOR JOE — owner semantics.** A relationship manager (`entities.relationship_manager_id`) KEEPS visibility after
the family is assigned to someone else; a creator (`households.created_by` / `entities.created_by`) sees an unassigned
family only, and loses it at the first assignment. A manager inherits reports' assignments, never their reports' created
or relationship-managed families. Reverse either in `FamilyGraph.visible_for`.

| Finding | Where |
|---|---|
| Five surfaces ALREADY restrict internal staff to the legacy assignment set today (semantic search, document review / download, assistant counts, `/portfolio/commitments`, ownership-tree staff route) — with 2 live assignments a support user sees almost nothing there. Unchanged with the switch off; the rule with it on | 1c |
| Removing a team member and deleting an assignment wrote no audit row (both do now); `users.manager_id` had no route or UI | 1e |
| Bare SQL totals the response scrubber cannot fix: `/dashboard/brief` blocks, `/tax/overview` totals, `/households-by-margin` aggregates — their row lists ARE scrubbed. OPEN | 1c |
| Today nearly every CRM record is a standalone family: 1 household, 0 `primary_household_id` set | 1a |

**Enforcement, when on, for a restricted caller only:** an app-wide dependency (`main._family_path_guard`) returns the same
404 as a missing id for another family's entity / household / account / position / transaction / liability / document /
relationship / proposal / instruction / gift / commitment id, path or query, every method; the shared scope
(`member_entity_scope`, `require_entity_access`) filters lists in SQL; explicit filters on households, X-ray households,
approvals, notifications; `require_visible` on body-id writes; a response scrubber in `rls_context_middleware` removes any
JSON row naming an invisible family; the legacy resolver's callers follow the rule. Cost: one statement (the org graph),
once per request, restricted callers only.

**Screens.** Team panel on the household page and the CRM record (assigned users / teams with a role label, owner shown,
add / remove audited — org admin or `manage_members`, and only on families one can see). Admin → Staff visibility: what
each person will see and why, who would see none, the switch with the preview on its confirm step, Unassigned families
with bulk assign, People and managers (cycle-checked, audited), plus the SOC Phase 2 teams / assignments manager.

**Operator steps (in order):**
1. Admin → Staff visibility → create teams; add members; set each person's manager (People and managers).
2. Assign families — Team panel on each household / CRM record, or Unassigned families → Assign selected. **Assign the
   Olivo demo** (household "Olivo Family", tag `demo-olivo`): Team → the demo presenter or a "Demo" team, Lead adviser.
3. Review the preview — every internal user's families and why; fix everyone under "Would see no family".
4. Switch on — "Turn enforcement on…" → confirm (org admin). Audited.
5. Run `apps/api/scripts/verify_visibility1.py` (written, NOT run by the sprint).

**UPDATE (fix 1, 2026-11-07).** The first full run (06:19–07:03 UTC) was 201 PASS / 7 FAIL, and reading the FAILs turned up 1 live-data defect. (A, VERIFY) The "manager does not inherit" check compared M on g4 with M on the ORIGINAL graph. But g4 also carries a3 (F3 assigned to S1, who reports to M), so M rightly sees F3 through his report's ASSIGNMENT: original {H1, H2}, g3 {H1, H2, H3}, g4 {H1, H2, H3}. The check now compares g4 with g3 (equal), plus the case its label claims: S1 is ONLY the relationship manager of one family and ONLY the creator of another; S1 sees both and M sees neither (the code was right). (B–D, CODE) Enforcement cost 5 statements per request, not 1. `scope_for_request` took its own pool connection: BEGIN, the `set_config` GUC statement, GRAPH_SQL, COMMIT, and asyncpg's release reset. Now `family_visibility.preload_scope` reads the graph on the Principal's own transaction (`users.resolve_request_principal`), when that transaction's org is the principal's. Measured off → on: CRM list 11 → 12, households 11 → 12, positions 39 → 40. The Principal itself is unchanged off / on (the switch still rides on its one statement); if the preload cannot run, `scope_for_request` re-reads and still fails closed. (E, CODE) The POST /approvals/bulk route record gained `any(view_*|approve_*)` because `decide` called `BA.pending(`, which the route-record expansion resolved to the router's own GET handler, also named `pending`. The filter now goes through the internal, ungated `BA.visible_item_ids`; the record is identical to main's (`authz1_routes --check` CURRENT), the 404 for an invisible item is kept, and an unknown type is a 422, not a 500. (F, VERIFY) The three POSTs to LiteLLM `/v1/messages` were the admitted note's background `extract_from_note`: one call plus the SDK's two retries, all refused by the verify's transport stub, so nothing left the machine. It is now stubbed at its current signature and asserted called once. (G, VERIFY) verify_staffroles1 counted all of `public.audit_log`; the nightly refresh wrote 2 real `manual_asset_relinked` rows mid-run (778 → 780). It now counts fixture orgs / users / resource ids only (live: whole table 780, fixture-scoped 0, both nightly rows outside it). The same whole-table audit_log count is still in 26 verifies, NOT fixed here: authz1, bugfix1, bugfix2, crmdetail1, dealroom1, dealroom2, event_emission, fee42b, lotmethod, perms1, portentry1, portentry2, rep1–rep12 (incl. rep7b), usermgmt1. (H, CODE + operator repair) `relink_manual_assets` skipped only asset_type 'cash'. At 07:00:56 UTC the nightly relinked Olivo's digital assets "Bitcoin" (6426ce89…, BTC) → "Grayscale Bitcoin Mini Trust (BTC)" (b3e02657…) and "Ether" (abf477fc…, ETH) → "Grayscale Ethereum Staking Mini ETF Shares" (d7719ee0…). It now skips `held_away.NO_MASTER_ASSET_TYPES`: BUGFIX4's `NO_MASTER_KINDS` (cash, digital_asset) plus `cash_holding`, the stored form of a named cash / sweep line. `money_market` stays relinkable (a fund with a real ticker; BUGFIX4 never excluded it). New `scripts/revert_relinks.py` (`--org`, `--dry-run`) unlinks each still-linked relink as a new system version (id kept, prior version archived, relink closed, audited; idempotent). The sprint's live dry run listed exactly those two. Proof: a one-off driving the verify's own sections (pure rule, switch, writes, members + isolation, relink kinds, statements, real orgs, teardown) gave 113 PASS / 0 FAIL against the live DB. The pre-fix relink (fde0ad5) reproduced both wrong relinks on fixtures; after the fix, the digital asset and cash_holding stay unlinked and the equity with the same ticker still links; revert dry run / real / CHANGED: 0 all passed. Zero fixture rows remained, real rows unchanged. `--self-checks-only`: visibility1 0 FAIL, staffroles1 19 / 0. The full verify_visibility1.py, verify_staffroles1.py and npm build were NOT re-run (WSL memory).

**Operator step (fix 1, H — not run by the sprint beyond the dry run):**
1. `doppler run -- python3 apps/api/scripts/revert_relinks.py --org 00000000-0000-0000-0000-000000000001 --dry-run`
   — expect exactly Bitcoin (BTC → b3e02657…) and Ether (ETH → d7719ee0…), both trigger nightly, 07:00:56 UTC.
2. The same without `--dry-run` → `CHANGED: 2` (a second run → `CHANGED: 0`).
3. Recalculate the Olivo household.

## 00000000000000000000000000000000000000000000000000000000000000000000000000000000000000. ROLLUP2 — one net-worth roll-up everywhere: unclassified holdings counted, funds classified by category, duplicate ownership repairable, family / charitable split; schema APPLIED; verify WRITTEN, not yet run (2026-11-07)

**What was wrong.** Discovery: `docs/ROLLUP2_DISCOVERY.md`. Two net-worth formulas existed. The entity Net worth card and its
Recalculate read the stored `entity_holdings` snapshot, and the rollup that wrote it SKIPPED every valued position with no
taxonomy key (`entity_holdings.taxonomy_key` is NOT NULL, there was no bucket); the bugfix4 household total computed live
and counted them. Recomputed live with the real service code, for every one of the 40 Olivo household entities the
card = keyed lines only and the household rule = all valued lines, to the cent — the unkeyed funds were the whole gap.

| Owner (entity id prefix) | Card before (snapshot) | Now (live core) | Of which Unclassified |
|---|---|---|---|
| Dominic J. Olivo Revocable Trust (18400d6a) | 140,054,602.00 | 188,302,502.00 | 48,247,900.00 (SPY, VXUS, BND, IWM) |
| Olivo Family Foundation (b06b7417) | 4,500,000.00 | 44,950,000.00 | 40,450,000.00 (VT, BND) |
| Olivo 2016 CRUT (60c20f3b) | $0 — no snapshot row ("not yet rolled up") | 11,100,000.00 | 11,100,000.00 (SCHD, SCHR) |
| Cipolla Olivo Holdings LLC (27783cda) | 35,789,160.00 | 40,568,160.00 | 4,779,000.00 (SCHB, SCHF, SCMB) |
| Theresa Olivo Family Trust (3f098811) | 39,420,000.00 | 44,009,000.00 | 4,589,000.00 (SCHD, SCHP) |
| MTO Capital LLC (4ea9aa44) | 45,783,860.00 | 48,775,860.00 (= Muse's expected + the unentered −187,500 option) | 2,992,000.00 (QQQ) |
| Marguerite Meloni Olivo (801422c7) | 2,570,000.00 | 5,391,000.00 | 2,821,000.00 (SCHB, SCMB) |

Household (live): gross 716,925,590 · liabilities 52,800,000 · **Household total 664,125,590 = Family net worth
601,975,590 + Charitable 62,150,000** (CRUT, Giving Fund, Foundation) — both exactly Muse's expected figures.

Findings: **1c** Muse's Owners-list figures are not reproducible (two stored snapshots of different moments; neither
side right; there is no Owners list in the Consolidate code). **1d** NO live duplicate ownership pair exists — "Art
Holdings 60 % and 15 %" is the LP's stake drawn nested under Dominic's trust beside the trust's own 15 % (the tree now
says "via"); the real hole was `POST /entities/{id}/ownership` accepting a second live edge for a pair. **1e** 24 of 25
fund assets have a FinanceDatabase category; BTC is filed "Corporate Bonds" (wrong); mutual funds have no master link.
**[FIND] snapshot date skew**: the rollup dates its snapshot by the server's UTC day (2026-11-07 while New York was on
11-06), so a reader on the earlier day read the previous snapshot. **[FIND]** `verify_bugfix3.py` FIX 1's "unkeyed
positions are not counted / reported as skipped" and `kmap['mutual_fund']` checks encode the rule this sprint reverses.

**What changed.**
- `services/networth.py` — THE core: shares over both ownership tables, each live position valued once, a valued line
  with no key counted under the `unclassified` bucket and listed, a valueless line listed as skipped. The entity card
  (`liabilities.entity_net_worth`), Recalculate, the stored snapshot (`portfolio_rollup.compute_buckets`), the
  household total and its new **Owners** list (`household_total.owners`) all compute from it.
- Household page / summary API: **Family net worth / Charitable / Household total**, each gross / liabilities / net /
  Unclassified (`household_total.split`; charitable = foundations and CRUT / CRAT trusts), the Owners list and the
  Unclassified holdings. The entity card lists its Unclassified and valueless holdings.
- Fund classification (`services/fund_classification.py`, table `portfolio.fund_category_taxonomy` —
  `docs/rollup2_part1.sql`, APPLIED, empty): fund category → key, Super Admin `GET` / `PUT /admin/fund-categories`,
  `GET /admin/fund-categories/proposals`; the name cross-check sends a contradicted category to review. The
  `mutual_fund → Public Equity` kind default is removed; fund kinds are keyed only by category.
- Duplicate ownership: `GET /admin/ownership/duplicates` (Super Admin), a warning on the Ownership panel, a new
  percentage for a pair is a NEW VERSION (prior archived) unless `separate_holding: true` (a checkbox on the form).
- Held-away cash (and money-market) lines take an optional Name; a named cash line is its own Cash & Equivalents holding.

**Operator steps, in order (the sprint ran only the dry runs):**
1. `doppler run -- python3 apps/api/scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --funds --dry-run`
   (recorded: apply 23, review 2, unchanged 6, unclassified 1), then the same without `--dry-run`; then decide the two
   reviews and SGOV by hand: `--reclassify 63857a30-4845-4bf4-aef0-cf823aa54dfd:taxonomy_mc_7_3` (BTC → Digital Assets),
   `--reclassify a0e73fde-b1e2-4798-ad89-2215b49e6f5b:taxonomy_sc_2` (Fidelity U.S. Bond Index), SGOV
   `3fc0e3ad-3f5a-454d-8ac2-60917110b1e0` as you see fit.
2. `doppler run -- python3 apps/api/scripts/repair_ownership_duplicates.py --org 00000000-0000-0000-0000-000000000001 --dry-run`
   (recorded: 0 pairs), then without `--dry-run` only if it lists any.
3. Stored snapshots exist (`entity_holdings`, sunburst): `doppler run -- python3 apps/api/scripts/recalculate_rollups.py --org 00000000-0000-0000-0000-000000000001 --household f1aa5e56-3e76-427a-ab07-e93b4bbedd4f --dry-run`
   (recorded: 33 entities, the new snapshot equals the live card for every one), then without `--dry-run`.
4. Household Recalculate on the Olivo household's page.

**Verify:** `apps/api/scripts/verify_rollup2.py` — WRITTEN, NOT RUN (regression: `verify_bugfix4.py` once, flat,
VERIFY_NESTED=1). Added to `scripts/regression_set.txt`. Node tests `tests/rollup2` + the full suite pass (562 / 0 fail);
`npm run build` not run (WSL memory). `authz1_routes.py --check` CURRENT.

**UPDATE (fix 1, 2026-11-07).** The first full run was 138 PASS / 2 FAIL. (1, VERIFY + CODE + DISCOVERY) The duplicate fixture seeded two live `entity_relationships` edges for one pair, which the live partial unique index `entity_rel_active_unique` (from, to, type) WHERE valid_to IS NULL forbids, so it raised `UniqueViolationError`. Discovery 1d was wrong: before ROLLUP2 that POST was a 500 from the index, not a silent duplicate. A duplicate can really arise in two ways: inside `entity_ownership` (primary key only; `POST /entity-ownership` checks only the <= 100 % sum), or with one live edge in EACH table (`POST /entities/{id}/ownership` never looks at `entity_ownership`). The fixture now builds the duplicate through those two real routes (60 % + 15 %). Every assertion is kept: inflation reproduced at 60,000, reported, warned, dry run / real / CHANGED: 0, new version. Added: the index refuses the old state, and same-table rows are accepted and reported (rolled back). CODE: `separate_holding` on a pair that already has a live edge was a 500 from the index; it is now a 409 naming the edge, any index refusal is a 409, and the Ownership tab's checkbox is removed. The verify's 'separate holding recorded alongside' check asserted a state the DB forbids, so it was replaced by the 409, with nothing written. 1d corrected in docs/ROLLUP2_DISCOVERY.md. (2, CODE) `GET /entities/{id}/ownership`, `/ownership/history` and `/relationships` (routers/entity_graph.py) answered 200 with empty lists for another org's id, and for a missing id. This was NOT a leak: the body held no org A row, only the id the caller sent and the caller's own envelope. All three now 404 unless the id is a live entity of the caller's org (`_require_org_entity`, the net-worth rule). The other siblings already 404'd: tree, lookthrough, crm-relationships, details, liabilities, net-worth, permissions. Proof: a one-off driving the verify's own sections (catalog, seed, duplicates, scope, teardown) gave 30 PASS / 0 FAIL against the live DB. The pre-fix code reproduced org B's 200 on all 3 routes; after the fix, org B gets 404 and org A gets 200 with byte-identical bodies (9 route x entity pairs, seed row id normalized). Zero fixture rows remained. `--self-checks-only` 48 / 0, node tests/rollup2 7 / 0, authz1_routes --check CURRENT. The full verify_rollup2.py / verify_bugfix4.py / npm build were NOT re-run (WSL memory). Open, not fixed: neither ownership write path refuses a same-table or cross-table duplicate at write time (the report and repair handle both).

## 0000000000000000000000000000000000000000000000000000000000000000000000000000000000000. BUGFIX4 — HollisMuse Olivo final report: held-away snapshots, the household total, jobs switch, entry form, identity and ONE role source; verify WRITTEN, not yet run (2026-11-06)

**What was wrong, what changed (each Muse finding with its ID).** Discovery: `docs/BUGFIX4_DISCOVERY.md`.

| ID | Finding | Status |
|---|---|---|
| MUSE-OLIVO-O-04-02 (HIGH) | 56 approved held-away lines, 24 live: an import is an account SNAPSHOT and the form saved ONE line per draft, so each later line replaced the earlier (32 lines, 13 accounts, all at the same as-of). | FIXED — one draft per account with all its lines (add / edit / remove before saving); a draft that replaces a live snapshot says so BEFORE saving (`GET /held-away/accounts/{id}/snapshot`, an acknowledgement is required) and after; every import and line shows live / partly live / superseded and by which import. Existing Olivo lines: operator repair script (below). |
| MUSE-OLIVO-O-04-03 (HIGH) | Roll-up excluded $7,545,600 of listed lines the X-ray counted. The brief's premise (no price series) was WRONG: the lines had a value but NO taxonomy key (master `fund` / `equity` types unmapped). | FIXED — the master's unambiguous types map (equity → Public Equity, fixed_income → Fixed Income, structured_note → Structured Products); a master line keeps the kind it was entered as; a listed line can carry its asset-class key. `fund` stays unmapped on purpose (equity and bond ETFs share it) — such a line is IN the household total, labelled "not yet classified". No free EOD price source exists, so every line is valued at its entered value (labelled), the X-ray's basis. |
| MUSE-OLIVO-O-04-03 (jobs) | Every platform job "schedule not active". By design: triggers seeded inactive (`_apply_secmaster1.py --seed-jobs`), `--activate-jobs` never run; the attributes job's one run is HELD (FinanceDatabase 404). | FIXED — Security master → Platform jobs: a Super Admin switch per job (audited `security_master.schedule_switched`), what each job fetches, the price gap, and a held-run warning. NOTHING was switched on. |
| MUSE-OLIVO-FORM | A25's cash saved against QQQ (stale CUSIP); "ETH" → the Grayscale ETF; cash needed a quantity. | FIXED — every line starts empty and a kind change starts it over; only the fields a kind shows are sent; cash and digital assets never resolve through the master's identifiers; cash / private take an amount with no quantity. |
| MUSE-OLIVO-K19 | Working capital ($1,400,000, kind "other") counted $0. | FIXED — a direct kind "Cash / working capital" (`cash_holding` → Cash & Equivalents); the backfill gained `--reclassify ASSET_ID:KIND` (operator). |
| MUSE-OLIVO-HH | No household total / Recalculate on the household's own page; Consolidate blank; AND the household gross DOUBLE-COUNTED owned members (found in discovery). | FIXED — `/portfolio/households/{id}` shows gross / liabilities / net / as-of, top holdings and Recalculate (`GET /households/{id}/summary`); one household aggregate (`services/household_total`) counts each position once at the household's share of its owner — also used by the People & Access card and Recalculate. Consolidate always says why it is empty. |
| MUSE-OLIVO-PI | Private investments: no edit; simplified kinds had no vintage / date; commitments not links. | FIXED — Edit (notes, commitment date or investment date, vintage) as a new version, history kept (`PATCH /private-investments/assets/{id}`, `GET …/history`); simplified kinds take vintage + investment date; commitment rows link to their capital-call projection. |
| MUSE-OLIVO-DISPLAY | "3.25E+6" lot quantities; every non-taxable account labelled "IRA / Roth". | FIXED — fixed-point quantities (server and render); the Tax section says "Tax-advantaged — <the stored status>". |
| MUSE-OLIVO-STUBS | microsoft / alphabet / IBM stubs. Created 2026-07-02 by the CRM Employment picker (one mousedown = a record). | FIXED — "Create '<text>'" is an explicit choice with a confirmation. The existing stubs are NOT deleted — for Joe: `microsoft` (9ba65ed6…), `alphabet` (a7c23cdd…), `IBM` (7b7e0a3a…), all llc, plus a leaked verify fixture `__verify17_stub__` (1dfc9429…). |
| MUSE-OLIVO-IDENTITY | culmina.io row has a fake `auth0_sub` (its own id) and never signed in; the Hollisworks-tenant sign-in arrived with no verified email and became a placeholder super admin (be8165db…). | FIXED (tooling) — People & Access lists placeholder logins prominently with their sign-in id and contact email and LINKS one to a pre-created user (one row per person; the stub is retired, never deleted). Maker-checker refusals name the maker, and — when the checker is the maker — that the sign-ins are one person. No real user was changed. |
| MUSE-OLIVO-ROLES | Super Admin read from `users.role`, People & Access writes `user_roles`. | FIXED — ONE source: every role, Super Admin included, is decided from `user_roles`; `users.role` is mirrored by every write path (assign role, approve pending, staff provisioning). Only a Super Admin may grant super_admin (it was grantable by any manage_members holder — inert before, not now). Real users differing today (read-only): ONE, 4e9477e1 (placeholder login; string `member`, RBAC `support_staff`) — no Super Admin differs, so the switch changed nobody's access. |
| MUSE-OLIVO-AGG-VTI | Top holdings AGG 24,831,150 / VTI 15,848,700 vs the file. | PARTLY — live rows match the file exactly (AGG 19,500,250; VTI 15,988,700); Muse's figures are not reproducible from today's data. The one inflating aggregation in the code (member-sum double count) is fixed and proven exact on a fixture. |

**Operator steps (none run by the sprint):**
1. `doppler run -- python3 apps/api/scripts/repair_heldaway_snapshots.py --org 00000000-0000-0000-0000-000000000001 --dry-run` — expect 13 accounts, 30 lines to restore, 2 collisions (A25's cash-as-QQQ vs its real QQQ line; one more) left as is; then the same without `--dry-run` (idempotent; `CHANGED: 0` on a second run).
2. `doppler run -- python3 apps/api/scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --reclassify eddf9851-7140-4d38-a558-486d019616e3:cash_holding --dry-run`, then without `--dry-run` (keys the 5 master equities too; the 25 master funds are listed — give each its class).
3. Recalculate the Olivo household (its own page now has the button).
4. If wanted: switch on the platform jobs (Security master → Platform jobs, or `_apply_secmaster1.py --activate-jobs`); close the held attributes run first.
5. Link the culmina.io / Hollisworks placeholder login to Joe's row in People & Access.

**Proof.** `apps/api/scripts/verify_bugfix4.py` WRITTEN, not run (WSL memory): reproduces O-04-02 and the household double count before proving the fixes; repair dry run / real / second run on a fixture; roll-up == X-ray == SQL; jobs switch on a fixture platform org only; regression `verify_portentry1.py` once. Run here: `node --test tests/bugfix4` 15/15 and the full node suite (one existing rep7 test needed the button kept as "Save as draft"); the repair script's `--dry-run` against the live org (read-only); `authz1_routes.py --check` CURRENT. npm run build NOT run (WSL memory).

**UPDATE (fix 1, 2026-11-06).** The first full run was 167 PASS / 3 FAIL. One fix was CODE, two were VERIFY. (1, CODE) The household gross read 1,435,000 for 45,000 held plus 1,400,000 cash. The brief blamed the unclassified fund line, but that line was already in gross (unclassified_value 20,000 passed). The real cause: `portfolio_rollup._current_positions` used `DISTINCT ON (owner_entity_id, asset_id)`. The fixture owner f1p holds the same security in two accounts (10,000 each), so the second account's line was silently dropped. That was the same 10,000, and the roll-up (`entity_holdings`) had the same gap. The key is now (owner, asset, account_id); restating a direct holding (account NULL) behaves as before. On live data today, no real org holds the same asset in two accounts under one owner (read-only check), so the bug was latent there; it hit the fixtures and would have hit Olivo. Valued but unclassified lines were already in gross and reported ("Includes $X not yet classified"); only unvalued lines are left out, and they are counted. (2, VERIFY) The 422 is the designed `no TA parameter override set — pass ?strategy_key=<…>`. A new commitment has no calibration, and verify_tamodel3 3.1 pins that 422 as the contract that drives the page's strategy picker. The commitments page already turns it into the "pick a strategy" state, not an error. The verify now accepts a 200, or that 422 naming strategy_key with the picker wired to it, and also proves the SAME commitment projects 200 with a strategy. (3, VERIFY) `edit_private_investment` already keeps the commitment id: it archives a copy, then updates the live row in place. The verify read the rows `ORDER BY system_to NULLS LAST`, which put the live row SECOND while it asserted cm[0] was live, so it compared the archive copy's fresh id. Now NULLS FIRST, plus a capital call before the edit and another after it: both resolve to the kept id (called 150,000). Proof: a one-off driving the verify's own sections (catalog, seed, snapshot, repair, rollup, household page, teardown) gave 92 PASS / 0 FAIL against the live DB (gross 1,445,000 = 45,000 + 1,400,000 = X-ray + cash). Zero fixture rows remained; counted, real and demo-olivo rows were unchanged. `--self-checks-only` 39 / 0. Full verify_bugfix4.py / verify_portentry1.py / npm build NOT re-run (WSL memory).

## 000000000000000000000000000000000000000000000000000000000000000000000000000000000000. BULKAPPROVE1 — Approvals → Bulk approve: authority & custody changes and held-away entries, many at once, the same rules; verify WRITTEN, not yet run (2026-11-05)

**Why.** Joe (2026-11-05): "account authority approval needs a 'bulk' screen — it's 5 clicks per to approve." The Olivo
onboarding (34 accounts, many non-default authorities, ~25 held-away drafts) means dozens of second-person approvals.
Task 1 findings (single paths, the clicks, later candidates): `docs/BULKAPPROVE1_DISCOVERY.md`.

**UPDATE (fix 1, 2026-11-05).** First run was 157 PASS / 3 FAIL, all in the verify scripts; every bulk-approval check passed. (A) y_catalog read 94 constraint rows but counted 0: Postgres `"char"` `contype` decodes in asyncpg as BYTES (b'c' ≠ 'c'). It now reads `contype::text`, asserts every kind is text and all 10 tables have their PK, and reads 10 PK / 39 CHECK / 4 UNIQUE / 41 FK. `lit()` used to PASS when it found no definition (`not defs or …`); an empty set now FAILS. (B) maker ≠ checker is a real CHECK on each of the 3 tables (`approved_by <> maker_id`, or `<> entered_by` on held-away), now asserted per table by its definition. (C) verify_rep7b's real-account check was frozen to live state: it assumed every real account sat on the migrated default, but 11 of the 34 Olivo accounts carry approved settings (10 advise / other custodian, 1 read-only / other custodian). The check now holds every real account to its EFFECTIVE authority, resolved in independent SQL (settings row → household → default): every path and executor against the rule table, and default accounts still against rep7's rule. It adds a [FIND] count. Proven read-only on live: 34/34 pass. The old rule reproduces exactly 11 breaks, and a mis-applied fixture account FAILS. Self-checks: bulkapprove1 42/0, rep7b 13/0. Full verify not re-run (WSL memory).

**The screen.** Portfolio → Approvals (`/portfolio/approvals`, menu gate any(view_authority | approve_authority |
view_held_away | approve_held_away), strict). Tabs Authority & custody | Held-away entries (from the envelope). A table of
PENDING items — household, account (masked), change (from → to, with the reason), maker, requested at; filters
household / maker / type / age (vocabularies built by the server from the items); select one / select all on the
FILTERED set; items the viewer cannot decide are greyed with the server's reason (you made it / you do not hold the
permission / no longer pending — its task was withdrawn); Approve selected / Reject selected (a note required); ONE
confirmation naming the count and listing the items; held-away rows expand to their lines and totals (fixed-point
money). Logic in `apps/web/lib/bulkApprove.mjs`, fails closed (a lost envelope, `can_approve` not `=== true`, no
`editable: ["decision"]` or no server cap → no control). Entry points: "N pending — review in bulk" on the household
page, Portfolio → Accounts and the dashboard's My tasks, only when more than one item waits for THIS viewer
(`GET /approvals/summary`).

**The one-path rule.** `POST /api/v1/approvals/bulk {type, item_ids, decision, note}` (`routers/approvals.py`,
`services/bulk_approvals.py`): the route gate any(approve_authority | approve_held_away), then the type's own approve
key (403 naming it — a user without it is refused the whole request); then each item through **the same service
function the single route calls** (`services.authority.decide` / `services.held_away.decide_import`), once per item,
in its own transaction(s) — so maker ≠ checker (service, human task, engine, table CHECK), the per-call
`rbac.has_permission`, the state / prechecks, the system-axis archive and the single path's own audit + events all
apply unchanged. Each item is its own decision by its own approver. Per-item results approved / rejected / refused +
reason (404 not found for another org's id, 403 maker, 409 no longer pending, 422 bad id); one failure never stops or
undoes the others; at most 200 ids (422 over the cap); duplicates collapse. Audit: one `approvals.bulk_item` row per
item (refusals included) and one `approvals.bulk` row per request (who, how many, ids, counts). Nothing new writes a
setting or a position.

**Later candidates (1c, listed only — none built).** Model approval, rebalance proposals, allocation / hypothetical
policies, integrated import, account model changes, lot method, tax profiles, substitutions, tilts, gifts, note
replicas, option packages — each would be one more `TYPES` entry pointing at its own single-approval function.
Execution approval is custody-cliff adjacent and needs its own design review before any bulk tab.

**Proof.** `apps/api/scripts/verify_bulkapprove1.py` (WRITTEN, not run — WSL memory): 6 authority drafts + 3 held-away
entries by maker M in a fixture org; checker C bulk-approves 5 in ONE request (each approved by C, its own task closed,
its own audit rows; the effective settings equal a twin approved through the single route); M's own items refused per
item, nothing changed; a user without approve_authority refused the whole request while the identical body is
admitted for C; a mixed batch (one already approved, one withdrawn) refuses those two with reasons; reject needs a
note and changes no setting; held-away bulk approval creates the same positions row-for-row as single approval; the
cap; a bad id beside good ones; org isolation both directions (rolbypassrls = false); demo-olivo and real rows
unchanged (count + checksum); own routes via _verify_self; authz1_routes --check CURRENT; node --test tests/ and npm run
build; regression verify_rep7b.py once, flat, after teardown, VERIFY_NESTED=1, 2700 s. `node --test tests/` run here:
540 tests, 0 fail (10 new in tests/bulkapprove1). `docs/AUTHZ1_ROUTES.md` regenerated (CURRENT).

---

## 00000000000000000000000000000000000000000000000000000000000000000000000000000000000. BUGFIX3 — HollisMuse's Olivo pass: the CRITICAL member leak on Positions (and every portfolio read of its shape), then the Highs and Mediums; verify WRITTEN, not yet run (2026-11-05)

**The leak (MUSE-OLIVO-CRIT) — FIXED.** A member (jpl991721, role `member`) opened /portfolio/positions and read "Every
holding across the firm": 23 live positions with owners and values. `GET /portfolio/positions` called
`list_positions(org_id=…)` after a `view_portfolio` gate — org-scoped only — and the `member` role HOLDS
`view_portfolio`. Live data: all 23 were demo-olivo (no real family exposed). The same shape was open on the position
detail, the asset picker, the Securities grid / detail / versions, the Transactions grid / detail / picker, the TA
projection and obligation ledger by commitment id, and six org-wide lists (below). **Fix:** one helper,
`services/portfolio_scope.entity_scope` → `authz.member_entity_scope` (the BUGFIX2 rule: None for Super Admin / staff /
internal reads, else the member's `visible_entity_ids` family; EMPTY = nothing). Every scoped service takes
`entity_scope` and applies it IN SQL (count, page and totals agree); a detail outside it is the SAME 404 as a missing
id. The list publishes `scope` ({kind, description}); the page no longer hard-codes "across the firm" (a member reads
"Your family's holdings"). Fail closed: if the visible set cannot be decided the read raises (refused), never unscoped.

**Task 1a — portfolio read routes** (gate · member filter before · member today · class):

| Route | Gate | Filter before | Member today | Class |
|---|---|---|---|---|
| GET /portfolio/positions | view_portfolio | none (org-wide) | own family | member-scoped — FIXED |
| GET /portfolio/positions/{id} | view_portfolio | none | own family; else 404 = missing | member-scoped — FIXED |
| GET /portfolio/assets | view_portfolio | none | assets the family holds | member-scoped — FIXED |
| GET /portfolio/securities, /{id}, /{id}/versions | view_portfolio | none | held by the family; 404 | member-scoped — FIXED |
| GET /portfolio/transactions, /{id}, /transaction-positions | view_portfolio | none | own family; 404 | member-scoped — FIXED |
| GET /modeling/ta/projection/{id}, /obligations/{id} | view_portfolio | none | own family; 404 | member-scoped — FIXED |
| GET /portfolio/commitments | view_portfolio | visible_entity_ids (BUGFIX1) | own family | already-correct |
| GET /portfolio/tax-chase | view_portfolio | none (org list) | 403 | staff-only — FIXED (internal) |
| GET /portfolio/position-account-exceptions | view_portfolio | none | 403 | staff-only — FIXED |
| GET /portfolio/precedence/households/{id} | view_portfolio | none | 403 | staff-only — FIXED |
| GET /udf/records/{t}, /export, /udf/values/{t}/{id} | view_portfolio | none (every org row) | 403 | staff-only — FIXED (no member UI reads them) |
| GET /portfolio/global-securities, /{id} | view_portfolio | n/a (platform master) | allowed | public-to-org reference |
| GET /entities/{id}/direct-assets, /private-investments | member scope | own family | own family; 404 | already-correct |
| GET /entities/{id}/net-worth, /liabilities | entity access | own family | own family; 404 | already-correct |
| GET /portfolio/targets, /allocations | entity access | own family | own family | already-correct |
| GET /portfolio/my-investments, /summary | account | own rows | own rows | already-correct |
| GET /households/{id}/assets, /admin/households* | internal (+ manage_members) | n/a | 403 | staff-only |
| GET /held-away/*, /portfolio/manual-assets, /portfolio/option-contracts | view_held_away | n/a | 403 | staff-only |
| GET /xray/*, /authority/*, /lot-method/*, /replication/* | view_replication / view_authority / view_proposals | n/a | 403 | staff-only |
| GET /portfolio/precedence, /securities/search, profitability, fee screens | internal | n/a | 403 | already-correct |

**1b.** BUGFIX2 classified the 73 reads with NO gate; these already had `view_portfolio`, so none was in its list, and
verify_bugfix2 seeded no position for a second family. **1c.** The "approved" import was never approved: one
`provisional_imports` row, status `draft`; `provisional_positions` is EMPTY. On approval the rows are current positions
(authority `stated`); the grid listed them unlabelled and the household listed only direct / commitment kinds.
**1d.** `str(Decimal)` at the JSON boundary (positions / securities / transactions / authority / lot_method / xray
`_s`, direct_holdings, households `_money` = float). asyncpg decodes trailing-zero numerics as exponents, so 900000
became "9.0E+5" and `money()` split it into "$9.0E". **1e.** Lists rendered their empty sentence while still null;
AddressesTab kept its first-render copy.

**Muse findings.**
- MUSE-OLIVO-CRIT (Positions leak): FIXED as above.
- MUSE-OLIVO-H1 (Add private investment requires a commitment): FIXED — `direct_holdings.COMMITMENT_RULES`: fund
  required, SPV optional, hedge fund / direct deal / SAFE / convertible note none (amount invested + latest value, no
  commitment row, unfunded None → "—"; a commitment for those kinds is refused). New kind `hedge_fund`; "note" labelled
  Convertible note; a note field (assets.notes). The form shows commitment fields from the envelope's rules.
- MUSE-OLIVO-H2 (no household Recalculate): FIXED — `POST /admin/households/{id}/recalculate` (internal +
  manage_members | manage_portfolio), the members' family-scoped rollup now, net worth returned; audited; the card
  renders it only for `permissions.can.recalculate`.
- MUSE-OLIVO-H3 (addresses not editable): FIXED — Edit (PUT, system-axis archive, id kept: every field incl. seasonal
  months and note) and End (`POST …/addresses/{id}/end`: archive + valid_to; nothing deleted) for manage_members |
  edit_crm_contact; controls from the record envelope's `addresses` flag.
- MUSE-OLIVO-M1 (import rows not live): FIXED in code — `is_provisional` on every position row + the API's label
  "Provisional — until the custodian feed"; the household lists `provisional_holdings`. DATA: the Olivo import is
  still a DRAFT awaiting a second Super Admin.
- MUSE-OLIVO-M2 (support staff offered Add Relationship / New Entity): FIXED — envelope keys `new_entity` (POST
  /entities, exact manage_members) and `graph` (entity_graph writes, require_staff); the CRM list, /crm/new and the
  Ownership Hierarchy page render from them (the hierarchy page read the token's role claim via isStaff).
- MUSE-OLIVO-M3 (Northline: one asset or three?): ANSWERED — the NLF lines are in DRAFT held-away imports (6 of 58
  drafts); no asset exists yet. `manual_asset_for` reuses one asset for identical lines (identifier, else kind + name +
  terms). `GET /portfolio/manual-assets` now carries position / owner counts and a same-name flag, shown on
  Households. GAP (not built): a maker-checker MERGE of two manual assets — it must re-point positions, valuations,
  identifiers, transactions, held-away records and relinks; not small.
- MUSE-OLIVO-M4 (no option contract picker): FIXED — `GET /portfolio/option-contracts?underlying_ticker=`
  (view_held_away) lists existing rep8 contracts or says why none exist; the held-away listed_option entry fills the
  terms from a pick.
- MUSE-OLIVO-L1 ("$9.0E…" costs): FIXED — `services/money_text.fixed` (format "f") in every serializer above;
  households' float → fixed string; front end `lib/fixedPoint.toFixedPoint` inside every money formatter.
- MUSE-OLIVO-L2 (private investment has no note): FIXED (H1).
- MUSE-OLIVO-L3 (display lag): FIXED — `lib/listState.listView` (null = Loading…, never the empty sentence) in the
  Holdings tab; AddressesTab follows its parent's re-read.

**Proof.** `apps/api/scripts/verify_bugfix3.py` (WRITTEN, not run — WSL memory): reproduces the leak on the pre-fix call
path (`list_positions(org_id)` returns F2's distinctive asset), then proves every member-reachable route returns F1
only (independent SQL family; strict subset; F2's detail = the missing-id 404, byte for byte), the staff-only reads
refuse the member while the identical request is admitted for the org admin, Tasks 3–6 end to end, demo + real rows
unchanged (count + checksum), RLS isolation both ways (rolbypassrls = false), own routes, `authz1_routes.py --check`
CURRENT, node + build, and verify_bugfix2 once (flat, VERIFY_NESTED=1, 2700 s). node --test tests/ 529 / 0 fail
(9 new in tests/bugfix3). `docs/AUTHZ1_ROUTES.md` regenerated (CURRENT).

UPDATE (BUGFIX3 FIX 1, 2026-11-05): the first full run was 136 PASS / 4 FAIL. Three were VERIFY defects that together meant the CRITICAL leak had never actually been tested: (1) the BEFORE check required the word "scope" to be absent from the pre-fix route and failed on its docstring ("Org-scoped from JWT claims") — it now matches CALLS in the AST (list_positions called, no member-filter call, no entity_scope kwarg), with a self-check that a docstring cannot match; (2) the reproduction ran list_positions on a bare RLS-pool connection with no org context, so RLS returned [] — it now runs under the member request's org context (set_rls_context, asserted via app.current_org_id) and returns F1 AND F2, the distinctive F2 asset among them; (3) visible_expected's WITH RECURSIVE had two UNIONed recursive terms (InvalidRecursionError), so the whole leak section never ran — now one anchor and ONE recursive term over a sub-select UNIONing entity_relationships (ownership) and entity_ownership. (4) was a REAL CODE defect: no manual creation path (direct asset, private investment, manual asset, integrated import, cash, held-away global asset, option fill) set a taxonomy key, and the rollup skips an unkeyed position — the Olivo family's $0 (23 of 23 positions unkeyed; kinds real_estate 9, collectible 8, private_fund 4, operating_business 1, other 1; all in the default org). Fixed: (a) services/portfolio_kind_taxonomy.key_for_kind — kind → key from the org setting `portfolio.kind_taxonomy` (DEFAULT_SETTINGS holds keys only; an org overrides by merge) accepted only if the key is an active asset_taxonomy row in the org's own config; called by create_asset, manual_asset_for, held_away._asset_for and the option-fill asset; 'other' / 'unclassified' deliberately unmapped; (b) the rollup's skip reason is one constant and portfolio_refresh.skipped_of reports count / unclassified / unvalued / position ids on Recalculate, on the household detail (`skipped`, `last_rollup`) and on the entity net worth's last_rollup; the household card says how many holdings are not counted (lib/bugfix3 skippedSentence); (c) scripts/backfill_taxonomy_keys.py — sets ONLY assets.default_taxonomy_key through portfolio_securities.update_asset (system axis, id kept; positions untouched, the rollup's COALESCE reads the asset's key), --dry-run prints counts per kind, idempotent. The verify seeds each fixture org with a copy of the default org's asset_taxonomy rows and gained a FIX 1 section (17 creation kinds keyed; gross = sum of keyed values; an unkeyed position REPORTED; backfill dry-run / real / second run). Proof: a one-off driving the verify's own fixture sections (seed, holdings, leak, household, FIX 1, teardown) 99 PASS / 0 FAIL against the live DB — leak reproduced (F1+F2 under org context) then closed on every route, Recalculate +1,000,000 exactly, zero fixture rows after teardown, counted tables at pre-test counts, demo-olivo + real rows unchanged; --self-checks-only 33 / 0; node tests/bugfix3 10 / 0. The full verify_bugfix3.py (node suite, npm build, verify_bugfix2 regression) has NOT been re-run here (WSL memory). OPERATOR STEP (not run by the sprint): `doppler run -- python3 apps/api/scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --dry-run` (expect real_estate 9, collectible 8, private_fund 4, operating_business 1 keyed; other 1 SKIP), then the same without --dry-run, then Recalculate on the Olivo household. The one 'other' holding stays unkeyed until someone gives it a taxonomy key; the household now says so instead of reading $0.

## 0000000000000000000000000000000000000000000000000000000000000000000000000000000000. PORTENTRY2 — fix what HollisMuse hit while onboarding the Olivo family (interim report 2026-11-05, stopped at I.0); schema APPLIED; verify WRITTEN, not yet run (2026-11-05)

**The blocker.** 25 of 34 Olivo accounts (every trust / LLC / LP / foundation account, all eight integrated ones)
could not be created: an existing household had no member editor, and the Add account Owner list is the household's
members — all 14 of them people.

**Discovery (Task 1).** 1a — household_memberships is PK (household_id, entity_id), FK entities, NO type CHECK: a
legal entity could always be a member in the DB, and membership is many-to-many by design (an entity may sit in
several households; billing / net-worth partitions use entities.primary_household_id, untouched). The add / remove
routes existed but no UI called them, they were rbac-gated (not internal) and unaudited. Every reader keys on entity
ids, never on type (net worth / liabilities, household assets, portfolio_refresh, X-ray visibility, the member
closure). 1b — add_account required membership; Olivo's 8 accounts are UNKNOWN / UNKNOWN; service_model is free text
from the custody import. 1c — all three ownership views read services.ownership_read (both tables); the tab's tree
was the server-rendered prop, never refreshed after the tab's own writes; its "Owns" tree recursed; /lookthrough
returns 0–1 fractions while owners' effective_pct is 0–100. 1d — the picker matched one contiguous substring with
is_active = true; its value-sync effect could reset the box; a picked detail record displayed its id. 1e — native
date inputs; ReferenceSelect was uncontrolled (defaultValue before options loaded). 1f — the Ownership tab's Add /
Edit / Remove had NO gate (API: internal + manage_members; support staff lacks manage_members). 1g —
/portfolio/global-securities is view_portfolio, a member grant too; held-away entry was a free ticker box.

**Part 1 (APPLIED via MCP, docs/portentry2_part1.sql, additive).** public.config categories
account_registration_types (14) and account_tax_statuses (3) for the default org and every org with CRM config rows;
crm_seed_org_config copies them to any other org; entity_addresses.note.

**Each Muse finding → status.**
- MUSE-OLIVO-H-02 / G-02 (High, the blocker) — FIXED. Members panel on an existing household (people AND legal
  entities; add / remove audited household.member_added / member_removed; internal + manage_members; removal refused
  while the member owns an account of the household; a demo member is removed through crmdetail1's app path). Owner
  = any member, plus "legal entities owned by a member" which links the entity in the same transaction.
- MUSE-OLIVO-H-01 (High) — FIXED. Registration type and tax status (from config), billable (default OFF for a demo
  household), discretionary, optional service model on Add account and on each account card (edit: PATCH
  /admin/households/{id}/accounts/{id}, system-axis archive, id kept, audited).
- MUSE-OLIVO-H-03 / H-04 — FIXED. DVP receiving custodian + DTC / participant number visibly required; a missing one
  is refused naming it, with the reason (client and server).
- MUSE-OLIVO-PERS-01 (High) — FIXED. GET /entities/{id}/ownership carries a permissions envelope; GET
  /entities/{id}/permissions (services/record_permissions — each flag the exact rule of its route) drives Edit
  details, tax ids, addresses, notes, employment, social; the server refusal of support staff's ownership-% save was
  already in place (portentry1) and is re-proven on the identical request.
- MUSE-OLIVO-PERS-03 — NO CHANGE NEEDED. Support staff's Workflows → Runs / Triggers (view_workflow_runs) and Billing &
  Custody (internal + view_portfolio) items match grants it holds; the new Households row is hidden from it.
- MUSE-OLIVO-E4-01 (High) — FIXED. Picker option "Include inactive / deceased", ON by default for the family group
  and grantor.
- MUSE-OLIVO-B11-03 (High) / B11-01 — FIXED. One DateInput (text; ISO / US typed or pasted; years outside 1800–2200,
  two-digit years and impossible days refused with a message) on the person / entity / relationship / ownership /
  notes / employment / detail-field forms. NOT changed: the Liabilities and Assets & Investments tabs' date fields.
- MUSE-OLIVO-F-02 — FIXED. The tab's tree re-reads GET /entities/{id}/ownership-graph after every write; "Owns" shows
  direct holdings with a labelled look-through toggle, no duplicates.
- MUSE-OLIVO-F-03 — FIXED. Holdings' look-through fractions converted to percent (60.0% eff.).
- MUSE-OLIVO-F-01 — FIXED. OWNED BY / OWNS rows show the interest note.
- MUSE-OLIVO-P.1 — FIXED. GET /securities/search (ticker, name, CUSIP, ISIN, FIGI; read-only; require_internal; a
  member refused) and a "Find a security" box on held-away entry; the master admin screen stays Super Admin.
- MUSE-OLIVO-D19-01 — FIXED (word-token search over display / legal / first / middle / surname / maiden).
- MUSE-OLIVO-D17-01 — FIXED (the picked record's name, never its id). MUSE-OLIVO-E-01 — FIXED (search on any change of
  the box's text: keystrokes, paste, programmatic fill). MUSE-OLIVO-E-02 / D-02 / D19-02 — FIXED (the saved row shown at
  once; the routes are force-dynamic; re-read without cache).
- MUSE-OLIVO-D-01 / B114-01 — FIXED (ReferenceSelect controlled; the stored value kept as an option).
- MUSE-OLIVO-D6 / D7 / D8 / D18-01 — FIXED (trust detail keys remainder_to (CRM record), shares, beneficiaries_note —
  shown for every trust, labelled for GRAT / CRUT and bypass trusts).
- MUSE-OLIVO-B112-01 — FIXED (maiden name + date of death on New person). MUSE-OLIVO-C-01 — FIXED (address note).
  MUSE-OLIVO-C.check — FIXED ("Current · Nov–Apr" vs "Seasonal, May–Oct" by today's month).
- MUSE-OLIVO-G-01 — FIXED (Portfolio → Households). MUSE-OLIVO-H-05 — FIXED (counts follow every detail load).

**Gate change (deliberate).** POST / DELETE /admin/households/{id}/members: rbac manage_members → internal + manage_members
(PERMS1's household admin gate); docs/AUTHZ1_ROUTES regenerated, --check CURRENT.

**Data.** No demo-olivo or real row was written. The Olivo household still has its 14 people: adding the 26 legal
entities and the remaining 26 accounts is the Muse re-run's job, now unblocked.

**Regression fix (found, not caused, here).** ad97ea3 (Olivo README → the v3 entry plan) broke verify_crmdetail1's Task 6 self-check (it accepted v1 / v2 only), which verify_portentry1's chain runs — now any entry-plan version passes; crmdetail1 / portentry1 / portentry2 --self-checks-only: 23 / 37 / 21 PASS, 0 FAIL.

**Verify.** apps/api/scripts/verify_portentry2.py (written, NOT run by the sprint); node tests
apps/web/tests/portentry2 (13/13 pass; full suite 519 pass / 0 fail). Regression: verify_portentry1.py once, flat,
VERIFY_NESTED=1, 2700 s.

## 000000000000000000000000000000000000000000000000000000000000000000000000000000000. PORTENTRY1 — close the gaps between the Olivo entry plan and the front end (GAP-CRM-01…06, GAP-PORT-01…05) + verify tooling; schema APPLIED; verify WRITTEN, not yet run (2026-11-04)

**Discovery (Task 1).** 1a — entities.status is free text; its readers are the CRM list's exact-match filter, the
create default and note-apply (MEMBER_VISIBLE_STATUSES is deals only) — adding client / contact breaks none; live 33
prospect / 3 active; no safe mapping of 'active' to client or contact exists, so existing rows are UNCHANGED. Status,
marital and sub-type lists were constants in apps/web/lib/entityTypes.js. 1b — ownership writes: POST
/entities/{id}/ownership, PATCH + DELETE /entity-relationships/{id}/ownership (require_staff), POST /entity-ownership
(manage_members); verify_staffroles1 y_bypass asserted the DELETE gate. 1c — held-away rows reject at
resolve_identifiers (no_identifier / unresolved); an org manual asset = portfolio.assets with global_security_id NULL
(zero existed); held-away assets carried no taxonomy key, so the rollup skipped them. 1d — POST
/portfolio/import/positions (manage_portfolio) writes at once, no preview / no second person, authority aggregated,
source reporting_tool_import, which ranks ABOVE altruist in the default precedence order (a later feed would LOSE).
1e — called / distributed / unfunded are derived from transactions (recompute_commitment); NAV = valuations; TA read
the position's market_value else called − distributed. 1f — the rollup was org-wide per date, only POST
/portfolio/rollup; net worth summed the latest bucket per taxonomy key (a dropped key lingered). 1g — perms1 reached
main by fast-forward; secmaster1's merge subject was git's "Merge branch 'secmaster1-work'" — both fell to HEAD.

**Gaps.**
- GAP-CRM-01 — CLOSED. Statuses in public.config (crm_entity_statuses: prospect, client, contact, active, inactive,
  archived + deceased as a system value), served in GET /crm/vocabularies; Details / New entity forms and the CRM list
  pills read the envelope; writes refused outside the vocabulary (an unchanged legacy value passes).
- GAP-CRM-02 — CLOSED. crm_marital_statuses (single, married, divorced, widowed, separated, domestic_partnership); the
  person form field; POST / PUT /entities carry marital_status (key stored).
- GAP-CRM-03 — CLOSED. crm_sub_types per entity type (LLC: holding, real_estate, operating, collectibles, investment,
  other; trust = crmdetail1's trust_type labels + the old list; foundation = charity_type labels; corporation). The
  stored sub_type stays the LABEL (crmdetail1's trust_type writes labels; every row holds one).
- GAP-CRM-04 — CLOSED. Registry keys fund_role (LP), ein_status (foundation), the ILIT insurance policy group (insurer
  = CRM record, policy_type, face_amount, insureds = CRM records, policy_number_masked — a full number refused).
- GAP-CRM-05 — CLOSED (Joe). Every ownership route = internal + manage_members (org admin admitted, support staff
  without the key refused, member refused); verify_staffroles1's assertion updated to the decided rule. The rail's
  generic /entity-relationships writes keep require_staff (stricter).
- GAP-CRM-06 — CLOSED (Joe). New permission **edit_crm_contact** — notes, addresses, phones, emails (new PUT
  /entities/{id}/contact) and nothing else; granted to every support_staff role (migration +
  rbac.ensure_support_staff_grants on role assignment) and the 'CSA / Ops' profile; no client role (the BUGFIX2 client
  rule strips it from a member's profile too). STAFF_ROLE_NAMES / is_staff unchanged.
- GAP-PORT-01 — CLOSED. Held-away manual entry takes an asset_kind (mutual_fund, money_market, municipal_bond, cash,
  private, listed_option, digital_asset) with its terms; resolution master-first (identifier; an option only through
  an EXISTING rep8 contract), cash → the org cash asset, else an ORG-scoped manual asset created at approval; nothing
  in the platform master. Relink when an identifier later resolves: POST /portfolio/manual-assets/relink + the nightly,
  system-axis, REPORTED in portfolio.asset_relinks + audit_log (GET /portfolio/manual-assets). Maker-checker unchanged.
- GAP-PORT-02 — CLOSED (Joe: one-time import). Super Admin screen **/admin/integrated-import**: upload CSV → DRAFT
  preview (accounts by MASK, per-line resolution, totals) → a SECOND Super Admin approves (human task, DB CHECK) →
  positions authority 'stated', source 'manual' (ranked below altruist), listed in portfolio.provisional_positions; the
  Altruist sync supersedes them (precedence on shared keys, then every provisional row of the account is closed).
  The existing POST /portfolio/import/positions is unchanged (its source ranks above the feed — not used for this).
- GAP-PORT-03 — CLOSED. "Assets & Investments" tab: Add asset (real estate, collectible, operating business, insurance
  cash value, other; owners + %, cost basis, valuation value / date / method appraisal | estimate | statement /
  source, notes); new valuation = new row, history kept. Writes internal + manage_members; members read their own.
- GAP-PORT-04 — CLOSED. Add private investment (fund / direct deal / SPV / SAFE / note): commitment amount / date /
  vintage, called + distributed as opening ledger rows, unfunded computed by recompute_commitment, NAV as a
  valuation; later calls / distributions / NAVs; the TA projection reads the NAV through position_current_value.
- GAP-PORT-05 — CLOSED. After every position / valuation / commitment / liability write a FAMILY-scoped rollup
  (household members closed under ownership, never org-wide) is queued in portfolio.rollup_requests (debounced: one
  pending per family, PORTFOLIO_ROLLUP_DEBOUNCE_S, default 2 s) and run; Recalculate on the record (internal + manage_members |
  manage_portfolio); the scheduler tick drains leftovers and runs the org-wide nightly at 07:00 UTC; net worth shows
  holdings_as_of. Net worth now reads each entity's latest snapshot (a zero snapshot is written for an entity that no
  longer holds anything valued).

**Verify tooling (Task 8).** _verify_self: `<key>-work` merge subjects; a fast-forwarded sprint ends at the newest
first-parent commit naming it, base = parent of the oldest (SPRINT_FASTFORWARD perms1 3fe2cef / 01c0f57).
--self-checks-only: perms1 3→23 PASS 0 FAIL, secmaster1 0 FAIL, crmdetail1 0 FAIL; the other 34 scripts unchanged
(previewperf, previewperffix1, vhk2 keep their own pre-existing FAILs — not this sprint's). docs/VERIFY_RULES.md:
merges use --no-ff. [FIND] a0755ee (the v2 entry plan) had dropped crmdetail1's "Protection rule" section from the
Olivo README — restored (the copy-over-a-newer-doc pattern); crmdetail1's self-check accepts the v2 plan name.

**Operator steps.** (1) Super Admin → Integrated import → upload `docs/demo/olivo/data/import/integrated_positions.csv`
(27 lines, the eight integrated accounts A7 A9 A12 A14 A15 A20 A24 A29, masks only, totals = holdings.csv) → check the
preview → (2) a SECOND Super Admin approves. Format: docs/demo/olivo/README.md. Not loaded by this sprint.

**Verification.** `apps/api/scripts/verify_portentry1.py` WRITTEN, not run (self-checks only were run). Regression
inside it: verify_crmdetail1.py once, flat, after teardown, VERIFY_NESTED=1, 2700 s. `node --test tests/` passes
(506 pass, 0 fail). `npm run build` not run here (WSL memory) — the verify runs it.

UPDATE (PORTENTRY1 FIX 1, 2026-11-04): the first full run was 106 PASS / 18 FAIL from 5 roots, now fixed. (A, CODE) the held-away approve 500 was `asyncpg DataError: invalid input for query argument $9: '2027-01-15' ('str' object has no attribute 'toordinal')` in `portfolio_manual.manual_asset_for`: a municipal_bond's `maturity` / a listed_option's `expiry` (ISO strings in the terms JSON) were bound to `assets.maturity_date`; the other five types approved. Now parsed to a date. (B, CODE) the direct-asset / private-investment methods were a parallel vocabulary (`estimate` / `statement`) that `record_valuation` refuses; the keys are now the DB's five (appraisal / mark_to_model "Estimate (mark to model)" / nav "Statement / NAV" / market_price / amortized_cost), asserted equal to `portfolio_assets.VALUATION_METHODS` at import, labels served in the envelope; the CHECK is unchanged. (C, CODE) `require_entity_access` passes staff / internal callers unscoped, so the GET lists returned 200 + [] for another org's record; every portentry entity route (direct assets, private investments GET + POST, Recalculate) now resolves the record in the caller's org first (`_record_in_org`) → the same 404 "Entity not found" as a missing id. (D, VERIFY) the tax-id refusal sent `tax_id_value` (the schema field is `value`), so the 422 came from validation and not the gate; the verify now sends a valid `TaxIdCreate`, expects 403 naming manage_members with no row written, and proves the org admin is admitted (201, re-read) on the identical body. (E, VERIFY) the teardown now sets `execution.teardown = 'fixture'` on the operator connection (the verify_rep7 path, `execution_teardown_allowed()`); the trigger is unchanged. (F) was a cascade of A. The verify also gained a same-404-as-missing-id check on all six entity routes. Proof: a one-off driving the verify's own sections (gates, manual positions, direct + private, net worth, import, isolation, teardown) gave 114 PASS / 0 FAIL against the live DB, zero fixture rows after teardown (held_away_imports 0), every counted table at its pre-test count; `--self-checks-only` 37 / 0; node tests/portentry1 9 / 0; authz1_routes --check CURRENT. The full verify_portentry1.py (with the crmdetail1 regression and npm build) has NOT been re-run here (WSL memory).

## 00000000000000000000000000000000000000000000000000000000000000000000000000000000. CRMDETAIL1 — a CRM that can hold a real multigenerational family: governed relationships, typed detail fields, people fields, liabilities into net worth, permanent demo data; schema APPLIED; verify WRITTEN, not yet run (2026-11-04)

**Discovery (Task 1).**
- **1a — the CRM record.** Tabs: Overview (Details form + ownership trees + generic Attributes), Addresses, Employment
  (people), Tax IDs, Social Profiles, Notes, Documents, Linked Documents, Ownership, Compliance. Relationships were
  added ONLY through the hierarchy page's side rail (`POST /entity-relationships`, types offered: ownership /
  beneficiary, free text accepted, `require_staff` — an org admin was refused) and shown only as ownership trees; no
  reverse labels. Tags: comma-separated on the Details form (create and edit). `sub_type`: a hard-coded per-type list
  in `lib/entityTypes.js` (trust: Revocable / Irrevocable / Charitable / GRAT / SLAT / QTIP / Land Trust / Other).
  Notes: the Notes tab (`entity_notes`) and the free `entities.notes`. Addresses: the add form ALREADY offers
  seasonal months (is_seasonal + from / to month) and primary — no edit form. Custom fields: the generic Attributes
  list (key / value text). For a person the form's "Date of Birth" writes `inception_date` and "Date of Death" wrote
  `end_date` (there is a separate, almost unused `date_of_birth` column); "Country / Region" writes
  `country_code` / `region_code` — for a legal entity that IS its state of formation.
- **1b — readers of relationship_type.** Every ownership reader already filters `'ownership'` explicitly:
  services/ownership_read, entity_graph (children / parents / lookthrough / detect_cycle), portfolio_rollup
  (`_ownership_weights`), ownership_tree (edge types ownership + beneficiary), routers/entity_graph ownership
  endpoints, assistant_actions/entity_graph. `beneficiary` is ALREADY read: `entity_graph.get_beneficiary_visible`
  (member visibility flows down a beneficiary edge like ownership) and the ownership tree's default edge types — the
  governed `beneficiary` keeps that meaning; income / remainder beneficiary do not confer visibility. Two non-filtering
  spots in `POST /entity-relationships` were fixed: the ownership-sum warning now sums ownership rows only, and the
  ownership cycle check runs for ownership only.
- **1c — net worth.** `public.entity_holdings` (look-through buckets written by portfolio_rollup) summed by
  `services.households._sum_holdings` — the household flexible rollup, the strict primary-household net worth, and now
  the entity figure. Liabilities subtract there, on the same look-through basis (`services.liabilities`). The
  hierarchy page's total and the X-ray are allocation views, not net worth.
- **1d — entry paths.** Real asset / collectible with a valuation: `POST /portfolio/securities` (tenant asset) +
  `POST /portfolio/positions` exist (manage_portfolio — NOT granted to org_admin), `record_valuation` has no route
  (documents only), no entry UI. Private fund + commitment: `create_commitment` is service-only (the import path).
  Held-away manual: the account page's held-away panel — rows must carry an identifier that resolves in the security
  master (unresolved / no identifier rejected). Integrated custodian account with no feed: no manual path (the
  held-away panel is for held-away accounts). Security not in the master: Super Admin `POST /portfolio/global-securities`.
- **1f — broad-predicate deletes.** By name pattern: verify_chancery3b/5/6/9/11a, verify_ownershiptreea/b,
  verify_navregroup (plan), verify_soc1–6, verify_portfolioux1–4, verify_udf01a–02b, verify_tamodel1–4,
  verify_altruist_sprint5/6 (assets), verify_litellm* / verify_modelresearch / verify_ensemblesystemone (documents,
  models), verify_fee36/41/42, verify_rlsC/EF, verify_s27, verify_sprint12, verify_smtpservice, verify_multitenant2,
  verify_staffvisbypass, verify_edgarcohorts. By fixture org / plan predicate: `_verify_cleanup.cleanup` (every
  regression-set verify). By id: perms1_remove_muse_rows.py, bugfix2_remove_muse_rows.py.

**Relationship vocabulary (Task 2) — a code constant, `services/crm_vocab.py`, served in every envelope.** 29 types
(key, label, reverse label, allowed from / to entity types, symmetric): spouse, former_spouse, partner, parent_of ↔
child of, sibling, grantor, trustee, successor_trustee, co_trustee, trust_protector, distribution_committee,
beneficiary, income_beneficiary, remainder_beneficiary, annuitant, managing_member, sole_member, manager,
general_partner, limited_partner, investment_committee, board_chair, board_member, officer, owner, donor_advisor,
sponsor, advisor, ownership (unchanged, any pair, entered on the Ownership tab). Why not a table: the allowed pairs
are API rules; the schema stays free text and the API is the one gate. Both `POST /entity-relationships` (the
hierarchy rail, gate unchanged) and the new panel refuse an unknown type or a disallowed pair with the reason (422).
All 117 relationships of the Olivo plan pass. **Relationships panel** on every record: grouped, both directions with
the reverse label, add (type + direction, other record, note, effective date), end (`valid_to`, kept as history,
never deleted). Routes `GET/POST /entities/{id}/crm-relationships`, `POST /crm-relationships/{id}/end` — internal
read, internal + manage_members write (the org admin is admitted; support staff without manage_members refused).

**Detail fields (Task 3) — `entity_attributes` rows under the reserved prefix `detail.` with a typed key registry.**
Why: already bi-temporal / org-scoped / RLS'd, sparse per type, labels and types from the registry without a
migration per field. Trust (trust_type → also sets sub_type, revocable, situs_state, gst_exempt, vesting_age,
termination, annuity_term_years, annuity_end, unitrust_rate_pct, crummey_notices, funded_with), foundation
(charity_type → sub_type, sponsor = a CRM record, min_distribution_pct), llc / lp / gp / family_office (management,
purpose, valuation_policy); every legal entity shows its state of formation = `region_code` (edited on the Details
form, relabelled "Country / State of formation"). Valid-time restatement per key; wrong types refused (422, every
error at once, nothing written). The generic Attributes list no longer shows `detail.` keys and refuses to write them.

**People (Task 4).** `entities.maiden_name`, `entities.date_of_death` (migration). date_of_death → status `deceased`,
is_active false, in the one write path (`services/entity_writes.py`, now shared by the Details form and the detail
fields); the record and its relationships stay. `age` / `is_minor` (< 18 today) / `is_deceased` computed in
`EntityOut` at today's date from date_of_birth, else inception_date; shown on the record and as a "Person" column in
the CRM list. The archive copy now also carries date_of_birth, marital_status, maiden_name, date_of_death.

**Liabilities (Task 5) — `portfolio.liabilities` (docs/crmdetail1_part1.sql, APPLIED + verified by
`scripts/_apply_crmdetail1.py --all`: SCHEMA OK).** Valid axis with `lineage_id` (one live version per lineage),
owner, lender (CRM record or name), kind (mortgage, securities_backed_line, margin, term_loan, asset_secured_line,
personal_loan, other), original / current balance numeric(20,2), as_of, rate_type fixed|floating, rate text,
maturity, collateral (account / asset / text), notes; 8 CHECKs; RLS a policy per operation (NULLIF). Write =
internal + manage_members (the CRM's key — org_admin holds it, manage_portfolio it does not); read = staff any
record, a member only their own family's (404 otherwise) with view_portfolio. Liabilities tab on the record (add /
edit = new version / end, history) and a Liabilities section + net worth on the household card. **Net worth = gross −
liabilities**, three numbers (strings, exact Decimal) on the record (`GET /entities/{id}/net-worth`, and in the
liabilities envelope), on the household detail and in both household rollups (`net_worth`); liabilities attributed
look-through with the asset rollup's own weights. Unfunded commitments are not liabilities.

**Permanent demo data (Task 6).** A record tagged `demo-*` (entities.tags), or a household whose members are all
demo-tagged, is protected two ways: (1) a BEFORE DELETE guard on entities, households, household_memberships,
entity_relationships / attributes / addresses / notes, entity_ownership and portfolio.liabilities refuses any delete
of a demo row — the only path past it is the LOCAL GUC `crm.demo_teardown = 'fixture'` for a row of an `is_test` org
(a real org's demo rows can never be deleted; the app's own "remove from household" sets `crm.demo_edit = 'staff'`
for membership edits; deleting a demo household in the app is a plain 409); (2) `_verify_cleanup.cleanup` refuses a
delete set touching demo rows (rolled back, nothing deleted) unless they are the caller's own `demo_fixture_orgs`
(is_test), and both MUSE removal scripts refuse a plan holding demo rows (`demo_refusals`). Index: docs/demo/README.md.

**The Olivo entry plan, step by step (1e)** — today = before this sprint; after = after it; GAP = still missing.

| Steps | Today | After CRMDETAIL1 | Note |
|---|---|---|---|
| P.1 | supported (secmaster1) | supported | |
| P.2 | missing | supported | relationship types + Liabilities tab |
| A.1–A.8 organisations | supported | supported | types, tags, notes on the record |
| B.1 name parts, DOB, email, phone | supported | supported | DOB = "Date of Birth" (inception_date) |
| B.1 maiden name | missing | supported | |
| B.1 date of death (B.1.15) | end_date only, no status | supported | deceased + inactive |
| B.1 marital status | GAP-CRM-02 | GAP-CRM-02 | column exists, no form field / vocabulary |
| B.1 status client / contact | GAP-CRM-01 | GAP-CRM-01 | vocabulary is prospect / active / inactive / archived (+ deceased) |
| B.2 minor shown | missing | supported | record + CRM list |
| B.3 notes as support staff | refused | refused — GAP-CRM-06 | support_staff lacks manage_members (by its grants) |
| C.1–C.62 addresses incl. seasonal | supported | supported | seasonal months already on the form |
| C (Cipolla branch) as support staff | refused | refused — GAP-CRM-06 | same grant |
| D.1–D.26 type, inception, state | supported | supported | state = Country / State of formation |
| D trust / foundation sub-type | partial (fixed list) | supported | trust_type / charity_type set sub_type |
| D.20–D.26 LLC sub-type (real estate …) | GAP-CRM-03 | GAP-CRM-03 | hard-coded sub-type list |
| D detail fields | missing | supported | except fund_role, ein_status, policy — GAP-CRM-04 |
| D.n purpose / control / notes | supported | supported | Notes tab; purpose also a detail field |
| E.1–E.117 relationships | missing | supported | reverse labels on the other record |
| F.1–F.29 ownership % | org admin refused | org admin refused — GAP-CRM-05 | Ownership tab writes keep require_staff (verify_staffroles1 asserts it); super admin can |
| F.check graph / look-through | supported | supported | |
| G.1 household, G.2 entities as members | supported | supported | |
| H.1–H.34, H.check accounts, masks | supported | supported | |
| I listed tickers on other_custodian / outside | supported | supported | held-away manual entry; resolves in the master |
| I mutual funds, munis, cash, private, options, crypto | GAP-PORT-01 | GAP-PORT-01 | manual entry rejects unresolved / identifier-less rows |
| I integrated (Altruist) accounts | GAP-PORT-02 | GAP-PORT-02 | no feed, no manual path |
| J private investments + commitments, J.SPV | GAP-PORT-04 | GAP-PORT-04 | |
| K.1–K.19 direct assets with valuations | GAP-PORT-03 | GAP-PORT-03 | |
| L.1–L.7 liabilities | missing | supported | lender as a CRM record or name |
| M.1–M.2 security-master check | supported | supported | |
| N reconciliation | no net figure | supported (three numbers) | needs a rollup run — GAP-PORT-05; I/J/K gaps explain differences |
| O.1–O.4 persona views | supported | supported | liabilities member-scoped too |

**Named gaps (new backend work beyond this sprint; nothing built).**
- GAP-CRM-01 — entity status vocabulary (client / contact) to the config table, with the CRM list filters.
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-CRM-02 — marital status field + vocabulary on the person form.
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-CRM-03 — sub-type vocabularies from the config table (LLC: real estate / operating / collectibles).
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-CRM-04 — registry keys fund_role (LP), ein_status (foundation), insurance policy (ILIT).
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-CRM-05 — ownership writes on the Ownership tab: decide whether internal + manage_members (as the panel) may
  write ownership; today require_staff (org admin refused, Muse's maker).
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-CRM-06 — support staff notes / addresses: grant manage_members (or a narrower key) to support_staff, or not.
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-PORT-01 — manual positions for securities NOT in the master (mutual funds need SEC mutual-fund tickers;
  munis need a CUSIP source; cash / private / option / digital lines need an unmapped-manual row type).
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-PORT-02 — positions on an INTEGRATED custodian account with no feed. Options: (a) a provisional manual entry on
  the account, marked `stated`, superseded by the feed through the existing precedence rules
  (`portfolio_precedence`); (b) a one-off file import through `POST /portfolio/import/positions` (exists, operator /
  super admin) tagged as the integrated custodian's source; (c) leave them empty until the Altruist sync runs, and
  hold the demo's integrated accounts as held-away. Recommendation: (b) for a demo, (a) only if a real client ever
  waits for a feed. Decision open.
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-PORT-03 — direct assets: a "direct asset" entry on the CRM record (asset + position by owner + valuation;
  needs a valuation route and manage_portfolio for org admins or a CRM-key path).
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-PORT-04 — private fund interests with a commitment (create_commitment route + UI).
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.
- GAP-PORT-05 — net worth reads entity_holdings, which only a rollup run writes (`POST /portfolio/rollup`); a
  record's gross stays 0 until one runs after holdings are entered.
  UPDATE (PORTENTRY1, 2026-11-04): RESOLVED — see the PORTENTRY1 entry.

**Verification.** `apps/api/scripts/verify_crmdetail1.py` WRITTEN, not run (self-checks only were run). Regression
inside it: verify_perms1.py once, flat, after teardown, VERIFY_NESTED=1, 2700 s. `node --test tests/crmdetail1/`
passes (8/8). `npm run build` not run here (WSL memory) — the verify runs it.

## 00000000000000000000000000000000000000000000000000000000000000000000000000000. SECMASTER1 — the security master from free public sources: five reference-data providers, the matcher, the 13(f) list, a one-time load, and four platform jobs on the workflow engine; schema APPLIED, jobs SEEDED (triggers inactive until deploy); verify WRITTEN, not yet run (2026-11-04)

**Why.** The platform master held 67 securities (54 notes, 13 indices; 51 CUSIPs, 13 tickers) — HollisMuse could not
find AAPL / MSFT / GOOG (BUGFIX2 REP-05). Every price, model, replica, held-away and X-ray feature resolves through it.

**Discovery (Task 1, `docs/SECMASTER1_DISCOVERY.md`).** Formats of all five sources from small samples (two servers
ignored Range and returned a whole file — recorded); FinanceDatabase is JerBouma's (not "JetBrains") and is one CSV
PER EXCHANGE, not per asset class; OpenFIGI keyless = 10 jobs × 25 req/min, `OPENFIGI_API_KEY` absent; the 13(f) file
name is read from the page (some quarters are `…-txt.txt`). No workflow platform scope exists → the jobs are `system:`
workflows in the **Hollisworks org** (its only user is a Super Admin): 2nd Act org admins never see them, Super Admin
does, a held run alerts the Super Admin. Canonical ticker form `BRK.B` / `ASB.PRE` / `.WS` `.U` `.R`, applied by
`normalize_identifier_value('ticker')` (no stored ticker had a separator). Search got trigram indexes and an
uncorrelated identifier sub-select.

**Built.** `docs/secmaster1_part1.sql` (APPLIED + verified by `scripts/_apply_secmaster1.py --verify`: SCHEMA OK):
data type `reference`; `security_reference_attributes` (per security / field / provider, read through the rank);
`security_master_source_files`; `sec_13f_list`; `security_master_review`; `openfigi_mappings`;
`security_master_checkpoints`; pg_trgm indexes; five provider rows with licence facts and the `reference` rank.
Adapters `providers/{nasdaq_trader,sec_tickers,openfigi,sec_13f_list,finance_database}.py` (+ `reference_common.py`:
injected transport, every sec.gov request through `edgar_fetch`, call budgets → `provider_call_log`). Matcher
`services/security_master_load.py` (CUSIP → FIGI → ticker + exchange family → new; bulk per batch; conflicts to review,
nothing overwritten; ticker move retires the old identifier; 3 distinct absent files → delisted with a truncation
guard; idempotent). `services/security_master_sync.py` (the pulls, shared by jobs + load). `services/security_master_jobs.py`
(four system workflows + actions, tier 2, Super-Admin-only non-catalog permission, refuse outside the platform org,
deadline 200 s / 2000 new per night / 40 OpenFIGI requests per step). `scripts/secmaster_initial_load.py` (operator).
`routers/security_master.py` (6 routes, all Super Admin). Front end: Super Admin → Security master
(`/admin/security-data/master`: status panel, jobs with Run now / close held run, review queue) and a platform-jobs
panel with Run now on the Runs screen. `run_scheduler_tick(only_trigger_ids=…)` (additive, verify only).

**Smoke (sprint, fixture rows only, cleaned by id):** create / idempotent re-run (0 writes) / ticker+exchange / CUSIP /
FIGI / ticker move / index-ticker conflict / delisting 1→2→3 / rank-walk read / CUSIP licence rule all behaved; it
caught a truncation guard that blocked delisting on small universes (fixed: absolute floor of 25).

**Findings.**
- [FIND] There is no un-hold path in the engine and a held run blocks its trigger forever → added
  `POST /admin/security-data/master/runs/{id}/close` (Super Admin, platform-job runs only).
- [FIND] A Canadian issuer's numeric CUSIP has a `CA` ISIN that cannot be told from the CUSIP; the derived `US` ISIN is
  checked against FinanceDatabase's stated ISIN and a difference becomes an `identifier_conflict` review item.
- [FIND] The 13 index tickers (SPX, NDX, RTY, …) carry no exchange: a listed equity using one of them is a
  `ticker_conflict` review item, never matched.
- [FIND] Preferreds / warrants / units / rights are not sent to OpenFIGI by ticker (Bloomberg spells them per issue);
  they get FIGIs through their 13(f) CUSIP.
- marketdata1's nightly is now the `system:Security data — nightly` job — **its pending Render-cron step is no longer
  needed**. It HOLDS on any refresh error (e.g. FRED_API_KEY missing) until a Super Admin closes the run.

**Operator steps.**
1. (Optional, faster) add `OPENFIGI_API_KEY` to Doppler `prd` (and the scheduler service's sync).
2. Merge + deploy, then `python3 apps/api/scripts/_apply_secmaster1.py --activate-jobs` (the four triggers were seeded
   INACTIVE: until the deploy the scheduler's registry has no `security_master.*` verbs and would mark the steps
   completed having done nothing).
3. `doppler run -- apps/api/venv/bin/python apps/api/scripts/secmaster_initial_load.py --dry-run`, then without
   `--dry-run` (`--resume` after any interruption). ~35 min with the OpenFIGI key, ~80 without.
4. Work the review queue (Super Admin → Security master).
5. Drop marketdata1's "create a Render cron for security_data_nightly.py" step.
6. Run `verify_secmaster1.py` (in `scripts/regression_set.txt`).

**UPDATE (2026-11-04, secmaster1 fix 1):** the operator run's 7 FAILs (146 PASS) had 3 causes. CODE: `BatchResult.merge` dropped `by_record`, so every OpenFIGI-mapped 13(f) CUSIP was recorded "not mapped — no match" while its security HAD been created. CODE: the engine's permission check looked up the starter under the RUN's org RLS, so a Super Admin from another home org resolved to None and was refused before the action's org guard; it now reads that row under `platform_scope`. VERIFY: a failed run is HELD and lands in `TickResult.errors`, never `fired`. The errors entry now also carries the held `run_id` (additive), and the "blocks the next fire" check now asserts the blocking run IS this section's held run. VERIFY: the client-org case now runs as the Super Admin (org-guard refusal) plus a member without the permission (permission refusal). VERIFY: the nightly-step output check picked `outs[0]` from an unordered query and now picks by step_key. Proof: one-off `scripts/_oneoff_secmaster1_fix1.py` 59/0, zero fixture rows left, real rows' checksums unchanged; `--self-checks-only` 8/0. The full `verify_secmaster1.py` has not been re-run yet; the operator should re-run it.

---

## 0000000000000000000000000000000000000000000000000000000000000000000000000000. PERMS1 — one source of permissions; org admin baseline; deal + household creation; HollisMuse re-run fixes; verify WRITTEN, not yet run (2026-11-04)

**One source (Task 2).** `services.authz.PRINCIPAL_COLUMNS` (still ONE statement, no new parameter) now also returns
`granted_names` — the user's profile (`users.profile_id` → `profile_permissions`) and permission-set
(`user_permission_sets` → `permission_set_permissions`) keys of the user's OWN org, joined to the `permissions`
catalog — and `member_safe_names` (the org's `member` role grants). `principal_from_row` unions them onto the role
grants; a user with no Super / staff / internal role and neither org-administration permission BY ROLE keeps only
the member-safe keys (BUGFIX2's rule, decided from roles so a profile key can never lift a user out of it). The legacy
profile key `"staff"` is not in the catalog and nothing checks it — it maps to NOTHING; `is_staff` / `is_internal`
stay role-only. Every other reader now returns the Principal's answer: `services.profiles.get_effective_permissions`
/ `user_has_permission` (workflow engine), `rbac.fetch_permission_names` / `get_user_permissions` / `has_permission`
(every rbac gate — it used to read roles of ANY org), `rbac.get_users_with_permission` (todo fan-out, same
statement, Super Admin not counted as holding everything), and the workflow routes (the request's cached Principal).
`/users/me` therefore carries the workflow keys; the menu's Workflows / Runs / Triggers tabs gate on the keys their
API checks (Runs / Triggers were super-admin only).

Real accounts that gained keys from the union (live, 2026-11-04): jpl99172 / jpl991723 (org_admin + "Org Admin"
profile) author_workflows, configure_workflow_triggers, view_workflow_runs; jpl991722 (support_staff + "CSA / Ops")
manage_documents, view_marketplace; jpl991721 (member) nothing. No user, role assignment or profile was changed.

[FIND] NAV-10 / WF-02: at the pre-sprint HEAD the workflow API already admitted the "Org Admin" profile holders (its
gate read the profile axis); the org admin was refused by the menu (Runs / Triggers super-admin only) and by every
ROLE-axis gate (Positions, Commitments: view_portfolio). The reverse disagreement was real too: a ROLE-only holder
(support staff's role grants view_workflow_runs) was refused by the profile-only workflow gate.

**Org admin baseline (Task 3).** `rbac.ORG_ADMIN_DEFAULT_PERMISSIONS` (+ `PERMS1_ORG_ADMIN_ADDED`) and
docs/perms1_part1.sql (APPLIED 2026-11-04: 18 grants = 9 keys × the 2 org_admin roles) add view_portfolio,
view_members, view_dashboard, view_deals, view_workflow_runs, author_workflows, configure_workflow_triggers,
manage_deals, manage_members. No approve_*. Before → after: 2nd Act org_admin 27 → 36 permissions; Hollisworks
org_admin 26 → 35 (it never had review_agent_proposals — unchanged). manage_billing (fee WRITES) is NOT granted.

**Deals (decision 1, DEAL-01).** POST /deals = require_internal + manage_deals; the deal starts draft and follows the
one lifecycle (services.deal_lifecycle); members never see a draft. The Marketplace "New deal" control and
/marketplace/new read the /users/me envelope (internal + manage_deals); they used `lib/roles.isStaff`, which
DEFAULT-ALLOWED when the token carried no roles claim — [FIND] `lib/roles.isStaff` is still used elsewhere.

**Households (decision 2, REP-01).** Portfolio → Households shows "New household" (name + member entities picked from
the CRM; account / SPV records refused) and, per household, "Add account" (custodian, the typed number stored ONLY
as the mask + the org-salted HMAC the custody import computes — same live unique key, so the import keeps working —,
owner = a household member, authority / custody: the account starts at REP7B's default; a different choice becomes
REP7B's draft change + approval task). Gate: require_internal_permission(manage_members) (services/household_admin.py,
routers/households.py GET/POST /admin/households, GET /admin/households/{id}, POST …/{id}/accounts). Audit rows
`household.created` / `household.account_added`. The UI renders only on the envelope's `permissions.can_write`.

**Defects (Task 4).**
- WF-06: system workflows (`system:` prefix — human_tasks' approval definitions) have no Delete and the API refuses
  them (409); Delete only on a never-run draft with no trigger; the confirmation names the workflow.
- WF-03: data path — GET /entities/{id}/tree read entity_relationships only, downward only; the Hargrove owners
  live in entity_ownership and point into the root. The tree now reads BOTH tables (ownership_edges) and returns
  `owners` (upward, nested, with `effective_pct`); HierarchyBuilder draws them above the root with their 60 / 40
  edges in both look-through states (the toggle is client-only). GET /lookthrough reads both tables for the graph
  (`both_tables=True`); [FIND] `get_lookthrough`'s DEFAULT stays entity_relationships-only because
  portfolio_rollup builds entity_holdings from it — moving the rollup to both tables is a separate, data-changing
  decision.
- REP-10: the duplicate guard blocks re-uploading the same bytes only while a LIVE model references the earlier
  upload (`models.LIVE_MODEL_REF_SQL`); otherwise the earlier upload (the one stored copy) is re-opened (audit
  `models.upload_reopened`, no "Duplicate" row) and may create a draft again. A draft_created upload whose model is
  gone displays "Closed — model removed". [FIND] an upload whose events AND versions were hard-deleted (bugfix2's
  clean-up) is indistinguishable from a fresh one — it stays re-openable, which is now harmless.
- WF-04: Billing & Custody lists "Fee schedules" (/fee-schedules/chat) and "Profitability" (internal +
  view_portfolio); /admin/fees redirects to the group landing; the app has its own not-found page. [FIND] the fee
  READ routes were gated on view_portfolio ALONE — a member grant: a member could read the firm's fee schedules and
  P&L. All fee-chat / fee-schedules / profitability routes now require_internal first.
- WF-05: the generator already retried once with the validator's errors and never saved invalid BPMN; the refusal
  now says, in plain words, what was wrong and what to add (`generation_refusal`).

**Test rows (Task 5).** scripts/perms1_remove_muse_rows.py --apply (operator DSN, `models.teardown = 'fixture'`
LOCAL, by id, count + checksum): workflow "MUSE – workflow" (1 definition, 1 version, 3 steps) and the six
MUSE-model.csv uploads (the orphaned Nov 3 receipt + its five 07:23 UTC duplicates); every other row's checksum
unchanged; the immutability trigger untouched; audit rows and the R2 object kept.

**Joe's product decisions (2026-11-04, Task 7).** (1) DEAL-01 org admins create deals — BUILT here. (2) REP-01
households by internal staff with manage_members from Portfolio → Households, accounts added by hand or by the
custody import, never by members — BUILT here. (3) NAV-03 / NAV-06 / NAV-07 org admins DO see price sources and the
hypothetical-performance policy — KEPT AS IS. (4) PRIV-06 team-based staff visibility (services/staff_visibility.py:
staff_assignments by user or team, team_members, users.manager_id, SOC Phase 2) is built but NOT enforced —
enforcing it is the NEXT sprint, visibility1. No visibility changed here; the new household reads go through
`require_internal_permission`, and new member-family reads must keep using `services.authz.member_entity_scope` /
`require_entity_access` — the one helper visibility1 will narrow.

[FIND] older verifies whose route matrix expected a MEMBER to read the fee routes (view_portfolio) or the
`org_admin_manage_org_settings` nav persona to see /admin/workflows now disagree by design (navregroup test updated).

verify_perms1.py WRITTEN, not yet run (self-checks only); regression: verify_staffroles1.py ONCE, nested.

UPDATE (2026-11-04, perms1 fix 1): the first run was 131 PASS / 3 FAIL. All three were VERIFY defects; no code changed and no mask leak was found. (A) The household detail is correct. Its account carries `account_number_masked` = the stored `*******6789`, and the full 4,374-char body contains no `0042`. The check searched `dumps()`, which is cut at 400 chars, and the accounts sit after that point. Every containment assertion (10, several of them negative and so vacuous past char 400) now searches the full `body_text()`, and the mask check compares against the row's stored value. (B) The teardown lacked the LOCAL GUC `execution.teardown = 'fixture'` that `authority_change_guard` (`execution_teardown_allowed()`) requires, the same GUC verify_rep7b sets. It is now added, and the trigger is untouched. (C) verify_staffroles1 `y_fixtures` now expects role ∪ catalog profile / set keys, read from the grant tables. "Nothing granted nowhere" still holds, support must match exactly with manage_documents coming via the profile only, and a new check runs a member holding the staff-only profile in a rolled-back transaction and finds no staff-only key. One-off proof: verify_perms1's own `y_households` passed 18/0, teardown left 0 rows (account_authority_settings 1 → 0), staffroles1's seed + `y_fixtures` passed 12/0 with 0 leftovers, and `--self-checks-only` passed 23/0 and 19/0. The full verify_perms1 re-run is still owed.

## 000000000000000000000000000000000000000000000000000000000000000000000000000. NEW-ISSUE NOTE CHECKS — rep12 (sprint 12 of 15): the automatic note-versus-replica check, the "New issues" feed and alerts, the underlying confirmation queue; schema APPLIED; verify WRITTEN, not yet run (2026-11-04)

- **Hook.** The one write path of a live terms row is now `note_terms_extraction.store_extraction`
  (split out of `extract_terms`, behaviour unchanged); its LAST step (after routing) is
  `services.note_checks.on_terms_written`. A human correction (`/admin/pricing/note-terms/{id}/resolve`)
  fires it too. The daily document-reading run writes readings / staging only (B2 promotion never
  built), so `note_checks.sweep` runs each scheduler tick after rep11's verdicts as the safety net.
- **Idempotent by terms version** (+ the underlying edges): one live check per (org, note) is a
  partial UNIQUE index. A restatement, a correction or a confirmed underlying closes the live check
  (valid_to) and writes a new one; status / alert changes are system-axis (archived copy, id kept).
- **Draft only, never approved.** rep11's `buildable` decides; `note_replication.prepare_draft` is
  rep11's builder with no person (created_by NULL, no maker). The comparison is rep11's
  `compute_cost`, stored with `computed_source = 'automatic_check'` (computed_by NULL — rep11's
  comparisons table changed additively). An index underlying is assumed broad-based (rep8's
  default) unless a contract already says otherwise — recorded on the check for a person to confirm.
- **Alerts** are system-opened human tasks (`note_check_alert`, eligible view_replication):
  embedded-fee estimate above the org threshold (at the check, or priced later by a person — the
  alert sweep), or a watched issuer / underlying / archetype. A non-internal recipient's to-do is
  dismissed at once. Review / dismiss (dismiss needs a note) completes the open alert task, else is
  applied directly; both record the user.
- **Underlying queue** (`/portfolio/replication/notes/underlyings`, Super Admin): edges grouped by
  one proposal vs none; one-click and multi-confirm call `confirm_resolution` once per edge as the
  clicker (still the one writer of 'resolved'); the affected notes are re-checked. The pricing-admin
  confirm / reject routes re-check too.
- **[FIND] routing queues every extracted row whose filer has no active STP policy**, and rep11 reads
  'queued' as "needs review": a newly extracted note gets a draft only under an STP grant (or after
  its review). Today 0 STP policies are active for the real filers.
- **[FIND]** live data at discovery: 69 ambiguous edges (each exactly one high `known_index`
  proposal — the schema allows one), 28 unresolved (no target), 0 resolved; 31 notes have a single
  edge, 23 have 2–5 (those can never be built by rep11's single-underlying rule). Only `org_admin`
  holds view_replication in real orgs; the human-task fan-out would notify a client role holding it,
  hence the withholding step.
- Schema: `docs/rep12_part1.sql` via `_apply_rep12.py --all` (SCHEMA OK). Routes regenerated in
  AUTHZ1_ROUTES (`--check` CURRENT): reads `internal + any(view_replication)`, writes
  manage_replication, the queue super_admin. Router included BEFORE rep11's (its `/{note_id}` would
  422 `/checks`). `node --test tests/` 477/0 locally; npm run build NOT run (WSL memory).
- `verify_rep12.py` WRITTEN, not run (fakes the model step through store_extraction; hook scoped to
  fixture orgs via `note_checks.HOOK_ONLY_ORGS`). Added to `scripts/regression_set.txt`.
- Operator steps: assign models + budgets to the note-extraction job (AI Models → Model Jobs); grant
  STP per filer where trusted; work the underlying queue; set the org's alert threshold / watches on
  New issues; enter note-price, level and premium quotes. Add rep12's merge to
  `_verify_self.SPRINT_MERGES` when merged.

## 00000000000000000000000000000000000000000000000000000000000000000000000000. STAFFROLES1 — "may SEE staff data" (is_internal) split from "skips permission checks" (is_staff); verify WRITTEN, not yet run (2026-11-04)

**Why.** After BUGFIX2 the 24 staff-only reads (CRM, ledger, SPV, document links, precedence) used `require_staff`,
and "staff" meant `STAFF_ROLE_NAMES` (investment_staff / admin / super_admin / owner) — the same flag that SKIPS
permission checks. Org admins and support staff (jpl991723 / jpl991722) were refused the CRM. Adding them to
`STAFF_ROLE_NAMES` would have handed them every bypass (approvals included), so the two ideas are now separate.

**The two concepts** (`services/authz.py`):

| Concept | Name | Who | Used by |
|---|---|---|---|
| skips permission checks (UNCHANGED) | `STAFF_ROLE_NAMES` → `Principal.is_staff`, `require_staff`, `require_staff_or_permission` | investment_staff, admin, owner, super_admin | every WRITE that was staff-gated (relationships, ownership, groups, targets, extraction / brief), the SPV subscribe bypass, the settings catalogs |
| may SEE staff data (new) | `INTERNAL_ROLE_NAMES` → `Principal.is_internal`, `require_internal`, `require_internal_permission`, `authz.is_internal` | the staff roles + org_admin, support_staff, advisor, compliance, fund_finance, investment_committee, ir_member_relations, member_manager | staff-data READS; member scope on a READ (`require_entity_access` / `member_entity_scope`); draft-deal / SPV / document-search / X-ray / assistant visibility; `is_member_principal` |

Client roles (`member`, `next_gen`) are not internal; an unknown or unclassified role is not internal (fail
closed) — a new internal role must be added to `INTERNAL_ROLE_NAMES` by name. `/users/me` publishes `is_internal`
beside `is_staff`; the menu's `isInternalStrict` mirrors it (`isStaffStrict` kept for the bypass).

**Routes (31, all reads; `docs/AUTHZ1_ROUTES.md`).** CRM (`/entities/{id}/full|notes|ownership-graph|tree|lookthrough|relationships|ownership|ownership/history|ownership-tree`,
`/entity-groups` ×2), document links ×3, `/portfolio/precedence` → `require_internal` (any internal role; the
compliance record inside `/full` still needs `view_compliance`). SPV reads ×5 → internal + `view_spv`. Ledger reads ×5 →
internal + `view_spv` or `manage_ledger`. Billing groups / custody import reads ×6 → internal + `view_portfolio`.
Staff and Super Admin pass all of them as before. `view_spv` / `view_portfolio` are MEMBER grants — the internal
role is what refuses a member. The 3 settings catalogs stay staff-or-`manage_org_settings`. Entity-scoped WRITES keep
the member path for a non-staff internal role (it may read any family, write none it is not a delegate of).

**What org admin and support staff can now see** (live grants 2026-11-04): org admin — CRM, documents, and its
own permission screens (replication, instructions, models, proposals, tax, blocks, settings); no SPV Manager /
ledger / billing (holds no `view_spv` / `view_portfolio`). Support staff — CRM (with the compliance record:
`view_compliance`), documents, Billing & Custody reads (`view_portfolio`); their staff-only "CSA / Ops" profile is
no longer stripped to member-safe keys. **Still cannot**: any write that is staff-gated (`require_staff`), and any
approval they do not hold the permission for. Support staff holds no `approve_*`; the REAL org_admin role holds every
`approve_*` / `manage_*` by GRANT (ORG_ADMIN_DEFAULT_PERMISSIONS) — that is a permission decision, unchanged by this
sprint. Not the bypass.

**Staff visibility (Task 4): enforcement via `staff_assignments` stays OFF.** Every internal role sees all of the
org's families on the CRM / member-scope reads, as staff did before (RLS keeps it in-org). The paths that already ran
through `visible_entity_ids(is_staff=True)` (document / semantic search, commitments, document review, assistant
counts, X-ray households) use the existing staff engine (assignments / teams / hierarchy; Super Admin all) — unchanged,
now also for internal roles. [FIND] those were already assignment-scoped for staff before this sprint.

**[FIND]s.** The prompt's "20 router files use require_staff" was 15 at BEFORE_REF (11 + 4 with
`require_staff_or_permission`). `fund_finance` / `compliance` / `investment_committee` hold only
`review_agent_proposals`: they now see the CRM but no SPV / ledger / billing (operator grant decision). The workflow
Runs menu tab stays Super Admin only (profile-axis key not on `/users/me`). `docs/schema_snapshot.sql` lacks
`investment_profile_answers` (the table is live).

**Regression edits:** `verify_bugfix2` (the member expectation also covers the `internal` rules; its staff tree may
read `internal`), `verify_authz1` matrix (`internal` / `internal_perm` rules), `verify_navregroup` live personas carry
`is_internal`, `navregroup_server_gates.mjs` / `navregroup_traced_gates.mjs` (gate kinds `internal`, `internal:<perm>`),
`_verify_self.SPRINT_MERGES['bugfix2']`. Verify: `apps/api/scripts/verify_staffroles1.py` (WRITTEN, not run; regression
= verify_bugfix2 once, flat, VERIFY_NESTED=1, 2700 s).

## 0000000000000000000000000000000000000000000000000000000000000000000000000. BUGFIX2 — member data isolation first, then the HollisMuse round-3 findings; verify WRITTEN, not yet run (2026-11-03)

**The Critical (three HollisMuse CRITICALs, one root cause).** A MEMBER pasted a staff URL and read another
family's full CRM record (Hargrove Family Trust: legal details, ownership, every tab, an Edit button) and its
Compliance tab (KYC / OFAC / AML / accreditation / PEP); Investment Profile's "Investment Entity" dropdown
listed all 34 tenant entities; the member menu showed CRM, SPV Manager and Billing & Custody. Cause: 73
non-public READ routes carried no gate beyond an admitted account (AUTHZ1 recorded the CRM / ledger /
document-link reads as "left for a product decision"), and `GET /entities/{id}/full` returned the
compliance record with no permission check (the "Compliance Override" set was NOT the cause — it grants
only `view_dashboard` and the Principal never read permission sets).

**Task 1a — the 73 routes, classified** (full table: `docs/AUTHZ1_ROUTES.md`; every open read carries its
class in Notes; `verify_bugfix2` asserts the table):

| Class | # | Gate after | Routes |
|---|---|---|---|
| STAFF-ONLY | 24 | `require_staff` | `/entities/{id}/full`, `/notes`, `/ownership-graph`, `/tree`, `/lookthrough`, `/relationships`, `/ownership`, `/ownership/history`; `/entity-groups` (2); `/ledger/*` (5); `/spvs` list / detail / documents / download / transactions (5); `/documents/{id}/links`, `/records/…/documents`, `/document-link-proposals`; `/portfolio/precedence` |
| STAFF-OR-ORG-ADMIN | 3 | staff or `manage_org_settings` | `/admin/model-catalog`, `/admin/system-one-catalog`, `/modeling/ta/calibration-floor` (settings screens) |
| MEMBER-SCOPED | 18 | own family (404 otherwise) / published deals | `/entities`, `/entities/search`, `/entities/{id}`, entity documents list + download (+ the duplicate handler), `/portfolio/targets`, `/portfolio/allocations`, `/allocation-lens`; `/deals`, `/deals/{id}`, `/taxonomy-placement`, `/lifecycle`, `/ai-summary`, `/deals/{id}/classes`, `/deals/stage-summary`; `/document-search`, `/semantic-search` (already scoped by `visible_entity_ids`) |
| OWN ROWS | 18 | account only (WHERE user_id = caller) | assistant (2), dashboard brief / narration / todos, deal-room me / my-access, portfolio my-investments / summary, `/me/ownership-graph`, notifications (3), `/users/me`, market key-dates / views, replication member backtests, human-tasks mine |
| PUBLIC-TO-ORG | 10 | account only | config, taxonomy, transaction-types, reference, fx-rates, theme, investment-profile questions, market catalog / series, deal-room templates |

**Task 1b.** A member's family = `delegate_grants` → `resolve_entity_set` subtree → `filter_restricted`
(`document_embedding.visible_entity_ids`, is_staff=False) — the engine the clean member portfolio path
already used. New `services.authz.member_entity_scope` (None for staff, the member's id set otherwise —
an EMPTY set means nothing, never "all") + the existing `require_entity_access` (404). The three real
member test accounts hold ZERO delegate grants, so today their family is empty.

**Task 1c.** jpl991722 and jpl991723 are identical in the DB; the API refuses both. "Loaded" was the Next
page chrome + title, rendered for any signed-in user. Fixed by area layouts (below).

**Fixes.** Route gates as tabled. `MEMBER_VISIBLE_STATUSES` = ('active',) (was also `under_review` —
"Verify12 Test Deal"); a member's `?status=` narrows instead of replacing the member filter (it listed
drafts); dashboard NEW DEALS and the assistant's show-new-deals use the one set + the 506(b) offering rule.
Menu: CRM / SPV Manager / Billing Groups / Custody Import → `GATE_STAFF_STRICT`; area layouts
(`app/crm`, `app/spvs`, `app/admin`, `app/portfolio/replication`) refuse server-side via `AreaGuard` /
`canOpenArea` (TILT-02: Tilts rendered an empty state). Staff-only profiles / permission sets (any key the
org's `member` role does not grant) are refused for a member user (409) and a member already holding one
gets only the member-safe keys on the profile axis (`services.profiles.get_effective_permissions`).
Task 5: NAV-02 (`rbac.ensure_permission` upserted the global catalog under RLS — reads first now);
REP-01 / DEAL-01 = staff / super admin only BY DESIGN (PROJECT_STATUS "Unchanged rule", rbac
ORG_ADMIN_DEFAULT_PERMISSIONS, live grants); REP-05 resolution message + links; REP-09 task Open → the
task's own page; BT-01 failure reason; BT-02 wording; AI-08 catalog loading state; WF-01 BPMN diagram
layout added when the generator omits it; WF-03 the staff tree was SHADOWED by `entities.ownership_graph`
(same path, included first) and read only `entity_relationships` — own path `/ownership-tree`, both
ownership tables, owners shown by default when the entity owns nothing; workflow DELETE for a definition
with no runs and no triggers (`author_workflows`; maker-checker not required for a never-run draft).
Task 6: verify_bulkfetchui times the server (`elapsed_ms`). Task 7: the MUSE model / workflow rows removed
by `scripts/bugfix2_remove_muse_rows.py --apply` (18 rows; every other row's checksum unchanged).

**Operator (data, not changed by this sprint):** jpl991723 → org admin; jpl991722 → a staff role;
jpl991721 → remove the "Compliance Override" set AND the "CSA / Ops" profile (the real staff-only grant);
verify_bugfix2 prints every other member holding a staff-only profile / set.

**[FIND]s.** Org admins (not staff) no longer read the CRM / ledger / SPV APIs (staff-only). Billing groups /
custody import READ APIs gated on `view_portfolio` alone (a member grant) — they now also `require_staff`
(6 routes, outside the 73; fail closed). Old `/admin/document-search` bookmarks are refused to a
member by the /admin layout instead of redirecting.

**UPDATE 2026-11-03 (bugfix2 fix 1 — the run's 5 FAILs):** A VERIFY — authz1's U_MEMBER is a delegate of E_A (seed, since AUTHZ1), so its 200 was correct; the unlinked case now targets a new unlinked org-A entity E_A2 → 404 (Principal: member, is_staff False, 10 member perms; path get_entity → require_entity_access → visible_entity_ids). B CODE+VERIFY — the 4 ledger GETs gate as a route dependency (member 403 before query validation; staff still 422 without vehicle_id), verify sends a fixture SPV vehicle_id and requires staff 200. C CODE — GET /entity-groups/{id} selected nonexistent `entity_groups.updated_at` / `entity_group_members.added_at` (500 for every id; POST create + add-member had the same drift — fixed); download of a record with no storage_key → 404 not R2-with-None; verify seeds the file key + a group member and stubs the by-name `get_signed_url` import. D VERIFY — fixture versions lacked `is_current = true` (DEFAULT false). E VERIFY — one real user's `last_login_at` moved mid-run (a real sign-in; no other column); users checksum excludes `last_login_at` only and a new per-row column diff fails on any other change. One-off proof: 22/0 + A section green (verify_bugfix2.py not re-run in full).

## 000000000000000000000000000000000000000000000000000000000000000000000000. NOTE REPLICATION — rep11 (sprint 11 of 15, PHASE 3 BEGINS: structured notes): replicability verdicts from the 424B2 terms, replicas from rep8's options machinery (+ a Treasury leg), the at-expiry overlay, the embedded-fee estimate only when quoted, the X-ray marker under rep10's policy; schema APPLIED; verify WRITTEN, not yet run (2026-11-03)

**What landed**

- **Schema.** `docs/rep11_part1.sql` was applied through `_apply_rep11.py --all` (SCHEMA OK). It
  adds the platform `portfolio.note_replicability` (valid axis) and the org
  `public.note_replicas` (system axis, maker-checker), `note_replica_legs` (valid axis) and
  `note_replica_comparisons` (insert-only). Every operation has an RLS policy; org tables use
  NULLIF on the org GUC. `adviser_quotes` gains `note_price` / `treasury_yield`.
- **The verdict job ran once** over the real notes as `app_service` (rolbypassrls = false). It
  wrote 54 verdicts: **static 11, approximate 3, not replicable 40**. A second run wrote
  nothing. It is now a step of the cron tick (`workflow_scheduler_tick.py`).
- **Not buildable today: 0 of 54.** No underlying edge is resolved yet; 69 are proposed
  (ambiguous), awaiting a Super Admin. 8 of the 14 replicable notes also need term review.
  `routing_decision` is NULL on all 54, so "approved" reads from `extraction_confidence` and
  `field_status`.
- **Distribution agent and fees** are wired from `note_term_readings`, but no reading belongs
  to the 54 notes' filings, so every note reads "not in the extracted terms".
- **Approved replicas.** An approved replica is the firm's registry entry for the note (the
  X-ray's "replicable alternative"). It does **not** create a `portfolio.models` version:
  weight-based versions cannot carry option legs at a ratio. That wiring is open (a rep12
  candidate).
- **SSVI is not used.** It needs ≥ 8 call / put pairs per expiry and stops at 2.5 years.
  Pricing reads each contract's own premium, or its own entered implied vol through
  Black–Scholes.
- **Front end.** Replication → Notes, a note page, a replica page, and the X-ray's Structured
  notes card. `node --test` for rep11 + rep7 (14) and nav (20) passes locally. npm run build
  was not run (sprint rule).
- **Routes.** 10 new routes, all gated, in AUTHZ1_ROUTES; `authz1_routes.py --check` is CURRENT.
  `verify_rep11.py` was added to `scripts/regression_set.txt`; its regression is
  `verify_rep8.py` (once, flat, after teardown, VERIFY_NESTED=1, 2700 s).

**Counsel / Joe items** are listed in docs/REPLICATION_STUDIO.md ("rep11"):

- tax treatment of the note vs the replica (straddle pending);
- whether a member-visible embedded fee is marketing;
- the issuer-credit disclosure wording;
- the `floor` / `cap_pct` scale semantics.

## 00000000000000000000000000000000000000000000000000000000000000000000000. PREVIEWPERF — the EDGAR bulk-download live preview back under 2 s; verify WRITTEN, not yet run (2026-11-03)

**Cause: round trips, not SQL.** verify_bulkfetchui's "live preview returns under 2 s" failed (2026-11-02: best 4095 ms end to end, 2416 ms server-side; 2026-11-03: 3.75 s on an idle machine). Measured 2026-11-03, JPMorgan 2025 (18,860 filings), warm, laptop → Supabase:

| step | statements | time (best of 3) | SQL execution (EXPLAIN ANALYZE) |
|---|---|---|---|
| validate_params (load_source, catalog, last_indexed_date) | 3 platform_scope blocks | 292 ms | < 1 ms each |
| scope_counts (one block per institution) | 1 per institution | 113 ms | 20 ms (bitmap on selected_count_idx, 1,193 buffers) |
| stored_bytes | 1 | 97 ms | 5 ms (seq scan, 11,836 rows) |
| measured_rate | 1 | 95 ms | 0.1 ms |
| last_indexed_date (again, at the end) | 1 | 89 ms | 0.05 ms |
| **preview() in total** | **28** | **636–706 ms** (2,150 ms on a slow-network call) | ~26 ms |
| middleware principal (AUTHZ1) | 12 | 377–489 ms | — |
| route gate `_require_super_admin` (re-loads the Principal) | 5 | 123–131 ms | — |
| **end to end** (ASGITransport) | **49** | **1,241–1,406 ms** | response 884 bytes |

A `platform_scope` block is savepoint + set_config + prepare + execute + release. At 23 ms per statement (today) the preview took 0.65 s; at the ~85 ms of 2026-11-02 it took 2.4 s. **The index was already right.** The 20 ms scan uses selected_count_idx, and the scope's OR branches (fetch_failed retry, peek-skipped) rule out an index-only plan. The end-to-end gap is the middleware plus the redundant gate plus the route's own transaction (BEGIN / set_config / COMMIT). Serialisation and the test client are not part of it: the response is 884 bytes.

**Fix (no DDL, no index):**
- `edgar_bulk_queue.preview` now runs TWO statements inside ONE platform_scope: `PREVIEW_SNAPSHOT_SQL` (sources, catalog, last indexed date, stored bytes, measured rate) and `edgar_bulk.count_scope` (every institution in one GROUP BY cik, year, split per institution in Python). Then come validate_params' own rules (`_validate`, now shared with the queue path) and plan_scope's own arithmetic (`plan_from_counts`).
- The snapshot embeds the SAME SQL constants the standalone functions run: `SOURCES_SQL`, `CATALOG_*`, `LAST_INDEXED_SQL`, `STORED_BYTES_SQL`, `MEASURED_RATE_SQL`.
- `scope_counts` (CLI dry run, bulkfetchui3) uses the same single query.
- `routers/edgar_pipeline_admin._require_super_admin` reads the middleware's cached Principal: 0 statements. It falls back to the DB path only when no Principal is cached; the 403 detail is unchanged.
- No caching was added, so there is nothing to invalidate.

**Result:**
- preview(): 28 → 5 statements, server-side 636–706 → 149–164 ms.
- End to end: 49 → 21 statements, 1,241–1,406 → 648–988 ms (best of 3 per run: 648–685 ms).
- The widest scope (all 21 institutions) is faster too: 2,109 → 204 ms for 2025, and 2,717 → 822 ms for all years. All years is now a parallel seq scan, still faster than the 21 bitmap scans it replaced.

**Answers unchanged:** 172 previews (21 institutions × 2019–2026, all institutions for 2025 and for all years, a preliminaries variant, a peek-off variant) are byte-identical before and after (JSON minus elapsed_ms). The refusal messages and their order are unchanged: unknown bank, empty banks, unknown source, To past the index (409 + date), From after To. No index was created, so no other query's plan could change (institutions(), DOWNLOADED_STORED_SQL, the claim — untouched).

**Remaining lever (not taken — every route, AUTHZ1 code):** the middleware's 12 statements are now the biggest share of end to end. At 2026-11-02's ~85 ms per statement, the 21 statements project to ~1.8 s, inside the 2 s limit but with little margin. Merging `authz.load_principal`'s four reads (users, roles, permissions, all permissions) into one statement would save 3 on every request.

verify_bulkfetchui.py --self-checks-only: 1 PASS / 0 FAIL. `apps/api/scripts/verify_previewperf.py` is WRITTEN, NOT RUN, and added to scripts/regression_set.txt. It checks: (1) no laptop worker; (2) selected_count_idx valid and used, no previewperf index; (3) JPMorgan 2025, Citigroup 2024 and Goldman Sachs 2023 through the app equal SQL, and every institution-year is identical to the code at d9afcea, loaded with `git show` and run side by side; (4) best-of-3 server-side and end to end < 2 s with the load rule, preview() ≤ 6 statements, gate 0, real super admin's last_login_at unchanged; (5) verify_bulkfetchui.py in full → 0 FAIL.

## 0000000000000000000000000000000000000000000000000000000000000000000000. AI MODEL BUDGETS — aibudget1: daily model budgets on the ensemble picker, enforced at the call, and a daily document-reading run inside them; verify WRITTEN, not yet run (2026-11-03)

Joe (2026-11-03): "when assigning models to jobs, for this document reading job, that is where you pick the budget
after you pick the model for both models. An option is 'unlimited'." Built for OpenAI's complimentary daily tokens on
traffic shared with OpenAI; vendor-neutral. Full design: `docs/LITELLM_INTEGRATION_DESIGN_V1.md` §14.9.

Schema `docs/aibudget1_part1.sql` is APPLIED (`scripts/_apply_aibudget1.py`: pre-check — no views over
ai_ensemble_configs, only the retire-only trigger, 0 rows; then SCHEMA OK). Four budget columns on
`ai_ensemble_configs` (kind unlimited | tokens_per_day | usd_per_day; value NULL iff unlimited, positive, whole for
tokens — 4 CHECKs); the retire-only trigger compares whole rows, so budgets are immutable with no trigger change and a
budget change is a NEW config row. `public.ai_daily_usage` (the shared per-model ledger, PK (budget_day, deployment))
and `public.ai_daily_runs` (one scheduled run per job per UTC day, one running run per job — partial unique indexes),
both platform-wide, RLS for all four operations on app.is_super_admin. No business rows.

Code: `services/ai_budget.py` (limits, the atomic reserve / settle ledger, the scope, `pair`), the hook in
`services/note_extraction/proxy.chat` (the one chokepoint of every ensemble call), pair reservations in
`cascade._run_note` (both reader pairs), `replica_terms.read_pair` and `held_away.read_statement_pair`, a job scope in
`runner.run_notes`; `services/ai_daily_runs.py` (the two public-data jobs, lease-claimed time slices, the scheduler step
wired LAST into `workflow_scheduler_tick.py`, "run now"); `services/ai_ensembles.py` (budgets in activation, the
public-data refusal per job, the envelope: budget vocabulary, day boundary, today's usage per model); routes
`GET /admin/ai/jobs`, `GET /admin/ai/daily-runs`, `POST /admin/ai/daily-runs/{task_key}/run` (super_admin; in
`docs/AUTHZ1_ROUTES.json`, `--check` CURRENT). Web: the budget control right after each model select
(`components/admin/EnsembleBudget.jsx` in `EnsemblePanel`), the new tab AI Models → Model Jobs (`/admin/ai-jobs`: the
daily runs + every job's picker), `node --test tests/aibudget/` 11/11 and the full `node --test tests/` 452 / 0 fail
(run in the sprint; `npm run build` was not).

Verify `apps/api/scripts/verify_aibudget1.py` is WRITTEN, NOT RUN (`--self-checks-only` run after the commit). Its
regression section re-runs `verify_ensemblesystemone.py` once, flat, after teardown, VERIFY_NESTED=1, 2700 s.

UPDATE (PORTENTRY2 FIX 1, 2026-11-05): the run was 147 PASS / 1 FAIL. Both causes were VERIFY defects, and no product code changed. (A) The owner-candidates check expected the hand-written pair {llc2, own1}. `household_admin.owner_candidates` correctly also returned `child`, which the LP (a member) owns 60%. The expected set is now DERIVED by independent SQL over both ownership tables (every current legal entity a member owns that is not a member). A new seeded negative case, Side Venture LLC, is owned only by own2 (never a member) through `entity_ownership`, and it is asserted absent. (B, hidden) 12 verify scripts read a nested script's result with `re.search(r"TOTAL: …")`, the FIRST match anywhere. The regression line "verify_portentry1.py: 0 FAIL — TOTAL: 23 PASS, 0 FAIL" was actually perms1's self-check total printed in a detail line of a 167-check run, so the nested gate could pass while the nested script failed. One shared helper, `apps/api/scripts/_verify_total.py` (`nested_total(out, returncode)`), now reads the LAST line that starts with `TOTAL: ` and requires the exit code to agree; it fails if that line is missing, PASS is 0, or there is no exit code. It is used by authz1, bugfix1, bugfix2, crmdetail1, goldset, notefix1, notefix2, perms1, portentry1 (both parses), portentry2, staffroles1 and usermgmt1. It is also used by bulkfetch, bulkfetchui, bulkfetchui2, bulkfetchui3 and navregroup, which already took the last line but never checked the exit code. scripts/run_regressions.sh was already correct (anchored `^TOTAL:`, `tail -n 1`, rc checked). The other 24 last-match scripts (rep*, dealroom*, vhk*, …) were left unchanged. Proof: one-off driving verify_portentry2's own seed + `y_members` passed 20/0, with the service set equal to the derived set {llc2, own1, child}, side excluded, 0 fixture rows left and every counted table at its pre-test count. The parser one-off passed 9/0: it reproduced the old first-match 23/0, then showed the final-line numbers win, that an exit-code disagreement either way fails, and that a missing final line, empty output, timeout and 0 PASS each fail. `--self-checks-only` passed 0 FAIL on every changed script that has the mode. FIND: goldset / notefix1 / notefix2 have NO `--self-checks-only` (they ignore argv and run in full). goldset's full run was 127/1, failing on "every live field has a current gold value → 'done'" with a 422 on proceeds_to_issuer_usd, outside the regression section this fix touched, so it is pre-existing and not investigated. The nested goldset gates in notefix1 (62/1) and notefix2 (107/1) correctly reported it as "TOTAL: 126 PASS, 1 FAIL; 1 FAIL (exit 1)". That was their only FAIL, and all fixtures were torn down by each script. The full verify_portentry2.py has NOT been re-run here (WSL memory).

UPDATE (aibudget1 fix 1, 2026-11-03): the re-run was 129 PASS / 1 FAIL, the FAIL in the verify_ensemblesystemone regression ([Y] "an INSERT failing after the retire rolls the retire back", raised=TypeError). **VERIFY defect, not code**: its `broken_llm_options(c)` stub took one argument while aibudget1's `activate_ensemble` now calls `llm_options(conn, task_key)` → TypeError on the function's first line, before any SQL, so the rollback was never exercised. The code was right: a None version still reaches the INSERT. The stub now forwards `*args, **kwargs`, and the assertion is unchanged. One-off `scripts/_oneoff_aibudget1_fix1.py` 3/3: it reproduced the TypeError (zero statements issued), and with the corrected stub it got NotNullViolation on the INSERT after the retire UPDATE ran, with #2 still active, 2 rows, and 0 fixture rows left. verify_ensemblesystemone has no `--self-checks-only` mode (it ignores argv), so it was compile-checked only and not run. The full verify_aibudget1 re-run is still owed.

Task 1 findings:
- 1a. note_terms_extraction: 5,366 manifest rows `ready_for_extraction` (all with a stored document), 0 staged → 5,366
  queued; started today only by `scripts/run_note_extraction_pilot.py` (reads the active ensemble). replica_terms_
  extraction: started per registry entry by a person (`build_draft` → `run_extraction`; reads the active ensemble, with
  a note_terms_extraction fallback); 0 live registry entries in any org → 0 queued. statement_positions: started by the
  member flow (`held_away.create_statement_import`); member data, never scheduled. No ensemble config is active
  (0 rows), so no job runs until Joe assigns models.
- 1b. Every note reader call row (`note_term_readings`, `__call__`, model_1/model_2/escalation) carries input and output
  tokens (100 rows, 0 missing); replica readings carry tokens (0 rows today). **[FIND] the statement reader
  (`held_away._proxy_reader`) records no tokens anywhere** — so the ledger is a table, not a view over the readings.
  Jev (System One) goes through `/typesafe`, not a Model 1/2 call, and is not budgeted. The app reads no LiteLLM spend
  log (app_service cannot read the litellm schema).
- 1c. Day boundary: midnight UTC, computed by the database at each reserve (`(now() AT TIME ZONE 'UTC')::date`), shown
  on the picker and the daily-run screen.
- 1d. gpt-5-mini → `openai/gpt-5-mini`, gpt-5-nano → `openai/gpt-5-nano`, DB-stored deployments, both registered
  against the key NAME `OPENAI_API_KEY` (Doppler prd_lite_llm). /model/info exposes no OpenAI project or organization,
  so whether that project has data sharing ON is not readable from the platform — an operator step. Not changed.
- 1e. Picker: GET → llm_options / system_one_options / preselected / blocker / active / history / permissions; POST →
  task_key, model_1, model_2, system_one_model, notes. Only the hazard task had a picker on screen
  (`/admin/pricing/note-terms-queue`); the document jobs had none → the new Model Jobs page.
- **[FIND] the statement path never ran the public-data guard** (it calls the proxy directly, not through
  `services.extraction`), and the picker did not refuse a data-sharing model for statement_positions. Both closed: the
  picker refuses it with the reason, and `held_away._refuse_sharing_models` refuses it at the call.
- [FIND] replica terms reuse keyed on the config id, so a budget-only change (a new config row) would have re-read every
  prospectus. Reuse (and the daily queue) now key on the same two models.
- [FIND] Input tokens are an estimate (chars / 4); output is bounded by max_tokens. A prompt that tokenizes denser can
  record slightly more input than it reserved — the next reservation sees the true total. The statement reader sends
  no max_tokens; under a LIMITED budget ai_budget injects `AI_BUDGET_DEFAULT_MAX_TOKENS` (16,000).
- [FIND] Earlier verify scripts that run `run_notes` / the statement import with a faked proxy now write
  `ai_daily_usage` rows for their fixture model names (their teardowns do not know the table). Harmless to budgets
  (fixture names); a later housekeeping sprint may add the table to those teardowns.
- Open: a crash between reserve and settle leaves that day's reservation in place until the day ends (no sweep).
  The daily note run's per-run dollar cap is `AI_DAILY_RUN_SPEND_CAP_USD` (unset = none; the budgets are the limit).

Operator steps (also in the design doc): enable data sharing on the OpenAI project behind `OPENAI_API_KEY`; read the
daily complimentary amounts on OpenAI's dashboard; AI Models → Model Jobs → assign Model 1 / Model 2 with those
numbers as tokens-per-day budgets for the two document jobs (note terms, fund prospectus terms).
## 0000000000000000000000000000000000000000000000000000000000000000000000. VERIFY HOUSEKEEPING ROUND 2 — vhk2: no check frozen against live state; verify WRITTEN, not yet run (2026-11-03)

The full regression run of 2026-11-02 (25 scripts on bfdfe16, 14 clean, 11 with failures) was triaged one failure at a time. **Every failure was a stale check or a code/DB timing gap. No real defect was proven, so no gate, route record, nav visibility, router or schema changed.** Full record: `docs/VHK2_FINDINGS.md`.

- **authz1:** the 32 org_admin 2xx are routes gated by permissions org_admin holds by design (`rbac.ORG_ADMIN_DEFAULT_PERMISSIONS`, rep1–rep10). The verify had frozen the persona at 2 permissions; it now reads them from the DB. Effective permissions are now "== the recompute from current role grants" for every real user, not authz1's counts. The flag rule covers only scripts that load the app.
- **navregroup:** the "refused" hrefs were untraced, not refused (the hand gate table predates REP1–REP10 / MARKETDATA1). Gates are now traced from `AUTHZ1_ROUTES.json` (untraced fails closed). The page and additions rules hold at navregroup's own merge and as rules today; every real role is checked at run time. [FIND] `/portfolio/households` has no nav link (URL only).
- **dealroom1:** allowed downloads now follow the room's settings. dealroom3's watermark-ON default serves investors a watermarked `/file` stream, which is fetched and checked. Teardown passes dealroom3's `dealroom.activity_teardown` GUC. `--cleanup-only` removed the 2026-11-02 leftovers (625 rows); LEFTOVERS 0.
- **bulkfetchui** perf: the 2 s limit is unchanged; it retries while load per CPU is > 0.75 and reports the load. **bulkfetchui3**, **edgarpipelinea**, **rep1** (`_apply_rep1 --verify`), **rep2**, **vhk1**: frozen lists re-based to rules (VHK2_FINDINGS 1a / 1f). The real-sample shortfall (Goldman Sachs pool exhausted) is a [FIND].
- **rep10-related:** rep5 and lotmethod are clear on c6f7077. marketdata1's `--verify` now allows rep10's deliberate `factor_score` vendor-slot rank (never first, still not connected); marketdata1's own six types stay unranked for it.
- `docs/VERIFY_RULES.md` rules 6–8: never freeze a live count or list; timing checks state their load rule; append-only teardowns pass the table's own GUC. `_verify_self.SPRINT_MERGES` gains rep10 (c6f7077). The operator's removal of the `Claude outputs/` prompt copies (committed by b9c186c) is committed.

UPDATE (2026-11-03, vhk2 fix 1, branch vhk2-fix1): its run on main was 49 PASS / 5 FAIL; all five were VERIFY defects, no code changed. (A–C) `_verify_self` ended a merged sprint at its merge COMMIT, whose tree also carries aibudget1 (merged first) — a sprint now ends at its merge's second parent, `BEFORE_REF` must lie on its own line (VERIFY_RULES rule 9), bulkfetchui3's `<end>^1` base became `sprint_base_ref`, vhk1's helper check expects rep6 → f8f5741, and verify_vhk2 reproduces the old range then proves the fix. (D) `/admin/ai-jobs` calls only server actions, so the tracer could not see its three API routes (all in AUTHZ1_ROUTES.json, all `super`) — a hand row in navregroup_server_gates.mjs; the un-nested regression section also had two stale checks (BUGFIX1's comingSoon items in "super admin sees every item"; MARKETDATA1's tabs missing from AUTHZ1's frozen role-less loss list, now derived). (E) the worker check read the stale `apps/api/.env` DATABASE_URL — it now uses `_db_connect.admin_dsn()` (live count 0). `.env` keys that differ from Doppler: DATABASE_URL, APP_SERVICE_DATABASE_URL. `--self-checks-only` 28/28 scripts 0 FAIL; verify_navregroup in full 158/3 before the harness fix, the fixed section then 10/10 in isolation.

Verify `apps/api/scripts/verify_vhk2.py` is WRITTEN, NOT RUN, and has been added to `scripts/regression_set.txt`. It runs the static frozen-state scan (with listed invariants), runs the ten previously failing scripts in full (1800 s each, one at a time), shows the nav/server rule both ways, and runs dealroom1 `--cleanup-only`. `--self-checks-only` for all 27 regression-set scripts: see VHK2_FINDINGS "Self-checks run".

## 000000000000000000000000000000000000000000000000000000000000000000000. TILTS, COMPLETION, GIFTING, BACKTEST — rep10: phase 2 complete; verify WRITTEN, not yet run (2026-11-02)

Replication Studio sprint 10 of 15. Full method, math and open items: `docs/REPLICATION_STUDIO.md` (rep10 section).

Schema `docs/rep10_part1.sql` is APPLIED (`scripts/_apply_rep10.py --all`: SCHEMA OK, applied twice — idempotent).
Eight org tables (tilt_sets, tilt_lines, completion_settings, charitable_donees, gift_proposals, gift_lots,
hypothetical_performance_policies, backtests), each with RLS for all four operations; the factor-score store
`portfolio.security_factor_scores` (mixed scope like prices); the `factor_score` data type (csv_upload rank 1,
the not-connected vendor slot rank 2); the permission `manage_hypothetical_policy` (every org_admin; also in
`rbac.ORG_ADMIN_DEFAULT_PERMISSIONS`). No business rows: no tilt, donee, gift, backtest or policy exists.

Code: `services/tilts.py`, `completion.py`, `gifts.py` + `gift_letter.py`, `backtest.py`,
`hypothetical_policy.py`, `rep10_common.py`, `rep10_sweeps.py` (wired into `workflow_scheduler_tick.py`, never
a test org, never a backtest); routers `replication_rep10.py` (22 routes) and `tax_gifts.py` (16 routes), all in
`docs/AUTHZ1_ROUTES.json` (`--check` CURRENT). The ONE shared change: `rebalance_proposals.target_for` applies the
approved tilt set then completion after exclusions (`_rep10_adjust`); with neither in effect the target is exactly
rep5's, and a proposal's target lines gain model / tilt / completion / final only when one applies.
`generate(..., target_override=)` is additive (the gift rebuy). Screens: Replication → Tilts (with Completion),
Replication → Backtest, Household → Consolidate, Tax → Charitable gifts, Settings → Hypothetical-performance policy;
`node --test tests/replication/rep10.test.mjs` 9/9 (run in the sprint; the full suite and `npm run build` were not).

Verify `apps/api/scripts/verify_rep10.py` is WRITTEN, NOT RUN (only `--self-checks-only` / `--cleanup-only` were
run). Its regression section re-runs `verify_rep5.py` once, flat, after teardown, VERIFY_NESTED=1, 2700 s.

Findings:
- [FIND] `security_data.history` uses the FIRST ranked source with ANY point in the window. The custodian (rank 1)
  values a held position on its position dates only, so one day of custodian value shadows a full uploaded series —
  a backtest or TE for anything held would always read "not estimated". rep10 reads each source through the layer
  and takes the one with the most points (`rep10_common.longest_history`; never combined). The layer itself is
  unchanged — a later marketdata sprint may want the same rule there.
- [FIND] No non-sale removal path exists for a lot (no transfer / gift transaction type). A gift is a manual
  letter plus person-entered statuses; the lots leave the book when the custodian feed shows them gone. An open
  gift's lots are NOT reserved against a rep5 sale (open item).
- [FIND] `data_uploads.id` is not unique (system-axis copies), so `security_factor_scores.upload_id` has no FK,
  exactly like prices.
- Open for counsel / CPA (shown on the screens): the hypothetical-performance policy and who may see a backtest;
  AGI limits and carryover (estimates); itemizing; Form 8283; donee acceptance of in-kind securities; state
  deductibility; factor-score data licences.
- **UPDATE 2026-11-02 — rep10 fix 1** (first run: 142 PASS / 4 FAIL). A = VERIFY: the stale-data confirmation
  task was completed via `HT.complete_task` on a pool connection with no org RLS context. The `users` read
  matched zero rows, which raised AgentActorRefused, so nothing was confirmed. The verify now confirms through
  rep7's `POST /held-away/confirmations/{id}/confirm` as the checker and re-reads the row. completion.py was not
  changed. B = CODE: `gifts.mark_received` compared the local received date with `sent_at.date()`, which is the
  UTC day. At 21:55 ET a same-evening receipt was refused as "received before the letter was sent" (422). Fixed
  with `rep10_common.local_day`; backtest's version `approved_on` had the same bug and is fixed too. A pure verify
  check reproduces the bug, then proves the fix. C = consequence of B: `acknowledge` needs 'received' and `rebuy`
  needs received/acknowledged, so both returned 409. The earlier A_M proposals are drafts, which `generate`
  supersedes, so rep5's one-open rule was not the cause. Bodies are now printed. D = VERIFY (rep5's fixture), not
  a rep10 leak. sec_B was priced by marketdata1's custodian provider from rep5's OWN fixture: ACCT_IRA holds B,
  10 sh at mv 1000, as of seed_val → 100.00 as of 2026-11-01. That behaviour exists since 6423d80, and this was the
  first verify_rep5 run after it. rep10 did not touch the layer's get / get_bulk / history; `longest_history` is
  read only by rep10's backtest and tilts. Fix: that IRA position's market value is now unknown, and a new
  premise check asserts no account values B or C. The "unpriced → notional" assertion is unchanged. verify_rep10
  gains a "no data → not found" section, with a positive control; no missed teardown table was found. Task 4
  probe `scripts/_rep10fix1_probe.py`, 8 PASS / 0 FAIL: P1 with no source → found = false through get, get_bulk,
  history, longest_history and `_unheld_prices` ('none'). FAIL D reproduced: a 10 sh / $1000 position → custodian
  100 as of 2026-11-01 ('security_price'); invisible to P2; with mv NULL → not found. P2's synthetic csv_upload
  history (42 days) is found by P2 and P1 still finds nothing. Teardown removed 53 rows; 0 remain. Self-checks:
  rep10 5/0, rep5 5/0, rep6 7/0. verify_rep10 / verify_rep5 were NOT re-run (operator memory limit).

## 00000000000000000000000000000000000000000000000000000000000000000000. VERIFY HOUSEKEEPING — vhk1: self-checks pinned to each sprint's own merge; verify WRITTEN, not yet run (2026-11-02)

In two weeks, nine fix rounds went to checks that a LATER sprint broke: new routes, edits to an older verify script, or cleaned-up data. None of those failures meant the feature had broken. VHK1 removes that class of failure across the 24-script regression set. No application code or schema changed.

- **`apps/api/scripts/_verify_self.py`**:
  - `sprint_end_ref`: the sprint's merge on main (explicit `SPRINT_MERGES`), else HEAD.
  - `own_routes` / `still_gated`, `own_lines` / `own_diff` / `own_text`.
  - `when_empty`: a [FIND] when no real rows exist, otherwise the rule is asserted for every row.
  - `run_self_checks`.
- **Every regression-set script** has a stdlib-only VHK1 block before `hydrate()` and `--self-checks-only`. All 24 report 0 FAIL in seconds, with no database.
- **Exclusion lists removed** (no longer needed once each check ends at its own merge):
  - rep6's "rep7 superseded rep5".
  - rep7's rep7b / authority / lot-method route exclusions.
  - rep1 / rep2 / rep3 / rep5 by-path route scoping.
  - dealroom2's `DEALROOM2_REF`.
  - bugfix1's dealroom1 exclusion.
  - navregroup's `LATER_SPRINT_*` lists.
- **Two checks were already failing on main and are now pinned:**
  - bulkfetchui3's `main:` reads.
  - rep7b's lotmethod-B1 guard on verify_rep7.
- **Module-level `TODAY` / `VAL` / `YEAR` removed** from rep2, rep4 (missed by the prompt), rep5, rep6 and rep7. Expectations about the server's clock use a call-time `today()`; fixture dates use a date captured when seeding.
- **Shared cleanup.** The 13 scripts that were not on `_verify_cleanup.py` now are, each with `--cleanup-only`. Each exits 0 with LEFTOVERS 0, but each removed 0 rows: no fixture rows existed, so the new delete paths have not run against real fixture rows yet.
- **Rule** added to `docs/VERIFY_RULES.md`, with a pointer in `docs/SPRINT_WORKFLOW_STANDARD.md`.
- **Record:** `docs/VHK1_SELF_CHECKS.md` holds the Task 1 table, the end points, the outputs and the simulation.

Verify `apps/api/scripts/verify_vhk1.py` is WRITTEN, NOT RUN, and has been added to `scripts/regression_set.txt`.

---

## 00000000000000000000000000000000000000000000000000000000000000000000. BLOCK TRADING — rep9: blocks, allocation, DVP settlement, manual-first; verify WRITTEN, not yet run (2026-10-29)

The design, the allocation arithmetic, the always-manual rule, DVP, the FIX interface with its "add a
router" checklist, and the open compliance items are in `docs/REPLICATION_STUDIO.md` (rep9 section).

Schema `docs/rep9_part1.sql` is APPLIED (`scripts/_apply_rep9.py --all`: SCHEMA OK). It adds 8 tables,
each with a policy for every operation: execution_accounts, allocation_policies, trade_blocks,
allocation_corrections, block_allocations, block_fills, block_charges, block_events. It also adds the
permissions view_blocks / manage_blocks / approve_blocks, granted to every org_admin. It writes no
business rows: no block can be built until the firm writes its allocation policy and a second person
approves it.

Verify `apps/api/scripts/verify_rep9.py` is WRITTEN, NOT RUN. The sprint ran no verify script and no
`npm run build`. Results so far:
- `node --test tests/blocks/ tests/execution/ tests/nav/` passes (38/38).
- 30 new routes under `/api/v1/execution/blocks/…`, each gated and in AUTHZ1_ROUTES. 727 routes in
  total; no existing gate changed; `--check` CURRENT.
- The regression (verify_rep7b.py, once, flat, after teardown, VERIFY_NESTED=1) runs inside
  verify_rep9.

Findings:
- **FIND — fills never write `portfolio.tax_lots` for equities.** rep2 records fills (and their lots)
  on the instruction. The book's lots come from the custodian feed or the held-away import. rep9
  books each account's share as a rep2 fill at the average price, with its own lots, and does not
  add a second write path into the book.
- **FIND — verify_rep7b's route self-check counted every route added since its base.** That base was
  9ed75ae, so lotmethod, rep8, marketdata1 and rep9 all broke the check. It is now scoped to rep7b's
  own 11 routes: each must still exist with its gate. No other line of verify_rep7b changed.
- **FIND — rep2's execute-by sweep would have expired an instruction under a working block.** The sweep
  now skips any instruction held in an open block allocation; the block's own execute-by rule (in the
  cron entrypoint) applies instead. rep2's direct mark-sent / fill / cancel on such an instruction is
  refused (409), naming the block.
- **FIND — a correction cannot rewrite rep2's fills.** Fills are immutable. A correction is therefore
  carried on the block allocation (the block's book of record and its settlement) and on the
  instruction's timeline. The instruction reconciles against the corrected allocation.
- **FIND — every real custodian's fractional_shares and equity_trading_api is 'unknown'.** So every
  account is whole shares, and no automated route is ever "available" today. The verify proves
  the fractional and manual-over-automated paths on a fixture custodian (zzrep9frac) whose two
  capability rows it writes and removes.

Compliance items stay open for Joe and counsel, shown as notes and not decided:
- the written aggregation and allocation policy, and its ADV disclosure;
- the best-execution review;
- trade-away commissions;
- receiving-custodian DVP agreements;
- whether Altruist accepts DVP deliveries in.

Operator: run `verify_rep9.py` (it runs the build and the rep7b regression).

UPDATE 2026-11-02 (rep9.fix1): the first run (2026-10-29) gave 150 PASS / 8 FAIL. Two FAILs were code defects, now fixed. (1) The FIX refusal did not say "not connected", the spec's word: `execution_routers.NOT_CONNECTED_REASON`. (2) The cross-trade check ran after every other refusal, so a below-minimum or ineligible pick hid an opposite-side block in the same window. `blocks._plan` now returns a `basis`; `build_block` and `preview` check for crosses first and report them next to any refusal. Five FAILs were verify defects, now fixed. (3) The regex-and-zlib PDF reader matched zero streams (reportlab writes ASCII85 + Flate); it now uses pypdf, same assertions. (4) The adapter calls came from rep2's `POST /execution/instructions` during fixture setup. The spy now records setup apart and checks it is live; no block path reached an adapter. (5) The cross-trade sell fixture was 3, below the minimum of 5; it is now 8. A 3-share sell is kept to show both reasons appear together. (6) The execute-by "None" came from the block being refused (`m_A1` was 3, below the minimum), not from the sweep, which keeps the row. `m_A1` is now 6. The rolled-up check passes once (1), (3) and (4) do. (7) verify_rep7b had three stale checks: rep8's `options` path is added to the decided matrix (managed × integrated / other_custodian only), and zero existing accounts or held-away drafts now report a [FIND]. verify_rep9's exact-diff list for verify_rep7b is extended to match. Verify re-run NOT done (WSL memory). Operator: re-run `verify_rep9.py`.

## 0000000000000000000000000000000000000000000000000000000000000000000. SECURITY DATA LAYER — marketdata1: vendor-neutral prices / corporate actions / holdings / index levels / option quotes; verify WRITTEN, not yet run (2026-10-28)

Design, ranks, the licence rule, quality rules and the 5-step "add a vendor" checklist are in
`docs/SECURITY_DATA_DESIGN.md`. Which "needs prices" lines now fill is in `docs/REPLICATION_STUDIO.md`
(MARKETDATA1 section).

Schema `docs/marketdata1_part1.sql` is APPLIED (`scripts/_apply_marketdata1.py --all`: SCHEMA OK). It
adds 8 tables, each with a policy for every operation. It adds provider and licence columns to the
price, corporate-action and holdings-snapshot stores, and org-scopes an org's uploaded rows. It
writes no org rows: only 8 platform providers and the platform rank.

Verify `apps/api/scripts/verify_marketdata1.py` is WRITTEN, NOT RUN. The sprint ran no verify script
and no `npm run build`. `node --test tests/securitydata/` passes (5/5), and the nav / bugfix1 tests
still pass (30/30). There are 13 new routes, each gated (Super Admin, or view_replication |
manage_org_settings to read and manage_org_settings to write) and listed in AUTHZ1_ROUTES; 688 →
701 routes, no existing gate changed, `--check` CURRENT.

- **FIND — rep4 / A1 prices had no provider.** `securities_global.add_price` and rep4's verify write
  prices with no provider_key. Those rows are now the `platform_entry` provider (rank 4), so they
  stay readable through the rank. `add_price` stamps it.
- **FIND — the UX3 securities grid is a master-data view, not a "needs prices" reader.** It keeps
  reading the platform store, and now with `org_id IS NULL`, so an org's uploaded file never shows
  as "platform-sourced". Routing it through the rank would have changed what UX3 means.
- **FIND — fund holdings from a file are NOT accepted.** rep1's holdings store is platform-only
  (`replica_holdings_snapshots_platform_chk`), so an org's file would publish to every org. Deferred.
- **FIND — corporate actions reuse Portfolio F's table.** It was not duplicated. Dividends are
  recorded there; `apply_corporate_action` still refuses them by name.
- **FIND — X-ray's tracking error, harvest value and after-tax performance need a price HISTORY.**
  No free source has one, so they keep "not estimated — needs prices" and name `eod_price history`.
- **FIND — the nightly's open-proposal feed is injected in the verify.** Seeding a real proposal
  needs rep5's whole path. The real feed query is proven to run.
- Indicator nightly / store / read gate: byte-identical to the base (afdbc6d).
- Operator: run the verify (it runs `node --test`, `npm run build` and the `verify_rep6.py`
  regression). [SUPERSEDED by SECMASTER1: it runs as the `system:Security data — nightly` workflow job — no Render cron.]
  Create a Render cron for `python scripts/security_data_nightly.py` with its OWN
  Doppler sync (DATABASE_URL, FRED_API_KEY) — it is not scheduled yet. Refresh the schema snapshot.

---

## 000000000000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — rep8 (sprint 8 of 15, phase 2 starts): the options sleeve — buffer and income ETFs as core + options package, option contracts in the security master, adviser quotes, the payoff lab, the options TAX GATE, options proposals → manual per-leg instructions, fills → option lots, rolls and expiry, minimum account; verify WRITTEN, not yet run (2026-10-28)

Design, payoff math, tax-gate rules and findings 1a–1e: `docs/REPLICATION_STUDIO.md` (rep8 section).
Schema `docs/rep8_part1.sql` — APPLIED (`scripts/_apply_rep8.py --all`: SCHEMA OK). It adds 1 platform
table and 5 org tables, each with a policy for every operation, plus the `option` security type,
two instrument classes, 15 class-scoped terms fields, and `option_leg` / `option_terms` columns
with their CHECKs. It writes no org rows.
Verify `apps/api/scripts/verify_rep8.py` — WRITTEN, NOT RUN. The sprint ran no verify script and
no `npm run build`. `node --test tests/` passes (416 pass, 0 fail, 1 pre-existing skip), with 7
new rep8 render tests. The 17 new routes are all under `/api/v1/replication/options/`, each gated
and listed in AUTHZ1_ROUTES. No existing gate changed (671 → 688 routes).

- **Phase-2 gates still open.** Altruist's options level and FLEX access are 'unknown', so every
  options trade is a manual instruction. There is no options data source and no price vendor, so
  premiums, levels and implied vols come only from adviser quotes. Counsel has not yet ruled on
  whether index options against a stock basket are offsetting positions under 26 U.S.C. 1092, so
  those results are labelled "pending counsel review".
- **Operator.** Run `verify_rep8.py`. It runs node tests, `npm run build`, and the `verify_rep6.py`
  regression (VERIFY_NESTED=1, 2700 s). Then redeploy the API and the scheduler cron, because
  rep5's `org_tick` now runs the options roll / expiry sweep. Then refresh the schema snapshot.
- **Optional org settings:** `replication.options.hedge_ratio_tolerance` (0.05),
  `.roll_notice_days` (10), `.payoff_step_pct` (0.05) and `.constructive_sale_band` (0.15).

## 00000000000000000000000000000000000000000000000000000000000000000. DEALROOM3 — Marketplace Deal Room Phase 3: watermarked downloads and a view-only page viewer, access expiry + pause on lapsed eligibility, the per-investor activity log (CSV), Q&A, investor advisors, Phase 3 notifications, the return-or-destroy notice, DRAFT 506(b) / 506(c) rules; verify WRITTEN, not yet run (2026-10-28)

**UPDATE 2026-10-28 (dealroom3.fix1):** first run was 221 PASS / 4 FAIL. The publisher's `PUT …/share-unwatermarked` 500'd on every call: `CASE WHEN $3 THEN $4 END` left `$4` an untyped parameter inside CASE, which Postgres resolves to `text`, so the UPDATE failed at PREPARE with `DatatypeMismatchError: column "share_unwatermarked_by" is of type uuid but expression is of type text` (data-independent — the route could never succeed). Fixed with `$4::uuid`; the "investor receives it as is" FAIL was only that cascade (the download policy reads the live column). Also: verify `y_pause` wrapped the paused-reason check in `bool(...)` (it stopped the section early, so the restore checks after it have not run yet), and the 1b discovery reason was re-wrapped onto one line. Verify NOT re-run (WSL memory).

**Run:** `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_dealroom3.py 2>&1 | grep -E "^\[FAIL\]|^\[FIND\]|^TOTAL"`
(`--cleanup-only` for teardown alone). The script runs node tests, `npm run build` and, flat after teardown, the
regression `verify_dealroom2.py` (VERIFY_NESTED=1, 2700 s). The sprint ran neither the verify script nor the build.

- **Design:** `docs/DEAL_ROOM.md` (Phase 3 section). **Discovery:** `docs/DEALROOM3_DISCOVERY.md` (1a–1e).
- **Applied live (2026-10-28):** `docs/dealroom3_part1.sql` via `apps/api/scripts/_apply_dealroom3.py --schema` on the
  operator DSN. The landing check (`--check`) reports 0 FAIL. Existing deals: all 9 live rooms keep downloads allowed /
  watermark off (today's signed-URL download), no access period, offering not_set. 0 access rows existed. New rooms
  default to watermark ON.
- **Code:** `services/deal_room_control.py` (policy, watermark register, activity, sweep, Q&A, advisors, notices);
  `services/deal_room_watermark.py` (pypdf + reportlab stamp, pypdfium2 pages); 19 new `/deal-room` routes (AUTHZ1
  `--check` CURRENT, no existing gate changed). Scheduler: `access_tick` in `workflow_scheduler_tick.py` (skips
  is_test orgs). `PUT /entities/{id}/compliance` now re-checks deal-room access in the same transaction and accepts
  `accreditation_verified_date` / `next_reverification_due` (additive).
- **[FIND] constraint names kept:** `deal_access_status_v2_check` / `deal_access_prior_level_check` keep their names
  with widened bodies, because verify_dealroom2 reads them by name.
- **[FIND] verify_dealroom2 changes:**
  - Its teardown moved onto `_verify_cleanup.py`. The FK-walk `fk_children` / `delete_tree` / `purge_privileged`
    (old lines 230–329) were replaced by `operator_conn` + `teardown` (new lines 233–271), plus the import and the
    docstring.
  - Its `y_routes` "14 new routes" check was ALREADY failing on main: 168 routes are new since its base (rep1–7).
    It is now scoped to routes present at its own commit (`DEALROOM2_REF = 994d8ce`). That is the one assertion-code
    change.
- **[FIND] 506(b):** the CRM had no relationship date. The new `crm_relationship_dates` table is the input; when no
  date is recorded, the rule falls back to the entity's `created_at`. The rule is DRAFT until counsel confirms it.
- **Known limits:**
  - A pending (unacknowledged) advisor could still find PUBLIC-folder file names through Document Search.
  - Agreement page images and signed copies are not watermarked. They are the signed record.
  - Office files cannot be watermarked (no pure-Python path): refused unless shared unwatermarked.
- **Operator next:** run verify_dealroom3; refresh `schema_snapshot.sql`; counsel review of the DRAFT 506 rules.

## 0000000000000000000000000000000000000000000000000000000000000000. REPLICATION STUDIO — lotmethod: the lot-relief method (household default, account override, per-proposal override; maker-checker), tax-optimized by estimated tax per dollar raised, custodian method + average cost; BUGFIX2 (fast scoped verify cleanup, B2–B6); verify WRITTEN, not yet run (2026-10-28)

Design: `docs/REPLICATION_STUDIO.md` (lotmethod section). Schema `docs/lotmethod_part1.sql` —
APPLIED (`scripts/_apply_lotmethod.py --all`: SCHEMA OK — 2 new tables, 27 constraints, 6 partial
uniques, RLS NULLIF policies for all four operations, guards, grants; proposals / trades method
columns; `deals.version_of_deal_id` backfilled on 13 history rows, 6 left NULL as ambiguous).
The migration writes no settings row: an account with no method keeps today's rule exactly.
Verify `apps/api/scripts/verify_lotmethod.py` — WRITTEN, NOT RUN (the sprint runs no verify script
and no `npm run build`). Run during the sprint: `authz1_routes.py` (9 new routes under
`/lot-method/`, every one gated on rep5's proposal permissions, no existing gate changed) and
`node --test tests/` (0 fail).

- **Findings.** 1a: `plan_sales` is the one sale path (engine build → every model proposal,
  one-off, transition; harvest's swap-back); harvest loss lines and the X-ray choose no lots.
  1b: maker-checker (same class as the gain budget). 1c: no mutual-fund type / cost-basis marker
  in the security master → average cost is an account election. 1d: the slow cleanup was
  `delete_tree` on users / roles / organizations walking every FK child with nested IN-subqueries.
  1e / B3: ensure_org_admin_role already grants everything every real org_admin holds (rep2→rep7b
  extended it); `review_agent_proposals` is held by one real org only and is not granted.
- **[FIND] default conflict.** "tax_optimized (default)" with a profile cannot equal today's
  rule; no method set = today's rule; the profile ranking applies once a method is set.
- **[FIND] verify_rep6 y_routes already failed on main** (41 new routes, not 31 — rep7's
  reconcile route and rep7b's routes were never excluded); scoped now to rep6's own routes.
  verify_rep6's verify_rep5 diff check and verify_rep7b's verify_rep7 diff check are now
  two-stage: the original rule up to the lotmethod base, then "only BUGFIX2 cleanup changes"
  (`_verify_cleanup.b1_diff_bad`). verify_rep7 / rep7b route counts exclude `/lot-method/`.
- **BUGFIX2 — B1–B6 DONE.** B1: `scripts/_verify_cleanup.py` (seeds by fixture ids, FK closure
  only into tables that hold rows, materialised `= ANY` arrays, refusal outside the fixture orgs,
  explicit cycle order) used by verify_rep2 / rep5 / rep6 / rep7 / rep7b, each with
  `--cleanup-only` printing its time. B2: verify_rep5's nested timeout 2700 s. B3: proven (see
  1e). B4: `lib/authHeaders.mjs` — an expired session redirects to `/auth/login` (no `returnTo`:
  the helper does not know the page) instead of an unauthenticated request; node test. B5:
  verify_workflowmgr5 asserts workflowpermsfix's grants; verify_underlyingresolution's scan
  excludes `apps/api/scripts` and prints TOTAL. B6: deal history rows carry `version_of_deal_id`;
  un-archive matches on it (old match only for un-tagged pre-B6 copies, logged).
- **Operator.** Run `verify_lotmethod.py` (it runs the five `--cleanup-only`s, node tests,
  `npm run build`, and `verify_rep6.py` once with VERIFY_NESTED=1). The claude/BUGFIX1_BACKLOG.md
  file named by the sprint prompt is not in the repo; B1–B6 are recorded done here.

UPDATE 2026-10-28 (lotmethod fix 1, after verify_lotmethod ran 139 PASS / 1 FAIL): NO code defect — the one FAIL was the B6 verify fixture. Its two deals were inserted with only name + creator, so `apply_status(..., 'submitted')` raised the lifecycle's own 422 (missing description, asset class, target raise, minimum investment — `marketplace.submit_required_fields`) before the un-archive scenario ran. The fixture deals now carry a description, `target_raise` 1000000, `minimum_investment` 25000 and an `asset_super_class` read live from the `asset_taxonomy` config (`taxonomy_sc_N`, ORG_A's own catalog first, else 2nd Act's — the column has no CHECK / FK); a new check proves `missing_for_submit` returns nothing for them before the stage moves. The lifecycle rule is unchanged; teardown is unchanged (deals are removed with the fixture orgs). Re-run `verify_lotmethod.py` pending (operator).
UPDATE 2026-10-28 (lotmethod fix 2, after the re-run went 142 PASS / 1 FAIL): a real CODE defect — the B6 un-archive restored deal one (draft → submitted → archived) to 'draft'. Not a missing or stale version: a one-off probe on is_test fixture rows showed deal one's closed versions were correct ('draft' and 'submitted', both carrying `version_of_deal_id`), but every `system_from` / `system_to` was the SAME instant — `archive_version` stamped `system_to = now()`, which is TRANSACTION time, and the RLS pool's `acquire()` opens one outer transaction so each step's `transaction()` is only a savepoint. `prior_status`'s `ORDER BY system_to DESC LIMIT 1` was a tie, broken arbitrarily. Fix (`services/deal_lifecycle.py`): `apply_status` takes ONE `clock_timestamp()` and uses it for both the closed version's `system_to` (new optional `at` argument on `archive_version`; other callers keep `now()`) and the live row's `system_from` — versions stay contiguous and strictly ordered; `prior_status` adds `system_from DESC` as a tiebreak; the pre-column fallback is unchanged. The probe after the fix: prior / un-archive = 'submitted' for deal one, 'draft' for deal two; zero probe rows remain. Re-run `verify_lotmethod.py` pending (operator).

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
