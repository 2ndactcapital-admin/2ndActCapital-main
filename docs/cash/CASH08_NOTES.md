# CASH08 — payees, bank-detail controls, SLOAs, vendor invoices: discovery, decisions, reuse inventory (2026-11-10)

Repo at `e122089` (merge cash06) and the live database, read 2026-11-10. Standing rule (Joe, 2026-11-08): reuse the
app's existing constructs; the payee registry is the ONE new construct already justified (v1.2 header); anything else
new is named and justified here. Third-party payments stay OFF: nothing in this sprint can turn them on.

## 1. Source documents and findings

- **[FIND]** `CASH_TREASURY_ADDENDUM_v1.1_EXECUTION_ADAPTERS.md` (§5–§6) and `CASH_TRACK_PLAN_v1.2.md` (§2.4, §2.7,
  §2.10) are still not in the repo or in Downloads (cash00, cash06 and cash07 recorded the same). Nothing was invented
  from them: the concepts are taken exactly as the sprint prompt states them. `CASH07_NOTES.md`, `CASH_PAYMENTS.md`,
  `CASH06_NOTES.md` and `CASH_RULES.md` bind and were read first.
- **[FIND] Chancery has no 'sloa' and no 'invoice' document kind.** `reference_data` `doc_category` has 12 global rows
  (llc_formation … other) — no SLOA, no invoice; `documents` carries NO category column at all (the classifier's
  result sets only `doc_family` + `status`; `services/chancery_intake.doc_family_for_category`). cash05 met the same
  wall for statements / 1099s and decided: **a document's role lives in its `document_record_links` row** (polymorphic
  `record_type` text, no CHECK). cash08 follows that precedent exactly:
  - SLOA → link `record_type 'cash_payee_sloa'`, `record_id` = the payee id;
  - an invoice awaiting recording ("the manual tag") → link `record_type 'household_vendor_invoice'`, `record_id` = the
    household id (written through the existing `POST /documents/{id}/record-links`, or this sprint's tag route);
  - a recorded invoice → also linked `'cash_payee_invoice'` → payee and `'cash_obligation'` → the obligation lineage;
  - the counsel sign-off for the third-party switch → link `'third_party_counsel_signoff'` → the org id.
  Adding an `invoice` / `sloa` category to `reference_data` would only feed the classifier — a MODEL call this sprint
  must not make — so it is left to the classifier's own review queue (`doc_category_proposals`) when it proposes one.
- **[FIND]** `document_record_links` is NOT bi-temporal (plain insert / delete, `ON CONFLICT DO NOTHING`) and has one
  `ALL` RLS policy (org isolation). It cannot carry an SLOA's expiry / revocation / source accounts, so those live on
  the payee row (bi-temporal, below) — the link stays the Chancery-side pointer.
- **[FIND] cash07's same-owner decision** is `services.payments.same_owner_refusal` (service, inside `_validate`, every
  draft / edit / submit / rule draft) + `payment_instructions_same_owner_trg` (DB). Both compare two `accounts` rows —
  a payee is not an account, so a payee destination is NOT expressible in `payment_instructions` at all
  (`destination_account_id uuid NOT NULL REFERENCES accounts`). The third-party switch plugs in at the ONE place a
  payee destination could be asked for: `payments._validate`, before any account is loaded
  (`cash_payees.third_party_refusal`) — "third-party payments are not enabled". Even with the switch on the database
  still could not store one; enabling is a later sprint's schema change, after counsel (§6).
- **[FIND] The S30 path for a two-Super-Admin floor item**: `sod.Context.evaluate` refuses a checker who is a maker of
  a floor item type (Super Admin included); it knows nothing about WHO the maker / checker must be. "Two Super Admins"
  is therefore enforced by the service (both the requester and the approver must load as `is_super_admin` through
  `services.authz.load_principal` — user_roles, BUGFIX4), and "two DIFFERENT people" by the floor.
- **[FIND] Org settings**: `org_settings (org_id, setting_key, setting_value jsonb)` UNIQUE per key, one `ALL` RLS
  policy, read through `services.org_settings.get_setting` (DEFAULT_SETTINGS backstop). No status, no versioning, no
  maker-checker — the S29b autonomy switch needed its own versioned table for that. cash08 does NOT write the key: the
  default (off) is the platform default; turning it on is the governed request below, and that request cannot complete
  in this release.
