# Hollisworks / 2nd Act — Outstanding To-Do List

**Version 2 — 2026-09-30.** v1 was the first 2026-09-30 reconciliation; this adds the Altruist access request and the identity-decoupling principle. Both supersede the unnumbered 2026-09-05 list.

Cross-checked against: the previous version of this list,
`docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md` (2026-09-12), the roadmap,
`docs/HOLLIS_DISCOVERY.md`, `docs/WORKFLOW_WAVE2_DISCOVERY.md`, the sprint
results from 2026-09-14 through 2026-09-30, direct queries against the live
database on 2026-09-30, and a second, independent review of
`HOLLIS_DISCOVERY.md` done in a chat outside this project. That review's
claims are recorded below as confirmed, consistent, or unverified — never
assumed.

Legend: ✅ done · 🔍 needs verification · 🔴 must fix before members use it ·
⏸️ blocked on something external · 🧭 decision needed · ⬜ not started

---

## 0 · Verify first

Claims or states not yet confirmed. Each one changes what the rest of this
list means.

- 🔍 **An unreviewed fix is sitting in the working tree.** A chat outside this
  project reports it wrote the member-scoping fix directly into the repo: a
  new `services/assistant_actions/_visibility.py`, a caller gate on the
  `entity_id` of eight handlers (three of them write paths),
  `find_my_investment` scoped to `mi.user_id` with real column names,
  `is_staff` threaded into confirm handlers, `PermissionError` mapped to 403,
  plus `scripts/verify_hollisfix.py` and `docs/HOLLIS_FIX.md`. **Its verify
  script has never run** (that chat had no Doppler access), and it was written
  without this project's `CLAUDE.md` or sprint standard in context. Before
  trusting it: confirm the files exist and which branch they are on (likely
  uncommitted on `main`); move them to a branch; check the verify script
  against the rules that broke earlier scripts — `check(passed, label, detail)`
  order with an `isinstance(passed, bool)` guard, no fixture value derived from
  a UUID prefix slice, RLS context set on every read, `audit_log` cleared
  before fixture users; then run it.
- 🔍 **`is_staff` may return True when a token has no roles claim.** Reported
  by the second review, not yet confirmed. If true, any caller whose token
  lacks a roles claim takes the staff branch everywhere — and staff visibility
  is not enforced yet (blocked on the `staff_assignments` backfill, §4), so a
  rolesless caller would see everything. **This would undercut the entire
  member-scoping fix.** Confirm by reading `is_staff` and by inspecting what a
  real member token actually carries.
- 🔍 **`POST /assistant/confirm` may take `action_key` and `params` straight
  from the client request body.** Reported by the second review; consistent
  with `HOLLIS_DISCOVERY.md`, which shows the proposed action rendered to the
  client and then confirmed. If true, the LLM is not in the confirm path at
  all: a caller can hand-craft a request to any write action they hold the
  permission for, with any parameters, and `required_permission` is the only
  gate. That is what made `crm.draft_note` (`required_permission=None`)
  arbitrary text onto any entity. Confirm by reading `ConfirmBody` in
  `routers/assistant.py`.
- 🔍 **`spv.subscribe` may retire another member's real commitment.** The
  close-then-insert behavior is confirmed by the discovery doc (`spv.py:149-184`
  closes the prior active subscription for the `spv_id` + `entity_id` before
  inserting). What is unconfirmed is whether `entity_id` is scoped to the
  caller. If not, a subscribe against someone else's entity silently closes
  their commitment. **Checked 2026-09-30: `assistant_activities` has zero rows
  — no write has ever been confirmed through the assistant — so this has never
  happened.** It must close before any member uses the assistant.
- 🔍 **Scheduler first fire.** The only scheduled trigger had never fired: a
  stale `held` run from the 2026-08-26 test fixtures blocked it through overlap
  protection. That run was closed 2026-09-30. Check at 09:00 UTC 2026-10-01
  whether `occurrence_count` moved off 0. If it did, production scheduling is
  proven end to end.
- 🔍 **fee40 chat and fee41 polish** were blocked on LiteLLM having zero
  models. Models are now registered; nobody has confirmed they actually serve.
