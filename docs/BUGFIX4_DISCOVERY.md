# BUGFIX4 — Task 1 discovery (HollisMuse Olivo final report, 2026-11-06)

All evidence below was read live (read-only SQL, 2026-11-06) or from the code at `be14df0`. Nothing was written.

## 1a. Held-away apply = an account SNAPSHOT (hypothesis CONFIRMED)

**Code.** `services/held_away._apply_import` step 1 closes EVERY live position of the account (and its open lots,
and their provenance in `held_away_records` with `superseded_reason = 'replaced by held-away import <id> as of
<date>'`) before writing the import's own positions. The only guard is "the account already holds NEWER data" — an
import at the SAME as-of is accepted and replaces. So an approved import is the account's whole book as of its date.

**Front end.** `HeldAwayPanel.jsx` "Save as draft" posts `rows: [manualRow(row)]` — ONE line per draft.

**Olivo evidence (default org, manual imports, `system_to IS NULL`).** 58 approved manual imports, 0 drafts;
2 of them have no position line; the other 56 carry exactly ONE position each (`max(lines) = 1`); 24 accounts, each
with ONE as-of (24 distinct (account, as-of) pairs). Position provenance: 24 live, **32 superseded**, across **13
accounts**, every superseded line replaced by an import of the SAME account and the SAME as-of. So per-line drafts
for one account replaced each other in APPROVAL order — the last approved line per account is the only live one.
The import row itself still reads "Approved · read 1, accepted 1, rejected 0": nothing on the import said its lines
were no longer live.

## 1b. Roll-up vs X-ray valuation — the brief's premise is WRONG; the cause is CLASSIFICATION

`portfolio_rollup._position_value` already uses the entered `market_value` first (units basis), falling back to
quantity × resolved valuation only when no value was entered; a missing price series never excludes a valued line.
The X-ray sums `market_value` (any line with a value; "unclassified" sector allowed).

The four named lines all HAVE a market value and are skipped for having **no taxonomy key**:
SCHR 5,220,000 + SCMB 882,000 + "cash" (QQQ) 1,050,000 + ETH ETF 393,600 = **7,545,600** exactly. Each resolved to
a security-master row, so `held_away._asset_for` created the org asset with `asset_type = securities_global.
security_type` (`fund`, `equity`) and `key_for_kind('fund')` → none (bugfix3's map has the manual kinds only). Live:
25 `fund` assets and 5 `equity` assets, ALL unkeyed. (SCHB 1,687,000 is unkeyed the same way, outside the four.)
Master types live: equity 8,666 · fund 6,133 · other 917 · fixed_income 187 · structured_note 54 · index 13.

Also: a line resolved to the master DROPS its `asset_kind` (only a non-master line keeps `asset_ref`), so even a line
entered as "Money market fund" with a ticker that the master knows loses its kind.

**Real second defect found: the household gross DOUBLE-COUNTS.** `household_admin.household_detail` / Recalculate
read `households._sum_holdings(member_ids)` = Σ each member's `entity_holdings`; but each member's buckets already
include the look-through share of every entity it owns (`rollup_entity_holdings` writes ancestor buckets). The Olivo
household's 40 members include owners AND the entities they own (e.g. Olivo Holdings LP 45589654 is 30 % / 34.5 % /
10 % × 3 / 5 % / 0.5 % owned by other members), so the same position is summed once per owning member.

K.19 working capital ($1,400,000, kind `other`): bugfix3 left `other` unmapped by design; its creation form called
it "other (cash)". The only `other` asset live is this one.

## 1c. Every platform job "schedule not active" — by design, pending the operator

The four SECMASTER1 jobs live in the Hollisworks org (bb347258…). `scripts/_apply_secmaster1.py --seed-jobs`
seeds the triggers with `active=False` on purpose ("inactive until --activate-jobs (after deploy)"); `--activate-jobs`
was never run. Live: 4 scheduled triggers, all `is_active = false`, `last_fired_at` NULL (the screen shows "never"
from the run list, and "schedule not active" from `trigger_active`). Each job has exactly ONE run, all "Run now"
at 2026-11-06 20:10 UTC: nightly, 13F, security-data nightly **completed**; **attributes is HELD**
(`FinanceDatabase .../etfs/ASE.csv` 404) — a held run blocks its trigger until a Super Admin closes it, so switching
attributes on would not fire until that run is closed. There was no switch in the UI. `price_coverage = 'unknown'`
everywhere: the security-data nightly has no free end-of-day price provider configured.

