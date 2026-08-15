# Hollisworks / 2nd Act Capital — Project Status

**This file is the single, durable source of truth for what has actually been built.** It lives in git specifically because both chat memory and Drive-hosted generated documents have been lost at different points — git survives sandbox resets, session boundaries, and everything else.

**Every sprint prompt should include a final task: update this file as part of the same commit.** Record real gaps and blocked items honestly, not only successes.

**Companion file:** `docs/DEVELOPMENT_ENVIRONMENT.md` — how we work (stack, sprint methodology, conventions). This file is what exists.

---

## 0 · Identity and core decisions

- **"Hollisworks" is the platform name, fully replacing "Ripasso"** — both public brand and internal reference. The embedded AI assistant is **Hollis**. Tagline: *"Hollis works. For you."* / *"AI orchestration for the modern RIA."*
- **2nd Act Capital** is the first client/tenant and current demo account — an RIA and private membership club. Structure: Ripasso Holdings (holdco) → 501(c)(6) membership club → Access (the RIA) + Hollisworks (the licensable software).
- **No Mesh integration** — repeatedly, deliberately reconfirmed. 2nd Act's own bitemporal entity graph (Sprint 15) is authoritative.
- **Light theme only, everywhere.** 2nd Act Signature palette: Navy `#1B2B4B`, Gold `#C5A880`. Hollisworks marketing has its own distinct tokens: holly `#1F4034`, bronze `#8A6220`. Never cross-apply.
- **Testing is exclusively against live production** — no staging environment exists.
- **Not yet in real production use** — all current data is dummy/test data, which substantially de-risks structural changes.

---

## 1 · Completed — platform spine (S11–S22)

Assistant framework · SPV manager · immutable general ledger · bitemporal entity/ownership graph · reference data · EntityPicker · ownership editing + time-travel · transaction types · marketing site · portfolio allocation lens (sunburst).

## 2 · Completed — S23 through S27

| Item | Status |
|---|---|
| S23 — Investment/Class restructure | DONE |
| S24 — White-label config (org_settings, RBAC, brand sweep) | DONE |
| Grid UX A + B — DataGrid (TanStack Table + dnd-kit, **not** AG-Grid) | DONE |
| Mini-Bedrock — org_settings-driven model selection | DONE (extended by S27) |
| S25 — DeepEval + open-set document classifier | DONE |
| S27 — TaskRouter | DONE |

