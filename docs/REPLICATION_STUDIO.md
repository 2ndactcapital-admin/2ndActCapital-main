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


## rep3 — model management (2026-10-23)

### Task 1 findings (rep3)

- **1a human tasks** — `services/human_tasks.py`: `open_task(pool, *, org_id, kind,
  subject_type, subject_id, maker_id, eligible_permission, title, payload, action_path,
  actor_kind)`, `complete_task(pool, *, org_id, task_id, actor_id, outcome, note)`,
  `cancel_task(pool, *, org_id, task_id, actor_id, reason)`, `list_tasks` / `get_task`.
  Subject in `workflow_runs.context.human_task`; outcome in `workflow_run_steps.result`.
  CHOSEN: the kind's idempotent `on_complete` callback plus a `sync_*_task_outcome`
  recovery (the execution pattern) — the only way the subject reacts however the task
  is completed. Added (additive) `system_open` kinds for review tasks with no maker.
- **1b rep1 approvals** — `_open_approval_todos` (build_draft / correct_field /
  change_field) and `decide` (close_todos); `verify_rep1.py` asserted them at its
  "approval needed" (~l.992) and "approval tasks are closed" (~l.1026) checks. Moved
  onto human tasks (see docs/HUMAN_TASKS.md); only those two checks (plus teardown of
  the engine rows the tasks now create) changed in `verify_rep1.py`.
- **1c spreadsheets** — openpyxl 3.1.5 installed and pinned; xlrd NOT installed (no
  network during the sprint) → pinned `xlrd==2.0.1` (pure Python, cached values only).
  pandas present, not used.
- **1d fee schedules** — reference by `fee_schedules.id` of an APPROVED row (never
  mutated; an edit writes version N+1 with a new id); validated APPROVED + same org;
  org-isolation RLS (ALL) makes it readable from model code.
- **1e platform rows** — the rep2 `custodian_capabilities` shape (owner_scope
  'platform', org_id NULL, read by all, written under `app.is_super_admin`).

### One registry (`portfolio.models`)

Sources: upload, library_copy, library_subscription, fund_replica (rep4), composite.
"Holdings of …" is reserved for fund_replica (CHECK `models_reserved_name_chk` + the
service's 422). Optional default fee schedule (an APPROVED `fee_schedules.id`).
Versions (`model_versions`): draft → submitted → approved → superseded; reject (note)
→ draft; abandon (reason). One approved and one open version per model (partial
uniques); approved versions never change (trigger + 409). Positions
(`model_version_positions`, valid axis): resolved security + raw identifiers, target
weight (numeric fraction), asset class, sleeve label, drift band, alternates,
restrictions, resolution state (resolved | unresolved | cash). Unresolved rows can be
saved in a draft but block approval.

### Upload and the upload log

`services/model_upload.py` reads .xlsx (openpyxl read-only, `data_only=True`; a formula
with no cached value is a row error, never evaluated), .xls (xlrd), .csv (UTF-8 /
UTF-8-SIG, sniffed delimiter). Refused: .xlsm/.xlsb/.xltm, macro-enabled packages,
> 5 MB, > 5,000 rows, zip expansion > 50 MB or > 100×. Header row detection, sheet
choice, column mapping from a synonym vocabulary with a provider's saved mapping
(`model_column_mappings`, versioned) applied first. Weights percent or fraction
(detected, stated, converted; Decimal), sum 100% ± 0.05% (org setting
`portfolio.models.weight_tolerance_pct`), gap to cash only for a marked cash row or
"fill with cash". Identifiers: CUSIP/ISIN first, ticker/SEDOL only when neither is
given and unambiguous — never guessed. Tradability notes per custodian the org uses
(`capability()`, informational). Originals stored once per (org, SHA-256) under
`models/uploads/<org>/<sha256>`.

The log (`model_uploads`, append-only, never deleted) records every file —
received, duplicate (with `duplicate_of`, no second object) or failed — and
`model_upload_events` (append-only) records parsed / mapping_saved / draft_created /
rejected / abandoned / failed with row counts and row-numbered messages. Export:
`GET /models/uploads/export.csv` (every row; = + - @ tab CR prefixes neutralised).

### Library

`model_library_entries`: shelf platform (org_id NULL; Super Admin publishes a model of
the Hollisworks org; every org reads it through a SECURITY DEFINER carve-out on the
model's live approved version) or org (an org admin with manage_models; own org
only). Copy → an independent org model; subscribe → an org model
(`model_subscriptions`) that receives each new approved version as a draft with a
review task in its own org. `requires_agreement` → an org admin's logged
`agreement_confirmed` first.

### Composite models (sleeves)

`model_sleeves`: parent version → child model, weight, follow live | pinned, the
resolved child version. Weights sum to 100%; cycles refused (edit-time graph walk +
look-through guard). Look-through = sleeve weight × child weights, merged by
security. A child's new approved version drafts every composite that follows it
live, with a review task; pinned sleeves are untouched.

### Events for rep5

`model.version_approved` (source_type `model_version`, source_id = the version):
`{model_id, model_name, version_id, version_no, superseded_version_id, approved_by,
diff: {added, removed, changed, threshold, turnover}}`. Every human action is also a
`model_events` row + audit_log + `model.<action>` domain event.

### Permissions and screens

view_models / manage_models / approve_models — org_admin by default (migration +
`rbac.ensure_org_admin_role`). Portfolio → Models: list, upload wizard
(`/portfolio/models/upload`), model page, library (`/portfolio/models/library`),
upload log (`/portfolio/models/uploads`), templates (`/api/models/template`).

### rep3 operator steps

1. `pip install -r apps/api/requirements.txt` (xlrd).
2. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep3.py`.

## rep4 — build a model from a fund (2026-10-23)

Spec: "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22) — "What can
be replicated", "Workflow" steps 2, 5, 6, "Portfolio construction", "Fees".
Services: `services/replica_models.py` (construction, find-or-build, specs,
monitoring), `services/replica_viability.py` (the five questions, cost
comparison). Routes: `routers/replication_models.py`. Schema:
`docs/rep4_part1.sql` (applied by `scripts/_apply_rep4.py`). Screen: the **Model**
tab on a Replication entry (`components/replication/ModelTab.jsx`). Verify:
`apps/api/scripts/verify_rep4.py` (written, not run in the sprint).

### Task 1 findings (rep4)

- **1a signatures.** rep3's `models.create_model` REFUSES `fund_replica` ("created
  from the Replication registry (rep4)"), so rep4 adds `create_fund_model(conn, *,
  org_id, actor_id, name, replica_security_id, disclosure, description)` — the only
  path that creates one. Drafts go through rep3's `_create_version(conn, *, org_id,
  model, origin, maker_ids, created_by, positions, …)`; system drafts through
  `_submit_system(pool, *, org_id, model, version, why, extra_payload)` — the
  composite-follow / subscription path, which opens `model_version_review` with NO
  maker (`system_open`). rep1: `load_entry`, `live_approved`, `display_name_for`,
  `disclosure_for`; holdings: `latest_snapshot(conn, fund_id, source_key, *,
  live_only)`, `snapshot_rows`. Human tasks: `open_task(pool, *, …, actor_kind)`.
  Capabilities: `capability(conn, code, key)` + `available()` ('unknown' = not
  available; Altruist: all 13 'unknown'; manual: trading API 'no').
- **1b prices.** `portfolio.securities_global_prices` held **zero rows at all** on
  2026-10-23 (and no live snapshot existed). SSGA files carry weight + shares;
  N-PORT carries valUSD + balance — a value / balance quotient is a price as of the
  filing date and is NOT used (never estimate a price). Computable today: Q1, Q2,
  Q4, Q5's expense ratio and strategy fee, and trading cost once two live snapshots
  exist. **Q3 says "unknown — needs prices" for every real fund until a price feed
  exists** (FMP not contracted).
- **1c hook.** rep1's refresh signals a new snapshot only by its return value
  (`IngestResult.created / status / snapshot_id`); there is no event of its own and
  the member_todos review fires only over the diff threshold. Hook:
  `replica_schedule.monitor_models(pool, res)` after every ingest in BOTH the
  weekday refresh and the manual refresh route; terms: the approve branch of
  `replica_terms._apply_terms_decision` (with a prior version).
- **1d fee.** `fee_calc.apply_tiers(billable, tiers, method)` on the model's
  `default_fee_schedule_id` tiers (`fee_schedule_tiers`), method = `tier_method` or
  GRADUATED. Tiers only — a schedule minimum / maximum is period-scoped and is
  named in the inputs, not applied. No fee run, no invoice.
- **1e real data.** 0 replica entries, 0 models, 0 fee schedules on 2026-10-23.

### Schema

| table | axis | what |
|---|---|---|
| `portfolio.replica_model_specs` | immutable, one per model version | the recipe: entry, terms version, snapshot, custodian, method, top N, min weight, cash target, unresolved policy, rebalance (+ origin), drift and turnover-review thresholds, departures, dropped rows, origin `fund_build` / `fund_monitor` |
| `portfolio.replica_registry_keys` | valid axis (closed, never edited) | the "find or build" key: one live model per (org, entry, custodian, method, top N) — partial unique `replica_registry_keys_live_uq` |
| `portfolio.replica_viability_reports` | immutable | entry, custodian, account size, method, the five answers (CHECK: exactly five), cost lines, overall |

rep3 additions: `model_versions.origin` += `fund_build`, `fund_monitor`;
`model_events.action` += `fund_build_joined`, `holdings_monitor`, `terms_monitor`,
`terms_review_done` (the two monitor actions may have no actor); partial uniques
`model_events_holdings_monitor_uq` (model, snapshot) and
`model_events_terms_monitor_uq` (model, terms version) make monitoring idempotent
in the database; `models.disclosure` (fund models only). RLS: NULLIF org
isolation on every operation of every new table.

### Build (find or build)

Needs an APPROVED terms version and a LIVE snapshot — otherwise 409 naming which
is missing (`detail.missing`). A `needs_review` snapshot is never used.
Construction, exact Decimal (numeric(14,10), largest remainder so the positions
sum to exactly 1 − cash target):

1. equity rows only — the fund's cash rows are replaced by the cash target;
   futures / other are dropped (departure "stocks only" when they carry weight);
2. unresolved rows: `block` keeps them as unresolved positions (rep3 refuses to
   approve until they are resolved); `exclude_and_reweight` drops them (departure);
3. `top_n`: the N largest by fund weight (ties by row number), departure;
4. `min_weight`: rows under it (by FUND weight) dropped, weight spread pro rata
   (departure);
5. normalise to 1 − cash target; a cash position holds the rest (departure).

Name: rep1's "Holdings of <TICKER>"; top N → "Holdings of QQQ — Top 50"; a second
custodian with the same method appends the custodian's label (names are unique
per org). The model is `fund_replica` with `replica_security_id` and the rep1
disclosure; the version is a DRAFT with origin `fund_build` and the builder as
maker; approval is rep3's `model_version_approval` task (approve_models, maker ≠
checker). The registry key is (entry, custodian, method, top N): a second build on
an existing key returns the existing model (200, `joined`) and says which
requested settings differ — personal settings belong to the account (rep5).

### Viability — the five questions and their data needs

| # | question | yes / no | unknown when |
|---|---|---|---|
| 1 | rules-based and public? | `index_etf` / `index_mutual_fund` → yes; else no | never |
| 2 | rebuildable with stocks / listed options? | futures + other ≤ `max_non_equity_weight` (5%) of non-cash weight → yes; else no + "core only" note. As-of date + age shown; N-PORT > 45 days flagged | no live snapshot |
| 3 | account big enough at the tracking target? | minimum = max(max p·u / w, Σ p·u / T); u = 1 share, or the fractional increment (0.001) when the custodian's `fractional_shares` is available; T = `tracking_target` (0.5% summed weight error) | any constituent lacks a close ≤ 5 days old (unresolved rows count as unpriced) — "needs prices for N of M" |
| 4 | custodian allows every leg? | always yes for a stock-only model: via the trading API when available, else "yes, via execution instructions" (manual); 'unknown' evidence listed as missing | never |
| 5 | beats the fund after fees and tax? | fund net expense ratio × size vs strategy fee (apply_tiers) + trading cost (Σ turnover of the last 12 live diffs × 365 / days spanned × 2 sides × size × 5 bp); the tax line is "estimated by the tax engine (rep6)" — never a number | no approved net expense ratio, no model / no default fee schedule, fewer than two live snapshots |

Every default is an org setting under `replication.viability.*`
(`default_account_size` 250000, `max_non_equity_weight` 0.05, `nport_stale_days` 45,
`price_max_age_days` 5, `tracking_target` 0.005, `fractional_increment` 0.001,
`turnover_snapshots` 12, `trading_cost_bps` 5) and is reported in the answer's
inputs with its origin. Thresholds for built models: `replication.models.
drift_threshold` (0.01) and `replication.models.turnover_review_threshold` (0.02).
A report is stored immutable; computing again writes a new one.

### Monitoring

- **New live snapshot** → every fund_replica model of the fund, in every org (each
  in its own RLS context): the would-be version from the live version's spec;
  turnover vs the live version ≥ the spec's threshold, or any constituent added /
  removed → system DRAFT (origin `fund_monitor`, no maker, its own spec) + rep3
  review task carrying the diff; else `within_tolerance`. A model with no approved
  version, or with an open draft, is logged (`no_live_version`,
  `skipped_open_draft`) and not drafted.
- **New approved terms version** with a change in expense ratio (net / gross),
  weighting method, rebalance frequency or index name → one
  `fund_model_terms_review` task (outcome `done`, manage_models) per model of the
  fund, naming the changed fields; no automatic version.
- Every outcome is a model event (+ audit row) and a domain event
  (`model.holdings_monitor`, `model.terms_monitor`); the Model tab lists them.

### Permissions and screens

No new permissions. view_replication: Model tab, viability (compute + list +
read), find. manage_models AND view_replication: preview, build. view_models: a
fund model's specs (`/replication/models/{id}/spec`). The Model tab: sources line
(terms version, snapshot as-of and age, N-PORT warning, what is missing), the
viability checklist with custodian / account-size pickers and an "as of" stamp,
the cost table (unknown shown as unknown, the tax line without a number), the
registry offer FIRST ("An approved model already exists: … — Use this model"),
otherwise Build with a preview of positions vs the fund's holdings (dropped rows,
departures), the models built from the fund, and the monitoring log. Build /
Preview / Use render only on `permissions.can_build === true` with non-empty
`editable`.

### rep4 operator steps

1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep4.py` (it
   runs `node --test`, `npm run build` and the `verify_rep3.py` regression itself).
2. Q3 stays "unknown — needs prices" for real funds until a licensed price feed
   writes `portfolio.securities_global_prices`.

## rep5 — accounts join models; proposals; execution (2026-10-23)

Spec: "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22) — "Workflow"
steps 2 and 7, "Portfolio construction" (rebalance triggers; every trigger produces a
proposal, never a trade), "Fees" (the account override; no double charging while a
replicated fund is still held), "Custodians" (manual execution instructions).
Decisions (Joe): personal settings live on the ACCOUNT, not the model; a new account
joins an existing model rather than forking it; every human action is a human task;
nothing trades.

Services: `services/account_models.py` (link versions, settings, the join, fees, the
account's holdings), `services/rebalance_engine.py` (PURE: exclusions, cash floor,
lot selection, gain budget, trade sizing, wash-sale flag),
`services/rebalance_proposals.py` (the one generator, lifecycle, execution through
rep2, the tick). Routes: `routers/account_models.py`. Schema: `docs/rep5_part1.sql`
(applied by `scripts/_apply_rep5.py`). Screens: Portfolio → **Accounts**
(`/portfolio/accounts`), the account page (`/portfolio/accounts/{id}`), a proposal
(`/portfolio/proposals/{id}`). Verify: `apps/api/scripts/verify_rep5.py` (written,
not run in the sprint).

### Task 1 findings (rep5)

- **1a signatures.** `execution.create_instructions(pool, *, org_id, actor_id,
  account, trade_list, routed_via, routing_reason, upstream=None)` (upstream is new);
  `custody.trading.route_trade_list(pool, *, org_id, actor_id, account, trade_list,
  upstream=None)` (upstream rides on `TradeContext`); `execution.submit / decide /
  account_lots(conn, org_id, account_id, global_security_id=None) /
  _validate_lots(...)`; `human_tasks.open_task(pool, *, org_id, kind, subject_type,
  subject_id, maker_id, eligible_permission, title, payload, action_path,
  actor_kind)` / `complete_task` / `cancel_task` / `list_tasks`; a kind is
  maker-checker OR system_open, never both; `models.live_approved(conn, model_id)`,
  `positions_of(conn, version_id)` (alternates are TICKERS), `look_through(conn,
  version_id)`, `after_approval(pool, *, org_id, model_id, version_id)`;
  `replica_models.spec_for_version(conn, version_id)` (drift_threshold);
  `fee_schedules.create_assignment(conn, org_id, *, fee_schedule_id, scope_type,
  scope_id, effective_from, effective_to, agreement_document_id, created_by,
  replace_existing=True)`, `end_assignment`; `custodian_capabilities.capability(conn,
  custodian_code, key)` + `available()` ('unknown' = not available).
- **1b holdings.** One account: `portfolio.positions WHERE account_id` — the latest
  live row per asset (`as_of_date` desc) is "current"; the valuation as-of is the
  latest of those dates. Lots come from `execution.account_lots` (lot →
  `positions.account_id`, security via `assets.global_security_id`). Price =
  `market_value / quantity` on the position's own as_of_date (source
  `account_position`, as-of shown). The household is accounts with the same
  `household_id`, the same primary entity, or a shared `account_owners` entity.
  Real rows: 0 accounts, 0 account positions (2026-10-23).
