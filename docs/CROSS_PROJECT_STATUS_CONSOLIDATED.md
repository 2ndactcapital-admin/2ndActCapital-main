# Cross-Project Status — Consolidated from All Threads

**Source**: 18 thread summaries (`FSI_Summary_sept12.docx`), reconciled against live-database discovery and this session's own work. **Reconciled**: 2026-09-12.

**How to read this**: several findings in the source summaries were *already resolved* by work done after they were written. Those are marked **[RESOLVED THIS SESSION]** rather than deleted, so the record shows what was found and what happened to it.

---

## 1 · The universal finding — every thread independently flagged it

**Twelve of eighteen threads independently reported the same problem**: two divergent copies of `SPRINT_WORKFLOW_STANDARD.md` live in Project context simultaneously, with the `claude/` copy a stale ancestor missing the heredoc convention, the Part-1-before-handover rule, the eight known gotchas, the DB-contamination recovery procedure, the Task-N regression check, and the FK teardown ordering rule.

**[RESOLVED THIS SESSION]** — root copy confirmed canonical, `sprint_prompts/` copy replaced with a pointer. **A related error was also found and corrected**: an earlier attempt in this session to "write a canonical version" had actually *replaced* the full document with a thinner reconstruction, silently dropping real content. The complete version has been restored.

**Also flagged repeatedly**: `litellm_diagnose.py` sitting as ambient Project context with no tracked home. **[RESOLVED THIS SESSION]** — removed from Context, kept in repo history.

---

## 2 · Shipped and merged

| Module | Scope | Verification |
|---|---|---|
| **Fee/Billing** | 14 sprints (fee31–fee43 + event-emission). Accounts/balances/flows, schedule catalog, calc engine, run lifecycle with maker-checker, cost model, Altruist One evaluator, profitability views, fee chat, narrative generation, SPV fee terms, carry waterfall, GL posting + invoices/receipts | All merged, re-verified live post-merge |
| **CRM UDF** | 6 sprints (udf00–udf02b). EAV storage, 15 data types, three-state FLS, tab layouts, bi-temporal history (17a-4), tag vocabulary, DataGrid columns/filters, CSV import/export | **462 assertions, all green** |
| **Altruist Integration** | Sprints 1–6. OAuth2 scaffold, identity resolution, positions/transactions sync, connection lifecycle, sync orchestration, HMAC webhook receiver | ~150 assertions; **every proof against synthetic responses — never a live Altruist call** |
| **Structured Notes** | R2 migration, Portfolio A1 global identity, EDGAR fetcher + corpus, payoff DSL, corrections polymorphism, extraction + hazard ensemble, STP routing + review queue, SSVI surface engine, underlying resolution with DB-enforced maker-checker | Real sprints against live infra |
| **SPV Manager / GL** | Sprint 22: chart_of_accounts, journal_entries/lines, posting templates, posting/reversal functions, tenancy model | 12/12 live |
| **Workflow Scheduler** | 5 sprints. RRULE recurrence, timezone-aware, Render cron, idempotent claim, overlap protection, CRUD UX, run history, notifications | All merged, cross-org proven |
| **TA Model** | 4 sprints. Pure modules, bi-temporal schema, 7 endpoints, admin settings UX, projection UX, calibration UX + obligation ledger | 77 + 31 + 22 + 48 assertions |
| **LiteLLM** | Phase A (deploy) + Phase B (route text calls, rollback proven) | 68/68 — **plumbing only, no tool/UX** |

---

## 3 · Serious project-wide problems

### 3.1 RLS was inert in production — **[RESOLVED THIS SESSION]**

Flagged independently by three threads (Altruist, Structured Notes, and the RLS review). `DATABASE_URL` pointed at the RLS-bypassing `postgres` role; every policy across 171 tables was decorative.

