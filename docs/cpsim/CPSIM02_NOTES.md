# cpsim02 — discovery notes, decisions as built, reuse inventory

Sprint cpsim02 (2026-11-10/11), branch `cpsim02-work`. The simulated custodian ANSWERS: an Altruist-compatible read
API served from an uploaded BOOK, webhooks out, the existing Altruist client pointed at it through a third environment
`simulator`, and the conformance suite. Design: `COUNTERPARTY_SIMULATOR_DESIGN_v1.md`; contract: `ALTRUIST_CONTRACT.md`;
cpsim01 notes: `CPSIM01_NOTES.md`; operator guide: `CPSIM_ADMIN.md`.

Paths are relative to `apps/api/` unless they start with `docs/` or `apps/web/`.

## 1. Findings (Task 1) — confirmed, and what differed from the prompt

### 1a. Confirmed against HEAD (`122ac75`)

- `ALTRUIST_HOSTS` keys `sandbox` / `production` (`services/altruist_oauth.py:78-87`); `ENVIRONMENTS = ("sandbox",
  "production")` (`:100`); `client_credentials()` (`:245-256`) picks `ALTRUIST_SANDBOX_*` for `sandbox` and
  `ALTRUIST_PRODUCTION_*` for **any other value** — confirmed, the §7.4 hazard is real.
- Resource calls: one GET each, Bearer + Accept only, 15 s, optional `transport`, `_extract_list(primary, data,
  results)`; paths `/api/v2/households`, `/v2/accounts`, `/v2/positions`, `/v2/transactions` (`:646-651`).
- **Differs:** `exchange_code_for_tokens` (`:283`) and `refresh_access_token` (`:322`) take **no `transport`** — only
  the four resource calls do. cpsim02 adds an optional `transport` (and the resolved hosts) to both, default `None`
  (production callers unchanged). Without it the token leg could not be driven in-process.
- `_redirect_uri` requires https (`:230-242`) — the simulator's authorize step validates the same value.
- Identity (`services/altruist_identity.py:62-78`): crosswalk `source_system='ALTRUIST'`, `record_type='account'`;
  match → `last_seen`, else one `account_import_exceptions` row (idempotent by `raw_row->>'external_id'`); never
  creates an account. Confirmed.
- Webhook (`services/altruist_webhook.py:72-85, 126-128, 157-172, 237`): one global secret, payload environment
  (default sandbox), account-only lookup across orgs, response includes `org_id`. Confirmed (§7.9, §7.10).
- **Differs:** the org-settings key list is `ADMIN_ROUTER_ONLY_SETTING_KEYS` (`services/org_settings.py:86`), not
  `SUPER_ADMIN_ONLY_SETTING_KEYS`. Same behaviour (generic path refuses 403). cpsim02 adds
  `counterparty_sim.altruist_counterparty` to it.

### 1b. Every read site, and the CHECKs (from the catalog, 2026-11-10, as postgres)

| Symbol | Read at |
|---|---|
| `ALTRUIST_HOSTS` | `services/altruist_oauth.py:264, 292, 332, 762, 803, 846, 889` |
| `ENVIRONMENTS` | `services/altruist_oauth.py:260`; `routers/altruist_connection.py:41, 69, 72` |
| `client_credentials` | `services/altruist_oauth.py:262, 290, 331`; `routers/altruist_connection.py:240` |
| `SOURCE_SYSTEM_ALTRUIST` (`'ALTRUIST'`, the crosswalk) | `services/altruist_identity.py:77, 97, 112, 123, 140`; `services/altruist_positions_sync.py:302, 327, 350, 429, 455`; `services/altruist_webhook.py:171` |
| `source_system='altruist'` (ledger tag) | `services/altruist_positions_sync.py:99` (`SOURCE_SYSTEM`, positions + transactions); `services/portfolio_assets.py:156` (`SOURCE_SYSTEMS`); `services/org_settings.py:256` (default precedence order); `services/integrated_import.py:318` (`supersede_for_account` default) ; `services/fee_narratives.py:141`; `services/portfolio_altruist.py:62` (legacy client) |

