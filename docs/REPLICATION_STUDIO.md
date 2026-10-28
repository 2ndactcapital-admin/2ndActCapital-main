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