## 1d. The entry form

* **Stale security.** `HeldAwayPanel` keeps ONE `row` state across saves and never resets it; the security search
  writes `ticker`/`cusip` into it, and `manualRow` sends `ticker`/`cusip` for EVERY kind. A25's "Cash / sweep" line
  carried the previous line's CUSIP → `resolve_manual` tried the master FIRST for every kind and resolved
  "master by CUSIP" → QQQ (live: payload `resolution = 'master by CUSIP'`, label "Cash / sweep", asset QQQ).
* **Digital asset.** `manualTermFields('digital_asset')` shows a ticker; `resolve_manual` looks every kind up in the
  master's tickers, so ETH → Grayscale Ethereum Staking Mini ETF and "Bitcoin" (BTC) → an ETF the same way
  (both payloads `resolution = 'master by TICKER'`).
* **Quantity.** `validate_row` rejects `quantity_missing` before it looks at the kind, so cash / value-only lines
  needed qty = amount @ 1.0.

## 1e. The stub entities

`EntityPicker` (`allowCreate`, used by CRM **Employment** — employer, `createEntityType` llc — and Ownership)
shows "+ Create '<text>'" in the dropdown, fired on **mousedown**, and POSTs `/entities/stub` at once (a
confirmation appears only when an exact-name duplicate exists). `microsoft`, `alphabet`, `IBM` (llc,
`created_via = 'picker_stub'`, 2026-07-02 — months before Muse, not Muse's) came from the employer box; each opened
a "Complete profile: …" to-do. A fourth stub `__verify17_stub__` (2026-07-02) is a leaked Sprint-17 verify fixture.

## 1f. AGG / VTI

The stored rows are exact: live AGG 6 positions = **19,500,250** (= the file), live VTI 5 positions = **15,988,700**
(the file's 15,989,700 differs by 1,000 — on the file side). The approved integrated import's preview carries the same
lines. The X-ray's own holdings query over the Olivo household returns AGG 19,500,250 / VTI 15,988,700 (6 / 5 rows, no
duplicates); no registry fund held has AGG or VTI as a constituent (no look-through contribution); the two stored
X-ray runs contain neither line. No position of either security was ever superseded.
The only aggregation in the code that inflates a holding is the member-sum double count of 1b (each member's
look-through share added on top of the owner's own). Applied to AGG it gives 31,235,855, not Muse's 24,831,150, so
**Muse's exact figures are not reproducible from today's data** (most likely read mid-entry, before later lines
replaced earlier ones). Fix: ONE household aggregate — each position counted ONCE at the household's share of its
owner (`services/household_total`), used by the household total and its top holdings.

## 1g. Identity and the approval check

* `jlarizza@culmina.io` (06fe93ed…) has `auth0_sub = 'auth0|06fe93ed-…'` — its OWN uuid (the pre-AUTHZ1 uuid5
  phantom pattern), never a real Auth0 subject, last login NULL: a PRE-SEEDED row no sign-in can ever match.
* The Hollisworks-tenant sign-in (`auth0|6a7c8b47…`) arrived with no verified `/userinfo` email, so first-login
  provisioning (`services/users.resolve_identity`) created a placeholder row `<sub>@placeholder.local` (be8165db…,
  org bb347258…, 2026-10-01, super_admin from the pre-AUTHZ1 issuer rule) — the "stub super admin".
* The three 2026-11-03 placeholder logins (9460cacb super_admin, 4e9477e1, c550ee48) all carry
  `contact_email = jpl99172@gmail.com`; their `user_identities` count is 0.
* **Role sources.** `authz.principal_from_row` decides Super Admin from `users.role` (`rbac.is_super_admin`), but
  People & Access writes `user_roles` (`PUT /admin/users/{id}/role` syncs `users.role` for org_admin only and never
  touches a super_admin string). Real active users whose two sources differ today: **one** — 4e9477e1 (`users.role
  member`, RBAC `support_staff`); no Super Admin disagrees (after Claude's hand-alignment of 9460cacb).
  Today ANY holder of `manage_members` can assign the org's `super_admin` RBAC role (inert today; it would not be
  once user_roles decides) — the switch must gate it.
* **The approval check compares user ids**: `entered_by` / the task's `maker_id` vs the acting user's `users.id`.
  Both of Joe's sign-ins resolve to ONE row (f46eb620, the gmail user — Auth0 identities linked), which entered the
  integrated import → the refusal is correct; the screen's "A different Super Admin must approve this import." said
  nothing about who entered it or that both logins are the same person.
