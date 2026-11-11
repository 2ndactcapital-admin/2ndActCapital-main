# ALTREAD1 — the Altruist READ client on Altruist's PUBLISHED Open API

Branch `altread1-work`, 2026-11-11. Source of truth for the read path: developer.altruist.com/docs, read
2026-11-11 through the operator's browser (the sprint has no network — the brief's "CONFIRMED REAL FACTS" are the
source; nothing below was re-derived). Read path only: no Realtime API, no trading, no money movement.

Status: see `docs/PROJECT_STATUS.md` (ALTREAD1). Contract: `docs/cpsim/ALTRUIST_CONTRACT.md` (rewritten for the read
path). Simulator operator guide: `docs/cpsim/CPSIM_ADMIN.md` §14.

---

## 1. Task 1 — discovery findings

### 1a. Prerequisites

- `git log main` contains `9866144 merge cpsim04` — cpsim04 is merged. Proceeding.
- `VERIFY_REGRESSIONS` fast mode is present: `apps/api/scripts/_verify_total.py:177-220` (unset / `fast` → a nested
  verify runs `--self-checks-only`; `full` → the old full run; `scripts/run_regressions.sh` exports `full`).

### 1b. The client as built (every item confirmed with file:line, before this sprint's changes)

| Item | Where | As built | Documented (developer.altruist.com) |
|---|---|---|---|
| authorize path | `services/altruist_oauth.py:92-95` `_OAUTH_PATHS["authorize"]` | `/oauth/authorize` on the login host | `/api/oauth2/authorize` on the login host (docs/open-api-authentication) |
| scope | `altruist_oauth.py:389` `build_authorize_url(scope="accounts:read")`; the router passes none (`routers/altruist_connection.py:225, 238`) | `accounts:read` | `openid` |
| authorize params | `altruist_oauth.py:401-407` | response_type, client_id, redirect_uri, state, scope | client_id, scope, redirect_uri, state (response_type kept — standard and harmless; recorded as not shown on the page) |
| token URL | `altruist_oauth.py:433-434` (exchange), `:477-478` (refresh) | login host + `/oauth/token` | **API host** + `/oauth2/token` (api_base already ends in `/altruist-open-api`) |
| token body | `altruist_oauth.py:440-446`, `:484-489` | no `token_type` | `token_type=id` on both grants |
| refresh lifetime | `altruist_oauth.py:98` `REFRESH_TOKEN_LIFETIME = 30 days` — **never read anywhere** | 30 days, unused | **28 days**; nothing flagged an expired refresh token |
| resource paths | `altruist_oauth.py:795-800` `_API_PATHS` | households `/api/v2/households`; accounts `/v2/accounts`; positions `/v2/positions?account_id=`; transactions `/v2/transactions?account_id=` | households `/api/v2/households` (endpoints index; the households page itself shows `/v2/households` — portal inconsistent); accounts `/v2/accounts?household_id=`; positions `/v2/accounts/{account_id}/positions/`; cost basis `/v2/accounts/{account_id}/cost-basis/`; transactions `/v2/accounts/{account_id}/transactions` |
| request id | `altruist_oauth.py:916-931` | none | `X-API-Request-ID` (optional) |
| paging | `altruist_oauth.py:909` "one request (no pagination)" | one request per resource | `page` (default 0) + `page_size` (default 100; max 1000, transactions 500); `paging.total_pages` |
| envelope | `altruist_oauth.py:803-820` `_extract_list(body, key, "data", "results")` | first of three keys that is a LIST | `{"paging": {...}, "data": {"<key>": [...]}}` — `data` is an OBJECT, so **every documented response raised `AltruistOAuthError`** |
| transactions window | `altruist_oauth.py:1017-1033` | no `date_from` / `date_to` | default = the past month → a sync would only ever read one month |
| transaction date | `services/altruist_positions_sync.py:428-433` + normalizer `altruist_oauth.py:877` | `trade_date` or `date`, `date.fromisoformat` | `transaction_date`, `settle_date`, formatted `YYYY/MM/DD` → **every documented transaction was a per-row error** |
| fees | `altruist_positions_sync.py:449`, normalizer `:882` | `fees` | `transaction_fees` + `broker_fees` + `other_fees` |
| type map | `altruist_positions_sync.py:130-150` | lower-cased lookup (BUY → buy works) | `transaction_type` (+ `transaction_subtype`) |
| position fields | normalizer `altruist_oauth.py:839-859` | symbol / description / security_type / quantity / market_value / currency_code via aliases | compatible (as the brief said); `long_short_indicator`, `as_of_date` now read |
| position rule | `altruist_positions_sync.py:416-426` | a transaction needs a current position for its asset | unchanged |
| account crosswalk | `services/altruist_identity.py:263-264` | by `id` through `portfolio.external_references` | unchanged; `account_balance` was not read |

