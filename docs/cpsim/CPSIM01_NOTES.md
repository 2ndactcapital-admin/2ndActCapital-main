# cpsim01 — discovery notes and reuse inventory

Sprint cpsim01 (2026-11-10), branch `cpsim01-work`. Design: `docs/cpsim/COUNTERPARTY_SIMULATOR_DESIGN_v1.md`.
Contract for cpsim02: `docs/cpsim/ALTRUIST_CONTRACT.md`. Operator / admin guide: `docs/cpsim/CPSIM_ADMIN.md`.

## 1. Findings (Task 1) — what differed from the prompt

- **The Altruist "stage" environment is keyed `sandbox`.** `services/altruist_oauth.py:78` — `ALTRUIST_HOSTS` has
  `sandbox` (hosts `openapi.stage1.altruistnet.tech` / `oauth.stage1.altruistnet.tech`) and `production`;
  `ENVIRONMENTS = ("sandbox", "production")` (`:100`). The prompt's "stage" is the hostname, not the key.
- **Adding a third environment is NOT a one-line change.** `client_credentials()` (`:245`) picks
  `ALTRUIST_SANDBOX_*` for `sandbox` and `ALTRUIST_PRODUCTION_*` for *anything else* — a `simulator` environment
  added naively would send the production client id / secret to the simulator. cpsim02 must branch it explicitly
  (and the simulator must refuse any secret without the `cpsim_` prefix — it already does: `cpsim/auth.py`).
- **"No live call ever made" is almost true.** Sprint 1's verify reached the stage1 host with synthetic credentials
  and got a 401 (`docs/PROJECT_STATUS.md`). No response *shape* has ever been observed — 86 of 89 contract fields are
  ASSUMED (`ALTRUIST_CONTRACT.md`).
- **The OAuth callback cannot be reached by a real browser redirect** (it is an authenticated API route; nothing in
  `apps/web` calls `/altruist/*`). cpsim02 meets this first.
- **A second, older Altruist client exists** (`services/portfolio_altruist.py`, `GET /portfolio/altruist/status`)
  with different env vars, paths without `/v2` and the client secret as a Bearer token. The simulator targets the
  newer client only.

## 2. Reference tables the simulator may read (exact names)

| Table | Why | Privilege |
|---|---|---|
| `portfolio.securities_global` | the security master (names, types, currency) | SELECT |
| `portfolio.securities_global_identifiers` | ticker / CUSIP / ISIN lookup for uploaded books (cpsim02) | SELECT |
| `portfolio.securities_global_prices` | last price for fills / valuations (cpsim03) | SELECT |

RLS on all three is `TO public`: the security master's SELECT policy is `true`; prices are visible where
`org_id IS NULL` (platform prices) or the org GUC matches — under `cpsim_app` with no org GUC the simulator sees the
platform prices only, which is correct (a simulated custodian has no tenant).

