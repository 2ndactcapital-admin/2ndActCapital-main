# Altruist-compatible counterparty — wire contract (cpsim01)

Specification for the cpsim02 Altruist-compatible simulator and the basis of
its future conformance suite. Written 2026-11-10 from the code as it stands on
branch `cpsim01-work` (`fca695d`). Every statement cites `file:line`; paths are
relative to `apps/api/` unless they start with `docs/` or `sprint_prompts/`.

> **Read this first: almost nothing here has been observed live.**
> The client's own docstring says *"NO LIVE ALTRUIST CALL HAS EVER BEEN MADE
> FROM THIS MODULE"* (`services/altruist_oauth.py:22`). Every token-response,
> resource-response and webhook shape was built from guesses and proven only
> against `httpx.MockTransport` payloads the verify scripts wrote themselves
> (`services/altruist_oauth.py:27-31`). The webhook signature scheme is a
> self-declared industry-standard best guess (`services/altruist_webhook.py:4-19`).
> **So every field below is ASSUMED unless the code or a repo doc cites
> Altruist's published docs.** Only three items carry such a citation (the
> households/accounts paths and the `household_id` parameter, cited from the
> Open API "Guides" — `services/altruist_oauth.py:632-634`,
> `sprint_prompts/altruist_sprint2_identity_resolution.structural.md:9`), and
> even those were never checked against Altruist's `/reference` pages.
>
> One nuance: the Altruist stage1 host *has* been reached over the network, by
> Sprint 1's verify script calling `call_households` with synthetic
> credentials. It answered HTTP 401 (`docs/PROJECT_STATUS.md:7326-7329`,
> `:7444-7448`). That proves DNS and egress, not any response shape.

Status vocabulary used in the tables below:
- **DOCUMENTED**: cited by the code or a repo doc to Altruist's published docs.
- **ASSUMED**: never observed live, and no citation.

---

## 1. Environments

`ALTRUIST_HOSTS` (`services/altruist_oauth.py:78-87`):

| environment key | `api_base` | `login_base` (OAuth host) |
|---|---|---|
| `sandbox` | `https://openapi.stage1.altruistnet.tech/altruist-open-api` | `https://oauth.stage1.altruistnet.tech` |
| `production` | `https://openapi.altruist.com/altruist-open-api` | `https://oauth.altruist.com` |

- The source cited is "Confirmed real facts (sprint prompt)" (`:73-77`). The
  Sprint 1 prompt is not in the repo, so these hosts are sprint-prompt
  assertions, not published-doc citations. Status: **ASSUMED**.
- The allowed set is `ENVIRONMENTS = ("sandbox", "production")` (`:100`). The
  router checks every request against it (`routers/altruist_connection.py:68-74`).
- The key is **`sandbox`, not `stage`.** The cpsim01 prompt says "stage"
  (`sprint_prompts/cpsim01.structural.md:88`). The code never uses that word.
- `_OAUTH_PATHS` (`:91-94`): `authorize` = `/oauth/authorize`, `token` =
  `/oauth/token`. The code calls these an assumption from standard OAuth2
  convention (`:48-56`, `:89-90`). **ASSUMED**.
- `_API_PATHS` (`:646-651`):

| key | path | basis |
|---|---|---|
| `households` | `/api/v2/households` | "as documented … in the source Guides" (`:632-634`), with the inconsistent `/api` prefix flagged. **DOCUMENTED (Guides only)** |
| `accounts` | `/v2/accounts` | same citation. **DOCUMENTED (Guides only)** |
| `positions` | `/v2/positions` | "a guess, not a confirmed fact" (`:637-645`). **ASSUMED** |
| `transactions` | `/v2/transactions` | same guess. **ASSUMED** |

The full URL is `api_base + path`, for example
`https://openapi.stage1.altruistnet.tech/altruist-open-api/api/v2/households`
(`:762-763`).

Credentials are read from environment variables. The code never reads them
from the database for outbound calls, except the per-connection tokens.

| var | read at | notes |
|---|---|---|
| `ALTRUIST_SANDBOX_CLIENT_ID` / `_CLIENT_SECRET` | `:245-256` | used when `environment == "sandbox"` |
| `ALTRUIST_PRODUCTION_CLIENT_ID` / `_CLIENT_SECRET` | `:246` | used for **any other** environment value |
| `ALTRUIST_REDIRECT_URI` | `:230-242` | must be set and must start with `https://` |
| `ALTRUIST_TOKEN_ENCRYPTION_KEY` | `:116-138` | Fernet key for tokens at rest |
| `ALTRUIST_WEBHOOK_SECRET` | `services/altruist_webhook.py:47,80` | HMAC key |