- 🔍 **Textract and EDGAR** untested since the credential rotation.
- 🔍 **SPX live-market-hours check** was deployed but never verified against
  real SPX — the whole point of that sprint.
- 🔍 **Voyage is on the free tier** (3 requests/minute, 10K tokens/minute).
  Fine for tests; will throttle production embedding immediately.

---

## 1 · Before any member uses the assistant 🔴

Context: only the two super-admin accounts have ever used the assistant (last
2026-08-01), and no write has ever been confirmed through it. These are
pre-launch gaps, not incidents — but each must close before a member logs in.
The assistant is mounted on member pages today.

- 🔴 **Cross-member read exposure.** `portfolio.show_allocation`,
  `portfolio.find_my_investment` and `entity_graph.show_hierarchy` scope only
  by org, with `required_permission=None`, and the member's own message
  controls the `entity_id`. Proven live: all 34 entities in 2nd Act are
  reachable, including a real individual client. Confirmed independently by
  the second review. The correct visibility engine already exists and is used
  by `entities.count` / `investments.count`. A fix reportedly exists (§0).
- 🔴 **`find_my_investment` schema drift — fix only together with scoping.**
  It selects columns that do not exist (`status`, `current_stage`,
  `committed_amount`, `currency`); the real ones are `investment_stage`,
  `amount_committed`, `amount_funded`, `user_id`. It has failed on every call
  since it shipped, and that breakage is the only thing stopping it leaking.
  Fixing the columns alone would turn it into a live leak.
- 🔴 **`crm.draft_note` writes to any entity.** No permission, no ownership
  check on `entity_id`.
- 🔴 **`delegate_grants` has 0 rows for org 1.** Under the correct engine every
  member's visible set is empty, so the scoping fix will show members nothing.
  That is the engine telling the truth. The fix is to backfill grants — not to
  loosen the gate.
- 🔴 **Fix alongside: `allocation_lens` double-counts** against look-through
  buckets in its subtree selector — the same code path as `show_allocation`.
- 🔴 **No idempotency on confirm.** A retried `POST /assistant/confirm`
  re-runs the handler: `spv.record_transaction` inserts a second transaction
  and runs allocation twice. The roadmap's agent harness already specified
  idempotency keys; the confirm path never got them. Staff-only, but it
  corrupts financial records.
- 🔴 **Hollis does not exist as an agent.** One generic loop and registry serve
  members and staff alike: no allowlist, no `agent_key`, and the assistant
  never reads `tier`. Needs a real tool allowlist; `tier` honoured on the
  confirm path; step-cap exits recorded with an `escalation_reason` (today a
  cap-out is indistinguishable from a normal finish); a tool-level decision log
  (tool, arguments, result, `user_id` — `ai_decision_log` records model and
  cost only); and an eval gate (none exists for the assistant).
- ⬜ Stale comment at `main.py:399-401` says RLS is inert in production. It is
  not — the connection is `app_service`, `rolbypassrls = false`.

---

## 2 · Decisions only Joe can make 🧭

- 🧭 **Should one held run stop a schedule forever?** Overlap protection treats
  any unfinished run as blocking, so one failed step freezes a daily job
  indefinitely. `awaiting_approval` runs block the same way (probably correct
  there).
- 🧭 **`spv.subscribe`: one-tap member action, or `propose()` via the
  advisor?** A member can commit capital with a single tap today. It is their
  own money, but Hollis-as-designed never executes writes.
- 🧭 **Should confirm trust a client-supplied action?** (if §0 confirms it).
  Either store the proposed action server-side and confirm by id, or accept
  that the permission check is the only gate and harden every write's
  `required_permission`.
- 🧭 **Super-admin excluded from the self-approval emptiness gate.**
  `has_other_eligible_checker` ignores super-admins while
  `is_eligible_reviewer` counts them. Defensible and documented, but the two
  functions disagree about who is a reviewer.
- 🧭 **`has_permission` default-allows a user with zero roles.** A deliberate
  bootstrap posture; it means any account created without a role holds every
  permission.
