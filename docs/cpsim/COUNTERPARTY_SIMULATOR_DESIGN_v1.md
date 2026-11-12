# Counterparty Simulator — design v1 (2026-11-10)

Status: **agreed (Joe, 2026-11-10); four sprints, cpsim01–cpsim04 — v1 COMPLETE (2026-11-11).** cpsim01–cpsim03
merged; cpsim04 built (2026-11-11, see `CPSIM04_NOTES.md`; verify written, not yet run). A semi-independent module that plays the other side of
Hollisworks — custodians and banks — so the app can be tested and demoed end
to end without a real counterparty. Altruist first.

## 1 · Why

- Altruist never answered the dev-environment request (2026-09-30). The
  Altruist integration (OAuth, households, accounts, positions, transactions,
  webhooks) has **never made a live call**; it is proven only against mocked
  payloads.
- Order routing (Replication Studio), payments (cash07), reconciliation
  against a custodian feed, ACH returns, ladder maturities and the cash
  waterfall all need a counterparty that answers back.
- HollisMuse and the Olivo demo get something real to run against.

## 2 · Principles (decided)

1. **Separate book, shared reference data.** The simulator READS the security
   master and prices. Its own accounts, positions, transactions, orders and
   payments live in its own schema (`cpsim`) and reach Hollisworks only
   through the API, webhooks and files — exactly as a real custodian's would.
   It never writes Hollisworks' portfolio tables. (If it did, reconciliation
   would compare a table with itself.)
2. **Semi-independent, hosted in the existing API service** (Joe,
   2026-11-10 — no new Render service). A self-contained package
   (`apps/api/cpsim/`) mounted at `/cpsim`, with its own key authentication
   (not Hollisworks sessions) and its own database role `cpsim_app` (read
   reference data, write `cpsim.*`, nothing else). Hollisworks talks to it
   over HTTP with a URL and credentials, like any counterparty; the package
   can be lifted into its own service later without surgery.
3. **Never mistaken for real.** The registry holds only simulated
   counterparties (a database CHECK); an org can point at one only when the
   org setting `counterparty_sim.allowed` is on (default off; Super Admin,
   audited) — and in a real org only for households tagged demo (§6 D1);
   every screen and every simulator response says SIMULATED; simulator
   credentials carry a `cpsim_` prefix and anything else is refused.
4. **Reuse the app's constructs.** The existing Altruist client, execution
   routers, payment adapters, custody import, Chancery, security master,
   prices and audit log. New tables only for the simulator's own book.