- **1c event.** `domain_events.publish_event` fans out only to BPMN
  `workflow_triggers`; no in-process subscriber registry exists. Chosen: a direct
  call from rep3's `after_approval` (same request, deterministic, failure logged and
  never undoing the approval), plus the scheduler tick re-sweeping the last 7 days of
  'model.version_approved' events. Idempotent per (account, model version).
- **1d upstream approval.** `execution_instruction_lots_guard` admitted lots only
  while the instruction was a draft, and rep2's CHECKs require a human maker distinct
  from `approved_by`. The exact change:
  - `create_instructions(upstream=…)` re-reads the proposal: approved, made by a
    person, maker ≠ checker, its task completed 'approve' by that checker.
  - It then inserts each instruction 'approved' with maker = the proposal's maker
    and approved_by = its checker.
  - A new BEFORE INSERT trigger, `execution_instructions_insert_guard`, admits a live
    'approved' row only from such a proposal.
  - The lots guard also admits lots on that instruction, but only in its creating
    transaction.
  - Every rep2 CHECK is unchanged.
  - A SYSTEM proposal cannot satisfy this (no human maker). Its instructions are
    drafts by the reviewer, submitted to rep2's own approval.
- **1e fees.** An ACCOUNT assignment goes through `create_assignment`: precedence 10
  is derived, a RETIRED schedule is refused, and there is no approval of its own.
  `fee_exclusions` had NO write path. rep5 adds `create_exclusion` /
  `end_exclusion` / `list_exclusions` through `fee_validation.validate_exclusion`
  and the same scope check. A SECURITY basis is the org asset id. The join task —
  whose payload names both — is their approval.

### Schema

| Table | Axis | Notes |
| --- | --- | --- |
| `public.account_model_links` | both | a version per change; the decision is a system-axis archive; one active + one pending per account (partial uniques); settings never edited |
| `public.rebalance_proposals` | system | one open (draft / pending / approved) per account; content fixed at generation; status edges in a guard; `superseded_by_id` deferrable |
| `public.rebalance_proposal_trades` | valid | quantity xor notional (a sale is always a quantity); price source + as-of; lots; wash-sale flag; instruction link by close + insert |
| `public.account_transition_plans` | valid | keep / trim / add / sell now / sell later by year; embedded gain; years to complete |

RLS: org isolation with NULLIF on every operation of all four. Deletes only on the
fixture-teardown path (`execution.teardown = 'fixture'`, never app_service).

### Transition and proposals

- **Exclusions:** pro_rata (rep4's largest-remainder allocation — exact), to_cash,
  use_alternates (the first alternate not itself excluded; otherwise pro rata, with
  a note saying so). Then the cash target is at least the floor (plus any withdrawal).
- **Lots:** lowest tax per dollar raised — losses first, then highest cost; at equal
  cost long-term before short-term. The short-term guard defers a short-term gain
  lot that turns long-term within the wait window, to that date. The gain budget
  caps NET realized gain for the year: realized-this-year counts against it, and
  same-proposal losses add room. What does not fit is "sell later" by year, one
  budget per year. A lot with no cost is never sold against a budget.
- **Trades:** whole shares unless the custodian's fractional_shares is 'yes'. An
  unpriced buy is by notional. A holding with no price is never traded. Trades
  under `replication.proposals.min_trade_size` are dropped as dust. Every trade
  carries a reason. Buys are scaled to the cash the allowed sales raise.
- **Warnings:** cannot value, unpriced buy, stale valuation, capability unknown,
  held-away, advice only, dust, buys scaled, sell later, unknown cost basis, no cash
  balance, unresolved holding / target, no trades, wash-sale flag.
- **Triggers:**
  - join — after the link approval; the joiner is the maker; auto-submitted.
  - model_change — system.
  - drift — system; the daily tick on the account's own valuation; threshold:
    link → model spec → org setting → 1%.
  - cash_flow — system when cash exceeds its target by more than
    `cash_flow_threshold`; or a person entering a withdrawal.
  - manual — a person; a draft they submit.
- **Lifecycle:** a newer proposal supersedes an older draft or pending one (its task
  is withdrawn and the change is audited). While an approved proposal's instructions
  are working, a new one is refused. Proposals expire after `expiry_days`, or when
  the account's valuation date moves.
- **Execution:** approval routes through `route_trade_list`. Altruist
  'unknown' → manual; held-away → manual; advice-only and held-away → executor
  'member'. A person's proposal → instructions APPROVED (maker + checker + lots
  carried). A system proposal → drafts submitted to rep2's approval. The proposal
  becomes 'executed' once every instruction is closed, cancelled or expired, with
  the counts in its reason.

### Permissions and screens

`view_proposals`, `manage_proposals` (join, settings, generate, submit),
`approve_proposals` — org_admin by default (migration + `rbac.ensure_org_admin_role`).
17 routes, each gated and in `docs/AUTHZ1_ROUTES.json`. Every envelope carries
`permissions`, `vocabularies` (triggers, statuses, exclusion policies, warning codes,
harvesting engines; `editable` empty for a view-only caller) and the server's `actions`.
The screens render a control only for `=== true` on the flag AND an action the
server listed (`lib/proposals.mjs`; `tests/replication/rep5.test.mjs`).

### rep5 operator steps

1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep5.py` (it
   runs `node --test`, `npm run build` and the `verify_rep2.py` regression itself).
2. Redeploy the scheduler cron service so `workflow_scheduler_tick.py` runs the new
   `proposals_tick` step.
3. Optional org settings: `replication.proposals.min_trade_size`, `.expiry_days`,
   `.stale_valuation_days`, `.cash_flow_threshold`, `.execute_within_days`.

## rep6 — the tax engine (2026-10-23)

Spec: "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22), "Tax overlay"
(phase-1 rows): lot-level accounting, loss harvesting, household wash-sale control
(spouse and IRAs included — an IRA buy permanently forfeits the loss, Rev. Rul.
2008-5), the substitution table as a versioned approved compliance artifact, the loss
bank and tax-alpha ledger. Guardrails: tax inputs come from the adviser with a date
and are never guessed; every tax figure is labelled an estimate; benefit decay shown
honestly. Decisions (Joe): Hollisworks runs its own lot-level engine with a
per-account switch to the custodian's (only where `custodian_tax_loss_harvesting` is
evidenced 'yes'; Altruist 'unknown'); every human action is a human task; nothing
trades.

Services: `services/wash_sale.py` (household CONTROL — replaced rep5's flag),
`services/tax_profiles.py` (subjects, profiles, carryforwards, declared outside
trades, combined rates), `services/substitutions.py` (sets, denials, rule seeding,
suggestions), `services/harvest.py` (the daily scan → one harvest proposal),
`services/loss_bank.py` (sources, IRS netting, the ledger), `services/tax_overview.py`
(the dashboard and the account panel). Routes: `routers/tax.py` (`/tax/…`,
`/harvest/…`). Schema: `docs/rep6_part1.sql` (applied by `scripts/_apply_rep6.py`).
Screens: Portfolio → **Tax** (`/portfolio/tax`: Harvest | Substitutions | Profiles |
Declared outside trades) and a Tax panel on the account page. Verify:
`apps/api/scripts/verify_rep6.py` (written, not run in the sprint).

### Task 1 findings (rep6)

- **1a.** `rebalance_proposals.generate(pool, *, org_id, account_id, trigger,
  maker_kind, maker_id=None, auto_submit=False, trigger_ref=None, withdrawal=None,
  today=None, harvest_plan=None)` (harvest_plan is new); `compute(conn, org_id,
  account, link, version_id, *, withdrawal=None, today=None)`;
  `rebalance_engine.plan_sales(requests, SaleSettings)` (SaleSettings gains
  `wash_hold`); `account_models.load_account / active_link / settings_of /
  harvesting_options / load_holdings`; `human_tasks.open_task / complete_task /
  cancel_task / register_kind(…, system_open=)`; `custodian_capabilities.
  capability(conn, custodian_code, key)`. `wash_sale_flags` was called once
  (`compute`), and its output read by the trades insert (`wash_sale_flag`),
  `_trade_line` (→ the instruction's `wash_sale_notes`), `trade_out`, the
  ProposalDetail screen, `verify_rep5.py` (y_wash, y_execution) and
  `tests/replication/rep5.test.mjs`. All moved to the control's `wash_sale` result; a
  pre-rep6 proposal still shows (and routes) its flag.
- **1b subject.** A profile hangs on the account's HOUSEHOLD (`accounts.household_id`
  → `households`; a married couple filing jointly is one profile covering both
  spouses' accounts), or — for an account with no household — its primary entity.
  Wash-sale scope is wider: rep5's `household_accounts` (household, primary entity,
  shared `account_owners`).
- **1c loss bank sources** (each counted once): `lot_close` — a closed
  `tax_lots.realized_gain_loss` (the custodian's number, keyed by the lot); `fill` —
  a rep2 fill on a sell, per lot it names, PROVISIONAL, closed (`valid_to` + reason)
  when the custodian's closed lot for that tax lot arrives; `carryforward` — an
  approved row; `declared_outside` — a declared sale with an amount and term.
  `portfolio.transactions` carry no gain (rep5's finding) and are not a source. One
  row per source key, closed rows included, so a re-run never re-adds anything.
- **1d pending trades.** An open execution instruction counts from today to its
  execute-by; a proposal awaiting approval (or approved and not yet routed) counts
  from today to its expiry + the execute-within days — only its clear / replaced
  lines. The account's own draft or pending proposal is excluded (the new one
  supersedes it). Reported fills count on their fill date.
- **1e hook.** `rebalance_proposals.org_tick` (the scheduler's `proposals_tick`,
  after the holdings refresh and rep2's execute-by step): expiry / execution sync →
  model-change sweep → **loss-bank ingest → harvest scan** → the drift / cash-flow
  scan. The harvest scan runs before the drift scan, so it gets the account's one
  open-proposal slot first; it skips an account that already has an open proposal
  (each candidate recorded as `open_proposal`).

### Schema (`docs/rep6_part1.sql`)

Nine org-scoped tables, each with a policy for SELECT / INSERT / UPDATE / DELETE:
`tax_profiles`, `loss_carryforwards` (versioned, both axes, one live approved per
subject and year, maker-checker CHECK), `substitution_sets` + `substitution_set_
candidates` (every version kept), `substitution_denials` (pairs stored in id order;
rule-seeded = the system's, active at once; an adviser's needs a second person),
`declared_outside_trades` (valid axis), `loss_bank_entries` (valid axis; unique per
source key), `harvest_runs` (one live run per account per day) and
`harvest_candidates`. Combined rates are computed, never stored. rep5 changes:
trigger 'harvest'; `rebalance_proposal_trades.wash_sale` with a CHECK that a deferred
or blocked line never carries an instruction; four harvest settings on the link
(NULL = the org default).

### Tax profiles and carryforwards

Entered by `manage_tax` (rates as decimal fractions, the as-of date, a source note),
approved by a different holder of `approve_tax` on a maker-checker human task
(`tax_profile_approval` / `loss_carryforward_approval`). Combined rates:
  ST = federal ordinary + NIIT (3.8% when it applies) + state + city
  LT = federal LT + NIIT + state LT + city LT — a state / city with no separate LT rate
       taxes LT gains as ordinary income, so its ordinary rate is used.
No approved profile → every tax value reads "not estimated — no tax profile", never a
default rate.

### Household wash-sale control (`services/wash_sale.py`)

- **Window: 61 days** — a loss sale on day L is washed by a substantially identical
  purchase on L − 30 … L + 30, both edges IN; day 31 either side is clear (a buy is
  clear from L + 31; a loss sale from B + 31 after the last matching buy B). A pending
  trade spans every day it could still happen.
- **Who:** every account of the household (spouse, IRAs / Roths) + declared outside
  trades. **[FIND]** A loss realized INSIDE an IRA / Roth is not deductible, so it
  opens no window; a BUY counts in every account. `tax_class` reads the account's
  `tax_status` (free text — no CHECK): 'taxable', a deferred / exempt status or an
  IRA-like registration → tax-advantaged; anything else (the importer's `UNKNOWN`) is
  treated as taxable.
- **Substantially identical** = the same security or an ACTIVE denial pair (either
  direction). Nothing inferred; an approved substitute is not identical unless a
  denial says so (a denial overrides a set).
- **Rules:** (a) a buy inside a household loss window → the next approved substitute
  that is itself clear (same dollars; by amount when it has no price), else deferred
  to the first clear day (or the cash kept — org setting
  `replication.tax.wash_buy_fallback`); (b) a loss sale with a household buy within 30
  days → its loss lots wait for the first clear day and the plan sells other lots
  instead (`plan_sales` — held lots never add gain-budget room); (c) an IRA / Roth buy
  inside a taxable loss window with no clear substitute → BLOCKED in plain words;
  (d) planned loss sales (open instructions, pending proposals, the proposal's own)
  reserve their forward 30 days. Every trade line carries `clear | replaced | deferred
  | blocked` with its matches; deferred and blocked lines are recorded and never
  routed. A sale of unknown cost cannot be shown to be a loss: it is not held (its
  matches are recorded); a sale whose loss is unknown still opens a window for buys.

### Substitutions

A set per security: ranked candidates, each with a reason and evidence. The model's
alternates are offered as suggestions only (`GET /tax/substitutions/suggestions`).
Proposed by `manage_tax`, approved by another holder of `approve_tax`; a newer approved
version supersedes the older one (kept). ONLY approved, live sets are read by the
engine. Denials seeded from the rules (`POST /tax/substitutions/denials/seed`):
same index (approved rep1 terms with the same `index_name`, both in the org's
registry), share classes (same SEC series in the registry, or one canonical security
in the master), plus adviser pairs. Retiring a set or a denial needs `approve_tax` and
is never done by its maker.

### Harvesting (`services/harvest.py`)

Daily, per account following a model with engine 'hollisworks': candidates = open lots
at a loss on the account's own valuation; net loss = loss − market value × the org's
`replication.tax.trading_cost_bps` × 2; taken when the net loss is at or above BOTH the
% threshold (net loss ÷ cost basis) and the $ threshold (link settings, else
`replication.tax.harvest_loss_pct` / `.harvest_loss_min`, else 5% / $500). Value =
loss × the lot's combined ST / LT rate — an estimate with its rate and as-of.
Unpriced → "needs a price"; no cost basis → skipped; a household buy within 30 days →
held (wash sale); no clear approved substitute → skipped (or harvested to cash when
`harvest_to_cash`). Swap back (`harvest_swap_back`): 31+ days after an approved
harvest, sell the replacement lots and buy the original when it is clear; the sale
goes through `plan_sales` (gain budget, short-term guard). ONE proposal per account
per day, trigger 'harvest', a SYSTEM proposal on the review task — a person approves
(`approve_proposals`); every candidate is a `harvest_candidates` row. Engine
'custodian' → no harvest proposals (the run says so); its realized losses reach the
loss bank through the custodian's lots.

### Loss bank and tax-alpha ledger

IRS order: ST vs ST (the ST carryforward is an ST loss), LT vs LT, then cross-net; a
net loss offsets ordinary income up to $3,000 ($1,500 married filing separately),
taken from the ST loss first; the rest carries forward with its character. Per subject
and year: totals, every netting step, carryforward in / out, the losses harvested on
approved harvest proposals, and the estimated value at the profile's rates (labelled;
"a deferral, not a permanent saving" stated).

### Not estimated — needs prices

`securities_global_prices` has 0 rows: the tracking-error cost of a harvest and
pre-tax / after-tax PERFORMANCE read "not estimated — needs prices" wherever they
would appear. Later sprints: the dividend holding-period guard, charitable gifting,
step-up flags, the Section 351 exit.

### Permissions and screens

`view_tax`, `manage_tax`, `approve_tax` — org_admin by default (migration +
`rbac.ensure_org_admin_role`). 31 routes, each gated and in
`docs/AUTHZ1_ROUTES.json`; harvest proposals keep rep5's `approve_proposals`. Every
envelope carries `permissions`, `vocabularies` (filing statuses, wash-sale outcomes,
skip reasons, ledger sources, denial rules, …; `editable` empty for a view-only
caller) and the server's `actions` (per row for decisions — never the maker's). The
screens render a control only for `=== true` on the flag AND a listed action
(`lib/tax.mjs`; `tests/replication/rep6.test.mjs`). Every tax number shows as
"$X (estimate)" with the rates and as-of on hover.

### rep6 operator steps

1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep6.py` (it runs
   `node --test`, `npm run build` and the `verify_rep5.py` regression itself).
