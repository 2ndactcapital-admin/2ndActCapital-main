# REVIEWQ1 — Security master Review queue: choices, Run, bulk, Known discrepancies

Branch `reviewq1-work` (base: main @ a10c26a "chore: refresh schema snapshot", track1 + prices2 + prices3 merged —
nothing to rebase). All evidence below was read from the code and the live database (read-only) on 2026-11-12.

## Evidence

### The review table — `portfolio.security_master_review`
- Columns as the prompt states. Catalog (read live, the snapshot records no CHECKs / indexes):
  - `security_master_review_status_chk`: status IN ('open', 'resolved', 'dismissed').
  - `security_master_review_kind_chk`: the 9 kinds in `security_master_load.REVIEW_KINDS`.
  - `security_master_review_decision_chk`: (status = 'open') = (decided_at IS NULL); `…_decider_chk`: open OR decided_by.
  - `security_master_review_platform_chk`: owner_scope 'platform' AND org_id NULL.
  - `security_master_review_open_uq`: UNIQUE (kind, subject_key, field, incoming_value) WHERE status = 'open' AND live.
  - RLS: four policies (SELECT / INSERT / UPDATE / DELETE), all `NULLIF(app.is_super_admin,'') = 'true'`.
  - FKs: global_security_id → securities_global(id); decided_by → users(id). Nothing references the review row's id.
- **FIND — the live open count is 726, not ≈716.** By kind / field / provider: identifier_conflict isin finance_database
  382 · type_mismatch security_type openfigi 167 · identifier_conflict cusip finance_database 72 · identifier_conflict
  figi finance_database 68 · ticker_conflict sec_13f_list 13 · ticker_cusip_conflict finance_database 10 ·
  **ticker_cusip_conflict openfigi 8 (all "held by …")** · **ticker_conflict sec_tickers 3** · **ticker_conflict
  nasdaq_trader 1** · identifier_conflict figi sec_13f_list 1 · type_mismatch finance_database 1. No live row has
  status resolved / dismissed. The verify's read-only check compares against these counts, read before seeding.

### Its writers
ONE writer: `services/security_master_load.insert_review` (called by `Matcher._write` for every provider —
finance_database, openfigi, sec_13f_list, nasdaq_trader, sec_tickers, sec_mutual_funds, treasury_fiscal, sec_nmfp — and
directly by `security_master_sync` for the 13(f) kinds). Its de-duplication is `ON CONFLICT … WHERE status = 'open' DO
NOTHING`: an identical OPEN item writes nothing, but a DECIDED item is re-opened as a brand-new open row on the very
next run — the "repeats" Joe sees. Changing this one function changes every provider run.

`subject_key` is NOT the security: an identifier conflict's subject is the INCOMING value (`isin:CA…`), the security is
`global_security_id`. So "the same subject + field + provider" is matched on the natural key
(kind, subject_key, field, incoming_value, provider_key, global_security_id): the same incoming value against the same
security from the same provider. A different incoming value always has a different key → a new open item.

### The decide route + screen
`POST /admin/security-data/master/review/{id}/decision` (outcome resolved / dismissed, note required; valid-axis
restatement; changes no security). `ReviewQueueView` in `components/securityData/SecurityMaster.jsx` renders one button
per outcome. `apps/web/tests/secmaster1/secmaster1.test.mjs` and `verify_secmaster1 y_review` assert that route and those
buttons — **kept unchanged** (never weaken an assertion): the legacy route still works and `ReviewQueueView` is still
exported; the screen now renders the new `ReviewChoicesView` instead.

### Identifiers — `portfolio.securities_global_identifiers`
- `uq_sec_global_ident_issuer_assigned`: UNIQUE (id_type, id_value) live for cusip / isin / figi / lei — per VALUE, not
  per (security, kind). **A second live ISIN / CUSIP / FIGI on one security is already allowed by the schema**; one
  value can never be live on two securities. No security has two of one kind today (read live).
- `is_primary` exists but is not a usable "primary vs alternate" marker: every live ISIN (10,673) and FIGI (12,539) is
  is_primary = false; 97 CUSIPs are primary, 10,627 are not.

### Every resolver that matches on isin / cusip / figi / ticker
All of them look up `(id_type, id_value)` among LIVE rows with no `is_primary` filter, so an alternate resolves to its
security with no change:
- `securities_global.get_by_identifier` / `get_by_identifiers_bulk` (merge-following; used by track1's lists,
  Replication Studio, held-away entry …).
- `replica_holdings.resolve_rows` (CUSIP → ISIN → SEDOL, ticker only without a strong id) — the list loader's index
  path; a list member identified by a foreign ISIN resolves once that ISIN is an alternate.