- **[FIND]** `cash_obligations` already allows `source_type 'vendor_invoice'` with `cash_obligations_invoice_ref_chk`
  (`source_ref_type = 'document'`), and `cash_obligations_external_key_uq` is a live-row UNIQUE on
  `(org_id, external_key)` — the duplicate rule needs no new index (§5).
- **[FIND]** The platform rule "never store a card or bank account number" had no database backstop anywhere; the
  `accounts` table stores `account_number_masked` + `account_number_hash`. cash08 adds the first one, on its own table.

## 2. Why one new table (and nothing else new)

`public.cash_payees` — decided (v1.2 header). A payee is not an `entities` row (a CRM record carries no bank reference,
no SLOA, no approval status, and a vendor must not become a CRM member) and not an `accounts` row (no custodian
position, no owner). It needs: an approval status graph, maker-checker in the database (`approved_by ∉ maker_ids`), a
governed bank change with its call-back attestation, SLOA state, and a guard against numbers. **SYSTEM axis**
(`services.bitemporal.archive_live_row`): the payee's id is pointed at by its approval tasks, `sod_decisions`, SLOA /
invoice links and obligations' documents, so the live row keeps its id and every prior state is an archived copy
(`archived_from_id`, `system_to`) — the bank-change history is those copies.

No new link table (§1), no new settings table (§6), no new event table (`audit_log` `cash.payee.*` + `domain_events`
`payee.*` carry the history alongside the archived rows).

## 3. The payee

`household_id`, `kind` (vendor | charity | person | outside_entity), `display_name`, `legal_name`, contact
(`contact_name`, `contact_email`, `contact_phone`), bank (`bank_name`, `account_type` checking | savings | other,
`last_four` ^[0-9]{4}$, `payee_reference` — the custodian's / adapter's standing-instruction reference, free text the
operator copies), `notes`, SLOA columns, status `draft / pending_approval / active / suspended / retired`. No tax id
(the W-9 is a Chancery document). **No full account or routing number anywhere** — `cash_payees_no_numbers_trg`
refuses, with the column named:
- any run of 5+ consecutive digits in ANY free-text column (a full account number, an unseparated ABA);
- in every non-phone column, a 9+ digit run once spaces / dashes / dots / slashes are removed (an ABA written
  `021-000-021`);
- phone columns are digits with separators only, 7–25 characters, no run over 4 (write `212-555-0100`).
`last_four` has its own CHECK (exactly four digits). The service refuses first, with the same reasons.

## 4. Floor duties (S30)

| Item type (floor class `transfer`) | Maker duty → permission | Checker duty → permission | Where |
|---|---|---|---|
| `payee` | `create_payee` → `manage_payees` | `approve_payee` → `approve_payees` | create / edit / submit; approve |
| `payee_bank_change` | `change_bank_details` → `manage_payees` | `approve_bank_change` → `approve_payees` | request; approve with call-back attestation |
| `first_payment_to_new_payee` | `create_payee` | `approve_first_payment` → `approve_payment` | dormant (§6) |
| `third_party_payments_setting` | `request_third_party_switch` → `manage_org_settings` | `approve_third_party_switch` → `manage_org_settings` | request; approve (both Super Admin) |

Each has a platform `floor:` rule and a `policy:` (never; enforce_floor true) — no org setting and no role (Super Admin
included) relaxes it. Approvals are `services.human_tasks` tasks (kinds `payee_approval`, `payee_bank_change_approval`,
`third_party_switch_approval`) — in the inbox; `sod.TASK_KIND_ITEM_TYPES` maps each. New permissions `manage_payees`
/ `approve_payees` (no existing name fits: `initiate_payment` / `approve_payment` move money between a family's own
accounts and must not ride a payee grant, nor the reverse — D8 of cash07); org_admin by default.

**Bank change** (the fraud vector): a request on an ACTIVE payee stores the new details as `pending_*` and SUSPENDS the
payee (one transaction); the current (approved) details stay as they are. The approver must record the CALL-BACK:
who they called, the number they called, when (all required). The number must be the payee's `contact_phone` ON FILE
(compared as digits); a number matching the request's own contact details (`bank_change_request_contact` /
`_phone`) is refused even if it also matches the file — the request's channel is never the verification channel. The
requester never approves (floor). Approve → pending details become current, status active, attestation recorded on
the live row (CHECK: an approved change has all three call-back fields and an approver ≠ requester); reject → pending
details discarded, the previously approved details stand, status active. Every state is an archived copy.