Callers of the internals (every one updated or proven unaffected):

- `_OAUTH_PATHS` — only `altruist_oauth.py` itself.
- `_API_PATHS` / `_extract_list` / `fetch_resource` — `altruist_oauth._call`, `services/altruist_conformance.py`
  (`both()`), `scripts/verify_cpsim02.py` (direct `fetch_resource` calls — signature kept; it now pages).
- `build_authorize_url` / `exchange_code_for_tokens` / `refresh_access_token` — `routers/altruist_connection.py:225,
  238, 312-315`, `altruist_conformance.py`, `ensure_fresh_token`, verify_cpsim02/03/04, verify_altruist_sprint4/5
  (monkeypatch the module attribute — signatures kept), verify_rep2 (reads the scope default as EVIDENCE; see §5).
- `call_positions` / `call_transactions` — `altruist_positions_sync.py:82` (bare imports, monkeypatched by
  verify_altruist_sprint5/6 with `(conn, *, org_id, environment, account_id, transport=None, timeout=15.0)` — kept
  call-compatible: the sync passes the date window only to `call_transactions_window`, a new function).
- The router sends `scope` = the default and `redirect_uri` = `ALTRUIST_REDIRECT_URI` (https enforced,
  `altruist_oauth.py:346-358`); the simulator connect reads the code from the 302 Location.

### 1c. Lots, balance, reversals

**Lots.** The lot construct is `portfolio.tax_lots` (position_id NOT NULL, opening/closing transaction, acquired_date,
quantity, original_cost, adjusted_cost, term, status open/closed, **source_system**, external_ref; valid + system axes).
It CAN carry a source — held-away imports already write `source_system` (`services/held_away.py:1219`). But it is NOT a
safe home for a custodian lot of an account Hollisworks already computes lots for:

- it is read with NO source filter by eight services (execution `_LOT_SQL` `execution.py:222`, held_away, loss_bank,
  options_sleeve, options_tax_gate, rebalance_proposals, section351, wash_sale) — a custodian lot written there would be
  read by every one of them immediately, double-counting the account's holdings, i.e. the default would change;
- three live FKs point at its id (`execution_instruction_lots`, `gift_lots`, `section351_transfers`) and its lifecycle
  (opening / closing transaction, realized gain) is Hollisworks-computed — a custodian snapshot has neither.

→ D4's "else" branch: the smallest new table beside it, `portfolio.custodian_tax_lots`.
The tax engine's ONE lot chokepoint is `services/account_models.load_holdings` → `services/execution.account_lots`
(harvest, rebalance / drift, x-ray); `lot_method.lot_options` (the specific-lot picker) also reads `account_lots`.

**Balance.** `public.account_balances_daily` exists, but it is the TREASURY's cash source: `account_models.py:330`,
`cash_instruments.py:405`, `cash_earnings.py:164` read its latest row (ordered by date, is_final, is_billing_source,
source_system). A custodian row written there would become the latest row and change the treasury cash reading
(its cash_value would be 0) — D3 forbids changing treasury. `public.accounts` is bitemporal (an in-place update breaks
Rule 3). → the smallest new table: `public.account_custodian_balances`.