- `custodian_lots` statement matching (`SELECT DISTINCT s.id … WHERE id_type = $1 AND upper(id_value) = upper($2)`).
- `security_search` (rank 0 = an identifier equal to the query).
- The matcher itself (`_lookup`: CUSIP → FIGI → ISIN → class id → ticker+exchange).
Pickers that show ONE value per kind (`security_master_summary.securities`: `DISTINCT ON (id_type) … ORDER BY
is_primary DESC, id_value`; `security_search`'s list) would pick between two values arbitrarily.

## Decisions

1. **Statuses — the existing table, an extended vocabulary (no new review table).** New statuses `used_incoming`,
   `kept_existing`, `kept_both` (old `resolved` / `dismissed` stay for the legacy route and history). Known discrepancies
   = live rows with status `kept_existing` or `kept_both`. A decision is the same valid-axis restatement the legacy
   route already does (close + insert, who / when / note).
2. **"Seen again" — a small append-only table `portfolio.security_master_review_sightings`** (natural key + review_id +
   run_id + seen_at; RLS for all four operations, Super Admin). Why not a counter on the review row: Rule 3 forbids an
   in-place update, and restating the decided row on every nightly run would mint ~700 rows a night and change the
   item's id under the screen. Seen-again = sightings of the natural key after the item's decided_at; last seen = their
   max. Moving an item back and deciding it again starts the count over by construction.
3. **Keep both — the smallest support: one column `securities_global_identifiers.is_alternate boolean NOT NULL DEFAULT
   false`.** The value uniqueness rule is already right (one value, one security). The alternate is a non-primary,
   `is_alternate = true` row beside the existing value, which is untouched. Resolvers need no change (above); the two
   one-value pickers order `is_alternate` after the primary so the existing value stays the one shown. Offered only for
   `identifier_conflict` on isin / cusip / figi where the existing value is the security's own (not "held by …"), and
   for `list_match`.
   **Not offered**: `security_type` (a security has one type); a ticker conflict (`identifier_conflict` on ticker,
   `ticker_cusip_conflict`, `ticker_conflict`) — one listing has one ticker, and one ticker on one exchange names one
   security; class / series ids; an identifier "held by" another security (one value, one security — that is a merge).
4. **Use incoming** — per kind:
   - identifier_conflict isin / cusip / figi / ticker / class id: the security's live value(s) of that kind are retired
     (valid_to = now(), kept as history) and the incoming value inserted with the retired row's is_primary — a new
     bitemporal version, reversible. Refused when the incoming value is live on ANOTHER security (identifier: one
     value, one security; ticker: another live security on a compatible exchange family).
   - ticker_cusip_conflict (incoming `T@CUSIP`): the security's CUSIP becomes the incoming CUSIP and the security holds
     ticker T. Refused when T is live on another security on a compatible exchange (all 8 openfigi "held by" rows
     today) or the CUSIP is live on another security.
   - type_mismatch: `securities_global.update_security` (system-axis archive, id kept — the existing mechanism).
   - add_to_master: creates the security from OpenFIGI's ticker / name / type with its identifiers; refused on any
     ticker / identifier collision.
   - Not offered (only Use existing): ticker_conflict (the record matched no security — nothing to apply it to),
     ambiguous_match, exchange_mismatch (exchange is a per-provider attribute ranked at read time — a decision cannot
     outrank a provider), ticker_moved (informational; the move already happened), 13f_deleted, 13f_conflict.
   The choices a row offers come from the server (`choices` per row + `choice_notes` naming why one is not offered).
5. **Use existing** — nothing on the security changes; the item becomes Known (`kept_existing`).
6. **Repeat suppression in `insert_review`** (the one writer): before inserting, a live Known row with the same natural
   key → a sighting, no new item. Same value against an OPEN item → nothing (as before). A different value → a new
   open item (its key differs). A moved-back item is open → the open unique index keeps the next run from duplicating
   it and nothing closes it. Keep both items are seen again too: the matcher, matching a record whose identifier is
   an alternate of the security (`is_alternate`), records a sighting for the Known item of that key.
7. **Move back** — Super Admin, note required, only a Known item; restated to `open` (decided_at cleared, the move in
   `detail.moved_back` and audit_log). A Keep both item's alternate identifier is retired on the way back (so the next
   run raises the disagreement again and it stays one open item). Use incoming items are not Known and are not moved
   back from this list; they appear in the security's history (`GET …/review?status=used_incoming&global_security_id=`).
8. **Run** — `POST …/review/run {choices: [{id, choice}], note?}`: every item in its own transaction, a refusal names
   its reason and leaves that item open; counts per choice returned. **Bulk** — `POST …/review/bulk {filters, choice,
   expected_total, note?}`: the filter re-evaluated on the server, must equal the confirmed count (else nothing is
   written), cap 2,000; rows a choice does not apply to are reported, not forced. Filters: kind, field, provider, q
   (subject / incoming / existing / security name), `home_vs_us` (field isin AND incoming country ≠ US AND every
   existing ISIN US — the same matched security, i.e. the same issuer).