`cpsim_app` holds **nothing else** in `public`. `public.audit_log` is written by the admin router as `app_service`
(after `RESET ROLE`, in the same transaction) — so `cpsim_app` needs no audit privilege, and the simulator package
writes no non-cpsim table at all (stricter than the prompt's "except audit_log").

## 3. The mount and the middleware order (main.py)

- Starlette runs the LAST-registered `@app.middleware` outermost: `auth0_jwt_middleware` (outer) →
  `rls_context_middleware` (inner) → the routes. **Middleware wraps mounted sub-apps too** — a mount is just a route.
  Without a change, every `/cpsim/*` request would be refused 401 by `auth0_jwt_middleware` ("Missing or malformed
  Authorization header") or, worse, a `Bearer cpsim_…` key would be handed to `verify_token`.
- Change: `main.SIMULATOR_PREFIX = "/cpsim"` and `_is_simulator(path)`; `auth0_jwt_middleware` passes a simulator path
  straight through (no Hollisworks token is read), and `rls_context_middleware` treats it like a public path (no
  principal, no org context). The sub-app then does its OWN auth (`cpsim/auth.py`: its fake keys only). A Hollisworks
  JWT sent to `/cpsim/...` is just a bearer value without the `cpsim_` prefix → 401.
- App-level `dependencies=[Depends(_family_path_guard)]` apply to the app's own routes, not to a mounted sub-app
  (FastAPI dependencies are per-route). CORS wraps everything (harmless: the simulator is server-to-server).
- Mount: `app.mount("/cpsim", cpsim.app.create_app())` — `render.yaml` unchanged (same `uvicorn main:app`).
- `scripts/authz1_routes.py` flattened `app.routes`; a `Mount` would have counted as an app route without a row
  (verify_authz1 asserts `rows == routes − docs`). It now lists mounted simulator routes in their own section of
  `docs/AUTHZ1_ROUTES.md` ("Simulator routes — own auth") and a separate `simulator_routes` key in the JSON; the
  Hollisworks route rows are unchanged.

## 4. Admin nav and the Super Admin gate

- API: `services.authz.require_super_admin(request)` — the ONE gate (`routers/security_master.py` is the model:
  gate first, `platform_scope`-style transaction, permission envelope with `can_*` flags and vocabularies).
- Web: one menu definition `apps/web/lib/menuVisibility.mjs` — `tab("platform", "controls", n, path, label,
  GATE_SUPER_ADMIN, true)`; pages are server components (`getHostSession`, `fetchAPI`), clients call a Next.js proxy
  route under `app/api/admin/...` that forwards with the caller's own token (Rule 5). Render tests:
  `node:test` + `react-dom/server` + `tests/market/jsxLoader.mjs` (pattern: `tests/secmaster1`).

## 5. org_settings storage

`public.org_settings (org_id, setting_key, setting_value jsonb, category, is_public, updated_by, ...)`, UNIQUE
`(org_id, setting_key)`, plain upsert (not bi-temporal — `services/org_settings.py` docstring). The generic writer
`set_setting` lets an **org admin** write any key of their own org — so `counterparty_sim.allowed` is:
- refused by `services.org_settings` for the generic path (`SUPER_ADMIN_ONLY_SETTING_KEYS`, a 403), and
- refused by the database unless `app.is_super_admin = 'true'` (trigger `cpsim_org_setting_guard_trg`), and
- written only by `routers/admin_counterparties.py` (Super Admin, audited `counterparty.org_setting_changed`).
Default false = no row. The audit trail is `audit_log`, because the settings table keeps no history.

## 6. Reuse inventory

| Piece | Existing construct | Used how / why new |
|---|---|---|
| Security master, prices | `portfolio.securities_global*` | READ only (SELECT grants above) |
| Audit log | `public.audit_log` | written by the admin router, same transaction, `counterparty.*` actions |
| Super Admin gate | `services.authz.require_super_admin` | every admin route |
| Business days | `services/us_business_days.py` (cash03) | the clock's "advance one business day" — imported by the package (pure, stdlib only) |
| System-axis archive | `services/bitemporal.archive_live_row` (cash07) | counterparty edits — imported by the package (pure SQL on the caller's connection) |
| Demo rule | `public.crm_is_demo_household` (CRMDETAIL1) | `cpsim.may_connect` for a real org |
| Org flag | `public.org_settings` | `counterparty_sim.allowed` |
| Custodian tables | `custodian_capabilities`, `altruist_connections`, custody import | **not touched** — the registry holds ONLY simulated counterparties (design principle 3); real custodians stay where they are |
| Chancery, payment adapters, execution routers | — | not used until cpsim02–04 |
| Fake keys / bank numbers | none existed | NEW `cpsim/generators.py`; the platform's real-data bank-number guard (cash08, `cash_payees_no_numbers`) is unchanged and does not apply to `cpsim.*` |
| Registry tables | none existed | NEW `cpsim.*` — the simulator's own book, by design |

**The package imports only two shared helpers from `services/`**: `services.us_business_days` and
`services.bitemporal` (both stdlib-only, no further imports) — enforced by `scripts/cpsim_boundary_check.py`, so the
package can be lifted into its own service by copying those two files.

## 7. Database access

- Role `cpsim_app`: NOLOGIN, NOINHERIT; `GRANT cpsim_app TO app_service WITH INHERIT FALSE, SET TRUE` (PostgreSQL 17).
  `app_service` holds none of its privileges until a transaction runs `SET LOCAL ROLE cpsim_app`.
- **The app's normal role cannot read `cpsim.*`**: no privilege on the schema (`has_schema_privilege` false). The only
  two paths are (a) the simulator sub-app (`cpsim/db.py`: its own small asyncpg pool on the same `DATABASE_URL`,
  `statement_cache_size=0`, every unit of work `SET LOCAL ROLE cpsim_app`) and (b) the admin router, which enters the
  same role on the app pool's connection through `cpsim.db.as_simulator(conn)` and `RESET ROLE`s before its audit
  insert. Both are inside one transaction (transaction pooler safe — `SET LOCAL`).
- cpsim tables carry no RLS: no role but `cpsim_app` (and the owner) holds any privilege on them, and they hold no
  tenant data. Every public-schema change (none new) keeps RLS.

## 8. Decisions taken in this sprint (no human available)

- Credentials: `sha256(salt ‖ key)` with a 128-bit random salt; constant-time compare (`hmac.compare_digest`). Keys
  carry 160 bits of randomness, so a slow KDF adds nothing. The DB cannot see the key, so "a key without the prefix is
  refused by the DB" is enforced on the stored `key_prefix` (CHECK `^cpsim_[a-z]+_$`); the app refuses any presented
  value without `cpsim_` before any lookup.
- Fake-number ranges (documented in `CPSIM_ADMIN.md` §4): ABA `99…` + wrong check digit; account `CPSIM` + 10 digits;
  BIC country `XX` + location ending `0`; IBAN check digits `00`/`01`/`99` (never produced by ISO 13616).
- Registry privileges: "ALL on schema cpsim" is implemented as USAGE + SELECT/INSERT/UPDATE/DELETE + EXECUTE — no
  CREATE, TRUNCATE, TRIGGER or REFERENCES (DDL stays with the migration owner).
- `destination_path` is stored (`/cpsim/c/<slug>`, CHECKed); the URL shown is `<API base>` + path, the base taken from
  the admin request itself.

## Fix 1 findings (2026-11-10)

The first full run ended 109 PASS / 9 FAIL. Every FAIL below has a CODE or VERIFY verdict with its evidence.
**No product code changed, no gate was loosened, and the app login role still has no USAGE on schema cpsim.**

**Leftover fixtures (before the fix):** 0. Counted with `--cleanup-only` (every fixture scope, public and cpsim)
and directly as cpsim_app: slug `cpsimverify-%` = 0, name `CPSIM01 VERIFY%` = 0, cpsim.counterparties total = 0,
request_log `cpsimverify-%` = 0, orgs named `CPSIM01 VERIFY` = 0. The first run's teardown had already left nothing.

| # | FAIL | Verdict | Evidence | Fix |
|---|------|---------|----------|-----|
| A | `schema raised — permission denied for schema cpsim` (and the 7 sections "not run" that followed) | VERIFY | `$1::regclass` resolves a name inside schema cpsim; the verify's login correctly has no USAGE on it. Two sites: `seed_rows_vs_checks` and `y_schema`. `'cpsim_app'::regrole` needs no schema access, and every other cpsim read already goes through `sim()` (SET LOCAL ROLE cpsim_app) | `REL_OID`: the relation found by `pg_class ⋈ pg_namespace` on namespace + relname, with no cast. Grep: no `::regclass` / `to_regclass` remains |
| A1 | safety: "a NON-test org … only an all-demo household may connect": plain / mixed / empty → true | VERIFY | The result itself shows `is_test: True`: `ORG_R = _id(3)` sits in the reserved `99000000-` prefix, and REP6FIX2's `organizations_mark_test_fixture` trigger forces `is_test = true` there. So `may_connect` correctly answered as for a test org | `ORG_R` gets a fixed id outside the prefix (the insert still rolls back); `guard_prefix` asserts no real org has it |
| A2 | admin: "a reject without a reason is refused (400)" → 200, and "audited exactly once" +1 `behaviour_updated` | VERIFY | `PUT /behaviour` is a sparse update (`model_dump(exclude_unset=True)`, then `{**live_row, **fields}` in `registry.set_behaviour`). The preceding PUT had stored `reject_reason='closed'`, so the merged row was valid. The audit FAIL is the same accepted write | Clear the reason explicitly (`reject_reason: null`). The check now also requires the named constraint `counterparty_behaviour_reject_chk` in the 400 (stronger) |
| A3 | (not one of the 9) vhk2 frozen-state scan flags `verify_cpsim01.py`: `st_old == 401 … cpsim_live_`, `len(abas) == 1000` | VERIFY | HTTP status codes, and the generator sample size. Neither is live state | Renamed `code_old` / `code_new`; `N_GENERATED = 1000` |
| B1 | authz1: `jpl991722@gmail.com` Principal 11 vs recompute 9, diff `manage_documents`, `view_marketplace` | VERIFY | Read-only query: role `support_staff` (internal, so not client-like), profile **'CSA / Ops'** carries both keys (own roles: 0; permission sets: none). PERMS1 (`ff54ef0`) made the Principal union profile + permission-set keys; `_authz1_common.granted` still read `user_roles ⋈ role_permissions` only. PERMS1's own notes record exactly this union for this user | `effective_report(…, profiles=True)` + `profile_granted` restate PERMS1's rule independently (catalog keys, own org, member-safe cut for a client by role standing). Asserted: the role-name copy equals the code's, and the source is exercised by at least one real user |
| B2 | authz1 route matrix, member and other_org: 16 each | VERIFY (stale generator rule) | Full list (one-off with the pre-fix JSON). 12 GETs refused by `require_internal`: fee-chat/conversation, fee-schedules, fee-schedules/{id}, …/{id}/assignments, …/resolve/account/{id}, portfolio/position-account-exceptions, portfolio/precedence/households/{id}, profitability/pnl, profitability/households-by-margin, udf/records/{t}, udf/records/{t}/export, udf/values/{t}/{id}. Plus 4 writes whose 403 "does not name the permission" (PATCH fee-schedules/{id}, POST …/{id}/submit, …/{id}/retire, …/assignments/{id}/end): `require_internal` refuses first. Gates: BUGFIX3 `00e553b` (staff-only portfolio / UDF reads), PERMS1 `ff54ef0` (fee screens = internal + view_portfolio). A member is MEANT to be refused, so no CODE defect. `authz1_routes.expect()` had sent `require_internal` + `require_permission(X)` to rule `perm` | Generator: that shape is `internal_perm` with `all_of` = the permissions and `staff_bypass: false` (require_permission has no staff bypass). verify_authz1 `expectation()` and `navregroup_traced_gates.mjs` honour it. 24 rows regenerated (also POST /deals and the manage_billing writes) |
| B3 | authz1 route matrix: staff `GET /admin/workflow-runs` → 200 (and org_admin: workflow-runs, workflows → 200; the nested summary showed only 3 of its 5 FAIL lines) | VERIFY | Gate `_require_workflow_permission(request, view_workflow_runs)`: Super Admin first, then the Principal (PERMS1). The staff persona holds `view_workflow_runs` (`INVESTMENT_STAFF_PERMS`, documented seeding); the org_admin persona holds it and `author_workflows` (PERMS1 baseline). The generator read the helper's `if not is_super_admin(` bypass as a super-only gate | `super_bypass_only`: such routes get the EXACT rule, `perm any_of [X]` (X = the helper's permission; `review_agent_proposals` for the step decision). Not the loose `workflow`, so deny assertions stay for non-holders. 11 rows |

The regenerated `docs/AUTHZ1_ROUTES.json` changes 35 `expect` records and 0 `after` gate texts (`.md` unchanged;
`--check` CURRENT for both route docs). Older sprints compare records through `_verify_self.gate_of`, which now maps
exactly these two corrections (only when the `after` text has the named shape, the workflow ones by a hard-coded
route → permission table). All 57 route self-check verifies pass `--self-checks-only`. `verify_authz1.py` now prints
every matrix mismatch, not only the first six.

**Proof (one-off, `timeout 600` each):** cleanup → 0 before and after; `--self-checks-only` 40/0; seed 73/0;
registry 88/0, credentials 96/0, generators 100/0, subapp 99/0, clock 79/0, safety 81/0, admin 101/0 (totals include
the seed + schema sections; teardown and real-row checksums PASS in each); isolation checks PASS in every run.
authz1 `AUTHZ1_ONLY="real users"` 46/0, `AUTHZ1_ONLY="route matrix"` 28/0 (all 7 personas, 0 mismatches). vhk2 41/0,
catchall1 34/0.

**Open, not fixed here (pre-existing; reproduced with the pre-fix files):** `node menuvisibility_harness.mjs`
crashes at line 172 (`i.gate.roles` undefined). It runs only in `verify_authz1`'s own `regression()` block, which is
skipped when nested, so the cpsim01 run never reached it. A standalone `verify_authz1.py` will FAIL there.
