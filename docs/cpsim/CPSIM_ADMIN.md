# Admin → Counterparties — the Counterparty Simulator's registry (cpsim01)

Super Admin only. Every screen carries a **SIMULATED** banner; every simulator response carries the header
`X-Counterparty-Simulated: true`. The registry holds **only simulated counterparties** — the database refuses any row
with `is_simulated = false`. Real custodians stay where they are (`altruist_connections`, `custodian_capabilities`,
custody import); nothing here ever touches them.

Design: `COUNTERPARTY_SIMULATOR_DESIGN_v1.md`. Discovery and decisions: `CPSIM01_NOTES.md`. The Altruist wire
contract cpsim02 implements: `ALTRUIST_CONTRACT.md`.

## 1. Create a counterparty

1. Admin → Platform → Controls → **Counterparties**.
2. **New simulated counterparty**: a name and an institution type. The wizard shows the sections that type declares.
3. **Create.** The counterparty starts as a **draft** and the response shows, **once**:
   - its key `cpsim_live_…` (every type);
   - its FIX password `cpsim_fixpw_…` (FIX types only).
   Copy them now. They are stored only as a salted hash plus the last four characters and can never be shown again;
   a lost key is **rotated**, not recovered.
4. Optional: **Send test message** right away (the key is pre-filled) — a sample envelope goes to the mirror and the
   reply is shown.
5. Open the counterparty and **Activate** it. A draft or disabled counterparty answers every request with 401.

Hollisworks reaches it at its **destination URL**, `<API base>/cpsim/c/<slug>` (shown and copyable on the Connection
tab), with `Authorization: Bearer <key>`.

## 2. What each field is for

| Section | Field | Purpose |
|---|---|---|
| — | Name, slug | Display name; the slug is the address (`/cpsim/c/<slug>`), unique among live counterparties, never changed. |
| — | Institution type | From `cpsim.institution_types` (seeded: Altruist-compatible custodian, Schwab-compatible custodian, Domestic bank, International bank). The type decides the field groups (tabs) and the message envelope schema. New types are rows, not code. |
| — | Status | draft → active → disabled → active. Every change is archived (system-axis history) and audited. |
| Connection | Destination URL | Where Hollisworks sends. Stored as `/cpsim/c/<slug>`; the API base comes from the request. |
| Connection | Key | `cpsim_live_` + 40 hex. Generate / rotate / revoke. A rotated or revoked key answers 401 at once. |
| FIX | BeginString, SenderCompID, TargetCompID, host, port, HeartBtInt, ResetOnLogon, Username, password | **Stored only — there is no FIX engine in v1.** The default host is under the reserved `.invalid` TLD. |
| Bank details | ABA (wire, ACH), account, beneficiary name / address | Domestic bank. Generated, fake (see §4). Regenerate replaces them (history kept). |
| Bank details | SWIFT / BIC, IBAN, intermediary bank / BIC / ABA | International bank. Generated, fake. |
| Behaviour | Default outcome (accept · reject with reason · partial % · delay seconds), ACH return (code + after N business days), webhooks on / off | **Stored now; drives outcomes from cpsim03 (orders) and cpsim04 (payments).** |
| Clock | Business date; **Advance one business day** | The counterparty's own date (US federal holidays and weekends skipped — `services/us_business_days.py`). The mirror stamps `received_at` from it. Every advance is audited. |
| Activity | Request log | Every simulator request: path, status, auth outcome, shape valid, message type. Never a body, never a key. |
| Import / Export | — | Placeholder: arrives with the Altruist API (cpsim02). |

## 3. The mirror (v1 — stateless)

`POST /cpsim/c/<slug>/mirror`, `Authorization: Bearer cpsim_live_…`, a JSON envelope
`{message_type, message_id, as_of, payload}` (schema `apps/api/cpsim/schemas/generic_envelope_v1.json`):

- properly formatted → **200**, the same message back with `mirrored: true` and `received_at` on the counterparty's
  clock;
- malformed → **422** with every schema error (`path`, `message`);
- no / wrong / rotated / revoked key, a value without the `cpsim_` prefix, a Hollisworks JWT or session cookie, or a
  counterparty that is not active → **401**;
- unknown slug → **404**.

`GET /cpsim/health` → 200 (no auth).

## 4. The fake ranges (why no value can ever be real)

| Value | Construction | Database refuses |
|---|---|---|
| ABA routing | `99` + 6 digits + a **wrong** check digit. The Federal Reserve never assigns a `99` prefix (assigned: 00–12, 21–32, 61–72, 80) and the real check-digit rule fails. | anything not `99…`, and anything passing the real check digit (CHECK + trigger) |
| Account | `CPSIM` + 10 digits | anything not `CPSIM` + digits |
| BIC | `CPSM` + country `XX` (ISO 3166 user-assigned — no country) + a location ending `0` (SWIFT's test marker) | anything without `XX` / `…0`; any BIC on the seeded list of real examples (`cpsim.real_bic_examples`, trigger) |
| IBAN | `GB` + check digits `00` / `01` / `99` (ISO 13616 only produces 02–98; the one congruent to the canonical check is never chosen) + `CPSM…` | any IBAN passing the mod-97 test, or with check digits 02–98 |
| Key / password | `cpsim_live_` / `cpsim_fixpw_` + random hex | a credential row whose prefix is not `cpsim_<word>_` |

The platform's rule for REAL bank numbers is unchanged: real data keeps the last four only (cash08's guard on its own
tables). Fake numbers live in `cpsim` only.

## 5. The safety rules

1. **Separate book, shared reference data.** The simulator reads the security master and prices
   (`portfolio.securities_global`, `…_identifiers`, `…_prices`) and writes only `cpsim.*`. Hollisworks reaches it only
   over HTTP. `apps/api/scripts/cpsim_boundary_check.py` enforces it statically — run it after any change near the
   simulator; it exits 1 on a violation:
   `python3 apps/api/scripts/cpsim_boundary_check.py`.
2. **Its own role.** `cpsim_app` (NOLOGIN). The app's login role holds none of its privileges until a transaction says
   `SET LOCAL ROLE cpsim_app` — the app cannot read `cpsim.*` by accident.
3. **Never mistaken for real.** `is_simulated` CHECK; the `cpsim_` prefix; the SIMULATED banner and header.
4. **Who may connect** — the org setting `counterparty_sim.allowed` (default **false**; set only here, by a Super
   Admin, audited `counterparty.org_setting_changed`; the generic settings screen and the database both refuse it
   otherwise). `cpsim.may_connect(org, household)`:
   - setting off → false;
   - a **test** org → true (for its own households, or none);
   - a **real** org → true only for a household whose members are **all** tagged `demo-*` (the Olivo demo lives in the
     real 2nd Act org; whether it moves to its own tenant is still open — the rule works either way);
   - another org's household → false, always.
   cpsim02 calls it before pointing an org's Altruist connection at a simulator.

## 6. Operator notes (cpsim01)

- **Nothing to switch on yet.** `counterparty_sim.allowed` stays **false** for every org until cpsim02 connects the
  Altruist integration; no counterparty exists until a Super Admin creates one.
- Hosting: inside the existing API service (`2ndactcapital-api`), mounted at `/cpsim` — no `render.yaml` change, no
  new secret, no new login.
- Schema: `docs/cpsim01_part1.sql` (applied 2026-11-10).
- Verify: `apps/api/scripts/verify_cpsim01.py` (written, not yet run); in `scripts/regression_set.txt`.
