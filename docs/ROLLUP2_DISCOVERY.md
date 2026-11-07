# ROLLUP2 — discovery (Task 1) and the sprint's dry-run records

Live database read 2026-11-06 (project `mmgwmcinimzuhargsazs`, org `00000000-0000-0000-0000-000000000001`), read-only.
Every figure below was produced by the code named next to it, not by hand arithmetic.

## 1a. Every net-worth reader — and the one that dropped valued holdings

| Surface | Code path (before ROLLUP2) | Reads | Valued position with no effective key |
|---|---|---|---|
| Entity **Net worth card** (CRM → Liabilities tab; `GET /entities/{id}/net-worth`, `GET /entities/{id}/liabilities`) | `liabilities.entity_net_worth` → `households._sum_holdings([entity])` | **stored snapshot** `entity_holdings` (latest `as_of_date` ≤ today) | **DROPPED** — the snapshot never contained it |
| Entity **Recalculate** (`POST /portentry/entities/{id}/net-worth/recalculate`) | `portfolio_refresh.recalculate` → `portfolio_rollup.rollup_entity_holdings(scope=family)`, then the card above | writes the snapshot, then reads it | **DROPPED** — `rollup_entity_holdings` skipped it (`NO_TAXONOMY_REASON`) because `entity_holdings.taxonomy_key` is NOT NULL and it had no bucket |
| Household total + household Recalculate (`GET /households/{id}/summary`, `POST /admin/households/{id}/recalculate`, People & Access household card `household_admin.household_detail`) | bugfix4 `household_total.total` | **live positions** | counted ("not yet classified") |
| Consolidate tab "Owners" list | **no such list exists in the code.** `ConsolidatePanel` lists overlapping funds only; the household page showed the bugfix4 summary (gross / liabilities / net / top holdings). The per-owner figures Muse compared are the entity cards (above) | — | — |
| X-ray valued total (`xray.compute`) | Σ market value of the household's ACCOUNT lines | live account lines | counted (the X-ray never looks at keys) — but direct assets / private investments are out of its scope by design, so it is not a net-worth figure |
| Flexible / primary household rollups (`GET /households/{id}/rollup`, `/networth`) | `households._sum_holdings(members)` | stored snapshot, member-summed | dropped (and double-counts owned members — bugfix4) |

**Proof the card path is the culprit**, recomputed live per entity with the real service code
(`portfolio_rollup._current_positions` + `position_current_value` + the rollup's own weights): for EVERY one of the 40
Olivo household entities, *keyed-only* equals the stored 2026-11-07 snapshot to the cent and *all valued lines* equals
`household_total.total([entity])` to the cent. The only difference between the two formulas was the unkeyed lines:

| Entity | snapshot (card) | all valued lines | unclassified, now counted |
|---|---|---|---|
| Dominic J. Olivo Revocable Trust | 140,054,602.00 | 188,302,502.00 | 48,247,900.00 |
| Olivo Family Foundation | 4,500,000.00 | 44,950,000.00 | 40,450,000.00 |
| Olivo 2016 CRUT | — (no row) | 11,100,000.00 | 11,100,000.00 |
| Cipolla Olivo Holdings LLC | 35,789,160.00 | 40,568,160.00 | 4,779,000.00 |
| Theresa Olivo Family Trust | 39,420,000.00 | 44,009,000.00 | 4,589,000.00 |
| MTO Capital LLC | 45,783,860.00 | 48,775,860.00 | 2,992,000.00 |
| Marguerite Meloni Olivo | 2,570,000.00 | 5,391,000.00 | 2,821,000.00 |

MTO: 48,775,860 = Muse's expected 48,588,360 + the $187,500 written option that could not be entered — it ties.
Marguerite's $2,821,000 (SCHB 1,687,000 + Schwab Municipal Bond ETF 1,134,000) was not in Muse's table; her card was short
by exactly that.

**[FIND] snapshot date skew.** The rollup labels its snapshot `date.today()` on the server (Render = UTC): the live
snapshot is dated **2026-11-07** while it was still 2026-11-06 in New York. A reader whose "today" is 11-06 reads the
PREVIOUS day's snapshot (Dominic's trust: 101,891,602 ≤ 11-06 vs 140,054,602 on 11-07). Computing cards live removes
this for every card; the stored snapshot (sunburst) keeps it.

## 1b. The CRUT: "$0, Holdings not yet rolled up", missing from the per-owner figures