Catalog (`pg_constraint`, namespace + relname):

| Table | CHECK on source / environment |
|---|---|
| `portfolio.external_references` | **none on `source_system`** (only `ext_ref_record_type_chk`); UNIQUE `(org_id, source_system, external_id, record_type)` |
| `portfolio.positions` | `positions_source_chk`: `reporting_tool_bd, …_addepar, …_orion, …_apx, …_import, altruist, spv_subscriptions, chancery, manual` |
| `portfolio.transactions` | **none** on `source_system` (Python applies `SOURCE_SYSTEMS`) |
| `public.account_import_exceptions` | none on source (only `record_kind`, `source_row`) |
| `public.altruist_connections` | **`altruist_connections_environment_check`: `sandbox, production`** |
| `public.altruist_oauth_states` | **`altruist_oauth_states_environment_check`: `sandbox, production`** |

**Differs (D5):** of the three tables the prompt named, only `positions` has a source CHECK. The crosswalk token
`ALTRUIST_SIMULATOR` needs no DDL (external_references has no CHECK). The ledger tag for simulator-fed rows is
`altruist_simulator` — added to `positions_source_chk` and to `SOURCE_SYSTEMS` (so transactions, which are checked in
Python only, admit it too). **Not in the prompt but required:** the two **environment** CHECKs above refuse
`simulator`; without extending them the simulator environment could never store a state or a connection. cpsim02
extends each with `simulator` only (nothing else changed).

### 1c. Gates

`routers/altruist_connection.py` `_require(request, permission)` → `services.rbac.require_permission(pool, user_id,
org_id, permission)` (the DB Principal path since AUTHZ1; `docs/AUTHZ1_ROUTES.md:233-238`):

| Route | Permission |
|---|---|
| `POST /altruist/connect` | `manage_custody_connections` |
| `GET /altruist/callback` | `manage_custody_connections` |
| `GET /altruist/status` | `view_custody_connections` |
| `POST /altruist/sync` | `manage_custody_connections` |
| `POST /altruist/disconnect` | `manage_custody_connections` |
| `POST /altruist/webhook` | public (HMAC) |

The callback validates state with `consume_oauth_state(conn, org_id, state)`: row by `(state, org_id)`; missing /
used / expired → `AltruistOAuthError` → 400; single use via `UPDATE … WHERE used_at IS NULL RETURNING`.

### 1d. Custody import reuse (the boundary forbids importing it INTO cpsim)

`routers/custody_import.py` + `services/custody/{base,csv_adapter,importer}.py`. Reusable pieces, used in the ADMIN
ROUTER (which then hands plain rows to the package):

- `services.custody.base.parse_text` — text cells → stripped or None.
- `routers/custody_import.MAX_UPLOAD_BYTES` pattern / `_read_upload` (empty → 400, too large → 413). The book limit is
  its own constant (2 MB — a book, not a custodian export); the shape of the check is the same.
- The CSV decoding approach of `csv_adapter._load` (utf-8-sig, `csv.DictReader`). The sniffer is NOT reused: the
  template is comma-separated by definition, and a sniffed `;` would silently re-shape a book.
