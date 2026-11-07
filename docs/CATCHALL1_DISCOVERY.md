# CATCHALL1 — Task 1 discovery (2026-11-07)

Branch `catchall1-work`, base `8b7e8f2` ("merge s28"). Repo read + live DB (project mmgwmcinimzuhargsazs).

## 1a. Bulk approve — the two-step flow

`components/approvals/BulkApprove.jsx` (state: `selected`, `asking`, `note`, a local `busy`) over the pure
`lib/bulkApprove.mjs` (`bulkControls`, `confirmation`, `requestBody`, `resultLines`). The first buttons were
**"Approve selected" / "Reject selected"** — they only set `asking`, which rendered `ConfirmView` (a `role="dialog"`
card) **below the table**, off screen on a long list; nothing scrolled or focused it. Its final button
(`{decision label} {N}`) POSTs `/api/approvals/bulk`; while pending only `disabled` changed — no label, no spinner,
so "nothing visible happens until the batch finishes" (Joe). Cancel = `setAsking(null)` (selection already kept);
no Escape.

**Built (Task 2):** first buttons "Review N selected →" / "Review N to reject →" (`reviewLabel`); the confirmation is
scrolled into view and focused on open (`tabIndex=-1`, `scrollIntoView`, `focus`), Escape = Cancel, both keep the
selection (`cancelConfirmation`); final button "Approve N" / "Reject N" (`finalLabel`) on the shared busy indicator.

## 1b. Slow actions with no busy state — and no shared component

No shared busy component or hook existed (`BusyButton` / `useBusy` / `aria-busy`: zero hits); ~20 screens kept a local
`const [busy, setBusy]`, ~9 hard-coded "Working…", and these had NO busy state at all: LiabilitiesTab Recalculate,
HeldAwayPanel upload / extract / approve, IntegratedImport upload / approve, OrgSources upload, DataRoom uploads /
replace PDF / agreement upload, InstructionDetail letter + order sheet PDFs, BlockDetail sheet, CharitableGifts
letter, HarvestDashboard refresh, AccountModelPanel rebalance, ProposalDetail check instructions, EntryView refresh /
re-extract, BacktestWorkspace run, ConsolidatePanel, SecurityMaster run-now. The full list (34 files) is
`SLOW_ACTION_FILES` in `apps/web/tests/catchall1/catchall1.test.mjs`. Not in the web UI at all: narrative
generation, Altruist sync, a roll-up button (Recalculate is the roll-up).

**Built (Task 3):** `lib/busyAction.mjs` (pure: `runBusy`, `busyButtonProps`, `jsonOrThrow`) + `components/ui/Busy.jsx`
(`useBusy`, `BusyButton`, `BusyError`, `Spinner`) on every one of them; every local busy copy removed.

## 1c. The direct-asset model