**S27 TaskRouter**: real decision log (`ai_decision_log`: model requested/used, fallback_used+reason, cost, latency, success) + genuine per-org **ordered** fallback chain (upgrading Mini-Bedrock's single-value fallback) + non-blocking logging wired into the central AI-calling mechanism. Later independently confirmed as the real path Chancery's NL generation calls through.

---

## 3 · Completed — SOC / RBAC (6 phases + follow-on UI)

Profiles + Permission Sets on the fixed action-registry vocabulary + beneficiary edges · staff visibility (hierarchy+teams+assignment — additive/standalone, **not yet enforced**, see Known Gaps) · households (flexible rollup + strict primary) · restricted-access accounts (existence-hiding, wraps both visibility engines) · trading-authority tiers (Inquiry/Limited/Full) + maker-checker (confirmed intentionally broad, not money-movement-only) · Trusted Contact / POA-Delegate / External Professional Access · Profiles/Permission-Sets admin UI.

Full spec: `2nd Act SOC Access Control Design.docx`.

---

## 4 · Completed — RLS / tenant isolation, including the production cutover

**Policy-writing phase fully closed.** All tables in the public schema have RLS enabled with at least one policy — confirmed via a comprehensive final sweep, not assumed.

That sweep caught a real gap immediately before cutover: **15 tables had no policy at all** — 7 from the Workflow Manager/TaskRouter build (`workflow_definitions`/`versions`/`steps`/`runs`/`run_steps`/`triggers` + `ai_decision_log`) plus 8 the original batches simply missed (`member_target_allocations`, `organizations`, `permissions`, `posting_template_lines`, `role_permissions`, `team_members`, `user_permission_sets`, `user_roles`). Three distinct policy shapes were needed: standard direct `org_id`; a self-referencing policy for `organizations` (its `id` **is** the org); global-read/super-admin-write for `permissions` (genuinely global, no `org_id` column at all); and indirect EXISTS-subquery policies via a real parent for 5 junction tables.

**CUTOVER COMPLETE.** Render's `DATABASE_URL` points at the non-bypass `app_service` role — tenant isolation is genuinely enforced in production. Core functionality confirmed working post-cutover across entities/SPVs/marketplace/ownership graph/workflow manager.

**Two issues surfaced during the cutover smoke test**, both since resolved — see §9.

---

## 5 · Completed — Ownership Tree Graph (Sprints A, B, C)

**Sprint A (interactive)**: dual staff/member routes sharing one component, both ownership and beneficiary edges shown distinctly, time-travel, reverse/owned-by toggle, restricted-access enforcement proven end-to-end.

**Sprint B (export)**: a real stress test proved a simple print-stylesheet fails on large trees (SVG can't page-break — content either clips or shrinks to ~2px text). Built a dedicated paginated renderer instead, proven on a 36-node/10-page tree.

**Sprint C (CRM integration)**: the Ownership tab now embeds the graph directly; clicking a node navigates to the destination entity's CRM page with the Ownership tab pre-selected via a `?tab=` query param, proven with a real generated route.

**Feature complete.**

---

## 6 · Completed — Workflow Manager Wave 2 (S29a)

`bpmn-js` for authoring, **SpiffWorkflow** for execution (paired — SpiffWorkflow is built to consume bpmn-js output). Five-table object model. A workflow's effective autonomy = its **single highest-tier step**. Tier-1 proposed state lives in the schema as real rows. User Task assignment is role-based, specified by the process author, referencing the real Profiles table. Task/alert surface reuses existing `member_todos`, not a new notification system.

| Phase | Scope | Status |
|---|---|---|
| 1 | Object model + SpiffWorkflow engine | DONE — pause/resume + maker-checker proven with real seeded data |
| 2 | NL-to-BPMN generation + generic step deriver + safe tier defaults (read→T3, write→T2, never silently autonomous) | DONE — real failure-path testing, TaskRouter integration confirmed |
| 3 | Diagram editor (bpmn-js 18.22.1 + properties-panel 5.63.0) + Library screen | DONE — version-increment/re-derive proven (v1 untouched, v2 fresh) |
| 4 | Run console + Scheduler/Routine Viewer + Task/Alert integration + Version history | DONE — found and fixed: a failing run previously **vanished entirely** (rolled back) rather than getting stuck; now correctly transitions to `held` with an alert |
| 5 | Permissions — 3 granular action-registry permissions replacing a blanket admin gate | DONE — proven genuinely granular (an unrelated admin permission still gets rejected from all 3 surfaces); Profiles UI picked them up with zero frontend changes |

**Wave 4** (autonomous scheduled/event triggers) remains deliberately deferred — holds-and-alerts on failure, never silently retries. Note: Chancery Phase 7 built the **first real event-triggered execution** in a narrowly-scoped way (see §7), but general Wave 4 is still unbuilt.

Also deferred: dry-run/simulation mode. bpmn-js's attribution watermark accepted as-is. *"Jeremy's context framework"* resolved as historical-only (tied to the dropped Mesh plan), not a live dependency.

---

## 7 · Completed — Chancery (the platform's universal input + surfacing layer)

**Reframed from a document vault into the platform's alternate INPUT mechanism** (documents replace/supplement manual data entry) **and its CONTEXTUAL SURFACING layer** (documents appear ambiently wherever relevant, not via a search box). Full design: `2nd Act Chancery Expanded Design.docx`.

**All 11 phases complete.**

| Phase | Scope | Notable |
|---|---|---|
| 1 | DROP + ROUTE + EXTRACT (native PDF) | 23/23 — batch sequencing proven with real timestamps, partial-failure recovery within a batch |
| 2 | SORT (classifier) + STORE (R2, versioned) | 16/16 — propose-new-category queue; real R2 versioning (re-upload creates v2, v1 retained) |
| 3 | TABULAR K-1 extraction via Textract | Real Textract access after genuine troubleshooting (truncated keys, local-vs-Render env, an accidentally-attached AWS deny policy) |
| 3b | **Gap closure** — Phase 3's actual extraction logic was never built after the access gate passed; found during Phase 5 | Real end-to-end proof: DROP→extract→SORT→K-1→Phase 5's real auto-link/propose logic, both matched and no-match branches |
| 4 | Multi-format ingestion (DOCX/XLSX/PPTX/email+attachments/text/images) | 22/22 — mislabelled-extension anti-spoofing (magic bytes, not extension); email with 2 attachments recursively processed; zero PDF regression |
| 5 | Entity/transaction linkage + propose-new-record fork | 12/12 — many-to-many + generic polymorphic linking; approve routes through the **real** Sprint-17 entity-creation flow, never a bare insert |
| 6 | Review/confirm screen — the data-entry moment | 11/11 — **honest finding**: neither path captures source coordinates (Textract *does* return Geometry/BoundingBox but the code discards it — a real, fixable enhancement; pdfplumber never captured it). Degrades to a page reference rather than faking precision |
| 7 | Workflow Manager integration | **First real event-triggered execution in the platform.** Governance preserved: a Tier-1 step still genuinely pauses for approval even on an auto-started run (`run='running'`, User Task `'active'`, `approved_by=None`) |
| 8 | Correction-learning loop | **Not fine-tuning** — a correction log read back at inference time. DeepEval measured a real 33.3% → 100% accuracy improvement (+66.7 points). Org isolation proven twice (query logic + real `app_service` role) |
| 9 | Contextual surfacing — reusable Documents panel | 13/13 — discovery caught a real route collision (`/entities/{id}/documents` already claimed); same component proven embedded in 3 genuinely different pages |
| 10 | VDR upload → propose a new deal record | **First aggregate cross-document AI capability.** Existing `createDeal` logic refactored into a shared service so both paths call identical code |
| 11a | Narrative metadata extraction | 11/11, zero skips — 3 parties extracted with **specific** roles (Grantor/Trustee/Beneficiary). A human-corrected link role is never overwritten by later automation |
| 11b | Semantic INDEX + RETRIEVE (Voyage → pgvector) | 10/10 — both external gates passed live. Org-configurable provider (4 listed, only Voyage wired; others **backend-rejected**, HTTP 400). Restricted-access documents correctly hidden from search without a grant |

---

## 8 · Hollisworks headless multi-tenant architecture

**Foundational pieces built and proven working in production. Full SAML federation designed but not yet built.**

### 8.1 · Domain / DNS — live and working

- `hollisworks.com` purchased via Cloudflare Registrar (Aug 1, 2026), same account as `2ndactcapital.com`.
- **Hard constraint discovered**: Cloudflare Registrar domains **cannot** point nameservers to a third party — confirmed directly by Cloudflare support and docs, not a UI-discoverability issue. A true wildcard (`*.hollisworks.com`, which requires Vercel-controlled nameservers) is therefore not currently possible.
- **Working solution**: each subdomain added individually — one CNAME in Cloudflare + one custom domain in Vercel — the same pattern already proven for `2ndactcapital.com`. Genuinely fine at expected client volume.
- **Live and confirmed "Valid Configuration" in Vercel**: `hollisworks.com`, `www.hollisworks.com`, `admin.hollisworks.com`, `2ndactcapital.hollisworks.com`.
- Cloudflare Email Routing configured (MX + DKIM added; the SPF TXT record deliberately deferred until SES domain verification, so one correct combined record is written instead of two conflicting ones).
- **Dated reminder — on/after Oct 1, 2026** (past the likely 60-day ICANN transfer lock): revisit whether a registrar transfer + true wildcard is worth it, versus continuing the manual per-client pattern — which may honestly remain simpler long-term.

### 8.2 · Identity architecture

- **Two Auth0 tenants, not three**: (1) 2nd Act's existing tenant — **not deprecated**, to be *reconfigured* as a federatable IdP source; (2) a new Hollisworks tenant (`dev-gy85vzuf6mruzv3j.us.auth0.com`) serving **both** Hollisworks staff corporate identity **and** the central broker other RIAs' IdPs federate into.
- The application never implements raw SAML — Auth0 does that work and returns a JWT the existing `verify_token()` already validates. **No change to core verification logic required.**
- **Auth0's free tier includes exactly one permanent SAML/Enterprise connection** — enough to pilot with one real client. Beyond that, multiple independent sources report **$5,000–$34,000+/year** per additional connection — a real business decision for later. **Okta ruled out** as a cheaper alternative (same company as Auth0; no permanent free tier, $1,500/year minimum).
- **Enrollment model**: RIA-initiated, not Hollisworks-invite-initiated. The RIA supplies a list (email + role); Hollisworks creates a pending record; the RIA separately enrolls that person in their own IdP. Matching is by **exact email** (SAML NameID, `emailAddress` format — zero extra IdP configuration burden). **No match = hard reject**, never auto-create.
- `admin.hollisworks.com` is a **reserved, special-cased subdomain**, deliberately kept out of the `organizations` table so real-client resolver logic isn't entangled with this one case.
- **Auth0 URL config convention: explicit listing, not wildcards** — Auth0's own docs caution against wildcards in production, and independent reports describe real bugs with wildcard support for "Allowed Web Origins" specifically.

### 8.3 · Built and proven working

- **Sprint 1 — host-header tenant resolver** (16/16). Subdomain-to-org resolution proven RLS-safe. Discovery found `/theme/public`'s pre-auth lookup only worked because production still ran the bypass role at the time; added a narrowly-scoped SELECT-only carve-out (`organizations_preauth_resolve`) — proven to allow reads but block writes, and not to leak cross-tenant data. Slug validation added to org creation (rejects uppercase/special characters/reserved words).
- **Hollisworks marketing page** — real HTML integrated faithfully; `hollisworks.com` (bare) serves it, `2ndactcapital.com` correctly serves 2nd Act's own separate page.
- **Shared firm-search interstitial** — both Login and Enroll route to one search flow, remembering original intent; fuzzy-matches `organizations.name`; redirects to the org's explicitly **stored** `login_url`/`enroll_url` (stored, not constructed by convention — this is what enables a future custom-domain client with no special-case logic). Ambiguous or no match: **asks the user to clarify/retry, never guesses, never shows a pick-list.**
- **Contact endpoint** — `POST /api/v1/marketing/contact` persists real submissions.
- **Second Auth0 tenant wired additively** — used only for `admin.hollisworks.com`. 2nd Act's own login proven unaffected (`lib/auth0.js` confirmed **byte-identical** to git HEAD).
- **`admin.hollisworks.com` login works end-to-end**, confirmed by real browser testing.
- **Sprint 2 (invite flow)** — the invite data model, token generation, expiry, and revocation are **done and proven**, including cross-org isolation. The email-delivery tasks were correctly **blocked** at an honest SES credential gate; SES credentials have since been configured, so those tasks are ready to complete.

### 8.4 · The admin.hollisworks.com debugging chain — six real issues, all resolved

| # | Issue | Type |
|---|---|---|
| 1 | Tenant/domain selection silently fell back to 2nd Act's tenant (SDK's `domain ?? AUTH0_DOMAIN` default) | Code |
| 2 | Auth0 dashboard callback/login URIs missing the app's real `/auth/` route prefix | Config |
| 3 | `appBaseUrl` silently fell back to the shared, 2nd-Act-scoped `APP_BASE_URL` | Code |
| 4 | `audience` — **both** frontend and a separately-broken backend default — silently fell back to 2nd Act's API audience | Code |
| 5 | `https://api.hollisworks.com` was never registered as a real API in the Hollisworks tenant | Config |
| 6 | The real Application was never authorized for **User-delegated** access to that API (a separate axis from Client/M2M — easy to configure the wrong one) | Config |

Issue 4 was found by a **comprehensive field-by-field audit** (22/22) rather than another reactive one-off fix — that audit is what caught the backend-side default that would otherwise have caused a fourth round of debugging.

**Lesson worth keeping**: for any *new* API/Application pairing in Auth0, items 5 and 6 are real, necessary, one-time dashboard steps — not automatic.

### 8.5 · Not yet built

- Full SAML federation of 2nd Act's tenant into the Hollisworks broker (the actual Enterprise Connection + "SAML2 Web App" addon on 2nd Act's side).
- The password "back door" for RIAs without SAML — deferred; per-org-toggle vs. universal also deferred.
- Per-tenant SAML setup automation — deliberately manual until there's real recurring multi-client demand.
- **Minor**: `hollisworks.com/login` typed directly (bypassing the real button) falls through to 2nd Act's tenant. Confirmed **not** a bug — real users reach the correct flow via the button — but worth guarding eventually.

