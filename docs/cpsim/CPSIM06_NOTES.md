# cpsim06 — ACCOUNT OPENING at the simulated custodian: discovery, decisions as built, the ASSUMED Realtime contract, the validation rules

Sprint cpsim06 (2026-11-12), branch `cpsim06-work`. Hollisworks gets an "Open account at custodian" action on a CRM
household. It sends an account-opening request, pre-filled from the CRM, to the SIMULATED custodian, in the SHAPE of
Altruist's Realtime API (account drafts / contacts / households / beneficiaries / trusted contact). It is simplified by
Joe's rule: "as long as all the data is provided and in good order (no obvious data issues), the account gets 'opened',
the response is generated and the open account appears in the feed — no need for a mock client to approve". In good
order, the counterparty opens the account at once. Otherwise it refuses and lists EVERY problem, field by field.
Hollisworks then records the account through its EXISTING `household_admin.add_account`, writes the crosswalk in the same
transaction, and syncs it. **Simulated counterparty only.** No real Realtime client exists.

Paths are relative to `apps/api/` unless they start with `docs/` or `apps/web/`.

## 1. Findings (Task 1, HEAD `df11f40`)

### 1a. Prerequisite

- **cpsim05 is merged**: `git log main` → `df11f40 merge cpsim05`.
- Fast regression mode is present: `scripts/_verify_total.py:177-194` (`VERIFY_REGRESSIONS` unset / `fast` → each nested
  verify runs `--self-checks-only`; `full` → the whole verify).