2. Redeploy the scheduler cron service (the tick now ingests the loss bank and runs the
   harvest scan) and the API.
3. Optional org settings: `replication.tax.trading_cost_bps` (0), `.harvest_loss_pct`
   (0.05), `.harvest_loss_min` (500), `.harvest_to_cash` (false), `.harvest_swap_back`
   (false), `.wash_buy_fallback` ('defer').

## rep7 — held-away accounts and the portfolio X-ray (2026-10-26)

Read-only visibility into accounts held elsewhere, every position with its source and
as-of; held-away data feeding household wash-sale control, the gain budget and the loss
bank; a stale-data confirmation task; and the X-ray, which looks through every fund a
household holds, marks the replicable ones and opens a join request in one click.
**A held-away account is never traded.** No aggregator is contracted: the provider
interface exists, nothing is connected, and there is no client code or network call.
Completion portfolios and overlap consolidation are rep10.

### Task 1 findings (rep7)

- **1a — held-away accounts today.**
  - `accounts.is_held_away` is set only by the custody CSV importer (`services/custody/importer.py`).
  - A held-away account carries an ordinary `custodian_code`.
  - Readers before rep7:
    - `account_models.harvesting_options` (no custodian harvesting)
    - `execution` (display)
    - `custody/trading.route_trade_list` (**routed to manual**, not refused)
    - `rebalance_proposals.compute` (a warning only) and `.execute` (executor = member)
    - `fee_calc` / `fee_run_inputs`, `model_upload`, `tax_overview`
  - **Trade paths it could reach:**
    - `account_models.propose_change` (join → a transition)
    - `rebalance_proposals._generate` (every trigger) and `.execute`
    - `harvest.scan_account` (no held-away check at all)
    - `execution.create_instructions` / `route_trade_list`
  - Each now calls `services.held_away.refuse` once, at its entry (409, plain words).
  - rep5's account envelope lists no join / model / settings / proposal action for a held-away account.
  - **[FIND]** `verify_rep2` and `verify_rep5` asserted the old behaviour (manual routing; the member executes). Their assertions were restated to the refusal.
- **1b — where the data lives.** Held-away positions, lots and transactions go in `portfolio.positions` / `tax_lots` / `transactions`, the one place every reader already looks.
  - What was missing is provenance: which source and which approved import wrote a row, and its as-of.
  - That is `public.held_away_records`, a link table, so the shared portfolio tables gain no held-away semantics.
- **1c — no double count.**
  - `wash_sale.load_context` reads every `household_accounts` account's transactions, so held-away ones are included (event source `held_away`). It also reads `declared_outside_trades`.
  - When a held-away transaction matches a declared trade (same subject, security, side, trade date and, when declared, quantity), the match is recorded on its provenance row (`matched_declared_id`). The declaration is then skipped in the window and its loss-bank entry is closed.
  - `realized_ytd` was per-account. It now adds `held_away.realized_elsewhere` (same tax subject).
- **1d — reuse.**
  - **rep3's file machinery:** `model_upload.precheck` / `read_workbook` / `detect_header` / `suggest_mapping` / `mapping_from_request` now take an optional field vocabulary (`fields=`). Model uploads are unchanged; the held-away file uses the same machinery with its own fields.
  - **Chancery:** a statement is a `documents` row linked via `document_record_links` (`record_type 'held_away_account'`). Its text is the latest `document_extractions.extracted_text`.
  - **Ensembles:** they are keyed by `ai_ensemble_configs.task_key`, and `'statement_positions'` was added. The Claude refusal is `replica_terms.is_claude`. With no active row the path says "no extraction models configured".
- **1e — registry.**
  - A held fund matches `portfolio.replica_securities.global_security_id`.
  - Its holdings and their as-of come from `replica_holdings.latest_snapshot` / `snapshot_rows`; weights are fractions.
  - `expense_ratio_net_pct` (a **percent**) comes via `replica_models.term_value`.
  - The verdict is `replica_viability.list_reports()[0].overall`.
  - The strategy fee comes via `replica_viability.strategy_fee`. There is no all-in-rate function.
  - **Sector:** the security master has no sector column. It is taken from the holdings file's own `raw->>'Sector'`, else "unclassified".

### Schema (`docs/rep7_part1.sql`)

| Table | What it holds | Axis |
|---|---|---|
| `held_away_sources` | Kind, provider, status, `last_as_of`, `stale_after_days` | System axis |
| `held_away_imports` | One row per load: draft → approved / rejected; the mapping, rows read / accepted / rejected, rejects with reasons, payload, extraction metadata | System axis |
| `held_away_records` | Provenance per position / lot / transaction, plus `matched_declared_id` | Valid axis: closed when replaced, never deleted |
| `stale_confirmations` | pending → confirmed → expired / cancelled | System axis |
| `xray_runs`, `xray_lines` | Recorded runs | Written once |

- Constraints:
  - An aggregator / feed source can never be `active` (nothing is connected).
  - The maker never approves their own import.
  - Partial uniques: one live source per account + kind + provider; one live match per declared trade; one open confirmation per account + purpose + proposal; one scheduler run per household per day.
- RLS: NULLIF org isolation on SELECT / INSERT / UPDATE / DELETE for every table.
- Loss bank: `loss_bank_entries.source` gains `'held_away'`.
- Permissions: `view_held_away` / `manage_held_away` / `approve_held_away`, on every org_admin role and in `rbac.ensure_org_admin_role`. These are not the tax permissions: keying another custodian's positions is a data duty, and the import needs its own checker.

### Sources, in order (best first)

1. A direct custodian feed.
2. An aggregator (ByAllAccounts, Plaid, Yodlee). Both are registered in `services/held_away_adapters.py` and shown **"not connected"**. The interface covers accounts, positions, lots, transactions and as-of.
3. A statement filed in Chancery, extracted by the `statement_positions` ensemble (never Claude). An item is accepted only when **both** readers report it identically; a row only one reader saw is rejected as `models_disagree`.
4. A file (xlsx / xls / csv through rep3's machinery) or manual entry, which a person can always use.

### Imports and approval

- **Every load is a draft import.** This includes refused files, which are logged with the reason and can only be rejected.
- **Rejected rows are listed with reasons:** no identifier, not in the security master, identifiers conflict, not a number, negative, a date after the as-of, side / term invalid, a spreadsheet formula / error cell.
- **Nothing changes until a second holder of `approve_held_away` approves.** This is a human task, maker ≠ checker, enforced by a DB CHECK as well.
- **An approved import replaces the account's live positions as of its date:**
  - The earlier rows and open lots are closed on the valid axis and kept.
  - New lots and transactions are added. A sale with a realized result also writes its closed lot.
  - A transaction already recorded is skipped.
- **An import older than the account's data is refused** before the task commits (`human_tasks.register_precheck`, new).

### Staleness rule

- **Threshold:** an account's data is stale when today − its as-of > `stale_after_days`. The source's value applies, else the org setting `replication.held_away.stale_after_days` (default 30).
- **Edge:** data exactly that many days old is not stale; one day more is.
- **Effect on proposals:**
  - Every proposal states the date of any stale held-away dependency. A dependency is a held-away account of the household (wash-sale) or of the tax subject (gain budget).
  - The platform opens the `confirm_held_away_data` task.
  - **Approval is refused before the approval task commits** (a pre-check on rep5's approval and review kinds) until an adviser holding `manage_held_away` confirms.
- **Confirmation:**
  - It is logged with who confirmed and which as-of.
  - It expires when newer data is approved, or after `stale_after_days`. It holds on its expiry day.

### How held-away data enters tax

- **Wash-sale:** held-away trades are household trades. An IRA held elsewhere counts as tax-advantaged, so rule (c) blocks a substantially identical buy into any household IRA. A declared trade matched by a held-away transaction counts once.
- **Gain budget:** `realized_ytd` = the account's own + gains realized in held-away accounts of the same subject. The proposal says "realized YTD includes $X realized elsewhere (N closed lots …)".
- **Loss bank:** held-away closed lots enter with source `held_away` (`lot_close` no longer takes them), idempotent per lot. A matched declared sale's entry is closed.

### The X-ray method (`services/xray.py`)

- **Holdings:** every account of the household, managed and held-away, with source and as-of per line.
- **Look-through:**
  - A registry fund with a live holdings file is decomposed: exposure = weight × the holding's market value, cents ROUND_HALF_EVEN, labelled with the holdings as-of.
  - Any other fund stays one "no look-through" line.
  - A single stock passes through at weight 1.
- **Aggregates:**
  - Exposure by security and by sector.
  - Concentration flags at or above `replication.xray.single_name_pct` (default 0.10).
  - Overlap between funds: Σ min(weight) over shared constituents.
  - Overlap with followed replicas.
- **Replicable funds** (each figure an **estimate** with its inputs and as-of):
  - **Verdict:** rep4's verdict, else "no viability report"; the approved "Holdings of …" model, else "no model yet".
  - **Annual fee difference** = market value × `expense_ratio_net_pct` ÷ 100 − the model's strategy fee. If there is no schedule: "no fee schedule".
  - **Embedded gain** comes from the fund's open lots.
  - **Transition tax** = gains netted per character × the rep6 profile's combined rates. No profile → "not estimated — no tax profile". An IRA / Roth owes no tax.
  - **Break-even** = tax ÷ fee difference.
- **Join:**
  - "Open join request" (a MANAGED account, an approved model, a verdict of yes) calls rep5's `propose_change(join)`. The clicker is the maker and rep5's maker-checker follows.
  - A held-away line has no button and reads "held away — moving the assets comes first".
- **Needs-prices lines:**
  - Tracking error: not estimated — needs prices.
  - Harvest value of switching: not estimated — needs prices.
  - After-tax performance: not estimated — needs prices (`securities_global_prices` has no history).
- **Scheduler:**
  - rep5's `org_tick` runs the held-away staleness check, then the X-ray sweep (one recorded run per household and day), after the harvest / drift steps and therefore after the holdings refresh.
  - Test orgs are skipped.
  - A verify that ticks its own test org gets no sweep unless it asks (`xray_sweep=True`), so an older verify's household teardown is never blocked by a run it did not create.
  - Neither step trades nor opens a proposal.

### Permissions and screens

- **Routes:**
  - `/api/v1/held-away/…` (14) and `/api/v1/xray/…` (5), all in `AUTHZ1_ROUTES`; no existing gate changed.
  - X-ray reads need `view_replication` plus household access. Super Admin, staff and org admins (`manage_org_settings`) see any household; anyone else only a household holding an entity the visibility engine grants them, else 404.
  - Recording a run needs `manage_replication`; the join needs `manage_proposals`.
- **Account page (held-away):**
  - Sources (kind, status, last as-of, stale badge).
  - Imports: the log, draft review with each reject and its reason, approve / reject.
  - Positions and lots with source and as-of, with earlier versions kept.
  - Statements, and stale-data confirmations.
  - **No trade control at all.**
- **Household → "X-ray" tab** (`/portfolio/households/{id}`).
- **Proposal view:** the stale held-away badge and the confirm link. Approve is hidden while the proposal is blocking (the server refuses it anyway).
- **Client logic:** controls fail closed (`lib/heldAway.mjs`; `tests/replication/rep7.test.mjs`).

### rep7 operator steps

1. Run `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep7.py`. It runs `node --test`, `npm run build` and the `verify_rep6.py` regression itself (VERIFY_NESTED=1, 2700 s).
2. Redeploy the API and the scheduler cron service (the tick now runs the staleness check and the X-ray sweep).
3. Optional org settings: `replication.held_away.stale_after_days` (30) and `replication.xray.single_name_pct` (0.10).
4. To extract statements, activate an ensemble for task key `statement_positions` with two non-Claude chat models.

## rep7b — account authority and custody (2026-10-27)

Joe's decision 6 (corrected 26 Oct) was "DVP into held-away accounts", not read-only
visibility. rep7 merged the two meanings into `accounts.is_held_away` = never traded. rep7b
separates them into two settings per account:

- **Authority** — what the adviser may do: `read_only` | `advise` | `managed`. Set per
  household by the adviser (`public.household_authority`), overridable per account
  (`public.account_authority_settings.authority_override`). Every change is maker-checker.
- **Custody** — where the account is held: `integrated` (the integrated custodian feed,
  `is_held_away = false`) | `other_custodian` (settled delivery-versus-payment) | `outside`
  (outside the platform). On `account_authority_settings.custody`, with the receiving
  custodian's details for `other_custodian`.

### Task 1 findings (rep7b)

**1a — readers.** `is_held_away`: set by `custody/importer.py`; read by `held_away` (sources,
staleness, the guard), `rebalance_proposals` (warning, executor), `xray` (join eligibility),
`loss_bank` / `wash_sale` / `tax_overview` (data tagging — unchanged), the routers' envelopes and
the web labels. `is_discretionary` → rep5's `advice_only` on the link at join and at generate.
`service_model`: free text, read by nothing that decides anything.

**1b — the matrix (final).**

| authority \ custody | integrated | other_custodian | outside |
|---|---|---|---|
| read_only | nothing (data only) | nothing | nothing |
| advise | one-off + execute/instructions/route, executor member \| other_adviser, never an adapter | same | same |
| managed | every path, as before (adapter routing) | every model path (join, change model, settings, transition, each proposal trigger, harvest); instructions broker \| trader = manual DVP settlement (member \| other_adviser for rep5 advice-only) | **not a setting** (refused) |

Paths: `join`, `change_model`, `settings`, `transition`, `proposal:<trigger>` for join /
model_change / drift / cash_flow / manual / harvest, `one_off`, `harvest`, `execute`,
`instructions`, `route`. Correction to the prompt: advise is NOT `not is_discretionary` — a
non-discretionary managed account following a model gets drift / model-change proposals as
advice today (rep5), and mapping it to advise would refuse them. One source per decision:
which paths ← authority × custody; who executes when managed ← rep5's `advice_only`; who executes
under advise ← the authority (member / other adviser).

**1c — the migration.** Writes no rows. Effective authority = the account's approved override ??
the household's approved authority ?? the default, computed at read time
(`services/authority.default_for`): `is_held_away` → read_only / outside; otherwise managed /
integrated — rep7's behaviour exactly. The 5 live accounts at the time (all in the leftover REP2
fixture orgs, is_test): `******2222` held-away → read_only / outside; `******4321`, `******4444`,
`******5678`, `******6666` → managed / integrated. `_apply_rep7b.py --migrate` proves each path
allowed / refused before = after.

**1d — where authority is read.** The one guard (`held_away.refusal` → `authority.refusal`, one
call per path, now with a structured `path` and the executor), every account loader
(`account_models.load_account`, `execution.load_account`, `xray.household_accounts` attach the
effective setting), the envelopes (account model actions, held-away `trade_actions`, X-ray line
actions), rep5's executor choice (`rebalance_proposals.executor_for`), and staleness (a held-away
account proposed for depends on its own data too). The existing DRAFT instruction on a read-only
held-away account: submit and the approval task's precheck refuse it with the reason; it is neither
deleted nor cancelled.

