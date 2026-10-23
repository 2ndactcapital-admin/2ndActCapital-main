# Replication Studio

Turns a fund into a separately managed account the member owns directly.
Phase 1: index equity ETFs and index mutual funds. This document describes
what exists after **rep1** (sprint 1 of 14): the security registry, fund
holdings ingestion, fund terms extraction, and the fix/edit terms flow. No
models, accounts or trades yet (rep2 custodian adapters + execution, rep3 model
builder, rep4 accounts/proposals, rep5 tax overlay, rep6 held-away accounts).

Spec: "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22).

## Naming and disclosure (Joe, 2026-10-22)

- Anything built from a fund is named **"Holdings of <ticker>"** — enforced
  by `replica_securities_display_name_chk`. A licensed index name is never our
  product's name; it is stored only as the disclosed term `index_name`
  (`disclosure_only`: it can be corrected, never changed).
- Disclosure line on every screen: *"Replicates the published holdings of
  <ticker>; not affiliated with or endorsed by the fund's sponsor or index
  provider."*

## Platform data vs org data

| Scope | Tables (`portfolio.` schema) |
|---|---|
| PLATFORM (`owner_scope 'platform'`, `org_id NULL`; read by all, written under `app.is_super_admin`; source rows immutable) | `replica_instrument_classes`, `replica_holdings_sources`, `replica_holdings_snapshots`, `replica_holdings_rows`, `replica_source_documents`, `replica_terms_field_registry`, `replica_term_readings`, `replica_refresh_events` |
| ORG (NULLIF org isolation on SELECT/INSERT/UPDATE/DELETE) | `replica_securities` (the registry), `replica_terms_versions`, `replica_terms_actions` |

Public source data — holdings files, prospectuses, readings — is fetched and
read ONCE and shared: a second org adding the same fund reuses the snapshot
and the extraction run (no model call). Nothing platform-scoped points at an
org row, so the Hollisworks platform org can later publish models as a
sub-adviser from the same rows.

DDL: `docs/rep1_part1.sql`, applied by `apps/api/scripts/_apply_rep1.py
--schema` (operator DSN) and proven by `--verify` (columns, constraints,
partial uniques, RLS, every policy, triggers, grants, seeds).

Bi-temporal (Rule 3): every table has `valid_*`/`system_*`. Immutable rows
take no UPDATE but a retraction (`replica_immutable_guard`); the registry
entry and terms versions are FK targets, so every edit first archives the
outgoing state on the SYSTEM axis (fresh id, `archived_from_id`, `system_to`)
and the live row keeps its id. Fixture teardown is the only delete path:
operator connection + `SET LOCAL replica.teardown = 'fixture'`.

## Services

| File | What |
|---|---|
| `services/replica_parsers.py` | Holdings parsers (code): `ssga_xlsx`, `invesco_csv`, `ishares_csv`, `sec_nport`. Weights → decimal fractions. |
| `services/replica_holdings.py` | ticker/CUSIP → fund (security master) → SEC ids → source → fetch → R2 → snapshot + rows; validation; diff; dry run. |
| `services/replica_terms.py` | Registry entries, prospectus pull, the no-Claude ensemble extraction, drafts, correction / deliberate change, maker-checker approve / reject, workflow + audit logging. |
| `services/replica_schedule.py` | The weekday holdings refresh (Task 5). |
| `routers/replication.py` | `/api/v1/replication/...` (10 routes, all in `docs/AUTHZ1_ROUTES.json`). |
| `services/securities_global.py` | Gained `get_by_identifiers_bulk` / `add_relationships_bulk` — the SAME rules as `get_by_identifier` / `add_relationship`, one statement each, so there is still exactly one resolver. |

### Ingestion rules

- **Resolution is never a guess.** CUSIP, then ISIN, then SEDOL; a ticker only
  when the row published no stronger identifier and exactly one surviving
  security carries it. A row whose CUSIP the master does not know stays
  unresolved even if its ticker matches something. Unresolved non-cash rows
  are queued as `constituent_of` edges, `link_state 'unresolved'`, on
  `securities_global_relationships` — the underlying-resolution review queue.