- **[FIND] `claude/ALTRUIST_API_MAP_2026-11-11.md` does not exist in this checkout** (no `claude/` directory, and no
  file of that name anywhere in the repo). The Realtime facts used here are the ones the sprint prompt quotes ("CONFIRMED
  REAL FACTS — ALTRUIST'S PUBLISHED REALTIME API"), recorded in §3 with that as their source.

### 1b. The "confirm" items

| Item | Finding (file:line) |
|---|---|
| add_account | `services/household_admin.add_account` `:298`. It opens ITS OWN `pool.acquire()` + transaction (`:342-343`), stores `AccountNumber.masked` + `.hashed(org salt)` only (`:365-385`), checks registration type / tax status against the org's config vocabulary (`_check_vocab` `:284`, `crm_config.account_vocab`), requires the owner to be a household member (or a member-owned legal entity with `add_owner_as_member`), sets `is_billable` OFF for a demo household (`crm_is_demo_household`, `:362`), audits `household.account_added`, and turns a non-default authority / custody into a REP7B draft change (`:396-405`). |
| add_account's gate | `routers/households.py:357` `POST /admin/households/{id}/accounts` → `_require_household_admin` `:114` = `require_internal_permission(request, "manage_members")` (`services/authz.py:362`): Super Admin first, staff, then an internal role holding **`manage_members`**; never a client role. Then `require_visible(entity_ids=[owner])` (VISIBILITY1). |
| How tax ids are stored | TWO stores. (1) **`entity_tax_ids`** (the CRM's Tax IDs tab, `routers/entities.py:1069-1115`): `tax_id_encrypted` = **sha256 of the raw value** ("placeholder encryption", `:1074-1078`) + `tax_id_last4`. **The full number cannot be recovered from it.** (2) **`entities.tax_id`** (text, `docs/schema_snapshot.sql:1927`): a legacy plain column, editable through `services/entity_writes.EDITABLE` `:31`, and **returned by every entity read** (`routers/entities.py:90` `ENTITY_COLUMNS`). |
| How DOBs are stored | `entities.date_of_birth` (date, CRMDETAIL1), read by the entity read path (`ENTITY_COLUMNS` `:96`); `crm_vocab.age_on` / `MINOR_AGE = 18` (`services/crm_vocab.py:421-437`). |
| Addresses | `entity_addresses` (`address_type` enum `primary_residence` / `mailing` / `business` / `registered`, `schemas/entities.py:86`; street1/2, city, state, postal_code, country, `is_primary`, valid + system axes). |
| Email / phone | `entities.primary_email`, `entities.primary_phone`. |
| Citizenship | `entities.citizenship_status` (text) exists but is **NOT in `ENTITY_COLUMNS` and not editable** (`entity_writes.py:27`: not copied). No visa type or visa expiry anywhere in the CRM. |
| Employment | `entity_employment` (employee_id → employer_id, title, is_current). No employment STATUS (retired / student …) anywhere. |
| Relationships | `services/crm_vocab.RELATIONSHIP_TYPES` `:71-111`: `trustee` / `co_trustee` / `successor_trustee` (party → trust), `beneficiary` (party → LEGAL only — a beneficiary OF A TRUST / entity), `spouse`, `parent_of`. **[FIND] There is NO `custodian_of_minor` type and NO `trusted_contact` type**, and no account-level beneficiary designation (an IRA's beneficiaries are not modelled anywhere). |
| Registration vocabulary | `docs/portentry2_part1.sql:17-30`: `account_registration_types` keys `individual, joint, trust, llc, lp, corporation, foundation, ira, roth_ira, 401k_rollover, 529, daf, insurance, other`; `account_tax_statuses` `taxable, tax_deferred, tax_exempt`. Per org, seeded by `crm_seed_org_config`. |

### 1c. The simulator book and the Open API feed

- Book tables (`docs/cpsim02_part1.sql:74-106`): `cpsim.book_households (counterparty_id, external_id, name)` and
  `cpsim.book_accounts (counterparty_id, external_id, household_external_id, name, account_type)`. VALID axis, partial
  unique `(counterparty_id, external_id) WHERE valid_to IS NULL`.
- Served by `GET /cpsim/c/<slug>/v2/accounts?household_id=` (`cpsim/app.py:319`) → `cpsim/book.accounts` `:551`. Every
  account is served as `status "ACTIVE"`, `date_open null`, and a synthetic hash-derived `account_number` (`book.py:499`).
  The book stores no opening date and no account number. cpsim06 adds both columns to `cpsim.book_accounts` (nullable,
  cpsim-only DDL), so an opened account is served with its `date_open` and its `CPSIM…` number. Uploaded rows are
  unchanged (null → the old behaviour).
- The crosswalk: `portfolio.external_references` UNIQUE `(org_id, source_system, external_id, record_type)`; the writer
  is `services/portfolio_assets.upsert_external_reference` (`:1104`, `_OrgWrite`). The simulator's source is
  `ALTRUIST_SIMULATOR` (`services/altruist_identity.py:70`).
- The sync: `services/altruist_positions_sync.sync_resolved_accounts` `:608` syncs EVERY resolved account of the org.
  cpsim06 adds an optional `only_account_ids` filter (default None = unchanged) for "an immediate sync of that
  account".
- Simulator-fed accounts carry custodian code **`ALTRUIST`**: every cpsim03 / cpsim05 fixture does this, and
  `custodian_execution.simulator_route` reads a crosswalk, not a code. The crosswalk source marks the account as
  simulated. An opened account therefore uses `ALTRUIST`, so cpsim03 / cpsim05 trade on it unchanged.
- The client-side refusals (`services/altruist_oauth.simulator_counterparty` `:208`, `resolve_hosts` `:229`,
  `check_destination`, `_transport_for` `:185`) are reused. `may_connect`: `public.counterparty_sim_may_connect(org,
  household)` (cpsim02's public wrapper over `cpsim.may_connect`).

### 1d. Consequences for the design (each a [FIND] recorded here)

1. **The CRM cannot supply a full tax id** for any owner whose number lives only in `entity_tax_ids` (hashed). Built:
   at send time the server reads `entities.tax_id` through the entity read path. When that column is empty, the form
   asks the opener to type the number for that owner. It is sent with the one request, never stored by Hollisworks,
   never returned. If the owner has `entity_tax_ids` rows, the typed value must hash to one of them (same sha256 the
   CRM used, tried as typed / digits only / `XXX-XX-XXXX`), else the problem "does not match the tax ID on the CRM
   record (•••• 1234)". The page only ever shows last four.
2. **[FIND] `entities.tax_id` is a plaintext tax-id column returned by every entity read** (`ENTITY_COLUMNS`). This is
   pre-existing and NOT fixed here (out of scope, and changing the entity read shape touches every CRM page). It needs a
   follow-up sprint.
3. Citizenship, visa type / expiry, employment status and trusted contact are not in the CRM. The form takes them
   (pre-filled where anything exists: `citizenship_status` when it names `us_citizen` / `permanent_resident`; the current
   employer from `entity_employment`). They are sent with the request and not written back to the CRM. Writing them back
   is a CRM change this sprint was not asked for.
4. Beneficiaries: pre-filled from `beneficiary` relationships TO the account's owner record (a trust's beneficiaries).
   For a person-owned account (IRAs), the CRM has no designation, so the form lets the opener pick household members /
   type names, with tier + percentage.
5. Custodian of a minor: pre-filled from `parent_of` (an adult household member who is the minor's parent); else picked.
6. **DOB never leaves the server to the page**: the pre-fill sends `date_of_birth_on_record: true|false` and the computed
   `is_minor`. The server reads the date at send time. A missing DOB is typed in the form (sent once, not stored).
7. **No public DDL** is needed. Hollisworks writes nothing new: `add_account` (existing table), the crosswalk (existing
   table), audit_log (existing). Idempotency is the crosswalk: the counterparty's account id already crosswalked → the
   same Hollisworks account is returned, never a second add_account.

## 2. Decisions as built (D1–D6)

| D | As built | Where |
|---|---|---|
| D1 | Simulated Realtime surface under `/cpsim/c/<slug>/realtime/`: `POST …/realms/altruist/protocol/openid-connect/token` (form: `grant_type=password` with client_id, client_secret, username, password; `grant_type=refresh_token` with client_id, client_secret, refresh_token). The client secret is the counterparty's `cpsim_live_` key (cpsim02 client auth reused). Username = a simulator user created on the admin page; password `cpsim_rtpw_…` generated, shown once, stored as a salted hash. Access `cpsim_rtat_…` 300 s; refresh `cpsim_rtrt_…` expiring at the session's FIXED end (issue + 10 h). A refresh returns a new access token and a refresh token with the SAME end; the original refresh token stays valid. `POST …/api/households` (create or reuse by exact name), `POST …/api/contacts` (create, or restate the same `client_reference`), `POST …/api/account-drafts` (create, or restate while still a draft, keyed by `client_reference`), `POST …/api/account-drafts/{id}/validate` (the problems, opens nothing), `POST …/api/account-drafts/{id}/submit` (no problems → OPENED at once: status ACTIVE, `date_open` = the counterparty clock, number `CPSIM` + 9 digits, the account written to `cpsim.book_accounts` under its household and served by `/v2/accounts` at once). A repeat submit of an opened draft returns the same account (200). Every path / field ASSUMED. | `cpsim/realtime.py`, `cpsim/app.py` |
| D2 | `cpsim/realtime.validate_draft` returns EVERY problem `{field, code, message}` (§4). Contact-level rules are evaluated when the contact is stored (its tax id is gone afterwards) and kept on the contact (`problems`). Draft-level rules (type, roles, ages against the clock, visas against the clock, unique email, beneficiaries, trusted contact) are evaluated on each validate / submit. The response is the union. | `cpsim/realtime.py` |
| D3 | Tax ids reach the simulator only inside the contact request. Stored as `tax_id_last4` (CHECK `^[0-9]{4}$`) + `tax_id_digest` (bytea, sha256 of salt‖digits) + `tax_id_salt` (bytea). No text column holds more than 4 consecutive digits of a tax id: CHECKs on every cpsim06 text / jsonb column that a tax id could reach refuse a 9-digit run. The request log stores no bodies (cpsim01). Every response shows `tax_id_last4` only. DOB is not stored: the contact keeps `age_years` at `age_as_of` (the clock). Hollisworks: the opener's typed tax id / DOB live only in the request; the audit row carries last four only. | `docs/cpsim06_part1.sql`, `cpsim/realtime.py`, `services/custodian_account_opening.py` |
| D4 | `services/altruist_realtime_sim.py` (client: per-call hosts, password-grant token cache honouring 5 min / 10 h fixed, transport on every call, connection released before HTTP, RateLimit-* headers read, 429 + Retry-After honoured once). `services/custodian_account_opening.py` (the flow: pre-fill, check, open). Routes `GET/POST /admin/households/{id}/custodian-account…` gated by `_require_household_admin` (= add_account's gate, `manage_members`, Super Admin first) + `require_visible`. Open → `add_account(custodian_code 'ALTRUIST', account_number = the returned number, owner = the primary owner, registration type / tax status mapped through the org vocabulary, authority / custody = REP7B defaults managed / integrated, on_created = the crosswalk write in the SAME transaction)` → `sync_resolved_accounts(environment 'simulator', only_account_ids=[that account])` → audit. A refusal returns every problem and creates nothing in Hollisworks. | as named, `routers/households.py`, `services/household_admin.py` (`on_created`), `services/altruist_positions_sync.py` (`only_account_ids`) |
| D5 | The action is offered only when the org has `counterparty_sim.allowed` true AND a configured counterparty AND (a non-test org) the household passes `counterparty_sim_may_connect`. Otherwise the envelope has `available: false` and the reason. For sandbox / production there is no Realtime client: `environment != 'simulator'` → refused before any request, with "account opening needs Altruist Realtime API access (not granted)". | `services/custodian_account_opening.py` |
| D6 | Admin → Counterparties gains a **Realtime** tab (Super Admin): simulator users (create → password shown once; revoke), contacts (last four only), drafts and opened accounts, and the validation log (problems per request). | `routers/admin_counterparties.py`, `apps/web/components/counterparties/Counterparties.jsx` |

## 3. The ASSUMED Realtime contract (every path and field)

Source of the REAL facts: the sprint prompt's "CONFIRMED REAL FACTS" (developer.altruist.com, read 2026-11-11; the map
file it cites is absent, §1a). The real portal names the resources but its paths and field lists were not readable, so
**every path and field below is ASSUMED**.

| Real (published) | Simulated (ASSUMED shape) |
|---|---|
| OAuth 2.0 password grant on behalf of a user; token endpoint `…/realms/altruist/protocol/openid-connect/token` | `POST /cpsim/c/<slug>/realtime/realms/altruist/protocol/openid-connect/token`, form-encoded: `grant_type`, `client_id`, `client_secret`, `username`, `password` / `refresh_token` → `{access_token, expires_in: 300, refresh_token, refresh_expires_in, token_type: "Bearer"}`; refusal → 401 `{error: "invalid_grant"}` (Keycloak-style) |
| Access tokens 5 minutes | `expires_in` 300 |
| Refresh tokens 10 h on a FIXED window; refresh does not extend; the original stays valid | each refresh token's `expires_at` = the session's start + 10 h; refresh issues a new refresh token with the same end and leaves the old one valid |
| API base `…/api/` | `/cpsim/c/<slug>/realtime/api/` |
| Rate limits per org per endpoint (reads 60/min, writes 30/min); `RateLimit-*` headers; 429 + Retry-After | every Realtime response carries `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`; a 429 + `Retry-After` is produced only by the injected test behaviour (`cpsim.realtime.force_429_once`). The simulator does not meter |
| Households | `POST …/api/households {name}` → 201 `{household: {id, name}}` / 200 the existing one with that exact name |
| Contacts | `POST …/api/contacts {client_reference, kind: person\|entity, legal_name, date_of_birth, email, phone, residential_address{street1, street2, city, state, postal_code, country}, mailing_address{…}?, citizenship: us_citizen\|permanent_resident\|visa, visa_type?, visa_expiration?, employment{status, employer?, occupation?}, tax_id{kind: ssn\|itin\|ein, value}}` → 201 / 200 `{contact: {id, legal_name, email, tax_id_last4, problems[]}}`. An entity (trust) carries legal_name, formation date (as `date_of_birth`), address, EIN |
| Account drafts (Invite workflow only; households, groups and users must exist first) | `POST …/api/account-drafts {client_reference, household_id, account_type, account_group?, owners[{contact_id, role}], beneficiaries[{tier: primary\|contingent, name, kind: person\|entity, contact_id?, relationship?, percentage}], trusted_contact{name, email?, phone?, contact_id?}?}` → 201 / 200 `{draft: {id, status, …}}`. Roles: `owner`, `joint_owner`, `trust`, `trustee`, `minor`, `custodian` |
| (none — Joe's simplification) | `POST …/api/account-drafts/{id}/validate` → 200 `{ok, problems[]}`, opens nothing |
| (none — Joe's simplification; the real flow invites the client) | `POST …/api/account-drafts/{id}/submit` → 201 `{draft, account: {id, account_number, account_type, status: "ACTIVE", date_open}}` / 200 the same (repeat) / 422 `{problems[]}` |
| Open API accounts: id, rep_code, account_number, account_type, account_name, status, date_open, date_closed, as_of_date, account_balance | unchanged route `/v2/accounts?household_id=`; an opened account: `status ACTIVE`, `date_open` = the opening clock date (YYYY/MM/DD), `account_number` = its `CPSIM…` number, `account_balance "0"` |

Account types the simulator accepts (the published YES list, keys ASSUMED): `individual_cash, trust_cash, roth_ira,
ira, rollover_ira, beneficiary_ira, simple_ira, solo_401k, individual, trust, jtwros, utma, ugma, minor_roth_ira`.
Refused with "not supported by the custodian's API for account opening — open it through the custodian's own process"
(the published NO list): `joint_cash, single_member_llc_cash, c_corporation_cash, s_corporation_cash,
llc_s_corporation_cash, non_profit_cash, sep_ira, beneficiary_roth_ira, roth_solo_401k, retirement_trust,
joint_tenants_in_common, joint_tenants_by_entirety, joint_community_property, minor_ira, sole_proprietorship,
single_member_llc, c_corporation, llc_c_corporation, s_corporation, llc_s_corporation, partnership, llc_partnership,
non_profit, donor_advised_fund`. Any other key → "unknown account type".

## 4. Validation rules (D2) — with sources

"Published" = the prompt's confirmed Realtime facts. "Decided" = this sprint's D2. "Simplification" = the simulator's
own reading, stated here.

| # | Rule | Field | Source |
|---|---|---|---|
| V1 | every required field present (per contact kind and role; per draft) | each field | Decided (D2) |
| V2 | account type in the YES list; a NO-list type refused with the custodian-process message; unknown → unknown | `account_type` | Published (YES / NO lists) |
| V3 | owners per type: Individual / Individual Cash / every IRA / Solo 401(k) → exactly one `owner`; JTWROS → exactly two (`owner` + `joint_owner`); Trust / Trust Cash → one `trust` (an entity contact) + ≥ 1 `trustee`; UTMA / UGMA / Minor Roth IRA → one `minor` + one `custodian` | `owners` | Decided (D2) |
| V4 | a `minor` is under 18 on the clock date; every other person role is 18 or over | `owners[i].date_of_birth` | Decided (D2: 18, documented; some states use 21) |
| V5 | DOB in the past | `date_of_birth` | Decided |
| V6 | residential address: US (`country` US / USA), not a PO Box (`P.O. Box`, `PO Box`, `P O Box`, `Post Office Box`, `POB`, any case / spacing), state a US state or DC, ZIP 5 or 9 digits (`12345` / `12345-6789`) | `residential_address.*` | Published (US residential, no PO Box, no foreign) + Decided (state / ZIP form) |
| V7 | mailing address, when given: US, state and ZIP as V6 (a PO Box IS allowed for mailing) | `mailing_address.*` | Published ("may differ but must be a valid US address") |
| V8 | SSN: 9 digits, area not 000 / 666 / 900–999, group not 00, serial not 0000 | `tax_id` | Published (valid SSN) + SSA's never-issued ranges |
| V9 | ITIN: 9 digits, starts 9, group in 50–65, 70–88, 90–92, 94–99 | `tax_id` | Published (valid ITIN) + IRS ITIN ranges |
| V10 | EIN (a trust contact): 9 digits, prefix not an unassigned IRS prefix (00, 07–09, 17–19, 28, 29, 49, 69, 70, 78, 79, 89, 96, 97) | `tax_id` | Simplification (IRS prefix list) |
| V11 | citizenship: `us_citizen`, `permanent_resident`, or `visa` with a type in {E1, E2, E3, F1, G4, H1B, L1, O1, TN1} expiring ≥ 90 days after the clock date | `citizenship`, `visa_type`, `visa_expiration` | Published |
| V12 | every client email unique across the counterparty's contacts (another contact's email → refused) | `email` | Published |
| V13 | beneficiaries: per tier present, percentages total EXACTLY 100 (Decimal); each has a name and a kind person / entity; each percentage > 0 | `beneficiaries[i].*` | Decided (D2) |
| V14 | trusted contact (optional): has a name and an email or phone, and is not an owner (by contact id, email or exact name) | `trusted_contact` | Decided (D2) |
| V15 | email format; phone 10 digits (US) | `email`, `phone` | Simplification |
| V16 | employment status in employed / self_employed / retired / unemployed / student; employer required when employed | `employment` | Simplification |

Flagged-account document requests (ID / SSN card) are NOT simulated (published fact, out of scope).

## 5. Reuse inventory

| Piece | Existing construct | Used how |
|---|---|---|
| Account | `household_admin.add_account` | unchanged behaviour; new optional `on_created(conn, row)` runs inside its transaction (the crosswalk) |
| Crosswalk | `portfolio.external_references` via `portfolio_assets.upsert_external_reference` | source `ALTRUIST_SIMULATOR`, record_type `account`, the counterparty's account id |
| Sync | `altruist_positions_sync.sync_resolved_accounts` | `only_account_ids=[the new account]` |
| Hosts / guard | `altruist_oauth.simulator_counterparty`, `resolve_hosts`, `check_destination`, `_transport_for`, `client_credentials(SIMULATOR)` | the Realtime client |
| Demo rule | `public.counterparty_sim_may_connect` | D5 |
| CRM reads | `entities` (`ENTITY_COLUMNS` read path), `entity_addresses`, `entity_tax_ids` (last four + hash only), `entity_relationships`, `entity_employment`, `household_memberships` | pre-fill (one query per table for all members — no N+1) |
| Simulator | `cpsim.book_households` / `book_accounts`, `registry.live_by_slug`, `oauth._authenticate_client`, `request_log`, SIMULATED header | D1 |
| Admin | `routers/admin_counterparties` (`require_super_admin` first, audit) | D6 |
