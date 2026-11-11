CPSIM01 — the COUNTERPARTY SIMULATOR begins: a semi-independent module that
plays the other side of Hollisworks (custodians and banks) so the app can be
tested and demoed end to end. This sprint: write down the Altruist contract,
create the simulator's own schema + database role + mounted sub-app, the
COUNTERPARTY REGISTRY with its Super Admin page (name, institution type,
destination URL, fake key, FIX session fields, fake bank details, scripted
behaviour, a clock), the SAFETY RULES that keep simulated and real apart, and
one working "mirror" endpoint that proves the plumbing. No Altruist API yet
(cpsim02), no orders (cpsim03), no payments (cpsim04). 9 tasks +
verification. Branch cpsim01-work.

Design: sprint_prompts/logs/COUNTERPARTY_SIMULATOR_DESIGN_v1.md (2026-11-10)
— READ IT FIRST; copied into docs/cpsim/ by this sprint (Task 9). DECIDED (Joe,
2026-11-10): build it, four sprints; version 1 "accepts any properly
formatted input and provides the mirrored output" (stateless); reuse shared
tables and features — extend, don't re-create (security master, prices,
audit log, custodian tables, custody import, Chancery); HOSTING: inside the
existing Render API service (2ndactcapital-api), not a new service.

PRINCIPLES (decided, do not re-decide):
1. Separate book, shared reference data. The simulator READS the security
   master and prices. Everything it owns lives in schema `cpsim` and reaches
   Hollisworks only over HTTP (and later webhooks / files) — never by
   writing Hollisworks' own tables, and Hollisworks' services never read
   `cpsim.*` directly (assert both by static scan: no `cpsim.` reference
   outside the simulator package and its admin router / verify; no write to
   a non-cpsim table from the simulator package except audit_log).
2. Semi-independent inside one service: a self-contained package
   apps/api/cpsim/ with its own FastAPI sub-app MOUNTED at /cpsim on the
   existing app (main.py app.mount), its own auth (its fake keys — NOT
   Hollisworks sessions), and its own database access through a NOLOGIN role
   `cpsim_app` (privileges: ALL on schema cpsim; SELECT on the security
   master and price tables it needs — list them; nothing else) entered with
   SET LOCAL ROLE inside each simulator transaction. No new login, no new
   secret. The package imports nothing from services/ except the shared
   read helpers named in Task 1 (so it can be lifted into its own service
   later without surgery).
3. Never mistaken for real: every counterparty row is `is_simulated = true`
   by a CHECK (this registry holds ONLY simulated counterparties — real
   custodians stay where they are); simulator credentials are generated
   with a `cpsim_` prefix and any value without it is refused; a
   Hollisworks org may point at a simulator only when the org setting
   `counterparty_sim.allowed` is true (default false; Super Admin, audited)
   — and in an org that is not is_test, only for households tagged demo
   (the Olivo demo lives inside the real 2nd Act org; whether it moves to
   its own tenant is still open — build the rule so either works). This
   sprint builds the setting and the check function
   (cpsim.may_connect(org, household)); cpsim02 calls it. Every admin
   screen and every simulator response carries a SIMULATED marker (header
   `X-Counterparty-Simulated: true`; a banner in the UI).
4. v1 stateless: validate, answer in shape. Behaviour settings and the
   clock are STORED and editable now; they start driving outcomes in
   cpsim03 / cpsim04.