Token lifetimes (`services/altruist_oauth.py:96-98`):
- `ACCESS_TOKEN_LIFETIME = 1h`. It is used only as the default when
  `expires_in` is missing or falsy (`:370`).
- `REFRESH_TOKEN_LIFETIME = 30d`. **It is defined but referenced nowhere** in
  the codebase. Refresh-token expiry is not tracked or enforced.
- `STATE_TTL = 10 min`: the OAuth `state` lifetime (`:180`).
- Proactive refresh fires once the access token is within
  `DEFAULT_REFRESH_THRESHOLD = 5 min` of expiry (`:555`, `:581`).

---

## 2. OAuth

### 2.1 Authorize redirect (browser → Altruist)

Built by `build_authorize_url` (`services/altruist_oauth.py:259-272`):

```
GET {login_base}/oauth/authorize?response_type=code&client_id=…&redirect_uri=…&state=…&scope=accounts:read
```

- Encoding is `urllib.parse.urlencode`, with params in the order shown (`:265-272`).
- `scope` defaults to `accounts:read` (`:259`). The only caller
  (`routers/altruist_connection.py:194`) never overrides it.
- `state` is `secrets.token_urlsafe(32)` (`:174-175`). It is stored in
  `altruist_oauth_states` with a 10-minute expiry and is single-use (`:178-225`).
- No PKCE parameters, no `nonce`, no `prompt`.
- Entry point: `POST /api/v1/altruist/connect` returns `{authorize_url, environment}`
  (`routers/altruist_connection.py:179-198`).

The return leg (Altruist → our redirect URI) is
`GET /api/v1/altruist/callback?code=…&state=…`. Both query parameters are
required (`routers/altruist_connection.py:201-203`). The route is
**authenticated**: it runs `_require(request, manage_custody_connections)`
(`:222`) and derives `org_id` from the caller's session (`:78`). The state is
consumed, which rejects a missing, used, expired or cross-org state with 400
(`:226-229`). An `error=` or `error_description=` callback parameter is
**not handled**. FastAPI would return 422 for the missing `code`.

### 2.2 Token exchange: `authorization_code`

`exchange_code_for_tokens` (`services/altruist_oauth.py:283-319`):

- **Method/URL:** `POST {login_base}/oauth/token` (`:293`, `:297`).
- **Headers:** `Accept: application/json` (`:306`). `Content-Type` is
  `application/x-www-form-urlencoded`, which httpx sets implicitly from `data=`
  (`:299`).
- **Client authentication:** `client_secret` goes in the form body
  (client_secret_post). **There is no HTTP Basic header** (`:299-305`).
- **Body:** `grant_type=authorization_code`, `code`, `redirect_uri`,
  `client_id`, `client_secret` (`:300-304`).
- **Timeout:** 15 s (`:283`). A transport error raises `AltruistOAuthError`
  (`:308-312`). Any status `>= 400` raises
  `AltruistOAuthError("Token exchange was refused: HTTP n")` (`:314-317`).
  The callback maps that to 502 (`routers/altruist_connection.py:237-238`).
- **Response:** the client calls `response.json()` (`:318`), then
  `_parse_token_response` (`:361-376`).

### 2.3 Token refresh: `refresh_token`

`refresh_access_token` (`services/altruist_oauth.py:322-358`): same URL,
headers, auth style and error handling as the exchange.

- **Body:** `grant_type=refresh_token`, `refresh_token`, `client_id`,
  `client_secret` (`:339-344`).
- **No `redirect_uri` and no `scope`** are sent.
- It is called only from `ensure_fresh_token` (`:558-594`).

### 2.4 Token response parsing (`_parse_token_response`, `:361-376`)

