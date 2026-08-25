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

## 7b · Completed — EDGAR reference corpus (fetcher + storage + HTML extraction)

**A GLOBAL, non-org-scoped harvester of public SEC filings** — 424B2 (prospectus supplements) and FWP (free writing prospectuses). Deliberately **not** Chancery: no `documents` row, no drop path, no classifier call (form type is already known from EDGAR metadata), no entity/member linkage, and no writes to `portfolio.securities_global`. This sprint stores **raw filings and plain text only** — term extraction (payoffs, barriers, caps) is a later sprint and nothing here parses a term.

**R2 access — the hard gate PASSED.** The prior sprint's blocker is gone: `R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET_NAME` are now present in `apps/api/.env`, and a real write → read-back → delete round-trip against `hollisworks-docs` succeeded before a single line of fetcher code was written. Nothing was mocked or stubbed.

| Piece | Detail |
|---|---|
| `portfolio.reference_filings` | Global table with **no `org_id` column at all**. RLS enabled with the four-policy global shape copied from `public.permissions` (SELECT `USING (true)`; INSERT/UPDATE/DELETE gated on `app.is_super_admin`) — four separate policies, not one `FOR ALL`. Unique on `(accession_number, primary_document)`. `retention_classification` is `NOT NULL DEFAULT 'public_reference'` with a CHECK pinning the value — a deliberate explicit classification so the retention system, when built, never finds a NULL to guess at. |
| `services/edgar_fetch.py` | Quarterly full-index strategy (`.../full-index/{YYYY}/QTR{n}/master.idx`), not the full-text search API — ~30 index files cover 2019-present versus a quarter-million search calls. SEC compliance is enforced, not best-effort: `EDGAR_USER_AGENT` raises loudly when unset (no silent default), a shared limiter caps requests at 10/s with a real `asyncio.sleep`, and 429/5xx back off exponentially honouring `Retry-After`. `httpx` reused (already the declared HTTP dep); no second client added. |
| R2 layout | New `reference/edgar/{cik}/{accession}/{document}` prefix — deliberately non-org-scoped, alongside the existing tenant prefixes `deals/`, `entity-docs/`, `spvs/`. `services/storage.py` hardcodes no org segment; keys are entirely caller-supplied. Added `download_bytes()` for server-side reads of objects the API itself owns (tenant retrieval stays presigned-only). |
| Idempotency | `store_filing` upserts on `(accession_number, primary_document)` and compares the stored `content_hash` before uploading — re-running produces no duplicate row and re-uploads no identical bytes. Proven by the verifier. |
| `services/html_text.py` | 424B2 filings are HTML, so **Textract is not involved**. Chancery's `_strip_html` was discovered to be an email-body helper only (a regex tag-stripper, no offsets), so a general extractor was added. Built on the stdlib `html.parser`, **not** lxml/BeautifulSoup/selectolax: the requirement is that a later sprint can point at the exact span a term was read from, and none of the tree parsers expose character offsets for text nodes. Every text node is stored as `(text_start, text_end, raw_start, raw_end)` — positional data is preserved, not discarded the way the Textract path currently discards Geometry. |
| Offset map storage | Written to R2 as a derived sibling key `{r2_key}.offsets.json` rather than a Postgres column — one entry per text node runs to tens of thousands per filing, and it is only read when tracing a term back to its source. |
| Prefilter | 424B2 over-selects heavily (any shelf takedown uses it). A cheap deterministic keyword prefilter runs on the extracted text. **A flat two-hit threshold was measured and corrected**: it dropped an "Enhanced Return Notes linked to a Basket" FWP whose only hit was *participation rate*. Final rule — any one STRONG term (`barrier`, `buffer`, `autocall`, `contingent coupon`, `participation rate`, `initial level`) passes; the weak term `underlying` only counts toward a two-hit total. Failures are marked `skipped` and **retained with their text** — the negative set is what makes a later precision measurement possible. |

**Bounded sample run — 2025 QTR1, capped at 200 filings** (deliberately not a backfill). Actual numbers:

- Index rows for the quarter: **43,871** — 37,678 × 424B2, 6,193 × FWP.
- Selected by deterministic stride across the whole quarter (not the head, which would return one week and a handful of filers): **200** — 171 × 424B2, 29 × FWP.
- Fetched and stored: **200/200, zero failures**, 63,028,018 bytes.
- Prefilter: **165 passed** (146 × 424B2, 19 × FWP), **35 skipped** (25 × 424B2, 10 × FWP) — all retained.
- Longest extracted text: 257,109 characters. `file_number` (`333-…` shelf linkage) resolved for the sampled filings.

**Verification — `apps/api/scripts/verify_edgarcorpus.py`, 16/16 PASS.** Asserts real values: no `org_id` column; exactly four policies whose commands are SELECT/INSERT/UPDATE/DELETE; global read succeeds from a **non-bypass** session with `app.org_id` genuinely unset (200 rows visible); a **negative** test proves an insert without `app.is_super_admin` is rejected and leaves no row; two identical `store_filing` calls produce one row with an unchanged hash; a sample-run object is fetched back through `services/storage.py` with its byte length matching the stored `byte_size`; a stored offset map is re-applied to the re-downloaded raw HTML and lands on a real, tag-free span.

**Environment finding — `APP_SERVICE_DATABASE_URL` in `apps/api/.env` no longer authenticates** (`InvalidPasswordError`), almost certainly stale since the password rotation in §9. `postgres` was a member of `app_service` `WITH ADMIN OPTION` but **without the SET option**, so verify scripts could not assume the role either and had been skipping real RLS enforcement checks. Fixed with `GRANT app_service TO postgres WITH SET TRUE, INHERIT FALSE` — this grants nothing `postgres` could not already grant itself, and it makes non-bypass RLS testing possible for every future sprint. The verifier prefers `APP_SERVICE_DATABASE_URL`, falls back to `SET ROLE`, and **reports which path it used** rather than hiding the fallback. The local `.env` value should still be refreshed.

**Not built, by design**: term extraction, any write to `portfolio.securities_global` (`securities_global`, `securities_global_identifiers`, `securities_global_prices`, `securities_global_relationships` all confirmed deployed and untouched), Chancery classification, entity linkage, and a full historical backfill.

---

## 7c · Completed — Payoff DSL (versioned note-terms schema + field registry)

**Schema only.** This sprint defines *where* extracted structured-note terms will live and *what* is permitted to be extracted. It contains **no extraction logic, no LLM calls, and no read of `reference_filings.extracted_text`**. Term extraction plus the hazard-field verification path is the next sprint and depends on this one. `document_field_corrections` was deliberately not touched — corrections polymorphism is its own sprint and is a prerequisite for extraction, not for this.

### The versioning decision — RECORDED SO IT CANNOT DRIFT BACK

**`portfolio.securities_global_note_terms` is VERSIONED, not a 1:1 extension of `securities_global`.** Earlier design drafts specified 1:1; that was wrong and was corrected before build. One `global_security_id` legitimately holds **many** terms rows over its life:

- **preliminary** terms from the FWP,
- **final** terms from the 424B2 that priced it,
- occasionally a **restated** / corrected 424B2.

These must not collapse into one row. The gap between offered and final terms — a barrier that got worse at pricing, a cap that shrank — **is itself the signal** the comparison model exists to surface. Overwriting the preliminary row destroys it. Accordingly the uniqueness key among current rows is `(global_security_id, terms_status, reference_filing_id)` and is **deliberately not unique on `global_security_id` alone**. Any future change that adds such a constraint is a regression, and `verify_notetermsdsl.py` asserts against it from both directions.

| Piece | Detail |
|---|---|
| `portfolio.securities_global_note_terms` | Global reference data extended from public filings: **no `org_id` column**. FK to `securities_global(id)`, nullable FK to `reference_filings(id)`. Bitemporal columns copied exactly from `securities_global` (`valid_from`/`valid_to`/`system_from`/`system_to`, `timestamptz`, the two `_from` columns `NOT NULL DEFAULT now()`). Every monetary/percentage column is `numeric` — never float — read as `Decimal` in Python. CHECKs pin `terms_status`, `product_archetype`, `protection_type`, `basket_type`, `return_basis`, `autocall_frequency`, `extraction_confidence`. |
| Uniqueness | `sec_global_note_terms_current_unique` on `(global_security_id, terms_status, reference_filing_id)` `WHERE system_to IS NULL AND valid_to IS NULL`, with **`NULLS NOT DISTINCT`** (PG 17.6). Without that clause two current rows with the same security + status and *no* source filing would compare as distinct and un-sourced duplicates would slip through. |
| `portfolio.note_terms_field_registry` | **19 rows seeded**, one per extractable term column. Excluded by design: `id`, the four bitemporal columns, `global_security_id` / `reference_filing_id` (linkage, not extracted from prose), `field_status` (registering it would be self-referential), and `extraction_confidence` / `source_char_start` / `source_char_end` (provenance about the row, not a term of the note). `applies_to_archetypes text[]` is `NULL` for universal fields; an empty array is rejected by CHECK. |
| Six hazard fields | `protection_type`, `basket_type`, `return_basis`, `is_decrement_index`, `autocall_frequency`, `terms_status` — flagged `hazard_field = true`. These are the misreads that are **catastrophic *and* arithmetically clean**: the wrong answer is as plausible as the right one, so nothing downstream trips. A 10% *buffer* absorbs the first 10% of loss; a 90% *floor* caps loss at 10% — opposite payoffs, and both get marketed as "10% downside protection". `basket` (weighted average) vs `worst_of` (single worst performer) is the same trap. The extraction sprint gives these six their own verification path. |
| The four-state model | A NULL term is three different facts wearing one hat. `coupon_barrier_pct` is NULL on a principal-protected note because it is **inapplicable**; NULL on an unprocessed autocallable because it is **unresolved**; NULL on a note whose barrier table defeated the parser because extraction **failed**. `field_status jsonb` carries this per-row, per-field: `extracted` \| `not_applicable` \| `extraction_failed` \| `not_in_template`. `applies_to_archetypes` answers the static half; `field_status` answers the per-row half. |
| **Stated limitation — jsonb enum is NOT enforced in Postgres** | A CHECK constraint validating per-key enum values inside `field_status` would have to iterate the object's values, which is not `IMMUTABLE`-safe. So it is enforced at the **application layer only**, by `models.note_terms.validate_field_status()`, which `NoteTerms.__post_init__` also calls. **The database will accept an invalid state string if that function is bypassed.** This is documented in the migration and the model docstring rather than silently skipped — every writer must call the validator. |
| `apps/api/models/note_terms.py` | Shapes only, no extraction functions. `NoteTerms` and `NoteTermsFieldRegistryEntry` dataclasses; `Decimal` on every numeric field; controlled vocabularies kept in lockstep with the CHECK constraints; `validate_field_status()` raising `FieldStatusError`. Underlyings are deliberately absent from the model — they hang off `securities_global_relationships` (`relationship_type='underlying_of'`, `link_state` resolved/unresolved/ambiguous), because a worst-of basket has N underlyings and some never resolve, which no direct FK could express. |
| RLS | Both tables carry the **exact four-policy global shape read live off `securities_global`**: `{table}_global_read FOR SELECT USING (true)`; `{table}_super_admin_insert / _update / _delete` gated on `current_setting('app.is_super_admin', true) = 'true'`. Four separate policies, never one `FOR ALL`. No `NULLIF` guard is used or needed — these read a text flag, not an org uuid, so the `''`-cast hazard does not apply. `SELECT/INSERT/UPDATE/DELETE` granted to `app_service`. |

**Verification — `apps/api/scripts/verify_notetermsdsl.py`, 24/24 PASS**, idempotent across consecutive runs, teardown at start and end.

The **versioning proof** is the core assertion: a `preliminary` row and a `final` row are inserted for one `global_security_id`, both persist as separate current rows, and the `protection_pct` delta (10.00 → 8.00) is asserted to survive. From the other direction, a true duplicate `(security, status, filing)` is asserted **rejected** by name, while a third differing `terms_status` is asserted **accepted** — three current rows sharing one security. Also asserted: no `org_id` on either table; exactly four policies each with commands SELECT/INSERT/UPDATE/DELETE; all seven monetary columns are `numeric`; every registry `field_key` resolves to a real column; exactly six `hazard_field` rows matching both the specification and `models.HAZARD_FIELD_KEYS`; the validator accepts all four states and raises on a fifth; and a **negative** test on each table proving an insert without `app.is_super_admin` is rejected.

**`APP_SERVICE_DATABASE_URL` is now required, with no fallback.** §7b's verifier preferred that credential but fell back to `SET ROLE app_service` when it failed — which means an RLS regression could pass under a differently-privileged session. This verifier connects with it, asserts `rolbypassrls = false`, and **aborts loudly** if it cannot. The credential in `apps/api/.env` **authenticates correctly as of this sprint**, closing the environment finding recorded in §7b.

**Not built, by design**: any extraction logic or LLM call, any read of `reference_filings.extracted_text`, any change to `document_field_corrections`, hazard-field verification, and any API router or UI over these tables.

---

## 7d · Completed — corrections polymorphism

**Schema only, zero application code changed.** `document_field_corrections` can now receive a correction against a **non-document** target — a mis-extracted structured-note term, a proposed template — not just an org-scoped document. §7c deferred this deliberately; it is a prerequisite for the term-extraction sprint. Nothing yet *produces* a note-terms correction: this sprint only makes the table able to receive one.

**A parallel corrections table was rejected**, because two correction systems guarantee drift. One table, one discriminator.

| Piece | Detail |
|---|---|
| Nullability | `document_id` and `org_id` both dropped `NOT NULL`. Their FKs to `documents(id)` / `organizations(id)` are retained and still enforced when the columns are populated. |
| `target_type` / `target_id` | `target_type text NOT NULL` (CHECK: `document` \| `note_terms` \| `template_proposal`), `target_id uuid NOT NULL`. Backfilled `('document', document_id)` before the `NOT NULL` was set. Indexed as `idx_doc_field_corr_target (target_type, target_id)`. |
| **No FK on `target_id`, on purpose** | It resolves to `documents(id)`, `portfolio.securities_global_note_terms(id)`, or a proposed-template row depending on `target_type`. One FK cannot span three tables. Referential integrity for non-document targets is an **application-layer** responsibility, documented in a `COMMENT ON COLUMN` on the column itself so the next reader finds the reason at the schema, not in a commit message. |
| The pairing CHECK | `document_field_corrections_document_pairing_chk`: a `document` row still requires **both** `document_id` and `org_id` (unchanged org-scoped behaviour); any non-document row must have `org_id IS NULL`. Global reference data has no tenant, and the constraint makes that unfalsifiable rather than conventional. |
| **Backward compatibility — the addition not in the sprint spec** | A bare `target_type NOT NULL` would have broken **every existing writer** (`submit_field_correction`, `submit_classification_correction`, `eval_correction_loop._seed_correction`, `verify_chancery6/8`), none of which mention the new columns — and fixing them by editing call sites is exactly the scope creep this sprint forbade. Instead: `target_type DEFAULT 'document'` plus a `BEFORE INSERT` trigger that fills `target_id` from `document_id` **only when it is NULL**. Every pre-existing INSERT statement remains byte-for-byte valid and lands correctly typed; an explicitly supplied `target_id` is never rewritten. **Zero call sites were touched** — asserted by the verifier against `origin/main` and the working tree. |
| RLS — both shapes coexist | The existing `document_field_corrections_org_isolation` (PERMISSIVE, `FOR ALL`, org GUC with `NULLIF`) is **untouched**. Added alongside it, scoped to `target_type <> 'document'`, is the same four-policy global shape §7c read off `securities_global`: `_global_read FOR SELECT USING (target_type <> 'document')` plus `_global_super_admin_insert / _update / _delete`. Permissive policies OR together, so a `document` row's cross-org invisibility is arithmetically unchanged while a `note_terms` row is globally readable. |
| The document retrieval path is untouched twice over | `correction_retrieval.get_relevant_corrections` already guarded `if org_id is None: return []` and filters `c.org_id = $1` **in the SQL itself**. A global row (`org_id IS NULL`) can therefore never satisfy that predicate — the new rows are invisible to the document loop by construction, not by policy. Asserted explicitly. |

