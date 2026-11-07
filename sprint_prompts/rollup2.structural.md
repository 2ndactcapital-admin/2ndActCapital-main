ROLLUP2 — one net-worth roll-up everywhere: entity cards, the Consolidate
Owners list and the household total must count the SAME holdings, a valued
holding with no allocation bucket is counted under "Unclassified" (never
dropped), listed funds get a real allocation bucket, duplicate ownership
entries are found and repairable, and the household total is shown split into
family and charitable. 9 tasks + verification. Branch rollup2-work.

Context: HollisMuse fix-up v5 (2026-11-06, report
olivo-fixup-v5-report-2026-11-06.md). The household total now ties exactly
($664,125,590 = family $601,975,590 + charitable $62,150,000), but 6 entity
cards are short by $111,970,400 in total. Joe's rule: net worth must never
silently omit a valued holding.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (live DB 2026-11-06 23:35 ET; Task 1
confirms the code paths):
- EVERY entity-card gap equals EXACTLY that owner's live positions whose asset
  is kind 'fund' with NO taxonomy key (positions.taxonomy_key NULL AND
  assets.default_taxonomy_key NULL):
  | Owner (entity id prefix) | Unkeyed fund lines | Sum = card gap |
  |---|---|---|
  | Dominic J. Olivo Revocable Trust (18400d6a) | SPY 24,399,800; VXUS 11,424,000; BND 7,115,500; IWM 5,308,600 | 48,247,900 |
  | Olivo Family Foundation (b06b7417) | VT 29,964,000; BND 10,486,000 | 40,450,000 |
  | Olivo 2016 CRUT (60c20f3b) | SCHD 5,880,000; SCHR 5,220,000 | 11,100,000 (card shows $0, "Holdings not yet rolled up") |
  | Cipolla Olivo Holdings LLC (27783cda) | SCHB 2,169,000; SCHF 1,728,000; SCHM-muni 882,000 | 4,779,000 |
  | Theresa Olivo Family Trust (3f098811) | SCHD 3,234,000; SCHP 1,355,000 | 4,589,000 |
  | MTO Capital LLC (4ea9aa44) | QQQ 2,992,000 | 2,804,500 (= QQQ − the $187,500 written option that is in the expected figure but could not be entered) |
  | Marguerite Meloni Olivo (801422c7, person) | SCHB 1,687,000; Schwab Municipal Bond ETF 1,134,000 | not in Muse's table — check her card |
- Proof it is the missing key: operator ran backfill_taxonomy_keys.py
  --reclassify (K.19 → cash_holding, and 5 master equities keyed
  taxonomy_sc_1). Dominic's card rose by exactly Apple 4,723,200 + Berkshire
  4,483,800 = 9,207,000; MTO's by exactly MSFT + NVDA + GOOGL = 7,579,200;
  Family Office by K.19's 1,400,000; the household's "not yet classified"
  figure fell by exactly the same 18,186,200. So the entity card / Owners
  list roll-up DROPS positions with no effective key, while
  services/household_total (bugfix4) counts them. bugfix4 fix 1 proved the
  roll-up's gross on a FIXTURE; the card path is a different reader or an
  older stored snapshot — find it.
- backfill_taxonomy_keys.py dry run: "KIND fund: assets 25, live unkeyed
  positions 16 SKIP (no mapping for this kind (portfolio.kind_taxonomy))".
  A fund's bucket depends on WHAT the fund holds, not its kind — a kind-level
  mapping is wrong for funds.
- Mis-classification found: kind 'mutual_fund' defaults to taxonomy_sc_1
  (equity) for every mutual fund — "Fidelity U.S. Bond Index Fund" (2,630,000)
  is keyed as equity. Same root cause: classify funds by their holdings /
  category.
- Many live positions have positions.taxonomy_key NULL but an asset
  default_taxonomy_key (e.g. municipal bonds taxonomy_sub_2_3_4, MMFs
  taxonomy_sub_8_1_1); those already count, so the effective key is
  COALESCE(position key, asset default). Keep that rule.