- Numbers: NOT `services.portfolio_import._to_decimal` (the client's lenient parser — `$`, commas, `%`) and NOT
  `custody.base.parse_decimal` (quantizes to cents, which would round share quantities). D8 says "decimal strings or
  numbers", so the package has its own strict parser (`cpsim/book.py` `_num`: a JSON number or `^-?\d+(\.\d+)?$`); it
  could not import either anyway (boundary B3). Whatever the simulator accepts, `_to_decimal` reads back.
- Dates: ISO only (D8). `custody.base.parse_date` is NOT used: it accepts eight formats including `%d/%m/%Y`.
- Not reusable: `build_plan` / `commit_plan` (they write `public.accounts` / balances for a real custodian file).

### 1e. The six Altruist verifies

See §6 below (filled from a read of each script; none was run in discovery).

### 1f. menuvisibility_harness.mjs

`scripts/menuvisibility_harness.mjs:164-175` describes each gate as `staff…` / `perm…` / else `roles.join` — a gate
with neither `staff` nor `perm` nor `roles` crashes (`i.gate.roles` undefined). Every gate shape `apps/web/lib/
menuVisibility.mjs` defines: `{roles}` (GATE_SUPER_ADMIN), `{perm}`, `{perm, strict}`, `{staff, strict}`,
`{staff, perm, strict}`, `{internal, strict}`, `{internal, perm, strict}`, `{anyOf, strict}`, `{adminArea}`. The three
that crash are `{internal, strict}`, `{anyOf, strict}` and `{adminArea}`. Reproduced: `node
scripts/menuvisibility_harness.mjs` → `TypeError … reading 'join'` at `:172`.

## 2. Decisions as built (D1–D11)

| D | As built | Where |
|---|---|---|
| D1 | `ENVIRONMENTS = ("sandbox", "production", "simulator")`. `client_credentials` has three explicit branches (`ALTRUIST_SANDBOX_*`, `ALTRUIST_PRODUCTION_*` only for `== "production"`, `ALTRUIST_SIMULATOR_*`); any other value raises `AltruistConfigError` (never a fallback). Refusals both ways: a simulator secret without `cpsim_` is never sent; `check_destination` refuses sandbox / production credentials to a host carrying `/cpsim/c/`, simulator credentials to a real host, and a `cpsim_` secret as real credentials. | `services/altruist_oauth.py` `client_credentials`, `check_destination` |
| D2 | `counterparty_sim.altruist_counterparty` (a slug string, or null) — in `ADMIN_ROUTER_ONLY_SETTING_KEYS` and in the same DB trigger (`cpsim_org_setting_guard`, now also refusing a non-slug value). Hosts resolved PER CALL (`resolve_hosts`): sandbox / production read the static table exactly as before (no query); simulator = `CPSIM_BASE_URL` + `/cpsim/c/<slug>` for both bases, https required, re-checking the allowed flag on every call (turning it off stops the next call). No flag / no counterparty → `AltruistSimulatorRefused` → 409 with the reason, before any call. | `altruist_oauth.simulator_counterparty`, `simulator_hosts`; `routers/altruist_connection._require_simulator` |
| D3 | client_id = the live api_key credential's id (`cpsim.counterparty_credentials.id`, shown on the admin page); client_secret = the `cpsim_live_` key. Nothing stored in Doppler by the sprint. | `cpsim/oauth.py` `_client` |
| D4 | `POST /altruist/connect {environment: simulator}`: state row → `build_authorize_url` → GET (no redirect following) → `code` + echoed `state` from the Location → `complete_callback` (the SAME function the browser callback now calls: state check, exchange, persist, auto-sync). Same gate (`manage_custody_connections`) + the D2 refusal. The browser leg is untouched (§7.12, logged). | `routers/altruist_connection.py` `_connect_simulator`, `complete_callback` |
| D5 | Crosswalk source per environment: `ALTRUIST` (sandbox / production, unchanged) vs `ALTRUIST_SIMULATOR`, in identity, positions + transactions sync, and the webhook lookup. Ledger tag `altruist_simulator` (positions CHECK + `SOURCE_SYSTEMS`). Unmatched simulator accounts use their own reason code `altruist_simulator_account_unmatched` (so the two environments' exceptions can never satisfy each other's idempotency probe). A simulator account crosswalked to a household that fails the may-connect rule → one `not_a_demo_household` exception (identity) AND a skip in sync (`SyncResult.refused_accounts`) — two independent stops. | `services/altruist_identity.py`, `services/altruist_positions_sync.py`, `services/altruist_webhook.py` |
| D6 | `verify_signature_source`: the real secret first (identical to `verify_signature`), then `ALTRUIST_SIMULATOR_WEBHOOK_SECRET` only if it is configured with `cpsim_whsec_`; a simulator match FORCES environment simulator; a real-secret payload claiming simulator is logged (`altruist_webhook_events`) and answered `refused_environment` (never dispatched). The simulator signs with the same Doppler secret, read by `cpsim/webhooks.py` from its own environment; missing → `not_sent`, request_log `unsigned`. | `services/altruist_webhook.py`, `routers/altruist_webhook.py`, `cpsim/webhooks.py` |
| D7 | `cpsim.book_households / _accounts / _positions / _transactions` (valid axis, one live row per counterparty + id) + `cpsim.book_uploads` (append-only: cpsim_app holds SELECT + INSERT only, and a trigger refuses UPDATE / DELETE except the owner with `cpsim.allow_teardown = 'fixture'`). Replace mode = close the live rows of every owner with at least one ACCEPTED row (a fully rejected file never empties the book — a refinement of "the accounts the file names"). Ids: given (validated) or `sim-` + 16 hex of the content (stable). | `docs/cpsim02_part1.sql`, `cpsim/book.py` |
| D8 | One security per row via identifiers (CUSIP, ISIN, ticker): any unmatched identifier → `unknown_identifier`, several live securities → `ambiguous_identifier`, two identifiers naming different securities → `identifiers_disagree`; cash rows (`row_type = cash`) are the documented exception; blank market value → quantity × the last platform close on or before the as-of date, else `no_price`; the client's 7 types; the position rule; ISO dates; 3-letter currency. 21 reason keys, each with a label (the admin page's rejection table). | `cpsim/book.py` `REASONS`, `validate` |
| D9 | Positions: the latest snapshot on or before the clock date; transactions: trade_date ≤ the clock date; primary keys, explicit ids, ISO dates, one response, SIMULATED header. Behaviour on the four RESOURCE routes only (not the token endpoint): reject → 503 `{detail: reason}`; delay → `sleep(min(delay_seconds, 20))` after the database work (no connection held); partial / ACH ignored. Resources need an ACCESS token — the `cpsim_` key itself is refused there. | `cpsim/app.py` `_resource` |
| D10 | `account.positions_updated` / `account.transactions_updated`, compact-JSON body `{event_id, event_type, account_id, environment: "simulator", occurred_at}`, target `CPSIM_BASE_URL` + `/api/v1/altruist/webhook` or the https override (`counterparty_behaviour.webhook_url`), 1 attempt + 2 retries (5xx / transport error) with backoff, every attempt in request_log (direction out, status, latency). Sent after an accepted positions / transactions upload and by "Send test event" — BOTH only when webhooks are on (the test event refuses 409 when off). | `cpsim/webhooks.py` |
| D11 | `services/altruist_conformance.py` (the suite core, no DB, no simulator import: it drives `build_authorize_url`, `exchange_code_for_tokens`, `refresh_access_token` and `fetch_resource` — the one request path every `call_*` now uses — and pins the raw primary keys) + `scripts/cpsim_altruist_conformance.py` (in-process fixture run / `--base-url` / `--target sandbox --i-have-sandbox-access`, refusing with exit 2 otherwise; table + `logs/cpsim_conformance_<date>.json`; exit 1 on a failure). The admin page runs the same core in-process and keeps the last 50 runs in `cpsim.conformance_runs`. | as named |