**Verification — `apps/api/scripts/verify_correctionspoly.py`: 19 PASS, 0 FAIL, 0 flagged** (green whenever `ANTHROPIC_API_KEY` is live and funded — first on 2026-08-22 and again on the fifth re-verification, 2026-08-23; the three intervening runs scored 17/2 or 17/1 with every failure being the unrunnable DeepEval gate, never a schema or RLS defect)**.** Idempotent (run twice, identical result), teardown at start and end, zero leftover rows. Proven: both columns nullable; `target_type`/`target_id` `NOT NULL` with the FK absent and the reason commented; all three legacy INSERT shapes still succeed unmodified and land as `('document', document_id)`; both CHECK rejections asserted **by constraint name** (document row with NULL `document_id`, document row with NULL `org_id`, note_terms row carrying an `org_id`, unknown `target_type`); a real `note_terms` correction (`protection_type: floor → buffer` — the sprint's motivating misread) inserted against an actual `securities_global_note_terms.id` with `org_id NULL`; and under the **real non-bypass `app_service` role**, that global row is readable with no org context while an ORG_B document correction stays invisible to an ORG_A session that still sees its own three.

**Honest note on the backfill**: `document_field_corrections` held **0 rows** at migration time, so the backfill `UPDATE` covered nothing. The verifier reports that plainly rather than claiming a vacuous pass, and proves the same rule through the legacy-INSERT path instead.

### DeepEval regression — CLEARED 2026-08-22, re-measured on the polymorphic schema

The gate that held this sprint is now closed. `ANTHROPIC_API_KEY` became available, and `apps/api/scripts/eval_correction_loop.py` — the exact script behind §7's on-record figure — was re-run against the **same** self-contained `AMBIGUOUS_CASES` fixtures (seeded inside a rolled-back transaction), with the polymorphic `document_field_corrections` schema live:

| | Cases | Accuracy |
|---|---|---|
| WITHOUT correction retrieval | 1/3 | **33.3%** |
| WITH correction retrieval | 3/3 | **100.0%** |
| Δ | | **+66.7 pts (improvement)** |

Per-case, identical to the original run: `accreditation` and `llc_formation` both **flipped wrong → right** by the retrieved correction; `estate_plan` was already correct without it and did not regress. This is an **exact reproduction** of the on-record 33.3% → 100.0% (+66.7 pts) — not merely "near" it — so the schema change demonstrably did not disturb the document correction-retrieval path.

The measurement matters because the seeding INSERT in `eval_correction_loop._seed_correction` names **none** of the new columns. Its passing is a live end-to-end proof that the `DEFAULT 'document'` + `BEFORE INSERT` trigger keeps unmodified legacy writers working through the real classifier path, not just in the verifier's synthetic inserts.

Assertion 9 prints these figures inline in the verifier's output and fails if WITH-retrieval accuracy is not at or near 100%. **No substitute measurement was ever fabricated** while the gate was blocked — it was reported as a hard FAIL rather than a SKIP, on the reasoning that a regression check which passes without running is worse than no check at all.

#### Re-verification later on 2026-08-22 — schema green, DeepEval gate unrunnable (no API credit)

The verifier was run again the same day. **17 PASS, 2 FAIL, 0 flagged**, and the two failures are both the DeepEval gate, from an environment problem rather than a defect:

- **Assertions 1–8c and 10 all still PASS** — nullability, the `target_type`/`target_id` columns, both CHECK rejections by constraint name, the real `note_terms` insert, the three legacy INSERT shapes, `get_relevant_corrections` returning both shapes (classification hits=2, extraction hits=1), global read under `app_service` with no org context, cross-org invisibility of document rows, and all 8 call-site files byte-for-byte unmodified. The polymorphic schema and its RLS are intact as deployed.
- **Assertions 9 / 9b FAIL as NOT MEASURED.** Every Anthropic call returned `400 invalid_request_error — "Your credit balance is too low to access the Anthropic API"`, so `call_claude_json` exhausted and the classifier returned `None` on all three cases. Both arms scored 0/3. **This is not evidence of a retrieval regression** — the 33.3% → 100.0% figure above stands from the run taken while credit was available, and assertion 4's structural check confirms the retrieval SQL path still returns both correction shapes. Restore Anthropic credit and re-run to re-take the measurement.

**Verifier hardening (the only code change in this pass).** The re-run exposed a real hole in the load-bearing gate: with the API dead, both arms score 0%, so assertion 9 failed with the misleading label "WITH-retrieval accuracy COLLAPSED" while assertion **9b passed vacuously** (0.0% ≤ 33.3%) — precisely the "check that passes without running" this sprint's design forbids. `verify_correctionspoly.py` now detects the no-measurement condition (both predictions `None` on every case, which a live classifier never produces) and reports it as an explicit **NOT MEASURED** hard FAIL on *both* checks, with the cause named. No application code, schema, migration or fixture was touched; assertion 10 still reports zero scope creep.

#### Third re-verification, 2026-08-22 — 17/2 unchanged, plus the root cause of the missing key

Re-run once more with nothing re-applied: **17 PASS, 2 FAIL, 0 flagged** — byte-identical to the run above, same two DeepEval assertions, same `400 invalid_request_error` credit-balance response. The hardening worked as designed: both 9 and 9b reported **NOT MEASURED** as hard FAILs and named the cause, so the outage could not clear the gate vacuously.

Root cause of the "key not set" half of the problem, which earlier passes had misattributed to non-interactive shells: **`~/.bashrc` line 125 reads `export ANTHROPIC_API_KEY =sk-ant-...`, with a space before the `=`.** Shell assignment permits no space, so bash exports the name *unset* and then tries to execute `=sk-ant-...`. The key is therefore set in **no** shell, interactive included. The file was not edited — it is outside the repo and outside this sprint's scope. **Two independent fixes are needed to clear the gate: (1) delete that space in `~/.bashrc`; (2) restore Anthropic account credit.** Fixing only (1) still yields 17/2 — the key already reaches the API today and is rejected on billing, not auth.

#### Fourth re-verification, 2026-08-22 — 17 PASS / 1 FAIL; the key is now gone from the machine entirely

Re-run again with nothing re-applied: **17 PASS, 1 FAIL, 0 flagged.** Assertions 1–8c and 10 are unchanged and green — the polymorphic schema, both CHECK constraints, the real `note_terms` insert, the four-policy RLS shape, cross-org invisibility of document rows, and all 8 call sites byte-for-byte unmodified.

The single failure is the DeepEval gate, and **the cause has changed**: `ANTHROPIC_API_KEY` is no longer present anywhere on this machine. The malformed `export ANTHROPIC_API_KEY =sk-ant-...` line described above is **no longer in `~/.bashrc` at all**, and the key does not appear in `~/.profile`, `apps/api/.env`, `apps/web/.env.local`, or the repo root `.env`. So the previous pass's fix (1) is moot — there is nothing left to un-malform; the key must be re-added. Consequently the run fails **once** (key-absent) rather than twice (both arms scoring 0/3 on billing 400s), which is the hardened verifier behaving correctly: absence is a hard FAIL, never a SKIP.

**DeepEval figure from this run: NOT MEASURED** — no substitute was fabricated. The on-record **33.3% → 100.0% (+66.7 pts)**, exactly reproduced on the polymorphic schema on 2026-08-22 while credit was available, remains the standing measurement. Assertion 4 independently re-confirms the retrieval SQL path is intact (`get_relevant_corrections`: classification hits=2, extraction hits=1), so there is no evidence of regression in the path this sprint was forbidden to touch. **To clear the gate: re-add `ANTHROPIC_API_KEY=sk-ant-...` (no space around `=`) to `~/.bashrc` or `apps/api/.env`, ensure the Anthropic account has credit, then re-run `python3 scripts/verify_correctionspoly.py` from `apps/api`.**

#### Fifth re-verification, 2026-08-23 — 19 PASS / 0 FAIL / 0 flagged; the DeepEval gate is CLEARED again

`ANTHROPIC_API_KEY` is present in the shell environment again and, unlike the second and third passes, the account is funded — a direct `claude-haiku-4-5-20251001` smoke call returned normally before the verifier was run, so the key was confirmed live rather than merely present. Nothing was re-applied: no schema change, no migration, no application code, no fixture edit. The whole sprint was re-verified against the already-deployed state.

**DeepEval figure from this run — measured, not assumed:**

| | Cases | Accuracy |
|---|---|---|
| WITHOUT correction retrieval | 1/3 | **33.3%** |
| WITH correction retrieval | 3/3 | **100.0%** |
| Δ | | **+66.7 pts (improvement)** |

Per-case, identical again: `accreditation` and `llc_formation` both **flipped wrong → right** by the retrieved correction; `estate_plan` was already correct without it and did not regress. That is the on-record **33.3% → 100.0% (+66.7 pts)** reproduced exactly — the second independent exact reproduction on the polymorphic schema, and the fourth run of this verifier overall. The schema change has not disturbed the document correction-retrieval path.

The rest of the run is unchanged and green: both columns nullable; `target_type`/`target_id` `NOT NULL` with no FK and the reason in a `COMMENT ON COLUMN`; all four CHECK rejections asserted by constraint name; the real `note_terms` correction (`protection_type: floor → buffer`) inserted with `org_id NULL` against a live `securities_global_note_terms.id`; global read under the non-bypass `app_service` role with no org context; cross-org invisibility of `document` rows intact (an ORG_A session sees 0 of ORG_B's and 3 of its own); zero leftover rows after teardown; and all 8 call-site files byte-for-byte unmodified versus `origin/main` **and** the working tree — **zero scope creep, no call site was touched to accommodate this change**.

A live introspection of the deployed table confirmed the migration is fully in place as recorded in `apps/api/migrations/correctionspoly_document_field_corrections.sql`: 12 columns with `target_type text NOT NULL DEFAULT 'document'` and `target_id uuid NOT NULL`, both CHECK constraints, the four retained FKs, `idx_doc_field_corr_target`, the `document_field_corrections_default_target_trg` BEFORE INSERT trigger, and five RLS policies — the untouched `_org_isolation` (`FOR ALL`, `NULLIF` on the org GUC) plus the four-policy global shape scoped to `target_type <> 'document'`.

Still unchanged, by design: nothing yet **produces** a `note_terms` correction — the table is schema-ready only. The next sprint (note-terms extraction) is what will write the first one. **Superseded by §7e**, which now writes them: 29 rows and counting, all machine-generated hazard-ensemble disagreements with `corrected_by NULL`.

---

## 7e · Completed — note-terms extraction (LLM extraction + six-field hazard ensemble)

**The first sprint that actually reads `reference_filings.extracted_text` and produces terms.** 424B2 / FWP filings in → one `portfolio.securities_global_note_terms` row each, plus unresolved underlying edges. Deterministic validators run around an LLM extraction pass; a second, different model re-reads only the six hazard fields, and any disagreement flags the row for review.

**Explicitly NOT built** (each was named out of scope and each stayed out): underlying **resolution**, comparability scoring / percentiles, staff UI, template induction / clustering, and any change to `securities_global_note_terms`' schema.

### Task 5 — the bounded run (50 filings, real numbers)

Capped at 50 deliberately: this proves the pipeline and produces the evidence a scaling decision needs. Input population is **165** filings (`extraction_status='extracted'`, non-fixture, ≥2000 chars) out of 166 `extracted` / 35 `skipped`.

| Measure | Result |
|---|---|
| Filings processed / rows created / failed | **50 / 50 / 0** |
| `field_status` distribution (950 slots = 50 rows × 19 registry fields) | `extracted` 749 (78.8%) · `not_applicable` 144 (15.2%) · `not_in_template` 43 (4.5%) · `extraction_failed` 14 (1.5%) |
| Hazard ensemble genuinely measured (2 distinct models) | **50/50** |
| Rows with ≥1 hazard disagreement | **22 (44.0%)** |
| Disagreements by field | `protection_type` 16 · `autocall_frequency` 5 · `return_basis` 2 · `terms_status` 2 · `is_decrement_index` 1 · `basket_type` 1 |
| Validator hard-failure rate | **9/50 (18.0%)** — `cik_matches_filer` 6, `tenor_consistent` 3; 0 warning-only rows |
| `extraction_confidence` | `needs_review` 26 (52.0%) · `high` 24 (48.0%) |
| Source spans populated | 50/50 |
| Unresolved underlying edges | 93 |

**The 44% disagreement rate is the sprint's most useful output, and `protection_type` is 16 of the 22.** That is exactly the field the hazard list was built around — buffer absorbs the first *n*% of loss, floor caps total loss at *n*%, opposite payoffs, and both are marketed as "*n*% downside protection". Every one of those 16 rows passed all five arithmetic validators. Nothing except the ensemble would have caught them. **Treat the current per-row output as review-grade, not trusted**, and do not scale past 50 until the `protection_type` prompt is sharpened and re-measured.

### What was built

| Piece | Detail |
|---|---|
| `services/note_terms_validators.py` | Five deterministic checks, each returning `(ok, reason)`: `cusip_checksum` (mod-10 Luhn variant, verified against 4 real CUSIPs), `cik_matches_filer` (issuer read from the prose vs the EDGAR registrant — free ground truth; a 17-CIK stem map seeded from the actual corpus, unknown CIK returns "not contradicted, not verified"), `barrier_price_consistent`, `autocall_le_coupon_barrier`, `tenor_consistent`. `Decimal` throughout. A validator that **cannot** run returns `ok=True` with a reason saying so — absent data never fabricates a failure. The module docstring states in full that **these cover arithmetic only and cannot catch a single hazard field**, so nobody later mistakes them for a sufficient gate. |
| `services/note_terms_extraction.py` | The pipeline. Windowing (head 12k + densest keyword window 24k — the median filing is 69k chars and the terms occupy a few thousand of them); primary Haiku extraction constrained to the live registry's field keys with out-of-vocab enum answers discarded rather than crashing the CHECK; four-state `field_status` for **every** registry field; validators; hazard ensemble; bitemporal-safe persistence. `extract_underlying_mentions()` writes each mention verbatim with `link_state='unresolved'`, `to_global_security_id NULL`. |
| `services/note_terms_corrections.py` | Task 4. `log_note_terms_correction()` — one place, not inlined at call sites — writing `target_type='note_terms'`, `target_id`, `org_id NULL`, setting `app.is_super_admin` inside its own transaction. Plus `log_hazard_disagreement()` and `get_note_terms_corrections()`. |
| `scripts/run_note_terms_extraction.py` | The bounded runner (`--limit`, `--force`). Exits non-zero when 0 rows are created, and refuses to run at all without `ANTHROPIC_API_KEY` rather than reporting a vacuous success. |
| **Real offsets, not hallucinated integers** | Models are unreliable at reporting character offsets and reliable at copying a phrase. So the model returns a **verbatim quote** per field and the code locates it in the full text (exact match, then a whitespace-normalised index with a map back to real positions). `source_char_start/end` is therefore a measured fact. Populated on 50/50 rows. |
| **Idempotent by default** | A filing that already has a current terms row is returned untouched with no model call. `--force` supersedes bitemporally (close the old row, insert a new one — Rule 3, never an in-place update). |

### The ensemble can silently collapse — guarded

The platform fallback chain is `["claude-haiku-4-5-20251001"]`, the **same model as the primary**. If Sonnet were unreachable, `call_claude_json` would transparently retry on Haiku and return a good answer — and the "two model" ensemble would become one model agreeing with itself, reporting 100% agreement and upgrading every row in the run to `high` confidence while having checked nothing. This is the same failure shape as §7d's vacuous DeepEval pass.

So the model that actually served each hazard call is read back from `ai_decision_log` and compared to the primary. If it is not independent, the six fields are marked **not cross-checked** and confidence drops to `low` — never `high`. Confirmed genuinely independent on **50/50** rows (`claude-haiku-4-5-20251001` vs `claude-sonnet-4-6`, `fallback_used=false`).

### Two schema gaps found and reported, not silently worked around

Both were explicit STOP gates in the sprint prompt.

**1 · `reference_filings.extraction_status` collision — real. Task 3 step 7 was NOT implemented.** That column already means "did the HTML yield text, and did it pass the prefilter", and its CHECK permits exactly `pending|fetched|extracted|failed|skipped` — **no value can express "terms were extracted"**. Writing `failed` on a terms failure would tell the corpus pipeline the HTML produced no text, and any write would corrupt the prefilter positive/negative set §7b keeps precisely so precision can be measured later. **Resolution: the terms pipeline never touches that column.** Progress is derived — a current terms row exists with that `reference_filing_id` (`filings_with_terms_extracted()`). One column, one meaning. Asserted three ways by the verifier.

**2 · The hazard disagreement record has nowhere to live on the terms row.** `securities_global_note_terms` has exactly one jsonb column, `field_status`, and `validate_field_status()` rejects any value outside the four states — so the ensemble record cannot go on the row, and this sprint did not alter the table. Both answers are written to `document_field_corrections` (`target_type='note_terms'`, `org_id NULL`, `corrected_by NULL`, `notes.source='hazard_ensemble_disagreement'`) — the only existing store that is field-level, note-terms-targeted and org-NULL. `correction_retrieval` filters on `org_id = $1`, and NULL never equals a uuid, so **the tenant few-shot corpus is not polluted**. This is a workaround, recorded as a gap: the right long-term fix is an `extraction_notes jsonb` column on the terms row.

### Model resolution with no org context (Task 1c finding)

`resolve_model(org_id=None)` returns `DEFAULT_SETTINGS[key]` directly with no DB lookup (`services/extraction.py:76-77`), so `ai.model.default` → `claude-haiku-4-5-20251001` and `ai.model.assistant` → `claude-sonnet-4-6`. **The org-less case is handled; nothing needed patching.**

The real finding: **`ai_decision_log.org_id` is NOT NULL**, so `extraction.py:51,189` attributes every org-less platform call to `DEFAULT_ORG_ID` (`00000000-…-0001`). This sprint writes to global tables with no tenant, so **its AI cost and decision log land on 2nd Act's ledger**. Reported, not patched around — a nullable `org_id` or a reserved platform org is the fix, and it is a decision, not a bug fix.

### Other honest notes

- **Underlying resolution is NOT done.** 93 edges exist from the run (98 including earlier trial rows), **all** `link_state='unresolved'` with `to_global_security_id NULL`. The verifier asserts resolved edges = 0. Resolving them is the next sprint.
- **`securities_global` was empty (0 rows).** `global_security_id` is `NOT NULL`, so extraction had to create the security row — unavoidable, and not mentioned in the prompt. CUSIP is the natural key (an FWP and the 424B2 that prices it are the *same* security, which is what makes §7c's versioning real); only a **checksum-valid** CUSIP is attached, because a mistyped one would silently merge two unrelated notes. 52 CUSIP identifiers attached across 54 securities. Filings with no CUSIP get a filing-scoped security and their preliminary/final rows will **not** link — an honest limitation of preliminary filings, not papered over with a name match.
- **`cik_matches_filer` failed on 6 of 50** — the model reading the guarantor or an index sponsor as the issuer. This is the validator doing its job; the extraction prompt needs work.
- **A leaked test fixture sits in the corpus**: `reference_filings` row `cik=9999999999`, `filer_name='VERIFY FIXTURE'`, 110 chars, `extraction_status='extracted'`, from an earlier sprint's teardown miss. Excluded from the run rather than deleted — it is another sprint's row.
- **One source span per row, covering all located quotes.** Spans run 28→48428 chars. Good enough to locate the term-sheet region; **not** good enough for per-field UI highlighting, which would need per-field offsets the schema cannot hold. Same family as gap 2 above.

**Verification — `apps/api/scripts/verify_notetermsextraction.py`: 26 PASS, 0 FAIL.** Run twice, identical result; teardown at start and end; corpus returned to exactly 166 `extracted` / 35 `skipped` with zero leftover rows. `APP_SERVICE_DATABASE_URL` required with **no** `SET ROLE` fallback. Proven: all five validators on known-good **and** known-bad fixtures (a validator hardcoded to `True` fails these); `cusip_checksum` rejects `17333HJG0` → `71333HJG0`, a real corpus CUSIP with one transposed digit; `field_status` covers all 19 registry fields on all 54 rows with none omitted; **the core assertion — scripted disagreement forces `needs_review` with both answers recorded (`protection_type` buffer/floor, `basket_type` single/worst_of) and neither silently chosen**; the isolated agreement case stays `high` with zero validator failures, proving the ensemble is not simply failing closed; source spans sliced and the **actual substrings printed** (all three real term-sheet headers); `log_note_terms_correction` readable under the non-bypass `app_service` role with no org context; and the `extraction_status` resolution asserted internally consistent.

**The two ensemble assertions are mocked on purpose.** A live two-model comparison is nondeterministic — an assertion that depends on Sonnet happening to disagree with Haiku today is a flaky test. The mock pins the comparison logic; the real disagreement **rate** is reported separately from live data.

---

## 7f · Completed — STP policy + note-terms review queue

**The routing decision that sits on top of §7e's confidence, plus the screen that makes it reviewable.** A newly-extracted terms row now either goes straight through (auto-confirmed, no human touch) or lands in a review queue, decided by a per-`(cik, form_type)` trust policy. Nothing in the extraction or hazard-ensemble logic changed; this sprint only *reads* what §7e produced and decides who looks at it.

### The rule, in order — and the one thing it must never do

1. **Any hazard-ensemble disagreement → `queued`. Always.** No policy overrides this. STP is a statement of trust in *agreement* between the two readers; it is never a bypass of disagreement detection. The ensemble runs on every row regardless of STP status and its result is stored identically either way.
2. Else, an **active policy** for `(reference_filings.cik, form_type)` → `stp`.
3. Else → `queued`. The safe default: a pairing nobody has explicitly trusted stays queued forever until somebody grants it.

**One refinement to step 2, stated rather than slipped in:** it fires only when `extraction_confidence = 'high'`. That is the only value meaning the two readers agreed *and* the comparison actually happened. `needs_review` means either a disagreement (step 1 caught it) or a failed arithmetic validator — not agreement about anything, and routing it `stp` would contradict the queue query, which returns every `needs_review` row by definition. `low` means the ensemble did not run or ran on the primary model and compared nothing; straight-through on an unmeasured row would let an Anthropic outage silently clear a batch — the exact failure §7e's confidence ladder exists to prevent. **Trust in an issuer is not trust in a run that did not happen.**

**Disagreement is read from two places and either one queues.** `route_note_terms_row` takes the caller's in-memory ensemble result *and* independently queries the recorded rows in `document_field_corrections`. §7e logs disagreements in a `try/except` that deliberately never loses a good row over a logging failure, so the database can under-report; trusting it alone would let a failed INSERT promote a disagreeing row to straight-through. Both sources must be silent for a row to go STP.

### Why `(cik, form_type)` and not a global switch

"Trust this issuer's 424B2s" is a defensible statement; "trust everything" collapses the distinction between a prospectus template read forty times and one seen once. `cik` is the stable key — `filer_name` is display-only and drifts: the live corpus holds **`JPMORGAN CHASE & CO` at cik 19617** and **`JPMorgan Chase Financial Co. LLC` at cik 1665650**, different issuers a name-based key would merge or split arbitrarily. **No auto-granting**: STP is granted by an explicit human act from the queue screen, never inferred from accuracy statistics.

### The 54 pre-existing rows have `routing_decision = NULL`, by design

They were created before any routing policy existed. **NULL here means "no routing decision was ever made for this row", which is the truth**, and the migration deliberately does not backfill. `'queued'` would claim a human was asked to look when none was; `'stp'` would claim trust that was never granted. Nothing is lost: the 28 of them at `extraction_confidence='needs_review'` still surface through the confidence half of the queue query. The verifier asserts this on the **rows themselves** (54 rows, 54 NULL, 0 non-NULL) — not merely that the migration ran, because a stray backfilling UPDATE would pass the weaker check.

### The live corpus, measured (Task 1a)

All 54 note-terms-linked filings have a populated `cik` and `form_type`; zero NULL `reference_filing_id`. **17 distinct `(cik, form_type)` pairings**, none dominant — the largest is Goldman Sachs Group 424B2 at 6 rows. **48 of 54 rows are 424B2**, 6 are FWP. (The sprint prompt's "48/54 from one JPMorgan CIK" was not what the database says: 48/54 is the *form type* split, spread across sixteen issuers. JPMorgan's two CIKs hold 10 rows between them.)

| Pairing | Rows | needs_review | high |
|---|---|---|---|
| GOLDMAN SACHS GROUP INC · 424B2 (886982) | 6 | 6 | 0 |
| Morgan Stanley Finance LLC · 424B2 (1666268) | 6 | 4 | 2 |
| JPMorgan Chase Financial Co. LLC · 424B2 (1665650) | 5 | 3 | 2 |
| BofA Finance LLC · 424B2 (1682472) | 5 | 1 | 4 |
| MORGAN STANLEY · 424B2 (895421) | 5 | 3 | 2 |
| GS Finance Corp. · 424B2 (1419828) | 4 | 1 | 3 |
| JPMORGAN CHASE & CO · 424B2 (19617) | 4 | 1 | 3 |
| BANK OF AMERICA CORP /DE/ · 424B2 (70858) | 4 | 1 | 3 |
| BARCLAYS BANK PLC · 424B2 (312070) | 3 | 3 | 0 |
| BANK OF MONTREAL /CAN/ · 424B2 (927971) | 3 | 0 | 3 |
| *seven pairings at 1–2 rows* | 11 | 5 | 6 |

### What was built

| Piece | Detail |
|---|---|
| `portfolio.note_terms_stp_policy` | Global, **no `org_id`** — SEC filings are public reference data with no tenant. `CHECK form_type IN ('424B2','FWP')`; a **partial** unique index on `(cik, form_type) WHERE enabled` so unlimited revoked history coexists with one live grant; a second CHECK forcing `enabled=false` to carry `revoked_by`/`revoked_at`, so "revoked" can never be an assertion with no evidence behind it. Four-policy RLS (global read, super-admin insert/update/delete), copied verbatim from `securities_global_note_terms`. A re-grant **inserts a new row** rather than flipping the old one back, so who trusted this issuer the first time, and why, survives. |
| `securities_global_note_terms.routing_decision` / `routed_at` | Additive, nullable, `CHECK IN ('queued','stp') OR NULL`. In-place columns, not a Rule-3 supersede: `routing_decision` is metadata about the extraction *event*, stamped once at the end of it, and the partial current-row unique index would force a supersede to retire a perfectly good terms row to record an annotation. |
| `services/note_terms_routing.py` | `route_note_terms_row` (the three-step rule), `grant_stp`, `revoke_stp`, plus policy/ledger readers. Called as the **last** step of `extract_terms`, after the ensemble has run, been compared, and been recorded — routing reads that outcome, it does not participate in producing it. A routing failure is caught and leaves `routing_decision` NULL rather than losing a good extraction. |
| `routers/pricing_admin.py` | Five endpoints under `/api/v1/admin/pricing/…`: the queue, resolve, list/grant/revoke policy. **Super Admin only**, same `_require_super_admin` shape as `restricted_access.py` and `trading_authority.py` — but returning no `org_id`, because there is no org in play and inventing one to match a helper signature later reads as a real scoping rule. |
| `app/admin/pricing/note-terms-queue/page.js` + `components/admin/NoteTermsQueueManager.jsx` | List on the shared TanStack `DataGrid`; per-row detail showing **primary vs. secondary answer side by side** with the model that produced each, and the **actual source sentence** sliced at `[source_char_start:source_char_end]` — not a page reference. Nav entry gated on `role === "super_admin"` alongside Restricted Access / Trading Authority. |
| The grant moment | When the last outstanding row for a pairing is settled, the detail view **offers** the grant with a notes field and a confirm button. No separate settings page, and no grant without a person pressing the button. A small STP POLICY panel lists active policies with a revoke action. |

### Resolve is an in-place field write, deliberately

`POST …/{id}/resolve` logs through `log_note_terms_correction` **first** (`target_type='note_terms'`, `org_id` and `document_id` NULL, `corrected_by` = the reviewer), then writes the column and sets `field_status[field] = 'extracted'`. Rule 3 exists so a changed fact does not erase the previous one — and nothing is erased here: the corrections ledger records the original value, the chosen value, who chose it and when, which is richer than a superseded row. A supersede would also **break the screen it serves**: corrections and disagreements are keyed to `target_id`, so superseding on the first of three disagreed fields would hand the next two a new row id with no records attached. Stable identity is a requirement of per-field resolution, not a shortcut around Rule 3.

`routing_decision` and `extraction_confidence` are **not** rewritten by a resolve. The row was queued because at extraction time it had a disagreement; that remains true about that moment. "Done" is derived instead — every ensemble-flagged field now carrying a human correction — so no historical record is edited to make a screen look tidy.

### Explicitly NOT built

Underlying **resolution** (still the next sprint) · any change to extraction or hazard-ensemble logic · a global STP on/off toggle · auto-granting STP from a clean run.

**Verification — `apps/api/scripts/verify_notetermsrouting.py`: 26 PASS, 0 FAIL.** Teardown at start and end; `APP_SERVICE_DATABASE_URL` required with **no** `SET ROLE` fallback. Every routing assertion runs the **real** pipeline end to end — real inserts, real ensemble comparison, real correction logging — with only the two Anthropic calls scripted, on **synthetic CIKs** (`99999xxxxx`) so a missed teardown could never leave a real trust decision behind. Proven: all three routing proofs, including **PROOF 1 — a disagreeing row queues even with an active policy for its pairing**; that an STP'd row stores the *same* ensemble result as a queued one (six fields compared, ensemble measured, identical `field_status` and source offsets — only `routing_decision` differs); that a disagreement **under** an active policy is recorded byte-identically to one with no policy; the partial-unique behaviour (revoked + new active coexist, a second active is rejected); `grant_stp`/`revoke_stp` rejected without `is_super_admin` **and** independently rejected by RLS under `app_service`; global read with no org context; a member getting 403 from the queue; the queue returning **exactly** the query-definition set (31 rows: 28 corpus + 3 fixtures) checked against an independent re-derivation in SQL rather than a hand-listed fixture set; resolve writing the value, the `field_status`, and the ledger row; and the grant offer **not** appearing while the pairing still has unresolved fields.

---

## 7g · Completed — underlying resolution (normalize → match → propose → human confirm)

**The 97 unresolved `securities_global_relationships` edges §7e left behind now carry a machine proposal and a human-gated confirm path.** §7e wrote each underlying's name verbatim off the prospectus with `link_state='unresolved'` and an explicit note that resolution was a later sprint. This is that sprint. **Nothing is auto-resolved**: the pipeline proposes, and only a Super Admin pressing confirm writes `link_state='resolved'`.

### The live population, measured (Task 1a) — it was not what the brief described

97 edges, **57 distinct `raw_underlying_text` values**, not the 15 the prompt listed. The five major index families are 57 of the 97 edges once formatting noise is collapsed; the remaining 40 edges are a real tail the top-15 view hides: 11 single-name equities, 7 ETFs/funds, 6 foreign indices, 5 decrement/risk-control indices, 2 Nasdaq-100 *sub*-indices, and one 340-character description of a WTI CL1/CL2 futures roll.

Two noise patterns the brief did not list, found only by pulling the full distinct set: **trailing parenthetical ticker glosses** (`(the "NDX Index")`, `(ticker: "NDX")`, `(SPXFP)`, `(SPXF40D4)`) and **spaces stranded in front of punctuation** by a removed mark character (`S&P ® /ASX 200`, `Swiss Market Index (SMI ® )`). Both had to be handled or six S&P/Nasdaq/Russell edges would have fallen to manual review for a typesetting artifact.

Also confirmed before writing anything: `securities_global` held **54 rows, all `structured_note`, zero indices** — every index row in the table today was created by this sprint. And the table had **no unique constraint of any kind** (pkey, one FK, two value CHECKs), so nothing prevented a duplicate "S&P 500 Index".

### The precedent (Task 1d) was inspected and rejected for a structural reason

The platform's "AI proposes, human confirms" table is Chancery's `document_link_proposals`. It is **not reusable here**: its shape is `document_id uuid NOT NULL → documents(id)` and `org_id uuid NOT NULL → organizations(id)`, and an unresolved underlying edge has neither — these hang off `portfolio.reference_filings`, global public SEC data with no org anywhere in its lineage. Satisfying those FKs would mean inventing a document and attributing a public fact to one tenant. So the **pattern** was reused (pending → `reviewed_by`/`reviewed_at`, approve or reject) on the edge itself, per the sprint's stated fallback, with the proposal in a **new `proposed_global_security_id` column** — `to_global_security_id` is not overloaded, because `sec_global_rel_resolved_has_target` pins its meaning to "resolved's target".

### Maker-checker is enforced in the database, not just in Python

A CHECK constraint cannot express *who wrote this*, so the gate is a `BEFORE INSERT OR UPDATE` trigger, `trg_sec_global_rel_confirm_gate`, keyed to a transaction-local GUC. **Any transition into `link_state='resolved'` raises `42501` unless `app.underlying_confirm='true'` is set `LOCAL` in that transaction**, and `confirm_resolution` is the only function in the codebase that sets it. This is stronger than the existing RLS: the verifier proves that a caller holding `app.is_super_admin='true'` — full write rights on the table — **still cannot resolve an edge** without going through the confirm path. A future refactor that points the proposal pipeline at the wrong column gets a database error, not a silently auto-approved corpus.

### What was built

| Piece | Detail |
|---|---|
| `docs/underlyingresolution_part1.sql` | Eight additive nullable columns on `securities_global_relationships` (`proposed_global_security_id`, `proposal_confidence`, `proposal_kind`, `proposal_hint`, `proposed_at`, `normalized_underlying_text`, `resolved_by`, `resolved_at`); four new CHECKs locking the vocabularies and forbidding contradictory states (a `'high'` confidence with no proposed target; an `'ambiguous'` edge with nothing to confirm; a `'resolved'` edge with no `resolved_by`/`resolved_at`); the confirm-gate trigger; RLS re-asserted DROP-then-CREATE on both tables with `NULLIF` on every GUC read. |
| `uq_sec_global_active_index_name` | Closes the Task-1e gap. Partial unique on `lower(name)` `WHERE security_type='index' AND valid_to IS NULL AND system_to IS NULL`. **Scoped to indices deliberately** — the 54 `structured_note` names are prospectus titles ("Callable Contingent Income Securities due March 17, 2028") that genuinely repeat across issuers, so a table-wide name index would reject legitimate data. |
| `services/underlying_normalization.py` | `normalize_underlying_text` — five ordered rules, no LLM. Trailing parenthetical → mark characters (`® ™ ℠`) → trailing `SM` → punctuation spacing → whitespace → leading article. `'SM'` is stripped **only as a whole uppercase token at end-of-string**, because `SMI` (Swiss Market Index) is in the same corpus and a blanket strip would corrupt it. Casing is preserved per the brief, so `normalization_key()` (casefolded) is what every lookup uses — the corpus contains both `Common Stock` and `common stock` for NVIDIA. |
| `services/underlying_index_registry.py` | A hand-maintained `dict`, **not** a fuzzy matcher: exact case-folded equality or nothing. 13 securities over 14 lookup keys. `resolve_or_create_index_security` is idempotent three ways — SELECT-then-INSERT in one transaction, the new unique index, and a catch-and-re-SELECT so a losing concurrent INSERT returns the winner's id instead of an error. Raises `KeyError` for any name not in the table: it will not invent a security. Also writes a `ticker` identifier row so a future price sprint has a handle. |
| `services/underlying_resolution.py` | `propose_resolution` / `propose_all_unresolved` / `confirm_resolution` / `reject_proposal` / `load_queue`. Confidence is deliberately **not a number** — a percentage invites a threshold, and a threshold is an auto-approval rule in disguise. Two values only: `high` (exact registry hit) and `needs_manual_match`. |
| `routers/pricing_admin.py` (+3 endpoints) | `GET /admin/pricing/underlying-queue`, `POST …/{id}/confirm`, `POST …/{id}/reject`. Same `_require_super_admin` gate and same file as the §7f note-terms queue — the scope, the gate and the data are identical, and a second router would mean a second copy of the gate to drift. `resolved_by` is the authenticated caller, never from the body. |

### The index registry, seeded — 13 securities across the families

**Tier 1 (the five the brief names, plus the required split):** S&P 500 (SPX) · Russell 2000 (RTY) · Nasdaq-100 (NDX) · Dow Jones Industrial Average (INDU) · EURO STOXX 50 (SX5E) · **S&P 500 Futures Excess Return Index (SPXFP) as a SEPARATE entry**. That last one is not pedantry: the futures ER series is net of a financing cost and drifts persistently below spot, so collapsing it into "S&P 500 Index" would silently substitute a materially worse price history — a real error, not a formatting one.

**Tier 2 (the unambiguous, exchange-published remainder of the live tail):** MSCI EAFE (MXEA) · FTSE 100 (UKX) · S&P/ASX 200 (AS51) · Swiss Market Index (SMI) · TOPIX (TPX) · Nasdaq-100 Equal Weighted (NDXE) · Nasdaq-100 Technology Sector (NDXT). The same reasoning splits NDXE and NDXT from NDX — shared branding, different constituents. Conversely `TOPIX Index` and `Tokyo Stock Price Index` are **one index under two names**, and are the registry's only alias pair.

### The result against the real 97 — 69 proposed, 28 to manual review

Normalization collapsed **57 distinct raw strings to 37**.

| Outcome | Edges | What it means |
|---|---|---|
| **`high` — index proposal** | **69** | Exact registry hit. Edge moved to `link_state='ambiguous'` with a target awaiting one click. 57 of these are the five brief-named families; the other 12 are Tier 2 + the futures ER. |
| `needs_manual_match` · `single_name` | 15 | Company name extracted as a **hint** (`'NVIDIA Corporation'`), never a ticker. |
| `needs_manual_match` · `fund_etf` | 7 | SPDR sector funds, iShares, VanEck. A fund tracking an index is not the index. |
| `needs_manual_match` · `decrement_candidate` | 5 | MerQube ×2, S&P 500 Futures 40% Intraday 4% Decrement VT ×2, GS Momentum Builder Focus ER. |
| `needs_manual_match` · `unclassified` | 1 | The WTI CL1/CL2 futures-roll description. |
| **`resolved`** | **0** | Correct. No human has confirmed anything yet — which is the entire point. |

16 of the 28 manual-review edges carry an extracted name hint. **Every one of the 97 remains human-gated.**

### Explicitly NOT built, per the brief

Auto-resolution of single-name equities to tickers (share classes, reassigned tickers and foreign private issuers all break a name match at scale) · a fuzzy/similarity matcher of any kind · **price-series wiring for decrement indices** — out of scope, possibly permanently; a reviewer's `create_new` will make a placeholder row at `price_coverage='no_public_source'`, and that is the extent of it · auto-approval above any confidence threshold · comparability scoring or percentile ranking (the next sprint, which depends on this one) · a frontend screen — the three endpoints exist and are proven; the queue UI is not in this sprint's task list.

**Verification — `apps/api/scripts/verify_underlyingresolution.py`: 53 PASS, 0 FAIL** (idempotent — two consecutive runs, identical result). Teardown at start and end; `APP_SERVICE_DATABASE_URL` required with **no** `SET ROLE` fallback. Every assertion runs against the **real corpus**: 13 verbatim duplicate pairs from the database collapse to one string, and 5 verbatim distinct pairs (spot vs futures-ER, NDX vs NDXE/NDXT, index vs ETF, index vs decrement) stay apart. Proven: the registry creates at most one row per security across repeated calls with the index row count unchanged, and refuses a name outside the table; **the core governance assertion — `propose_resolution` on a real high-confidence index match leaves `link_state` NOT `'resolved'`, with `to_global_security_id` still NULL**; a source scan of every `UPDATE` statement in `services/`, `routers/`, `scripts/`, `models/`, `migrations/` and `docs/` finds **exactly one** write site setting `link_state='resolved'` and it is `confirm_resolution` (scoped to UPDATE statements on purpose — a naive grep for the literal reports five sites, most of them `<>` guards and the trigger's own comparison); all three service functions refuse without `is_super_admin`; **RLS blocks the resolve for `app_service` with no context, and the trigger independently blocks it for a caller that *has* super-admin rights but no confirm token**; the full 97-edge pass with the counts above; one real proposed index resolution confirmed end to end (`resolved`, non-null target, target is a `security_type='index'` row) and then **restored to its pre-test state**; `create_new` building a decrement placeholder named from the normalized text at `price_coverage='no_public_source'`; reject clearing the proposal back to a clean `unresolved` while keeping `normalized_underlying_text`; the queue joining **97/97** edges to their real `note_terms` row, filer and accession; global read under `app_service` with no org context returning the same 97; and 403 on all three endpoints for a non-Super-Admin.

**One design change the database caught.** The first cut of `confirm_resolution` nulled `proposed_global_security_id` on the way out and hit `sec_global_rel_high_needs_proposed_target`. The fix is the better design: the proposal is **kept** alongside the confirmed target, so "did the reviewer accept the matcher's answer or override it" is answerable from the row itself — the only direct measure of whether the registry is any good, and it would have been erased on exactly the rows where it matters most.

---

## 7h · Completed — Portfolio A1, the global security layer

**Schema and services only. A1 is the global master layer that both the portfolio track and the structured-investments corpus build on; it unblocks the corpus track.** `apps/api/services/securities_global.py` + `apps/api/scripts/verify_portfolioa1.py`. **39 PASS, 0 FAIL**, idempotent across consecutive runs.

The Part 1 SQL was applied directly via Supabase MCP before the sprint, so A1 built *against* four existing tables rather than creating them: `portfolio.securities_global` (no `org_id` — deliberately global), `..._identifiers`, `..._prices`, `..._relationships`. All four have RLS enabled with exactly **4 policies each**, in the inverted shape that `public.permissions` uses: `USING (true)` global read, `app.is_super_admin` on insert/update/delete.

### What the service layer contains

`get_by_identifier` (identifier → surviving security, **following the merge chain in one statement**) · `create_security` / `add_identifier` / `add_price` / `add_relationship` (all Super-Admin-gated) · `resolve_scoreability` (derived, never stored) · `merge_securities` and `backfill_canonical_ids` (the helpers that keep reads cheap) · `set_price_coverage`.

**The underlyings-only pricing rule is enforced in code, not by convention.** `add_price` raises `StructuredNotePricingError` — its own class, naming the security and pointing at `resolve_scoreability` — when the target's `security_type` is `structured_note`. A note's secondary prices are sporadic TRACE prints, not a daily series. The natural implementation of a price loader (`for security in securities: fetch_prices(security)`) would not crash and would not log anything alarming; it would quietly write ~250k individually-plausible, collectively-meaningless rows. Deliberately **not** log-and-continue: a "54 skipped" counter at the end of a run is indistinguishable from a healthy run and converts a design invariant into a statistic.

**`resolve_scoreability` returns the reason, not just `False`.** Scoreable = at least one current relationship, every one `link_state='resolved'`, every target `price_coverage='has_series'`. Each gap names the specific `raw_underlying_text` or the specific uncovered target. `False` alone turns into "we couldn't score 4,000 notes" with nothing to act on. Zero relationships is reported as an honest gap rather than treated as vacuously true — a note with no recorded underlyings is one we have not finished reading. Derived on read because all three inputs move independently, and a stale scoreability flag silently stops (or starts) scoring notes.

### Task 1 findings worth keeping

- **1c — `portfolio` is NOT on the `search_path`. Every query must schema-qualify.** Measured, not assumed: `app_service` has no `pg_db_role_setting` row, so it inherits `"$user", public`. An unqualified `SELECT ... FROM securities_global` raises `UndefinedTableError` under the production role while working fine in a psql session that happened to `SET search_path` — invisible in development, total in production. **A2 and the SI track both need this.** The service layer routes every table name through schema-qualified module constants, and the verify script parses the module with `ast`, strips docstrings, and fails on any bare `FROM`/`INTO`/`UPDATE` of a portfolio table.
- **1b — the RLS GUCs are connection-level `SET LOCAL`, not schema-scoped.** `_RLSPool.acquire()` opens an explicit transaction whose first statement is `set_config('app.current_org_id'/'app.is_super_admin'/'app.current_auth0_sub', ..., is_local => true)`. A `SET LOCAL` GUC has no schema binding, which is exactly why policies in a non-`public` schema read it with no extra work.
- **1d — verified live, under real `app_service` (`rolbypassrls = false`).** Global read works with `is_super_admin='false'`; INSERT into all four tables is rejected with `InsufficientPrivilegeError`; the same connection with `is_super_admin='true'` inserts successfully.
- **1a policy-shape inconsistency, not fixed (it is Part 1 SQL, already applied).** `securities_global` and `..._relationships` guard with `NULLIF(current_setting('app.is_super_admin', true), '') = 'true'`; `..._identifiers` and `..._prices` use the bare `current_setting(...) = 'true'`. Both deny correctly here — it is a text compare, not a uuid cast, so the `''` reset artifact cannot raise — but the shapes differ and the bare form is the one that bites the moment anyone copies it onto a uuid column.
- **`canonical_id` shipped NULL on all 67 live corpus rows.** The Part 1 SQL added the column; nothing populated it. A read path trusting a bare `canonical_id` would have returned zero rows for the entire corpus. Every read uses `COALESCE(canonical_id, id)` (still one join, still no walk), `create_security` mints the id in Python so `canonical_id = id` in the same INSERT (no NULL window), and `backfill_canonical_ids` closed the legacy gap — **67 rows backfilled, permanently**. `canonical_id` is the one column deliberately exempt from Rule 3 bitemporal supersede: it is a materialized derivation, not a business fact, and minting a new `id` to change it would orphan every FK pointing at the old one.
- **`docs/PORTFOLIO_REPORTING_DESIGN_V6.md` does not exist in the repo.** A1 was built against the deployed schema, which is the source of truth per CLAUDE.md anyway. If that design doc exists somewhere, it is not in git. **Resolved in A2** — see §7i.

### Two things the verification caught about itself

**The four tables were never empty.** They held the live EDGAR corpus (67 securities / 64 identifiers / 97 relationships) throughout. The brief's "teardown leaves zero rows in all four tables" would have deleted verified production data, so it is enforced as the form that actually means something: **all four tables are counted before the run and after teardown, and must match exactly.** A leaked fixture row fails as hard as a deleted corpus row. Fixtures are tagged `VERIFY-PORTFOLIOA1` and deleted by exact match, and every fixture name is declared up front — a name appended at runtime is one the *next* run's start-teardown cannot find.

**The CHECK-constraint test passed while proving nothing, and the fix was to assert on the constraint name.** BEFORE triggers fire ahead of CHECK constraints, and `sec_global_rel_confirm_gate` carries its own belt-and-braces `RAISE ... ERRCODE 23514` for a resolved edge with a NULL target. So the obvious test — INSERT a resolved row with a NULL target — was stopped by the *trigger*; `sec_global_rel_resolved_has_target` was never consulted, and dropping it entirely would not have failed the test. The check now drives an edge into `resolved` through the real `confirm_resolution` path and *then* nulls the target: the trigger does not re-gate an already-resolved row, so it steps aside and the constraint is the only thing standing there. Both mechanisms are now asserted separately. **Generalizable: "an exception was raised" is not a passing test when two layers can raise the same error class.**

Also proven: the merge chain resolves B's identifier to A with `EXPLAIN` showing no recursive node (N+1 impossible — the chain is collapsed at write time by `merge_securities`, including re-pointing transitive rows, so reading it is a join and not a traversal); `add_relationship` inserts with `to_global_security_id` NULL and `raw_underlying_text` populated (**the case v5 got wrong**); `add_price` stores `Decimal`, and `_money()` refuses `float` outright because `Decimal(0.1)` silently preserves the binary error with no exception anywhere downstream.

### Deliberately NOT built (A2 and later, per the brief)

`portfolio.assets` / `positions` / `valuations` / `transactions` / `external_references` · `account` in the `entity_type` enum · `market` on transaction types · `rate_type` / bitemporal on `fx_rates` · any ingestion, EDGAR fetching or price feed · any UI · any extension table. `securities_global_note_terms` already exists from the note-terms sprints; A1 does not preclude it and does not touch it. `add_relationship` cannot write `link_state='resolved'` or `'ambiguous'` — those belong to `services/underlying_resolution.py`'s propose/human-confirm path and to `trg_sec_global_rel_confirm_gate`, and A1 defers to both rather than opening a second door.

---

## 7i · Completed — Portfolio A2, tenant assets / positions / transactions

**Schema-facing services + the `account` entity node. No router, no UI.** `apps/api/services/portfolio_assets.py` + `apps/api/scripts/verify_portfolioa2.py`. **63 PASS, 0 FAIL**, idempotent across consecutive runs, real `app_service` connection (`rolbypassrls = false`).

The Part 1 SQL was applied directly via Supabase MCP before the sprint, so A2 built *against* six existing tables: `portfolio.assets`, `asset_identifiers`, `positions`, `valuations`, `transactions`, `external_references`. All six had RLS enabled with **exactly ONE policy each** — `org_id = NULLIF(current_setting('app.current_org_id',true),'')::uuid OR is_super_admin`, `cmd=ALL`.

**The policy shape is the inverse of A1's and the difference is the point.** A1's four-policy `USING (true)` global-read shape copy-pasted onto one of these six tenant tables would be a silent cross-org read — it raises nothing, logs nothing, and returns every other tenant's positions. The verification asserts the count is exactly one per table **and** that the single policy is org-scoped, because "one policy" that happened to be `USING (true)` would pass a count check on its own.

### What the service layer contains

`create_asset` / `add_identifier` (org-scoped, **not** Super-Admin-gated — this is tenant data) · `create_position` (the entity↔asset edge, with the three-basis contract) · `record_transaction` (type existence + `market` compatibility) · `record_valuation` (append-only supersession) · `resolve_current_value` (the status ladder) · `upsert_external_reference`.

`_OrgWrite` mirrors A1's `_SuperAdminWrite` but raises **org context** rather than privilege, so the RLS `WITH CHECK` — not a Python `if` — is what refuses a cross-tenant write. It deliberately does **not** touch `app.is_super_admin`: elevating on a Super Admin's behalf would mean the module could not tell the two cases apart, and a bug that wrote to the wrong org would pass silently.

**The ownership-basis contract has no database backstop.** `units → quantity` (pct NULL), `percent → ownership_pct` (qty NULL), `value → market_value` (both NULL). Introspection found `portfolio.positions` carries only `positions_basis_chk` / `_authority_chk` / `_source_chk` — **nothing** ties the basis to which measure is populated, so `_validate_basis` is the only enforcement in the system. It matters because a row declaring `value` while carrying a quantity is not a harmless extra field: one rollup computes from `market_value`, another from quantity × price, and the two silently disagree about the same holding.

**Supersession is an INSERT, never an UPDATE.** `supersedes_valuation_id` is a forward pointer on the *new* row; the prior row is left byte-identical (the verify script snapshots every column before the restatement and re-compares, not just `value` — comparing only the amount would miss an implementation that closed the row with `valid_to`). This is *not* a Rule 3 supersede: Rule 3 closes a row because the old value stopped being true, and a superseded valuation never stopped being true — it remains, permanently, the number struck on that date by that source. What changed is which number is *current*, and that is answered on read.

**The resolver demotes rather than excludes, and never returns zero.** Ladder: latest `valuation_date`, then `audited > final > preliminary > estimated`, and **any row another current valuation supersedes drops below all four regardless of its own status** — a rule keyed only to `status` would return a restated-away `audited` figure forever. When nothing qualifies it returns `value=None` with a reason naming which of three cases applies. A zero for "we have no mark" is indistinguishable from a genuine zero position the instant it is summed into a rollup, and by then the fact that it was never measured is gone.

### The `account` node

`entity_type='account'` (custodial accounts) is a **real entity** — a position's `owner_entity_id` points straight at one, so account-level reporting is an ordinary graph query rather than a special case in every rollup. It is also **optional**: a position may name a trust directly with no account in between, proven explicitly rather than left implied.

Accounts are excluded from every CRM-facing surface. `OPERATIONAL_ENTITY_TYPES = {account, spv}` in `schemas/entities.py` is **default-deny on purpose** — a new operational type added to the enum is hidden from the CRM the moment its name lands in that set, with no further edits. An allow-list would have to be found and updated instead, and the failure mode of forgetting is the account leaking into the CRM.

### Task 1 findings worth keeping

- **1d — the account node could not be created at all.** `schemas/entities.py::EntityType` was missing both `account` and `spv`, so `POST /entities` returned 422 for either. The Postgres enum had carried them for some time; the Pydantic enum had never been updated. **A schema-level enum and a database enum drifting apart fails at the API boundary and nowhere else** — no verify script, no query and no migration would have caught it.
- **1d — three CRM call sites needed the exclusion**, and the third is the dangerous one: `find_entity_dupes` is reused *verbatim* by Chancery Phase 5 (document-party linkage) and Phase 11a (narrative parties). An account named after the institution holding it — "Fidelity", "Schwab" — is exactly the string a document-party matcher hits, and a match would have linked a K-1 to a brokerage account instead of the trust that owns it. The exclusion is the **default** there rather than a flag both callers would have to remember to pass. `INVESTOR_ENTITY_TYPES` needed no change — it is an allow-list and excluded accounts by construction.
- **1b — `entity_type` also carries `spv`, which the brief did not mention.** It landed alongside `account` and has the identical CRM-visibility problem, so it is covered too. **Generalizable: when a brief names one new enum value, read the whole enum.**
- **1a DRIFT — `portfolio.external_references` UNIQUE is `(source_system, external_id, record_type)` and is NOT org-scoped**, despite the table carrying `org_id` and an org-isolation policy. Two tenants ingesting from the same source with colliding external ids hard-conflict, and the loser gets a unique violation **on a row RLS will not let it see** — an error with no visible cause. `upsert_external_reference` guards its `ON CONFLICT ... DO UPDATE` with an org equality clause so a cross-org collision raises a legible message instead of silently re-pointing another tenant's row. **Widening the constraint to include `org_id` is a required Phase B migration.**
- **1b DRIFT — `fx_rates` gained `rate_type` but its UNIQUE was not widened.** `UNIQUE (base_ccy, quote_ccy, as_of_date)` means a spot and a period-end rate cannot coexist for the same pair/date, and a Rule 3 supersede on `fx_rates` is *impossible* — closing the old row and inserting a new one violates the constraint. **Blocks Phase B FX.**
- **1a — `assets.asset_type` is NOT NULL with no CHECK.** Validated for non-emptiness and nothing more, deliberately: inventing a vocabulary in Python that the database does not share would reject rows the database would take, and the next person would have no way to tell which layer was wrong. It is also why the transaction market check keys off `valuation_method` (which *does* have a CHECK) rather than the obvious `asset_type` — a check keyed to open text starts silently passing everything the first time somebody types "Equity".
- **1a — no triggers exist on any of the six tables**, so A1's "a BEFORE trigger can silently mask a CHECK constraint" hazard has no instance in A2. Asserted rather than assumed, so adding one later fails this check and forces the two mechanisms to be tested separately.
- **1c — confirmed again under `app_service`:** `search_path` is `"$user", public`, `portfolio` is not on it, and an unqualified `SELECT FROM assets` raises `UndefinedTableError`. The AST check is replicated and extended to catch bare `JOIN` as well as `FROM`/`INTO`/`UPDATE`.

### Task 2 — `transaction_types.market`, all 16 rows

All 16 were `NULL`. Classified: **private (10)** — `call_investment`, `call_mgmt_fee`, `call_org_cost`, `call_partnership_expense`, `dist_roc`, `dist_gain`, `dist_income`, `dist_recallable`, `dist_stock`, `valuation`. **public (3)** — `buy`, `sell`, `dividend`. **both (3)** — `adjustment`, `fee_expense`, `interest`.

`valuation` is private because a valuation mark *as a transaction* (`affects_nav=+1`) is how an illiquid holding's NAV moves; a listed position is marked by its price series, not by a transaction row.

**`interest` is `both`, a deliberate deviation from the brief's suggested `public`.** The deployed row already records `applies_to_security_types = {unitized, alt}` — it is *already* classified as spanning both, and it genuinely does: a bond coupon is public, private-credit interest is not. Classifying it `public` would have made `record_transaction` reject private-credit interest, which is real and common. **The deployed data won over the brief.** SQL and full rationale in `docs/portfolioa2_part2_backfill.sql`.

### `docs/PORTFOLIO_REPORTING_DESIGN_V6.md` now exists

It was missing at A1 and still missing at A2, and no original was recoverable — `git log --all` has nothing. It is now committed as an **as-built reconstruction**, labelled as such at the top, written from the deployed schema and the A1/A2 briefs. It is the design of record going forward; if the original ever surfaces, reconcile rather than overwrite.

### One thing the verification caught about itself

**Signature checks do not prove an endpoint runs.** The first version asserted `include_operational` was present with a `False` default on all three call sites and passed — while `list_entities` and `search_entities` had never been executed. `_operational_filter` derives its `$n` placeholder from the live length of the params list (these queries build their WHERE clauses incrementally, so a hard-coded `$n` binds the wrong value the moment a condition is inserted above it), and that is exactly the class of bug `inspect.signature` cannot see. Both endpoints are now *called* against real rows, with and without the opt-out. **Generalizable: an `inspect`-based check proves an interface exists, never that it works.**

Also proven: cross-org isolation on `assets` *and* `positions` under real `app_service` — including the **write** half (`WITH CHECK`), because a policy with a correct `USING` and a missing `WITH CHECK` reads correctly and writes across the boundary, and only the INSERT catches it. Teardown asserts exact before/after counts on all six tables even though they measured empty: "it was empty when I looked" is a fact about one afternoon, and the moment Phase B writes the first real position an unconditional `TRUNCATE` becomes a data-loss bug nobody notices until quarter-end.

### Deliberately NOT built (Phase B and later, per the brief)

Any real ingestion (Altruist, reporting-tool import, Chancery consumption) · source-precedence resolution — `positions.superseded_by_source` is written when a caller supplies it and is never *computed* · the S21 sunburst rollup into `entity_holdings` (Phase C) · the SPV derivation view (Phase D) · cash modelling, corporate actions, commitments, UDFs · any router and any UI.

**Next: Phase B — ingestion.** Its two prerequisites are both recorded above: widen the `external_references` UNIQUE to include `org_id`, and widen the `fx_rates` UNIQUE to include `rate_type` (or drop it for a bitemporal-aware one). ✅ Both applied — see 7j.

---

## 7j · Completed — Portfolio B, ingestion + source precedence

**File-based reporting-tool import, org-configurable source precedence, and an honest Altruist gate.** `services/portfolio_import.py` · `services/portfolio_precedence.py` · `services/portfolio_altruist.py` · `routers/portfolio_ingest.py` · `scripts/verify_portfoliob.py`. **50 PASS, 0 FAIL, 2 BLOCKED**, idempotent across consecutive runs, real `app_service` connection.

A **BLOCKED** assertion is not a pass and is not counted as one. Both belong to Altruist and are reported separately, so "all green" can never quietly mean "all green except the thing that was never measured".

### The Altruist gate — BLOCKED, and honestly so

**Credentials are absent.** `ALTRUIST_CLIENT_ID`, `ALTRUIST_CLIENT_SECRET` and `ALTRUIST_BASE_URL` are unset in the process environment and in `apps/api/.env`. **No call was attempted**, because there was nothing to authenticate with. Nothing was mocked, simulated or fabricated.

`services/portfolio_altruist.py` is real and is only a gate: `credential_state()` reads the environment, `probe()` makes **one** genuinely authenticated request if and only if all three credentials are present, and `ingest_positions()` raises `AltruistBlocked` carrying the exact reason. `GET /portfolio/altruist/status` surfaces the live probe rather than a stored flag.

The probe deliberately hits an authenticated accounts call rather than a health endpoint — an unauthenticated liveness check returns 200 whether or not our credentials work, which is precisely the question. `AltruistGate.attempted` separates **"no credentials, nothing tried"** (a provisioning gap) from **"credentials present, real call refused"** (partner access) — different findings that go to different people, and one boolean would lose that.

**There is no mapping function, on purpose.** Writing `_map_altruist_position()` against a guessed response shape is worth nothing and costs something real: Altruist's field names, nesting, pagination, quantity-vs-value conventions and cost-basis lot handling are unknown here, so a guessed mapping gets rewritten — and in the meantime it reads to everyone downstream, and to the verify script, as though the integration is built and merely unconfigured.

**Phase B does not depend on it.** File-based reporting-tool import works completely independently of Altruist and is what actually gets data into the portfolio today. That was the design intent, and it is why the block is contained rather than fatal.

### Source precedence is DATA, not code

`org_settings` key **`portfolio.precedence.source_order`** — an ordered JSON array of `source_system` values, most-trusted first, following the same convention as `ai.model.fallback_chain`. Default (design V6 §1.1): `reporting_tool_bd > reporting_tool_addepar > reporting_tool_orion > reporting_tool_apx > reporting_tool_import > altruist > spv_subscriptions > chancery > manual`. A firm that trusts its custodian over its reporting tool re-orders the setting and **deploys nothing**.

The literal lives in `org_settings.DEFAULT_SETTINGS`, not in the precedence module — that module's own docstring makes it the one place allowed to hold default data, and it keeps the dependency one-directional so `_validate_setting` can reach back with a lazy import instead of a cycle. **Both the configured and the unconfigured case are tested**, and the fixture order deliberately *inverts* the default's top and bottom: a "configured order wins" test whose configured order happens to produce the same winner as the default proves nothing.

`resolve_precedence()` re-reads every candidate from the database under RLS and uses **only** the caller's ids — a caller passing its own `source_system` alongside an id would be trusting the ingestion pipeline to remember what it wrote, and precedence exists because pipelines disagree. Candidates must share one `(owner_entity_id, asset_id, as_of_date)`; spanning two holdings is refused, because it would mark a position superseded by a source describing a different asset. Recency is the within-source tie-break (a restatement), never a cross-source one.

**Losers are annotated, never deleted**, and the winner's stale flag is **cleared**. A row that lost a previous resolution and wins the next one — because the org re-ordered its sources — would otherwise stay flagged as superseded by a source that no longer outranks it, and every downstream reader would skip the actual answer.

**Why the annotation is an UPDATE, and why that is not a Rule 3 violation.** Rule 3 closes a row because its content stopped being true. Nothing here stopped being true: the losing row remains, permanently, what that source said on that date. `superseded_by_source` records the *resolution over* a set of facts, not a change to one. The Rule-3 shape is actively wrong twice over — closing the loser with `valid_to` drops it out of every current-row query, which defeats the design's own "keep it for reconciliation"; doing it as a system-time correction mints a **new `id`**, and `portfolio.transactions.position_id` is an FK onto that id, so every transaction booked against the position would be left pointing at the corrected-away row. It is therefore a narrow, idempotent, single-column write, and **the verification snapshots every column of the losing row and asserts only that one changed**.

### File-based import — works with no external credential

`POST /api/v1/portfolio/import/positions` (CSV or XLSX). Parsing **reuses Chancery**: `detect_file_type` (magic bytes — a CSV renamed `.xlsx` still parses as a CSV), `extract_xlsx` (the existing openpyxl path), `extract_text`. Chancery has no CSV-specific path, so CSV is that text path plus the stdlib `csv` reader — not a second parsing stack.

Header mapping is alias-based against headers reporting tools actually emit, **longest alias first**, so `Ending Market Value` is not swallowed by the `value` alias. Assets match by identifier (CUSIP/ISIN/SEDOL before ticker — a ticker is reused across exchanges and after a delisting) then by exact name; **no fuzzy name matching**, because the same leniency that merges "Apple Inc" and "Apple Inc." merges "Blackstone Real Estate Income Trust" and "Blackstone Real Estate Partners", and only one of those is a disaster.

**Idempotency is a pre-insert READ, not an `ON CONFLICT`.** Each row gets a stable `external_id` — the file's own row id, else a SHA-256 over the row's *normalised meaningful fields*. `find_external_reference` is consulted **before** anything is written. Writing the position first and upserting the mapping afterwards makes the *mapping* idempotent while the *position* duplicates — exactly the bug the assertion exists to catch. The hash covers the row's meaning and **not** the filename, so the same holdings re-sent as `q2.csv` and `q2-final.csv` is one position; proven, not asserted.

**One bad row never fails the file.** A malformed row is skipped with its 1-based file line number and a reason; the rest imports. A file that is unusable *as a file* (a PDF, a header with no data rows, a table naming no security) is a 400 — a different failure, kept distinct.

`source_system = 'reporting_tool_import'`, `authority = 'aggregated'`. Vendor-agnostic on purpose: BD, Addepar, Orion and APX export the same tabular shape, and sniffing the vendor from column headers would manufacture provenance the file does not carry.

### Task 1 findings worth keeping

- **A2's `upsert_external_reference` was broken by its own prerequisite, and would have failed closed.** The Part-1 SQL replaced the UNIQUE with `(org_id, source_system, external_id, record_type)`; the function still said `ON CONFLICT (source_system, external_id, record_type)`. Postgres matches an inference clause against a real unique index, so the old target matched **nothing** and raised `InvalidColumnReferenceError` on every call. Re-pointed, and the now-redundant cross-org guard removed — `org_id` is part of the key, so two tenants can hold the same upstream id independently. **Generalizable: widening a constraint is not a backward-compatible change for any code that names it in an `ON CONFLICT`.**
- **`positions_source_chk` did not admit `reporting_tool_import`.** Introspected, not inferred from the sprint prompt, which mandated that exact value. Every import would have raised 23514. Widened additively in `docs/portfoliob_part1.sql`.
- **openpyxl's `datetime` does not survive Chancery's serialisation.** `_json_cell` keeps only int/float/bool/str/None as-is and stringifies everything else, so an XLSX date cell arrives at the importer as `'2026-06-30 00:00:00'`, **not** as a `datetime`. Found by running the XLSX path — reading openpyxl's docs tells you what openpyxl returns, not what survives the step after it. `_to_date` now tries `fromisoformat` first.
- **`_json_cell` also keeps numeric cells as `float`.** That precision is gone before the importer is reached and cannot be recovered; `Decimal(str(f))` recovers the shortest decimal that round-trips — the number the spreadsheet was displaying — rather than `Decimal(float)`'s binary expansion.
- **Altruist was genuinely greenfield.** The complete pre-existing set: a comment in `schemas/entities.py`, the vocabulary string `'altruist'`, a fixture constant in A2's verify script, `services/trading_authority.py` explicitly recording that the assumed custodian subsystem does not exist, and design-doc prose. No client, no stub, no env var.

### One thing the verification caught about itself

**A vacuous pass, caught only because the detail string was printed.** The "resolving candidates that span different holding keys is refused" assertion passed on its first run — with the refusal message `position_candidates contains None`. The XLSX import was broken, so the second candidate id was `None` and the guard that fired was the null check, not the holding-key rule. It now asserts the id **exists** *and* that the refusal names the key spanning. **Generalizable: an assertion that only checks "it raised" passes on any raise, including the one proving your fixture never got built.**

Teardown asserts exact before/after counts on all six portfolio tables **and `public.org_settings`** — this script writes to live tenant configuration, so it captures the org's real precedence setting before starting and restores it byte-for-byte. The one value it will not restore is one identical to its own fixture order: that is a previous crashed run's residue, not the org's setting, and restoring it would make every future run inherit the wreckage.

### Deliberately NOT built (Phase C and later, per the brief)

The S21 sunburst rollup into `entity_holdings` (Phase C) · the SPV derivation view (Phase D) · cash modelling, corporate actions, commitments, UDFs · any UI beyond the file-upload endpoint · real-time or intraday anything — daily is the maximum frequency per the design · Chancery consumption as an ingestion source (the `chancery` vocabulary slot and its precedence rank exist; nothing writes through it yet).

**Next: Phase C — the S21 sunburst rollup into `entity_holdings`.** Its input is now real: positions exist, and precedence decides which of several competing rows the rollup should count. A rollup built before precedence would have double-counted every holding reported by two sources. ✅ Built — see 7k.

---

## 7k · Completed — Portfolio C, the rollup into `entity_holdings`

**The sprint that finally puts data in front of the Sprint 21 sunburst.** `services/portfolio_rollup.py` · `POST /api/v1/portfolio/rollup` in `routers/portfolio_ingest.py` · `scripts/verify_portfolioc.py`. **22 PASS, 0 FAIL**, idempotent across consecutive runs, real `app_service` connection.

### The headline: the S21 sunburst renders real data for the first time since it shipped

`services/allocation_lens.py` reads `entity_holdings` and nothing else (line ~138). That table has had **no writer at all** since Sprint 21 shipped, so the allocation lens has spent its entire life drawing an empty actuals tree against real targets — a screen that looked finished and reported nothing. Phase C is the writer. **Given a rollup run for an org and an as-of date, the sunburst now renders real positions.** No change was made to `allocation_lens.py` or to the sunburst UI; the rollup's output grain was read off the lens's actual query rather than guessed at.

### What it does

`rollup_entity_holdings(conn, *, org_id, as_of_date)` groups current, non-superseded positions by `(entity_id, taxonomy_key)` and writes one `entity_holdings` row per bucket under `source = 'portfolio'`. `taxonomy_key` falls back from `positions.taxonomy_key` to `assets.default_taxonomy_key`.

**Look-through attribution, not direct ownership.** A position's value lands in the direct owner's bucket *and* in every ancestor's, each at its own compounded percentage. An individual who holds everything through a trust owns nothing by `owner_entity_id`, and a rollup keyed to that column would have rendered them an empty sunburst that is not wrong so much as meaningless. The percentages come from **`entity_graph.get_lookthrough`** — the same BFS `resolve_entity_set` and the Ownership Tree Graph use. This module computes no ownership percentages of its own; it calls the existing engine once per entity that owns anything and inverts the direction. Verified with an exact figure: an individual owning 50% of a trust that owns 60% of an LLC holding $100,000 gets **exactly $30,000.00** — 50% and 60% and their product are three different plausible bugs and only the exact number tells them apart.

**Callable, not trigger-fired — deliberately.** Positions arrive in batches; a row-level trigger would rebuild the buckets after every individual write, and every intermediate state is a real, readable, *wrong* number a member could refresh into mid-import. Rolling up is something you do when a batch is finished, which is a fact only the caller knows.

### Two things the brief did not ask for and the design needed

| | |
|---|---|
| **The rollup DELETES as well as upserts** | An upsert alone is not idempotent in the way that matters. If a position is retired or superseded between runs its bucket is no longer computed, the upsert never touches it, and a stale figure survives forever under the current date. Every run also deletes the `source = 'portfolio'` rows for that `(org, as_of_date)` the new computation did not produce — scoped to this source, so a manual or S21-era holding row from another track is never touched. Asserted separately: retire a position, re-run, the bucket is gone rather than standing at its last figure. |
| **A position with no mark is skipped and COUNTED, never zeroed** | Same rule `resolve_current_value` already enforces. A zero for "we have no valuation" is indistinguishable from a genuine zero once summed, and the fact that it was never measured is gone. `RollupResult` reports every drop with its reason, so "the sunburst looks light" has an answer. |

### Percent-basis and compounding (Task 3)

`ownership_basis = 'percent'` means `ownership_pct` is the authoritative measure, so the position's value is that fraction of the **asset's own resolved valuation** via `portfolio_assets.resolve_current_value` — *not* the stored `market_value`, which on a percent position is a convenience copy that a revaluation of the underlying does not update. Trusting it would freeze an LLC interest at whatever it was worth the day somebody typed it in. Proven: a 25% position with no stored `market_value` reads $100,000 against a $400,000 appraisal, and $200,000 on the next run after a superseding $800,000 appraisal.

### Reported, not papered over — the `subtree` selector double-counts

`aggregate_allocation` accepts two selector shapes and weights them differently. `{"type": "entity", "id": E}` is E alone at weight 1.0 — **exactly correct** against look-through buckets, and it is the default the assistant action uses. `{"type": "subtree", "root_id": R}` is R at 1.0 **plus** every descendant at its `effective_pct` — which double-counts, because R's own bucket already contains the descendants' compounded value and the lens then adds a weighted copy of each descendant's row on top.

`services/allocation_lens.py` is explicitly out of scope for Phase C, so the buckets were written as mandated and this is recorded here rather than silently absorbed. **The fix belongs in the lens** — a `subtree` selector should stop re-weighting descendants now that holdings are themselves look-through — and is a one-line change there. Related and smaller: the lens does not filter on `source`, so a manual `entity_holdings` row and a rollup row for the same `(entity, key, date)` are tie-broken arbitrarily by its `DISTINCT ON`. Phase C owns `source = 'portfolio'` and never reads, writes or deletes another source's rows.

### The endpoint

`POST /api/v1/portfolio/rollup`, form field `as_of_date` (**required**, not defaulted to today: a rollup labels every bucket with that date and the lens picks the latest on or before the queried date, so a mistaken default would stamp a quarter-end position set with today's date and shadow the real one). Gated on **`manage_portfolio`** via the same `require_permission` call every other write on that router already uses — no new gating invented. Synchronous, because the caller wants to know it actually happened.

### Verification — 22/22

`scripts/verify_portfolioc.py`, real DB, real RLS, real `app_service` connection, no `SET ROLE` fallback. Two real ownership chains (100%→100% for reach, 50%→60% for compounding), one contested holding resolved by the **real** `resolve_holding` rather than a hand-set `superseded_by_source`, exact-Decimal assertions throughout. Covers: direct rollup · look-through two levels up · exact $30,000.00 compounding · precedence loser excluded · re-run updates rather than duplicates (row count + a duplicate-group query) · a superseding valuation reflected on the second run · stale bucket removal · cross-org isolation in both directions · the endpoint's 403 for a non-admin and 200 for an authorised caller.

**A trap worth recording:** `rbac.has_permission` **default-allows** a user with zero `user_roles` rows, and `permissions.get_user_id` does **not** look users up by `auth0_sub` — with no namespaced claim and a non-UUID `sub` it returns `uuid5(NAMESPACE_URL, sub)`. A fixture user seeded under a hand-picked `99000000-…` id is therefore a user the endpoint never finds, `load_principal` returns None, and the "non-admin is denied" test passes an endpoint with **no gate at all**. The script seeds under the derived id and gives the member a real role granting a different permission — the only shape in which the strict check actually runs. It caught a genuine false-pass on the first run.

Teardown asserts exact before/after counts on all six portfolio tables **plus `public.entity_holdings` and `public.entity_relationships`**. `entity_holdings` is deleted by fixture *entity*, never by `source` or `as_of_date`: it is a public, tenant-visible table that S21, `services/households.py` and the RLS Batch-A verification all read, and a source-keyed delete would take out a real rollup another track had run for the same date.

### Deliberately NOT built (Phase D and later, per the brief)

The SPV derivation view (Phase D) · any change to `services/allocation_lens.py` or the sunburst UI · cash modelling, corporate actions, commitments, UDFs · a trigger firing the rollup on every position write (see above) · automatic invocation at the end of Phase B's import — the function is callable and the endpoint exists; wiring it into `import_positions` was left out because an import's rows can span several as-of dates and picking one silently would be worse than an explicit call.

**Next: Phase D — the SPV derivation view.** ✅ Built — see 7l. The `allocation_lens` `subtree` double-count flagged above is explicitly *not* part of it and remains open.

---

## 7l · Completed — Portfolio D, the SPV derivation view, cash, and document drill-through

**SPV subscriptions become portfolio holdings without being copied; cash becomes an ordinary asset; portfolio records gain document drill-through.** `docs/portfoliod_part1.sql` · `services/portfolio_spv.py` · `services/portfolio_cash.py` · `services/portfolio_documents.py` · `scripts/verify_portfoliod.py`. **56 PASS, 0 FAIL**, idempotent across consecutive runs, real `app_service` connection.

**No Part 1 SQL was pre-specified for this phase, deliberately** — the correct SPV-valuation join was a real discovery question, not a known shape. `docs/portfoliod_part1.sql` was written *after* Task 1 and reflects what the deployed database actually supports.

### The headline finding: where an SPV interest's current value lives — nowhere, until now

This is the fact worth carrying forward; anyone touching SPV valuation later will otherwise re-derive it. All four plausible homes were traced against the live database:

| Candidate | Verdict |
|---|---|
| `spv_subscriptions.commitment_amount` / `funded_amount` | A commitment and a cost. Neither is a mark. |
| `spvs` | `target_raise` / `minimum_raise` / `hard_cap` / `min_commitment` — fundraising parameters. **There is no NAV, value or market column on the table at all.** |
| `member_investments` | `amount_committed` / `amount_funded`. Same shape, same problem. |
| Sprint-22 GL, `v_capital_accounts` | Structurally the right idea — `sum(credit − debit)` over `is_capital_account` accounts — and **not connected to anything.** |

The GL finding is the important one. `v_capital_accounts` groups by `journal_lines.dim_member_series_id`, which has **no foreign key** (the table's only FKs are `account_id` and `entry_id`), **no referent relation anywhere in the database**, is written solely from a caller-supplied `dims['member_series_id']` in `services/ledger/posting.py`, and is **NULL on every deployed row**. `scripts/verify_sprint22.py` itself passes `str(uuid.uuid4())` for it. And `member_series` is a `spvs.vehicle_type` value — so even fully populated, that dimension is grained at the **series**, not the **subscriber**, and still could not answer "what is this member's interest worth".

**The path Phase D establishes**, using the one join between the two subsystems that was already deployed (`assets_internal_spv_id_fkey`):

```
spv_subscriptions  (valid_to IS NULL, status IN ('committed','funded'), ownership_pct NOT NULL)
  → spvs.id → portfolio.assets.internal_spv_id      (ONE asset per SPV)
  → portfolio.valuations, purpose='market', resolved by A2's ladder
  → value × ownership_pct / 100
```

**If the GL ever starts writing a real per-subscriber capital-account dimension, that becomes the better source and this join should be revisited.** Recorded in the `portfolio_spv.py` module docstring as well as here.

### The view

`portfolio.spv_derived_positions` — a real view, projecting current subscriptions into the full `portfolio.positions` shape (`authority='internal'`, `source_system='spv_subscriptions'`, `ownership_basis='percent'`, `quantity` NULL) plus provenance columns that get from a derived position back to the book of record in one hop. Nothing is stored twice: **zero** rows exist in `portfolio.positions` with `source_system='spv_subscriptions'`.

**`WITH (security_invoker = true)` is the single most important line in the file.** Every base table has RLS with one org-isolation policy, and all of them — plus the view — are owned by `postgres`, which has `rolbypassrls`. A view built the DEFAULT way executes as its owner, so it would have returned **every tenant's subscriptions to every tenant**, through a relation that looks exactly like the org-isolated table it derives from, and nothing would have raised. The verification asserts both the reloption *and* the behaviour on the real `app_service` connection, with a control assertion that the owning org still sees its row — otherwise "the other org sees zero" is satisfied just as well by app_service being unable to read the view at all.

**The read-only contract is enforced three independent ways**, because corrections belong in `spv_subscriptions`: the view is not auto-updatable (`pg_relation_is_updatable = 0`); its write grants are explicitly revoked (`ALTER DEFAULT PRIVILEGES` in the `portfolio` schema grants `app_service` `arwd` on every new relation, and a view is a relation — so it would otherwise have held INSERT/UPDATE/DELETE, harmless only by rewrite-rule technicality); and row ids are **v5 UUIDs under a namespace of their own**, so an id that reaches `record_transaction` is refused by that function's existence check rather than matching something. Proven: `record_transaction` against a derived id raises `PortfolioError` naming the id, with a transaction type that would otherwise have been accepted.

**The value ladder is A2's `resolve_current_value` transcribed into SQL**, and the verification asserts the two agree **exactly** (Decimal equality, no tolerance) rather than approximately — a second, subtly different resolver is how this goes wrong. The fixture is built so a status-only ladder returns the wrong number: an `audited` 500,000 mark superseded by a `final` 800,000 mark on the same date, where forgetting the supersession demotion yields 125,000 instead of 200,000. Arithmetic is `value * pct / 100`, multiplying before dividing — `value * (pct/100)` rounds the quotient first and loses cents on any percentage that is not a terminating decimal.

**Which subscriptions project, and why those.** The predicate is lifted verbatim from `services/spv_allocation.py` — `valid_to IS NULL`, `subscription_status IN ('committed','funded')`, `ownership_pct IS NOT NULL` (its own "skip subs without a post-close ownership pct") — not invented here. The last one is also a hard requirement of the shape: A2's `_validate_basis` refuses `percent` with a NULL `ownership_pct`. **Consequence, stated plainly: both currently-deployed subscriptions are `status='soft'` with `ownership_pct` NULL, so the view returns zero rows in production today.** That is correct, and it means an empty result is the expected state until an SPV closes. `unprojected_subscriptions()` reports every non-projecting subscription with its reason (`superseded` / `status_not_active` / `no_ownership_pct` / `no_spv_asset`) — a derived view's one failure mode a stored table does not have is dropping a row silently, and this is what makes it diagnosable.

### Cash — a position, with no special case anywhere

`ensure_cash_asset(org, currency)` + `record_cash_balance(...)`, both **thin compositions of A2's `create_asset` / `create_position`**. There is no `INSERT INTO portfolio.assets` or `portfolio.positions` in either new module (AST-asserted) — `create_position` is the only thing in the codebase enforcing the ownership-basis contract, so a cash writer that inserted directly would be the one holding type whose basis nobody validated.

`ownership_basis='value'` (no unit, no percentage, the amount *is* the fact) and `valuation_method='amortized_cost'`, which is load-bearing rather than decorative: A2's `record_transaction` derives an asset's market from `valuation_method`, and `amortized_cost` maps to `both`, so public-market and private-market transaction types are both legal against cash. They have to be — cash is the settlement leg of every transaction in either book. `market_price` would have made every capital call against cash illegal.

**A bank account is not a new concept**: an `entity_type='account'` entity as the `owner_entity_id` of a cash position — the identical call to a trust holding cash directly, with a different owner. Nothing inspects `entity_type` or requires an account node. Asserted by writing both and checking they share an asset, a basis and a mechanism.

**Idempotency is enforced by an index, not only by a Python `SELECT`.** `assets_cash_active_uniq` on `(org_id, currency_code) WHERE asset_type='cash' AND valid_to IS NULL AND system_to IS NULL`, and `assets_internal_spv_active_uniq` on `(org_id, internal_spv_id)` likewise — both partial on the current-row predicate, so unlimited bitemporal history stays legal. Without them, two concurrent callers both miss the SELECT and both insert, and the failure is silent: an org's cash split across two lines that neither sums nor reconciles; an SPV projected twice into every rollup. Note the deliberate asymmetry — `assets.currency_code` is NULLABLE and NULL is not equal to itself in a unique index, so `ensure_cash_asset` refuses a NULL currency to close the hole the index cannot.

### Document drill-through — no migration was needed, and that was checked before assuming it

`document_record_links.record_type` is plain `text` with **zero CHECK constraints**; there is no vocabulary in `document_linkage` (non-emptiness only), in `routers/document_links.py` (`record_type: str`), or in the frontend (`DocumentsPanel` passes it through to the URL). The four new values — `portfolio_position`, `portfolio_valuation`, `portfolio_transaction`, `portfolio_asset` — are added **by writing them**. `services/portfolio_documents.py` supplies a Python-side vocabulary so a typo fails at the call instead of writing a link nothing reads back.

**Prefixed, not bare**, because `record_type` is a global namespace shared with Chancery's `entity` / `spv` / `deal` / `transaction` — a bare `'transaction'` would collide with the SPV-ledger links that already use it, and since `record_id` is an unconstrained uuid nothing would raise: the panel would just show one record's documents against another's.

Every function delegates to `services.document_linkage` — no second INSERT, no second lookup query. The read side is `list_documents_for_panel`, byte-for-byte the function behind `GET /records/{record_type}/{record_id}/documents` and the Phase-9 `DocumentsPanel`, so a link written here renders in the existing panel with **no UI work**. Wired at the natural points: `record_valuation_from_document` / `record_transaction_from_document` / `create_position_from_document` / `create_asset_from_document`, plus an optional `document_id` on Phase B's `import_positions_file` (linkage runs last and its failures are recorded on `ImportResult`, never raised — an unlinked position is a documentation gap, an abandoned import is data loss).

### Two things fixed outside the brief, and one flagged and not fixed

| | |
|---|---|
| **`scripts/refresh_schema.py` could not see views** | It filtered `table_type = 'BASE TABLE'`, so a VIEW never reached `docs/schema_snapshot.sql` — and CLAUDE.md's rule is "if it is not in the snapshot, it is not deployed yet". Phase D's entire deliverable is a view, and `v_capital_accounts` / `v_trial_balance` had been undocumented there since Sprint 22. The script now emits a `[VIEW]` section per view with its columns and its `security_invoker` state. |
| **`routers/ledger.py` filters `v_capital_accounts` and `v_trial_balance` on `entry_date`, a column neither view selects** | `GET /ledger/capital-accounts?as_of=…` and `GET /ledger/trial-balance?as_of=…` raise `UndefinedColumn` today. Found while tracing Task 1b. **Not fixed — Sprint 22's territory, out of Phase D's scope.** |
| **`v_capital_accounts` and `v_trial_balance` both run WITHOUT `security_invoker`** | Now visible in the snapshot. They are owned by `postgres` (`rolbypassrls`), so RLS on `journal_entries` / `journal_lines` is bypassed, and `routers/ledger.py` takes `vehicle_id` straight from a query parameter — a caller can read another org's trial balance. **Flagged, not fixed:** it is Sprint 22's surface and warrants its own verification rather than a drive-by `ALTER VIEW`. |

### Verification — 56/56

`scripts/verify_portfoliod.py`, real DB, real RLS, real `app_service` connection, no `SET ROLE` fallback — which matters more here than in any previous phase, because the thing being isolated is a view and a view is org-isolated only if somebody remembered `security_invoker`. All four Task-1 findings are **reported AND asserted** — a finding printed from a docstring and never checked is a claim.

Covers: the view's `security_invoker` / non-updatability / SELECT-only grants / full position-shape column parity · one asset per SPV, idempotent and index-enforced under a simulated race · NULL-with-a-reason before any valuation, never zero · the exact 200,000.00 against the 125,000.00 a naive ladder gives · exact Decimal agreement with `resolve_current_value` · the retired subscription excluded *and* surfaced as `unprojected` · four independent proofs of no write path · an edit to `spv_subscriptions` changing the view on the next read under the same derived id, and a later valuation doing the same · cash idempotency across three calls including a lower-cased currency · bank-account and direct-holder cash sharing one mechanism · float refused · all four record types written with the CHECK count 0 before and 0 after · readback through Chancery's real panel function · cross-org isolation on the view, the cash position, `ensure_spv_asset` and `link_portfolio_document`.

**The cash fixtures use ISO-4217 `XTS` and `XXX`, not `USD`, on purpose.** A cash asset is keyed on `(org_id, currency_code)` and is therefore org-global — it carries no fixture name to delete by. A fixture creating `Cash (USD)` would either delete a real org-wide cash asset on teardown, or, if one already existed, find it instead of creating it and prove nothing about creation. Teardown asserts exact before/after counts on twelve tables including `spv_subscriptions`, `spvs`, `deals` and `document_record_links` — the first three hold real SPV Manager rows, so an unconditional truncate would be a data-loss bug against another track.

### Deliberately NOT built (per the brief)

Corporate actions · commitments-table population · tax-doc tracking · UDFs · any change to `entity_holdings` or the Phase C rollup · any new UI · the `allocation_lens` `subtree` double-count Phase C flagged (its own separate follow-up) · any write path against the view, now or ever.

**Next: Phase E — Chancery-sourced alts and hard assets, commitments, and tax-document tracking.** ✅ Built — see 7m.

---

## 7m · Completed — Portfolio E, Chancery-sourced positions, commitments, tax-doc tracking

**A confirmed Chancery document becomes an asset + position with drill-through to the page it came from; commitments derive their running totals from real transactions; a hard asset carries two valuations at once; and there is finally a list of who is missing a K-1.** Part 1 SQL applied directly (`portfolio.commitments` + `idx_commitments_tax_chase`, RLS enabled, one org-isolation policy) · `services/portfolio_commitments.py` · `services/portfolio_chancery.py` · `GET /portfolio/tax-chase` in `routers/portfolio_ingest.py` · `scripts/verify_portfolioe.py`. **39 PASS, 0 FAIL**, idempotent across consecutive runs, real `app_service` connection.

**A documentation gap found on the way in, recorded rather than papered over:** the brief cited `docs/PORTFOLIO_REPORTING_DESIGN_V6.md` **§12, §13**. That document has never had sections past §9 — the Phase-E specification actually in force is its §7 phase-map row plus the brief itself. The phase map is now updated (E shipped, F next) with a note saying so; the findings below are recorded here rather than back-filled into the design as sections nobody wrote.

### Task 1a — the real Chancery hook point

`services/document_review.py:356` — `confirm_document(conn, org_id, document_id, *, confirmed_by)`. Its entire body is **one UPDATE** setting `documents.status='confirmed'` plus `confirmed_by` / `confirmed_at`, returning those three values. **It has no extension point at all** — no callback, no event row, and it does not return any extracted field.

The seam that exists is one layer up, in `routers/document_review.py:104` (`POST /documents/{id}/confirm`): the router calls `review.confirm_document` and *then*, only on success, calls `chancery_workflow_bridge.fire_document_confirmed_triggers(pool, org_id, document_id, started_by=user_id)`. That ordering — status write first, fire second, bridge written never to raise — is the established Phase-7 pattern and the verification asserts it still holds in the source.

**What is available at that point, and nothing more:** `org_id` (JWT claims via `get_org_id`), `document_id`, `user_id`, the pool. **Confirmed extraction fields are NOT passed** and must be read back by `document_id` — which is what `portfolio_chancery.read_document_extractions` does.

**Phase E deliberately adds no second auto-fire to that router.** "This document represents a position" is not a decision an auto-fire can make: the same confirmed capital-account statement is a NEW position in the first quarter and a VALUATION on an existing one every quarter after, and nothing in the document distinguishes them. `create_position_from_chancery_document` is therefore explicitly called, and *verifies* the document reached the confirm hook (`status='confirmed'`, overridable only by an explicit `require_confirmed=False` for a deliberate historical backfill) rather than hanging off it.

### Task 1b — the extraction-field mapping is a GAP, and it is reported as one

Both deployed extractors were read and the live `reference_data` catalogue queried. **No deployed Chancery extractor produces commitment figures.**

| | |
|---|---|
| **Narrative (Phase 11a)** | `document_narrative_extractions` has exactly four payload columns: `summary`, `extracted_provisions`, `key_dates`, `key_parties`. Item shapes are fixed by `normalize_extraction` at `{provision_type, description}` / `{date, description}` / `{name, role}`. **Not one monetary key anywhere.** It also would not run on this document: `run_narrative_extraction` is gated on `_NARRATIVE_CATEGORIES = {llc_formation, trust_instrument, will, estate_plan, operating_agreement}`. |
| **The catalogue** | 12 `doc_category` codes deployed (org_id NULL, all active) and **there is no capital-account-statement code among them**. The nearest — `financial_statement`, `subscription_doc` — are both `doc_family='tabular'`, i.e. routed to the K-1 extractor, not the narrative one. |
| **Tabular (Phase 3)** | `'k1'` is the **only** template that exists. Its `mapped_fields` keys are five income boxes (`ordinary_business_income`, `net_rental_real_estate_income`, `interest_income`, `ordinary_dividends`, `net_long_term_capital_gain`) plus the **recipient's** name (`partner_name` / `shareholder_name` / `beneficiary_name`). |

**So `commitment_amount`, `called_to_date`, `distributed_to_date` and `recallable_amount` require a NEW extraction template that does not exist**, and building it is Chancery's work, not the portfolio layer's — guessing at its output shape now would mean writing a mapper against field names nobody has chosen. `portfolio_chancery.COMMITMENT_FIELDS_NOT_EXTRACTED` names the four, and `commitment_fields_from_document()` returns them as `missing` with the reason on every call, so a caller that assumed extraction would supply them finds out **at the call** and not from a zero in a report.

**What genuinely exists is what gets mapped.** `derive_asset_name` is a three-rung ladder over real fields and reports which rung it used: an explicit human-supplied name → the first `key_parties[].name` from narrative extraction (a real, document-stated name; for an `llc_formation` or `operating_agreement` the instrument's own named entity genuinely *is* the asset) → `documents.original_filename` (NOT NULL, so this rung never fails, and a filename is visibly provisional, which is what makes somebody fix it). **Deliberately not a rung: the K-1's `partner_name`** — that is the recipient, not the partnership, and using it would name the asset after its holder, a mistake that looks correct in a list.

### Commitments — derived, explicitly, never by trigger

`recompute_commitment` sums the position's real transactions joined to `public.transaction_types`, reading `affects_paid_in` / `affects_unfunded` / `is_recallable` off the catalogue. **Nothing pattern-matches the `call_` / `dist_` prefix** — a new type with the right flags is picked up with no code change, which is the whole point of the flags existing.

It is not a trigger, for Phase C's reason: a capital call posts as several transactions (`call_investment` + `call_mgmt_fee` + `call_org_cost`), and a row-level trigger would fire between them and leave the commitment stating a called-to-date that was never true. It is also **idempotent by construction** — it re-derives from the ledger rather than incrementing, so a double call, a replayed import or a crash between transaction and recompute all converge.

`create_commitment` leaves `unfunded` **NULL**, not `commitment_amount`. That NULL means "not yet derived"; writing the commitment amount there would make a derived column look like a stated one and erase the difference between a commitment that has never been recomputed and one whose recompute happened to return the full amount.

**One thing flagged, implemented as specified rather than silently changed.** The brief's formula is `unfunded = commitment_amount - called_to_date + recallable_amount`, and that is what ships (`portfolio_commitments.UNFUNDED_FORMULA`). But on the deployed catalogue `affects_unfunded` is the **exact negation** of `affects_paid_in` on all five non-zero codes, so a purely flag-driven accumulator gives `commitment_amount - called_to_date` — **10,000 lower** after a 10,000 recallable distribution, because `dist_recallable`'s `affects_paid_in = -1` has already restored that capacity through `called_to_date`. The brief's formula adds `recallable_amount` on top, so a recallable distribution moves `unfunded` by twice its face value. `CommitmentTotals.unfunded_flag_driven` computes the alternative and the verification asserts the difference is **exactly** the recallable amount (970,000 vs 960,000) — so the choice is a measured number rather than a claim in prose. If it should change, it is one line plus the constant, and every stored figure is re-derivable by re-running the recompute.

Smaller decisions worth not re-deriving: the amount is `COALESCE(gross_amount, net_amount)` (a 50,000 call with a 500 fee on the same row *was* a call for 50,000, and `called_to_date` is a gross figure in every LP statement it will be reconciled against); `distributed_to_date` is keyed on the real `transaction_types.category = 'distribution'` column, not a code prefix; transactions carrying neither amount are counted and returned as `amountless_transactions`, because a units-only stock distribution silently valued at zero is exactly the absence that reads as a number. The recompute **UPDATEs in place** and that is not a Rule 3 violation: these four columns are an arithmetic projection of `portfolio.transactions`, which *is* the bitemporal history — and superseding the commitment row on every recompute would bury the one thing on this table that *is* a bitemporal fact, `tax_doc_status`'s `awaiting → received` timeline, under a torrent of arithmetic.

### Composition — Phase D's writers, unchanged

`portfolio_chancery.py` calls `portfolio_documents.create_asset_from_document` and `create_position_from_document` and contains **no `INSERT INTO portfolio.*` and no write against `document_record_links`** (AST-asserted in the verification, the same way Phase D asserted it of the cash module). That is load-bearing rather than tidy: `portfolio_assets.create_position` is the only code in the codebase enforcing the ownership-basis contract (`positions` has no CHECK covering it), and `link_portfolio_document` is the only thing checking the record-type vocabulary against a column that has no CHECK either.

Two shapes are fixed, not parameters: `authority='stated'` and `source_system='chancery'` — a caller able to pass `authority='custodial'` would be asserting a custodian confirmed what a PDF asserted, and Phase B's precedence engine ranks sources by exactly that field. `valuation_method` defaults to `'nav'`, **not** A2's `'market_price'`: a holding whose source of truth is a PDF has no listed price series, and A2's `record_transaction` derives an asset's market from that column — `market_price` would make `call_investment` illegal against the position this function just created. The ownership basis is resolved **once** and passed to both writers (`infer_ownership_basis`), because A2's create-position inherits an omitted basis from the asset and this function creates both in the same breath: an asset defaulted to `units` while the caller supplied `ownership_pct` yields an error about a basis nobody chose.

### The hard asset — and why "the final state is right" is not the assertion

`asset_class='hard_asset'` and `include_in_performance=false` being true of the stored row proves the row has those values, which is *also* what you get if the defaults were those values all along. So the verification reads the deployed defaults out of `information_schema` (`'financial'::text` / `true`, confirmed live), asserts the stored values differ from **both**, and creates a **control** asset through the same function with no overrides and asserts it lands **on** them. Only the pair means the override did work.

The two-purpose assertion is fixture-designed the same way. An `insurance` valuation of 1,450,000 and a `net_worth` valuation of 1,200,000 coexist on one asset — and the `net_worth` one is deliberately dated **later** (2026-06-30 vs 2026-01-31), so a purpose-blind "latest row wins" resolver would return 1,200,000 for both and the insurance assertion fails. A2's `resolve_current_value` filters on `purpose` and returns each correctly, plus an honest `None`-with-a-reason for `market`, which has no valuation at all.

### The chase list, and asking the index question properly

`GET /portfolio/tax-chase?tax_year=…`, gated on `view_portfolio` (it reads and writes nothing; which commitments are outstanding is what an administrator chasing documents needs, not a portfolio-management action). `tax_year` is required and not defaulted to "last year" — in January that is two different years to two people in the same office.

The query spells out every term of `idx_commitments_tax_chase`'s **partial** predicate (`tax_doc_expected = true AND system_to IS NULL AND valid_to IS NULL`) because that is the only way the planner can prove the index applies; dropping `system_to IS NULL` because "nothing writes it yet" would silently cost the index. Three cases are proven **distinctly**: expected + `awaiting` appears, `received` does not, `tax_doc_expected=false` does not — plus a wrong-tax-year control, so the year filter is shown doing real work.

**The EXPLAIN assertion runs with `enable_seqscan=off`, and that is the only honest way to ask.** The planner is cost-based: on a four-row fixture table a sequential scan genuinely *is* cheaper than any index, so a plain EXPLAIN measures the row count and not the query. With seqscan discouraged, a query the partial index could not serve still cannot use it — it falls back to `idx_commitments_org` or to a seq scan anyway — so seeing `idx_commitments_tax_chase` **by name** in the plan is a real proof of applicability. Both plans are printed: the cost-based one as a FINDING, the forced one as the assertion.

### Verification — 39/39

`scripts/verify_portfolioe.py`, real DB, real RLS, real `app_service` connection, no `SET ROLE` fallback. All four Task-1 findings are **reported AND asserted** — a finding printed from a docstring and never checked is a claim.

Covers: the confirm hook's real signature and the router's ordering · the narrative and K-1 field inventories, and the absence of any capital-account category · the AST proof of composition · the deployed defaults · exact-Decimal commitment arithmetic through a 50,000 call (called +50,000, unfunded −50,000), a 10,000 recallable distribution (called −10,000, distributed and recallable +10,000 each) and a 7,500 `dist_income` that moves **nothing** but `distributed_to_date` · recompute idempotence and the amountless-transaction count · the Chancery position's `stated`/`chancery` shape, both links read back through Chancery's real `list_documents_for_panel`, and an unconfirmed document refused with nothing left behind · the hard-asset override against a control · two purposes at once with the later one deliberately the wrong answer for a purpose-blind resolver · the three chase-list cases, the wrong-year control, the endpoint, and the index by name · cross-org isolation on the commitment, the chase list, the recompute and the Chancery creation function, each with a control assertion that the owning org still succeeds.

Teardown asserts exact before/after counts on **eleven** tables including `portfolio.commitments`, `public.documents`, `public.document_record_links` and `public.entities` — all of which hold real production rows, so an unconditional truncate would be a data-loss bug against another track. Every fixture row carries the `VERIFY-PORTFOLIOE` tag in a natural-key column and is deleted by it, child tables first.

### Deliberately NOT built (per the brief)

Corporate actions (Phase F) · UDFs (Phase G) · the reconciliation engine, performance calculations and cross-client analysis (Phase H — designed for, not built) · any change to Phase D's SPV derivation view or Phase C's rollup · a capital-account-statement extraction template (Chancery's work; the gap is named in `COMMITMENT_FIELDS_NOT_EXTRACTED` rather than papered over) · a second auto-fire hanging off `POST /documents/{id}/confirm` (see Task 1a) · any UI beyond the one endpoint · the `allocation_lens` `subtree` double-count Phase C flagged, still open.

**Next: Phase F — corporate actions.** `portfolio.transactions.corporate_action_id` already exists as a nullable column with no referent table, which is the shape Phase F fills in. ✅ Built — see 7n.

---

## 7n · Completed — Portfolio F, corporate actions

**A split, a reverse split and a spinoff are recorded once, globally, and applied by each org to its own positions independently.** Part 1 SQL applied directly (`portfolio.securities_global_corporate_actions`, RLS enabled with A1's four-policy global shape; `transactions.corporate_action_id` given a real FK; `transactions.is_corporate_action_adjustment` added) · `services/portfolio_corporate_actions.py` · one additive change to `services/portfolio_assets.record_transaction` · `scripts/verify_portfoliof.py`. **57 PASS, 0 FAIL**, idempotent across consecutive runs, real `app_service` connection.

### The §10 correction — global, not tenant — and why it was necessary

The design's original §10 sketch keyed `corporate_actions` to `asset_id`, which is **tenant-scoped**. That was wrong for exactly the reason A1 keeps prices and identifiers global: a 2-for-1 split of one security is **one real-world event about one security**, not a fact that becomes true once per tenant that happens to hold it.

Keyed to `asset_id`, the same split would have been recorded N times — once per holder — with N independently editable copies of the ratio and the ex-date. Nothing would raise when two of them disagreed; two tenants would simply restate the same holding by different multipliers, and there would be no row anywhere that was *the* split. Corrections would be worse: a ratio republished by the issuer would have to be chased across every tenant copy, and the ones missed would stay silently wrong.

Corrected in this sprint's Part 1 SQL to `portfolio.securities_global_corporate_actions` — **global scope, no `org_id`**, RLS identical in shape to A1's other global tables (`USING (true)` read + Super-Admin `INSERT`/`UPDATE`/`DELETE`). Two consequences that are the whole architecture of this phase:

- **RECORDING is global and Super-Admin-gated.** One fact, once. `record_corporate_action` composes A1's real conventions rather than inventing parallel ones — the same `_require_super_admin` app-layer gate for a legible refusal, the same `_SuperAdminWrite` transaction-local elevation so RLS stays the real gate, and the same `COALESCE(canonical_id, id)` merge-chain forwarding `add_price` uses.
- **APPLYING is tenant-scoped.** Every org holding the security applies the SAME recorded event to its own assets and positions, independently. One org's apply provably does not touch another's — asserted directly against the `app_service` connection with a control, not inferred from the existence of a policy.

Also fixed: `portfolio.transactions.corporate_action_id` had existed as a **bare `uuid` with no FK since A2** (the referent table did not exist yet when A2 shipped) and now has `transactions_corporate_action_fkey`. `transactions.is_corporate_action_adjustment boolean NOT NULL DEFAULT false` was added so an adjustment can never be misread as an ordinary trade.

### `applied_at` is never written by an apply function

It is a column on the **global** row, and "applied" is a per-org fact. Stamping it when the first org applies would tell the second org the event had already been handled. Whether a given org has applied an action is answered by `already_applied_transactions`, which reads that org's own transactions — which is also the idempotency key.

### Consumed, not computed

`terms` is published data — a custodian feed or a market-data provider's already-declared ratio and Form-8937 allocation. Nothing in this module derives a split ratio from a price discontinuity or invents a cost-basis split. `record_corporate_action` therefore validates that `terms` is present and is a **non-empty JSON object and nothing further**: the keys that matter differ per `action_type` and are read at apply time by the function that needs them, so a `merger` recorded for future use is not forced to invent a `ratio`. `cash_in_lieu_per_share` is read, reported back on `ApplyOutcome.unapplied_terms`, and deliberately **not** turned into a cash movement this module would have to compute.

### `adjustment` is the honest type — measured, not assumed

Read from the deployed `public.transaction_types`: of the 16 rows, `adjustment` is the **only** one that is simultaneously `direction='none'`, `performance_impact='none'`, `affects_paid_in=0`, `affects_unfunded=0`, `affects_nav=0` and `market='both'` — so it attaches to a listed equity and a private fund interest alike and registers as neither gain, income, contribution nor distribution. `sell` carries `performance_impact='gain'` and would put every split into realized gains; `fee_expense` is `market='both'` but `direction='debit'`/`affects_nav=-1`; `valuation` is `direction='none'` but `market='private'`/`affects_nav=1`.

**Reported mismatch, not papered over:** `adjustment.amount_basis='currency'` while a split adjustment carries a **unit** delta. There is no units-based, performance-neutral type in the deployed vocabulary, and adding one is a schema change this sprint did not ask for.

### The flag is set explicitly, not derived

`is_corporate_action_adjustment` is a real parameter on `record_transaction`, not something computed from `corporate_action_id IS NOT NULL`. Two reasons. A report must be able to write `WHERE is_corporate_action_adjustment = false` and get a correct realized-gain population **without knowing the corporate-action machinery exists at all** — that is Task 5's whole requirement. And deriving it would collapse the one case where the two legitimately differ: a cash-in-lieu *sale* cites a corporate action and genuinely **is** a realized gain.

A2 shipped before the column existed, so `record_transaction`'s INSERT did not name it — every adjustment would have silently stored the column default. That was the one additive change made outside the new module.

### Bi-temporal restatement, and the two things that are silent if you get them wrong

A split changes quantity, so per Rule 3 the current row is closed (`valid_to = now()`) and a successor inserted through A2's real `create_position`. The only `UPDATE` in the module is that close; it touches `valid_to` and nothing else. Writes go through `create_position` / `create_asset` / `record_transaction` rather than direct SQL because `create_position` is the only code in the codebase enforcing the ownership-basis contract (`positions` has no CHECK covering it) and `record_transaction` is the only thing checking a type's `market` against the asset's.

- **The position id changes.** Idempotency therefore keys on `(org_id, corporate_action_id)`, never on a position id — a position-keyed check would be looking for a marker on a row the restatement just closed, and would happily double-adjust.
- **`as_of_date` is preserved deliberately.** Advancing it to the ex-date would mint a second holding under a different natural key `(owner_entity_id, asset_id, as_of_date)` — which is exactly what `portfolio_precedence.resolve_precedence` resolves on, and it would then see two holdings where there is one.

Precedence *losers* (`superseded_by_source IS NOT NULL`) are adjusted too. They are still current rows, and leaving one at its pre-split quantity means the day the org re-orders its sources, `resolve_precedence` promotes a number wrong by the split ratio.

### Atomicity is the outer transaction, not `_OrgWrite`

`_apply` opens **one** transaction and calls A2's writers inside it, where their `_OrgWrite` nests as a SAVEPOINT. Left to `_OrgWrite` alone, each writer commits per call — a spinoff would commit the parent's restated basis and then, if the resulting-side insert failed, leave the org holding half an event with no error visible in the data.

### Three states a caller must be able to tell apart

`ApplyOutcome` distinguishes them explicitly, because two of them are both "zero positions affected":

| State | `positions_affected` | `already_applied` |
|---|---|---|
| Org holds none of the security | 0 | `False` — and **nothing is written**, so a later apply after the org buys in still works |
| Org already applied it | 0 | `True`, plus the prior transaction ids |
| Applied now | *n* | `False` |

`percent`- and `value`-basis positions are **skipped with a reason** on `ApplyOutcome.skipped`, not failed and not silently adjusted: a share ratio does not change a percentage of ownership or a stated currency value. "12 affected" out of a 14-position holding is a number somebody has to be able to reconcile.

`merger`, `tender`, `delisting`, `name_change` and `cusip_change` **record** cleanly and are refused **by name** by `apply_corporate_action` (`UnapplicableActionError`) rather than returning a clean zero — which would be indistinguishable from "this org holds none of it".

### Verification — 57/57

`scripts/verify_portfoliof.py`, real DB, real RLS, real `app_service` connection, no `SET ROLE` fallback. All four Task-1 findings **reported AND asserted**.

The four assertions this phase is easiest to fake, and how they are written:

- **"The split was applied."** `quantity = 200` proves nothing alone — it is also what a fixture seeded at 200 gives you. The pre-split row is asserted at `100` **first**, the successor at `200`, and the closed predecessor asserted to still read `100` with a non-null `valid_to`.
- **"Total cost basis is unchanged."** Trivially true of code that never touches cost basis. So the spinoff case, on the same code path, asserts basis **did** move (30,000 → 24,000 on the parent, 6,000 to the resulting position, summing back to 30,000) — a module that ignored `cost_basis` passes the split assertion and fails this one.
- **"The adjustment is excluded from realized gains."** A filtered query returning one row proves nothing if the fixture only ever had one. The holding's history is built with a real `buy` **before** the split and a real `sell` **after** it, spanning the bi-temporal restatement; the unfiltered query must return all three, the `= false` filter exactly the buy and the sell, and the realized-gain sum exactly −5,000 + 2,600 = −2,400.
- **"The other org is unaffected."** Snapshotted before the first org's apply and asserted byte-identical after — **and** the second org is then made to apply the same action successfully through `app_service` (400 → 800), proving "unaffected" was isolation and not a broken apply, with the first org re-asserted at 200 afterwards.

Also covered: the four-policy shape read from `pg_policies` · `ACTION_TYPES` asserted equal to the deployed `corp_actions_type_chk` · the non-super-admin refusal **with a row-count assertion**, plus a separate proof that RLS is the real gate (a direct `INSERT` from `app_service` with no elevation raises `InsufficientPrivilegeError`) · reverse split 500 → 50 with a negative −450 delta · `parse_ratio` refusing float, zero, negative, empty and non-numeric · schema-qualification of every `portfolio.*` reference.

Teardown asserts exact before/after counts on six tables **including the new global `securities_global_corporate_actions` and `securities_global` itself** — the latter holds the real 67-row reference corpus, so an unconditional truncate would be a data-loss bug against another track. FK order is load-bearing now: adjustment transactions before corporate actions (the new FK), tenant assets before the global securities they point at.

### Deliberately NOT built (per the brief)

UDFs (Phase G) · the reconciliation engine, performance calculations and cross-client analysis (Phase H — designed for, not built) · deriving a split ratio or a spinoff allocation independently — terms are always supplied · merger / tender / delisting application logic beyond recording the `action_type` · cash movement for `cash_in_lieu_per_share` · any router and any UI · the `allocation_lens` `subtree` double-count Phase C flagged and the `routers/ledger.py` `entry_date` bug Phase D flagged, both still open.

**One documentation gap, same shape as Phase E's.** The brief cited `docs/PORTFOLIO_REPORTING_DESIGN_V6.md` **§10**. That document still has no sections past §9 — the specification actually in force is its §7 phase-map row plus the brief. The phase map is updated (F shipped, G next); the findings are recorded here rather than back-filled as sections nobody wrote.

**Next: Phase G — UDFs (user-defined fields).** ✅ Built — see 7o.

---

## 7o · Completed — Portfolio G, user-defined fields · **THE PORTFOLIO REPORTING LAYER (A1–G) IS NOW COMPLETE**

**Four parties author custom fields — the platform, an org, a team, a person — and they do not compete.** Part 1 SQL applied directly (`portfolio.udf_definitions`, `portfolio.udf_values`, both RLS-enabled) · `services/portfolio_udf.py` · `scripts/verify_portfoliog.py`. **63 PASS, 0 FAIL**, idempotent across consecutive runs, real `app_service` connection, no `SET ROLE` fallback. No changes to any existing module.

### The §15 refinement — parallel namespaces, not a cascade

The obvious design is an override chain: user beats team beats org beats platform, one winner per `field_key`. That is **not** what this is, and the difference is the whole phase.

Under a cascade, the platform's `asset_classification` and a client's `asset_classification` are the same field with two candidate values, and something must pick one. That silently destroys the ability to say *"the industry-standard feed says **equity**, **and** this client books it as **debt**."* Both are true, both are wanted, and a report that can only see the winner cannot reconcile them — which is precisely what a reconciliation engine (Phase H) will need to do.

So there is **no merge and no override anywhere in the module**. `resolve_visible_definitions` returns every definition a user can see, from all four scopes, side by side, each carrying its own `owner_scope` and its own `id`. Two definitions sharing a `field_key` across scopes is a normal, expected, non-error state. Values bind to a `definition_id`, **never** to a `field_key`, so there is never a question of which definition a stored value belongs to — `get_udf_value` has no `field_key` parameter at all, which is asserted by signature inspection.

### Where enforcement lives, and why it is split in two

RLS carries the **hard boundary and only that**: cross-org isolation, platform global read, Super-Admin for platform-scope writes. Team and user narrowing lives in Python, in `resolve_visible_definitions`.

That is the **same division A2 already made** for the ownership-basis contract, and for the same reason. The database can cheaply prove a tenant boundary because `org_id` is on the row. It cannot cheaply prove "this user is on that team" without a correlated subquery on every row of every read — and a policy that is expensive gets disabled, at which point the boundary it was protecting is gone.

**Stated plainly rather than glossed:** a caller who bypasses `resolve_visible_definitions` and issues a raw `SELECT * FROM portfolio.udf_definitions` **will** see their own org's team-scope rows for teams they are not on. That is not a hole in RLS; it is the boundary drawn where it was designed to be drawn. There is exactly one resolver, and `list_udf_values_for_target` reuses its predicate rather than filtering after the fact — so a team-scope *value* cannot leak by the simple route of reading the value table directly.

### The membership mechanism is `team_members` — `staff_assignments` was considered and rejected

The brief offered "SOC Phase 2's `staff_assignments` or an equivalent membership table". Introspected, the real answer is **`public.team_members`** — PK `(team_id, user_id)`. `staff_assignments` maps a team-or-user to an **entity** (`staff_assignments_exactly_one_target`) and answers *"who covers this client"*, which is a different question that happens to mention teams.

`team_members` carries **no `org_id` of its own** — its RLS policy reaches the org through an `EXISTS` on `teams` — so every membership predicate in the module `JOIN`s `public.teams` and constrains `t.org_id`. Precedent: `services.staff_visibility.get_team_ids_for_users` already does exactly this. Dropping that join would let a membership row from another tenant satisfy the check.

### What the database gates, and what only Python can

**Duplicates are the database.** There is no pre-flight `SELECT` looking for an existing `field_key` — the `INSERT` is issued and the deployed partial index raises. Two reasons: a pre-check is a race (two concurrent creates both see nothing and both insert), and, worse, it would pass a verification suite **even if the index had been dropped**. The verification asserts the raised exception carries `constraint='idx_udf_def_key_unique'` — a name an application-level check could not produce.

**Cross-scope ownership is Python, because it cannot be anything else.** `owner_scope_id` is polymorphic — a team id under `team` scope, a user id under `user` scope — so it carries **no FK** and the database cannot check it at all. A `team_id` from another org would sit in the row looking entirely valid and fail only as a silent absence from every resolution months later. Both are refused at creation with `UdfScopeError`, against a **real** team in a **real** other org (a randomly minted uuid would also be refused, and for the wrong reason).

### Findings from the introspection, recorded

- **`udf_def_scope_org_chk` is stricter than the brief described.** The brief said only that `org_id` is NULL for platform scope. The deployed CHECK **also** requires `owner_scope_id IS NULL` for platform — a real schema-level gate, not a runtime one.
- **`idx_udf_def_key_unique` COALESCEs `org_id` and `owner_scope_id` to a zero uuid.** That is load-bearing, not stylistic: NULLs are distinct in a btree, so a bare column list would never catch a duplicate **platform** definition at all.
- **`udf_values` has no unique CONSTRAINT — only the partial INDEX.** So `ON CONFLICT ON CONSTRAINT` is impossible and the conflict target must be inferred by repeating both the column list **and** the predicate; omit the `WHERE` and PostgreSQL raises 42P10.
- **RLS is enabled but NOT FORCED on either table.** `postgres` owns them and has `rolbypassrls`, which is why the verification refuses to start without `APP_SERVICE_DATABASE_URL`.
- **Policy counts confirmed exactly** — 4 on `udf_definitions` (SELECT/INSERT/UPDATE/DELETE), 1 on `udf_values` (ALL).

### The value contract

Typed against the definition's real `data_type`, with A2's float refusal carried over verbatim: a UDF numeric is no less load-bearing than a position measure just because a tenant defined it. `Decimal`/`int`/`str` accepted, `float` refused (`Decimal(0.1)` is `0.1000000000000000055…` and no error is raised anywhere downstream). A `datetime` is **refused, not truncated**, because it is a subclass of `date` and the column is `date`. `"true"` and `1` are refused as booleans — truthiness would read the string `"false"` as True. A `select` value must be in its definition's own option list, and a `select` definition with no options is refused at creation because it could never accept any value.

`coerce_value` returns **all four** value columns with exactly one populated, so an upsert writes NULL over the other three — a definition whose `data_type` was corrected cannot strand a value in the old column beside the new one, where two readers would disagree about which is the value.

**A `target_type` that disagrees with its definition's `applies_to` is refused.** `udf_values.target_id` is polymorphic and has no FK, so a mismatched row would not error — it would just never join to anything, and the value would look like it was never recorded.

### One deliberate divergence from Rule 3, named

`record_udf_value` **UPSERTs** — `ON CONFLICT … DO UPDATE` on the real partial index — rather than closing the old row and inserting a successor. That is a departure from CLAUDE.md Rule 3 and it is deliberate: the design's own requirement is "one current value per definition per target". A UDF value is a tenant's own annotation with no accounting consequence and no downstream restatement, unlike a position quantity that a corporate action restates and that a report must read "as of" a past date. Closing and re-inserting would grow an unbounded history of edits to a free-text note nothing reads. The bi-temporal columns remain on the table and the partial index is predicated on them, so if Phase H ever needs value history the change is to one statement and nothing else.

### Verification — 63/63

All four Task-1 findings **reported AND asserted**, including the real team-membership mechanism.

The five assertions this phase is easiest to fake, and how they are written:

- **"A non-member does not see the team's field."** A resolver returning an empty list satisfies this on its own. So **both** directions are asserted against the same call — the member's list must **contain** the team field, the non-member's must **omit** it — **and** the non-member's list is separately asserted non-empty and to contain the platform and org fields, proving the resolver ran and genuinely narrowed. `is_team_member` is also asserted directly in both directions, because "the definition did not appear" is also what a broken query returns.
- **"A duplicate is refused."** Any exception satisfies "it raised" (Phase B's finding). The refusal is asserted to be a `UdfDuplicateError` whose `.constraint` is literally `idx_udf_def_key_unique`. Plus the converse: the **same** `field_key` in a **different** namespace (team scope) is asserted to succeed — so the refusal was a real collision, not a blanket ban on the key.
- **"A platform write is refused for a non-super-admin."** Trivially true of code that never writes. The platform-scope row count is snapshotted before the refusal and asserted unchanged after, the refused `field_key` asserted absent everywhere — and the **same arguments** are then accepted under a Super-Admin caller through the real `app_service` connection, proving the refusal was the privilege check and not a broken statement.
- **"A numeric round-trips."** `str()` of the returned value is compared against the literal `1234.56789012` digit-for-digit **and** the type asserted to be `Decimal` — equality alone can pass on a silently converted float. The float refusal then asserts the previously stored value is **untouched** by the attempt.
- **"The two `asset_classification` definitions coexist."** Two rows existing proves nothing about disambiguation. A **different** value is recorded against each on the **same** target — `equity` from the feed, `debt` as the house view — and each is read back **by `definition_id`** and asserted to be its own value. An implementation matching on `field_key` would return the same row twice and fail.

Cross-org isolation is asserted with a **control** in both directions: org B can create its own definition and record its own value against the identical `target_id`, so "org A's rows are invisible" is isolation and not a broken write. `get_definition` and `get_udf_value` both return `None` from org B's context with org A's ids in hand.

**One thing the verification reports rather than asserts as a win.** A2's `_OrgWrite` **sets `app.current_org_id` from its `org_id` argument** — that is the entire point of the class. So `record_udf_value(org_id=<org A>)` called on a connection whose context is org B **succeeds**. RLS is not a defence against a caller that passes the wrong `org_id`; it is a defence against a connection that never set one — demonstrated in a deliberately rolled-back transaction, with the raw-INSERT case (GUC left at org B) asserted to raise `InsufficientPrivilegeError`. This is exactly why CLAUDE.md's standing rule is *"`org_id` never from a request body"*: the router's JWT claim is the boundary. The module never defaults `org_id` and never reads it back off the connection. Reads are genuinely RLS-gated; writes are gated by the caller's honesty about the claim, and always have been across every phase since A2.

Teardown asserts exact before/after counts on five tables — `udf_values`, `udf_definitions`, `team_members`, `teams`, `users` — all by fixture tag, never a truncate. Platform-scope fixtures have `org_id IS NULL` and cannot be found by any org predicate, so they are matched by the tagged `field_key`.

### Deliberately NOT built (per the brief)

The reconciliation engine, performance calculations and cross-client analysis (**Phase H — designed for, not built**) · any UI beyond what was needed to prove the resolution logic · **any general "override" or "merge" mechanism** — see the §15 refinement above · any router (`portfolio_udf` is a service module only) · the `allocation_lens` `subtree` double-count Phase C flagged, the `routers/ledger.py` `entry_date` bug and the two GL views missing `security_invoker` Phase D flagged — all still open.

### The layer is complete

**Portfolio Reporting phases A1, A2, B, C, D, E, F and G are ALL COMPLETE.** Every phase `docs/PORTFOLIO_REPORTING_DESIGN_V6.md` designed has shipped and is verified against the deployed database:

| Phase | Scope | Verification |
|---|---|---|
| **A1** | Global security layer | 39/39 |
| **A2** | Tenant assets / positions / transactions / valuations | 63/63 |
| **B** | Ingestion + source precedence | 50/50 (+2 BLOCKED — Altruist credentials) |
| **C** | Rollup into `entity_holdings` | 22/22 |
| **D** | SPV derivation view, cash, document drill-through | 56/56 |
| **E** | Chancery-sourced positions, commitments, tax-doc tracking | 39/39 |
| **F** | Corporate actions (global record, per-org apply) | 57/57 |
| **G** | User-defined fields (parallel namespaces) | 63/63 |

**What remains is Phase H, and it is explicitly designed-for-not-built:** the **reconciliation engine**, **performance calculations**, and **cross-client analysis**. The schema and the service layer were shaped to accommodate all three — bi-temporal columns throughout, `superseded_by_source` precedence annotation rather than deletion, `is_corporate_action_adjustment` so a realized-gain population is correct without knowing the corporate-action machinery exists, and Phase G's parallel namespaces so a standard feed and a house view can be compared rather than collapsed. None of it is implemented, and nothing in A1–G should be read as a partial implementation of it.

**One documentation gap, the third in a row and now a pattern rather than a typo.** The brief cited `docs/PORTFOLIO_REPORTING_DESIGN_V6.md` **§15**; that document has never had sections past §9 (Phase E cited §12/§13, Phase F cited §10). The specification actually in force is its §7 phase-map row plus the brief. The phase map is updated (G shipped, A1–G complete, H designed-for-not-built) and now says so explicitly.

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

### 8.4b · Issue #7 — platform staff had no `users` row at all (superadminmenu sprint)

**Reported symptom**: a confirmed super_admin saw many sidebar pages missing, and could not create or manage users via `/admin/users`. A real session on `admin.hollisworks.com` was working, yet a live query found **zero** rows in `users` for that email — before and during the session.

The menu-gating gap was real (§below), but it was **not** the root cause. Two independent layers were, and the first is the seventh member of the same silent-fallback family as issues 1–6 above.

**Layer 1 — the API was never told the second Auth0 tenant exists.** `main.Settings.hollisworks_auth0_domain` defaults to `""`, and `hollisworks_enabled` keys off it. `render.yaml` declared only `AUTH0_DOMAIN` / `AUTH0_AUDIENCE` on the API service — **`HOLLISWORKS_AUTH0_DOMAIN` was never declared there at all** (the earlier sprints set the *frontend* vars on Vercel). With it unset, `verify_token` skips the Hollisworks leg entirely, so every request from a valid `admin.hollisworks.com` session 401s. `ensure_user` — the only thing that creates a `users` row — is called **exclusively from inside route handlers**, which never run when the auth middleware rejects the token. Hence no row, ever. Worse, the surfaced error was *2nd Act's* `"Unable to find a matching signing key"`: the wrong tenant, and no mention of any env var, so the failure was undiagnosable from the logs. **This was not a regression from the redirect-loop fix** — that fix was frontend-only (`lib/authServer.js`) and correctly obtains a Hollisworks token; the token then dies at the API boundary.

*Fixed*: `HOLLISWORKS_AUTH0_DOMAIN` + `HOLLISWORKS_AUTH0_AUDIENCE` now declared on the API service in `render.yaml`, and `verify_token` now **fails loud** — when a token's issuer is not 2nd Act's and the second tenant is unconfigured, the error names the missing variable instead of blaming 2nd Act's key set.

> **Action still required outside the repo**: set `HOLLISWORKS_AUTH0_DOMAIN` (and optionally `HOLLISWORKS_AUTH0_AUDIENCE`) in the **Render API service** environment. `render.yaml` declares them `sync:false`, so declaring them does not populate them. Until that is done the API still rejects every Hollisworks token — the code change only makes the reason obvious.

**Layer 2 — the email column could never hold a real address.** `ensure_user` read `claims.get("email")` off the **access token**. An Auth0 access token minted for a custom API audience carries only `sub`/`iss`/`aud`/`azp`/`scope`; `email` and `name` live in the **ID token**, which the API never sees. So the claim was always absent and every row a real login created fell back to `{sub}@placeholder.local`. The live database proves it: the one row ever created by a real Auth0 login is `auth0|6a3af4c9a1c6aeb8baddf3eb@placeholder.local`. **A query by real email was therefore guaranteed to return zero rows even on the tenant where provisioning worked** — the reported "no user row" was partly a lookup artifact.

*Fixed*: `services/users.py` now resolves the real profile from the issuing tenant's `/userinfo` endpoint (trustworthy — it returns the verified profile for exactly the sub the presented token was issued to, needs no Auth0 dashboard change since `openid profile email` is already requested, and cannot be spoofed the way a client-supplied header could). The outbound host is derived from the **validated** `iss` claim and only after checking it against the issuers this API accepts. Best-effort by contract: any failure keeps the placeholder, and an existing placeholder row is back-filled on a later request. Bounded to one attempt per sub per process so an Auth0 outage cannot make every request retry.

**Deliberately NOT changed — `org_id` for Hollisworks staff.** `get_org_id` puts a Hollisworks session into 2nd Act's default org, so platform staff rows land in `00000000-…-0001` rather than the Hollisworks org (`bb347258-…`). That is the *established* convention and this sprint kept it: changing it would break `/admin/users` (which filters `u.org_id = $1`, and the Hollisworks org has zero users) and would revoke the current operator's own access. It is the cross-org concern tracked in §10, not a defect introduced here.

### 8.4c · Super Admin menu gating — the last two holdouts

> **Retrospective correction**: the drift documented here was real and worth fixing, but it was **not** what made `jlarizza@gmail.com` see limited access. That was `ensure_user` failing outright — see **§8.4e**.

`is_super_admin` is checked **first** in every enforcement layer (RLS, `restricted_access`, `staff_visibility`, `trading_authority`, Workflow Manager, and `services/rbac.has_permission` since commit 470eb26). Two **frontend** menus were the remaining exceptions, each with its own independently-copied gate logic and neither with a bypass: `lib/usePermissions.can()` (the sidebar) and `app/admin/page.js visibleSections()` (the `/admin` index).

**The "no roles yet → default-allow" posture did not cover it.** That shield only holds while `user_roles` is empty, and it is not: `jlarizza@culmina.io` (`users.role = 'super_admin'`) holds a granted `admin` role, so that account takes the strict per-permission branch. Its menu survives today only because `admin` happens to include `manage_members` — granting it any role that does not (e.g. `member`) would silently remove **Admin**, **User Management** and **Staff Visibility** from the sidebar while the backend continued to authorize all three pages.

The two copies had also **drifted**: **Note Terms Review** and **Volatility Surface** were in the sidebar's super-admin block but missing from the `/admin` index, so that page showed a strictly smaller menu than the sidebar it claims to mirror.

*Fixed*: both menus, plus the sidebar's role gates, now read one pure, dependency-free module — `apps/web/lib/menuVisibility.mjs` — which checks Super Admin first, then the default-allow posture, then the granted permission set. Same discipline as `lib/authHostConfig.mjs`: because the module imports nothing, the Node harness `apps/api/scripts/menuvisibility_harness.mjs` exercises the *shipped* rule rather than a re-implementation. A role gate that forgets to name `super_admin` now still admits platform staff. Non-super-admin menus are proven byte-identical to the pre-fix rule across four fixtures (plain member, member holding `manage_members`, org_admin, and the pre-RBAC no-roles case).

### 8.4d · User creation via `/admin/users` — the backend was never wired to a UI

The invite backend from §8.3 works and is correctly org-scoped (`org_id` from `get_org_id`, never from the body — `InviteCreateRequest` structurally has no such field). **Nothing in `apps/web` ever called it**: no Next.js route, no server action, no button. `/admin/users` had only search, filter and edit-role. That — not a permission problem — is why creating a user failed.

*Fixed*: `lib/inviteActions.js` (server actions) + `createInvite`/`getInvites`/`revokeInvite` in `lib/api.js` + an **Invite Member** button and modal in `UserManagement.jsx`, which surfaces the enrollment URL for manual sharing. `GET /admin/users` now also selects `invite_status` and `users.role`, so a pending invite is visible on the screen that created it and can be revoked there — the list previously hardcoded every row as "Active".

**Invite email — honest status: still NOT built.** §8.3 records that SES credentials "have since been configured, so those tasks are ready to complete"; the *tasks themselves were never done*. There is no SES, SMTP, SendGrid, Postmark or Resend client anywhere in `apps/api`, and `routers/invites.py` still carries the literal `# --- Task 3 hook (BLOCKED — SES gate failed)` marker. **User creation today inserts a pending row and returns an enrollment URL with no notification path at all.** There is also **no `/enroll` page**, so an invited member cannot yet redeem the link even if it were delivered by hand. Both remain unbuilt work.

*Verifier*: `apps/api/scripts/verify_superadminmenu.py`. Note one honest limitation it reports as BLOCKED rather than PASS: no Hollisworks Auth0 client credentials exist in the sprint environment, so a genuinely tenant-signed token cannot be minted and the JWT **signature** leg is unproven end-to-end. Everything else — the row writes, the `/userinfo` back-fill, the invite creation — runs against the live database, and the menu assertions run against the shipped module.

### 8.4e · Issue #8 — `uuid_generate_v4()` was unqualified: **new-user provisioning was broken for every brand-new identity** (ensureuseruuidfix sprint)

**This is the actual root cause behind the "jlarizza@gmail.com sees limited access" investigation.** It was never a menu-gating problem. §8.4c fixed a real and separate drift in the two frontend menus, but the account's degraded experience came from here: `ensure_user` was failing outright, so no `users` row was ever created and the caller ran under an id that matches no row.

**The live evidence** — a real Render production log, not a hypothesis:

```
ERROR in ensure_user (sub='auth0|6a7c8b473069946d5a6d5400'):
function uuid_generate_v4() does not exist
HINT: No function matches the given name and argument types.
```

**Why it was silent.** `ensure_user` never re-raises — by contract it swallows every exception and returns `get_user_id(request)`, a **uuid5** derived from the token `sub`. So the symptom was not a 500. Every affected caller got a plausible-looking UUID that matches **no** `users` row, and every FK-bearing feature behind it degraded quietly. Eighth member of the same silent-fallback family as issues 1–7.

**Blast radius: every brand-new identity, on every tenant.** Not Hollisworks-specific and not admin-specific — any first-time login on any host hit the same INSERT. Existing users were unaffected (they resolve by `auth0_sub` and never reach the insert), which is exactly why it went unnoticed.

**The genuine open question, and the confirmed answer.** 107 `id` columns — including `users.id` itself and every `portfolio.*` table — also default to `uuid_generate_v4()`, and those tables had been inserting successfully all evening. The difference is **where the name is resolved**:

- `uuid_generate_v4` exists **only** in the `extensions` schema (confirmed: one row in `pg_proc`). The application role's `search_path` is `"$user", public` — the same reason every `portfolio.*` reference needs schema-qualification.
- A **column DEFAULT** is parsed and name-resolved **once, at DDL time**, and stored as a parse tree holding the function's **OID**: `{FUNCEXPR :funcid 16548 ...}`. No schema name is stored, so the default never consults `search_path` at runtime. (`pg_get_expr` *deparses* that OID, so it prints `uuid_generate_v4()` to a reader who can see `extensions` and `extensions.uuid_generate_v4()` to one who cannot — the schema name is the reader's rendering, never the stored value.)
- **Literal SQL text** in a statement is re-resolved against the **session's** `search_path` on every parse. `ensure_user` named `id` in its column list and supplied `uuid_generate_v4()` explicitly in `VALUES` — it did *not* omit `id` and fall back to the default. That one difference is the whole bug.

**Why dev never caught it.** Local `DATABASE_URL` authenticates as `postgres`, whose `rolconfig` is `search_path="$user", public, extensions`. Under that path the bare call resolves fine and every existing verify script passed. The verifier for this fix therefore pins `search_path = public` on every probe connection — without that pin the assertions would pass vacuously, fix or no fix.

**Not isolated to `ensure_user`.** An AST scan of every SQL string literal in `apps/api` (docstrings excluded, so prose describing the function is not miscounted) found four live call sites, all now `extensions.uuid_generate_v4()`:

| Call site | Consequence before the fix |
|---|---|
| `services/users.py:214` — `ensure_user` | the production break; no `users` row for any new identity |
| `services/invites.py:71` — `create_invite` | same latent break; admin invite creation would have failed the same way |
| `scripts/verify_sprint19.py:211` | verify-script INSERT |
| `scripts/verify_superadminmenu.py:362` | verify-script INSERT |

Table-level `DEFAULT` clauses in migrations were deliberately **left alone** — they are already OID-resolved and are not the failure mode. No DDL was run; all 107 defaulting columns are unchanged.

*Verifier*: `apps/api/scripts/verify_ensureuseruuidfix.py` — **28/28**, run twice, real database, no mocks. It proves a real `ensure_user` call for a genuinely never-seen `auth0_sub` mints a real v4 id and that the row is findable afterward on an **independent** connection; it asserts the returned id is **not** the swallowed-error uuid5 fallback (asserting only "it returned a UUID" would have passed against the broken code); it replays the **pre-fix** form of each of the four statements under the same pinned `search_path` and requires each to still raise, so no assertion can pass vacuously; and it confirms the `users` and `portfolio.assets` DEFAULT-driven inserts still work untouched.

*Incidental hardening found while writing the verifier*: `DATABASE_URL` goes through PgBouncer, so a **session-level** `SET search_path` leaks onto the pooled *server* connection and outlives the client that issued it — an early draft polluted later runs and produced two contradictory readings of the same stored default. Every probe now uses transaction-scoped `SET LOCAL`, and each connection issues `RESET search_path` on open.

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

**New-user provisioning broken by an unqualified `uuid_generate_v4()`** — a real, live production failure, found in Render logs and fixed. `ensure_user`'s INSERT supplied `id` with a bare `uuid_generate_v4()`, which resolves against the session `search_path` (`"$user", public`) while the function lives only in `extensions` — so **every brand-new identity on every tenant** failed to get a `users` row, silently, because `ensure_user` swallows the error and returns a uuid5 fallback. The 107 tables whose `id` DEFAULTs to the same function were never affected: a DEFAULT is OID-resolved at DDL time and never consults `search_path`. This, not menu gating, was the root cause of the "limited access" report. Four call sites fixed; verifier 28/28. Full detail: **§8.4e**.

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
| R2 bucket name (`2ndactcapital-docs` → `hollisworks-docs`) | **Migration attempted 2026-08-14 — BLOCKED, not done.** Two independent honest gates tripped: (1) **No R2 credentials or copy tooling in the sprint environment** — `R2_ACCOUNT_ID/ACCESS_KEY_ID/SECRET_ACCESS_KEY/BUCKET_NAME` live only in Render (`render.yaml`, `sync:false`); absent from `apps/api/.env`, shell, and `~/.bashrc`. No `rclone`/`aws`/`cloudflared`; `boto3` only in the venv. Cannot create the new bucket or copy objects. (2) **Bucket name is embedded in row data** — `deal_documents.r2_bucket` (1 row = `'2ndactcapital-docs'`); this makes it a data migration, not just an object copy, which is out of this sprint's scope. **Findings worth keeping:** stored keys are bucket-**relative** (`chancery/{org}/…`, `deals/{id}/…`, `spvs/{id}/…`) except the `deal_documents.r2_bucket` column; **versioning is application-level** (distinct keys `…/v{n}/{document_id}`, tracked in Postgres — *not* R2 native), so a byte-for-byte key copy preserves all versions with no history loss; retrieval is **presigned-only** (no public `r2.dev`/custom-domain URL); **frontend has zero R2 references** (no Vercel var needed). Bucket name is read via `R2_BUCKET_NAME` (default fallback `'2ndactcapital-docs'` in `services/storage.py`, `routers/entity_documents.py`, `routers/marketplace.py`, `routers/spv.py`). **To unblock:** run with real R2 creds available; the migration must then also rewrite `deal_documents.r2_bucket`. **Old bucket retained** — deletion is a separate follow-up sprint after a soak period. Verifier: `apps/api/scripts/verify_r2rename.py` (gates cleanly to BLOCKED when creds absent). **UPDATE 2026-08-22 (EDGAR sprint discovery):** blocker (1) is gone — all four `R2_*` vars are now present in `apps/api/.env`, and `hollisworks-docs` **exists and is writable** (proven by a real round-trip). Local `R2_BUCKET_NAME` already reads `hollisworks-docs`, so anything running locally now writes to the new bucket while the old objects still sit in `2ndactcapital-docs`; Render's value was not inspected. Blocker (2) is unchanged — `deal_documents.r2_bucket` still holds `'2ndactcapital-docs'` (1 row). **The object copy and that data rewrite remain undone.** The rename is now genuinely runnable and should be its own sprint. |
| Cross-org browsing / org-picker UI — **separate, tracked, NOT built** | Reconfirmed during the superadminmenu sprint and deliberately kept out of its scope. A Super Admin administers *any* tenant in principle, but there is **no org-picker and no cross-org browsing UI**, and `get_org_id` resolves a single org per request from the token (falling back to `DEFAULT_ORG_ID`, which is what every row in `users` currently has). Consequence: platform staff rows sit in 2nd Act's org rather than the Hollisworks org (`bb347258-…`), and `/admin/users` — which filters `u.org_id = $1` — can only ever show one org. This is genuine future work with a real design decision behind it (whether org context comes from an explicit picker, the host, or a token claim); it is **not** a bug to patch in passing. See §8.4b for why the superadminmenu sprint left `org_id` resolution untouched. |
| Invite email + `/enroll` page — **still not built** | §8.4d. The invite row, token, expiry and revocation are done and now reachable from `/admin/users`, but there is **no email-sending code of any kind** in `apps/api` (the `# BLOCKED — SES gate failed` hook is still a comment) and **no `/enroll` page** to redeem the link. An admin must copy the enrollment URL out of the modal and deliver it by hand, and the invitee currently has nowhere to take it. Note that §8.3's "ready to complete" refers to the SES *credentials*, not the tasks. |
| `HOLLISWORKS_AUTH0_DOMAIN` on the Render API service | §8.4b. Now declared in `render.yaml` (`sync:false`) and the code fails loud without it, but **the value itself must still be set in Render's environment**. Until then the API rejects every `admin.hollisworks.com` token and platform staff get no `users` row. This is a dashboard action, not a code change. |
| No 2nd-Act-tier competitor research | Only Quorum's ($100M–$1B UHNW tier) research exists — a different tier from 2nd Act's post-liquidity-founder audience. |
| Note-terms extraction accuracy — `protection_type` (§7e) | **44% of extracted rows had a hazard disagreement and 16 of 22 were `protection_type`** (buffer vs floor — opposite payoffs, identical marketing language, invisible to every arithmetic validator). The ensemble catches them; the extraction prompt does not yet get them right. **Do not scale past the 50-filing bounded run** until the prompt is sharpened and re-measured. `cik_matches_filer` also failed on 6/50 (guarantor or index sponsor read as the issuer). |
| `securities_global_note_terms` has no home for extraction metadata (§7e) | Its only jsonb column, `field_status`, is enum-constrained to the four states, so the hazard-ensemble disagreement record had to go to `document_field_corrections` instead. Same column shortage means **one** `source_char_start/end` pair per row, so per-field UI highlighting is impossible. Fix: an `extraction_notes jsonb` column and per-field offsets on the terms row. Deliberately not added — §7e was forbidden to alter that schema. |
| Platform AI calls are billed to 2nd Act (§7e) | `ai_decision_log.org_id` is `NOT NULL`, so every org-less platform call — all of note-terms extraction, which writes to global tables with no tenant — is attributed to `DEFAULT_ORG_ID` (`00000000-…-0001`) by `services/extraction.py:51,189`. Cost and decision history for global reference work pollute a real tenant's ledger. Fix is a nullable `org_id` or a reserved platform org; it is a decision, not a bug fix. |
| No `--2a-error` / `--2a-success` theme tokens | The Design Tokens list names Error `#9B2335` and Success `#2D6A4F`, but the tenant theme layer publishes no custom property for either, so no component can read them at runtime. Every admin screen hardcodes the hex (`DocumentReviewManager`, `DocumentSearch`, `TopBar`, and now `NoteTermsQueueManager`, which at least names them once as module constants instead of inlining them). A white-label tenant therefore cannot restyle error or success ink. Small, real, and a one-line fix in the theme route + `globals.css` whenever someone is in there. |
| Underlying-resolution queue has no UI (§7g) | The three `/admin/pricing/underlying-queue` endpoints are built and proven (53/53), but the sprint's task list contained no frontend task, so nothing renders them. **69 confirmable index proposals and 28 manual-review edges are sitting in the queue with no screen to clear them from.** Natural shape: the same TanStack `DataGrid` + detail pattern as `NoteTermsQueueManager.jsx`, with a bulk-confirm for the 69 (they group to 13 distinct securities). Small and well-specified; blocks nothing else in the codebase, blocks everything for the human. |
| Underlying resolution is upstream of comparability (§7g) | Comparability scoring / percentile ranking — the sprint after §7g — needs edges at `link_state='resolved'`, and **0 of 97 are resolved today** because resolution is human-gated by design. That gate cannot be opened by code; it needs the UI above, then a person. Sequencing note, not a defect. |
| Leaked test fixture in `reference_filings` | One row (`cik=9999999999`, `filer_name='VERIFY FIXTURE'`, 110 chars, `extraction_status='extracted'`) from an earlier sprint's teardown miss. Harmless but it sits in the "real" corpus population; §7e excludes it by filter rather than deleting another sprint's row. |

**Operational gotcha worth remembering**: Vercel **preview** deployments don't inherit production environment variables — preview-branch errors about missing Auth0 config are expected and are *not* production issues.

---

## 11 · Remaining backlog — unbuilt

**Portfolio Reporting Phase H — designed for, not built.** The reconciliation engine, performance calculations, and cross-client analysis. Phases A1–G are all complete (see §7o); H is the only designed phase remaining, and none of A1–G should be read as a partial implementation of it. Also still open from those phases: the `allocation_lens` `subtree` double-count (Phase C), the `routers/ledger.py` `entry_date` bug and the two GL views missing `security_invoker` (Phase D), and Altruist ingestion (Phase B, BLOCKED on absent credentials). No router or UI exists for Phase G's UDFs — `services/portfolio_udf.py` is a service module only.

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