- Muse also saw: the Consolidate "Owners" list differs from the entity's own
  card for Dominic Revocable Trust ($129,387,702 listed vs $127,406,602 card)
  and Olivo Holdings LP ($157,741,600 listed vs $161,291,600 card), and the
  CRUT is missing from the Owners list; ownership panels show two entries for
  the same owner / asset (Art Holdings 60.00% AND 15.00% for Dominic
  Revocable Trust; Stag's Hollow 80.00% AND 10.00% for Cipolla Holdings), and
  Cipolla's card uses the lower one.
- The held-away Cash line has no Name field, so "Coastal Bank checking" and
  "Cash / sweep" could not be recorded (MUSE F1-01 / F2-01).
- The household total includes the charitable entities (Foundation, Giving
  Fund, CRUT); the family net-worth figure in the demo plan excludes them.
- Demo rows (demo-olivo) are permanent demo data. The sprint writes NO real
  or demo row; every data change is an operator script with --dry-run.
- Regression: this changes the roll-up every net-worth surface uses → re-run
  verify_bugfix4.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build, any full
  verify script, or an operator script without --dry-run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret. Decimal everywhere. Bi-temporal
rule 3; RLS for every operation. Light / cream UI. Commit ALL sprint files
(excluding sprint_prompts/logs) on the current branch at the end with message
"rollup2: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Every net-worth reader: the entity Net worth card, its Recalculate, the
      Consolidate Owners list, the household total (services/household_total),
      the X-ray valued total, People & Access cards. For each: the code path,
      whether it reads live positions or a stored snapshot, and how it treats a
      position with no effective key. Name the one(s) that drop it.
  1b. Why the CRUT card says "Holdings not yet rolled up" and is missing from
      the Owners list (no snapshot row? all its holdings unkeyed?).
  1c. The Owners-list vs card differences (Dominic +1,981,100; Olivo Holdings
      LP −3,550,000): which side is right and why they differ.
  1d. The duplicate ownership entries: are they two live rows, two valid-time
      periods, or one archived version shown as live? Which one each reader
      uses. Count every owner / asset pair in the org with more than one live
      percentage (read-only; list them).
  1e. What the security master knows about each of the 25 kind-'fund' assets
      and the mutual funds (asset class / category / fund type from the SEC,
      FIGI or FinanceDatabase loads) — can a bucket be derived for each?
  1f. How entities are marked charitable (entity type / tax status) so the
      household total can be split.

=== TASK 2: ONE ROLL-UP ===
Every reader in 1a computes from ONE service (household_total's per-position
logic, or a shared core it and the card both call). A valued position with no
effective key is counted in gross under "Unclassified" and listed; only a
position with NO value is skipped (and listed as skipped). An entity card,
its Recalculate, the Owners list and the household total agree to the cent
for the same as-of. A stored snapshot, if one exists, is recomputed by
Recalculate from the same service — never a separate formula.

=== TASK 3: FUND CLASSIFICATION ===
A listed fund (ETF, mutual fund, closed-end) gets its bucket from what it
holds: derived from the security master's category data (1e) through one
mapping table you add (fund category → taxonomy key, readable and editable by
Super Admin), never from the kind alone. Fix the mutual-fund default
(bond / municipal / TIPS / treasury / international / money-market funds are
not "equity"). Where no category exists, the fund stays "Unclassified" and is
listed for the operator. Extend scripts/backfill_taxonomy_keys.py: --funds
proposes a key per fund asset with the reason (category → key), --dry-run
lists every proposed change, and --reclassify accepts several ASSET_ID:KEY
pairs. The sprint runs only --dry-run (read-only) and records the proposals
in the discovery doc.

=== TASK 4: DUPLICATE OWNERSHIP ===
A read-only report (Super Admin) of owner / asset pairs with more than one
live percentage, and a warning on the ownership panel where it happens. Rule
going forward: a new percentage for the same owner / asset is a new version
(the prior one archived) unless the user explicitly records a second,
separate holding. scripts/repair_ownership_duplicates.py (--dry-run lists
each pair, both entries, which one is newer, the proposed survivor; the
operator runs the real pass; audited; idempotent). The sprint runs only
--dry-run.

=== TASK 5: FAMILY / CHARITABLE SPLIT ===
The household page and its summary API show Family net worth, Charitable
(entities from 1f) and Household total = both, each with gross / liabilities
/ net, plus the Unclassified amount.

=== TASK 6: CASH LINE NAME ===
The held-away entry form's cash (and money-market) line takes an optional
Name, stored on the line and shown in the account's holdings. Two cash lines
in one account with different names stay two holdings.

=== TASK 7: SNAPSHOT REFRESH (OPERATOR) ===
If 1a finds stored snapshots, scripts/recalculate_rollups.py --org X
[--household Y] --dry-run shows before / after per entity; the real run
writes new versions. The sprint runs only --dry-run.

=== TASK 8: REAL PROOF ===
  - Fixture org with an unkeyed fund line, a keyed equity, a cash line and a
    valueless line: entity card == its Recalculate == Owners list ==
    household total == independent SQL; the fund is under "Unclassified";
    the valueless line is listed as skipped.
  - A fixture CRUT whose only holdings are unkeyed funds shows its real total,
    never $0 / "not rolled up", and appears in the Owners list.
  - Fund mapping: a fixture bond fund, muni fund and equity fund each get the
    right key from their category; a fund with no category stays
    Unclassified; the mutual-fund default no longer keys a bond fund as
    equity. --funds --dry-run on the fixture lists exactly those proposals.
  - Duplicate ownership: a fixture pair with two live percentages is
    reported, warned, repaired by the script (dry run first, then real on the
    fixture, then a second run CHANGED: 0); a new percentage archives the
    prior version.
  - Family / charitable split sums exactly to the household total.
  - Cash line names kept; two named cash lines stay two holdings.
  - Read-only proof on the LIVE org (no writes): the readers agree with each
    other for every Olivo entity, and the rows in the table above now appear
    under "Unclassified" on their cards; print each entity's card total.
  - Org isolation both directions (rolbypassrls = false); real and demo rows
    unchanged (count + checksum).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_bugfix4.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md entry with the findings table, the operator steps in
order (1. backfill_taxonomy_keys.py --funds --dry-run, then the real run;
2. repair_ownership_duplicates.py --dry-run, then real; 3. recalculate if
snapshots exist; 4. household Recalculate), and verify_rollup2.py added to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rollup2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id; re-read every
touched table for its exact pre-test count. Fixture orgs and users is_test =
true (assert it). Read UNIQUE / CHECK / FK constraints from the catalog
before seeding (and FAIL if the catalog read returns none). Reserved fixture
prefixes that match no real row (assert it). Support --self-checks-only and
--cleanup-only. The regression subprocess gets VERIFY_NESTED=1, after
teardown, 2700 s; with VERIFY_NESTED=1 skip it with one '[SKIP] nested run'
line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read (the same org context the route sets);
    rolbypassrls = false asserted; an empty list never passes a check.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - A recursive CTE has ONE anchor and ONE recursive term.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates, and name them.
  - No check frozen against live state: live-org checks assert that the
    readers AGREE, never a fixed total.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1f
  [Y] One roll-up: card == Recalculate == Owners list == household == SQL
  [Y] Unclassified counted and listed; valueless skipped and listed
  [Y] Fund classification by category; mutual-fund default fixed; --funds dry run
  [Y] Duplicate ownership report, warning, repair (dry run / real / CHANGED: 0)
  [Y] Family / charitable split; cash line names
  [Y] Live org read-only: readers agree for every Olivo entity
  [Y] Isolation; own routes; --check CURRENT; real + demo rows unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_bugfix4.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