| response field | behaviour when present | behaviour when missing or falsy |
|---|---|---|
| `access_token` | stored Fernet-encrypted, plus its last 4 characters | `AltruistOAuthError("Token response missing access_token.")` (`:363-364`) |
| `refresh_token` | stored (replaces the old one on refresh) | exchange: error (`:368-369`); refresh: **the old refresh token is kept** (`:358`, `:367`) |
| `expires_in` | `int(...)` → `token_expires_at = now + expires_in` (`:370`, `:431`, `:463`) | defaults to 3600. A value of `0` also becomes 3600 (falsy). A non-numeric string raises an unwrapped `ValueError` |
| `scope` | stored in `altruist_connections.scope` (`:375`, `:453`) | `None` |
| `token_type` | **never read.** The client always sends `Bearer` | — |
| `id_token`, other fields | ignored | — |

A non-JSON 2xx body makes `response.json()` raise an unwrapped decode error
(`:318`, `:357`).

### 2.5 When refresh happens

- There is **no reactive refresh**. A 401 from a resource call is not retried
  or refreshed. It becomes `AltruistOAuthError` like any other `>= 400`
  (`:779-782`, etc.).
- Refresh is **just-in-time only**. `require_active_connection` (`:597-629`)
  runs before every resource call and calls `ensure_fresh_token`, which
  refreshes when `token_expires_at - now <= 5 min` (`:581`).
- If the refresh fails while the token is still valid, the failure is
  swallowed (`:587-590`). If the token is already expired, the error is
  re-raised.
- `GET /api/v1/altruist/status` also refreshes opportunistically
  (`routers/altruist_connection.py:270-280`).
- There is no 429 handling, no `Retry-After`, no backoff and no retry anywhere.

---

## 3. Resource calls

Every call shares this shape (`services/altruist_oauth.py:741-912`):
1. `require_active_connection(org, env)`, then decrypt the access token.
2. `GET api_base + path` with headers `Authorization: Bearer <access_token>`
   and `Accept: application/json`. No other headers: no `User-Agent`
   override, no request id, no idempotency key.
3. Timeout 15 s. Optional `transport` (test injection only).
4. A status `>= 400` raises `AltruistOAuthError("GET <url> was refused: HTTP n")`.
5. `response.json()`, then `_extract_list(body, <key>, "data", "results")`
   (`:654-671`). The body may be a bare JSON array, or an object where the
   **first** of the three keys that holds a list wins. Anything else raises
   `AltruistOAuthError`.
6. **Pagination: none.** Each resource is fetched in exactly one request. No
   page, limit, cursor or offset is sent. Envelope keys such as `next`,
   `cursor`, `total`, `has_more` and `links` are ignored. The call ends after
   the first response, so only the first page of a paginated server would ever
   be read.
7. **No date-range parameters** on positions or transactions. "As of" is
   local: `as_of_date or date.today()` (`services/altruist_positions_sync.py:482`).

### 3.1 Households: `GET {api_base}/api/v2/households`

- No query parameters (`:765-773`). Envelope list key: `households`, then `data`, then `results` (`:784`).
- Per item (`_normalize_household`, `:674-679`):
  - `id` = `str(id or household_id or "")`
  - `name` = `name or household_name or ""`
  - `raw` = the whole item
- Caller: `resolve_identity` (`services/altruist_identity.py:198-200`) uses
  each `id` as the `household_id` for the accounts call. `name` reaches the
  database only inside an exception row's `raw_row` (`:139-146`).

### 3.2 Accounts: `GET {api_base}/v2/accounts?household_id=<id>`

- One call per household (`services/altruist_identity.py:207-211`, `services/altruist_oauth.py:808-815`).
- Envelope key: `accounts`, then `data`, then `results` (`:826`).
- Per item (`_normalize_account`, `:682-687`):
  - `id` = `str(id or account_id or "")`
  - `name` = `name or account_name or nickname or ""`
- `id` is the crosswalk key: the client looks up
  `portfolio.external_references(source_system='ALTRUIST', record_type='account', external_id=id)`
  (`services/altruist_identity.py:68-78`).
  - On a match it updates `last_seen` (`:81-98`).
  - Otherwise it writes one `account_import_exceptions` row, idempotent by
    `raw_row->>'external_id'` (`:127-176`).
  - **It never creates an account.**
- An empty `id` (both keys missing) produces `external_id = ""`. Nothing guards
  against that.

### 3.3 Positions: `GET {api_base}/v2/positions?account_id=<altruist account id>`

- One call per *resolved* account, enumerated from `external_references`
  (`services/altruist_positions_sync.py:438-457`, `:293-296`). Envelope key:
  `positions`, then `data`, then `results` (`services/altruist_oauth.py:869`).
