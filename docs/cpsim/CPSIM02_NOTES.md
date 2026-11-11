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
- Numbers: `services.portfolio_import._to_decimal` — the client's own parser (contract §3.3), so the simulator accepts
  exactly what the client will read back. `custody.base.parse_decimal` is NOT used: it quantizes to cents, which would
  round share quantities.
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

Filled in per task below; each says where it lives.

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
| Demo rule | `cpsim.may_connect` (cpsim01) | reached from services through ONE thin public definer wrapper (no second copy of the rule) |

## 4. Differences from the prompt (summary)

1. Token calls had no `transport` — added (default `None`).
2. Environment CHECKs on `altruist_connections` / `altruist_oauth_states` had to be extended (not named by the prompt).
3. Only `positions` carries a source CHECK; external_references and transactions have none.
4. The settings key list is `ADMIN_ROUTER_ONLY_SETTING_KEYS`.
5. The services cannot call `cpsim.may_connect` (no USAGE on schema cpsim, by design) — a public SECURITY DEFINER
   wrapper `public.counterparty_sim_may_connect(org, household)` calls it, so the rule exists once.

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