- **Same bytes = nothing new** (SHA-256 check + `replica_holdings_snapshots_file_uq`).
- **Validation** (recorded on the snapshot; any failure → `needs_review`, not
  live): non-cash weights sum to 1 ± 0.5%; every non-cash row has a weight;
  row count within ±20% of the previous live snapshot; no duplicate
  constituent; as_of not older than the previous live snapshot.
- **Diff** vs the previous live snapshot: added, removed, weight moves ≥
  `REPLICA_DIFF_WEIGHT_THRESHOLD` (default 0.0025), turnover = ½Σ|Δw|.
  `diff_exceeds_threshold` when anything was added/removed or turnover ≥
  `REPLICA_DIFF_TURNOVER_THRESHOLD` (default 0.02).
- Raw files gzipped to R2 under `replica/holdings/<TICKER>/<as_of>/<sha16>.<ext>.gz`;
  prospectuses under `replica/documents/<TICKER>/<accession>/<doc>.gz`.

### The correction / change split

- **Correct** — the read was wrong. Writes `document_field_corrections`
  (`target_type 'replica_terms'`, `target_id` = the draft, org-scoped), origin
  `corrected`, **no departure**.
- **Change** — a deliberate departure from the fund. Reason required; origin
  `deliberate`; appended to the version's `departures` and shown at the top of
  the Terms tab as "Departs from the fund: …".
- Both edit only the live draft. Aimed at an approved version → **409**. A
  change with no draft open starts the next version from the approved one.

### Maker-checker

Everyone who corrects or changes a draft field is in `maker_ids`. Approve /
reject needs `approve_replication` and is refused for a maker (403 naming the
rule) — and by `replica_terms_versions_maker_checker_chk` in the database.
Approving supersedes the previous approved version; one approved version is
live per entry (partial unique index); an approved row is never edited
(`replica_terms_version_guard`). The first approval makes the registry entry
`active` (only active entries refresh daily).

### Workflow events (Task 1e)

The workflow engine creates User Tasks only from a published BPMN version, so
every human action (add, extract, correct, change, approve, reject, refresh,
holdings review) is: one `replica_terms_actions` row + one `audit_log` row in
the same transaction, then a `domain_events` row (`replication.<action>`,
source_type `replica_terms_action`, source_id = the action id) via
`publish_event` — which also starts any workflow an org subscribes to that
event type. "Approval needed" is a `member_todos` task (source
`replica_terms_approval`) for every `approve_replication` holder, closed when
the draft is decided. Holdings reviews are `member_todos` with source
`replica_holdings_review` for `manage_replication` holders.

### Extraction — no Claude

Readers go through `services.note_extraction.proxy.chat` (one attempt per
named deployment, `disable_fallbacks`, provider-reported model recorded) with
the slots of the active `ai_ensemble_configs` row for task
`replica_terms_extraction` (falls back to `note_terms_extraction`). A slot
whose name, upstream or reported model is Claude/Anthropic is refused before
and after the call. Quotes are located with `quote_match.TextIndex`; a field
needs review when its quote is not in the document, its value is not in its
quote, the two readers disagree or only one answered, or a critical term was
found by neither. Data fields (ticker, SEC ids, latest holdings count) come
from data with origin `extracted`, source `data`.

**Live today: `ai_ensemble_configs` has no rows at all**, so extraction is
recorded `blocked` and every model field is "needs review" until a Super Admin
activates an ensemble for `replica_terms_extraction` (the task key was added
to the picker and to the public-data task list).

## Permissions

`view_replication`, `manage_replication`, `approve_replication` — granted to
every existing `org_admin` role by the migration; Super Admin passes first; no
other role gets them. **A new org's first org_admin** (created by
`rbac.ensure_org_admin_role`) does not get them automatically — re-run the
§14 grant of `docs/rep1_part1.sql` (idempotent).

| Route | Gate |
|---|---|
| `GET /replication/registry`, `GET /replication/registry/{id}` | view_replication |
| `GET /replication/lookup`, `POST /replication/registry`, `POST …/{id}/holdings/refresh`, `POST …/{id}/terms/extract`, `POST …/{id}/terms/corrections`, `POST …/{id}/terms/changes` | manage_replication |
| `POST …/{id}/terms/{version_id}/approve`, `…/reject` | approve_replication (+ not a maker) |

