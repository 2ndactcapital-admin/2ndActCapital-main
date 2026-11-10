# Payees, bank-detail changes, SLOAs and vendor invoices — operator notes (CASH08)

A **payee** is someone a household may one day pay who is not one of its own accounts: a vendor, a charity, a person or
an outside entity. CASH08 builds the registry and its controls. **Third-party payments stay OFF**: no payment
instruction can be made to a payee in this release, and the switch that would allow it cannot be turned on (counsel
first). Design: docs/cash/CASH08_NOTES.md.

Screens: Household → Treasury → **Payees** and **Invoices** (the existing Treasury tab). API:
`/api/v1/cash/households/{id}/payees…`, `/…/invoices…`, `/api/v1/cash/third-party…` (routers/cash_payees.py). Services:
`services/cash_payees.py`, `services/cash_invoices.py`.

## Before the first real payee (operator, once)

1. **Apply the schema** — `apps/api/scripts/_apply_cash08.py --all` (done by the sprint: SCHEMA OK, 2026-11-10).
2. **Grant the permissions** — `manage_payees` (create, edit, submit, bank changes, SLOAs, record invoices) and
   `approve_payees` (approve payees and bank changes). Both are org_admin defaults. Recording an invoice also needs
   `manage_portfolio` (cash01's obligation write).
3. **A second approver** — every approval is by a DIFFERENT person (the S30 floor; Super Admin included). At 2nd Act,
   with one real org-admin checker today, a payee that person created waits until a second `approve_payees` holder
   approves it. There is no bypass, by design.
4. **Publish the disclosure** — `load_disclosure_defaults.py --drafter <super admin>` drafts `cash.third_party`; a
   SECOND Super Admin publishes it (Admin → Disclosures). Until then the built-in text shows.

## Payee onboarding

| Step | Who | What | Status |
|---|---|---|---|
| Create | `manage_payees` | kind, display name, legal name, contact, the **call-back number on file**, bank name, account type, the **last four** digits and the **custodian's standing-instruction reference** (copied from the custodian) | draft |
| Edit | a maker | any field while draft; every editor becomes a maker | draft |
| Submit | a maker | opens the approval task (inbox) | pending_approval |
| Approve | a **different** `approve_payees` holder (never a maker — Super Admin included) | on the payee or in the inbox | active |
| Reject | the approver, with a note | back to draft | draft |

**Never enter a full account or routing number** — anywhere, in any field. The database refuses any run of more than
four digits in every text field, any nine-digit (routing-like) number written with dashes or spaces, and a last-four
that is not exactly four digits; the refusal names the field, never the value. Write phone numbers with separators
(`212-555-0100`). The payee's tax form (W-9) is a Chancery document, not a field.

## The bank-change call-back procedure

A request to change a payee's bank details is the most common payment-fraud route. The platform treats it so:

1. **Request** (`manage_payees`, on an ACTIVE payee): the new bank name, account type, **last four** and custodian
   reference, plus **how the request arrived** (who, which channel — e-mail address, phone number given) and any phone
   number given in the request. The payee is **suspended at once**; its approved details stay as they are; the new ones
   wait as "pending". The approval task goes to the `approve_payees` holders.
2. **Call back** (the approver — never the requester): phone the payee on the number **on file** — the one shown on
   the attestation form, recorded when the payee was approved. **Never call a number from the request**, even if it
   looks familiar. Confirm the new details by voice with a person you can name.
3. **Attest and approve**: record who you spoke to, the number you called and when. All three are required. The
   platform refuses an approval without them, a number that is not the one on file, and a number taken from the
   request (even if it also matches the file). The requester can never approve (the floor). The generic inbox cannot
   approve a bank change — the attestation form on the payee is the only way.
4. **Result**: approved → the new details become current, the payee active again, the attestation kept on the record;
   rejected (with a note) → the pending details are discarded, the previously approved details stand, the payee active
   again. Every state is kept (the payee's history), and every step is in the audit log.

If the payee has no call-back number on file, a bank change cannot be verified: retire the payee and create a new one
through approval.

## SLOA handling

A Standing Letter of Authorization is a Chancery document the member signs with the custodian. On the payee:
**Attach SLOA** (the Chancery document, the household's source account(s) it covers, signed and expiry dates) — it is
linked in Chancery (`document_record_links` `cash_payee_sloa`). **Mark SLOA expired** when the custodian says so;
**Revoke SLOA** (with a reason) when the member withdraws it. A payee is FLAGGED when its SLOA is missing, expired (marked,
or past its expiry date), revoked, or expiring within 30 days. When third-party payments are enabled, a missing,
expired or revoked SLOA — or one not covering the source account — **blocks payment**; today it only flags.

## Vendor invoices

1. Upload the invoice to Chancery (intake). Chancery has no invoice category, so **tag** it as one of the household's
   invoices (Treasury → Invoices, or `POST /invoices/tag`).
2. **Record invoice** (`manage_payees` + `manage_portfolio`): choose an ACTIVE payee, the paying member, type the invoice
   number, the amount and the due date from the invoice (nothing is read from the document automatically).
3. The invoice becomes a **firm** cash obligation (`vendor_invoice`) — it appears in the household's coverage at once —
   linked to the document and the payee.
4. The same payee + invoice number + amount is recorded **once**; a second record (or the same document twice) is
   refused. Recording an invoice pays nothing.

## What enabling third-party payments will require

The org setting `cash.third_party_payments` is OFF for every organization and cannot be turned on in this release. To
enable it later, ALL of the following:

1. **Counsel's written sign-off** on third-party payments (custody, SLOAs, disclosures, the call-back procedure) —
   filed in Chancery as the **counsel sign-off document**.
2. **Two Super Admins**: one asks (Admin → `POST /api/v1/cash/third-party/request`, naming the counsel document); a
   DIFFERENT Super Admin decides (the S30 floor `third_party_payments_setting`). One Super Admin alone, or a request
   without the counsel document, is refused.
3. **A code release** removing `THIRD_PARTY_RELEASE_LOCKED` in `services/cash_payees.py` (today it refuses every
   approval of the switch), reviewed after counsel signs off.
4. **The payment path for payees**: `payment_instructions` able to name a payee (schema + its database trigger), the
   SLOA check on the send path, and the first-payment rule live (the first payment to a payee is approved by someone
   who neither created nor approved the payee — built and dormant).
5. **The disclosure published**: `cash.third_party`, reviewed by counsel.

Until then a payment instruction naming a payee is refused: "third-party payments are not enabled".

## Never

- A full account or routing number in any field, screen, audit row, task, event or log line (last four only).
- A bank change approved without a call-back to the number on file, or by its requester.
- The third-party switch turned on by script, by one person, or without counsel's document.
- A demo or real payee seeded by script.