- 🧭 **Does workflow-instance execution apply to interactive Hollis?** The
  agent contract requires it; a chat turn does not obviously fit.
- 🧭 **Re-test the seven-agent split on tool allowlist alone.** Portfolio &
  Suitability vs Deal & SPV is the pair least likely to survive.
- 🧭 Router-level super-admin fix — three options (issuer check / org
  predicate / host separation), deliberately not chosen.
- 🧭 GP legal-entity model (enum value exists, zero rows; carry books as a
  payable).
- 🧭 Membership threshold.

---

## 3 · Blocked on something external ⏸️

- ⏸️ **Altruist — dev-environment access requested 2026-09-30, awaiting
  reply.** Six merged sprints and the entire cash module wait on it. When they
  reply, confirm the exchange covers the rest of the Sprint 0 asks: the real
  OAuth redirect URI, webhook signature docs, and the business inputs (entity
  choice, legal address, contact, launch date, AUM projection).
- ⏸️ All Altruist product claims (cash rate, brokered CDs, cash spread for ADV
  Item 5) are unverified.
- ⏸️ AWS SES: grant `ses:SendEmail` + `ses:SendRawEmail`, check sandbox status,
  set `SES_FROM_EMAIL` in Doppler, then re-run the `smtpservice` verify.
- ⏸️ Member Business Registration / EIN capture — waiting on the carrier.
- ⏸️ SAML federation — deliberately paused (Auth0 broker connection, org-picker
  UI, client IdP linking screen).
- ⏸️ Counsel: securities (member equity in Access), ERISA (DOL / MEWA),
  Marketing Rule review for structured notes.

---

## 4 · Infrastructure and security debt ⬜

- ⬜ **No staging environment.** Sprints test against the live database; a
  teardown bug has already corrupted real rows once.
- ⬜ **Decouple user identity from authentication (decided principle).** A
  user of any type is keyed by an internal random identifier; login IDs and
  emails map *to* it and are never the key. A person must be able to change
  email, add a second login, or switch identity provider without
  re-enrolling. Checked 2026-09-30: the foundation already complies —
  `users.id` is a random UUID and all 106 foreign-key columns reference it.
  What violates the principle: a single `users.auth0_sub` column (UNIQUE on
  the bare subject, with no record of which of the two Auth0 tenants issued
  it), and UNIQUE `users.email`, which makes email behave like identity. Live
  example: `jlarizza@gmail.com` and `jlarizza@culmina.io` are two user records
  for one person. Plan (additive): a `user_identities` table — user_id,
  issuer/tenant, subject, email at login, verified flag, linked/last-login
  times, UNIQUE (issuer, subject); login resolves through it; backfill from
  `auth0_sub`, then retire the column; email becomes an editable contact
  field. **Never auto-link identities by email match** — that is an
  account-takeover path; linking requires an existing signed-in session or an
  admin action. Open decision: whether to merge the two `jlarizza` records
  into one user with two identities.
- ⬜ **`users.role` has no CHECK constraint and no demotion path.**
- ⬜ **The `super_admin` role has 22 permissions and zero holders.** Both real
  super-admins work purely through the `users.role` string and the bypass.
- ⬜ **`staff_assignments` backfill** — blocks turning on staff-visibility
  enforcement. Directly relevant to §0's `is_staff` question.
- ⬜ **Hollisworks org has zero `manage_org_settings` holders**, so its alerts
  land in `audit_log` instead of reaching a person.
- ⬜ **Every reviewer role except `org_admin` has zero holders**, so
  maker-checker is recording that it cannot operate rather than operating.
  Staffing, not code.
- ⬜ `services/permissions.py` (marketplace / SPV / VDR) never checked for a
  super-admin bypass gap.
- ⬜ Eight hardcoded `"2ndactcapital-docs"` R2 bucket fallbacks across four
  routers.
- ⬜ Doppler → Vercel sync disabled (needs Supabase-owned variables excluded).
- ⬜ `doppler.yaml`'s `development` config is empty; local dev points at `prd`.
- ⬜ Move `DISABLE_SCHEMA_UPDATE=true` into the `prd_lite_llm` Doppler branch.
- ⬜ **`render.yaml` does not describe reality.** It declares a web service
  that lives on Vercel and omits the LiteLLM service. Nothing reads it — which
  is how the workflow scheduler sat uncreated, and never fired, without anyone
  noticing. Correct it or mark it plainly as non-authoritative.