`POST /replication/registry` runs synchronously with caps
(`REPLICA_ADD_INGEST_SECONDS` 90, `REPLICA_ADD_EXTRACT_SECONDS` 150): there is
no job worker (only the 5-minute cron) and every step is idempotent, so a step
that hits its cap is re-run from the entry page.

## Screens

Portfolio → **Replication** (`/portfolio/replication`, gated
`view_replication`, strict): the registry list and Add (lookup → confirm →
run); the entry page `/portfolio/replication/<id>` with Terms, Holdings and
Sources tabs. Write controls come only from the envelope
(`apps/web/lib/replication.mjs`, fail closed).

## Daily refresh

`workflow_scheduler_tick.py` (Render cron) → after the weekday EDGAR index
refresh → `replica_schedule.maybe_refresh_holdings`: Monday–Friday, the first
tick at/after 11:00 UTC; once per day by `replica_refresh_events_day_uq`;
every active entry of every org, once per (fund, source); failures recorded
per fund on the 'finish' row; a changed (over threshold) or failed-validation
snapshot opens a review task in each org holding the fund.
`REPLICA_HOLDINGS_AUTO_REFRESH=0` turns it off.

## Holdings sources and adding an issuer parser

| key | parser | active | status (2026-10-22) |
|---|---|---|---|
| `ssga_spdr` | `ssga_xlsx` | yes | Confirmed live with SPY. Tickers: SPY, MDY, DIA. |
| `invesco` | `invesco_csv` | no | Download answered **406** to a non-browser client. |
| `ishares` | `ishares_csv` | no | `.ajax` CSV URL answered an HTML product page (bot/JS gate); needs a per-fund product id. |
| `sec_nport` | `sec_nport` | yes | The fallback for any fund with an SEC series. |

To add an issuer: (1) write `parse_<name>(data) -> ParsedHoldings` in
`services/replica_parsers.py` and register it in `PARSERS` (and in the
`replica_holdings_sources_parser_chk` CHECK); (2) confirm one live file and
add a fixture test; (3) insert a `replica_holdings_sources` row (url template
with `{ticker}` / `{ticker_lower}`, `match_tickers`, `priority`, terms-of-use
note) and set it active.

## CLI

    doppler run -- apps/api/venv/bin/python apps/api/scripts/replica_ingest.py --ticker SPY --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/replica_ingest.py --ticker SPY
    doppler run -- apps/api/venv/bin/python apps/api/scripts/replica_ingest.py --ticker QQQ --source sec_nport
    doppler run -- apps/api/venv/bin/python apps/api/scripts/replica_ingest.py --all-active [--dry-run]

`--dry-run` writes nothing at all (no security created, no snapshot, no
queued edge, no R2 object).

## Task 1 findings (rep1, 2026-10-22)

**1a — security master.** `services/securities_global.py`:
`get_by_identifier(conn, id_type, value)` resolves ticker/CUSIP/ISIN through
the merge-following `COALESCE(canonical_id, id)` join (CUSIP/ISIN/FIGI/LEI
unique-indexed; ticker and SEDOL are not). Creation is
`create_security(..., security_type='fund'|'equity')` + `add_identifier`,
Super-Admin only (RLS + app gate) — so ingestion creates a missing FUND as a
platform job. Unresolved matches are queued with `add_relationship`
(`link_state 'unresolved'`), then `underlying_resolution.propose_resolution` /
`confirm_resolution` (human-gated; a DB trigger blocks any other path to
'resolved'). rep1 calls `get_by_identifier`, `create_security`,
`add_identifier`, and the two bulk variants added in the same module. A new
common stock is never created by ingestion — a person does that through the
resolution queue.

**1b — holdings sources** (one read-only request each; site terms of use were
NOT fetched or reviewed — counsel must confirm automated retrieval, storage,
and index-provider data rights before production use; never redistribute raw
files):
- State Street SPY: `https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx`
  → 200 xlsx; sheet `holdings`; preamble Fund Name / Ticker Symbol /
  "Holdings: As of 21-Oct-2026"; header row 5: Name, Ticker, Identifier
  (CUSIP), SEDOL, Weight (percent), Sector, Shares Held, Local Currency; 505
  rows including `US DOLLAR` cash (identifier 999USDZ92); ex-cash weights sum
  to 0.99928.