5. **v1 is stateless; v2 keeps ledgers.** v1 validates a request and answers
   with a correctly shaped response ("properly formatted in → mirrored
   out"); the "feed" it serves is whatever was uploaded. v2 adds books that
   move (fills change positions, payments change cash).

## 3 · The admin page (cpsim01)

Admin → Counterparties (Super Admin). Create a counterparty:

- **Name** and **Institution type** — Altruist-compatible, Schwab, Domestic
  bank, International bank (a table, so types can be added; the type defines
  the protocol and the fields shown).
- **Destination URL** (where Hollisworks sends).
- **Fake key** — generated, `cpsim_` prefixed, shown once, stored hashed.
- **FIX session** — BeginString, SenderCompID, TargetCompID, host, port,
  heartbeat interval, reset-on-logon. *Stored now; no FIX engine in v1.*
- **Bank details** — ABA routing number (wire / ACH), account number;
  SWIFT / BIC, IBAN, intermediary bank (international wire). Fake values
  only, generated so they can never be real (an ABA that fails the real
  check digit, an invalid IBAN), held in `cpsim` only — the platform's
  no-raw-bank-numbers rule for real data is unchanged.
- **Behaviour** — scripted outcomes: accept, reject (with reason), partial
  fill, delay, ACH return (code + day), webhooks on / off.
- **Clock** — the counterparty's business date, with "advance one business
  day" (later: settles what is due, fires maturities and returns).
- **Bulk import / export** (cpsim02) — accounts, holdings and values,
  transactions, later orders and payments: CSV / JSON, validated against the
  security master, stored as the counterparty's book and served back through
  its API and as downloadable custodian-style files.

## 4 · Altruist first

The simulator answers the same paths the existing client already calls
(services/altruist_oauth.py): OAuth authorize + token (and refresh),
households, accounts, positions, transactions; and it SENDS webhooks signed
the way services/altruist_webhook.py verifies. It is added as a third
environment beside stage and production ("simulator"), so every existing
code path runs unchanged against it.

Fidelity rule: the contract is written down once (docs/cpsim/
ALTRUIST_CONTRACT.md — each field marked documented vs assumed) and becomes
a conformance suite that can run against the simulator **and** against
Altruist's real sandbox the day access arrives. Any difference found then is
a simulator defect.

## 5 · Sprints

| Sprint | Scope |
|---|---|
| **cpsim01** | The Altruist contract written down; the `cpsim` schema, role and mounted sub-app; counterparty registry + admin page; safety rules; fake-credential generators; behaviour settings and clock (stored); one working mirror endpoint. |
| **cpsim02** — done 2026-11-10 (verify written, not run) | Altruist read API (OAuth, households, accounts, positions, transactions) + bulk import / export; webhooks out; the existing Altruist integration connected to it for an allowed org; the conformance suite. |
| **cpsim03** — done 2026-11-11 (verify written, not run; `CPSIM03_NOTES.md`) | Orders: the simulator accepts orders from the Altruist execution router, answers with scripted outcomes (accept / reject / partial / fill at the last price in the price table), reports fills; Hollisworks reconciles. |
| **cpsim04** — done 2026-11-11 (verify written, not run; `CPSIM04_NOTES.md`) | Payments and banks: the payment adapter for a simulated custodian and the two bank types — acknowledge, settle, return (ACH codes), status feed, member consent; a cash ledger (transfers and fills move cash); the clock drives settlement and returns. **v1 COMPLETE.** |
| **cpsim05** — built 2026-11-11 (verify written, not run; `CPSIM05_NOTES.md`) | A SIMULATED FIX counterparty: FIX 4.4 tag=value over HTTP (D/F/H/J → 8/9/P, Reject 3); an approved proposal's trades for simulated accounts sent AUTOMATICALLY (the proposal's maker-checker is the approval), fills booked, synced, reconciled, realized / unrealized P/L shown; blocks on fix_sim. Demo only. |
| **cpsim06** — built 2026-11-12 (verify written, not run; `CPSIM06_NOTES.md`) | ACCOUNT OPENING, Realtime-API-shaped (every path ASSUMED): password-grant token (5 min access, fixed 10 h refresh), households / contacts / account drafts; "in good order → opened at once, in the accounts feed" (Joe's rule — no client approval); every problem listed field by field against Altruist's published rules (YES / NO account types, US residency, SSN / ITIN, visas, unique email, beneficiaries). Hollisworks "Open account at custodian" on a CRM household → the EXISTING add_account + the crosswalk (one transaction) + an immediate sync. Admin → Counterparties → Realtime tab. Simulated only. **Account opening done; the cycle end: the full regression run (`VERIFY_REGRESSIONS=full`).** |
| later (v2) | A FIX engine (if a custodian needs it); Schwab file formats; multi-day scenarios; fault injection (timeouts, partial outages, duplicate / out-of-order webhooks); fees and corporate actions in the book; banks on their own clocks; third-party payees (after counsel). |

Not in v1: FIX sessions, real file formats for Schwab, independent ledgers.

## 6 · Decisions

- **D1 — who may connect to a simulator (open).** The Olivo demo family
  lives inside the 2nd Act org (real tenant). cpsim01 builds the rule so
  either answer works: test orgs freely; a real org only with the setting on
  and only for households tagged demo. Still to decide: move the demo to its
  own tenant (recommended; already an open decision — "Olivo: host tenant").
- **D2 — hosting (decided, Joe 2026-11-10).** Inside the existing Render API
  service; no new service.
- **D3 — Altruist developer terms (open, counsel).** Internal, unbranded
  "Altruist-compatible" simulator; confirm their terms do not forbid it.
- **D4 — order in the queue (proposed).** After verifyfix1 and the full
  regression run, before the HollisMuse pass (so Muse tests against it).

## 7 · Challenges noted

Fidelity without sandbox access (the conformance suite is the mitigation);
keeping simulated and real data apart (database-level rules, not just UI);
"mirror" needing scripted outcomes to be useful; time (the clock); FIX being
a large build with no current consumer; fake bank numbers vs the platform's
bank-number guard (kept in `cpsim` only).

## 8 · v1 COMPLETE (2026-11-11) and the v2 list

v1 (cpsim01–cpsim04) is complete: registry + admin, the Altruist-compatible read API, books and webhooks, orders and
fills, transfers, banks, consent, returns and a cash ledger — every flow person-driven, every contract field ASSUMED
and pinned by the conformance suite (C, O and T groups).

v2 candidates (none scheduled): a **FIX engine** (Schwab-style sessions are stored, never spoken); **Schwab file
formats** (custody import files instead of an API); **multi-day scenarios** (scripted calendars: maturities, coupon and
dividend dates, standing transfers); **fault injection** (timeouts, 5xx bursts, duplicate and out-of-order webhooks,
partial fills over days); **fees and corporate actions** in the simulator's book; **banks on their own clocks** (today a
bank's legs are booked on the sending custodian's date); **third-party payees** — only after counsel, with cash08's
switch.