## 2a. The in-process conformance run (Task 7)

`python3 apps/api/scripts/cpsim_altruist_conformance.py --target simulator` (2026-11-10, fixture counterparty
`cpsimconf-…`, created and torn down by the run; fixture book included one live security): **20 PASS, 0 FAIL** —
C01 authorize 302 + state · C02 unknown client_id refused · C03 the client's exchange · C04 used code refused · C05 raw
token fields + Bearer · C06 wrong secret refused · C07 refresh rotates both · C08 spent refresh refused · C09
households primary shape · C10 one response · C11 accounts per household · C12 positions T6 fields · C13 transactions
T7 fields + types · C14 position rule · C15 explicit ids · C16 ISO dates · C17 Bearer check · C18 SIMULATED header ·
C19 webhook signature verified by the receiver's own code (§6.7) · C20 no network. Leftover fixture rows: 0.

## 3. Reuse inventory

| Piece | Existing construct | Used how |
|---|---|---|
| OAuth client, resource calls | `services/altruist_oauth.py` | extended (simulator env, per-call hosts, optional transport); same functions drive the conformance suite |
| Identity, sync | `services/altruist_identity.py`, `services/altruist_positions_sync.py` | same functions; the crosswalk source and ledger tag are chosen per environment |
| Connect / sync routes | `routers/altruist_connection.py` | same routes + gate; `simulator` adds the flag check and the server-to-server authorize step (D4) |
| Webhook receiver | `services/altruist_webhook.py`, `routers/altruist_webhook.py` | second secret, forced environment, no org_id in the response |
| Parsing | `custody.base.parse_text`, `portfolio_import._to_decimal` | the book upload, in the admin router |
| Audit | `public.audit_log` | `counterparty.book_*`, `counterparty.connection_*`, `counterparty.webhook_*`, `counterparty.conformance_run` |
| Super Admin gate, cpsim role | `services.authz.require_super_admin`, `cpsim.db.as_simulator` | every new admin route |
| Demo rule | `cpsim.may_connect` (cpsim01) | reached from services through ONE thin public definer wrapper `public.counterparty_sim_may_connect` (no second copy of the rule) |
| Callback handling | `routers/altruist_connection.altruist_callback` | factored into `complete_callback`, called by both the browser route and the D4 server-to-server connect |
| Resource requests | the four `call_*` bodies | one shared `fetch_resource` (byte-identical requests, proven against the pre-sprint module) — the conformance suite drives it too |
| In-process timeouts | — (httpx's ASGITransport ignores them) | NEW `scripts/_cpsim_fixtures.TimeoutASGITransport` turns the client's read timeout into `httpx.ReadTimeout` |
| Teardown | `_verify_cleanup.cleanup` | the upload-log trigger honours its `'fixture'` GUC convention (`gucs=("cpsim.allow_teardown",)`) |

## 4. Differences from the prompt (summary)

1. Token calls had no `transport` — added (default `None`), plus optional `hosts` / `credentials` / `redirect_uri`
   overrides (the simulator's per-org hosts; the conformance suite's pasted key). Omitted, every value is read exactly
   as before.
2. Environment CHECKs on `altruist_connections` / `altruist_oauth_states` had to be extended (not named by the prompt).
3. Only `positions` carries a source CHECK; external_references and transactions have none.
4. The settings key list is `ADMIN_ROUTER_ONLY_SETTING_KEYS`.
5. The services cannot call `cpsim.may_connect` (no USAGE on schema cpsim, by design) — a public SECURITY DEFINER
   wrapper `public.counterparty_sim_may_connect(org, household)` calls it, so the rule exists once. The boundary list
   was extended for NEW boundary files only: `scripts/_apply_cpsim02.py`, `scripts/_cpsim_fixtures.py`,
   `scripts/cpsim_altruist_conformance.py` (B1 regex). No service imports the package.
6. **Supabase's `postgres` is not a superuser** — the first draft of the append-only trigger let "a superuser" tear
   down fixture rows and so matched no one (the Task 7 run left its fixture behind until fixed). The exception is now
   the table OWNER with the shared cleanup's `'fixture'` switch.
7. `CPSIM_BASE_URL` must be https always — the verify uses `https://cpsim.verify.invalid` with an in-process transport
   (never resolved), so no http exception exists in the code.
8. The admin Connections panel cannot call the /altruist ROUTES for another org (their org comes from the caller's
   session), so it calls the SAME functions (`_connect_simulator`, `complete_callback`, `_run_sync`) inside the target
   org's RLS context (`set_rls_context(target, super_admin=True)`), with the post-connect sync run inline. Nothing in
   the admin router writes a portfolio table.
9. `verify_cpsim01.py` froze cpsim's table set and cpsim_app's privilege set to exactly its own nine tables, and the
   `ADMIN_ROUTER_ONLY_SETTING_KEYS` literal; cpsim02 legitimately grows all three. verify_cpsim01 now requires its own
   nine (exact privileges) and accepts any other cpsim table only if a later `docs/cpsim*_part*.sql` declares it; the
   settings key is matched by name. Verify-only.
10. `verify_altruist_sprint6` asserted the webhook RESPONSE carries `org_id` (6-2b, 6-5b) — removed on purpose (§7.10);
    both checks now assert its absence and the org on the persisted row (6-2c / 6-5c, unchanged). S2 / S3 / S5 / S6
    gained a final `TOTAL:` line (counts unchanged).
11. **menuvisibility_harness** (Task 9): the crash is fixed (`describeGate` covers every shape) and the legacy-rule
    hand floor is limited to items the legacy rule still governs. The harness now RUNS, which unmasks pre-existing
    staleness it had been hiding: the navregroup persona snapshot fails for `staff_member`, `org_admin_manage_org_settings`
    and `super_admin` (persona permission fixtures predate later sprints; the traced-gate model has no Super Admin
    bypass for `internal_perm` / scoped routes; `/admin/workflows` moved behind a strict gate). Fixing those means
    re-deriving persona data other verifies own — out of a "harness only, no gate change" task. Recorded [FIND],
    logged in OUTSTANDING_TODO_LIST. Standalone: exit 0, every gate described, none unknown.
12. Fee narratives label every `source_system` from config rows (`services/fee_narratives.LABELLED_DOMAINS`); no label
    row exists for `altruist_simulator` (seeding one would write real config). A narrative over a simulator-fed
    position would fail loudly rather than print the raw token — test / demo orgs only. Logged.
13. The webhook account lookup is `LIMIT 1` across orgs (unchanged): two orgs crosswalking the SAME simulator account id
    would resync only one. The verify keeps their ids apart; logged.

## 6. Altruist verifies — offline status (1e)

Read, not run (2026-11-10). "Real network" = an unmocked client call that opens a socket.

| Script | Real network? | TOTAL line | VERIFY_NESTED | Last recorded result | cpsim02 regression? |
|---|---|---|---|---|---|
| sprint1_openapi_connection | **YES** — `:322` `call_households(sandbox)` unmocked (on purpose: proves past the guard; passes on 401 or unreachable) | no (`name: P/T PASS`) | no | 24/26 + 1 FIND + 1 BLOCKED (2026-09-06) | **no** — opens a socket to the real Altruist stage host; "never call real Altruist" |
| sprint2_identity_resolution | no (MockTransport; unmocked calls stop at the guard) | no | no | 19/20 + 1 BLOCKED | **yes** |
| sprint3_positions_transactions_sync | no (same) | no | no | 21/22 + 1 FIND + 1 BLOCKED | **yes** |
| sprint4_connection_api | **YES** — the `/altruist/callback` at `:511` schedules `_run_auto_sync`, which calls the real `call_households` (not patched; the error is swallowed) | no | no | 26/27 + 1 BLOCKED | **no** — same reason; logged in OUTSTANDING_TODO_LIST (patch the auto-sync) |
| sprint5_sync_orchestration | no in-process (all four client functions patched where they are called); its own regression block re-runs S1–S4 unless nested | no | yes | 32/34 + 1 FIND + 1 BLOCKED | **yes** (nested) |
| sprint6_realtime_webhook_receiver | no in-process (same patches); regression block skipped when nested | no | yes | PROJECT_STATUS stale ("discovery only"); commit `09b0340` says 30/33 + 1 BLOCKED | **yes** (nested) |

None prints `TOTAL: N PASS, M FAIL`, so the shared nested-run parser cannot read them. cpsim02 adds that one final
line to S2, S3, S5 and S6 (verify-only change, counts unchanged). S6 asserts the webhook RESPONSE carries `org_id`
(`:459` 6-2b, `:590` 6-5b) — the §7.10 fix removes it on purpose, so those two checks move to the DB row the script
already reads (`:469`, `:599`). Nothing in any of the six asserts `ENVIRONMENTS` or the production fallback exactly;
S3/S5/S6 depend on the single token `'altruist'`, which is unchanged for sandbox / production.