- Invesco QQQ: `https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0?audienceType=Investor&action=download&ticker=QQQ`
  → **406**, empty body, twice (also with `Accept: */*`).
- iShares IVV: `https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund`
  → 200 `text/csv` but a 2.3 MB HTML product page, twice.
- SEC `https://www.sec.gov/files/company_tickers_mf.json` → 200, fields
  `[cik, seriesId, classId, symbol]`, 28,592 rows: QQQ → 1067839 / S000101292 /
  C000271435, IVV → 1100663 / S000004310 / C000012040, VOO / VFIAX → 36405 /
  S000002839. **SPY is absent** (a unit investment trust: no series, no N-PORT
  — issuer file only).
- Latest N-PORT-P / prospectus per series: the series-filtered atom feed
  `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<series>&type=NPORT-P|497K|485BPOS&dateb=&owner=include&count=10&output=atom`
  through the existing EDGAR client (`edgar_fetch._get`: declared User-Agent,
  rate limit) — confirmed for IVV, latest NPORT-P filed 2026-08-25; the XML is
  `<folder>/primary_doc.xml`. **Lag:** only each fiscal quarter's third month
  is public, filed up to 60 days after month end — holdings are 2–5 months old.

**1c — extraction path.** `note_term_readings` are produced by
`services/note_extraction` (cascade/readers/runner: `proxy.chat` → schema
validation → `quote_match.TextIndex` quote location → `compare`), slots from
`ai_ensemble_configs` (`cascade.active_ensemble`). The prompt and few-shot
are note-specific, so rep1 reuses proxy, schema (`build_field_specs`,
`json_schema`, `parse_reader_output`, `normalize`, `value_in_quote`) and
TextIndex with its own fund prompt and registry. **No ensemble row exists**
for any task today, so no model — Claude or other — is configured on that
path; rep1 refuses a Claude slot outright.

**1d — corrections.** `document_field_corrections` is written for
`document` (document review), `note_terms` / `template_proposal`
(platform, org NULL), `FEE_SCHEDULE_SPEC`. Its readers
(`correction_retrieval` by `field_name 'doc_category'` or a template join,
`note_terms_corrections`, `fee_spec_corrections`, `document_review`) filter
so none sees `replica_terms`; both CHECKs were widened to a superset.

**1e — workflow.** No service opens an ad-hoc task for a human action (User
Tasks come only from a published BPMN run). Chosen: action rows + audit_log +
`domain_events` (workflow events, fan out to subscribed triggers) and
`member_todos` for approval-needed / holdings-review (see above).

**1f — scheduler.** bulkfetchui3's weekday index refresh runs in the
`workflow_scheduler_tick.py` entrypoint after `run_scheduler_tick`
(`edgar_index_schedule.maybe_refresh_index`, once per day by a partial unique
index); the holdings refresh runs right after it.

## Operator steps

1. Activate an extraction ensemble for `replica_terms_extraction` (Admin →
   AI ensembles; two different non-Claude deployments) — until then terms are
   "needs review" with extraction `blocked`.
2. Have counsel review the issuer sites' terms and index-provider data rights.
3. Confirm Invesco / iShares downloads from Render's network before activating
   those sources (`REPLICA_HTTP_USER_AGENT` is settable).
4. The daily refresh runs inside the Render cron service that runs
   `workflow_scheduler_tick.py` — the same one bulkfetchui3's weekday index
   refresh needs. If that cron service is not yet deployed (bulkfetchui3 left it
   as an operator step), neither refresh runs. No new service. Set
   `REPLICA_HOLDINGS_AUTO_REFRESH=0` to pause the daily refresh.