**First payment to a new payee**: the first instruction to a payee needs a checker who is neither the payee's creator
nor its approver: `cash_payees.first_payment_gate` = `sod.gate(first_payment_to_new_payee, maker_ids = [creator,
approver] ∪ the instruction's makers)` + the pure `first_payment_conflict`. Wired into `payments._precheck` /
`_apply` behind `row.get("payee_id")` — no instruction can carry a payee today, so it is dormant, and unit-proved.

## 5. Vendor invoices

Chancery intake (S26) → the document is tagged `household_vendor_invoice` (§1) → Treasury → Invoices lists the
household's tagged invoices not yet recorded → a staff member records it: payee (an ACTIVE payee of the household),
the paying entity (a household member), invoice number, amount (typed — no model call), due date →
`cash_obligations.create` (`source_type vendor_invoice`, `certainty firm`, `source_ref_type 'document'`,
`external_key = vendor_invoice:<payee id>:<normalised invoice number>:<amount>`) — so it is in coverage (cash01
`cash_coverage` reads stored obligations) at once — plus the `cash_payee_invoice` / `cash_obligation` links, in ONE
transaction. A second record with the same payee + invoice number + amount is refused by the service and, as a
backstop, by `cash_obligations_external_key_uq`. Permission: `manage_portfolio` (cash01's obligation write) AND
`manage_payees`.

## 6. Third-party payments — the switch

`cash.third_party_payments` (org setting, default off: `services/org_settings.DEFAULT_SETTINGS` is not touched; an
absent row is off). Changing it is a `third_party_switch_approval` task: the requester must be a Super Admin and must
name a counsel sign-off document of the org (linked `third_party_counsel_signoff`); the approver must be a DIFFERENT
Super Admin (floor + service). **Release lock**: `cash_payees.THIRD_PARTY_RELEASE_LOCKED = True` refuses the approval
of an ON request in this release ("counsel review first — Joe"), leaving the task open and the setting untouched; the
reader `third_party_enabled` is only true for a stored JSON `true`, which nothing writes. Enabling later needs: counsel's
written sign-off, the lock removed in code review, `payment_instructions` able to carry a payee (schema + the
same-owner trigger), SLOA enforcement wired to the send path, and the first-payment gate live.

## 7. SLOA

Attach (a Chancery document of the org, the payee's source account(s) of its household, signed / expiry dates) →
`sloa_*` columns + the `cash_payee_sloa` link; expire (an operator records the custodian's notice) and revoke (with a
reason) are new states on the same row. Flags (computed on read, `cash_payees.flags`): `sloa_missing`,
`sloa_expired` (marked, or `sloa_expires_on` < today), `sloa_revoked`, `sloa_expiring_soon` (≤ 30 days),
`bank_change_pending`, `suspended`. A flagged SLOA will block payment when third-party payments are enabled
(`sloa_blocks_payment`); today it only flags.

## 8. Disclosure

`cash.third_party` (risk, both) — fallback `cash_payees.THIRD_PARTY_DISCLOSURE`, default text
`docs/disclosures/defaults/cash.third_party.md` (DRAFT; a second Super Admin publishes). Shown on the payee screens.

## 9. Visibility

Same as payments (`routers/payments._open`): read `view_portfolio | manage_payees | approve_payees`; members their
family's payees READ-ONLY (display name, kind, last four, status, the disclosure — no reference, no contact, no SLOA
document); internal non-staff through VISIBILITY1; another family's household is a 404.

## 10. Reuse inventory

| Need | Construct |
|---|---|
| maker-checker (floor) | `services.sod` (gate / enforce / record), the S30 catalog (+4 item types, +7 duties) |
| approval + inbox | `services.human_tasks` (open / complete / cancel, prechecks, `task_makers`) |
| archive (system axis) | `services.bitemporal.archive_live_row` |
| document roles | `document_record_links` (cash05 precedent), `services.document_linkage.link_document_to_record` |
| obligations + coverage | `services.cash_obligations.create` (`vendor_invoice`, `external_key`), `cash_coverage` |
| Super Admin | `services.authz.load_principal` (user_roles) |
| settings | `org_settings` via `services.org_settings.get_setting` |
| disclosures | `services.disclosures` (+ `cash.third_party`) |
| visibility | `authz.member_entity_scope` + VISIBILITY1 (`family_visibility.scope_for_request`), payments' `_open` shape |
| audit / events | `audit_log` (`cash.payee.*`, `cash.invoice.*`, `cash.third_party.*`), `domain_events` (`payee.*`) |
| screens | the existing Treasury tab (`TreasuryDaily` → `TreasuryPayees`, `TreasuryInvoices`) |