- ⬜ `2ndactcapital-api`'s real public hostname never confirmed.
- ⬜ Log SpiffWorkflow's LGPLv3 license for vendor / SOC review.
- ✅ **`security_invoker` audit — closed 2026-09-30.** Every app view in every
  schema is compliant. The only views without the flag are Supabase system
  views and LiteLLM's own views, which hold no app data. What remains is the
  standing rule in `CLAUDE.md` (§6).

---

## 5 · Data integrity ⬜

- ⬜ `spvs.deal_id` is NOT NULL but has no FK; `spvs.master_entity_id` has no
  FK; `spvs.spv_status` is free text with no CHECK.
- ⬜ `entity_ownership` vs `entity_relationships` — likely duplicates; both
  still hold rows (2 each, 2026-09-30). Bears on the open
  `POST /entities/{id}/ownership` bug.
- ⬜ `reference_data.org_id` unused; seeding an org override without fixing the
  read path would return duplicates.
- ⬜ Five parallel registries (`config`, `reference_data`, `udf_definitions`,
  `investment_profile_questions`, `note_terms_field_registry`).
- ⬜ No per-org entitlement mechanism (`features.*` proposed, never built).
- ⬜ `v_capital_accounts` structurally broken on `dim_member_series_id`.

---

## 6 · Sprint standard and doc hygiene ⬜

- ⬜ **Add to the sprint standard: never derive any unique column from a slice
  of a fixture UUID.** Every fixture UUID starts `99000000`; prefix slices
  collided three times (emails twice, org slugs once).
- ⬜ **Add: every `check()` helper asserts `isinstance(passed, bool)`.** A
  reversed signature once made every assertion in a verify script report PASS.
- ⬜ Add a standing `security_invoker = true` rule for every view to
  `CLAUDE.md`.
- ⬜ Add a `CLAUDE.md` caveat: `schema_snapshot.sql` cannot represent CHECK
  constraints, RLS policies or seed data.
- ⬜ Fix stale docs: the D2/E spec's status says three-state availability is
  unbuilt (it shipped); the project overview says LiteLLM has zero models;
  `CROSS_PROJECT_STATUS_CONSOLIDATED.md` predates everything after 2026-09-12.
- ✅ `audit_log`-before-users teardown rule — added by the selfapproval sprint.

---

## 7 · Discovery sprints worth running before building

Wave 2 and Hollis both turned out to exist, in large part, before anyone set
out to build them. Check first.

- ⬜ **S27 TaskRouter** may be mostly built already: the fallback chain, the
  per-call decision log, and model / cost / latency tracking all landed through
  the LiteLLM phases.
- ⬜ Does TA Model's read-time obligation ledger resolve the cash module's
  flagged overlap with SPV commitment tracking? Gates `cash00`.
- ⬜ Do capital calls and distributions post real journal lines? Gates the
  `v_capital_accounts` fix.
- ⬜ `investment_profile_questions` vs `udf_definitions` — is one retirable?
- ⬜ **Ownership tree graph** — the roadmap calls it remaining work; the
  2026-09-12 status says both sprints are done. Confirm.
- ✅ **Suitability tracker — confirmed not built** (2026-09-30: no suitability
  or risk-profile tables). Investment-profile capture does exist (20
  questions, 10 answers stored); the roadmap's IPS-generation item builds on it.

---

## 8 · Build backlog ⬜

**LiteLLM** — Phase H (reporting), I (model recommender), J (voice),
guardrails proper.

**Workflow** — NL-to-workflow-template library (the last Wave 2 item).
Scheduler: consecutive-skip alert (needs a tick-outcome column), chaining /
entity-scoped sub-schedules, natural-language schedule authoring.