---

## 9 · Resolved issues

**AI sidebar missing count/filter queries** — found via real user testing (*"how many investments are there"*, *"how many entities reside in CT"* both correctly said no tool existed rather than guessing). Fixed for entities (state/region filter) and investments (status filter), reusing existing endpoints and the same visibility-composition pattern as the Ownership Graph and semantic search. **Proven**: a staff/member user with limited visibility gets a count scoped to only what they can see (1 of 3 org-wide CT entities), not the org total.

**Staff visibility Super Admin bypass** — `get_staff_visible_entity_ids` now derives the caller's role internally and returns the full org set for Super Admin, with Org Admin correctly **excluded** and regular staff still restricted. No call sites needed changing.

**RBAC Super Admin bypass (`services/rbac.py`)** — the remaining piece of the cutover incident. `has_permission()` previously default-allowed **only** when a user had zero `user_roles` rows; a Super Admin who acquired any role row fell through to a strict per-permission check with no escape hatch. Fixed: `is_super_admin` checked **first**. Proven against the exact incident scenario and real call sites, with non-super-admin behavior unchanged in both directions.

**Database password exposure** — a live `app_service` password was accidentally pasted into a chat. Rotated immediately via Supabase, Render updated and redeployed, chat message deleted. No indication of actual unauthorized access; handled as precaution.

