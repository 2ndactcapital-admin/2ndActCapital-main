# Hollisworks / 2nd Act Capital — Project Status

**Purpose of this file:** the single, durable, git-committed source of truth for project status. Lives in the repo specifically because chat memory and generated documents have both proven unreliable at different points — git survives sandbox resets, session boundaries, and everything else. **Every sprint's own prompt should update this file as part of its commit, going forward** — this is the fix for the exact failure mode that necessitated rebuilding this document from a full conversation re-read.

**Last full rebuild:** this version (v1), reconstructed from a complete re-read of an extremely long session, prior to which a comprehensive status document existed only as generated Word docs (delivered as downloads, not committed to the repo) and was lost when the generating sandbox reset. **Joe has a more recent version of that Word doc to reconcile against — treat that as a secondary source to merge in, not this file's replacement.**

---

## 0 · Identity, naming, and core architecture decisions

- **"Hollisworks" is the platform's name, fully replacing "Ripasso."** The AI assistant embedded in the platform is "Hollis." Tagline: *"Hollis works. For you."* / *"AI orchestration for the modern RIA."*
- **2nd Act Capital** is the first client/tenant — an RIA/membership-club business, now explicitly "the demo RIA" for Hollisworks. Ripasso Holdings → 501(c)(6) nonprofit membership club → Access (the RIA) + Hollisworks (the licensable software).
- **No Mesh integration** (Jeremy's separate product) — repeatedly, deliberately reconfirmed. 2nd Act's own bitemporal entity graph (from Sprint 15) is authoritative.
- **Stack:** Next.js/Vercel (frontend), FastAPI/Render (backend), Supabase Postgres (project `mmgwmcinimzuhargsazs`), Auth0 (two separate tenants — see §5), Cloudflare R2 (storage, bucket `2ndactcapital-docs` — cosmetic rename to something Hollisworks-branded still pending), Anthropic API direct, AWS à la carte (Textract, SES), Voyage AI (embeddings).
- **Light theme only, everywhere** — whites/cream, never dark mode. 2nd Act's Signature palette: Navy `#1B2B4B`, Gold `#C5A880`. Hollisworks marketing site has its own, separate brand tokens (holly `#1F4034`, bronze `#8A6220`) — do not confuse the two or accidentally cross-apply hex values.
- **Testing is exclusively against live production** (`2ndactcapital.com` historically; now also `hollisworks.com` and its subdomains) — no staging environment exists.

---

## 1 · Security foundation — RLS (Row-Level Security)

**STATUS: Policy-writing phase fully complete. Connection cutover to enforcement is DONE — RLS is genuinely live in production.**

- All ~77 original tables + `users` + pilot table (`trusted_contacts`) have real, live-proven RLS policies, built across 6 batches (pilot, `users`/Phase 2, Batch A SOC/RBAC, Batch B Entity/CRM, Batch C Financial/ledger, Batch D Deals/Marketplace, Batch E+F combined Assistant/Notifications/Audit + Config/Reference).
- **A final, comprehensive sweep before the cutover caught 15 more tables with zero policy** (7 from same-session Workflow Manager/TaskRouter work, 8 from earlier work the original batches simply missed — including `organizations` itself and the global `permissions` catalog). All fixed with the correct policy shape per table (standard direct, indirect via parent, or global-read/admin-write for genuinely platform-wide reference data).
- **`DATABASE_URL` now points at the non-bypass `app_service` role in production** — tenant isolation is genuinely enforced, not just proven-in-isolation.
- **A real production incident occurred during/after cutover**: a stray duplicate Auth0 identity for the Super Admin account, combined with a genuine gap in `services/rbac.py`'s `has_permission()` (no `is_super_admin` bypass — only "worked" via the accident of zero rows in `user_roles`), caused a real lockout. **Root cause found and fixed** (`is_super_admin` now checked first, before any role-based logic). A second, related but distinct RBAC system (`services/permissions.py`, JWT-claim-based, gates marketplace/SPV/VDR endpoints) was identified but **not** checked for the same class of bug — worth verifying later, not urgent.
- **Known, non-fatal, recurring issue**: every backend startup logs `sync_catalog failed (non-fatal): new row violates row-level security policy for table "assistant_action_catalog"` — confirmed reproducible across multiple deploys. Some startup/background process writes with no org context set. Not blocking, not yet fixed.

---

## 2 · SOC / RBAC (access control)

**STATUS: Complete, all 6 phases + follow-on UI, merged.**

Profiles + Permission Sets (org-defined, additive grants) · Staff visibility (hierarchy+teams+assignment — built additive/standalone, **not yet wired into real enforcement**, staff still see org-wide today, needs a `staff_assignments` data backfill first) · Households (flexible + strict-primary) · Restricted-access accounts (existence-hiding) · Trading authority tiers + hard maker-checker (confirmed intentionally broad, not money-movement-only) · Member-side relationships (Trusted Contact / POA-Delegate / External Professional Access).

Full spec: `2nd Act SOC Access Control Design.docx`.

---

## 3 · Ownership Tree Graph

**STATUS: Complete — Sprints A (interactive) and B (printable export), both merged.**

Dual staff/member routes sharing one component. Both ownership and beneficiary edges shown, visually distinct. Time-travel, reverse/owned-by toggle. Restricted-access enforcement proven end-to-end, including in export. Export: a real stress test proved a simple print-stylesheet approach fails on large trees (SVG can't page-break) — built a dedicated paginated renderer instead, proven on a 36-node/10-page real tree.

**Known, separately-tracked gap**: `staff_visibility.get_staff_visible_entity_ids` originally had no Super Admin bypass — **found and fixed** (same session, separate small sprint) using the exact same fail-loud/explicit-bypass discipline as everything else.

---

## 4 · Workflow Manager (S29a/S29b)

**STATUS: Wave 2 (safe, non-autonomous) fully complete — all 5 phases. Wave 4 (autonomous scheduled/event triggers) deliberately not built, its own later effort.**

Architecture: `bpmn-js` for visual authoring, `SpiffWorkflow` for real execution (paired, not competing — SpiffWorkflow is built to consume bpmn-js output). Five core tables: `workflow_definitions`, `workflow_versions`, `workflow_steps`, `workflow_runs`+`workflow_run_steps`, `workflow_triggers`. A workflow's effective autonomy = its single highest-tier step. Tier-1 proposed state lives in the schema as real rows.

- **Phase 1**: object model + SpiffWorkflow engine — proven pause/resume + maker-checker with real seeded data.
- **Phase 2**: NL-to-BPMN generation + generic step deriver + safe tier defaults (read→Tier 3, write→Tier 2, never silently autonomous).
- **Phase 3**: diagram editor (bpmn-js + properties panel) + Library screen.
- **Phase 4**: Run console + Scheduler/Routine Viewer + Task/Alert integration (reuses existing `member_todos`, not a new notification system) + Version history. Found and fixed: runs that failed previously vanished entirely (rolled back) rather than getting stuck — now correctly transition to `held` status with an alert.
- **Phase 5**: Permissions — replaced a blanket admin gate with 3 granular, action-registry-based permissions. Proven genuinely granular (a user with an unrelated admin permission is still rejected from all 3 surfaces).

**S27 TaskRouter** (a real prerequisite, built alongside): `ai_decision_log` table + genuine per-org ordered fallback chain (upgrading Mini-Bedrock's single-value fallback) + non-blocking logging wired into the real central AI-calling mechanism (`call_claude_text`/`call_claude_json` → `_execute_chain`). Confirmed working via real downstream usage (Chancery's NL generation calls through it).

---

## 5 · Chancery (Document Vault) — reframed as the platform's universal input + surfacing layer

**STATUS: All 11 phases complete.** What began as a document-vault sketch became a comprehensive system: multi-format ingestion, broad linkage with propose-not-create discipline, a real human review/correction screen, governed Workflow Manager integration, a measured learning loop, ambient contextual surfacing, VDR-to-deal-creation, narrative extraction, and real semantic search.

| Phase | Scope | Status |
|---|---|---|
| 1 | DROP + ROUTE + EXTRACT (native PDF only) | Done — proven batch/sequencing + cross-org RLS isolation |
| 2 | SORT (existing classifier) + STORE (R2, versioned) | Done — propose-new-category queue + real R2 versioning proven |
| 3 | TABULAR K-1 extraction via Textract | Done, after a real credential-setup saga (see §6) |
| 3b | Closing a real gap — Phase 3's actual extraction logic was never built after the access gate passed; found during Phase 5, closed with real end-to-end proof | Done |
| 4 | Multi-format ingestion (DOCX/XLSX/PPTX/email+attachments/text/images) | Done — anti-spoofing (magic-byte, not extension) proven, zero PDF regression |
| 5 | Entity/transaction linkage + propose-new-record fork | Done — many-to-many + generic polymorphic linkage, exact-name matching, propose-not-create |
| 6 | Review/confirm screen — the actual data-entry moment | Done — honest finding: neither Textract nor native extraction currently captures source coordinates (Textract discards available geometry data; a real, fixable future enhancement) — degrades to a page reference rather than faking precision |
| 7 | Workflow Manager integration | Done — FIRST real event-triggered execution in the platform, narrowly scoped to one event type, governance proven preserved (a Tier-1 step still pauses even on an auto-started run) |
| 8 | Correction-learning loop (correction log + retrieval-augmented classification, NOT fine-tuning) | Done — DeepEval measured a real 33.3%→100% accuracy improvement |
| 9 | Contextual surfacing — reusable Documents panel | Done — same component proven embedded in 3 distinct real pages |
| 10 | VDR upload → propose a new deal record | Done — first aggregate cross-document AI capability; existing `createDeal` logic refactored into a shared service, not duplicated |
| 11a | Narrative metadata extraction (provisions, specific-role party linkage) | Done — real live AI extraction proven, human-corrected links never silently overwritten by later automation |
| 11b | Semantic INDEX + RETRIEVE (Voyage embeddings → pgvector) | Done — both real external gates (pgvector extension, Voyage credentials) passed live; org-configurable embedding provider (Mini-Bedrock pattern) with only Voyage functionally enabled, others listed but backend-rejected if selected |

---

## 6 · AWS / external vendor credential setup (real, completed work)

- **AWS Textract**: real credentials configured after a genuine troubleshooting chain (truncated keys, local-vs-Render env distinction, an accidentally-attached AWS deny policy from using the console UI instead of a custom JSON policy). Working IAM setup: `AmazonTextractFullAccess` on a dedicated IAM user with long-lived keys.
- **Voyage AI**: real credentials configured, confirmed working via a real, live embedding call (`voyage-3.5`, real 1024-dim vectors).
- **AWS SES**: credentials configured (separate dedicated IAM user, `ses:SendEmail`/`ses:SendRawEmail`/`ses:GetSendQuota`). **Domain verification (DKIM/SPF/DMARC) not yet completed** — real DNS records need adding once `hollisworks.com` is the confirmed sending domain (see §7). Sandbox-mode status not yet confirmed for real production sending.

---

## 7 · Hollisworks headless multi-tenant / SAML architecture

**STATUS: Foundational pieces built and proven working end-to-end in production. A full SAML federation (2nd Act's tenant → Hollisworks broker) is designed but not yet built.**

### 7.1 · Domain / DNS (real, live)
- `hollisworks.com` purchased via Cloudflare (same account as `2ndactcapital.com`).
- **Real, hard constraint discovered**: Cloudflare Registrar domains cannot have nameservers changed to a third party at all — not a UI-discoverability issue, confirmed directly by Cloudflare support/docs. A true wildcard (`*.hollisworks.com`, requiring Vercel-controlled nameservers) is therefore not currently possible without a registrar transfer.
- **Pragmatic, working solution in place**: each subdomain added individually — one CNAME record in Cloudflare + one custom domain in Vercel per subdomain — the same simple pattern already proven working for `2ndactcapital.com`. Genuinely fine given client count stays small and grows slowly.
- **Live, working domains today**: `hollisworks.com`, `www.hollisworks.com`, `admin.hollisworks.com`, `2ndactcapital.hollisworks.com` — all confirmed "Valid Configuration" in Vercel.
- **Dated reminder set**: on/after **Oct 1, 2026** (past the likely 60-day ICANN transfer lock from the Aug 1 purchase date), revisit whether a registrar transfer + true wildcard is worth it versus continuing the manual per-client pattern indefinitely — genuinely may remain the simpler long-term choice regardless.
- Cloudflare Email Routing separately configured (MX + DKIM records added; the SPF TXT record deliberately deferred until SES domain verification, to write one correct combined record instead of two conflicting ones).

### 7.2 · Identity architecture (designed, partially built)
- **Two separate Auth0 tenants, not three**: (1) **2nd Act's existing tenant** — not deprecated, to be *reconfigured* as a federatable SAML/OIDC IdP source; (2) **a new Hollisworks tenant** (`dev-gy85vzuf6mruzv3j.us.auth0.com`) serving BOTH Hollisworks' own staff corporate identity AND the central broker other RIA clients' IdPs federate into.
- The application itself never implements raw SAML — Auth0 does that work and hands back a JWT the existing `verify_token()` logic already knows how to validate. This architecture requires no change to that core verification logic.
- **Auth0's free tier gives exactly ONE real, permanent SAML/Enterprise connection** — enough to pilot with one real client. Scaling past that has real, documented cost implications ($5,000–$34,000+/year per multiple independent sources) — a genuine business decision for later, not a blocker now. Okta directly ruled out as a cheaper alternative (same company as Auth0, worse free-tier situation — 30-day trial only, $1,500/year minimum after).
- **Enrollment model, confirmed**: RIA-initiated, not Hollisworks-invite-initiated. The RIA gives Hollisworks staff a list (email + role); Hollisworks creates a pending record; the RIA separately enrolls that person in their own IdP on their own timeline. Matching is by **exact email address** (SAML NameID, `emailAddress` format — zero extra IdP configuration burden). **No match = hard reject, always**, never auto-create.
- **`admin.hollisworks.com`** is the reserved, staff-only login subdomain — deliberately kept OUT of the `organizations` table (a special-cased resolver route, not a seed row) to avoid entangling real-client resolver logic with this one special case.
- Auth0 URL configuration convention: **explicit listing, not wildcards** — Auth0's own docs caution against wildcards in production, and independent reports describe real, documented bugs with wildcard support specifically for "Allowed Web Origins."

### 7.3 · What's actually built and proven working right now
- `hollisworks.com` (bare) correctly shows the real Hollisworks marketing page (full HTML/CSS/JS provided and integrated faithfully).
- `2ndactcapital.com` correctly shows 2nd Act's own, separate marketing page (regression-fixed after initially breaking).
- The shared firm-search interstitial: both Login and Enroll buttons on the Hollisworks marketing page route to one search flow, remembering original intent; fuzzy-matches against `organizations.name`; redirects to the org's real, explicitly-**stored** `login_url`/`enroll_url` (not constructed by convention — this is what allows a future custom-domain client with zero special-case logic elsewhere). Ambiguous or no match: **asks the user to clarify/retry, never guesses, never shows a pick-list** (explicit design decision).
- Typing "Hollisworks" itself into the search resolves to `admin.hollisworks.com`'s login/enroll paths (a narrow, explicit special case in the matching logic).
- A real contact-form endpoint (`POST /api/v1/marketing/contact`) persists submissions.
- **`admin.hollisworks.com` login is fully working end-to-end**, confirmed via real, live browser testing (not just automated tests) — see §7.4 for the debugging chain that got it there.
- The new Hollisworks Auth0 tenant is correctly wired as a **second, additive** auth path, used ONLY for `admin.hollisworks.com` — 2nd Act's own existing login is proven, repeatedly, to be completely unaffected (`lib/auth0.js` confirmed byte-identical to git HEAD after the integration work).

### 7.4 · The admin.hollisworks.com debugging chain — six real, sequential issues, all resolved

| # | Issue | Type | Fix |
|---|---|---|---|
| 1 | Tenant/domain selection silently fell back to 2nd Act's Auth0 tenant (SDK's own `domain ?? AUTH0_DOMAIN` default) | Code bug | Fail-loud `resolveAuthTenantForHost()` |
| 2 | Callback/Login URIs in Auth0's dashboard were missing the app's real `/auth/` route prefix | Dashboard config | Corrected to `https://admin.hollisworks.com/auth/callback` etc. |
| 3 | `appBaseUrl` silently fell back to the shared, 2nd-Act-scoped `APP_BASE_URL` env var | Code bug | Host-derived `hollisworksAppBaseUrl()` |
| 4 | `audience` (both frontend AND a separately-broken backend default) silently fell back to 2nd Act's API audience | Code bug | Found via a comprehensive field-by-field audit (not reactive one-off fixes) — 22/22 assertions, every field proven with explicit before/after |
| 5 | The audience value (`https://api.hollisworks.com`) was never registered as a real API in the Hollisworks Auth0 tenant | Dashboard config | Created a real API identity: Applications → APIs → Create API |
| 6 | The real Application was never authorized for **User-delegated** access to that API (a separate axis from Client/M2M access — easy to configure the wrong one) | Dashboard config | Application → APIs tab → granted User-delegated Access |

**Confirmed, real, complete login now works.** Every application-code layer (tenant selection, base URL, audience resolution, fail-loud guards) is proven correct through live testing, not just automated assertions.

### 7.5 · Not yet built
- Full SAML federation of 2nd Act's tenant *into* the Hollisworks broker tenant (the actual Enterprise Connection, "SAML2 Web App" addon on 2nd Act's side) — genuinely large, was explicitly paused ("pause on this thread, pick up in the am") before the Auth0 tenant setup itself needed real-time debugging attention.
- The "back door" (password fallback for RIA clients without real SAML) — deliberately deferred to later, per-org-toggle-vs-universal decision also deferred.
- Per-tenant SAML setup automation — deliberately manual for now (Auth0 Management API automation is premature until there's real, recurring multi-client demand).
- A minor, non-urgent follow-up: `hollisworks.com/login` (typed directly, bypassing the real button) falls through to 2nd Act's tenant — confirmed **not** a bug (real users reach the correct flow via the actual button), but worth guarding eventually to avoid confusing anyone who bookmarks or types the raw URL.

---

## 8 · Other completed fixes this session

- **AI sidebar missing count/filter queries**: found (via real user testing — "how many investments," "how many entities in CT" both correctly said no tool existed) and fixed for entities + investments, reusing existing endpoints and the same visibility-composition pattern as the Ownership Graph/semantic search. **Same gap confirmed to also exist** for SPVs, workflow runs, deals-by-attribute, member investments, documents, and task/notification counts — deliberately not fixed yet, a real, honest follow-up list.

---

## 9 · Backlog — ready to build, blocked on external input

- **Member Business Registration & EIN Capture** — full spec already written (sole-proprietorship path, no SSN ever stored, guided wizard + staff maker-checker verification). **Blocking gate: written carrier confirmation that a sole-proprietor EIN is accepted** — not yet obtained. Parked, not forgotten.

---

## 10 · Real security incident, resolved

**A live, plaintext production database password (`app_service` role) was accidentally pasted into this chat.** Immediately rotated directly via Supabase (`ALTER ROLE ... WITH PASSWORD`), new value provided once, Render updated and redeployed, chat message deleted. No indication of any actual unauthorized access — handled as a precaution, not in response to confirmed compromise.

---

## 11 · Process / tooling notes worth preserving

- **Direct Supabase MCP access is available in-chat** — Part 1 SQL should be applied directly (`apply_migration`) rather than handed to Joe to paste manually. **Every new table must get its RLS policy in the SAME migration it's created in** — never deferred, given RLS is now genuinely enforced in production.
- `run_sprint.sh`'s refresh-schema step has a retry-up-to-3× patch (was failing intermittently on `exit 124`) and its wall-clock leg cap was raised from 30 to 90 minutes (a fully correct, 11/11-passing sprint was once killed by the old cap before it could commit — real work was recovered manually from disk, not lost, but the cap was raised to prevent recurrence).
- **A sprint's own "report before proceeding" instruction can be misread as "stop and wait for a human"** — for any sprint expected to run unattended, prompts must explicitly state that discovery-reporting is followed immediately by continued work in the same response, not a checkpoint requiring reply.
- Generated documents (Word docs) are useful deliverables but are **not** a durable system of record on their own — the generating sandbox can reset, losing the ability to further edit them. **This file (committed to git) is the actual fix for that.**
- Two different, unrelated permission systems exist in this codebase (`services/rbac.py` and `services/permissions.py`) — a fix to one does not imply the other is safe; verify each independently if touching either.