5. `R2_BUCKET_NAME` must name the real bucket (see the EDGAR pipeline A note:
   Doppler's value was invalid).
6. Run `verify_rep1.py` (written, not run by the sprint).

---

# rep2 — custodians, trade adapters, human tasks, execution instructions (2026-10-22)

Sprint 2 of 15. No models, proposals or tax logic yet. **The custody cliff
holds:** nothing here places a trade or moves money. Altruist has no write
access (no live credentials; integration sprints 1–6 ran against mocks), so
every trade in this build ends as an execution instruction a person carries
out. Schema `docs/rep2_part1.sql`, APPLIED (`scripts/_apply_rep2.py --schema`)
and read back (`--verify`: SCHEMA OK).

## Task 1 findings (rep2)

- **1a. Custody registry.** `services/custody/registry.py:resolve_profile(conn, org_id, custodian_code)`
  reads `org_settings['custody.profiles']` over `DEFAULT_PROFILES` (GENERIC_CSV)
  and maps the profile's `adapter` key to a class registered by import side
  effect (`custody/__init__.py`). The only ingestion adapter is `csv`. rep2 adds
  a SEPARATE map appended to the same module (`register_trade_adapter`,
  `trade_adapter_for`, `trade_custodian_key`), keyed by the lower-case custodian
  route. `csv_adapter.py`, `importer.py` and `base.py` are byte-identical to main.
  Custodian codes in `public.accounts`: **none**. The table holds no rows today,
  and neither do `portfolio.tax_lots` or `portfolio.positions`.
- **1b. Workflow engine.** A system definition can be seeded per org and
  started for any subject with `start_workflow_run`. The subject rides in
  `workflow_runs.context`. Maker ≠ checker is `complete_user_task`'s
  `MakerCheckerError` (proposed_by == completed_by), backed by the
  `workflow_run_steps_maker_checker` CHECK. Todos attach through
  `workflow_todos.sync_user_task_todos`, by assigned PROFILE only.
  **Engine change (additive):** `workflow_steps.assigned_permission` (nullable,
  no default). When set, the engine notifies the permission's admitted holders
  except the maker, and refuses a completer without it. `cancel_user_task` is
  new. **Effect on existing runs: none.** Every pre-existing step is NULL and
  takes the old path. Live at the time: 3 definitions, 1 run (completed), 0 run steps.
- **1c. Tax lots.** `portfolio.tax_lots` has no account or security column.
  A lot reaches its account through `position_id → positions.account_id`, and
  its security through `positions.asset_id → assets.global_security_id`.
  Lot-level instructions validate along that path.
- **1d. Altruist.** Evidence: the OAuth scope is `accounts:read`
  (`altruist_oauth.build_authorize_url`). The mocked account payload has id and
  name only. Positions carry a position-level `cost_basis`. No order or trade
  endpoint exists in any Altruist module. The fee37 rate card (margin spreads)
  and fee38 (Altruist One TLH) are UNVERIFIED (fee37 F6) and describe the
  product, not an account. Unknown: every account capability. All 13 are seeded
  `unknown`, with that evidence cited.

## Custodian capabilities (`public.custodian_capabilities`, platform)

There are 13 capabilities per custodian route. Each has a value (`yes`, `no`
or `unknown`; for `options_level`, 0–4 or `unknown`), an evidence kind
(document, url or assumed), the evidence itself, an as-of date, a version
number and who recorded it. Versioning is on the valid axis: an edit closes
the live version and inserts the next one, and a trigger refuses any other
change. Seeded routes: `altruist` (all unknown) and `manual` (the trade APIs
are `no`).

`services/custodian_capabilities.capability(conn, custodian, key)` returns
`(value, evidence, as_of)`. **`available()` is the one rule: `unknown` is NOT
available**, and neither is a missing record. A missing capability downgrades
a plan; it never blocks it.

API: `GET /custodians/capabilities` and `.../{code}/{key}/history` (staff or
`manage_org_settings`, via `services.authz.require_staff_or_permission`), and
`PUT .../{code}/{key}` (Super Admin only, audit-logged).
**Decided (Joe, 2026-10-23):** `org_admin` is not a `STAFF_ROLE_NAMES` role, so
the original staff-only read hid the matrix from org admins. The read gate is now
staff OR `manage_org_settings` (org admins read); write stays Super Admin only.

## Trade adapters (`services/custody/trading.py`)

`TradeAdapter.submit / status / cancel`, registered per custodian route next
to (not inside) the CSV interface. `TradeList` is typed with `extra='forbid'`.
It holds one account and its trades: security, side, quantity OR notional,
specific lots (sells only), price guidance (market / limit / range against a
reference price and its time) and wash-sale notes. Amounts must be decimal
strings; a JSON float is a 422.

- `ManualTradeAdapter` turns each trade into a DRAFT execution instruction.
- `AltruistTradeAdapter` reads Altruist's `equity_trading_api` capability. While
  it is not `yes` (it is `unknown`), every trade routes to manual, and the
  result says so. No trading HTTP code exists.
- `route_trade_list`: a held-away account always goes manual. A custodian with
  no registered adapter goes manual. Per-trade downgrade notes cover fractional
  quantities, notional orders and lots without a lot feed.

## Execution instructions

Tables: `execution_instructions` (system-axis archive, so the id is stable),
`execution_instruction_lots` (valid axis), `execution_fills` (append-only) and
`execution_instruction_events` (append-only). All four are org-scoped with a
NULLIF RLS policy on every operation.

```
draft ─submit→ pending_approval ─approve (human task, maker ≠ checker)→ approved
      ←reject (note)─┘
approved ─mark sent (channel)→ sent ─fill→ partially_filled ─fill→ filled
filled ─reconcile (custodian data + reference, or manual + note)→ reconciled ─close→ closed
cancelled (reason) / expired from any open state
```

- **Enforced twice.** The service enforces the graph first, with clear
  messages; a database trigger re-enforces it. Terms are frozen once submitted.
  Lots change only while the instruction is a draft. A fill is accepted only
  after `sent`, never beyond the instructed quantity, and only on this
  account's lots in this security (and on the instructed lots, when it names
  any).