**Reversals.** `portfolio.transactions` is append-only; a correction closes the current row (`valid_to = now()`) and
records a successor through `related_transaction_id` (`services/portfolio_transactions.py:49-60, 796`). There is no
reversal column, and the documented feed carries `cancellation_reversal_flag` but NO reference to the transaction a
reversal reverses.

### 1d. Simulator + conformance (cpsim02–04) that change

- `cpsim/app.py:163-272` — the read routes at the client's ASSUMED paths, flat `{"<kind>": [...], "as_of"}` envelope
  (`:243`), ISO dates (`cpsim/book.py:38, 470-500`), no paging, no cost basis.
- `cpsim/oauth.py:36` `REFRESH_TTL = 30 days`; authorize / token at `/oauth/authorize`, `/oauth/token`.
- `cpsim.book_*` — households, accounts (no balance / status / rep code / number), positions, transactions (one fee
  column). No lot table. `book_uploads_kind_chk` admits four kinds.
- `services/altruist_conformance.py` C01–C18 pin the assumed shapes (C10 "no pagination keys", C16 "ISO dates",
  C12/C13 the assumed field lists). The orders (O01–O15) and transfers (T01–T15) groups observe the book through the
  OLD read paths (`/v2/positions?account_id=`, `/v2/transactions?account_id=`, flat keys, `asset_type`, `type`,
  `net_amount`).
- `scripts/verify_cpsim02.py:114-116` `SIM_ROUTES` pins the six old route paths (a `--self-checks-only` check).

---

## 2. Decisions as built (D1–D7)

**D1 — the documented shapes win** (`services/altruist_oauth.py`):
- `_OAUTH_PATHS = {"authorize": "/api/oauth2/authorize", "token": "/oauth2/token"}`; authorize on `login_base`, token
  on **`api_base`**; scope `openid`; `token_type=id` on both grants.
- `REFRESH_TOKEN_LIFETIME = 28 days`. `altruist_connections.refresh_token_issued_at` (set on connect and whenever a
  refresh response carries a NEW refresh token) + 28 days = the refresh token's expiry. Status shows
  `refresh_token_expires_at` / `refresh_token_expired`; `require_active_connection` (so every sync) refuses an expired
  one with `AltruistRefreshExpired` naming the date. A NULL issued-at (a connection made before this sprint) falls back
  to `created_at` — conservative (it can only flag early, never late). **Deviation recorded:** Task 2 said "D3 / D4
  only"; this one nullable column is what D1's expiry flag needs (there was no other source for the issue time).
- Paths: `HOUSEHOLDS_PATH = "/api/v2/households"` (one constant; the portal discrepancy is in the contract §3.1);
  positions / cost-basis / transactions under `/v2/accounts/{account_id}/…` (the id URL-quoted).
- `X-API-Request-ID`: a uuid4 per request, logged (logger `altruist.client`: method, path, request id, status — never a
  token) and returned on the fetch result.
- Paging (`fetch_pages`): `page_size` = the documented max per resource (1000; transactions 500); stop on an empty
  page; with `paging.page` echoed, stop at `total_pages` in the server's own base (the first echo decides 0 or 1);
  without an echo, walk to an empty page; a page with no NEW ids stops (one such page right after the first is allowed
  — the base-ambiguity probe); a base-1 server that answers page 0 empty is retried at 1; a hard ceiling
  (`MAX_PAGES = 200`, overridable) stops and records (`ceiling_hit`; `fetch_resource` raises
  `AltruistPagingCeiling` carrying the partial rows; the sync records the ceiling per account and keeps the partial
  rows).