---

## 10 · Known gaps — real, tracked, not forgotten

| Gap | Detail |
|---|---|
| `services/permissions.py` never checked for the super-admin bypass gap | This platform has **three** separate, independently-evolved permission systems: `services/rbac.py` (fixed), `services/permissions.py` (JWT-claim-based, gates marketplace/SPV/VDR — **unverified**), and `services/profiles.py` (Workflow Manager's, already correct). Worth checking; not urgent. |
| `staff_assignments` has almost no real data | Only 2 entities (test fixtures) have any assignment. Real staff-visibility enforcement isn't usable platform-wide until this is populated — a data backfill, separate from any code fix. |
| Stray duplicate user identity for jlarizza@culmina.io | Two user rows exist (normal, Jun 26; dormant, Jul 2 — promoted to super_admin as a cutover unblock). Root cause not fully diagnosed. **Explicit decision: leave as-is** — deliberately parked, not worth the risk of cleanup in a mature codebase for a low-harm item. |
| Aggregate-query gap in the AI sidebar, beyond what was fixed | Same missing count/filter capability confirmed for SPVs, workflow runs, deals-by-attribute, member investments, documents, and task/notification counts. Reuse the proven visibility-composition pattern. |
| Recurring non-fatal RLS startup warning | Every backend startup logs `sync_catalog failed (non-fatal): new row violates row-level security policy for table "assistant_action_catalog"` — reproducible across deploys. Some startup process writes with no org context. Non-blocking, unfixed. |
| Chancery source-coordinate tracing | Textract returns Geometry/BoundingBox data that the processing code currently discards before storage — a real, fixable enhancement (the data exists, it's just thrown away). pdfplumber never captured it at all. |
| Ownership Graph bidirectional view (Option B) | A single view showing owners **and** owned entities fanning both directions at once. Deliberately not built (Option A — the toggle — was chosen). A genuinely different rendering shape; its own future sprint. |
| No confirmed UI for AI model settings | `ai.model.*` and `ai.embedding.*` exist as real `org_settings` rows but may only be editable via direct DB access. Unknown whether `OrgSettingsEditor.jsx` is a generic key/value renderer (in which case they may already surface) or curated. Quick discovery task, not urgent. |
| R2 bucket name (`2ndactcapital-docs` → `hollisworks-docs`) | **Migration attempted 2026-08-14 — BLOCKED, not done.** Two independent honest gates tripped: (1) **No R2 credentials or copy tooling in the sprint environment** — `R2_ACCOUNT_ID/ACCESS_KEY_ID/SECRET_ACCESS_KEY/BUCKET_NAME` live only in Render (`render.yaml`, `sync:false`); absent from `apps/api/.env`, shell, and `~/.bashrc`. No `rclone`/`aws`/`cloudflared`; `boto3` only in the venv. Cannot create the new bucket or copy objects. (2) **Bucket name is embedded in row data** — `deal_documents.r2_bucket` (1 row = `'2ndactcapital-docs'`); this makes it a data migration, not just an object copy, which is out of this sprint's scope. **Findings worth keeping:** stored keys are bucket-**relative** (`chancery/{org}/…`, `deals/{id}/…`, `spvs/{id}/…`) except the `deal_documents.r2_bucket` column; **versioning is application-level** (distinct keys `…/v{n}/{document_id}`, tracked in Postgres — *not* R2 native), so a byte-for-byte key copy preserves all versions with no history loss; retrieval is **presigned-only** (no public `r2.dev`/custom-domain URL); **frontend has zero R2 references** (no Vercel var needed). Bucket name is read via `R2_BUCKET_NAME` (default fallback `'2ndactcapital-docs'` in `services/storage.py`, `routers/entity_documents.py`, `routers/marketplace.py`, `routers/spv.py`). **To unblock:** run with real R2 creds available; the migration must then also rewrite `deal_documents.r2_bucket`. **Old bucket retained** — deletion is a separate follow-up sprint after a soak period. Verifier: `apps/api/scripts/verify_r2rename.py` (gates cleanly to BLOCKED when creds absent). |
| No 2nd-Act-tier competitor research | Only Quorum's ($100M–$1B UHNW tier) research exists — a different tier from 2nd Act's post-liquidity-founder audience. |

**Operational gotcha worth remembering**: Vercel **preview** deployments don't inherit production environment variables — preview-branch errors about missing Auth0 config are expected and are *not* production issues.

---

## 11 · Remaining backlog — unbuilt

Deal Diligence Engine (scaffolding/UI exist; AI-generation wiring doesn't — Chancery Phase 10's VDR intake is its natural front door) · Opportunity/Pipeline member-acquisition funnel (deal-side largely built; member-side is the gap) · S28 Drift monitor (deprioritized) · Client Profitability/Revenue Module · Correspondence tracking · Voice onboarding · MCP connector registry + secrets · User-created scheduled agents · Retention policy system · AWS Secrets Manager migration (decided, not built).

**Deferred / placeholder**: staging environment · branch protection on `main` · mobile app for advisers · live video conferencing with AI-suggested questions · securities-based lending (sequenced last) · live voice/Nova Sonic · standing rules + full 'Send' action · user invite/pre-creation flow for 2nd Act itself.

---

## 12 · Ready to build — blocked on external input

**Member Business Registration & EIN Capture.** Full spec written. Fills the previously-empty "Insurance" nav placeholder. Risk tier `.structural`, small surface area.

**BLOCKING GATE — do not open this sprint without it**: written carrier confirmation that a sole-proprietor EIN (nine digits only; no state registration, formation date, or certificate of good standing; no minimum employee count) is accepted, plus the arrangement type on record. **If the carrier requires a registered entity, this sprint is void** and must be re-scoped to per-member formation — materially larger and more expensive. Store the confirmation in Chancery before opening.

**Locked decisions**: no entity formation (a sole proprietorship satisfies the requirement, $0/same-day) · no formation-vendor integration (there's no state filing to automate) · the **member** is the IRS responsible party, never the platform · the platform **never** stores an SSN (the member keys it directly into the IRS online assistant) · guided member-facing wizard with a staff verification gate.

**Data model**: `member_businesses` (member_entity_id FK, business_name, business_type, `ein` masked in list views, ein_status, ein_issued_date, formation_state/date nullable, source, confirmation_document_id → Chancery document, bitemporal timestamps, retention/classification columns). **Hard constraint: no `ssn` column, ever** — with an explicit code comment stating this is intentional. Org-scoped RLS; member visibility via `resolve_entity_set`, staff via the standard engine.

**Workflow**: (1) *"Do you already own a business with an EIN?"* asked **first** — an individual can hold only one sole-prop EIN, so this prevents a guaranteed-fail application; (2) if no, explain the sole-prop path; (3) a pre-filled SS-4 worksheet from CRM data, every field **except** SSN; (4) hand off to the IRS's own online assistant side-by-side with the worksheet; (5) member returns, enters the EIN, uploads the CP-575 into Chancery; (6) staff verification gate (maker-checker) validates format + document presence before status becomes `verified`.

**Out of scope**: formation-vendor APIs · registered agent services · state filing/annual-report tracking · foreign qualification · payroll/W2 · the carrier integration itself · any advice on entity choice (factual options + referral to the member's own counsel only).

**Compliance**: insurance economics sit in the club or a licensed services entity, **never in Access (the RIA)** — preserves fiduciary integrity, avoids an ADV disclosure conflict. The carrier's written characterization of the arrangement is the file's regulatory defense for the owner-only-business fact pattern.

**White-label**: zero hardcoded brand strings/hex. Feature-flagged (`features.insurance_benefit.enabled`), default **off**, 2nd Act's org seeded on.