- Per item (`_normalize_position`, `:690-710`). Candidate keys are listed in
  order, and **an `or` chain means a falsy first key (`0`, `""`) falls through
  to the next one**:

| normalized | raw keys tried | use (`services/altruist_positions_sync.py`) |
|---|---|---|
| `id` | `id`, `position_id` | external id `pos:{id}:{as_of}` (`:191-204`). Without it, the external id is a sha256 of account, identifier, quantity, market_value and as_of |
| `cusip` | `cusip` | asset identifier and security key (`:167-188`) |
| `isin` | `isin` | same |
| `ticker` | `ticker`, `symbol` | same |
| `name` | `name`, `security_name`, `description` | asset match and asset-creation name (`:254-271`) |
| `asset_type` | `asset_type`, `security_type` | `create_asset(asset_type=…)`, default `unclassified` (`:270`) |
| `quantity` | `quantity`, `units` | `_to_decimal`. Present means units basis, absent means value basis (`:313-322`) |
| `market_value` | `market_value`, `value` | `_to_decimal` → position market value |
| `cost_basis` | `cost_basis` | `_to_decimal` |
| `currency_code` | `currency_code`, `currency` | new asset's currency |

Numeric fields may be JSON numbers or strings, because `_to_decimal`
(`services/portfolio_import.py:226`) accepts strings with `$`, commas,
parentheses or `%`. A row needs at least a name or one identifier
(`services/altruist_positions_sync.py:261-265`).

### 3.4 Transactions: `GET {api_base}/v2/transactions?account_id=<altruist account id>`

Envelope key: `transactions`, then `data`, then `results`
(`services/altruist_oauth.py:912`). Per item (`_normalize_transaction`, `:713-738`):

| normalized | raw keys tried | use (`services/altruist_positions_sync.py`) |
|---|---|---|
| `id` | `id`, `transaction_id` | external id `txn:{id}`, no date suffix (`:207-220`). Also `external_ref` on the row (`:426`) |
| `type` | `type`, `transaction_type`, `activity_type` | lowercased and stripped, then mapped through `TRANSACTION_TYPE_MAP` (`:117-125`, `:357-364`) |
| `cusip` / `isin` | same name | locates the asset (`:366-387`) |
| `ticker` | `ticker`, `symbol` | same |
| `name` | `name`, `security_name`, `description` | security key only when no identifier is present |
| `trade_date` | `trade_date`, `date` | **required.** An ISO `YYYY-MM-DD` string goes through `date.fromisoformat` (`:401-412`) |
| `settle_date` | `settle_date` | optional ISO date |
| `quantity` | `quantity`, `units` | `_to_decimal` |
| `price` | `price` | `_to_decimal` |
| `gross_amount` | `gross_amount`, `amount` | `_to_decimal` |
| `fees` | `fees` | `_to_decimal` |
| `taxes` | `taxes` | `_to_decimal` |
| `net_amount` | `net_amount` | `_to_decimal` |
| `currency_code` | `currency_code`, `currency` | transaction currency |

Accepted `type` values: `buy`, `sell`, `dividend`, `interest`, `fee`,
`fee_expense`, `adjustment` (`:117-125`). Any other value becomes a per-row
error and is never guessed. A transaction is recorded only if a current
position exists for its asset under the account (`:389-399`), so a simulator
must return a matching position for each transacted security.

### 3.5 Call sequence for one sync

`POST /api/v1/altruist/sync`, the post-callback background task, or a resolved
webhook each run:
1. `require_active_connection` (`routers/altruist_connection.py:144`).
2. households → accounts × each household (`services/altruist_identity.py:198-211`).
3. A second `require_active_connection` (`services/altruist_positions_sync.py:480`).
4. For each resolved account: positions, then transactions (`:486-507`).

Every resource call also runs `require_active_connection` internally (`services/altruist_oauth.py:760`, etc.).

---

## 4. Webhooks (inbound, Altruist → us)

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

## 5. Field tables

Direction legend: req = we send; resp = counterparty sends; in = counterparty
sends to our endpoint.

**T1. Authorize + callback**

| field | dir | type | used for | status |
|---|---|---|---|---|
| `response_type=code` | req (query) | string | grant type | ASSUMED |
| `client_id` | req (query) | string | client identity | ASSUMED |
| `redirect_uri` | req (query) | https URL | callback target | ASSUMED |
| `state` | req (query) | url-safe string, 43 chars | CSRF; must be echoed back | ASSUMED |
| `scope=accounts:read` | req (query) | string | requested scope | ASSUMED |
| `code` | in (callback query) | string | token exchange | ASSUMED |
| `state` | in (callback query) | string | single-use state check | ASSUMED |