**1e — DVP settlement.** Beyond today's instruction columns a DVP needs the receiving custodian, its
DTC / participant number, the agent bank, the settlement date, the masked account at the
receiving custodian and the contra / executing broker. They live in a child table
`public.instruction_settlements` (written once with the instruction, valid axis), and a separate
**DVP settlement instruction** PDF (`services/settlement_letter.py`): rep2's letter asks a member
or other adviser to *place* a trade; this one tells a broker how it *settles* — different audience,
same masking rule and the same full-number path.

### Schema (`docs/rep7b_part1.sql`)

- `household_authority` and `account_authority_settings`: draft → approved / rejected on the same
  id (system axis, the draft state archived); a newer approved change closes the previous
  approved row's `valid_to` (never deleted — the history). One live approved row and one pending
  change per household / account (partial uniques). CHECKs: managed + outside refused; receiving
  details only (and then required) for another custodian; participant number digits; receiving
  account masked; maker ≠ checker; a rejection needs a note.
- `instruction_settlements`: CHECK authority = managed AND custody = other_custodian, executor broker
  | trader (advise / read_only never carry settlement detail), settlement after trade date,
  `calendar_basis = 'weekends_only'`; an insert guard ties it to its instruction's account, org and
  executor.
- `rebalance_proposals.proposal_kind` ('model' | 'one_off'); link / model / version nullable only
  for a one-off (manual trigger, made by a person).
- Permissions `view_authority` / `manage_authority` / `approve_authority` (org_admin by default).

### The advise flow

1. Account page or X-ray line → **Propose a one-off trade** (only on an advise account; the X-ray
   line offers it instead of "Open join request"). A draft manual proposal: the engine builds the
   trades from a target that moves only the named securities to / from cash, so lot selection, the
   gain budget (incl. gains realized elsewhere), household wash-sale control and held-away
   staleness all apply.
2. The maker submits; a second holder of `approve_proposals` approves (stale held-away data waits
   for the confirmation task).
3. The approved proposal routes to instructions executed by the member or the other adviser the
   adviser chose (name and contact recorded), source `proposal`, never a custodian API; each has
   rep2's letter of instruction (masked unless the full number is typed and checked).
4. The fill is entered by the adviser, or the instruction is reconciled when the held-away feed /
   import shows the trade (`POST /execution/instructions/{id}/reconcile-held-away` —
   `custodian_data`, reference `held_away_record:<id>`; same security and side, and quantity when
   stated).

### Manual DVP settlement (until rep9)

A managed account at another custodian follows models like any managed account (joins, every
trigger, harvest). Its approved proposals route to MANUAL instructions for a broker (the contra
broker, else the receiving custodian), each with its `instruction_settlements` row: receiving
custodian, DTC / participant number, agent bank, masked account there, trade date and settlement
date = trade date + 1 business day (weekends only — no holiday calendar exists). Nothing calls a
custodian API; a person sends the DVP settlement instruction PDF. Staleness applies: the account's
own held-away data is a dependency. Block trading in an omnibus account, allocation and automated
DVP are rep9. Billing / disclosure for advising on held-away assets: an open decision (Joe and
counsel) — nothing is shown.

### rep7b operator steps

1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep7b.py` (runs `node --test`,
   `npm run build` and the `verify_rep7.py` regression — VERIFY_NESTED=1, 2700 s).
2. Redeploy the API. Refresh the schema snapshot (`refresh-schema`).

## lotmethod — the lot-relief method (2026-10-28)

Joe (2026-10-26) asked for the lot method as an option. Before this sprint one fixed rule
(`rebalance_engine.lot_order_key`) served every sale. Every sale still names its lots on the
instruction (specific identification); what changed is HOW the lots are chosen.

### Methods (`services/rebalance_engine.py` — `LotMethod`, `method_key`, `_plan_by_method`)

| method | order |
|---|---|
| no method set (default) | **today's rule, exactly** — `_plan_today_rule` is the pre-sprint `plan_sales` body, unchanged: losses first (largest loss per share), then the lowest gain per dollar raised, long-term before short-term at equal cost, then oldest; unknown cost never sold against a budget |
| `tax_optimized` (once SET) | estimated tax per dollar raised, lowest first: (price − cost) ÷ price × the lot's combined rate (ST when short-term on the sale date, else LT) from the tax subject's approved rep6 profile — so a short-term loss ranks ahead of a long-term loss, a long-term gain ahead of a short-term gain. No profile → today's rule, and every line says so. Never a guessed rate; a lot with unknown cost or term is "not estimated" and ranked after every estimated lot |
| `hifo` / `highest_cost` | highest unit cost first (the same ordering under specific identification — both names kept because custodians use both) |
| `lowest_cost` | lowest unit cost first |
| `fifo` / `lifo` | oldest / newest acquisition first |
| `specific` | the adviser's picked lots on the proposal, validated (an open lot of this account in a security the sale sells; enough quantity; not wash-held; not unknown-cost against a budget; not over the sale) and used as given; any shortfall filled by today's rule, stated |

Gain preference: `prefer_long_term` (ties → long-term), `st_losses_first` (short-term losses ahead
of everything), `realize_gains` (gain lots, long-term first, until the adviser's entered target —
the lot that crosses it is sold only as far as the target; it chooses lots for sales the proposal
already makes and never adds one; the target is never computed from income we do not hold).

**Average cost.** The security master marks no mutual-fund type and no cost-basis method
(`security_type 'fund'` covers ETFs and mutual funds alike), so average cost is the ACCOUNT's
election: `account_lot_method_settings.average_cost_security_ids`. For those holdings the engine
makes no lot choice — every share at the average cost, holding periods first in, first out, the
instruction names no lots.

**Precedence, always: wash-sale holds and hard blocks > guards (short-term guard, gain budget) >
method.** A method selects WHICH lots fill the sale; the budget then decides now or later
(losses among the selected go now; selected gains in the method's order while they fit). No
method sells a wash-held loss lot, an unknown-cost lot against a budget, or a deferred short-term
gain now.

**[FIND] "tax_optimized (default)" vs "no figure changes".** With a tax profile the two cannot both
hold. Resolved: an account with NO method set keeps today's rule exactly (source `default`); the
profile-based ranking applies once an adviser sets a method (household, account or proposal).

### Settings and precedence of sources (`services/lot_method.py`)

Effective = the proposal's override ?? the account's approved override ?? the household's approved
default ?? none set (today's rule). `public.household_lot_method` and
`public.account_lot_method_settings` copy rep7b's authority pattern — versioned (system axis for
the decision, valid axis for supersession), **maker-checker** (task `approve_lot_method_change`,
`approve_proposals`, never the maker): a lot method is the same investment-policy class as rep5's
gain budget and short-term guard, which are already approved link versions. The per-proposal
override (`POST /lot-method/proposals/{id}/override`) makes a NEW proposal (trigger `manual`, the
person its maker, `trigger_ref.lot_method_override_of`) that supersedes the draft / pending one;
its own maker-checker approval approves the method. Gates: rep5's `view/manage/approve_proposals`
(no new permission). The migration wrote no rows.

The custodian's own method (`custodian_cost_basis_method`: as reported by the custodian or entered
by the adviser; `unknown` default) — when it is KNOWN, not specific identification and differs
from the account's method, the proposal warns (`lot_method_mismatch`) and every instruction's notes
say the named lots must be given to the custodian. `unknown` warns nothing (so no existing
proposal's warnings change).

Every proposal records `lot_method`, `lot_method_source`, `gain_preference`,
`realize_gains_target`, `lot_picks`, `custodian_cost_basis_method`; every sale line its
`lot_method`, and each lot its reason (`lots[].why`).

### Where the method applies

- Rebalance (every trigger), one-off, transition preview — through `plan_sales`.
- Harvest: a LOSS line sells each qualifying loss lot whole (judged on its own thresholds — no
  choice for a method to make; line method `harvest_loss`); the SWAP-BACK sale goes through
  `plan_sales` with the account's method.
- X-ray: a switch sells the whole fund, every lot, so the method changes no lot choice (stated);
  a fund the custodian carries at average cost is figured at the average cost
  (`xray.embedded_by_character`) — same total gain, a different ST / LT character and so a
  different transition tax.

### Screens

Household → tab "Lot method"; Account → "Lot method" panel (method, preference, custodian
method, average-cost securities, history, request / approve); Proposal → each sale's method and
each lot's reason, and "Choose the lot method" with a lot picker for `specific`. Every control
from `lib/lotMethod.mjs`, failing closed on the server's flags and actions.

## rep8 — the options sleeve (phase 2 starts, 2026-10-28)

Spec sections: "Options sleeve (buffer ETFs and notes)", "Options overlays" (the tax-treatment table and the tax gate), "Terms captured per class" (buffer and income ETF rows), "Other features worth building" (the payoff lab).

Instrument order (Joe): index ETFs → **defined-outcome options sleeve** → notes → long / short. Every human action is a human task. The custody cliff holds: nothing trades, and every options trade is a manual execution instruction.

Services:
- `services/options_sleeve.py` — contracts, quotes, payoff, builder, minimum account, rolls and expiry, fill booking.
- `services/options_tax_gate.py`
- `services/options_proposals.py`

Routes: `routers/replication_options.py`. Schema: `docs/rep8_part1.sql`, applied by `scripts/_apply_rep8.py`. Screens: the **Options** tab on an entry (`components/replication/OptionsTab.jsx`) and the tax-gate panel on an options proposal (`components/replication/OptionsTaxGate.jsx`). Client logic: `lib/replicationOptions.mjs`, which fails closed. Verify: `apps/api/scripts/verify_rep8.py` — written, not run in the sprint.

### Phase-2 gates still open — built around them

- **Altruist options access.** `options_level` and `flex_options` are `unknown`, and unknown counts as not available.
  - Every options leg becomes a MANUAL instruction for the custodian desk, a broker or a trader.
  - Viability Q4 and the Options tab both say: "manual execution only — Altruist options access unconfirmed".
  - The core is never blocked. A buffer package needs options level 3 or higher; a covered call needs level 1 or higher.
- **No options data or price vendor.** Premiums, index levels, underlying prices and implied vols come only from `portfolio.adviser_quotes`.
  - Each quote records its value, source text, as-of and who entered it.
  - A new quote is a new row. A trigger refuses any UPDATE.
  - Every priced figure is labelled **estimate** and lists its quotes and their as-of.
  - With no quote, the figure reads "not priced — enter a quote".
  - At-expiry payoffs need strikes only, so they are always available.
- **Counsel question (26 U.S.C. 1092).** Are index options written against a full stock basket offsetting positions? Until counsel answers, the gate treats them as a straddle and labels each such result "pending counsel review". The same label goes on index collars under §1259.

### Task 1 findings (rep8)

- **1a.** A security is a `portfolio.securities_global` row.
  - The type CHECK had no `option` value; it was added.
  - Org holdings run `assets.global_security_id` → `positions` → `tax_lots`.
  - An option contract = an `option` security plus a PLATFORM `portfolio.option_contracts` row with all its terms. The terms are the contract's identity (`terms_key`, partial unique), so defining the same contract twice returns the first.
  - Contracts are created as a platform job under `app.is_super_admin`, the same way rep1 creates a fund.
  - Positions, lots, instructions and wash-sale control all keep keying on `global_security_id`.
- **1b.** Terms are rep1's versioned `term_values` over the field registry. The registry was global, so it gains `instrument_classes` (NULL = every class).
  - `load_registry(conn, class)` and `for_class` give each class its own fields. An index fund's extraction never asks for buffer terms.
  - Holdings parsers type option rows `other`, with no strike or expiry. **Leg terms therefore come from the approved terms, not the holdings file.** Parser work for N-PORT option legs is later work.
- **1c.** A sleeve in rep3 is a weight, and an options overlay is not one.
  - The options sleeve is an `option_packages` row on the model version. The model version holds only the core (`sleeve_label 'core'`).
  - A proposal is `rebalance_proposals.proposal_kind 'options'`, and each line carries `option_leg`.
  - An instruction carries contracts in `quantity` and the leg in `option_terms`. A CHECK allows `option_terms` only with `routed_via 'manual'` and executor `custodian_desk | broker | trader`.
- **1d.** `wash_sale.identical()` is the single decision point. It is extended, never weakened: an option counts as its underlying's EXPOSURE.
  - An option on X is substantially identical to X, and two options on one index are identical to each other.
  - A denial pair on the exposures, such as an adviser's "index ↔ ETF", carries through to their options.
  - For a non-option, the exposure is the security itself, so every earlier answer is unchanged.
- **1e.** `securities_global_prices` holds no licensed series. Quotes live in `portfolio.adviser_quotes`, keyed by instrument, kind and as-of.

### The package and the builder

- **Buffer package:** underlying (ratio 1), plus a long put at the money, a short put at the buffer's end and a short call at the cap.
  - Strikes are a % of the outcome-period start level, taken from the approved terms (`leg_*_strike_pct`).
  - Default legs are FLEX index options: European, cash-settled, broad-based (Section 1256).
- **Income package:** underlying, plus written calls at (1 + % out of the money), ratio = coverage, expiry = start + tenor.
  - For a delta rule, the adviser enters the strike chosen for the target delta. It is never computed without a model.
- **Cap / buffer / floor** are computed from the legs (exact Decimal) and stored next to the fund's stated values. Floor = the worst at-expiry return.
- **Builder** (find or build, one options model per entry): a "Holdings of …" fund_replica model.
  - The core is an approved index replica model (its look-through) or the ETF itself.
  - A fresh outcome period starting on the funding date is allowed, with the same length as the fund's period.
  - Approval: the model through rep3 (`approve_models`); the package through its own maker-checker task (`option_package_approval`, `approve_replication`). Approval is refused until the package is priced (start level and every leg's contract).
- **Minimum account.** One contract covers N = multiplier × the quoted level. The sleeve covers c (the largest option-leg ratio) of the account. With hedge tolerance t (`replication.options.hedge_ratio_tolerance`, 5%):
  - minimum = N ÷ (c(1+t));
  - every account from n*·N ÷ (c(1+t)) fits, where n* = ⌈(1−t) ÷ 2t⌉.
  - A mini contract on the same index (flagged `is_mini` with its `mini_ratio`) is considered too. With no level quote, the figure reads "not priced — enter an index level".
- **Basis risk** (basket vs index): tracking error is "not estimated — needs prices".

### Payoff lab (exact)

At expiry, for an underlying return r, let s = 1 + r. Then:

    value(s) = Σ underlying ratio · s + Σ ±ratio · intrinsic(strike %, s)
    replica return = (value(s) − Σ underlying ratio) ÷ Σ underlying ratio

Long legs count +, short legs −. Premiums are excluded (a package opened at zero net premium).

The fund's stated outcome:
- r ≥ 0 → min(r, cap)
- −buffer ≤ r < 0 → 0
- r < −buffer → r + buffer
- never below a stated floor

The lab covers −50% … +50% at `replication.options.payoff_step_pct` (default 5%), plus both buffer edges and the cap. Example: with start 100, buffer 10% and cap 15%, the replica equals the stated outcome at every point.

From quotes only, labelled estimate:
- **Mark-to-market** = Σ ±ratio × premium × multiplier, per package unit.
- **The cap a new package would set** = the HIGHEST QUOTED call strike whose premium covers the net put cost. It is never interpolated.

A structured-note comparison slot is present, marked "phase 3", and empty.

### The tax gate (`services/options_tax_gate.py`)

The gate runs when a proposal is generated (the results are shown) and again as a pre-check on rep5's approval task.
- Any BLOCK refuses approval with a 409 naming the rule, and the task stays open.
- Every result is stored in `options_tax_gate_results` with its inputs. A newer run closes the earlier rows.
- The proposal view also hides Approve while any rule blocks.

| rule | outcome |
|---|---|
| section_1256 | A broad-based, exchange-traded index option (FLEX included) → FLAG: 60/40 and year-end mark-to-market. Single-stock, fund or narrow options → pass (not 1256). |
| wash_sale | A leg that acquires the exposure (a call bought to open) inside a household loss window of the same issuer or index → BLOCK. A leg closed at a loss (or at an unknown result) with a household buy within 30 days → BLOCK. The window edges are rep6's: L ± 30 is in, L ± 31 is clear. |
| cash_secured_put | A put written on an exposure the household sold at a loss within 30 days → BLOCK (a wash-sale purchase). |
| straddle | Options while the account holds the core → FLAG: loss deferral and holding-period suspension. Index or fund options against the basket / ETF are labelled **pending counsel review**. Realizing a loss (or an unknown result) on one leg while the offset stays open → BLOCK. |
| constructive_sale | A collar (long put + short call on the same underlying and expiry, with no written put below) on an appreciated position whose band is at or inside `replication.options.constructive_sale_band` (15%) → BLOCK. Appreciation that cannot be shown absent counts as appreciated. A buffer package is not a collar. |
| qualified_covered_call | A call written on a single stock must be exchange-traded with MORE than 30 days to expiry (31 passes, 30 fails). It must not be deep in the money: conservatively, the strike must be at or above the quoted price, because the strike grid is not held. With no price quote → BLOCK "cannot qualify without a price". |

### Proposals, instructions, fills, rolls

- **Proposal.** A person raises it with `POST /replication/options/accounts/{id}/proposals`, choosing open, close or roll.
  - The account must follow the package's model. Authority must be MANAGED (rep7b path `options`); read-only and advise accounts are refused.
  - Contracts = core value × ratio ÷ (multiplier × start level), rounded half-even. A result outside the tolerance gets a warning; zero is refused.
  - Each line's price is the latest premium quote. An unpriced line cannot be approved.
  - The proposal takes the account's one open-proposal slot.
- **Approval** goes through rep5's maker-checker routes.
- **Instructions.** Approval produces one MANUAL instruction per leg, routed straight to `execution.create_instructions`. No adapter is involved, and the manual adapter's submit is not called either.
  - Quantity is in contracts.
  - The limit price comes from the quote, which is also the reference with its as-of.
  - Each instruction carries the expiry, the FLEX terms, and the proposal's maker and checker.
- **Fills** are entered through rep2's flow. `POST /replication/options/instructions/{id}/book` then books option positions, transactions and lots, idempotently per fill.
  - A long lot is booked as +contracts at the premium paid.
  - A short lot is booked as −contracts at minus the premium received.
  - A closing fill closes lots FIFO.
- **Rolls.** rep5's `org_tick` runs the daily options sweep, never for an is_test org.
  - N days before an outcome period ends (`replication.options.roll_notice_days`, default 10), it opens a roll to-do with a DRAFT next package: same buffer, cap leg omitted, so the cap reads "not priced".
  - A person prices it, approves it and proposes the roll. On approval, the old package becomes `rolled`.
- **Expiry.** A package past its end with no approved roll becomes `expired` and gets a to-do.
  - Each cash-settled leg gets a settlement entry per holding account. The intrinsic value comes from an index-level quote dated the expiry day.
  - Confirming the to-do confirms the entries. It is refused while an entry is unpriced.
  - Physically settled legs are entered as trades.

### Permissions and screens

There are no new permissions. Gates by permission:
- `view_replication` — sleeve, package, payoff, settlements, quotes, contracts.
- `manage_replication` — price, submit, quotes, contracts.
- `approve_replication` — package decisions.
- `manage_models` + `manage_replication` — build.
- `view_proposals` / `manage_proposals` — gate results, proposal creation.
- `manage_execution` — booking.

All 17 routes are in AUTHZ1_ROUTES. Envelopes carry vocabularies for leg roles, gate rules and outcomes, and quote kinds. `editable`, `price_editable` and `quote_editable` are empty for a view-only caller. Package `actions` come from the server.

### rep8 operator steps

1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep8.py`. It runs `node --test`, `npm run build`, and the `verify_rep6.py` regression (VERIFY_NESTED=1, 2700 s).
2. Redeploy the API and the scheduler cron service, because the tick now runs the options sweep. Refresh the schema snapshot.
3. Before any real options trade: confirm Altruist's options level and FLEX access, record them in custodian capabilities, and get counsel's answer on the straddle question.

