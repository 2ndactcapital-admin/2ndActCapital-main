# BUGFIX1 — Task 1 discovery (2026-10-21)

Live database (project `mmgwmcinimzuhargsazs`) and the code at `e816797`
(bugfix1-work base). Every finding names the file and the query involved.
IDs match the HollisMuse backlog.

## 1a SPV totals (SPV-01, DASH-03)

Three surfaces, three computations. `spvs` has no committed column at all.

| Surface | Code | Query | Defect |
|---|---|---|---|
| Cap table | `apps/web/components/spv/SPVSubscriptionsTab.jsx:91-94` → `GET /spvs/{id}/captable` (`apps/api/routers/spv.py` `get_captable`) | `SELECT … FROM spv_subscriptions s WHERE s.spv_id = $1 AND s.org_id = $2 AND s.valid_to IS NULL`, summed in Python — **no status filter** (cancelled counted) | browser `Math.min(100, round(total/target*100))` — 134% displayed as **100%** |
| SPV list card | `apps/web/app/spvs/page.js` `ProgressBar` → `GET /spvs` (`routers/spv.py` `list_spvs`, `SPV_SELECT`) | `SELECT id, …, target_raise … FROM spvs WHERE org_id = $1` — no subscriptions join; `SPVResponse` has no `total_committed` | the card read `spv.total_committed \|\| 0` → **$0 / 0%** |
| Dashboard "SPV activity" | `components/dashboard/DashboardBrief.jsx` `SPVActivityBlock` → `services/brief_blocks.py` `_spv_activity_handler` | `SELECT s.name, ssh.from_status, ssh.to_status FROM spv_status_history ssh JOIN spvs s … ORDER BY ssh.changed_at DESC LIMIT 5` + an org-wide `count(*) … subscription_status = 'soft'` | lists status-change EVENTS, so "My first SPV" (created → `forming`, then `forming → open`) appears **twice**; no money at all |

The live SPV 968ffaef "My first SPV": two live soft subscriptions
($1,200,000 + $140,000) vs `target_raise` $1,000,000 = 134%. No cap of 100
exists in the database or the API; it was the browser. Soft vs hard: every
insert is `'soft'` (`routers/spv.py` subscribe); nothing anywhere separated
soft from hard. The "2soft subscriptionspending" spacing: the JSX source has
the spaces but splits the sentence across three text nodes, which a text
extractor joins without them — now one string.

Also sums committed: `services/spv_rollup._SUBSCRIPTION_TOTALS` (ledger, deal
roll-up) and `services/assistant_actions/spv.py` — same "no status filter".
Not changed (ledger semantics); recorded in PROJECT_STATUS.

## 1b Deal lifecycle (MKT-01, DASH-01, MKT-03, R2-MKT-05, MKT-04, VOTE-01)

Two columns hold a deal's state, neither matching the config:

* `deals.deal_status` — enum `deal_status` (draft, submitted, under_review,
  active, closed, archived), written by `PUT /deals/{id}/status`
  (`routers/marketplace.py` `update_deal_status`, in-place UPDATE, no history).
* `deals.deal_stage` — free text, default `'sourced'`, written only by the
  generic edit `PUT /deals/{id}` and `PUT /deals/{id}/stage`.
* config category `deal_stages` (org 2nd Act only): `deal_stage_1..6` =
  Sourced, Submitted, Under Review, Active, Closed, Archived — the same six
  states as the enum, by `display_order`. Hollisworks has no rows.

Live: 9 deals; `deal_stage` held `sourced` ×6, `active` ×2, `under_review` ×1 —
never a config key, so `DealsTable.jsx` (`stageLabels[value] || value`) showed
the raw "sourced". Readers: badge `StatusBadge.jsx` ← `deal_status`; Stage
column ← `deal_stage`; Marketplace summary `GET /deals/stage-summary`
(`SELECT LOWER(COALESCE(deal_stage,'sourced')) … GROUP BY 1`) ← `deal_stage`;
dashboard snapshot `brief_blocks._pipeline_snapshot_handler`
(`SELECT deal_status, count(*) … WHERE valid_to IS NULL`, no `system_to`
filter) ← `deal_status`. Two vocabularies.

Transitions: Submit (`marketplaceActions.createDealAction` → status
`submitted`, failure swallowed; server requires `name` only); Archive
(`DealStagePipeline.jsx` one-click form → status `archived`, no confirm, no
exit from archived — `STATUS_TRANSITIONS` had no `archived` key);
"Advance" (`PUT /deals/{id}/stage`, never rendered: `'sourced'` is not a
config key). `PUT /deals/{id}` could set any status/stage, bypassing
transitions; its bi-temporal copy kept the UNIQUE `slug` (so it always failed —
zero deal history rows live) and dropped `deal_stage`. TEST-Submit Test
(bed53061) audit trail: `create` (draft) → `status_change` submitted →
`status_change` archived.