- Envelope (`_extract_envelope`): `data.<key>` primary; tolerated: a bare list, a flat `<key>` list, `data` as a list,
  `results`. Anything else raises `AltruistOAuthError` naming `data.<key>`. A `data.account_id` / `data.household_id`
  that names a different account than requested is refused.
- Dates (`parse_altruist_date`): `YYYY/MM/DD`, ISO `YYYY-MM-DD`, an ISO timestamp's date part; anything else raises →
  a per-row error, never a guess. Queries send ISO.
- Transactions window (`call_transactions_window`, `transaction_windows`): first sync (no feed position snapshot yet
  for the account) = `LOOKBACK_MONTHS = 13` back from the as-of date; later syncs = the last synced date (the latest
  feed position as_of_date of that account) minus 5 US business days; walked in windows of ≤ 1 calendar month, each
  paged; deduped by id.

**D2 — transactions mapping** (`services/altruist_positions_sync.py`): `transaction_date` → trade date (aliases
`trade_date` / `date` kept for old fixtures); `settle_date`; fees = `transaction_fees + broker_fees + other_fees`
(Decimal, missing = 0; an old fixture's single `fees` still read when none of the three is present); taxes =
`TEFRA_withheld + state_tax_withheld` (same rule); `amount` → gross; `interest`, `transaction_description`,
`transaction_subtype` are carried on the normalized row and reported on the sync result's per-transaction detail —
**[FIND] `portfolio.transactions` has no interest / description / subtype column**, and Task 2 forbade ledger DDL, so
they are not persisted (follow-up in OUTSTANDING_TODO_LIST). Reversal: `cancellation_reversal_flag` parsed strictly
(true / Y / YES / TRUE / 1 → reversal; false / N / NO / FALSE / 0 / blank → normal; anything else → per-row error):
- the flagged row's id is ALREADY recorded and current → that transaction is CLOSED (`valid_to = now()`, no successor
  — the append-only model's own close; counted `transactions_reversed`);
- not recorded → SKIPPED (counted `transactions_skipped_reversal`, reason recorded) — the feed names no original, so a
  reversal is never booked as a new trade and never matched to a guessed original.
Unknown `transaction_type` → per-row error, unchanged.

**D3 — account balance**: `public.account_custodian_balances` (org, account, source_system, environment,
account_balance, as_of_date, custodian_status; valid-axis, one live row per account + source; RLS for all four
operations). Written by `resolve_identity` for every RESOLVED account (the accounts call is where the balance arrives);
unchanged balance + date → no new row. Shown on the account page as "Custodian balance (as of …)". Treasury
(`account_balances_daily` readers) untouched — follow-up recorded.

**D4 — cost basis**: `call_cost_basis` per resolved account (paged, max 1000) → `portfolio.custodian_tax_lots` (org,
account, source_system, environment, external lot id, security match, cusip, symbol, description, quantity,
market_value, cost_basis_unadjusted / adjusted, unrealized, open_date, as_of_date; valid-axis; a lot missing from a
new snapshot is closed; RLS for all four operations). Lot source: `account_lot_method_settings.lot_source`
('hollisworks' default | 'custodian') — set through the EXISTING lot-method account change (maker-checker, the same
gates). `execution.account_lots` returns custodian lots ONLY when the account's approved setting says 'custodian'
(the same row shape, `source: 'custodian'`). In custodian mode `load_holdings` feeds the engine the custodian lots
with `id = None` — a custodian lot has no Hollisworks lot id, so it can never be named on an execution instruction
(the FKs to `tax_lots` stand; the existing custodian-method mismatch warning still applies). Reconciliation
(`services/custodian_lots.reconcile`): per security, Hollisworks open lots vs custodian lots — quantity, basis
(adjusted, else unadjusted) and lot-count differences, flagged. A Hollisworks lot is never written by this sprint.

**D5 — simulator + conformance**: read routes moved to the documented paths and envelope (`data.<key>` + `paging`),
`YYYY/MM/DD` dates, accounts with account_balance (derived: the latest positions' market value on or before the
clock) / status (ACTIVE) / rep_code (null) / account_number (a stable synthetic 9-digit number per account) /
account_type (upper-cased), positions with `long_short_indicator`, cost basis from the book (uploaded lots — new
kind `lots`, `cpsim.book_lots`; else one lot per security position derived from the position's cost basis, opened on
the account's earliest buy of it, else the position's as-of — documented in CPSIM_ADMIN §14), transactions with the
documented names and `date_from` / `date_to` (default the past month) + paging (max 500), `name_pattern` on
households (case-insensitive substring — the docs do not define the pattern language; recorded). Paging base: 0 by
default; `cpsim.app.set_page_base(1)` is a test injection. A `page_size` over the documented max → 400.
OAuth routes moved: `/api/oauth2/authorize` and `/oauth2/token`; token requires `token_type=id`; refresh TTL 28 days.
The conformance suite re-pins every read check to the documented contract (rows tagged DOCUMENTED with the doc page),
adds paging (multi-page, empty page, both bases), envelope, dates, cost basis, transactions window. Orders §8 and
transfers §9 groups: checks unchanged; their book reads go through the documented read paths (`_book_positions` /
`_book_transactions` helpers); their contract sections relabelled "ASSUMED — not part of Altruist's published API
(Realtime / FIX)".

**D6** — `ALTRUIST_CONTRACT.md` read path rewritten from the published docs, with a corrections table (§10).

**D7** — sandbox / production change only by the corrections in §3 (diff review in §4).
`services/portfolio_altruist.py` (the older Phase-B single-credential client) NOT touched — superseded, slated for
removal (OUTSTANDING_TODO_LIST).

---

## 3. Corrections (old → new)

| # | What | Old (assumed) | New (documented) | Doc page |
|---|---|---|---|---|
| 1 | authorize path | `/oauth/authorize` | `/api/oauth2/authorize` | open-api-authentication |
| 2 | scope | `accounts:read` | `openid` | open-api-authentication |
| 3 | token host | login host | API host (`api_base`) | open-api-authentication |
| 4 | token path | `/oauth/token` | `/oauth2/token` | open-api-authentication |
| 5 | token body | — | `token_type=id` | open-api-authentication |
| 6 | refresh lifetime | 30 days (unused) | 28 days, enforced | open-api-authentication |
| 7 | positions path | `/v2/positions?account_id=` | `/v2/accounts/{account_id}/positions/` | endpoints |
| 8 | transactions path | `/v2/transactions?account_id=` | `/v2/accounts/{account_id}/transactions` | endpoints |
| 9 | cost basis | not read | `/v2/accounts/{account_id}/cost-basis/` | endpoints |
| 10 | request id | none | `X-API-Request-ID` per request | endpoints |
| 11 | paging | one request | page / page_size / total_pages | endpoints, json-code-examples |
| 12 | envelope | `{key: [...]}` | `{"paging": …, "data": {key: [...]}}` | json-code-examples |
| 13 | dates | ISO | `YYYY/MM/DD` (queries ISO) | json-code-examples |
| 14 | transactions window | none (= past month) | explicit date_from / date_to | transactions |
| 15 | transaction date | `trade_date` / `date` | `transaction_date` | transactions |
| 16 | fees | `fees` | transaction_fees + broker_fees + other_fees | transactions |
| 17 | reversal | not read | `cancellation_reversal_flag` | transactions |
| 18 | account balance | not read | `account_balance` | accounts |
| 19 | households path | `/api/v2/households` | unchanged — portal shows both `/api/v2/` and `/v2/` | endpoints vs households |

---

## 4. Diff review — sandbox / production

Filled in at Task 9 from `git diff main -- apps/api/services/altruist_oauth.py apps/api/services/altruist_positions_sync.py apps/api/services/altruist_identity.py`.

## 5. Findings as built

Filled in as tasks complete.