## MARKETDATA1 — "needs prices" through the security-data layer (2026-10-28)

Every price reader now asks the vendor-neutral layer (`services/security_data`, see
`docs/SECURITY_DATA_DESIGN.md`) for a data type. No paid vendor is connected. The first free
source is the custodian's own valuation of a position (market value ÷ quantity on the position
date). It is internal use for the org, and shown to a member only for that member's own holdings.

Lines that now fill from free sources (labelled estimate, with source and as-of):
- **Viability Q3 (minimum account)**: a constituent the org holds in any account is priced from
  its custodian valuation, or from an approved file upload or an adviser / platform price.
  `inputs.price_sources` gives provider, count and oldest as-of. When a constituent is unpriced,
  the message is unchanged ("unknown — needs prices for N of M …"), and `inputs.price_data_type`
  names `eod_price`.
- **Proposals (rep5), harvest replacements (rep6), one-off buys (rep7b)**: an unheld security
  the org holds elsewhere is now bought by quantity at the layer's price
  (`price_source = 'security_price'`, as-of on the line, hover shows both). An unpriced one is
  bought by amount, as before.
- **X-ray embedded gain**: the figure is unchanged (the line's own valuation). It now carries
  `price_provenance` from the layer (provider, as-of, licence).

Lines that still read "not estimated — needs prices", with the missing data type named in
`needs_prices_missing`: tracking error, harvest value of switching, after-tax performance. They
need a daily `eod_price` **history** of the fund and the replica constituents, and no free
source carries one. The harvest plan's tracking-error note is also unchanged. A vendor plugs in
behind the same layer when a real use case needs it.

## rep9 — block trading, allocation and DVP settlement, manual-first (2026-10-29)

Approved account instructions are combined into a **block**: one order for many accounts. The
allocation between accounts is set under the firm's written policy and **fixed before execution**.
A broker, desk or trader of the adviser's choice executes the block. The **manual route is always
offered**: print the order execution sheet, hand it to the broker, enter the fills by hand. Fills
are booked at the **average price**. Each account's share settles to its own custodian, DVP when
that custodian is not the integrated one. Nothing is sent electronically. A FIX router interface
exists, but no router is connected.

Code: `services/blocks.py` (lifecycle), `services/block_allocation.py` (the arithmetic, pure),
`services/order_sheet.py` (the PDF), `services/execution_routers.py` (the FIX interface),
`routers/blocks.py` (30 routes under `/api/v1/execution/blocks/…`). Schema `docs/rep9_part1.sql`
adds 8 tables, each with a policy for every operation.

### Task 1 findings (rep9)

- **1a — the life of an approved trade.** `rebalance_proposals.execute` →
  `custody/trading.route_trade_list` → `ManualTradeAdapter` → `execution.create_instructions`
  (`execution_instructions` + `execution_instruction_lots`, and `instruction_settlements` for a
  managed account at another custodian) → mark sent → `execution_fills` → reconcile → close.
  - A block sits **above** these instructions: `block_allocations.instruction_id`. The
    single-account flow is unchanged with one exception: an instruction held in an OPEN block
    (planned / fixed allocation) is sent, filled and cancelled only through the block, and rep2's
    execute-by sweep skips it.
- **FIND — fills never write `portfolio.tax_lots` for equities.** rep2 records each fill, with its
  lots as jsonb, on the instruction. The book's lots arrive from the custodian feed or the
  held-away import at reconcile. rep9 books each account's share the same way: a rep2 fill at the
  average price, carrying that account's own lots. There is no second write path into the book.
- **1b — eligibility.** An instruction can join a block only when it is:
  - approved, or the carried remainder of an earlier block;
  - for a quantity (not a notional amount) and not an option leg;
  - on an account with authority managed and custody integrated or another custodian;
  - on a discretionary account (`accounts.is_discretionary`), not from a rep5 `advice_only`
    proposal;
  - executed by a broker, trader or custodian desk;
  - still inside its execute-by date.

  A block has one security, one side, one executor and one execute-by date. Advise and read-only
  accounts are never in a block. An ineligible instruction is listed with its plain reason, and
  the system suggests groupings by security / side / executor / window.
- **1c — the omnibus account** is `execution_accounts`. It records the firm's own account at a
  broker: masked reference, DVP-capable flag. It is an org-level record, never a member account
  (no FK to `public.accounts`). The receiving side of each allocation is either the integrated
  custodian, or rep7b's receiving details on the account (`account_authority_settings`).
- **1d — rounding.** An account takes fractional shares only when its custodian's
  `fractional_shares` capability is an evidenced 'yes'. Every real custodian is 'unknown' today,
  so in practice every account is whole shares.
- **1e — the sheet vs rep2's letter.** The letter is for a member or another adviser. The sheet
  adds the executing account, the allocation table with each allocation's settlement instruction,
  the price guidance's source and as-of, and the firm contact. It never shows a member name.
- **Permissions.** Three new permissions: `view_blocks`, `manage_blocks`, `approve_blocks`
  (org_admin by default, and in `rbac.ensure_org_admin_role`). `approve_proposals` was not
  reused: approving a block fixes an allocation across several accounts under the firm's policy,
  a different act from approving one account's proposal. The sheet for a single instruction keeps
  rep2's `view_execution`, the same gate as its letter.

### Block lifecycle

`draft → pending_approval → approved → sent → partially_filled → filled → allocated → settled → closed`,
or `cancelled`. Both the service and `public.trade_blocks_guard` enforce it.

- **Build** (manage_blocks). Needs an APPROVED allocation policy. Refused, with every reason, when
  any chosen instruction is ineligible; nobody is dropped silently.
  - **Opposite sides are never netted.** A selection that mixes buy and sell is refused.
  - **Cross-trade risk.** If a live block of the opposite side exists for the same security and
    the same window, the new block is flagged and needs a reason.
  - A broker or trader block names an active executing account. That account must be DVP-capable
    if any allocation settles DVP.
- **Approve** (approve_blocks; a human task, maker-checker in the service, the engine and a DB
  CHECK). Approval **fixes** each allocation (`planned → fixed`). From then on the database
  refuses any change to `allocated_quantity`. Before approval, the precheck re-reads every
  instruction and refuses if any is no longer eligible.
- **Mark sent** records the channel and the person on the block and on every account instruction.
- **Fills** are entered against the block: quantity, price, time, venue. The block never takes
  more than its total. The average price is recomputed from the fills each time, never entered.
- **Allocate** (a person's act; one transaction). See the arithmetic below. The block's
  if-not-filled rule handles any unfilled remainder:
  - **cancel** — each instruction is completed short (`completes = true`);
  - **carry** — each instruction keeps its remainder working, and can join a later block;
  - **ask** — a to-do goes to the makers and the managers.
- **Corrections** after allocation go only through `allocation_corrections`: a reason, approved by
  a second person on a human task.
  - The block's filled total never changes; shares only move between accounts. A settled
    allocation is not corrected this way.
  - The database refuses any `final_quantity` change that does not carry a newly approved
    correction. The original state stays on record (system axis).
  - rep2's fill rows are immutable, so the correction is carried on the allocation (the block's
    book of record and its settlement) and on the instruction's timeline as an event.
- **Execute-by** (`blocks.expire_tick`, in the cron entrypoint after rep2's, never on a test org):
  - a block not yet working is cancelled;
  - a working block with no fills follows its rule (cancel / carry to today / ask);
  - a partially filled block gets a to-do to allocate (or carries).

  Every one of these is logged with no actor.

### Allocation arithmetic (`services/block_allocation.py`)

- **Average price** = Σ(quantity × price) / Σ quantity, to 6 decimal places, rounded half up.
- **Pre-trade, `pro_rata_requested`.** Each account's allocated quantity is what its instruction
  still needs. A request below the policy minimum is refused at build, as is a fractional request
  on a whole-share account.
- **Full fill.** Every account gets exactly its fixed allocation.
- **Partial fill, `pro_rata_allocated`.**
  1. Each account's exact share is filled × allocated / Σ allocated.
  2. An account whose exact share is below the minimum gets **zero, with the reason**; the rest
     are re-apportioned, repeating until none is below the minimum.
  3. Each share is rounded down to the account's unit (1, or 0.00000001 for a fractional
     account).
  4. Whole shares left over go out one at a time: largest remainder first, ties by account id,
     never above an account's fixed allocation.
  5. Any part-share left over goes to the first fractional account in that same order.
  6. The total always equals the filled quantity exactly. If it cannot, allocation is refused
     with the reason.
- **Lots (sells).** Each account relieves only its own instructed lots. When it receives less than
  it asked for, they are relieved pro rata by the same largest-remainder rule (ties by lot id).

### The always-manual rule

The **order execution sheet** is a PDF generated on demand and never stored. It carries:
- firm, executing account (masked), security (name, ticker, CUSIP), side, total quantity;
- price guidance with the reference source and as-of;
- execute-by and the if-not-filled rule;
- the allocation table: masked account, quantity, receiving custodian, and the DVP or receive
  instruction for each allocation;
- the firm contact and the block id.

It **never** carries a member name or a full account number (this module has no `reveal()`).
Generating it is logged.

The same sheet is offered for **one account instruction** whose executor is a broker, desk or
trader (`order_sheet` in rep2's action list), so manual is available on every trade the platform
proposes. When an automated route is available for that account (managed at its integrated
custodian, and that custodian's `equity_trading_api` an evidenced 'yes'), the adviser gives a
reason for choosing manual. The choice and the reason are recorded on the instruction's timeline.

The reference price is never invented. It is either the adviser's typed price (source 'adviser',
as of that time) or the security-data layer's (provider + as-of). With neither, the sheet says
"no reference price on record", and a range order is refused.

### DVP settlement and reconciliation

- **Integrated custodian.** The custodian receives the allocation versus payment; its desk is
  instructed, as today.
- **Another custodian.** rep7b's `instruction_settlements` row is rewritten from the block's trade
  date (the UTC date of the last fill): the creation-day row is closed and a new one written,
  settling T+1 counting **weekends only — no holiday calendar** (stated on the sheet).
- **Status per allocation:** pending → affirmed → settled, or failed. Each status is entered by a
  person, or settled by matching a record: a `portfolio.transactions` row from the custodian feed,
  or a rep7 `held_away_records` transaction, with the same account, security, side and quantity.
  - **failed** needs the reason and opens a to-do for the makers and the managers, naming the
    allocation and the counterparty.
- **Close rule.** The block settles automatically when every allocation is settled or cancelled
  (a zero allocation is cancelled). Only then can a person close it.
- **Instructions.** Each account instruction then reconciles and closes through rep2, unchanged.

### FIX router interface and the "add a router" checklist

`services/execution_routers.py` defines the interface: `submit_block`, `cancel_block`,
`execution_reports`, `send_allocations`.
- The registry holds one entry, the FIX router (Flyer or similar), marked **not connected**.
- Every method raises `RouterNotConnected`. There is no client code and no network.
- Choosing route `fix` is refused with that reason. The database admits `route = 'manual'` only
  (`trade_blocks_route_chk`).

Adding a router:
1. A signed agreement with the router and its executing broker; the firm's best-execution review
   covers it.
2. A subclass of `ExecutionRouter`:
   - submit_block → NewOrderSingle, with the executing account as the FIX account;
   - cancel_block → OrderCancelRequest;
   - execution_reports → ExecutionReport, turned into `block_fills`;
   - send_allocations → AllocationInstruction, with masked references and settlement details.
3. Credentials in Doppler only, read at call time, never logged.
4. `connected = True` only after a certification run against the router's test venue, recorded
   with its evidence.
5. A migration widening `trade_blocks_route_chk`. The verify must prove manual is still offered
   and is still the default.
6. Router fills go through the same allocation path: the average price is computed here, never
   taken from the router.

### Open compliance items (notes — not decided by the code)

Shown in the Blocks and Allocation policy screens:
- the written aggregation and allocation policy, and its Form ADV disclosure;
- the best-execution review;
- trade-away commissions (recorded per block in `block_charges`, adviser-entered);
- receiving-custodian DVP agreements;
- whether Altruist accepts DVP deliveries in.

The policy text is an org setting the firm writes (`allocation_policies`, versioned, approved by a
second person). The code enforces only its mechanics.

### Screens

- **Portfolio → Blocks:** the list and the builder — suggested groupings, eligible and ineligible
  (with reasons), the allocation preview under the policy.
- **The block page:** approve, the sheet, fills, allocate, settlement status, corrections,
  charges, the timeline.
- **Settings → Allocation policy:** versions, approve.
- **Settings → Executing accounts.**
- **Every broker / desk / trader instruction:** "Print order execution sheet".

All controls come from the envelope's flags and the server's `actions` list, and fail closed.

## rep10 — tilts, completion and consolidation, charitable gifting, the backtest (PHASE 2 COMPLETE)

Sprint 10 of 15 closes phase 2 (options sleeve, security data, blocks, and now "the index plus my
views", holdings-aware completion, gifting and the backtest). Schema: `docs/rep10_part1.sql`
(applied). Verify: `apps/api/scripts/verify_rep10.py` (written, not yet run).

### Where it plugs in

`rebalance_proposals.target_for`: model look-through → the account's exclusions → **the approved
tilt set** → **completion** → the engine (cash floor, lot method, gain budget, wash-sale control).
Everything downstream of the target applies on top of a tilt. An account with neither gets exactly
rep5's target. A proposal's target lines carry `model_weight`, `tilt`, `completion` and
`final_weight` when one applies.

### Tilt math (`services/tilts.py`)

- **Security** +d: weight + d; a security not in the model is added at d (stated); an underweight
  larger than the position is refused.
- **Sector** +d: spread over the sector's securities pro rata (largest remainder at 1e-10).
  Sectors come from the model's source holdings file (`replica_model_specs.snapshot_id`); a model
  with none → "needs sectors".
- **Factor** +d: weight d moves from the securities scoring below the median score to those above
  it, pro rata within each half; scores only through the layer (`factor_score`, subject
  `<security>:<factor>`, CSV upload or a vendor); below `replication.tilts.factor_coverage` (95%)
  of security weight scored → refused "needs factor scores (x% covered)".
- **Renormalisation:** the net of every delta is taken pro rata from the untilted securities; the
  total and the cash target are unchanged; with no untilted security left, cash absorbs it.
- **Limits** (org settings): `replication.tilts.max_security` 5%, `max_sector` 10% (also a factor
  tilt), `max_active_weight` 25%; `te_budget` (unset = none) applies only when TE is estimable.
- **Always shown:** active share = ½ Σ|final − model| (cash included), the largest single-name and
  sector deviations.
- **Tracking error (ex-ante, HYPOTHETICAL):** √(aᵀΣa × 252), a = final − model, Σ the sample
  covariance of daily simple returns on the dates every covered security shares, window
  `te_window_days` (365). Made only when securities with ≥ `te_min_observations` (60) returns hold
  ≥ `te_min_coverage` (95%) of Σ max(model, final) weight; otherwise "not estimated — needs prices
  (x% of weight covered)". Recomputed on every preview; nothing written.
- Tilt sets are versioned per account, maker-checker (`approve_replication`, never the maker):
  draft → awaiting approval → approved | rejected; an approved set is archived when superseded or
  retired.

### Completion rule (`services/completion.py`)

Per managed account (`completion_settings`, valid axis): count_elsewhere, which authorities count
(read_only + advise by default; managed opt-in — two managed completions would net each other),
a stricter stale threshold. A = the account's value; E_k = the household's exposure to k in the
counted accounts (the X-ray's per-account look-through); H = A + their value.
D_k = T_k × H − E_k, never negative (the excess is "over target elsewhere — not traded");
w_k = D_k ÷ A; cash takes the rest (scaled down pro rata when the counted accounts hold more cash
than the model asks). The household's dollars in each security then equal T_k × H within the
stated rounding. Counted held-away data older than the threshold must be confirmed by an adviser
(rep7's `stale_confirmations`, purpose `general`); until then completion is NOT applied at all.
Read-only and held-away accounts are never traded.

### Consolidation

From the household X-ray: each looked-through fund whose overlap (Σ min weights) with a followed
replica or another held fund is ≥ `replication.consolidation.overlap_threshold` (25%). Managed +
following a replica → a rep5 proposal (the fund is outside the target, so the engine sells it under
the lot method and gain budget); managed + no model → a join proposal; advise → a one-off advice
proposal (its instructions print as the letter); read_only → listed, never traded. Transition tax
and break-even are rep7's estimates.

### Charitable gifting (`services/gifts.py`, `gift_letter.py`)

- Candidates: long-term lots at a gain with a known cost and date, in managed or advise accounts,
  ordered by gain per dollar ((price − cost) ÷ price). Excluded with reasons: short-term (deduction
  limited to basis), loss lots ("sell it, take the loss, and give cash instead"), read-only,
  unpriced / unknown cost / unknown date, average-cost holdings.
- Comparison (all estimates with rates and as-of): CG tax avoided = gain × LT combined rate (federal
  LT + NIIT + state + city); deduction this year = min(FMV, limit × AGI) × the federal ordinary rate,
  only if the member itemizes (entered on the proposal with source and as-of; else "no deduction
  value — does not itemize"); the excess carries forward up to five years (not valued). Sell and give
  cash: the same CG tax paid, the cash limit. Tax saved in kind = CG avoided + (in-kind deduction −
  cash deduction). Limits: public charity / DAF 30% stock, 60% cash; private foundation 20% / 30%.
- Delivery is MANUAL: approve (second person, `approve_tax`) → transfer letter PDF (on demand, never
  stored; donee, EIN, receiving broker, DTC participant, masked accounts, lots) → sent → received
  (the date the donee's account received the shares = the gift date) → acknowledged. Reminders:
  Form 8283 above $500; a year-end to-do when a current-year gift is not received by
  `replication.gifts.year_end_receive_by` (12-15).
- Rebuy: none | same security (fresh basis; no wash-sale issue on a gain — stated) |
  replica-consistent (the account's normal proposal). Each is a rep5 proposal a person approves.

### The backtest and the hypothetical-performance gate (`services/backtest.py`)

Coverage first: trading days are the fund's price dates in the window; a security is covered with a
price on day 0 and on ≥ `replication.backtest.min_day_coverage` (95%) of days; the run is made only
when covered securities hold ≥ `min_weight_coverage` (95%) of every model version's weight —
otherwise stored `not_estimated` with the coverage and the missing list, no partial figures. History
comes through the layer from the ranked source with the most points (never combined). The engine
replays the model weights in effect at each rebalance (monthly | quarterly | annually | none),
fractional shares, price return, no trading costs; harvesting sells a lot whose loss meets rep6's
thresholds and buys it back after 31 days. Output: cumulative series for both, realised TE,
maximum drawdown, harvested losses (and their tax value with a profile), the fund's expense ratio
vs the strategy fee (shown, not deducted). Every result is `hypothetical = true` (a CHECK). Its
screen and PDF carry the approved policy's disclosure; with none approved: "no
hypothetical-performance policy approved — staff use only". A member envelope
(`GET /replication/member/backtests`) is empty unless the approved policy's audience is
staff_and_members, and then shows only the member's own households' backtests.

### Open for counsel and the CPA (shown on the screens, not decided by the code)

- The hypothetical-performance policy (SEC Marketing Rule) and who may see a backtest.
- AGI limits and the five-year carryover (estimates); whether the member itemizes; Form 8283;
  the donee's acceptance of in-kind securities; state deductibility.
- Factor-score data licences.
- Not built: reserving an open gift's lots against a rep5 sale.

## rep11 — note replication from the 424B2 terms (PHASE 3 BEGINS: structured notes, 2026-11-03)

Sprint 11 of 15 starts phase 3 (structured notes; rep12 = note-versus-replica across new issues).
Thesis (Joe): the public filing shows what a note really is. Hollisworks shows a member what
they own, what it is worth, and what a transparent replica would look like. Schema:
`docs/rep11_part1.sql` (applied). Service: `services/note_replication.py`. Routes:
`routers/replication_notes.py`. Verify: `apps/api/scripts/verify_rep11.py` (written, not yet run).

### Task 1 findings (live database, 2026-11-03)

- **1a "the" terms.** `portfolio.securities_global_note_terms` has 54 live rows, one per note,
  with no note holding two live rows. Final 48 / preliminary 6; the prompt said 47 / 7, and the
  live mix moves. The current terms of a note are its live rows ranked restated > final >
  preliminary, then the newest `valid_from` (`current_of`). **`routing_decision` is NULL on all
  54**, so STP routing never ran on them and "approved" cannot be read from routing. Needs
  review = `extraction_confidence` other than `high` (28 of 54), `routing_decision = 'queued'`, a
  used field whose `field_status` is `extraction_failed`, or a value on the wrong scale (below).
- **1a underlyings.** These come from `portfolio.securities_global_relationships`
  (`underlying_of`): 97 edges, 69 ambiguous (a known index proposed) and 28 unresolved.
  **Zero are resolved**, so no real note can be built until a Super Admin confirms its
  underlying in the underlying-resolution queue.
- **1a distribution and fees.** These are not on the terms row. They are only in
  `note_term_readings` (`distribution` [{name, role, fee_max_pct}], `agent_commission_pct`,
  `total_fees_pct`, `estimated_value_pct`, `price_to_public_pct`) for the same filing. **None
  of those readings belong to the 54 notes' filings today**, so every note shows "not in the
  extracted terms". The lookup is wired: a human reading first, then the newest; a model
  reading is labelled "not reviewed".
- **1c builder inputs.** These are the strike as a % of the start level, the ratio, expiry =
  the final valuation date, the outcome period = initial → final valuation date, and the
  underlying from the one resolved edge. The start level is the underlying's level quote **on
  the initial valuation date**; nothing is guessed.
- **1d holdings.** 0 live positions hold any of the 54 notes (positions → `assets.global_security_id`
  → `securities_global` → terms), and 0 assets are structured notes.
- **1e parent.** `option_packages` requires `replica_security_id` and `model_id` (NOT NULL), so a
  note replica is its own parent: `public.note_replicas` + `note_replica_legs`. These reuse
  rep8's `find_or_create_contract`, adviser quotes, `level_kind`, `PLeg` / `package_value` /
  `intrinsic` (the same payoff math) and `contract_out`.

### Verdict rules (`assess`, RULE_VERSION rep11.1; the platform table `portfolio.note_replicability`)

Each **not-replicable** feature found adds its own reason, and a note can carry several:

| code | feature | the plain reason |
|---|---|---|
| autocall | archetype autocallable, an autocall frequency, or an autocall barrier | early redemption depends on the path; static European options cannot stop on a call date |
| worst_of | basket_type worst_of | "Worst-of three underlyings — a replica would need correlation, which listed options cannot provide" (the count of edges) |
| basket | basket_type basket | an option on the basket's combined level; no listed option on that basket; per-component options pay more |
| decrement_index | is_decrement_index | a fixed daily deduction; no listed / FLEX options are written on it |
| contingent_coupon | coupon_barrier_pct present | a strip of digital options, one per observation date |
| memory_coupon | reading coupon_memory = true | a missed coupon is paid later if the underlying recovers (path) |
| daily_barrier | reading barrier_observation daily / continuous | a knock-in on the whole path |
| no_mapping | archetype other / not extracted | no payoff shape this build maps |

**Replicable** notes get one of four mappings:

| mapping | archetype | verdict | replica |
|---|---|---|---|
| buffer_package | buffered_note, single | static | underlying + long put 100% + short put at 100% − buffer (a floor: long put at the floor) + participation calls at 100% (p > 1 long p − 1; p < 1 short 1 − p) + short call ×p at 100% + cap ÷ p |
| principal_protected | principal_protected, single | static | Treasury zero repaying principal (face 100%, maturing at the final valuation) + the downside legs + long calls ×p at 100% + short calls ×p at 100% + cap ÷ p |
| digital_call_spread | digital, single | **approximate** | Treasury (face 100%) + the downside legs + a call spread long / short (digital ÷ ε)× at 100% / 100% + ε |
| reverse_convertible | reverse_convertible, single | static | Treasury (face 100% + coupon × tenor) + a short put at 100% (or where the buffer ends) |

- **Total return.** A note on a total-return index is **approximate**: listed options settle
  on the price index.
- **Protection semantics, stated for Joe to confirm.**
  - `buffer`: `protection_pct` is the first loss absorbed.
  - `floor`: `protection_pct` is the minimum repaid as a fraction of principal (1 =
    principal protected). This matches the principal-protected rows. A knock-in barrier
    labelled `floor` would be an extraction error.
  - `cap_pct` is the maximum return.
  - `participation_rate` is the upside multiple; absent means 1×.
  - `coupon_rate` is a fraction per year, paid at maturity in the replica.
- **Wrong-scale values are review flags, never guessed:**
  - a cap > 1 is probably the total payout;
  - a coupon > 1 is probably a percent;
  - a buffer outside 0–100% or a floor outside (0, 1];
  - a participation on a digital note.
- **Preliminary** terms are marked "preliminary — final terms may differ" and may be built.
  Terms that **need review** are marked and are never built.
- **The job** is `run_verdicts`, a platform job under `app.is_super_admin`. It runs as a step
  of the cron tick and from POST /replication/notes/verdicts/run. It writes a new row only
  when the terms fingerprint changes (the payoff fields, field_status, confidence, the
  features read, the edge count and RULE_VERSION). It closes the earlier row (`valid_to`),
  and closes the verdicts of terms that are no longer live. It is idempotent.

**The verdict counts over today's 54 notes** (the job, run once as `app_service`; a second run
wrote nothing):

- **static 11** (buffered single 9, principal-protected 2);
- **approximate 3** (digital single);
- **not replicable 40.** Reasons, overlapping: autocall 31, worst-of 22, contingent coupon 22,
  decrement index 5, basket 2, other 1.
- **Needing review:** 8 of the 14 replicable notes (5 static, 3 approximate). Review reasons
  across all 54 notes: confidence 28, extraction failed 8, coupon scale 6, cap scale 5, dates
  missing 2, digital participation 1, protection scale 1, protection missing 1.
- **Buildable today: 0**, because no underlying link is confirmed.

### Replica construction and the payoff

- **Build** is a POST that creates a DRAFT. It is refused (409, the reasons listed, **nothing
  written**) for a not-replicable note, terms that need review, terms that lack a payoff field,
  or an underlying that is not one confirmed edge.
- **Option legs** are FLEX European by default: cash-settled on an index and physical
  otherwise. Broad-based must be stated for an index (it decides Section 1256).
- **Contracts** come through rep8's `find_or_create_contract`, at strike level = start level ×
  strike % (cents, half-even), with expiry = the final valuation date. With no start-level
  quote, the strikes stay as percentages and the replica reads "not priced: the underlying's
  level on <initial date>". Pricing later sets the level on a draft.
- **Computed against stated.** `computed_terms` reads cap / buffer / floor / participation
  (and the digital amount or coupon) **back from the legs**, in exact Decimal, next to the
  note's stated values, and shows any difference.
- **Payoff.** Note value(s) = 1 + note_return(s − 1). Replica value(s) = Σ underlying ratio·s
  + Σ Treasury face + Σ ±ratio·intrinsic(K, s). Both are drawn on one grid from −100% to +100%
  (step `replication.notes.payoff_step_pct`, default 5%) plus every strike edge. It needs
  strikes only, so it is always available.
- **Approximation.** The digital is exact everywhere except underlying returns in [0, ε). There
  the replica pays less, by at most the digital amount at exactly 0%, falling linearly to
  nothing at ε. ε = `replication.notes.digital_spread_pct`, default 1%.
- **Legs are manual instructions.** Every leg is shown as a manual instruction ("Buy … — manual,
  through the custodian's options desk"). Nothing trades, and no execution instruction is
  written.
- **Maker-checker.** draft → pending_approval (a human task, `approve_replication`, never a
  maker or a pricer) → approved | back to draft with the note. An approved replica supersedes
  the earlier approved one (archived). **"Becomes a model / registry entry" means the approved
  replica is the firm's registry entry for that note** — the X-ray's "replicable alternative".
  It does **not** create a `portfolio.models` version: model versions are weight-based and
  cannot carry option legs at a ratio. Wiring an approved note replica into a model and
  account proposals is open (a rep12 candidate).

### Pricing rule (an ESTIMATE, per 100 of principal)

- **Quote kinds.** `adviser_quotes` gains `note_price` (% of par, on a structured note) and
  `treasury_yield` (% a year, annual compounding, on a fixed-income security). They are
  entered through rep8's POST /replication/options/quotes.
- **Underlying:** ratio × current level ÷ start level × 100.
- **Option:** ± ratio × premium ÷ start level × 100. Without a premium, the contract's **own**
  entered implied vol (% a year) is priced through Black–Scholes in Decimal, with no dividends
  and r = ln(1 + Treasury yield); the input is labelled.
- **Treasury:** face × 100 × (1 + y)^−(days ÷ 365), days from the yield quote's as-of to the
  leg's maturity.
- **Embedded fee** = note price − replica cost, to four decimals. Every input is listed with
  its quote id, source and as-of; the estimate's as-of is the oldest input.
- **Missing inputs.** With any input missing, the result is "not priced — enter a quote —
  missing: …", naming each gap.
- **Comparisons** are insert-only rows (`public.note_replica_comparisons`); a CHECK holds fee =
  note − cost.
- **SSVI is not used.** `services.pricing.ssvi_surface` calibrates from call / put price pairs
  (`min_quotes_per_slice` = 8 per expiry) and refuses maturities beyond 2.5 years. Notes run
  1–6 years, and adviser quotes are one contract at a time. Nothing is fitted or extrapolated.

### Member visibility

- **Always visible.** The verdict and the stated terms are factual and always shown to the
  household's own members: the X-ray marker, the note page.
- **Hypothetical figures.** Replica cost and embedded-fee figures follow rep10's
  hypothetical-performance policy. Staff see them: Super Admin, a staff role, or an org admin
  (`manage_org_settings`). A member sees them only under an approved policy whose audience is
  `staff_and_members`, with its disclosure. Otherwise the X-ray and the replica page carry
  "Estimate withheld — no hypothetical-performance policy approved — staff use only".
- **The X-ray marker.** Each held note shows its verdict, stated terms, distribution / fees
  (or "not in the extracted terms"), and "replicable alternative" linking the firm's approved
  replica. Without one it reads "no approved replica" or "not replicable with listed options".

### Screens

Replication → **Notes** (`/portfolio/replication/notes`, filter by verdict / archetype / issuer /
underlying, Recompute verdicts for `manage_replication`); a note's page (terms with the filing
link, verdict and reasons, payoff overlay, legs, replicas, builder, differences, counsel items);
a replica's page (`/portfolio/replication/notes/replicas/{id}` — legs as manual instructions,
computed vs stated, payoff, pricing, submit / approve / reject / archive); the X-ray's
**Structured notes** card. Controls from `lib/rep11.mjs`, failing closed.

### Open counsel / Joe items (on screen and here; never decided in code)

- **Tax.** The note (prepaid forward, or a contingent payment debt instrument) versus the
  replica (Section 1256 index options, 60/40, marked to market). The straddle question has
  been pending with counsel since rep8.
- **Marketing.** Whether showing members an embedded-fee estimate is hypothetical performance /
  marketing. The firm's policy (rep10) governs it, and counsel confirms.
- **Credit disclosure.** The issuer-credit disclosure wording.
- **Protection semantics.** The `floor` and `cap_pct` scale interpretations above. Joe confirms
  them against filings.
- **Before any real note can be built.** Its underlying edge must be confirmed, and its terms
  reviewed where flagged.

## rep12 — the note-versus-replica check across new issues (2026-11-04)

**The automatic check.** Every new or restated live note-terms version — written by the extraction's
one write path (`note_terms_extraction.store_extraction`) or a human correction — is checked for every
org that uses replication (a role holding view_replication; the org can turn it off). Per org: rep11's
verdict; a DRAFT replica when rep11's `buildable` holds (static / approximate, terms approved —
confidence high, routed 'stp', nothing failed or out of scale — and exactly one RESOLVED underlying);
the comparison when the note's price and every leg are quoted, else "not priced — enter a quote"
naming each gap. Idempotent by terms version (+ the underlying edges): nothing is re-checked unless
something changed. A sweep each scheduler tick catches anything the hook missed.

**No automatic confirmation, no automatic approval.** The check never confirms an underlying and
never submits or approves a replica. A draft it prepares has no maker; a person completes it, submits
it, and a second person approves it (rep11's maker-checker).

**The feed.** Replication → Notes → New issues (`/portfolio/replication/notes/new-issues`): newest
first by the 424B2 filing date (or, with no filing, the day the terms were first extracted); filters
by verdict, archetype, issuer, underlying, issue date and status; sort by the embedded-fee estimate
(priced only, highest first; unpriced last, each saying what it needs). Staff only (an internal role
holding view_replication); mark reviewed or dismissed (with a note) — recorded with the user.

**Alerts.** A system-opened human task to holders of view_replication (never a client role) when a
priced estimate is above the org's threshold — at the check or when a person prices it later — or a
watched issuer / underlying / archetype issues.

**The underlying queue** (`/portfolio/replication/notes/underlyings`, Super Admin): counts per state;
edges with ONE registry proposal confirm in one click or several at once — each its own
`confirm_resolution` call, recorded with the clicker; edges with no proposal open the manual match
(the pricing-admin confirm route with a named security or `create_new`). Every confirmation or
rejection re-checks the affected notes.

**Operator steps.** (1) Assign models and daily budgets to the note-extraction job (AI Models → Model
Jobs) — nothing is extracted until then. (2) Grant STP per filer you trust, or review queued terms:
an extracted note is "approved" only then. (3) Work the underlying queue. (4) Set the alert threshold
and watches on New issues. (5) Enter the note's price, the underlying's start and current level and
each option's premium (or implied vol) as quotes — the estimate appears only then.


## rep13 — the tax-aware long/short extension (PHASE 4 BEGINS, 2026-11-07)

Spec: "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22), "Long/short and options overlays". $100 of
capital holds $130 long and $30 short (150/50: $150 / $50). The shorts are the names the signal likes least.
Hollisworks has **no stock-selection model**: the signal is a sub-adviser's TARGET WEIGHTS, uploaded through
rep3's model management. Task 1 findings: `docs/REP13_DISCOVERY.md`. Schema: `docs/rep13_part1.sql` (APPLIED,
`scripts/_apply_rep13.py --verify`: SCHEMA OK). Services: `services/long_short_rules.py` (pure) and
`services/long_short.py`. Routes: `routers/long_short.py` (14, under `/api/v1/long-short/`). Screens: Account →
**Long/short** (`/portfolio/accounts/{id}/long-short`) and a long/short panel on the proposal page. Verify:
`apps/api/scripts/verify_rep13.py` (WRITTEN, NOT RUN).

### Eligibility (each refusal names the missing piece)

An account may go long/short only when all of these hold:

- Its custodian **declares** `margin`, `short_selling` AND `stock_borrow` in `custodian_capabilities`.
  `unknown` is not declared. The prompt's "short_sell" is rep2's existing key `short_selling`.
- Its effective authority is **managed at an integrated custodian** (rep7b's word is `managed`). read_only and
  advise refuse.
- The member's suitability profile holds an **APPROVED margin agreement**: `public.suitability_records`, with its
  date and a document, keyed by the account's household, else its primary entity. A different holder of
  `approve_proposals` approves it. A removal takes effect at once, with a reason, and is audited.
- The account is open and not held away.

**No custodian qualifies today.** Altruist and the manual route are `unknown` on all three keys. Schwab, Fidelity,
Pershing and IBKR have no rows. The path is proven on fixture custodians.

### Models

`portfolio.models.kind` is `long_only` (the default) or `long_short`. A long/short model records:

- its gross long and gross short: 130/30 or 150/50, or a custom pair with 0 < short ≤ 50 % and long = 1 + short,
  so net is always 100 %;
- the signal source (the sub-adviser) and the as-of of its weights;
- `short_exclusions`, the names the model never shorts.

It must be an upload, and it is named **"Holdings of …"**. rep3 had reserved that prefix for fund replicas; the
CHECK now admits it for long/short models and requires it on them.

Upload, edit and submit accept negative weights only on such a model. A trigger enforces the same in the
database. The validation:

- longs sum to the gross long and shorts to the gross short (± the org tolerance);
- no name is both long and short;
- every short is a listed equity in the master (not a fund or ETF, not delisted) and is resolved;
- no short is on the model's never-short list.

The upload log shows the kind. A long/short model is never published to the library and is never a sleeve of a
composite.

### Account settings

`public.long_short_settings`, on an eligible account only:

- the extension;
- the tracking-error limit;
- the maximum short per name (fraction of capital, ≤ 50 %);
- the borrow-cost ceiling (bps a year);
- exclusions (names never shorted).

A draft can be saved, then saved as a version. Rows are written once and only ever closed, so the history is
kept. Every write is audited. A long/short model can be joined only with saved settings of the same extension.

### Proposals (`long_short_rules.build_books`)

A long/short proposal is rep5's proposal row plus one `public.long_short_proposals` row. It is made by a PERSON:
Rebalance now, or the join, which no longer auto-submits. The drift, model-change and harvest sweeps skip
long/short accounts.

**Capital** = long value − short value + cash. Each name's signed weight becomes a long target and a short target,
sized in whole shares unless fractional shares are declared.

**The long book** buys up to its target and sells down to it with rep5's lot relief. A long book never sells
shares it does not hold.

**The short book** uses **SELL SHORT** to open and **BUY TO COVER** to close (oldest lot first, or the lots a
harvest names).

**Settings act on every short, in this order:**

1. An excluded name is skipped, with the reason.
2. A §1259 block makes the line BLOCKED. It is recorded and never routed.
3. A name with no borrow rate stays a line flagged **"borrow rate needed"**, and approval is refused.
4. A name whose rate is above the ceiling is skipped, with the rate stated.
5. A name caught by the short-sale wash rule is deferred.
6. A short above the maximum per name is capped.

A short that is not placed is not re-allocated. Buys are scaled to the cash the sales and the placed shorts
raise, and the resulting net exposure is stated.

**Borrow.** Rates are **entered**; no live borrow feed exists. They live in `public.borrow_rates`:

- Super Admin enters platform rates; an org enters its own rates, and those are read first;
- every rate is per security, with an as-of and a source;
- a short rebate is optional.

Each short line carries its estimated annual fee and rebate. The proposal's cost comparison has these lines:

- the trading cost (org bps);
- **"Borrow cost (long/short only) — estimated"**, over the short book after the proposal;
- **"Short rebate"**;
- the overlay fee (not set — an open decision);
- the net.

**Tracking error** vs the model, from signed weights: sqrt(252 · aᵀΣa) over daily closes from the security-data
layer (prices1). Every name needs a series; otherwise it reads "needs prices — N of M names …" and is never
invented. An estimate above the account's limit blocks approval.

### Tax

- **Short-term rule.** A short's gain or loss is **short-term whatever the holding period** (IRC §1233(a)–(b)).
  `long_short_rules.short_close_term` is the one place this is decided.
- **The §1259 constructive-sale HARD BLOCK.** No proposal or instruction may short a security that ANY household
  account holds long at an unrealized gain. That includes the spouse, IRAs and readable held-away accounts; every
  rep7b authority allows reading. The same applies to a substantially identical security, which here means:
  - the same security;
  - another share class of the same issuer (the same SEC CIK reference attribute, both equities);
  - the same canonical security;
  - an active substitution denial pair.

  A lot whose gain cannot be shown is treated as a gain (no cost basis or no price). A lot at a loss never blocks.
  The block names the account and the lot.

  It is enforced at four points: at generation, in the approval precheck (re-run on today's household), before
  routing, and in `execution.create_instructions` — the one chokepoint, so a manual instruction is caught too.
  There is **no override** in this sprint.
- **Two-sided harvest.** It runs inside the long/short proposal, on the link's rep6 harvest settings and
  thresholds.
  - Short book: a short at a net loss is covered and re-established in an approved, clear substitute. The
    substitute must not be identical, must not be excluded, must be shortable, must have a rate under the ceiling,
    must have no §1259 block, and must have no wash window.
  - Long book: a long at a net loss is sold, and the substitute is bought through rep6's `evaluate_buy`.
- **The short-sale wash rule** (IRC §1091(e)). A new short of a name the household covered at a loss within 30
  days is deferred to day 31.
- **Estimates.** The tax estimate (`GET /long-short/accounts/{id}/tax-estimate`) and the proposal show the
  harvest value at the profile's rates, labelled "estimate". Without a profile they read "not estimated — no tax
  profile".

### Disclosures and approval

Every long/short proposal shows:

- the six risks (AQR's list);
- the borrow estimate;
- the margin requirement as DECLARED by the custodian (`margin_initial_pct` / `margin_maintenance_pct`), else
  "custodian to confirm";
- a member-facing summary.

The maker must acknowledge every item before submitting; the acknowledgement is recorded once. A different person
approves. Approval is refused when:

- any eligibility piece has lapsed since the build (for example, the margin agreement was removed);
- any blocking code is present;
- a short line still has no rate.

### Execution

The side vocabulary gains `sell_short` / `buy_to_cover`. This covers proposal trades (with `short_leg`),
instructions (with `short_terms`), blocks and the typed `TradeLine`. A short or a cover is always a share
quantity. The order sheet prints "SELL SHORT" / "BUY TO COVER" plainly, with a position-effect row.

A custodian adapter whose capability row does not declare `short_selling` **refuses a short at submit**
(`TradeAdapter.refuse_undeclared_shorts`, 409). **The manual route is always offered**:
`POST /long-short/proposals/{id}/execute {"route": "manual"}`, or `route_trade_list(manual=True)`, produces
instructions a person carries out with the printed sheet. Eligibility and §1259 still apply on the manual route.

Fills give the **short position**, derived from SELL SHORT / BUY TO COVER fills (`long_short.short_book`): a
negative quantity, each opening fill a lot. No custodian feeds short positions yet.

### Open decisions for Joe (shown here; never decided in code)

1. **Signal source.** Sub-adviser target weights are assumed. Our own factor signal is NOT built. Which
   sub-adviser?
2. **Which custodian first.** It has to declare margin, short selling and stock borrow with evidence. Altruist has
   not said it supports short selling.
3. **Default limits for 2nd Act's members.** Today there are no defaults; every account's settings are entered
   explicitly. This covers the tracking-error limit, the maximum short per name and the borrow ceiling.
4. **The overlay's own bps.** The org setting `replication.long_short.overlay_bps` is shown "not set" and charged
   nowhere.
5. **Counsel / CPA.** The §1259 "substantially identical" rule (CIK share classes) and the conservative treatment
   of unknown-cost lots. Whether loss-bank ingestion should take short closes: it does not yet, and they are
   reported by the tax estimate instead.

### rep13 operator steps

1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep13.py`. It runs `node --test`,
   `npm run build` and the `verify_rep6.py` regression itself.
2. Add rep13's merge sha to `_verify_self.SPRINT_MERGES` when merged.
3. Refresh `docs/schema_snapshot.sql` (refresh-schema).
4. Redeploy the API and the scheduler cron service. The sweeps now skip long/short accounts.

## rep14 — the Section 351 exit (planning side, manual-first, 2026-11-08)

When harvesting has run dry, appreciated replica accounts can be pooled into a NEW ETF without a taxable sale. rep14
builds the planning side end to end: who is a candidate, whether each account passes the diversification test (and
a fix-up proposal if not), the pool's control test, the lot-level carryover basis record, consents and the project
tracker, and the in-kind transfer instructions. **Nothing executes automatically**: no trade, no sale, no transfer
leaves the system; every transfer is a printed instruction a person sends, and a person records the custodian's
confirmation. Task 1 findings: `docs/REP14_DISCOVERY.md`. Schema: `docs/rep14_part1.sql` (APPLIED, writes no rows).
Code: `services/section351_rules.py` (pure), `services/section351.py`, `routers/section351.py` (38 routes).

### Roles (Joe, 2026-11-07 — Alpha Architect / ETF Architect's 351 Education Center, Cambria's 351 program)

- **Sponsor.** A third-party ETF sponsor runs the new fund; 2nd Act is not an ETF issuer. A plan names its sponsor
  from a **sponsor profile** (`public.section351_sponsor_profiles`: name, adviser / distributor, contact, launch
  windows, and the eligibility rules below), edited by an org admin (`manage_org_settings`). **Every edit is a new
  version** (valid axis: the live version is closed, never changed); a plan records the exact version row and its
  number. The target ETF and the sponsor's trust are recorded as text (an open question until the sponsor is
  chosen).
- **2nd Act** is the members' adviser: it identifies candidates, runs the tests, collects consents and basis
  records, and instructs the custodian. The sponsor runs the fund and its own final compliance review.

### The rules and their citations (every result labelled "estimate — confirm with counsel")

| Rule | Citation | rep14's reading (flagged for counsel) |
|---|---|---|
| Investment-company exception: tax-free only if EACH transferor's contribution is already diversified | IRC §351(e)(1); Treas. Reg. §1.351-1(c)(1), (c)(6)(i) applying §368(a)(2)(F)(ii) | Tested per account (one account = one transferor's contribution); two accounts of one person in one plan are tested separately |
| ≤ 25 % of contributed value in any one issuer; ≤ 50 % in five or fewer issuers | §368(a)(2)(F)(ii) | Compared EXACTLY — 25.00 % / 50.00 % pass, 25.01 % / 50.01 % fail |
| Government securities | Treas. Reg. §1.351-1(c)(6)(i) | In the total (denominator), never an issuer (numerator) — identified by `section351_asset_classes` 'government' (the master has no such field) |
| Cash and cash items | §368(a)(2)(F)(iv) | Excluded from the total; the sponsor's default cash rule keeps cash in the account |
| Fund (RIC) stock looked through to its assets | §368(a)(2)(F) (RIC / REIT look-through — clause to confirm) | A name held directly AND inside an ETF is one issuer (summed); weights normalised to the snapshot's total; holdings as-of shown; no holdings → FAILS CLOSED "holdings needed"; an unresolved constituent → fails closed |
| Share classes / controlled groups are one issuer | §368(a)(2)(F)(ii) (§1563(a) controlled group) | Share classes grouped by SEC CIK; controlled groups NOT grouped (no data) — open item |
| Control: transferors own ≥ 80 % of the ETF right after | §351(a), §368(c) | Contributors' share = Σ(contributed − cash to them) ÷ (that + non-contributor seed); 80.00 % passes, 79.99 % fails |
| Carryover basis and tacked holding period | §358(a)(1) (shares), §362(a) / §1223(2) (fund), §1223(1) (holding period) | Per lot: the ETF shares split by each lot's FMV share; each slice keeps the lot's basis and acquisition date (Rev. Rul. 85-164 split holding periods) |
| Boot is taxable | §351(b) | Cash to a member is allocated to lots by FMV and recognises gain up to each lot's gain (Rev. Rul. 68-55); no loss recognised; the plan receives ETF shares only by default, any cash leg shown as taxable |

### Eligibility (sponsor-profile defaults — editable per profile)

- **Owners:** U.S. persons only (confirmed by the adviser per account, with source and date); individual, joint
  (an individual registration with two or more `account_owners`), trust and S-corp accounts eligible;
  partnerships / LLCs "needs sponsor review" (ready only after the sponsor's recorded acceptance); C-corps and
  non-U.S. persons refused. rep14 also refuses retirement / tax-exempt accounts (no gain to defer — **our rule**).
- **Assets excluded and listed:** restricted or illiquid securities, derivatives (option legs; rep13 shorts are
  never lots), crypto (`asset_type 'digital_asset'`), physical commodities, commodity pools, grantor trusts,
  partnership interests; holdings not in the security master. Cash per the sponsor's rule.
- **Accounts:** a replica account (an active model link) with lot-level data for every holding — each lot with its
  basis AND acquisition date (average cost is not enough) — not held-away, authority **managed** (rep7b's word).
  Today **no live account qualifies** (all 55 live open lots lack an acquisition date; no account follows a model).

### "Harvesting has run dry" (thresholds per org, versioned — `public.section351_settings`)

Run dry when ALL three hold: harvestable loss remaining ≤ 2 % of the account; unrealised gain ≥ 25 % of the
account; the model link ≥ 3 years old (unknown → not met). Each check is shown next to its threshold. Benefit decay
is shown next to year 1: losses harvested in the link's first tax year vs this year (rep6 loss bank), an estimate.

### The fix-up proposal

A failing account gets an optional proposal that makes it pass with the least estimated tax: greedy, deterministic —
while the test fails, sell from the binding issuer (the largest when over 25 %, else the five largest) the lot that
costs least tax per dollar raised (rep6 `tax_per_dollar` at the profile's combined rates; no profile → least gain
per dollar, tax "not estimated"), only the whole shares that step needs, the proceeds into the account's model names
outside the binding set. It is a NORMAL rep5 proposal (trigger manual, a person is the maker, lot method 'specific'
with the picked lots) and goes through rep5's maker-checker. The stored trades are compared with the plan and the
test is re-run on the proposal's own trades; the tax cost shown is the ENGINE's (the proposal's ST / LT gains at the
profile's rates).

### Operator flow

1. Org admin: a sponsor profile (and, if wanted, the run-dry thresholds). Super Admin / adviser: classify the
   government and other special securities the master does not mark (`section351_asset_classes`).
2. Disclosure library: counsel reviews `section351.exit`; a person uploads the draft, a second Super Admin publishes
   it (until then no plan can be ready — the disclosure is never text in code).
3. Replication → Section 351 → Candidates: pick run-dry accounts. Test each at a valuation date; run the fix-up if
   one fails (approve it through rep5, execute, then re-test).
4. Create the plan (valuation date, target ETF, sponsor, seed capital): its eight-step tracker opens as human tasks
   (counsel engaged → sponsor / trust selected → custodian confirms in-kind transfer → valuation date set → consents →
   basis record delivered → transfer → confirmation), due dates over ~8 months, editable.
5. Per account: U.S.-person confirmation, the member's written consent (a vault document, date, signer), the
   member's tax adviser's confirmation (recorded, never inferred), acknowledge the in-force `section351.exit`
   version, any cash leg (boot — taxable).
6. Mark ready (every account re-tested; every gate shown when refused) → export the basis record (CSV + PDF in the
   vault, a new version each time, totals reconcile to the plan to the cent) → submit → a different person approves
   (the precheck re-runs every gate: a newly published disclosure needs re-acknowledgement).
7. Approval issues one **in-kind transfer out** instruction per lot (`public.section351_transfers`). The adapter
   route refuses unless the custodian declares `in_kind_transfers` (none does; the manual route is always offered):
   print the instruction sheet, send it, mark it sent.
8. When the custodian confirms: record the received date, the ETF (from the master) and the shares. The lots close
   BY TRANSFER (`realized_gain_loss` NULL — nothing realised; the loss bank and realized YTD never see it), the
   position is restated to what is left, and the ETF position gets one lot per contributed lot with its carryover
   basis and tacked acquisition date.

### Open for counsel (shown on the screens; never decided in code)

1. The default `section351.exit` disclosure text (`docs/disclosures/defaults/section351.exit.md`).
2. Fees: the sponsor's and any adviser fee on the new ETF are not modelled.
3. Each reading in the table above — especially the look-through clause, government securities in the denominator,
   per-transferor testing, controlled groups, the per-lot share split and asset-by-asset boot.
4. Refusing retirement / tax-exempt accounts (our rule).

### rep14 operator steps

1. `doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_rep14.py` (it runs its node test file,
   `npm run build` and the verify_rep13.py regression itself).
2. Add rep14's merge sha to `_verify_self.SPRINT_MERGES` when merged.
3. Refresh `docs/schema_snapshot.sql`.
4. Redeploy the API.

## rep15 — platform-published models and tenant subscriptions (PHASE 4 ENDS, 2026-11-08)

The Hollisworks platform org can act as a **sub-adviser**: it publishes models from its own rep3 registry to a
catalog; tenant orgs subscribe, use a subscribed model's versions in their accounts, and owe a **model-provider fee**.
Everything sits behind ONE regulatory gate, **OFF** today. Code: `services/model_provider.py`,
`routers/model_provider.py` (28 routes), `docs/rep15_part1.sql` (APPLIED). Task 1: `docs/REP15_DISCOVERY.md`.

### The gate and the provider record
- `portfolio.model_providers` — one live record per provider org: legal entity name, SEC file number (`80[12]-…`) or
  CRD number, registration status (not_registered / pending / registered / withdrawn), effective date, ledger org.
  Super Admin only. A provider org is the Hollisworks platform org or an is_test org (trigger).
- `portfolio.model_publishing_gates` — no row = OFF. OPENING needs Super Admin (the `app.is_super_admin` GUC), a
  COMPLETE record (registered, named, numbered, effective on or before today) and the provider org's OWN published
  ADV Part 2A (library key `model_provider.adv_2a`, org scope, in force). The service names every missing item; the
  trigger re-checks all three in the database. Every open / close / refusal is a `model_provider_events` row + an
  `audit_log` row. Opening provisions the fee's ledger (below).
- While OFF every publish, subscribe, acknowledge, adopt, join (of a provided model), copy, refresh, accrual and
  invoice answers **409 "model publishing is not enabled — a registered adviser entity is required"**, and the
  catalog is invisible to tenants (RLS). The database refuses the same writes (catalog inserts / publication,
  subscription requests / activation, accruals, invoices all check `model_publishing_open`).
- A fixture provider can never serve a real tenant and vice versa (`provider_tenant_allowed`: is_test parity).
- rep3's platform library shelf (ungated until now) is gated for REAL orgs the same way: publishing to it needs the
  real platform org's gate unless the publisher is a fixture user; loading from it needs the gate unless the org is a
  fixture org.

### Publishing (provider org, maker-checker)
1. A provider model is marked **publishable** (`catalog_models`: default fee bps, construction notes). Long/short
   models are refused (their short book is not in the version's positions — not built).
2. One of its **approved** rep3 versions is **requested** for the catalog — the maker — with: name ("Holdings of …";
   never the index's own name — service + CHECK), description ("replicates the holdings of …" — service + CHECK),
   kind, construction notes, holdings as-of, fee bps. A `catalog_version_publish` task opens.
3. A **different** Super Admin publishes it — never the requester nor anyone in the rep3 version's `maker_ids`
   (service, precheck, CHECK `catalog_versions_maker_checker_chk`).
4. A published version is **immutable** (trigger: only published → retired). A retired version stays readable.
5. The catalog row carries a frozen snapshot of the **target weights** (look-through, exact Decimal; security id,
   ticker, name, weight) and its SHA-256 — never the fund's holdings file, an upload, or a provider registry row.

### Subscribing (tenant → platform, maker-checker across the two orgs)
1. Replication → **Model catalog** lists published provider models (name, "Provided by <legal name>", kind,
   versions, fee).
2. An org admin acknowledges the `model_provider.subadvisory_agreement` version **in force for that tenant** (the
   resolver: a tenant-specific org version overrides the platform default) — by version and hash.
3. The org admin **requests** (the maker); refused without the acknowledgement of the version in force.
4. A `provided_subscription_approval` task opens in the PROVIDER org (no maker there; the requester is on the row);
   a platform **Super Admin** who is not the requester approves (CHECK `provider_subscriptions_maker_checker_chk`),
   after the agreement in force for the tenant is re-checked against the one acknowledged (a newer agreement →
   refused; the tenant re-acknowledges and requests again).
5. Statuses: requested → active ⇄ suspended → ended (or rejected), each dated (`requested_at`, `started_on`,
   `suspended_at`, `ended_on`). Suspend / resume: platform Super Admin; end: either side. Both orgs log every change.

### Using a subscribed model (tenant)
- Approval creates a **read-only mirror** in the tenant's registry (`portfolio.models` source `provided`, "Provided
  by …", named "Holdings of …"): its versions come only from the catalog (trigger `provided_model_guard`: origin
  `subscription`, positions only through the import path's `models.provided_import` GUC); rep3 refuses drafts,
  edits, sleeves and renames. Its versions join accounts like the tenant's own (rep5).
- Every published version arrives as a mirror **draft** + a `provided_version_available` task carrying the exact
  weight diff (before / after / change per security).
- **Adopting** is the tenant's maker-checker decision: a holder of manage_models submits the draft (rep3's submit —
  the maker), a different holder of approve_models approves it (rep3's approval task), and rep5 makes the rebalance
  PROPOSALS. The org setting `replication.provided_models.pre_approve_adoption` (default **off**; absent = off) makes
  the person who recorded the setting the standing approver.
- **Copy** makes a tenant-owned model (source `provided_copy`, "Holdings of …", `copied_from_catalog_version_id`) with
  no subscription link — so no fee; it goes through rep3's own approval.
- **Suspending / ending** stops adoptions, joins, copies and imports; accounts already on the model keep their link
  and version, and each gets a `provided_model_move_account` task.
- Every proposal on a provided model carries `provider_notice`: the provider, "provided by a sub-adviser", and the
  library notice `model_provider.member_notice` (a NEW platform default, "have counsel review", `{provider}` filled).
  The tenant's own ADV disclosure of the sub-adviser relationship is an **operator item, not automated**.

### Cross-org isolation
- The provider reads, per subscribing tenant, ONLY: subscription status and dates
  (`portfolio.provider_subscription_status`) and the count of accounts and total model assets per catalog version
  (`portfolio.provider_model_usage`) — SECURITY DEFINER, answering only for the caller's own provider org. Never a
  member, account, position, household or person.
- A tenant never sees another tenant's subscription, events, accruals or invoices (RLS, org_id).
- Tenants never read the provider's registry rows (no rep3 carve-out is used).

### The model-provider fee and the GL
- **Asset definition (one place: `provider_model_usage`).** On day d, for each tenant account linked to the
  subscription's mirror on d: the sum of `market_value` of its live position rows, each asset at its latest
  `as_of_date` ≤ d (cash positions included; `account_balances_daily` cash excluded). The catalog version on d is the
  mirror version live (approved by the end of d).
- **Accrual (daily, valid axis).** `daily_fee = round(model_assets × bps / (10 000 × 365), 10)` — a CHECK holds the
  formula. A changed day is restated (closed + successor with `supersedes_id`), never edited.
- **Invoice.** Per subscription per period, one **"Model-provider fee"** line per catalog version (a CHECK holds the
  label): `amount = round(Σ daily model assets × bps / (10 000 × 365), 2)` = bps × the AVERAGE daily model assets over
  the period's calendar days × days / 365.
- **GL.** Posted through `services.ledger.posting` (`build_entry_on_conn` + `post_on_conn`) in the provider record's
  ledger org: book `MODEL_PROVIDER`, template `MODEL_PROVIDER_FEE` — **Dr 1230 Model-Provider Fee Receivable** (owed
  by the tenant) / **Cr 4800 Model-Provider Fee Revenue**; `source_event_id` = the invoice id.
- **Correction = reversal.** `fn_reverse_journal_entry` posts the mirrored entry; a reversal invoice (with mirrored
  lines) records it; the original invoice, lines and entry never change. Re-accrue, then re-invoice.
- The fee is not wired to the nightly tick (nothing accrues while the gate is OFF) — an operator item.

### Operator / Joe items
1. **The registered adviser entity** — a structuring decision (Hollisworks the software company is not an RIA):
   legal name, SEC file / CRD number, effective date on the provider record.
2. **The ADV Part 2A** — a library file (`model_provider.adv_2a`) at the provider org's scope, published by a second
   Super Admin.
3. **The sub-advisory agreement text** (`model_provider.subadvisory_agreement`) — counsel's; a platform default and
   any tenant-specific version. No text ships in code.
4. **The fee bps** per published model.
5. **Which org's books carry the revenue** (provider record `ledger_org_id`; the provider org by default — the spec
   says the fee is owed to 2nd Act). Changing it is a deliberate operator step (no API takes an org id).
6. The member notice default (`docs/disclosures/defaults/model_provider.member_notice.md`) — counsel reviews, then
   `load_disclosure_defaults.py` loads it.
7. Super Admins who act for the platform must be users of the Hollisworks org (the provider org is always the
   caller's own org).

## cpsim03 — the simulator route beside manual (2026-11-11)

An approved proposal's instructions (and any instruction routed from the instruction screen) take the
**custodian API route** only for an account of a SIMULATED Altruist-compatible custodian that meets every condition
(crosswalked to the simulator and never to real Altruist; the org allowed, pointed and connected; the demo-household
rule; integrated + managed; the simulator's `equity_trading_api` = yes) — and only for buy / sell at market or a
limit. Everything else is the manual route exactly as before, and the reason is shown on the instruction page.

Either way **a person approves, sends and imports**: the draft is approved through the same maker-checker task;
**Send to custodian (simulated)** is a person's action (manage_execution); **Import fills** books each execution through
the same `enter_fill` with that person as the one who entered it; **Reconcile** (custodian data) matches the fills to
the transactions the next sync brings in. The manual route (Mark sent, the order execution sheet) stays offered. The
real Altruist route is unchanged: manual (no published orders contract; `equity_trading_api` 'unknown'). Blocks and
the FIX router are unchanged (manual / not connected). Notes: `docs/cpsim/CPSIM03_NOTES.md`.

## cpsim05 — the simulated FIX route beside manual (2026-11-11)

For an account at the SIMULATED custodian (every cpsim03 D2 condition), the Altruist adapter now routes the trade list
`fix_sim`: the same instructions, sent as FIX 4.4 messages over HTTP to the simulated FIX counterparty. Joe's rule
(2026-11-11): the proposal's own maker-checker IS the approval, so once a human-made proposal is approved its trades
are sent, filled, synced and reconciled **automatically** (an explicit approval-of-record event names the checker).
A system proposal keeps rep2's second-person approval, and then goes automatic. A block may take `fix_sim` only when
every member is simulated (its own approval is the approval). Manual is still the default and always offered. Real
custodians are unchanged (manual; the real FIX router is not connected). The proposal page shows the simulated
execution's realized ST / LT and unrealized P/L from the tax engine (SIMULATED). See `docs/cpsim/CPSIM05_NOTES.md`.

