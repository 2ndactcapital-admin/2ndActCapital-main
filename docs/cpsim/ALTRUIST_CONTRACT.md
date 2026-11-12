# Altruist Open API — wire contract (READ path DOCUMENTED; orders / transfers / webhooks ASSUMED)

Rewritten 2026-11-11 by ALTREAD1 (`docs/altruist/ALTREAD1_NOTES.md`) from **Altruist's published Open API**,
developer.altruist.com/docs, read 2026-11-11 through the operator's browser (the sprint itself has no network). The
cpsim01 version of this file described the client as built, with every field ASSUMED; the read path is now pinned to
the published pages and the old assumptions that turned out wrong are listed in §10. Paths below are relative to
`apps/api/` unless they start with `docs/`.

Status vocabulary:
- **DOCUMENTED (page)** — on Altruist's published docs; the page is named (`open-api-authentication`, `endpoints`, a
  resource page, `json-code-examples`).
- **CLIENT** — a choice the client makes where the docs are silent (stated, never presented as Altruist's).
- **ASSUMED** — never published; the orders (§8), transfers (§9) and inbound webhooks (§4) are wholly ASSUMED —
  not part of Altruist's published API (money movement and events live in the Realtime API / FIX, not seen).

Nothing here has been observed against a live Altruist response yet (no sandbox credentials exist). The conformance
suite (`services/altruist_conformance.py`, rows C00–C17 tagged with their doc page) is the instrument for the day
they do: any difference it finds against the real sandbox is a SIMULATOR or client defect to fix, not a new guess.

---

## 1. Environments

| Environment | API base (`api_base`) | Login base (`login_base`) | Source |
|---|---|---|---|
| `sandbox` | `https://openapi.stage1.altruistnet.tech/altruist-open-api` | `https://oauth.stage1.altruistnet.tech` | DOCUMENTED open-api-authentication (stage1 hosts) |
| `production` | `https://openapi.altruist.com/altruist-open-api` | `https://oauth.altruist.com` | cpsim01 (unchanged) |
| `simulator` | `CPSIM_BASE_URL` + `/cpsim/c/<slug>` (both) | same | cpsim02 D2 |

Explicit branches (`services/altruist_oauth.py` `resolve_hosts`, `client_credentials`, `check_destination`): any other
environment raises; no credential crosses environments in either direction. Data is **prior business day, end of
day** (DOCUMENTED endpoints).

## 2. OAuth 2.0 — authorization code, user-scoped (DOCUMENTED open-api-authentication)

### 2.1 Authorize (browser → Altruist)

`GET {login_base}/api/oauth2/authorize?client_id=…&scope=openid&redirect_uri=…&state=…`

| Parameter | Value | Status |
|---|---|---|
| `client_id` | the app's client id | DOCUMENTED |
| `scope` | `openid` | DOCUMENTED |
| `redirect_uri` | fully qualified **https**, allow-listed per environment | DOCUMENTED |
| `state` | 32-byte url-safe random, single use, 10 min, org-scoped (`altruist_oauth_states`) | DOCUMENTED (param) / CLIENT (lifetime) |
| `response_type` | `code` | CLIENT (the OAuth 2.0 request parameter; not shown on the page — the simulator accepts it optionally) |

Built by `build_authorize_url` (`oauth_url(hosts, "authorize")`).

### 2.2 Token — `POST {api_base}/oauth2/token` (on the **API** host)

Form body (`application/x-www-form-urlencoded`), client credentials in the BODY:

| Grant | Fields | Status |
|---|---|---|
| `authorization_code` | `grant_type`, `code`, `redirect_uri`, `client_id`, `client_secret`, **`token_type=id`** | DOCUMENTED |
| `refresh_token` | `grant_type`, `refresh_token`, `client_id`, `client_secret`, `redirect_uri` (when configured), **`token_type=id`** | DOCUMENTED |

Response: `access_token` (1 h), `refresh_token` (**28 days**), `expires_in`, `token_type` — DOCUMENTED. `scope` in the
response: not documented (read when present). Whether a refresh rotates the refresh token: **not documented** — the
client keeps the old one when none is returned (`_parse_token_response`), and records the issue time of the one it
holds (`altruist_connections.refresh_token_issued_at`).

### 2.3 Refresh-token expiry (CLIENT, from the documented 28 days)

`refresh_token_expires_at = refresh_token_issued_at (else created_at) + 28 days`. `/altruist/status` shows
`refresh_token_expires_at` / `refresh_token_expired` / `reconnect_required`; `require_active_connection` — so every sync
— refuses an expired one with `AltruistRefreshExpiredError` (a 409 naming the date) before any call. The access token
is refreshed just-in-time (5 min before expiry) as before.

## 3. Read path (DOCUMENTED endpoints + resource pages + json-code-examples)

Every request: `Authorization: Bearer <access token>` (DOCUMENTED: the Authorization header carries the access token;
the `Bearer` prefix is CLIENT — standard, unconfirmed), `Accept: application/json`, and `X-API-Request-ID: <uuid4>`
(DOCUMENTED optional; CLIENT: one per request, logged with path and status — never a token). V1 paths are deprecating
and are never called.

### 3.0 Paging + envelope (every list)

- Query `page` (default 0) and `page_size` (default 100; **max 1000** households / accounts / positions / cost-basis,
  **max 500** transactions). The client always asks for the maximum.
- Response: `{"paging": {"page": 1, "page_size": 25, "total_pages": 3}, "data": {"<key>": [...], …}}`.
- **The example's `paging.page` is 1 while the parameter defaults to 0.** CLIENT rule (`fetch_pages`): the first
  `paging.page` echo decides the server's base; stop at `total_pages` in that base; stop on an empty page; stop on a
  page that adds no new id (one such page right after the first is allowed — the base-1 probe); a base-1 server that
  answers page 0 empty is retried at 1; a hard ceiling (`MAX_PAGES = 200`) stops the read and records it
  (`AltruistPagingCeiling` with the partial rows — the sync records `paging_ceilings` per account).
- Tolerated for old fixtures only: a bare list, `{"<key>": [...]}`, `{"data": [...]}`, `{"results": [...]}`. Anything
  else is `AltruistOAuthError` naming `data.<key>`. A `data.account_id` / `data.household_id` naming another id than
  the one requested is refused.
- **Dates in responses are `YYYY/MM/DD`** (json-code-examples: "2019/01/15"); query dates are `YYYY-MM-DD`. The client
  parses both (`parse_altruist_date`); anything else is a per-row error.

### 3.1 Households — `GET {api_base}/api/v2/households`

Query `name_pattern` (optional), `page`, `page_size`. `data.households[]`: `id` (required), `name`. **The portal is
inconsistent:** the endpoints index says `/api/v2/households`, the households page itself shows `/v2/households`. The
client uses ONE constant, `HOUSEHOLDS_PATH = "/api/v2/households"` (the index), so a correction is one edit.

### 3.2 Accounts — `GET {api_base}/v2/accounts?household_id=…`

`data.household_id`, `data.accounts[]`: `id`, `rep_code`, `account_number`, `account_type` (e.g. IRA, ROTH IRA, TRUST,
INDIVIDUAL), `account_name`, `status` (ACTIVE, REJECTED, CLOSED, RESTRICTED, PENDING), `date_open`, `date_closed`,
`as_of_date`, `account_balance`. CLIENT: the account number is reduced to its LAST FOUR in the normalizer (never
stored, logged or returned whole); the account is crosswalked by `id` (`portfolio.external_references`, unchanged);
`account_balance` + `as_of_date` + `status` are stored as the CUSTODIAN balance (`public.account_custodian_balances`)
for every resolved account — never in `account_balances_daily` (the treasury's source, unchanged).

### 3.3 Positions — `GET {api_base}/v2/accounts/{account_id}/positions/`

`data.account_id`, `data.positions[]`: `id`, `cusip`, `symbol`, `description`, `long_short_indicator`,
`currency_code`, `quantity`, `as_of_date`, `security_type` (e.g. ETF, AGENCY, CORPORATE, MUNICIPAL, GOVERNMENT,
TREAS…), `market_value`. CLIENT: matched to an asset by CUSIP / ticker (symbol) / name, as before; written to
`portfolio.positions` with the sync's as-of date (unchanged).

### 3.4 Cost basis — `GET {api_base}/v2/accounts/{account_id}/cost-basis/`

`data.account_id`, `data.cost_basis[]` — **one row per TAX LOT**: `id`, `cusip`, `symbol`, `description`, `quantity`,
`market_value`, `cost_basis_unadjusted`, `cost_basis_adjusted`, `unrealized_gain_loss`, `as_of_date`, `open_date`.
CLIENT: stored as CUSTODIAN lots (`portfolio.custodian_tax_lots`, valid-axis; a lot missing from a complete snapshot
is closed), never in `portfolio.tax_lots`. The tax engine reads them only for an account whose approved lot-method
row has `lot_source = 'custodian'` (default `hollisworks`); a reconciliation per account compares them with
Hollisworks' lots (`services/custodian_lots.reconcile`).

### 3.5 Transactions — `GET {api_base}/v2/accounts/{account_id}/transactions?date_from=…&date_to=…`

Query `date_from`, `date_to` (`YYYY-MM-DD`; **default the past month**), `page`, `page_size` (max 500).
`data.transactions[]`: `id`, `cusip`, `symbol`, `security_type` (ETF, FI, OPTION, TBILL, INDEX, EQ, WARRANT, CASH,
MBS, MUTUAL, CD, EQUITY, UNIT, OTHER), `quantity`, `price`, `amount`, `interest`, `currency_code`, `transaction_type`
(e.g. BUY), `transaction_subtype`, `transaction_description`, `cancellation_reversal_flag`, `transaction_fees`,
`broker_fees`, `other_fees`, `TEFRA_withheld`, `state_tax_withheld`, `as_of_date`, `transaction_date`, `settle_date`.

CLIENT mapping (`services/altruist_positions_sync.py`):

| Documented | Hollisworks | Rule |
|---|---|---|
| window | `date_from` / `date_to` always sent | first sync: 13 months back; later: last synced date − 5 US business days; walked in ≤ 1-month windows, each paged; deduped by id |
| `transaction_type` | `transaction_type_code` | lower-cased through `TRANSACTION_TYPE_MAP`; unknown → per-row error (only `BUY` is documented as an example — the rest of the map is ASSUMED) |
| `transaction_date` | `trade_date` | YYYY/MM/DD or ISO; bad → per-row error |
| `settle_date` | `settle_date` | same |
| `amount` | `gross_amount` | Decimal |
| `transaction_fees + broker_fees + other_fees` | `fees` | summed exactly (Decimal), a missing part = 0 |
| `TEFRA_withheld + state_tax_withheld` | `taxes` | same |
| `interest`, `transaction_description`, `transaction_subtype` | — | carried on the sync result; **no ledger column** ([FIND], follow-up) |
| `cancellation_reversal_flag` | — | strict yes / no; a flagged row whose id is recorded and current CLOSES that transaction (`valid_to = now()`); otherwise it is skipped with its reason — never booked as a new trade |
| (client rule) | position | a transaction is recorded only against a current position of its asset (unchanged) |

### 3.6 Call sequence for one sync

`require_active_connection` (refresh-expiry refusal, just-in-time refresh) → households (paged) → accounts per
household (paged; balances stored) → per RESOLVED account: positions (paged) → transactions (windows × pages) →
cost basis (paged; custodian lots stored) → the PORTENTRY1 provisional supersede and the family rollup (unchanged).

## 4. Webhooks (inbound, Altruist → us) — ASSUMED (not part of Altruist's published Open API; the Realtime API is a separate, beta track)

- **Endpoint:** `POST /api/v1/altruist/webhook` (`routers/altruist_webhook.py:40`,
  mounted with prefix `/api/v1` in `main.py:639`). It is listed in
  `PUBLIC_PATHS` (`main.py:158`), so no Auth0 check applies.
- **Header read:** `X-Altruist-Signature` (`services/altruist_webhook.py:46`,
  `routers/altruist_webhook.py:43`). No other header is read: no timestamp, no
  event id, no `Content-Type` check.
- **Signature** (`services/altruist_webhook.py:72-85`):
  - `expected = hmac.new(ALTRUIST_WEBHOOK_SECRET.encode("utf-8"), raw_body, sha256).hexdigest()`.
  - It signs the **exact raw request body bytes**, with nothing prepended (no
    timestamp, no method or path).
  - Output is **lowercase hex**, 64 characters. No `sha256=` or `v1=` prefix is
    accepted.
  - The header value is `.strip()`ped, then compared with
    `hmac.compare_digest`, which is constant-time and case-sensitive.
  - A missing secret, missing header or mismatch all return an identical 401
    `{"detail":"Invalid signature"}` (`routers/altruist_webhook.py:37`, `:45-48`).
    Verification runs before parsing.
- **Replay window: none.** There is no timestamp tolerance. Replay protection is
  only the `event_id` dedup below.
- **Body parsing:** JSON. An undecodable body becomes
  `{"_unparseable_body": true}` and is still logged (`routers/altruist_webhook.py:50-53`).
  A non-object JSON value is wrapped as `{"_non_object_payload": …}`
  (`services/altruist_webhook.py:196-197`).
- **Field extraction** (`:53-63`, `:88-128`). The first truthy match wins,
  checking the top level first and then a nested `data` object:

| extracted | keys tried in order | if absent |
|---|---|---|
| event id | `event_id`, `id`, `eventId`, `webhook_id` | `unidentified:<sha256 of canonical JSON>` (`:102-113`) |
| event type | `event_type`, `type`, `eventType` | `NULL`. Only stored, **never dispatched on** |
| identifier | `account_id`, `accountId`, `external_account_id`, `household_id`, `householdId`, `resource_id`, `resourceId` | status `logged_unrecognized` |
| environment | `environment`, `env` | `"sandbox"` (`:48`, `:126-128`) |

- **Idempotency:** `INSERT … ON CONFLICT (event_id) DO NOTHING` into
  `public.altruist_webhook_events` with `org_id NULL` (`:131-154`). A repeat
  returns `{"status":"duplicate","event_id":…}` (`:205-206`).
- **Dispatch** (`:208-237`):
  - The identifier is looked up across orgs as an **account**
    (`record_type='account'`) in `portfolio.external_references` (`:157-172`).
    A household id can therefore never resolve.
  - If found, the client sets `org_id` and `processed_at` (`:175-183`), then
    runs `resolve_identity` and `sync_resolved_accounts` for that org using the
    **payload's** environment (`:226-231`).
  - Any sync exception is captured, not raised.
- **Event types handled:** none specifically. Every event type that carries a
  resolvable account id triggers the same full sync.
- **Response:** 200 with the `process_event` dict. On success it includes the
  internal `org_id` and a sync summary (`:237`, `routers/altruist_webhook.py:70`).

---

## 5. Field tables — see §3 (every read field is listed there with its page)

The cpsim01 tables T1–T7 described the assumed shapes and are superseded by §2–§3; the rows that changed are in §10.

## 6. Simulator obligations (cpsim02 → ALTREAD1)

The simulator (`cpsim/app.py`, `cpsim/oauth.py`, `cpsim/book.py`) serves exactly §2–§3:

1. `GET /cpsim/c/<slug>/api/oauth2/authorize` (response_type optional, must be `code` when sent) and
   `POST /cpsim/c/<slug>/oauth2/token` (**`token_type=id` required**; refresh tokens live 28 days and rotate — rotation
   is a simulator choice, the docs are silent).
2. The read routes at the documented paths, the `{"paging", "data"}` envelope, `page` / `page_size` with the documented
   maxima (over the max → 400), page base 0 (a test injection, `set_page_base(1)`, serves base 1 — a page-0 request is
   page 1, echoed as 1).
3. `YYYY/MM/DD` dates; accounts with `account_balance` (DERIVED: the latest positions' market value on or before the
   clock), `status` ACTIVE, `rep_code` null, a stable synthetic 9-digit `account_number`, `account_type` upper-cased;
   positions with `long_short_indicator` (S when the quantity is negative) and `security_type` (CASH for a cash row,
   else the book's type upper-cased); CUSIP / symbol filled from the security master when an upload named only an ISIN.
4. Cost basis: the account's uploaded lots (kind `lots`, `cpsim.book_lots`) where it has any; else one lot per
   security position with a cost basis, opened on the account's earliest buy of it (else the position's as-of).
5. Transactions: `transaction_type` upper-cased, the book's single fee as `transaction_fees` (broker / other "0"), its
   single tax as `TEFRA_withheld`, `amount` = net amount else gross, `cancellation_reversal_flag` "N"; `date_from` /
   `date_to` (default the past month), never after the clock.
6. `name_pattern` on households: case-insensitive substring (the docs do not define the pattern language).
7. Every response carries `X-Counterparty-Simulated: true` and is request-logged (no bodies, no keys).

## 7. Open questions and divergences (updated by ALTREAD1)

1. **Two Altruist clients.** `services/portfolio_altruist.py` (Phase B, single global credential, `{BASE_URL}/accounts`,
   the client secret as Bearer) is SUPERSEDED by `altruist_oauth.py` and slated for removal (OUTSTANDING_TODO_LIST);
   untouched by ALTREAD1.
2. Households path `/api/v2/` vs `/v2/` (§3.1) — portal inconsistency, one constant.
3. `paging.page` base (§3.0) — handled both ways; the real base is unknown until a sandbox call.
4. Refresh-token rotation — not documented (§2.2).
5. `Authorization: Bearer …` — the docs say "the access token"; the `Bearer` prefix is unconfirmed.
6. `interest` / `transaction_description` / `transaction_subtype` have no ledger column (§3.5).
7. `cancellation_reversal_flag` values — the docs give the field, not its values; the client accepts only clear yes / no
   values and treats anything else as a per-row error.
8. Only `BUY` is documented as a `transaction_type` value; the rest of `TRANSACTION_TYPE_MAP` is ASSUMED.
9. Inbound webhooks (§4) are ASSUMED; the Realtime API (beta, approval pending) is a separate track.
10. Still open from cpsim01: the webhook's sandbox-vs-production choice from the payload; `external_references` not
    scoped between sandbox and production; no browser leg for the real callback (the callback is an authenticated API
    route); unwrapped parse errors on the token endpoint; state-rejection detail in the callback's 400.
11. CLOSED by ALTREAD1: `REFRESH_TOKEN_LIFETIME` unused (now enforced, §2.3); the falsy-`or` chains on `quantity` /
    `market_value` (now `is not None`); one-request reads (now paged); the past-month-only transactions read.

---

## 8. Orders — ASSUMED — not part of Altruist's published API (Realtime / FIX)

Written 2026-11-11 for cpsim03 (`docs/cpsim/CPSIM03_NOTES.md` D1). **Altruist's real trading API has never been seen**:
no repo doc, no code comment, no sandbox call cites an orders contract of any kind. The real Altruist route stays
manual (`custodian_capabilities` `altruist.equity_trading_api = 'unknown'`; no real trading client exists). Every row
below is **ASSUMED** — chosen to look like the read API in §3 (same hosts, Bearer access token, JSON, primary list key,
explicit ids, ISO dates) — and is pinned by the conformance suite's `orders` group (O01–O15,
`services/altruist_conformance.py`), so the day a real contract arrives the differences are a list, not a hunt.

Client: `services/altruist_trading.py` (the cpsim02 client conventions: hosts resolved per call, `Bearer` access token
through `require_active_connection`, `Accept: application/json`, a JSON body, 15 s, an optional `transport` on every
call, `AltruistOAuthError` on any HTTP status ≥ 400 or a transport failure). Server: `cpsim/orders.py` under
`/cpsim/c/<slug>`. Simulator environment only.

### 8.1 Calls

| # | Call | Status | Notes |
|---|---|---|---|
| O-1 | `POST {api_base}/v2/orders` — body below; `201` a new order, `200` the SAME order on a repeat `client_order_id` | ASSUMED | idempotent on `client_order_id` per counterparty: a repeat POST never creates a second order (whatever the repeated body says); `422` a malformed body `{detail, errors: [{field, message}]}`; `404` an account not in the book |
| O-2 | `GET {api_base}/v2/orders/{order_id}` → `{"order": {…}}` | ASSUMED | `404` an order of another counterparty, or unknown |
| O-3 | `GET {api_base}/v2/orders?account_id=<id>` → `{"orders": [{…}]}` | ASSUMED | `account_id` required (`400` without); newest first; one response, no pagination |
| O-4 | `DELETE {api_base}/v2/orders/{order_id}` → `{"order": {…}}` with status `cancelled` | ASSUMED | only while `accepted` / `working` / `partially_filled`; `409` `{detail}` once `filled`, `rejected` or `cancelled` (the reason names the status); executions already made stay |
| O-5 | resources need an ACCESS token (`Bearer cpsim_at_…`, §2) — the `cpsim_live_` key itself is refused (`401`) | ASSUMED | as §3 |
| O-6 | every response carries `X-Counterparty-Simulated: true` (simulator only) | simulator | as cpsim02 |

**The repeat rule (O-1, pinned by conformance O03 + O15):** a repeat `POST /v2/orders` with the same
`client_order_id` (per counterparty) returns the **original order unchanged, `200`, whatever the body** — a different
side, quantity, order type, limit price or time in force is ignored, not validated against the original, not merged and
not refused; exactly one order exists for that `client_order_id`. The simulator enforces it with
`orders_client_order_live_uq` (UNIQUE `(counterparty_id, client_order_id) WHERE system_to IS NULL`) and answers a repeat
with the live order as stored. ASSUMED, like the rest of §8 (CPSIM03_NOTES §9, fix 1 finding I).

### 8.2 Order request (POST body)

| Field | Type | Status | Rule |
|---|---|---|---|
| `client_order_id` | string ≤ 80 | ASSUMED | the Hollisworks execution instruction id (a uuid); the idempotency key |
| `account_id` | string | ASSUMED | the custodian's account id (the crosswalked external id) |
| `symbol` | string | ASSUMED | ticker; `symbol` and/or `cusip` — at least one; if both, they must name the same security |
| `cusip` | string | ASSUMED | |
| `side` | `buy` \| `sell` | ASSUMED | no short / cover side is defined (shorts stay manual — rep13) |
| `quantity` | decimal string | ASSUMED | exactly one of `quantity` / `notional`, > 0 |
| `notional` | decimal string | ASSUMED | an amount in the account currency; converted at the fill price (fractional shares) |
| `order_type` | `market` \| `limit` | ASSUMED | |
| `limit_price` | decimal string | ASSUMED | required for `limit`, refused for `market` |
| `time_in_force` | `day` \| `gtc` | ASSUMED | recorded; v1 of the simulator does not expire `day` orders (CPSIM03_NOTES §4) — the client sends `gtc` |

Decimals travel as STRINGS both ways (a JSON float is refused, as everywhere in the execution path).

### 8.3 Order response (`order`)

| Field | Type | Status |
|---|---|---|
| `order_id` | string (`sim-o-<16 hex>`) | ASSUMED |
| `client_order_id`, `account_id`, `symbol`, `cusip`, `side`, `quantity`, `notional`, `order_type`, `limit_price`, `time_in_force` | as sent (decimals as strings) | ASSUMED |
| `status` | `accepted` \| `working` \| `partially_filled` \| `filled` \| `rejected` \| `cancelled` | ASSUMED |
| `reject_reason` | string or null | ASSUMED |
| `filled_quantity` | decimal string | ASSUMED |
| `average_price` | decimal string or null | ASSUMED |
| `executions` | list (below), oldest first | ASSUMED |
| `created_at`, `updated_at` | ISO 8601 UTC | ASSUMED |
| `trade_date` | ISO date — the counterparty's business date at receipt | ASSUMED |

`executions[]`: `exec_id` (string `sim-e-<16 hex>`), `quantity`, `price` (decimal strings), `executed_at` (ISO 8601
UTC, the counterparty's wall clock — never its business date), `trade_date` (ISO date, the business date),
`settle_date` (ISO date, trade date + 1 US business day). All ASSUMED.

### 8.4 Webhooks (outbound, simulator → us) — ASSUMED

`order.updated` (an order accepted, working, rejected or cancelled) and `order.filled` (any new execution), only when
the counterparty's behaviour has webhooks on. Body (compact JSON, the signed bytes): `{event_id, event_type, order_id,
client_order_id, account_id, environment: "simulator", occurred_at}`; signed as §4 / cpsim02 D10
(`X-Altruist-Signature`, HMAC-SHA256 hex with `ALTRUIST_SIMULATOR_WEBHOOK_SECRET`). The receiver treats an order event
as a NOTIFICATION: the account's resync runs as for any event (§4), plus a to-do for the instruction's sender ("fills
available — import them"). **A webhook never books a fill.**

### 8.5 Simulator outcomes (D6) — the simulated counterparty's scripted behaviour, not a contract claim

From the behaviour row when the order is received; price = the last PLATFORM close on or before the counterparty's
clock date. accept → filled in full (a limit buy only if price ≤ limit, a limit sell only if price ≥ limit, else
working); reject → rejected with the behaviour's reason; partial → one execution for `partial_pct` of the quantity
(rounded down to the unit: one share for a whole-share quantity, 0.000001 otherwise), the remainder working until
cancelled; delay → working until the clock advances, then filled as accept. No price → rejected `no price`; a security
type other than equity / fund → rejected `unsupported instrument`; a sell above the position → rejected `insufficient
position`. Working orders are re-evaluated on every clock advance (delay orders and unmarketable limits; a partial's
remainder stays working).

## 9. Transfers — ASSUMED — not part of Altruist's published API (Realtime / FIX)

Written 2026-11-11 for cpsim04 (`docs/cpsim/CPSIM04_NOTES.md` D1). **Altruist's real money-movement API has never
been seen**: no repo doc, no code comment, no sandbox call cites a transfer contract of any kind
(`custodian_capabilities` `altruist.ach / wire / journal = 'unknown'`, `altruist.payment_status_feed = 'no'`). The real
Altruist route stays manual (the payment sheet). Every row below is **ASSUMED** — shaped like §3 and §8 (same hosts,
Bearer access token, JSON, primary list key, explicit ids, ISO dates, decimals as strings) — and is pinned by the
conformance suite's `transfers` group (T01–T16, `services/altruist_conformance.py`).

Client: `services/altruist_transfers.py` (the §8 client conventions call for call: hosts resolved per call, `Bearer`
access token through `require_active_connection`, `Accept: application/json`, a JSON body, 15 s, an optional
`transport` on every call, `AltruistOAuthError` on any HTTP status ≥ 400 or a transport failure; the caller reads its
session and RELEASES its database connection before the HTTP call). Server: `cpsim/transfers.py` under
`/cpsim/c/<slug>`. Simulator environment only — asked for sandbox or production the client refuses before any request.

### 9.1 Calls

| # | Call | Status | Notes |
|---|---|---|---|
| T-1 | `POST {api_base}/v2/transfers` — body below; `201` a new transfer, `200` the SAME transfer on a repeat `client_transfer_id` | ASSUMED | idempotent on `client_transfer_id` per counterparty; `422` a malformed body `{detail, errors: [{field, message}]}`; `404` a `from_account_id` not in the book |
| T-2 | `GET {api_base}/v2/transfers/{transfer_id}` → `{"transfer": {…}}` | ASSUMED | `404` a transfer of another counterparty, or unknown |
| T-3 | `GET {api_base}/v2/transfers?account_id=<id>` → `{"transfers": [{…}]}` | ASSUMED | `account_id` required (`400` without); either leg; newest first; one response, no pagination |
| T-4 | `DELETE {api_base}/v2/transfers/{transfer_id}` → `{"transfer": {…}}` status `cancelled` | ASSUMED | only while `received` / `pending_consent` (nothing has moved); `409` `{detail}` naming the status otherwise |
| T-5 | resources need an ACCESS token (`Bearer cpsim_at_…`, §2) — the `cpsim_live_` key itself is refused (`401`) | ASSUMED | as §3 / §8 |
| T-6 | every response carries `X-Counterparty-Simulated: true` (simulator only) | simulator | as cpsim02 |

**The repeat rule (T-1, pinned by conformance T03):** a repeat `POST /v2/transfers` with the same `client_transfer_id`
(per counterparty) returns the **original transfer unchanged, `200`, whatever the body** — a different amount, rail,
account or value date is ignored, not validated against the original, not merged and not refused; exactly one transfer
exists for that `client_transfer_id` (UNIQUE `(counterparty_id, client_transfer_id) WHERE system_to IS NULL`). The §8.1
rule, ASSUMED like the rest of §9.

### 9.2 Transfer request (POST body)

| Field | Type | Status | Rule |
|---|---|---|---|
| `client_transfer_id` | string ≤ 80 | ASSUMED | the Hollisworks payment instruction id (a uuid); the idempotency key |
| `from_account_id` | string | ASSUMED | the custodian's account id (the crosswalked external id) |
| `to_account_id` | string | ASSUMED | a destination account AT THE SAME custodian (a journal); exactly one of `to_account_id` / `to_bank` |
| `to_bank` | object | ASSUMED | a destination at another institution: `{institution_slug, account_id, routing_last4}` (domestic) or `{institution_slug, account_id, bic, iban_last4}` (international) — REFERENCES only: the last four digits of a routing number / IBAN, never a full number |
| `to_bank.institution_slug` | string | ASSUMED | the receiving bank (a simulated bank's slug) |
| `to_bank.account_id` | string | ASSUMED | the account in the receiving bank's book |
| `to_bank.routing_last4` | 4 digits | ASSUMED | domestic: last four of the bank's ACH ABA (rail `ach`) or wire ABA (rail `wire`) |
| `to_bank.bic` | string | ASSUMED | international: the receiving bank's BIC |
| `to_bank.iban_last4` | 4 characters | ASSUMED | international: last four of the beneficiary IBAN |
| `amount` | decimal string, 2 dp, > 0 | ASSUMED | a JSON number is refused (`422` naming the field) |
| `currency` | `USD` | ASSUMED | the only currency |
| `rail` | `ach` \| `wire` \| `journal` | ASSUMED | `journal` needs `to_account_id`; `ach` / `wire` need `to_bank`; an international bank takes `wire` only |
| `value_date` | ISO date | ASSUMED | a US business day; not before the counterparty's business date |

### 9.3 Transfer response (`transfer`)

| Field | Type | Status |
|---|---|---|
| `transfer_id` | string (`sim-t-<16 hex>`) | ASSUMED |
| `client_transfer_id`, `from_account_id`, `to_account_id`, `amount`, `currency`, `rail`, `value_date` | as sent | ASSUMED |
| `to_bank` | `{institution_slug, account_id, routing_last4 \| bic + iban_last4}` as sent, or null | ASSUMED |
| `status` | `received` \| `pending_consent` \| `acknowledged` \| `settled` \| `rejected` \| `returned` \| `cancelled` | ASSUMED |
| `reject_reason` | string or null (the custodian's or the receiving bank's reason) | ASSUMED |
| `return_code` | `R\d\d` or null | ASSUMED |
| `return_reason` | string or null | ASSUMED |
| `acknowledged_on`, `settle_on`, `settled_on`, `returned_on` | ISO dates or null (business dates) | ASSUMED |
| `events` | list `{status, on, at}` oldest first — every status the transfer took | ASSUMED |
| `created_at`, `updated_at` | ISO 8601 UTC | ASSUMED |

### 9.4 Feed transaction types for cash (`GET /v2/accounts/{account_id}/transactions`, `transaction_type`) — ASSUMED values

Every cash movement is a transaction on the account's CASH row (row_type cash, no identifier, `name` = the cash
position's name), with an explicit `id` and ISO dates; amounts in `net_amount` (decimal string):

| type | on | net_amount | maps to (client, D2) |
|---|---|---|---|
| `transfer_out` / `ach_out` / `wire_out` | the source account, at acknowledgement | the amount (positive) | `sell` — money leaves the holding |
| `transfer_in` / `ach_in` / `wire_in` | the destination account (same custodian), at settlement | the amount (positive) | `buy` — money arrives |
| `ach_return` | BOTH accounts, `ach_return_after_days` business days after settlement | `+amount` on the source, `−amount` on the destination | `adjustment` (the stored sign) |

### 9.5 Webhooks (outbound, simulator → us) — ASSUMED

`transfer.updated` on every status change (webhooks on only): `{event_id, event_type, transfer_id,
client_transfer_id, account_id (the source), environment: "simulator", occurred_at}`, signed as §4 / §8.4. Delivered
AFTER the response (cpsim03 fix 1). The receiver treats it as a NOTIFICATION: the account resync runs, plus a to-do for
the payment's sender ("custodian status changed — refresh"). **A webhook never applies a status.**

### 9.6 Simulator lifecycle (cpsim04 D3 / D6 / D8) — the simulated counterparty's scripted behaviour, not a contract claim

At receipt (the counterparty's clock date C): behaviour `reject` → rejected with its reason; `consent_required` →
`pending_consent` until the admin records the member's consent; `delay` → stays `received` until the next clock
advance; otherwise acknowledged at once: the source must hold the amount in cash (else rejected `insufficient funds`),
a bank destination is validated by the receiving bank (routing / BIC / IBAN reference, account in its book, its
behaviour) — a mismatch is rejected with the bank's reason — and the source is debited. Settlement when C ≥ `settle_on`
(journal and wire: the value date; ACH: value date + 1 business day): the destination is credited. An ACH with a return
code (the sender's behaviour, else the receiving bank's) is returned `ach_return_after_days` business days after
settlement, both legs reversed. Every step re-runs at each clock advance.

---

## 10. Corrections — the cpsim01 assumptions that the published docs overturned

| # | Item | cpsim01 (ASSUMED) | Published (DOCUMENTED) | Page |
|---|---|---|---|---|
| 1 | authorize path | `/oauth/authorize` | `/api/oauth2/authorize` | open-api-authentication |
| 2 | scope | `accounts:read` | `openid` | open-api-authentication |
| 3 | token host | login host | API host | open-api-authentication |
| 4 | token path | `/oauth/token` | `/oauth2/token` | open-api-authentication |
| 5 | token body | — | `token_type=id` | open-api-authentication |
| 6 | refresh lifetime | 30 days, unused | 28 days, enforced | open-api-authentication |
| 7 | positions | `/v2/positions?account_id=` | `/v2/accounts/{account_id}/positions/` | endpoints |
| 8 | transactions | `/v2/transactions?account_id=` | `/v2/accounts/{account_id}/transactions` | endpoints |
| 9 | cost basis | not read | `/v2/accounts/{account_id}/cost-basis/` | endpoints |
| 10 | request id | none | `X-API-Request-ID` | endpoints |
| 11 | paging | one response | `page` / `page_size` / `paging.total_pages` | endpoints, json-code-examples |
| 12 | envelope | `{"<key>": [...]}` | `{"paging": …, "data": {"<key>": [...]}}` | json-code-examples |
| 13 | dates | ISO | `YYYY/MM/DD` (queries ISO) | json-code-examples |
| 14 | transactions window | none | `date_from` / `date_to` (default past month) | transactions |
| 15 | transaction date | `trade_date` / `date` | `transaction_date` | transactions |
| 16 | transaction type | `type` | `transaction_type` (+ `transaction_subtype`) | transactions |
| 17 | fees | `fees` | `transaction_fees` + `broker_fees` + `other_fees` | transactions |
| 18 | amount | `gross_amount` / `net_amount` | `amount` | transactions |
| 19 | reversal | — | `cancellation_reversal_flag` | transactions |
| 20 | security fields | `ticker` / `name` / `asset_type` / `isin` | `symbol` / `description` / `security_type` (no ISIN) | positions |
| 21 | account fields | `name` | `account_name`, `account_balance`, `status`, `rep_code`, `account_number`, dates | accounts |
| 22 | households path | `/api/v2/households` | unchanged (index) — the page shows `/v2/households` | endpoints vs households |