THE REGISTRY (what an admin enters — Joe's list):
- name; institution type from cpsim.institution_types — seeded: altruist,
  schwab, domestic_bank, international_bank (a table, so types can be
  added; each type lists which field groups apply);
- destination URL (where Hollisworks will send — for the mounted simulator
  this is <api base>/cpsim/c/<counterparty slug>; stored, shown, copyable);
- fake key: generated (`cpsim_live_<random>`), shown ONCE, stored as a
  salted hash + last four; rotate / revoke;
- FIX session (stored only — no FIX engine): BeginString (FIX.4.2 / 4.4 /
  FIXT.1.1), SenderCompID, TargetCompID, host, port, HeartBtInt,
  ResetOnLogon, optional Username; password generated like the key;
- bank details (FAKE, generated, never typed from a real account): ABA
  routing number for wire and for ACH (9 digits in a range that can never
  be a real ABA — decide and document, e.g. a 99-prefix — failing the real
  check-digit test), account number (`CPSIM` + digits); international:
  SWIFT / BIC (a code that cannot be real — document), IBAN (invalid
  check digits by construction), intermediary bank fields, beneficiary
  name / address. A DB CHECK / trigger refuses a routing number that passes
  the real ABA checksum or a BIC from a seeded list of real examples — the
  platform's no-raw-bank-numbers rule for REAL data is unchanged
  (cash08's guard stays on its own tables);
- behaviour: default outcome (accept | reject with reason | partial |
  delay seconds), ACH return (code, after N business days), webhooks on /
  off — stored;
- clock: business date per counterparty; "advance one business day" (uses
  services/us_business_days.py); stored + audited;
- status: draft / active / disabled; bi-temporal; audited.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-10; Task 1 confirms):
- render.yaml: web services 2ndactcapital-web and 2ndactcapital-api
  (rootDir apps/api, `uvicorn main:app`), crons for the workflow scheduler
  and AI spend sync. No change to render.yaml is needed for a mount.
- The Altruist client (apps/api/services/altruist_oauth.py): OAuth2
  authorization-code + refresh; ALTRUIST_HOSTS has environments stage
  (openapi.stage1.altruistnet.tech / oauth.stage1…) and production;
  _OAUTH_PATHS authorize /oauth/authorize, token /oauth/token; _API_PATHS
  households, accounts, positions, transactions; access token 1 h, refresh
  30 d; per-org rows in public.altruist_connections (environment, client_id,
  Fernet-encrypted secret / tokens, last4s). Its own docstring: NO LIVE
  ALTRUIST CALL HAS EVER BEEN MADE; the paths are assumptions isolated in
  those two dicts. Also services/altruist_positions_sync.py,
  altruist_webhook.py (signature verify, event id / type / identifier /
  environment), altruist_identity.py, portfolio_altruist.py (older env-var
  path), routers/altruist_connection.py, altruist_webhook.py,
  custodians.py, custody_import.py, held_away.py.
- public.custodian_capabilities (key-value per custodian);
  services/execution_routers.py; services/payment_adapters.py (cash07);
  services/us_business_days.py (cash03).
- No network in the sprint: the mounted sub-app is exercised in-process
  (httpx ASGITransport against the real app object).
- bugfix4: roles from user_roles only. Count audit_log BY FIXTURE IDS ONLY.
  Demo rows (demo-olivo) are permanent: the sprint writes NO real or demo
  row and creates NO counterparty outside fixtures.
- Regression: verify_authz1.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s (a new mount + admin routes).
- WSL memory is limited and the runner kills a leg at 90 minutes: do NOT run
  npm run build or any full verify; COMMIT after each task ("cpsim01 (wip):
  <task>").

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. No model call. Never print a secret (a generated fake
key is printed ONCE to its creator's response only — never to a log). No
Altruist branding or logo anywhere: the type is "Altruist-compatible".
Bi-temporal rule 3; RLS for every operation on any table in the public
schema; cpsim.* tables are reachable only through role cpsim_app and the
Super Admin admin router. Light / cream UI. Final commit on the current
branch with message "cpsim01: <one line per change>" (excluding
sprint_prompts/logs).

=== TASK 1: DISCOVER + THE CONTRACT === Report findings, then continue
immediately. Write docs/cpsim/ALTRUIST_CONTRACT.md: every request the
existing client sends (method, path, headers, parameters, body) and every
response field it reads, for OAuth authorize / token / refresh, households,
accounts, positions, transactions; every webhook field
services/altruist_webhook.py reads and how the signature is computed; what
is ASSUMED (never observed) vs documented by Altruist — marked per field.
This file is the simulator's specification for cpsim02 and the future
conformance suite. Also docs/cpsim/CPSIM01_NOTES.md: the reference tables
the simulator may read (security master, prices — exact names), how
main.py mounts / orders middleware (auth middleware must NOT wrap /cpsim
with Hollisworks session auth), the admin nav / Super Admin gate pattern,
org_settings storage, reuse inventory (existing construct per piece, or why
new).

=== TASK 2: SCHEMA + ROLE === docs/cpsim01_part1.sql (applied): schema
cpsim; role cpsim_app (NOLOGIN; granted to the app's login role; least
privilege as above); cpsim.institution_types (seeded 4);
cpsim.counterparties (registry fields above; is_simulated CHECK; slug
unique; bi-temporal); cpsim.counterparty_credentials (key hash + salt +
last4 + prefix CHECK `cpsim_`; rotated_at / revoked_at);
cpsim.counterparty_fix_sessions; cpsim.counterparty_bank_details (fake-
range CHECKs / trigger); cpsim.counterparty_behaviour;
cpsim.counterparty_clock; cpsim.request_log (every simulator request:
counterparty, path, status, shape-valid, at — no bodies with secrets);
the org setting counterparty_sim.allowed; function
cpsim.may_connect(org_id, household_id) → boolean with the rule in
principle 3.

=== TASK 3: THE SUB-APP === apps/api/cpsim/ (app.py, auth.py, db.py,
registry.py, mirror.py, generators.py): mounted at /cpsim; GET
/cpsim/health; key auth (Authorization: Bearer cpsim_…; constant-time hash
compare; revoked / disabled → 401); every response carries the SIMULATED
header; request_log written; POST /cpsim/c/{slug}/mirror — validates the
JSON body against the institution type's declared envelope (a small JSON
Schema per type under cpsim/schemas/, starting with a generic envelope:
message_type, message_id, as_of, payload) and returns the mirrored message
(same message_id, `mirrored: true`, received_at from the counterparty's
clock) — "properly formatted in → mirrored out"; malformed → 422 with the
schema errors; unknown slug → 404.

=== TASK 4: GENERATORS === fake key, FIX password, ABA (wire + ACH), account
number, BIC, IBAN — each provably NOT a real value (unit tests: generated
ABA always fails the real checksum; a real-looking ABA / a real example BIC
is refused by the DB).

=== TASK 5: ADMIN API === routers/admin_counterparties.py (Super Admin only,
the existing gate): list / create / edit / activate / disable; generate /
rotate / revoke key (shown once); regenerate bank details; edit FIX
session, behaviour; advance the clock; request log (last N); the org
setting toggle (audited; shows which orgs are allowed). Every action
audited (counterparty.* events).

=== TASK 6: ADMIN PAGE === Admin → Counterparties (light / cream, SIMULATED
banner): list; create wizard (name, institution type → the field groups
that type declares); detail with tabs Connection (destination URL, key
last four, copy), FIX, Bank details, Behaviour, Clock (date + "Advance one
business day"), Activity (request log), Import / Export (placeholder tab:
"arrives with the Altruist API — cpsim02", disabled). A "Send test message"
button posts a sample envelope to the mirror and shows the reply.

=== TASK 7: SAFETY PROOF HOOKS === the static scans in principle 1 as a
reusable script (scripts/cpsim_boundary_check.py: exits non-zero on a
violation; listed in docs); the UI marker; the prefix rule; may_connect.

=== TASK 8: REAL PROOF ===
  - Schema: tables exist under cpsim only; cpsim_app's privileges are
    exactly the documented set (read from the catalog: no privilege on any
    public table except SELECT on the listed reference tables and INSERT on
    audit_log if used); the app's normal role cannot read cpsim.* without
    SET LOCAL ROLE (or only via the admin router's path — state which and
    prove it).
  - Registry: create each of the 4 institution types (fixtures); a
    non-simulated row is impossible (CHECK); slug unique; bi-temporal
    history on edit.
  - Credentials: key shown once, never retrievable again; stored hashed;
    a key without the cpsim_ prefix refused by the DB; rotate invalidates
    the old key; revoke → 401.
  - Generators: 1,000 generated ABAs all fail the real checksum; a
    checksum-valid ABA and a real example BIC are refused by the DB; IBAN
    check digits invalid by construction.
  - Sub-app: /cpsim/health 200 with the SIMULATED header; mirror with a
    valid envelope → mirrored (same message_id, counterparty clock);
    malformed → 422 with errors; wrong / revoked key → 401; unknown slug →
    404; a Hollisworks session cookie / JWT alone does NOT authenticate to
    /cpsim (401); request_log rows written without the key.
  - Clock: advance from a Friday → Monday; over a US holiday → next
    business day; audited.
  - Safety: counterparty_sim.allowed default false; may_connect false for
    a fixture org without the flag, true with it for an is_test org, and
    for a non-test fixture org only for a household tagged demo;
    cpsim_boundary_check passes on the repo and FAILS on a planted
    violation (a temp file referencing cpsim.counterparties from
    services/).
  - Admin: Super Admin only (an org admin → 403); every action audited
    (counted by fixture ids); node render tests (list, wizard per type,
    detail tabs, SIMULATED banner, send-test-message).
  - No network call (asserted); no real or demo row changed; no
    counterparty exists after teardown.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT (the
    mounted /cpsim paths are documented as simulator routes with their own
    auth).
  - Regression: verify_authz1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cpsim01), docs/cpsim/ (the design doc — copy
sprint_prompts/logs/COUNTERPARTY_SIMULATOR_DESIGN_v1.md, placed there for
this sprint; read it FIRST in Task 1 —,
ALTRUIST_CONTRACT.md, CPSIM01_NOTES.md, CPSIM_ADMIN.md: how to create a
counterparty, what each field is for, the safety rules), operator notes
(nothing to switch on yet; counterparty_sim.allowed stays false until
cpsim02); add verify_cpsim01.py to scripts/regression_set.txt (bare name,
the set's one format).

=== VERIFICATION: apps/api/scripts/verify_cpsim01.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it; the
sub-app is called in-process). Fixture orgs (privileged insert path,
is_test = true — assert it) plus one NON-test fixture org for the demo-
household rule; fixture counterparties carry a reserved slug prefix that
matches no real row (assert it); time injected. Wrap every section so an
exception is a [FAIL]; teardown via scripts/_verify_cleanup.py at start and
end by fixture id — cpsim.* tables have no org_id, so pass them in the
cleanup plan by fixture slug prefix, with every teardown GUC a guard reads;
re-read every touched table for its exact pre-test count, counting
audit_log BY FIXTURE IDS ONLY. Roles via user_roles (assert the app decides
each). Read UNIQUE / CHECK / FK constraints and unique indexes from the
catalog before seeding (FAIL if none are read) and compare every seeded
literal in a CHECKed column to its CHECK. Every INSERT's columns checked
against docs/schema_snapshot.sql. Text ids compared as text, uuids as uuid.
Use the shared verify tooling verifyfix1 added (_verify_roles, _verify_scope,
VT.install_guard / run_nested) where it exists. Support --self-checks-only,
--cleanup-only and --only=<section>. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line. If the seed fails, every fixture section is a
[FAIL], never skipped silently. Every section finishes in < 10 minutes.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool (wrap
    and-chains in bool()).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context set ON the connection inside its transaction; rolbypassrls
    = false asserted; an empty list never passes a check.
  - Every query's placeholders match its arguments; a parameter used in two
    contexts gets an explicit cast of the right type.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - Count your own node tests by their file, not by title prefixes.
  - Never seed a state the database forbids (read the unique indexes first).
  - No check frozen against live state; static markers match by name.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Altruist contract written (assumed vs documented per field); notes +
      reuse inventory
  [Y] cpsim schema + cpsim_app least privilege (from the catalog)
  [Y] Registry: 4 institution types, simulated-only, bi-temporal
  [Y] Credentials: prefix, shown once, hashed, rotate / revoke
  [Y] Fake bank / FIX values provably not real; DB refuses real-looking
      ones
  [Y] Sub-app: own auth, SIMULATED header, mirror in → mirrored out,
      malformed 422, request log
  [Y] Clock: business-day advance, audited
  [Y] Safety: setting default off, may_connect rule, boundary check passes
      and catches a planted violation
  [Y] Admin API + page: Super Admin only, audited, render tests
  [Y] No network; real + demo rows unchanged; own routes; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_authz1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