**T2. Token exchange (`POST /oauth/token`, authorization_code)**

| field | dir | type | used for | status |
|---|---|---|---|---|
| `Content-Type: application/x-www-form-urlencoded` | req hdr | — | form body | ASSUMED |
| `Accept: application/json` | req hdr | — | — | ASSUMED |
| `grant_type=authorization_code` | req body | string | — | ASSUMED |
| `code` | req body | string | — | ASSUMED |
| `redirect_uri` | req body | string | must equal the authorize value | ASSUMED |
| `client_id` | req body | string | client auth | ASSUMED |
| `client_secret` | req body | string | client auth (no Basic header) | ASSUMED |
| `access_token` | resp | string | Bearer for resource calls; required | ASSUMED |
| `refresh_token` | resp | string | stored; required on exchange | ASSUMED |
| `expires_in` | resp | int seconds | `token_expires_at`; default 3600 | ASSUMED |
| `scope` | resp | string | stored | ASSUMED |

**T3. Token refresh (`POST /oauth/token`, refresh_token)**

| field | dir | type | used for | status |
|---|---|---|---|---|
| `Content-Type` (form) | req hdr | — | — | ASSUMED |
| `Accept: application/json` | req hdr | — | — | ASSUMED |
| `grant_type=refresh_token` | req body | string | — | ASSUMED |
| `refresh_token` | req body | string | — | ASSUMED |
| `client_id` | req body | string | client auth | ASSUMED |
| `client_secret` | req body | string | client auth | ASSUMED |
| `access_token` | resp | string | required | ASSUMED |
| `refresh_token` | resp | string | optional; old one kept if absent | ASSUMED |
| `expires_in` | resp | int | expiry; default 3600 | ASSUMED |
| `scope` | resp | string | read, but `persist_refresh` does not store it (`:458-481`) | ASSUMED |

**T4. Households**

| field | dir | type | used for | status |
|---|---|---|---|---|
| path `/api/v2/households` | req | — | — | DOCUMENTED (Guides) |
| `Authorization: Bearer` | req hdr | — | auth | ASSUMED |
| `Accept: application/json` | req hdr | — | — | ASSUMED |
| list envelope (`households`, `data`, `results`, or bare array) | resp | array | item list | ASSUMED |
| `id` / `household_id` | resp item | string or number | accounts query parameter | ASSUMED |
| `name` / `household_name` | resp item | string | exception `raw_row` only | ASSUMED |

**T5. Accounts**

| field | dir | type | used for | status |
|---|---|---|---|---|
| path `/v2/accounts` | req | — | — | DOCUMENTED (Guides) |
| `household_id` | req (query) | string | scope to one household | DOCUMENTED (Guides) |
| `Authorization: Bearer` | req hdr | — | auth | ASSUMED |
| `Accept: application/json` | req hdr | — | — | ASSUMED |
| list envelope (`accounts`, `data`, `results`, or bare array) | resp | array | — | ASSUMED |
| `id` / `account_id` | resp item | string or number | crosswalk `external_id` | ASSUMED |
| `name` / `account_name` / `nickname` | resp item | string | exception row text | ASSUMED |

**T6. Positions**

| field | dir | type | used for | status |
|---|---|---|---|---|
| path `/v2/positions` | req | — | — | ASSUMED |
| `account_id` | req (query) | string | scope | ASSUMED |
| `Authorization: Bearer` | req hdr | — | — | ASSUMED |
| `Accept: application/json` | req hdr | — | — | ASSUMED |
| list envelope (`positions`, `data`, `results`, or bare array) | resp | array | — | ASSUMED |
| `id` / `position_id` | resp item | string | dedup external id | ASSUMED |
| `cusip` | resp item | string | asset match | ASSUMED |
| `isin` | resp item | string | asset match | ASSUMED |
| `ticker` / `symbol` | resp item | string | asset match | ASSUMED |
| `name` / `security_name` / `description` | resp item | string | asset match/create | ASSUMED |
| `asset_type` / `security_type` | resp item | string | new asset type | ASSUMED |
| `quantity` / `units` | resp item | number or string | units | ASSUMED |
| `market_value` / `value` | resp item | number or string | value | ASSUMED |
| `cost_basis` | resp item | number or string | cost | ASSUMED |
| `currency_code` / `currency` | resp item | ISO string | asset currency | ASSUMED |