- **Who counts as a maker.** Anyone who created, edited or submitted the
  instruction is in `maker_ids`, and none of them can approve it. The DB CHECK
  on `maker_ids` backs this, alongside the engine's own maker-checker check.
- **Execute-by.** `services.execution.expire_tick` runs in
  `workflow_scheduler_tick.py` and applies each instruction's if-not-filled
  policy:
  - `cancel` → expired
  - `carry` → working again today
  - `ask` → a todo for a person, once

  An instruction that is not yet working simply expires. This is the only
  automatic step, and it is logged with no actor.
- **Logging.** Every action writes an event row and an audit row, and then a
  `domain_events` row `execution.<action>`.
- **Letter of instruction.** `POST .../letter` builds a PDF on demand and never
  stores it; it is allowed only for member / other-adviser executors. The
  account number is MASKED. To include the full number the person must choose
  to AND type it. It is checked against the account's salted hash, used for
  that one PDF, and never stored or logged. The choice is logged
  (`execution.letter_full_account_number`).
- **Pending differences.** `pending_differences(conn, org, account)` (and
  `GET /execution/accounts/{id}/pending`) gives the signed remaining quantity or
  notional of each open instruction, for later sprints (rep5 proposals).

## Permissions and screens

`view_execution`, `manage_execution` and `approve_execution` are granted to
org_admin by migration. `rbac.ensure_org_admin_role` now gives a NEW org's
first org_admin these three AND rep1's three replication permissions (closing
rep1's [FIND]). Routes (17) are under `/api/v1/execution/`; every one is gated
and listed in AUTHZ1_ROUTES.

Screens:
- **Portfolio → Instructions** (strict `view_execution`): list + filters, New
  instruction, and a detail page with the timeline, fills, reconcile, letter
  and the approval panel. Controls render only from the server's flags and
  `actions` list.
- **Dashboard → My tasks** (`GET /human-tasks/mine`).
- **Billing & Custody → Custodian capabilities** (strict gate: `is_staff` or
  `manage_org_settings`; Edit for Super Admin only).

Human tasks: see `docs/HUMAN_TASKS.md`.

## rep2 operator steps

1. Run `verify_rep2.py` (written, not run by the sprint). It runs
   `npm run build` and the `verify_workflowmgr5.py` regression itself.
2. The execute-by step needs the same Render cron service as the rep1 holdings
   refresh.