**Agentic layer** — map verbs to the seven allowlists; Desks table and
task → desk → agent resolution; Compliance Analyst with no disposition field
and no self-surveillance; progressive context manifest; three-depth review UI;
a single shared `entity_context` assembler; four guardrail layers plus input
isolation and no self-escalation; acceptance-rate metric and shadow mode
before Tier 1. The "AI sidebar aggregate queries" backlog (SPVs, workflow
runs, deals by attribute, member investments, documents, notifications) is
future Hollis read verbs — each must be member-scoped from day one.

**Cash module** — acct00–acct02, cash00–cash12, after Altruist read-only
integration and the §7 obligation-ledger discovery.

**Fee / SPV / GL** — two-hop pro-rata allocation; WHOLE_FUND carry basis;
time-weighted preferred return; partial advisory-fee offset; illiquid-asset
tax tiering; 1065 / K-1 with a §704(b) capital-account layer.

**Structured notes** — comparability taxonomy and percentile scoring;
worst-of Monte Carlo (report a range, never a point estimate); base rates;
lifecycle monitoring (now genuinely unblocked, since the scheduler actually
runs); R2 old-bucket deletion.

**Portfolio / CRM** — corporate actions for merger, tender and delisting;
UDF dependent picklists; UDF tab-rename audit history; admin menu
rationalization; gridux_c.

**Tier 2 and 3** — Deal Diligence AI generation layer; Pipeline A
(member-acquisition funnel); S26 Chancery (source-coordinate tracing currently
degrades to page reference).

**Cost levers** — Batch API for the EDGAR corpus and the DeepEval run; prompt
caching for Chancery extraction; a reconciliation check of Anthropic billing
against gateway logs.

**Later tiers** — revenue / profitability module; correspondence tracking;
voice onboarding and meeting capture; MCP connector registry; retention policy
(unblocked since S23 landed); securities-based lending; embedded video with
AI-suggested questions; adviser mobile app; health-insurance member benefit;
IPS generation; reporting-gap review against what Altruist provides; the
product-philosophy document.

**Taxonomy** — add Volatility Strategies under Super-Class VII, tracking long
vol and return-seeking vol separately.

---

## 9 · Closed since the 2026-09-05 list ✅

- LiteLLM Phases C (embeddings), D1a/b/c (bring-your-own-key, routing, spend
  attribution, loud credential failure), D2 (catalog and org picker), E
  (per-task model and effort), F (force-Anthropic bypass), G (budgets),
  three-state model availability, and the seeded-chain naming fix. Models
  registered: `claude-sonnet`, `claude-haiku`, `voyage-3.5`.
- `PROXY_ADMIN` bug resolved via the `prd_lite_llm` Doppler branch.
- RLS enforcement cutover live (`app_service`, `rolbypassrls = false`).
- Workflow scheduler and AI spend sync created as real Render cron services.
- `org_admin` is a real role resolved by permission, with write paths kept in
  lockstep.
- Agentic substrate: `agent_proposals`, computed maker-checker with a database
  CHECK constraint, disclosed self-approval gated on genuine emptiness,
  `propose()`, action tiers, `escalation_reason`. Capability annotation
  dissolved (`required_permission` is the vocabulary at 17 verbs);
  `review_role` replaced by the computed rule; `compliance_sr` /
  `compliance_jr` merged.
- Action registry cleanup: `spv.subscribe` gated on `indicate_interest`,
  `default_autonomy` dropped, role-string permission fixed, modules collapsed.
- Wave 2 confirmed already built (workflowmgr1–5); Script Tasks refused at
  validation and parse; Tier-1 workflow steps suspend for approval, with
  effective tier = most-restrictive-wins.
- Test fixtures deactivated; `jlarizza@gmail.com` backfilled from a
  placeholder email; `crm.draft_note` `reversible` reverted to false (its undo
  path is hollow).
- Sprint standard: the operator runs the verify, connection-drop recovery,
  the no-background-wait rule, `audit_log` teardown ordering.

## 10 · Permanent boundaries — not gaps

- The custody cliff: trade execution, money movement, filing submission and GL
  posting never get an agent.
- `ConfidenceTier.PEER_CALIBRATED` cannot be reached; there is no peer-fund
  data. Left unfaked on purpose.
- The bpmn-js watermark is accepted until customer-facing launch.