**T7. Transactions**

| field | dir | type | used for | status |
|---|---|---|---|---|
| path `/v2/transactions` | req | — | — | ASSUMED |
| `account_id` | req (query) | string | scope | ASSUMED |
| `Authorization: Bearer` | req hdr | — | — | ASSUMED |
| `Accept: application/json` | req hdr | — | — | ASSUMED |
| list envelope (`transactions`, `data`, `results`, or bare array) | resp | array | — | ASSUMED |
| `id` / `transaction_id` | resp item | string | dedup, `external_ref` | ASSUMED |
| `type` / `transaction_type` / `activity_type` | resp item | string (7 legal values) | type code | ASSUMED |
| `cusip` | resp item | string | asset lookup | ASSUMED |
| `isin` | resp item | string | asset lookup | ASSUMED |
| `ticker` / `symbol` | resp item | string | asset lookup | ASSUMED |
| `name` / `security_name` / `description` | resp item | string | fallback security key | ASSUMED |
| `trade_date` / `date` | resp item | ISO date | required | ASSUMED |
| `settle_date` | resp item | ISO date | optional | ASSUMED |
| `quantity` / `units` | resp item | number or string | — | ASSUMED |
| `price` | resp item | number or string | — | ASSUMED |
| `gross_amount` / `amount` | resp item | number or string | — | ASSUMED |
| `fees` | resp item | number or string | — | ASSUMED |
| `taxes` | resp item | number or string | — | ASSUMED |
| `net_amount` | resp item | number or string | — | ASSUMED |
| `currency_code` / `currency` | resp item | ISO string | — | ASSUMED |

**T8. Webhook (inbound)**

| field | dir | type | used for | status |
|---|---|---|---|---|
| `X-Altruist-Signature` | in hdr | lowercase hex HMAC-SHA256 | authenticity | ASSUMED |
| raw body bytes | in | bytes | the signed message | ASSUMED |
| `event_id` / `id` / `eventId` / `webhook_id` (or under `data`) | in | string | dedup key | ASSUMED |
| `event_type` / `type` / `eventType` | in | string | stored only | ASSUMED |
| `account_id` / `accountId` / `external_account_id` / `household_id` / `householdId` / `resource_id` / `resourceId` | in | string | org lookup (account only) | ASSUMED |
| `environment` / `env` | in | string | which connection to sync; default `sandbox` | ASSUMED |
| `data` (object) | in | object | second lookup level | ASSUMED |

**T9. Legacy probe (`services/portfolio_altruist.py`, see §7)**

| field | dir | type | used for | status |
|---|---|---|---|---|
| `GET {ALTRUIST_BASE_URL}/accounts` | req | — | auth probe (`:132-143`) | ASSUMED |
| `limit=1` | req (query) | int | page size | ASSUMED |
| `Accept: application/json` | req hdr | — | — | ASSUMED |
| `X-Client-Id: <ALTRUIST_CLIENT_ID>` | req hdr | — | — | ASSUMED |
| `Authorization: Bearer <ALTRUIST_CLIENT_SECRET>` | req hdr | — | secret used as bearer | ASSUMED |
| status code + first 500 characters of body | resp | — | reported verbatim (`:155-175`) | ASSUMED |

**Count: 89 rows. 3 DOCUMENTED** (households path, accounts path, and the
`household_id` parameter, all "Guides only"). **86 ASSUMED.**

---

## 6. Simulator obligations for cpsim02

The minimal surface that lets every existing code path run **unchanged** once
`api_base`/`login_base` point at the simulator:

| # | must serve | minimal behaviour |
|---|---|---|
| 1 | `GET {login_base}/oauth/authorize` | Accept the 5 query parameters. Redirect (302) to `redirect_uri?code=<code>&state=<state echoed>` |
| 2 | `POST {login_base}/oauth/token` | Accept a form body. `grant_type` is `authorization_code` or `refresh_token`. Validate `client_id`/`client_secret` from the body (not Basic). Return JSON `{access_token, refresh_token, expires_in, scope, token_type:"Bearer"}`. Return `>= 400` on a bad code, secret or refresh token |
| 3 | `GET {api_base}/api/v2/households` | Bearer check. `{"households":[{"id","name"}]}` |
| 4 | `GET {api_base}/v2/accounts?household_id=` | `{"accounts":[{"id","name"}]}` filtered by household |
| 5 | `GET {api_base}/v2/positions?account_id=` | `{"positions":[…T6 fields…]}` |
| 6 | `GET {api_base}/v2/transactions?account_id=` | `{"transactions":[…T7 fields…]}`. `type` must be one of the 7 mapped values. Each security must also appear in positions |
| 7 | outbound webhook (simulator → `POST /api/v1/altruist/webhook`) | Body JSON with `event_id`, `event_type`, `account_id` (an id the org has already resolved) and `environment`. Header `X-Altruist-Signature` = lowercase hex `HMAC-SHA256(ALTRUIST_WEBHOOK_SECRET, body)` |

**cpsim02 status (2026-11-10): items 1–7 SERVED** by the simulator (`apps/api/cpsim/app.py`, `oauth.py`, `book.py`,
`webhooks.py`) at the client's exact paths under `/cpsim/c/<slug>`, from the uploaded book, and proven agreeing with
the EXISTING client by the conformance suite (`services/altruist_conformance.py`, checks C01–C20, each citing its row
here). Item 2's token endpoint additionally rotates refresh tokens; items 3–6 require an ACCESS token (the `cpsim_` key
authenticates only the token endpoint). Still ASSUMED on Altruist's side — the suite is what will prove or disprove
each row against the real sandbox.

| # | status (cpsim02) | conformance checks |
|---|---|---|
| 1 | served | C01, C02 |
| 2 | served (+ refresh rotation, single-use codes) | C03–C08 |
| 3 | served | C09, C10, C17 |
| 4 | served | C11 |
| 5 | served | C12, C15 |
| 6 | served (position rule enforced at upload) | C13, C14, C16 |
| 7 | served (second secret `ALTRUIST_SIMULATOR_WEBHOOK_SECRET`) | C19 |

Further obligations:
- Return all items in **one response**. The client never paginates, so a
  simulator page limit would silently truncate.
- Use the **primary** key names (`households`, `id`, `name`, `cusip`,
  `quantity`, …) so the conformance suite pins one shape. Alias keys are
  client tolerance, not contract.
- Give every position and transaction an explicit `id`. Without one, dedup
  falls back to content hashes.
- Use **ISO `YYYY-MM-DD`** for dates.
- Every response must also carry `X-Counterparty-Simulated: true`
  (`sprint_prompts/cpsim01.structural.md:48-49`). The client ignores unknown
  headers, so this is safe.
- Behaviours the client cannot currently observe, and so need not be
  simulated for parity: 401→refresh retry, 429/`Retry-After`, pagination
  cursors, `token_type`, and refresh-token expiry.

### What the client would need, to add a `simulator` environment (DONE in cpsim02 — see CPSIM02_NOTES D1–D6)

1. Add `ALTRUIST_HOSTS["simulator"] = {"api_base": "<api base>/cpsim/c/<slug>", "login_base": "<api base>/cpsim/c/<slug>"}`
   (`services/altruist_oauth.py:78-87`). The current dict is static and global,
   while a slug is per counterparty. One fixed slug, or a per-connection host
   lookup, is a design decision for cpsim02.
2. Add `"simulator"` to `ENVIRONMENTS` (`:100`). The router validator and
   `build_authorize_url` both gate on it (`routers/altruist_connection.py:68-74`,
   `services/altruist_oauth.py:260`).
3. **Fix `client_credentials` (`:245-246`).** Any environment that is not
   `sandbox` currently resolves to the `ALTRUIST_PRODUCTION_*` variables, so a
   `simulator` environment would silently send *production* credentials to the
   simulator. It needs its own branch (for example `ALTRUIST_SIMULATOR_*`, or
   the `cpsim_`-prefixed key from the registry).
4. `_redirect_uri` requires `https://` (`:238-241`). A local simulator
   round-trip needs an HTTPS API base or a tunnel.
5. The webhook has one global secret (`ALTRUIST_WEBHOOK_SECRET`) and no
   per-environment secret, so simulator-signed webhooks would need the
   production secret. The receiver would need an environment-aware or
   counterparty-aware secret lookup.
