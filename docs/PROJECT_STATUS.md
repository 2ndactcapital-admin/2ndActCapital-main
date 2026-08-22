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

**Verification — `apps/api/scripts/verify_correctionspoly.py`: 19 PASS, 0 FAIL, 0 flagged.** Idempotent (run twice, identical result), teardown at start and end, zero leftover rows. Proven: both columns nullable; `target_type`/`target_id` `NOT NULL` with the FK absent and the reason commented; all three legacy INSERT shapes still succeed unmodified and land as `('document', document_id)`; both CHECK rejections asserted **by constraint name** (document row with NULL `document_id`, document row with NULL `org_id`, note_terms row carrying an `org_id`, unknown `target_type`); a real `note_terms` correction (`protection_type: floor → buffer` — the sprint's motivating misread) inserted against an actual `securities_global_note_terms.id` with `org_id NULL`; and under the **real non-bypass `app_service` role**, that global row is readable with no org context while an ORG_B document correction stays invisible to an ORG_A session that still sees its own three.

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
| R2 bucket name (`2ndactcapital-docs` → `hollisworks-docs`) | **Migration attempted 2026-08-14 — BLOCKED, not done.** Two independent honest gates tripped: (1) **No R2 credentials or copy tooling in the sprint environment** — `R2_ACCOUNT_ID/ACCESS_KEY_ID/SECRET_ACCESS_KEY/BUCKET_NAME` live only in Render (`render.yaml`, `sync:false`); absent from `apps/api/.env`, shell, and `~/.bashrc`. No `rclone`/`aws`/`cloudflared`; `boto3` only in the venv. Cannot create the new bucket or copy objects. (2) **Bucket name is embedded in row data** — `deal_documents.r2_bucket` (1 row = `'2ndactcapital-docs'`); this makes it a data migration, not just an object copy, which is out of this sprint's scope. **Findings worth keeping:** stored keys are bucket-**relative** (`chancery/{org}/…`, `deals/{id}/…`, `spvs/{id}/…`) except the `deal_documents.r2_bucket` column; **versioning is application-level** (distinct keys `…/v{n}/{document_id}`, tracked in Postgres — *not* R2 native), so a byte-for-byte key copy preserves all versions with no history loss; retrieval is **presigned-only** (no public `r2.dev`/custom-domain URL); **frontend has zero R2 references** (no Vercel var needed). Bucket name is read via `R2_BUCKET_NAME` (default fallback `'2ndactcapital-docs'` in `services/storage.py`, `routers/entity_documents.py`, `routers/marketplace.py`, `routers/spv.py`). **To unblock:** run with real R2 creds available; the migration must then also rewrite `deal_documents.r2_bucket`. **Old bucket retained** — deletion is a separate follow-up sprint after a soak period. Verifier: `apps/api/scripts/verify_r2rename.py` (gates cleanly to BLOCKED when creds absent). **UPDATE 2026-08-22 (EDGAR sprint discovery):** blocker (1) is gone — all four `R2_*` vars are now present in `apps/api/.env`, and `hollisworks-docs` **exists and is writable** (proven by a real round-trip). Local `R2_BUCKET_NAME` already reads `hollisworks-docs`, so anything running locally now writes to the new bucket while the old objects still sit in `2ndactcapital-docs`; Render's value was not inspected. Blocker (2) is unchanged — `deal_documents.r2_bucket` still holds `'2ndactcapital-docs'` (1 row). **The object copy and that data rewrite remain undone.** The rename is now genuinely runnable and should be its own sprint. |
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