9. **Gates / audit** — every write `require_super_admin` first; one `audit_log` row per item on the platform org
   (`security_master.review_decided` / `security_master.review_moved_back`, payload: review ids, kind, choice, before →
   after of the item and of the security's values).
10. **List misses** — `security_lists_load.py --resolve-misses [--dry-run]`: each stored miss of a list with an ISIN /
    CUSIP is mapped through the existing OpenFIGI provider (`openfigi.map_jobs`; new job helper `isin_job` —
    `ID_ISIN` with exchCode US, the US composite). FIGI (else ticker) found in the master → a `list_match` review item
    (Keep both = add the miss's identifier as an alternate; Use existing = reject); nothing found → `add_to_master`
    (Use incoming = create from OpenFIGI's facts). Never auto-merged; review items only. The sprint runs it on
    fixtures with the transport stubbed.

## Built

- `services/security_master_review.py` — choices per row, the Run, bulk (estimate + apply), move back, collision
  refusal, audit, repeat-suppression helpers. Routes in `routers/security_master.py` (all `require_super_admin`):
  `POST …/review/run`, `POST …/review/bulk/estimate`, `POST …/review/bulk`, `POST …/review/{id}/move-back`;
  `GET …/review` gains status `known` / the decided statuses and the filters, `total`, per-row `choices` +
  `choice_notes`, facets. Legacy decision route unchanged.
- `security_master_load.insert_review` (the one writer) + the matcher's alternate sightings.
- `security_lists.resolve_misses` + `openfigi.isin_job`; `security_lists_load.py --resolve-misses [--dry-run]`.
  A miss the master now resolves (a confirmed alternate, an added security) is skipped until the next load.
- Screen: `ReviewPanel` → `ReviewChoicesView` + `KnownDiscrepanciesView` (`lib/reviewq1.mjs` helpers). Light theme;
  the master's table / filter form / `BusyButton`.
- Schema APPLIED (`_apply_reviewq1.py --all`: SCHEMA OK; open review items 726 → 726).

## Proof (`apps/api/scripts/verify_reviewq1.py`, in scripts/regression_set.txt)

One-offs, 2026-11-13: `--self-checks-only` 52/0 · `--cleanup-only` 1/0 · `--section l,g` 37/0 (after the live-check
fix) · `l,a` 52/0 · `b,s` 46/0 · `m,r` (+s) 52/0 · **`l,g,a,b,s,m,r` in one seed 110/0** · `w,j` 5/0 (render tests 23+
/ 0 incl. secmaster1 / secmaster2 / track1 suites; fast regressions verify_secmaster1, verify_secmaster2,
verify_track1, verify_prices1 each 0 FAIL). Full single-command run left to the runner.
Every run ends with the REAL queue's fingerprint (id, status, valid_to, system_to) and open counts by kind / field /
provider unchanged, and the real isin / cusip / figi identifiers unchanged. Nothing real was decided, changed or
re-opened.

## FINDs
- The live open count is **726**, not ≈716 (sec_tickers 3 + nasdaq_trader 1 ticker_conflicts; ticker_cusip_conflict
  openfigi is 8, not 2).
- All 8 openfigi ticker_cusip_conflict items are "held by" another security → Use incoming will be REFUSED for them
  by the collision rule (correct); Use existing is the choice unless a merge is made on the security.
- The "home-market vs US ISIN" filter selects **305 of the 382** ISIN items today.
- `is_primary` is not a usable primary marker (every live ISIN / FIGI is false) → the new `is_alternate` column.
- verify_secmaster1 asserts the legacy route and `actions == ["resolved", "dismissed"]` on an open row: kept, so
  `GET /review` still carries `actions` (legacy) beside `choices` (new).
- `--resolve-misses --dry-run` DOES call OpenFIGI (read-only) — unlike the loader's own `--dry-run`, which is
  network-free; it writes nothing.

## OPERATOR
1. Deploy the API + web (schema already applied).
2. Security master → Review queue: **start with the filter "Home-market vs US ISIN" → Keep both** (bulk: the server
   shows the count, confirm); **then CUSIP / ISIN rows where the existing value is the post-split / post-corporate-action
   identifier → Use existing** (filter field = cusip or isin, text = the issuer — ProShares, Direxion, Inuvo, Ocean
   Power, GrafTech, Virgin Galactic, DuPont …); **review the rest one by one** (choose per row, Run). Decided Use
   existing / Keep both items move to Known discrepancies; the next source runs count them as seen again instead of
   re-opening them; Move back re-opens one.
3. List misses (after a real `security_lists_load.py --all` stored them):
   `doppler run -- apps/api/venv/bin/python apps/api/scripts/security_lists_load.py --all --resolve-misses --dry-run`,
   then without `--dry-run`; confirm the `list_match` items (Keep both, bulk by kind) and decide `add_to_master` items
   (Use incoming creates the security) in the queue; the next load resolves them.