6. The `cpsim.may_connect(org, household)` gate
   (`sprint_prompts/cpsim01.structural.md:42-47`) would be called from
   `altruist_connect` before an authorize URL is issued for `simulator`.

---

## 7. Open questions and divergences

1. **Two independent Altruist clients.** `services/portfolio_altruist.py` (Phase
   B) differs from `altruist_oauth.py` in every respect:
   - it reads global `ALTRUIST_CLIENT_ID` / `_CLIENT_SECRET` / `_BASE_URL` (`:54-58`)
   - it calls `{BASE_URL}/accounts` without `/v2` (`:132`)
   - it sends `limit=1`, which the newer client never sends
   - it sends a non-OAuth `X-Client-Id` header and uses **the client secret as
     the Bearer token** (`:140-141`)

   It is still live behind `GET /api/v1/portfolio/altruist/status`
   (`routers/portfolio_ingest.py:363-384`). Neither client knows about the
   other's variables.
2. **Environment name:** the code says `sandbox`, the cpsim01 prompt says
   `stage`. The conformance suite should use the code's key.
3. **Path prefix inconsistency:** `/api/v2/households` versus `/v2/accounts`,
   self-flagged in `services/altruist_oauth.py:632-634`. Positions and
   transactions copied the `/v2` form as a guess.
4. **Production credentials on unknown environments** (§6 item 3). **CLOSED (cpsim02):** explicit branches; any other
   environment raises; `check_destination` refuses cross-environment credentials both ways.
5. **`REFRESH_TOKEN_LIFETIME` is unused.** Nothing records or checks refresh
   expiry, so a 30-day-stale connection shows `connected` until a refresh is
   refused.
6. **`scope` is dropped on refresh.** `persist_refresh` does not write it.
7. **Falsy `or` chains:** a `quantity` or `market_value` of `0` falls through to
   `units` or `value` (`services/altruist_oauth.py:705-706`).
   `_first_present` does the same for webhook fields
   (`services/altruist_webhook.py:91`).
8. **Webhook household ids can never resolve.** They are extracted, but the
   lookup is account-only (`services/altruist_webhook.py:171`).
9. **The webhook trusts the payload's `environment`** to choose which
   connection to sync, and defaults to `sandbox` even for production traffic
   (`services/altruist_webhook.py:126-128`, `:226`). **CLOSED for the simulator (cpsim02):** the simulator secret
   FORCES `simulator`, and a real-secret payload claiming `simulator` is refused. **Still open for real traffic:**
   sandbox vs production is still read from the payload (one real secret for both).
10. **The webhook response leaks the internal `org_id`** to the caller
    (`services/altruist_webhook.py:237`). **CLOSED (cpsim02):** the router pops it before answering
    (`routers/altruist_webhook.py`); it is still used internally for the credit events.
11. **`external_references` is not environment-scoped**
    (`docs/PROJECT_STATUS.md:7332-7337`). A sync in one environment re-syncs
    every resolved account. **CLOSED for the simulator (cpsim02):** the simulator crosswalks under
    `ALTRUIST_SIMULATOR`, the real environments under `ALTRUIST`, so neither touches the other's accounts. **Still open
    between sandbox and production** (both `ALTRUIST`).
12. **No web caller exists.** **Still OPEN (cpsim02 routed around it, D4):** the simulator connects server-to-server;
    the real browser leg is logged in OUTSTANDING_TODO_LIST. Nothing in `apps/web` references
    `/altruist/connect|callback|status|sync`. The callback is an authenticated
    API route, while a browser redirect from an OAuth server carries no bearer
    token, so the real redirect leg has never been wired end to end. cpsim02
    will hit this first.
13. **Unwrapped parse errors:** a non-JSON body (`response.json()`) or a
    non-integer `expires_in` raises outside `AltruistOAuthError`. The callback
    returns 500, not 502.
14. **The callback leaks state-rejection detail.** It surfaces `str(exc)`
    (`routers/altruist_connection.py:229`), so expired and already-used states
    are distinguishable, as Sprint 1 warned (`docs/PROJECT_STATUS.md` Sprint 1
    entry).
15. **No error-parameter handling** on the callback (`error`,
    `error_description`).

---

## 8. Orders (ASSUMED — no published Altruist trading API seen)

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

## 9. Transfers (ASSUMED — no published Altruist money-movement API seen)

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

### 9.4 Feed transaction types for cash (`GET /v2/transactions`) — ASSUMED

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