Its only two live positions (SCHD 5,880,000 + SCHR 5,220,000, held-away account `9ace622c`, as of 2026-11-04) are kind
`fund` with no taxonomy key on the position or the asset. The rollup skipped both, so it wrote **no `entity_holdings`
row at all** for the CRUT → `holdings_as_of` NULL → the card's "Holdings not yet rolled up" and $0, and nothing to list.
It has an account and is a household member; nothing else was missing.

## 1c. "Owners list" vs card (Dominic +1,981,100; Olivo Holdings LP −3,550,000)

**Muse's exact figures are not reproducible from today's data.** No reader today produces $129,387,702 / $127,406,602
for Dominic's trust or $157,741,600 / $161,291,600 for Olivo Holdings LP: the stored snapshots read 101,891,602 (≤ 11-06)
and 140,054,602 (11-07) for the trust and 169,091,600 for the LP; the live figure is 188,302,502 and 169,091,600.
**Neither side was right**: both were stored snapshots (each missing the unclassified lines) taken at different moments
— the household's held-away snapshots were being re-entered between Muse's reads (bugfix4 supersede), and the date skew
above lets two screens read two different days. The fix is structural: one live core, and the household page now shows
an **Owners** list computed per member by the SAME function as that member's card (`household_total.owners` →
`networth.figures(ctx, {member})`), so the two cannot disagree. Read-only proof: all 40 rows equal their cards.

## 1d. Duplicate ownership entries

**No gap found in the data: ZERO owner / owned pairs with more than one live percentage**, in either ownership table
(`entity_relationships` ownership + `entity_ownership`), and zero owner / asset percent-basis position duplicates.
`repair_ownership_duplicates.py --dry-run`: `0 duplicate pair(s)`, `CHANGED: 0`.

What Muse saw is the **look-through tree**: Olivo Art Holdings LLC is owned **60 % by Olivo Holdings LP, 15 % by
Dominic J. Olivo Revocable Trust, 25 % by Cipolla Olivo Holdings LLC**; Stag's Hollow Vineyard **80 % by Olivo Holdings
LP, 10 % by Cipolla Olivo Holdings LLC, 10 % by AOF Investments**. On Dominic's Ownership tab ("Show look-through"),
Art Holdings appears directly at 15.00 % AND nested under Olivo Holdings LP at 60.00 % — the LP's stake — with no "via".
Cipolla's card uses its own 10 %, which is correct. Every reader takes ALL live edges (summed, capped at 1).

The real way a duplicate CAN arise: `POST /entities/{id}/ownership` never checked for a live edge on the pair, so a new
percentage "added" instead of edited left both live. Fixed (Task 4): a new percentage is a new version unless
`separate_holding: true`; the tree labels nested entries "60.00% via Olivo Holdings LP".

## 1e. What the security master knows about the fund assets

`portfolio.security_reference_attributes` (`field = 'fund_category'`, provider `finance_database`, loaded 2026-11-06):
**24 of the 25 kind-`fund` assets carry a category**; SGOV has none. The 7 mutual funds have **no master link at all**
(manual assets, `global_security_id` NULL) — no category exists for them. The categories are coarse and sometimes
wrong: **Grayscale Bitcoin Mini Trust (BTC) is filed under "Corporate Bonds"**; VTI / SCHB / SCHD / VXUS / VT / QQQ are
all "Developed Markets" (only the super class is honest).

`backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --funds --dry-run` (2026-11-06, `CHANGED: 0`) —
**apply 23, review 2, unchanged 6, unclassified 1**:

| Fund | Status | Proposed key | Reason |
|---|---|---|---|
| State Street SPDR S&P 500 ETF Trust (SPY) | apply | taxonomy_mc_1_1 | category 'Large Cap' |
| Vanguard S&P 500 ETF (VOO) | apply | taxonomy_mc_1_1 | 'Large Cap' |
| Innovator U.S. Equity Power Buffer ETF - January | apply | taxonomy_mc_1_1 | 'Large Cap' |
| iShares Russell 2000 Index Fund (IWM) | apply | taxonomy_mc_1_2 | 'Small Cap' |
| Vanguard FTSE Emerging Markets ETF | apply | taxonomy_mc_1_4 | 'Emerging Markets' |
| VXUS, VT, VTI, SCHB, SCHD, SCHF, EFA, VEA, VEU, QQQ | apply | taxonomy_sc_1 | 'Developed Markets' (super class only) |
| Vanguard Total Bond Market ETF (BND) | apply | taxonomy_mc_2_1 | 'Investment Grade Bonds' |
| iShares Core U.S. Aggregate Bond ETF (AGG) | apply | taxonomy_mc_2_1 | 'Investment Grade Bonds' |
| Schwab Intermediate-Term U.S. Treasury ETF (SCHR) | apply | taxonomy_sub_2_3_1 | 'Treasury Bonds' |
| Schwab Municipal Bond ETF (SCMB) | apply | taxonomy_sub_2_3_4 | 'Municipal Bonds' |
| Vanguard Tax-Exempt Bond ETF (VTEB) | apply | taxonomy_sub_2_3_4 | 'Municipal Bonds' |
| Schwab U.S. TIPS ETF (SCHP), Vanguard Short-Term TIPS (VTIP) | apply | taxonomy_sub_2_3_2 | 'Inflation-Protected Securities' |
| Grayscale Ethereum Staking Mini ETF (ETH) | apply | taxonomy_mc_7_3 | 'Alternative' (super class 7) refined by the name ("ethereum") |
| **Grayscale Bitcoin Mini Trust (BTC)** | **review** | taxonomy_mc_7_3 | category 'Corporate Bonds' → mc_2_1 but the name says digital asset — the master's category looks wrong |
| **Fidelity U.S. Bond Index Fund** (mutual fund) | **review** | taxonomy_sc_2 | keyed taxonomy_sc_1 by the old mutual-fund default; the name says bond; no category |
| iShares 0-3 Month Treasury Bond ETF (SGOV) | unclassified | — | no category in the master (name says treasury; operator may `--reclassify`) |
| Fidelity 500 Index, Fidelity Freedom 2045, Fidelity Total Market Index, Schwab S&P 500 Index, Schwab Total Stock Market Index, Vanguard Intermediate-Term Tax-Exempt | unchanged | (their current key) | already keyed; no category |

**[FIND]** Fidelity Freedom 2045 is a target-date (multi-asset) fund keyed Public Equity by the old default; its name
gives no evidence either way, so it is left unchanged and named here for the operator.

The mutual-fund default (`portfolio.kind_taxonomy` `mutual_fund → taxonomy_sc_1`) is REMOVED: a fund kind is never keyed
by its kind (`portfolio_kind_taxonomy.key_for_kind` routes fund kinds to `fund_classification.key_for_fund`).

## 1f. How an entity is marked charitable

`entity_type = 'foundation'` (Olivo Family Foundation — sub_type "Private foundation"; Olivo Giving Fund — "Donor-advised
fund"), or `entity_type = 'trust'` whose trust type (stored as its LABEL in `entities.sub_type`, crm_vocab `trust_type`
`sets_sub_type`) is a charitable-REMAINDER trust: **CRUT** or **CRAT** (Olivo 2016 CRUT — sub_type "CRUT"). A CLAT's
remainder returns to the family, so it is not charitable. No tax-status flag exists on entities.

Live split (read-only, `household_total.split`): **Charitable $62,150,000** (CRUT, Giving Fund, Foundation) and **Family
net worth $601,975,590** — exactly Muse's charitable expected and the option-adjusted family expected; household total
$664,125,590 = both.

## Snapshot refresh dry run (Task 7)

`recalculate_rollups.py --org 00000000-0000-0000-0000-000000000001 --household f1aa5e56-3e76-427a-ab07-e93b4bbedd4f
--dry-run` (as-of 2026-11-06): 33 entities, **the rollup's new snapshot equals the live card for every entity** ("card
agrees: yes" ×33); 171 buckets (32 would change); 16 unclassified lines now counted; 0 valueless lines. Before → after:
Dominic J. Olivo Revocable Trust 101,891,602 → 188,302,502; Olivo Family Foundation 4,500,000 → 44,950,000; Olivo 2016
CRUT — → 11,100,000; MTO Capital LLC 34,959,160 → 48,775,860 ("before" = the ≤ 2026-11-06 snapshot, see the date skew).

## Superseded assertions (recorded, not hidden)

**[FIND]** `verify_bugfix3.py` FIX 1 asserts the household gross EXCLUDES unkeyed positions and that Recalculate reports
them as `skipped.count = 2 / unclassified = 2`; and that a `mutual_fund` manual asset gets `kmap['mutual_fund']`. ROLLUP2
reverses the first two by design (Joe: net worth never silently omits a valued holding — unkeyed lines are counted and
reported under `counted_unclassified`), and `kmap` no longer has `mutual_fund` (its asset stays unkeyed → Unclassified).
Those bugfix3 checks are expected to fail on a re-run; the rule they encoded is the bug this sprint fixes.
