# Payments between a family's own accounts — operator runbook (CASH07, stage A: manual)

A payment instruction moves cash between two accounts of the SAME owner in the SAME household. Nothing is sent by the
system: the operator downloads a printable payment sheet and sends it to the custodian. Third-party payees are cash08;
automatic sending is cash09+ (Altruist write access, counsel). Design: docs/cash/CASH07_NOTES.md.

Screen: Household → Treasury → **Payments** (the existing Treasury tab). API: `/api/v1/cash/households/{id}/payments…`
(routers/payments.py). Service: `services/payments.py`.

## Before the first real payment (operator, once)

1. **Apply the schema** — `apps/api/scripts/_apply_cash07.py --all` (done by the sprint: SCHEMA OK, 2026-11-09).
2. **Grant trading authority** — `trading_authority_grants`: each person who will initiate or approve payments needs
   `limited` (or `full`) for the owning entity. There are **0 grants** today, so nobody can initiate yet. `inquiry` is
   view only and is refused.
3. **Record the custodian's rails** — Admin → Custodian capabilities (Super Admin, with evidence): `ach`, `wire`,
   `journal`, `check` for each custodian an account may pay from. Today none is declared (Altruist `unknown`, the
   held-away custodians have no rows). A custodian code with spaces (e.g. `Goldman Sachs PWM`) cannot hold a row until
   the account's custodian code is normalised.
4. **Account authority** — the source account must be **managed** (rep7b). Read-only and advise accounts are refused.
5. **Publish the disclosures** — `load_disclosure_defaults.py --drafter <super admin>` drafts `cash.payments` and
   `cash.sweep_rate`; a SECOND Super Admin publishes each (Admin → Disclosures). Until then the built-in text shows.
6. **A second checker at 2nd Act** — see "What waits" below.

## The lifecycle, step by step

| Step | Who | What the screen does | Status |
|---|---|---|---|
| Draft | holder of `initiate_payment` with trading authority | New payment instruction: source (accounts you may instruct), destination (same owner only), rail (declared only), amount, value date (a business day), purpose, optional link to an obligation / cash proposal | draft |
| Submit | a maker | opens the approval task (inbox) | pending_approval |
| Approve | a **different** holder of `approve_payment` (never a maker — Super Admin included) | Approve on the payment, in the inbox, or Approvals → bulk | approved |
| Send | operator | **Download payment sheet**, send it to the custodian (portal upload, secure message, email, fax…), then **Confirm sent** with the channel (and the custodian's reference if given) | sent |
| Consent (some custodians) | operator | **Member consent requested** when the custodian asks the member to e-consent | awaiting_client_consent |
| Acknowledged | operator | **Custodian acknowledged** (+ reference) | acknowledged |
| Settled | operator | **Settled** with the settlement date. ACH stays *provisional* through the return window (default 2 business days) | settled |
| Reconcile | operator | **Find matching feed rows** → tick the custodian transaction(s) → Reconcile; or confirm **manually** with a note — by someone OTHER than the sender | reconciled |
| Close | operator | **Close** (refused while an ACH payment is still provisional) | closed |
| Custodian refused it | operator | **Custodian rejected** + the custodian's reason | rejected |
| ACH return | operator | **Record an ACH return** with the code (R01 …) — from settled or reconciled | returned |
| Cancel | operator | **Cancel** + a reason, from any open state (a sent payment: ask the custodian to recall it first) | cancelled |

Every step writes one `payment_events` row (the timeline), an `audit_log` row (`cash.payment.*`) and a domain event.
Refusals (authority, third party, rail, value date) are audited as `cash.payment.refused`; a maker trying to approve is
recorded in `sod_decisions` (Admin → Segregation of duties → refused checks).

## Value dates

A U.S. business day (federal holidays per `services/us_business_days.py`), today or later. A weekend or holiday is
REFUSED and the next business day is named — the date is never moved silently.

## Reconciliation

The match reads the custodian transaction feed (`portfolio.transactions`): the source or destination account, the exact
amount, money OUT of the source / INTO the destination, dated value date − 2 … + 5 business days, not already used by
another payment. A settled payment not reconciled after 3 business days (setting
`cash.payments.unmatched_business_days`) raises ONE review task — **only when the sweep is on** (below).

## The sweep (expiry + unmatched) — switched OFF

`services.payments.sweep_tick` runs in the scheduler tick but returns at once unless the environment variable
`PAYMENT_SWEEP_ENABLED=1` is set on the cron service. With it on: drafts / pending / approved payments past their value
date expire; a payment awaiting the member's consent 5 business days past its value date expires; unmatched
settlements raise their review task. It never acts on a test org.

## What waits for a second checker at 2nd Act

2nd Act has ONE real org-admin checker today. A payment that person made cannot be approved by them — ever (the S30
floor: no organization setting and no role relaxes it). It waits until a second eligible person (an `approve_payment`
holder who is not a maker) approves it. **There is no bypass, by design.** Action: grant `approve_payment` (and
trading authority) to a second person.

## Adapters

Only **manual** ships (`services/payment_adapters.py`). An org setting `cash.payment_adapters` (a list of custodian
keys) "enables" a direct adapter for a custodian; with no connected implementation that custodian's payments are
refused with the reason ("not connected — use the manual route"). Adding a real adapter: the checklist in the module.

## Never

- A full account number in any screen, sheet, audit row, event or AI context (last four only).
- An agent submitting, approving or sending a payment (the custody cliff).
- A demo or real payment seeded by script — payments are never loaded for the demo family.