**Fixed tonight**: full discovery (171 tables, 100% policy coverage confirmed, grants correct), one real blocker found and fixed (`workflow_scheduler_tick`'s raw connection — needed a pooler-safe `platform_scope()`, caught its own first attempt failing under transaction pooling), cutover executed, cross-org isolation proven live through the real application path. **A second live bug surfaced during the cutover's own smoke test**: `main.py`'s `sync_catalog` wrote through the RLS pool without setting context — invisible under bypass, silently failing the moment enforcement went live. Fixed and verified.

**Still requires manual action**: restart both Render services and watch the next few scheduler ticks for `FIRED`/`skip` lines. A tick reporting zero examined triggers is the silent-failure signature.

### 3.2 `security_invoker` view vulnerability — systemic, partially audited

Found **twice independently** in the fee thread (fee39's own new view, plus two pre-existing GL views `v_capital_accounts` and `v_trial_balance`). **Any `CREATE VIEW` omitting `security_invoker = true` is a potential cross-tenant leak**, regardless of how correct the underlying tables' RLS is — a view owned by a `rolbypassrls` role silently bypasses RLS on everything it reads.

Only `public` and `portfolio` were audited. **This needs a standing rule in `CLAUDE.md` and a recurring audit**, not a one-time fix.

### 3.3 `schema_snapshot.sql` cannot represent CHECK constraints, RLS policies, or seed data

Confirmed general (fee40's F40-I), affects every sprint back to fee31. **Anyone rebuilding an environment from the snapshot alone would silently be missing every correctness guarantee built across this entire platform.** Directly contradicts the snapshot's role as "schema source of truth" in `CLAUDE.md` — worth an explicit caveat there.

### 3.4 No staging environment

Flagged by the Altruist thread with a concrete cost: Sprint 6's teardown bug corrupted real rows in the shared live database, cascading into five other sprints' regression checks failing falsely in the same run. Not hypothetical.

### 3.5 `users.role` has no CHECK constraint and no demotion path

Once hand-set to `super_admin`, nothing in the application can revoke it short of direct SQL. Two such rows exist. Compounded by: **`auth0_sub` is UNIQUE on the bare subject string with no tenant-of-origin recorded** — if the same subject were ever issued by both Auth0 tenants, they'd collide onto one row, and the role-ratchet would silently promote it.

### 3.6 Five-registry duplication

`config`, `reference_data`, `udf_definitions`, `investment_profile_questions`, `note_terms_field_registry` are five independent implementations of "key/label/type/display_order/is_active." `investment_profile_questions` + `_answers` in particular is a fully-working UDF system for CRM entities under a different name. Registered as debt by udf00, deliberately untouched.

### 3.7 Smaller data-integrity gaps

- `entity_ownership` vs `entity_relationships` — likely duplicate/dead table, direction unresolved; bears directly on the open Sprint 21 `POST /entities/{id}/ownership` bug
- `reference_data.org_id` unused everywhere (all rows global) — seeding an org-specific override without fixing the read path would silently return duplicates
- `spvs.spv_status` free text, no CHECK; `spvs.deal_id` NOT NULL but no FK (a test SPV survived its paired deal's deletion)
- `features.*` in `org_settings` proposed in an EIN spec, never implemented — **no per-org entitlement mechanism exists anywhere**; every gating decision reduces to a single role check

---

## 4 · Sprint workflow — real findings not yet in the standard

The Altruist thread already folded its own findings in. These are from **other** threads and are **not yet in the document**:

**From the fee module (14 sprints):**
- **Connection-drop mid-leg happened three times** (fee38, fee41, fee43), same signature: `API Error: Connection lost mid-response` → `FATAL: sprint leg reported an error (success)`. Not in the failure taxonomy at all. Recovery required: check `git status`/`git log` immediately (state ranged from scratch files to **2000+ lines of uncommitted service-layer changes touching already-shipped files**), commit WIP with an UNVERIFIED marker, hand-review any diff to already-verified files, then re-run. Reconstructed live each time.
- **`run_sprint.sh` does not create the sprint's branch** — it pushes whatever is checked out. Caused a real incident (fee34 landed directly on main). Part 2 exists for this reason but doesn't say *why* skipping it is dangerous.
- **Later sprints repeatedly patch earlier merged sprints** — fee32 fixed a fee31 idempotency gap; fee37 found fee34's missing scale constraint; fee39/fee40 found live RLS bypasses; fee43 broke a live trigger with its own Part 1 SQL. Patches applied out-of-band with **no single index of "patches applied after the fact to sprint N."**
- **`PROJECT_STATUS.md` dated corrections** (`UPDATE <date>` appended under the original) emerged organically and works well — worth naming as the convention.

**From the UDF chain (6 sprints):**
- **`run_sprint.sh` lacked `--dangerously-skip-permissions`** — `acceptEdits` alone blocks live-DB Bash behind an approval prompt that doesn't exist in headless `-p` mode; sprint hangs to budget then reports blocked. Fixed in all three call sites. *(This session hit the identical failure twice on the RLS sprint before diagnosing it.)*
- **Regression-chain nesting compounds multiplicatively** — when each verify re-runs its predecessor's full script, the earliest re-executes once per depth level (4× in a 4-deep chain), turning ~5 min into ~45. Durable fix: state the predecessor's baseline as fact in the next prompt rather than re-running it.
- **A `-p` session can fabricate "I'll wait for a background process"** — no such mechanism exists. Happened 3+ times.

**From Structured Notes:**
- **Prompt landing at the wrong path is real and recurring** (twice in one thread) — worth a pre-flight check in `run_sprint.sh`: refuse to start if the file doesn't parse as markdown.
- **No way to represent "no verify script by design"** — a discovery-only sprint that correctly declares this still gets `FATAL`.
- **`ANTHROPIC_API_KEY` vs Claude Code's OAuth session actively conflict** — the key takes precedence, breaks `/refresh-schema`, and **routes an entire sprint's work through paid API billing instead of the Max plan**, silently, until credits run out mid-run.
- **stdout/stderr split makes the "live log" instructions misleading** — `SPRINT_LOG` only receives stderr; the real transcript goes to a per-leg JSON file nothing in the runner's guidance mentions. *(This session hit exactly this — two empty logs before bypassing the wrapper.)*
- **A sprint can silently execute a different sprint than the one named**, if wrong content was pasted into that filename. The runner trusts the filename, not the content.

**From SPV/GL:**
- **"Pushed, sprint complete" reported before any code executed — three times consecutively.** Each claimed done-ness from re-reading `schema_snapshot.sql`, not from running anything, because Claude Code had no `DATABASE_URL`. **Structural gap, not a wording problem**: the standard should require that a sprint is not complete until its verify script has executed with real output, and that an agent with no DB access must say so rather than report completion.
- **Schema snapshot staleness caused repeated real damage** — a snapshot read from a stale remote branch tip was missing 8+ sprints of tables. Part 2 should include git-divergence detection, not just checkout/merge.
- **The teardown-vs-immutability-trigger conflict is a pattern** — any verify script that posts a journal entry needs the disable/re-enable-trigger teardown from the start.

---

## 5 · Untracked specs, by weight

### 5.1 Agentic architecture — 15 items, zero built

Custody cliff (**settled, permanent**: trade execution, money movement, filing submission, GL posting never get an agent — now in `CLAUDE.md`). Compliance Analyst ≠ Compliance Officer (**settled**).

Highest urgency per that thread's own sequencing: **#3 capability annotation on the action registry** (cheap now, expensive to retrofit across S26–S29a) and **#5 `review_role` on the proposal queue**. **#2 and #10 (`entity_context` shared assembler) are the substance of S27.** #13 (fixed escalation-reason enum) is an afternoon. Full list in the prior reconciliation doc.

**New from this pass — #15 cadence finding**: six of eight agents are cron-driven, not chat-driven. This **raises the Workflow Scheduler's priority** (now built) and puts most token spend on the Batch API at 50% off. Not reflected in any roadmap ordering.

### 5.2 Cash & Liability Matching — designed, fully blocked

Design doc + interactive prototype delivered. Omnibus CD concept assessed and **rejected** in favor of omnibus execution / individual ownership (finalized). Matching horizon settled: 36-month visibility, 24-month match. Build order: Altruist read-only → acct01 → acct02 → cash00.

**Blocked on**: Altruist account/position data model (uninspected), write-endpoint partner access (cash08), S25 (cash12). **The entire Altruist product assessment — Cash, Altruist One rate, brokered CD availability, cash spread for ADV Item 5 — is unverified**, reasoned from priors with no web access, flagged low-to-medium confidence throughout.

**The obligation-ledger overlap flagged in its own open items is now likely resolved** — TA Model Sprint 4 built a real read-time obligation ledger. Worth a discovery pass confirming this before scoping `cash00`.

### 5.3 Workflow Manager Wave 2 + BPMN

SpiffWorkflow adopted (LGPLv3 — should be logged for SOC/vendor review; no evidence such tracking exists). bpmnchat rejected. BPMN XML canonical, JSON scratchpad-only. Verb-tier gating architecture, palette restriction spec, NL→BPMN compiler with auto-layout, RestrictedPython script engine, custom ServiceTask handler suspending on Tier-1 — **none scoped as sprints**. **bpmn-js watermark decision flagged as open in three separate threads, still undecided.**

**Sequencing dependency not reflected anywhere**: workflow steps target entities/series, so this should land *after* the Investment/Series restructure.

### 5.4 Demonstration-learning ("teach a task")

Registry-verb-trace capture (not screen pixels) → Sonnet-generated BPMN draft → bpmn-js review → tier-gated replay. Multi-actor capture against one process instance. Flagged as a **206(4)-7-relevant artifact and potentially licensable**. Proposed for Wave 3, between S29a and S29b.

**Verb registry cleanup, concrete and small**: `crm.draft_note` marked `reversible=false` despite being a draft; three rows have `action_key` prefixes drifting from their `module`; `spv.subscribe`/`spv.record_transaction` likely mis-tiered. The **~85-verb target taxonomy exists only in chat text.** The three-question tier test (moves money → third-party-relied artifact → mutates ownership/terms/posted ledger) is defined nowhere in schema or docs.

### 5.5 Structured Notes continuation

Comparability taxonomy + percentile scoring (designed, final corpus-track sprint). **SPX live-market-hours check deployed but never actually verified against real SPX** — the whole stated purpose of that sprint. Worst-of Monte Carlo (needs the agreed correlation-range policy: report a range, never a point estimate). Base rates / realized outcomes (~$500–2,500/yr data). Lifecycle monitoring **was** blocked on Wave 4 — **now unblocked by the Workflow Scheduler**, needs a discovery pass. Marketing Rule review needs counsel. R2 old-bucket deletion deferred with no trigger date.

**Blocked on a human decision**: router-level super-admin fix — three options (issuer check / org predicate / host separation), deliberately not chosen by the discovery sprint.

### 5.6 SPV / GL continuation

Two-hop pro-rata allocation (Investment Series → subscription ledger → Member Series → member) — `vehicle_type`/`master_entity_id` added as an additive seam, **explicitly not wired**. "Series"→"Class" rename applied only in verify-script comments. Illiquid-asset-class tiering (1250 recapture, PIK/163(j), depletion/IDC, QSBS, nested K-1s, §1256). 1065/K-1 in-house with e-filing to a CPA of record — requires a parallel §704(b) capital-account layer. Full fund-admin function map with build-vs-buy split.

**From the fee thread, unscoped and dependency-blocked**: GP legal-entity model (enum value exists, zero rows, carry books as a payable instead of an equity allocation); `v_capital_accounts` real fix (depends on whether capital calls/distributions post real journal lines at all — fee42b bypassed the GL entirely); WHOLE_FUND carry basis (fee42b refuses rather than approximating); time-weighted preferred-return accrual (the override hook exists, the convention was never specified); partial advisory-fee offset (boolean-only today).

### 5.7 Product philosophy — five concepts, document not written

Verb library / skills-as-firm-language; two-layer memory + **presentation-profile rendering layer**; anticipation via prepared drafts never autonomous action; orchestration as hidden dispatcher; coworker-in-place. Unified under **"context"** as Jeremy/Mesh's existing term — one primitive, three scopes.

Backed by two passes of outside-industry research (Salesforce/Agentforce case studies, Zappos, Epic MyChart, VA ambient scribe, IRS voicebot vs. chatbot, Ritz-Carlton $2,000 rule, Klarna's automate-then-reverse). **All source material settled; the document was explicitly held, not forgotten.**

Derived, untracked: AI-disclosure convention for member-facing drafted comms; pre-authorized action budget (Ritz-Carlton analog, should map onto the Sprint-11 autonomy tiers); ambient capture of adviser-member conversations into CRM (connects to the existing correspondence-tracking backlog item); confidence-aware escalation with full-context handoff.

### 5.8 AI Gateway / cost levers

Cloudflare AI Gateway as passthrough proxy — **must use the provider-specific Anthropic endpoint, not universal/auto-fallback** (fallback violates fail-loud + model-invariance). **Pre-adoption validation spike is the single highest-risk unknown**: confirm `anthropic-beta` headers survive the CF proxy (prompt caching depends on it) and SSE passthrough works. Portkey rejected (new SOC 2-scope vendor); OpenRouter rejected permanently for production.

**Two cost levers independent of the gateway decision**: Batch API for EDGAR/424B2 corpus + the S25 DeepEval 50-doc run (~50% off), and **prompt caching for Chancery extraction — identified as the actual dominant lever, far more than gateway choice.**

**Reconciliation check design**: compare Anthropic's own billing against gateway logs to detect calls bypassing the resolver. Motivated by the eight hardcoded `"2ndactcapital-docs"` fallbacks already found.

### 5.9 Business / strategy (not engineering)

Membership threshold decision (pay-and-participate vs. $500k-over-3–5-years forcing function). Whether members hold equity in Access — **needs securities counsel**. Series LLC tax treatment risk (live IRS comment period). Chapter legal operating model. DOL advisory opinion / MEWA pathway — **documented sequencing, zero actual ERISA counsel engagement**. Build-vs-buy for the five tech components — **the largest gap between documented intent and trackable work**; the asset allocation tool in particular was pulled from a separate conversation and connects to no current build plan.

Community prioritization (data-backed): community/peer-programming is **at market parity — not where further investment is warranted**. The real differentiation surface is member co-investment (I02) and personal-capital carry participation (I04), essentially unique in the market. **Long Angle is the one named competitive watch-out.** Gaps: C07 global footprint, P04 flagship conference.

### 5.10 Auth architecture

Directional conclusion: since 2nd Act/Masters isn't resold to other RIAs, multi-tenant IdP-brokering isn't needed — single-tenant OIDC suffices. Internal module linking should propagate the existing httpOnly JWT, not run SSO handshakes inside the trust boundary.

**Unscoped**: how token validation sits across Next.js and FastAPI if Jeremy's reporting tool is a separate backend. **Unconfirmed**: which of Fidelity / iCapital / specific carriers actually support inbound SSO vs. API-only vs. neither (carriers flagged as the weak case, often file-based/DTCC).

---

## 6 · Blocked on external action

| Item | Blocked on |
|---|---|
| **Altruist Sprints 1–6 validation** | Sprint 0 outreach — **drafted, never sent**. Needs Joe's business inputs (entity choice, legal address, contact, launch date, AUM projection). **Draft is now stale**: OAuth redirect URI can be filled in for real (Sprint 4 built the callback), and webhook signature docs should be added to the ask. |
| **Entire cash module** | Altruist data model inspection + write-endpoint partner access |
| **All Altruist product claims** | A conversation with the Altruist rep — nothing verified |
| **SES email delivery** | IAM permission grant, sandbox status check, verified sender address |
| **LiteLLM Phases C–J** | Zero registered models on the proxy — **blocks fee40's chat interface and fee41's polish pass from serving a real model in production** |
| **Router-level super-admin fix** | Human decision between three options |
| **Doppler → Vercel sync** | Needs scoping to exclude Supabase-owned variable names |

---

## 7 · Recommended priorities

**Do now — small, high-leverage, cheap to do and expensive to defer:**
1. Restart both Render services; watch scheduler ticks (closes out tonight's RLS cutover)
2. **Capability annotation on the action registry** (agentic #3) — explicitly flagged as cheap now, expensive to retrofit across S26–S29a
3. **`review_role` on the proposal queue** (agentic #5) — same reasoning
4. **Verb registry cleanup** — three concrete, tiny fixes already identified
5. **`security_invoker` standing rule** in `CLAUDE.md` + audit any schema beyond public/portfolio
6. **Fold section 4's workflow findings into `SPRINT_WORKFLOW_STANDARD.md`** — especially the connection-drop recovery sequence and the "never report complete without executing verify" rule

**Send:**
7. **Sprint 0 outreach to Altruist** — six sprints and an entire module are blocked behind an unsent email, and the pile of unvalidated assumptions grows with each sprint

**Discovery sprints worth scoping:**
8. Whether TA Model's obligation ledger resolves the cash module's flagged overlap (gates `cash00`)
9. Whether capital calls/distributions post real journal lines (gates `v_capital_accounts`)
10. `investment_profile_questions` vs `udf_definitions` — is one retirable?

**Decisions only Joe can make:**
11. bpmn-js watermark (flagged open in three threads)
12. Router-level super-admin fix (three options)
13. GP legal-entity model
14. Membership threshold

---

## 8 · Threads with nothing outstanding

- **Macro News-to-Asset Analyzer** — standalone HTML tool delivered; only open item is confirmation testing with a live API key
- **2nd Act Concept & Strategy** — six documents delivered and validated; all remaining items are business decisions, not work
- **Community Prioritization** — analysis complete; the recommendation is *not* to invest further in community features
- **Ripasso Development** — stalled at step one on folder access declined; no work started