MKT-04: `MemberInvestmentTracker.jsx` printed `String(inv.user_id).slice(0, 8)`;
`GET /deals/{id}/member-investments` SQL had no users join. VOTE-01:
`POST /deals/{id}/vote` toggles off only by re-clicking the same thumb; no
withdraw route or control.

## 1c Attachment tables (R2-DOC-03)

| Table | Name / type columns | File | Text | Live rows |
|---|---|---|---|---|
| `documents` (vault) | `original_filename`, `doc_family`, `mime_type` | `storage_key` | `document_extractions.extracted_text` (0 rows) | 0 |
| `deal_documents` | `file_name`, `file_type`, `document_type` | `r2_key`, `r2_bucket` | `extracted_data` jsonb (NULL on the live row) | 1 |
| `entity_documents` | `title`, `file_name`, `doc_category`, `content_type` | `storage_key` | none | 1 (status deprecated) |
| `spv_documents` | `title`, `doc_type` | `storage_key` (`spvs/{spv}/{uuid}_{name}`) | none | 3 |

`GET /document-search` (`routers/document_links.py` →
`services/document_linkage.search_documents` / `_search_rows`) and semantic
search (`services/document_embedding.semantic_search`, `document_embeddings
JOIN documents`) read only `documents`. Visibility to reuse:
`document_embedding.visible_entity_ids` (+ `filter_restricted`) for
entities; deals by status (`routers/marketplace.MEMBER_VISIBLE_STATUSES`
plus `status='approved' AND visible_to_members` for a member's documents);
SPVs by status (`routers/spv.MEMBER_VISIBLE_STATUSES`).

## 1d CRM-02 and CRM-04

CRM-02 — ownership is written to TWO tables. The Overview
(`components/crm/EntityDetailTabs.jsx` → `OwnershipTree` ← `GET
/entities/{id}/ownership-graph`, `routers/entities.py` `ownership_graph`)
reads `entity_ownership` (`parent_id`/`child_id`): James Hargrove
(10000000-…-0003) owns 40% of Hargrove Capital LLC; Hargrove Family Trust
owns 60% of it. The Ownership tab (`components/crm/tabs/OwnershipTab.jsx`)
lists from `GET /entities/{id}/ownership` (`routers/entity_graph.py`), which
reads `entity_relationships` (`relationship_type='ownership'`) — zero rows for
any Hargrove entity — and its tree asks `/ownership-graph` for a `tree` key:
`routers/ownership_tree.py` defines the same path but `routers/entities.py` is
mounted first, so the tree endpoint is unreachable and the tab always said "No
ownership tree data".

CRM-04 — true duplicates, not a display bug: `entity_tax_ids` rows b485d0bb
(07:38:30) and 9cff06ec (07:42:36) on James Hargrove, both current, same
`tax_id_type` ssn, country US, identical `tax_id_encrypted` (md5 c3833702…).
`POST /entities/{id}/tax-ids` inserted without checking for a current
identical row; the table has no unique constraint.

## 1e EDGAR peek (run e3fde9c0)

`services/edgar_bulk.py` `Runner._fetch_and_store`, per filing: `GET
index.json` → peek `GET` with `Range: bytes=0-16383` (`peek`, `PEEK_BYTES`) →
full `GET` when needed → `GET -index-headers.html`, then
`edgar_pipeline.store_fetched` uploads the document and text to R2. Run
e3fde9c0 (Wells Fargo, 2025-01-01→2026-10-01, kinds `final`, peek on):
`peek_range_ignored` 1071 of 1071 — the SEC answered every Range request
with 200 and the whole document. On a 200 the old code took the body as the
download (`raw = p.body`) and **never called `peek_decision`**, so every
preliminary (548 by kind) was stored: `peek_skipped` 0, 3222 SEC requests
(3 per filing + 9 retries), 271.8 MB downloaded. Separately, nothing after a
full fetch compared the full-text kind with `--kinds`, so a preliminary whose
cover the 16 KB peek could not read was stored too. Stored preliminaries in R2
now (all runs): 910 filings, 1,820 objects, 34,468,879 bytes (dry run of
`scripts/cleanup_preliminary_r2.py`).

## 1f /fee-schedules

`apps/web/app/fee-schedules/` holds only `chat/page.js` (the fee schedule
assistant, Fee40); `/fee-schedules` itself had no page (404). No menu entry or
link points at it. It now redirects to `/fee-schedules/chat`. Dead menu
entries `/community`, `/insurance`, `/portfolio-reporting` had no page at all.