* **Tables:** `portfolio.assets` (one org row, `asset_type` = the kind, `default_taxonomy_key`, `notes`,
  `manual_terms`), one PERCENT-basis `portfolio.positions` row per owner (`ownership_pct`, `market_value` NULL,
  `as_of_date` = the first valuation's date), `portfolio.valuations` (a new valuation is a new row).
* **Add path:** `POST /entities/{id}/direct-assets` → `direct_holdings.add_direct_asset` (gate
  `require_internal_permission("manage_members")`, org admin admitted); key by kind via
  `portfolio_kind_taxonomy.key_for_kind` inside `portfolio_assets.create_asset`.
* **How the net-worth core reads it:** `services.networth` → `portfolio_rollup._current_positions` (current on both
  axes, latest `as_of_date <= as_of` per owner / asset / account, precedence winners) → `position_current_value`
  (pct × the asset's latest valuation ≤ as_of). The card (`GET /entities/{id}/net-worth`), Recalculate, the household
  total and the stored snapshot all use that ONE function.
* **What "ended" means — decided:** NOT `valid_to` on the positions (their `valid_to` means "superseded by a
  restatement": stamping it would also drop the asset from every as-of BEFORE the end) and NOT a zero restatement
  (a $0 line still counted). A **disposal row**: new `portfolio.asset_disposals` (docs/catchall1_part1.sql, APPLIED
  and confirmed live: 4 RLS policies one per operation with NULLIF, reason CHECK, one current end per asset).
  `_current_positions` counts a position only while no current disposal of its asset has `end_date <= as_of`.
  "Entered in error" = `end_date` = the earliest `as_of_date` of its positions (never counted). Nothing deleted.
* **Private-investment edit pattern (reused):** `edit_private_investment` — archive EVERY column of the outgoing
  asset version (`system_to = now()`), update the live row in place keeping its id, audit
  `portfolio.private_investment_edited`; history from the archive + audit rows.
* **Reclassify:** `backfill_taxonomy_keys.py --reclassify` resolved kind → key inline. Now ONE function,
  `portfolio_kind_taxonomy.reclassify_target`, used by the script AND by the direct-asset Edit.
* K.19 "Family office working capital" (eddf9851…) shows "Estimate (mark to model)" — demo data, NOT edited here.

## 1d. Held-away option line → picker → rep8

`HeldAwayPanel` (listed_option row) → `components/heldaway/OptionContractPicker.jsx` → `GET
/portfolio/option-contracts?underlying_ticker=` (portentry, gate `view_held_away`; reads
`portfolio.option_contracts` on the master underlying; empty → "No option contract on SPY exists yet. A contract is
defined in Replication → Options."). rep8: `POST /replication/options/contracts` (gate **manage_replication**, Super
Admin first) → `options_sleeve.find_or_create_contract` — **idempotent on `terms_key`** (underlying, type, strike,
expiry, style, settlement, flex, multiplier, mini); platform rows written under `platform_tx`; DB guard: never
deleted, terms immutable; partial unique `option_contracts_terms_uq`. **No maker-checker** on contract definition
in rep8 (the package approval is where rep8 has one) — so none is added. Fields a listed contract needs:
underlying (master id), call / put, strike, expiry, exercise style, settlement (index → cash, stock / fund →
physical), is_flex, multiplier (100), broad_based_index (index only), mini (+ ratio).

**Built (Task 5):** the picker envelope publishes `define_contract` only for a caller holding manage_replication,
with the master underlying and its kind; "Define this contract" posts rep8's own route; same terms → the existing
contract (selected, "already existed").

## 1e. The three Lows

* **Liability labels vs the plan** (`docs/demo/olivo/HOLLISMUSE_OLIVO_DATA_ENTRY_v4.md:851-859`): the plan calls BOTH
  L.1 (securities-backed) and L.7 (art-secured) a **"Secured credit line"**; the labels were "Securities-backed line
  of credit" / "Asset-secured line of credit" (`services/crm_vocab.py LIABILITY_KINDS`). Now "Secured credit line —
  securities" / "Secured credit line — other assets (art, property)"; keys / `liabilities_kind_chk` unchanged.
* **Lists that render empty / stale until re-read:** `HouseholdManager` (a reopened household card never re-read:
  `if (next && !detail) await load()`; no `no-store`); `EmploymentTab`, `TaxIdsTab`, `SocialTab`, `ComplianceTab`
  (state from a prop once — the cause bugfix3 fixed only in AddressesTab). Fixed. Left (named, not changed):
  `EntityDetailTabs` Overview "Owns" tree uses the server-rendered graph prop (OwnershipTab re-reads its own copy).
* **Pickers refusing paste / autofill:** none searched on key events only, but four did not search on a CHANGE:
  `HouseholdManager`'s local EntityPicker (no debounce, no stale guard, no `onInput`), `tax/common.jsx`
  SecurityPicker (button only), `execution/NewInstruction` (submit only), `OptionContractPicker` (button only).
  All now on the shared `components/ui/useTypeahead.js` (value-driven, debounced, stale-safe, `onChange` + `onInput`).

## 1f. The pre-existing verify FAILs

* **verify_goldset — 422 on `proceeds_to_issuer_usd`: a VERIFY defect.** bulkfetch (1ef399c) added the CALCULATED
  registry rows `total_fees_usd` / `proceeds_to_issuer_usd`; `PUT …/gold/notes/{id}/fields/{key}` refuses them (422
  "is calculated … never edited") and `_reviewable` leaves them out of progress, but the verify (written before)
  PUT every live field and expected `fields_total == len(live)`. Fixed in the verify: reviewable = live − calculated
  (from `derive.CALCULATED_FIELDS`), plus a negative check that each calculated field is refused and nothing written.
  notefix1 / notefix2 nest goldset and inherit it. Not runnable in isolation (needs the full fixture flow).
* **verify_previewperf — 2 FAIL:** (a) its merge 22b4c0d brought previewperf AND its fix 1 in together, so its "own
  end" (the merge's second parent) was fix 1's tip and fix 1's files looked foreign; (b) the DDL scan matched prose —
  the sprint prompt's STANDING RULES sentence "except a new index (CREATE INDEX CONCURRENTLY, …". Fix: `_verify_self.
  SPRINT_PINNED` (previewperf ca39a17 on d9afcea; fix 1 58a6217 on ca39a17) and the scan limited to `apps/` +
  `docs/*.sql`. → 4/0.
* **verify_previewperffix1 — raised:** "previewperf fix 1" has no merge naming it, so it resolved to HEAD (base
  8b7e8f2, not an ancestor of ca39a17). Fixed by the same pin; then its OWN_FILES lacked `.gitignore`, which its own
  commit changed ("ignore Claude outputs/"). → 5/0.
* **verify_vhk2 — 1 FAIL:** its frozen-state scan covers today's regression set, so scripts added after it raised 12
  hits. 11 are true invariants (a pinned git record, the script's own fixtures, committed fixture CSVs, synthetic bars)
  → listed in `INVARIANTS` + `docs/VHK2_FINDINGS.md`. One is REALLY frozen against live state —
  `verify_bugfix2.py len(changed) == 45` reads TODAY's AUTHZ1_ROUTES.json — handed off to verifyfix1 as a `[FIND]`
  (`HANDED_OFF`), not whitelisted. → 41/0.
