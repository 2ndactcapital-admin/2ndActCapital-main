# VHK1 — verify self-checks pinned to each sprint's own merge (2026-11-02)

Rules: `docs/VERIFY_RULES.md`. Helper: `apps/api/scripts/_verify_self.py`. Verify: `apps/api/scripts/verify_vhk1.py` (written, not run).

## Task 1 findings that differ from the prompt

- `scripts/regression_set.txt` listed **24** scripts, not 27 (now 25 with verify_vhk1.py).
- `verify_mkt04a` has a new-routes count but is **not** in the regression set, so it was not changed.
- **verify_rep4** also had a module-level `TODAY = datetime.now(timezone.utc).date()`. The prompt's list missed it.
- **edgarpipelinea has no merge commit.** It landed on main directly as 4080f41, so `VS.SPRINT_LANDED` records it.
- **usermgmt1 has two merges** naming it (62364b4, then b07c839 "bugfix1 + usermgmt1 implementation"). They are listed explicitly in `VS.SPRINT_MERGES`, never guessed by subject.
- **Every merge's first parent is that sprint's own base**: `base..merge^1` is 0 commits for all 18 sprints with a `BEFORE_REF`. So base → merge is exactly the sprint's own work.
- **Two checks were already failing against HEAD** before VHK1, both for this reason:
  - bulkfetchui3 compared `main:docs/SPRINT_WORKFLOW_STANDARD.md`, and main has moved since.
  - rep7b's lotmethod-B1 guard on verify_rep7.py never allowed lotmethod's own `/lot-method/` route exclusion. It is now pinned to lotmethod's merge, with those four lines named in `LOTMETHOD_REP7_ROUTE_SCOPE`.
- `authz1_routes.py --check` compares the routes with the docs, both from the working tree. It is not a since-base check and takes about 60 s, so it stays in the full run only and is left out of `--self-checks-only`.
- **[FIND] Two exact real-data checks are drift, not empty data, so they were left unchanged:**
  - verify_edgarpipelinea `existing_documents == 200`.
  - verify_authz1 `y_real_users`, which compares live users with the frozen `AUTHZ1_REAL_USERS.json`.

## 1b. Each sprint's end point (`VS.sprint_end_ref`)

| sprint | BEFORE_REF (base) | end point |
|---|---|---|
| authz1 | a911ac7 | f115737 |
| usermgmt1 | f115737 | 62364b4 |
| navregroup | (4af0f9d) | a911ac7 |
| bugfix1 | e816797 | b07c839 |
| dealroom1 | b07c839 | 5760301 |
| dealroom2 | 0823d72 | 416a763 |
| dealroom3 | 376f8ef | 5681d30 |
| bulkfetch | (b6f60ea) | 4af0f9d |
| bulkfetchui | (4af0f9d) | a911ac7 |
| bulkfetchui2 | (4af0f9d) | a911ac7 |
| bulkfetchui3 | 416a763 | fb4205d |
| edgarpipelinea | — | 4080f41 (landed on main directly, no merge) |
| rep1 | fb4205d | 14200f8 |
| rep2 | ff61884 | b901a01 |
| rep3 | 93aaf23 | 34b3691 |
| rep4 | 3c09f92 | 0b2f7ff |
| rep5 | 1717482 | 2667e7e |
| rep6 | f1b0eb0 | ead5a7f |
| rep7 | 759b7e4 | 9ed75ae |
| rep7b | 9ed75ae | a5ffe9a |
| lotmethod | a5ffe9a | 376f8ef |
| rep8 | 5681d30 | 24962f1 |
| marketdata1 | afdbc6d | ab9d823 |
| rep9 | 87ad248 | afcae0b |
| vhk1 | afcae0b | HEAD (not merged) |

A parenthesised base is the merge's first parent; that script has no BEFORE_REF.

## 1a / 1c / 1d. Per-script table (from the five Task 1 sweeps)

verify_rep6 was done by the lead. It used to compare lotmethod's B1 base with the working tree's verify_rep5.py; that is now pinned to lotmethod's merge 376f8ef. Its own verify_rep5 guard used to end at main @ merge rep7b and now ends at its merge ead5a7f. That made the "rep7 superseded rep5" y_join / y_execution exclusion unnecessary (it no longer fires), so it was removed. Its 31-route check now goes through `VS.own_routes` + `VS.still_gated`.

### Group 1

### VHK1 group 1 — authz1, usermgmt1, navregroup, bugfix1, dealroom1

### Task 1 table (1a / 1c / 1d)
| script | line (before) | what it compares | broke / would break by | VHK1 action |
|---|---|---|---|---|
| verify_authz1 | 338-360 y_task1 | route doc vs today's code (`authz1_routes.py --check`, md/json row counts) | — (current vs current, not since-base) | left; full run only |
| verify_authz1 | 410-427 _load_old / y_reproduce | reads a911ac7 helpers only (old default-allow) | — | feature / before-only, left |
| verify_authz1 | 801-846 y_real_users | real users vs frozen docs/AUTHZ1_REAL_USERS.json + Joe's 3 ids | any later real user change (data drift, not empty data) | [FIND] for parent: not an empty-data check; left (would weaken the report assertion) |
| verify_usermgmt1 | 517 y_one_row | `git show f115737:routers/admin.py` only | — | feature / before-only, left |
| verify_usermgmt1 | 487 (1c) | `check(len(rows) > 0)` — every active REAL account's sign-in method derived | zero active real accounts → FAIL | VS.when_empty; derivation asserted per row |
| verify_navregroup | 593-617 y_urls_unchanged | page existence e581844 vs WORKING TREE; page diff e581844 vs working tree (later-sprint pages at e97718f) | usermgmt1 (/admin/users), bugfix1 (/spvs, /portfolio*, 3 comingSoon pages) — patched by 2 exclusion dicts | pinned: existence + `git diff` at navregroup's merge a911ac7 (content-identical to e97718f); sample from NAVREGROUP_BASELINE.json at the merge |
| verify_navregroup | 624-637 | router gate-token counts e581844 vs e97718f (fallback: working tree), router list from working tree | AUTHZ1 (nine routers) — already pinned by hand | pinned via VS: router list + text at the merge, no working-tree fallback |
| verify_navregroup | 628-632 /admin page | today's page keeps the session gate | — | feature, left (full run) |
| verify_navregroup | 670, 718 | old document_links.py / vdr.py at e581844 | — | feature / before-only, left |
| verify_bugfix1 | 1093-1110 y_gates | AUTHZ1_ROUTES.json e816797 vs WORKING TREE: no gate changed; exactly 4 new routes | dealroom1 (patched by /deal-room exclusion), then rep1–rep9, dealroom2/3, lotmethod, rep8… | pinned: own_routes(e816797 → b07c839); + still_gated(today) |
| verify_bugfix1 | 505, 516, 702, 1071 | before-only reads + assertions on today's code (firm-search page) | — | feature / before-only, left |
| verify_dealroom1 | 1017-1033 y_routes | e.g. "new routes are exactly the /deal-room surface", no gate changed, vs WORKING TREE | every later sprint adding routes (rep1 → rep9, dealroom2/3, lotmethod, marketdata1) | pinned: own_routes(b07c839 → 5760301); + still_gated |
| verify_dealroom1 | 493, 497 (1c) | `len(live) > 0` — every pre-sprint REAL live deal in the migration report / publisher + 9 folders + settings | real deals retired/cleaned → FAIL | VS.when_empty ×2; both rules per row |
| all five | 1d | module-level TODAY/NOW | — | none present (confirmed) |

### Exclusion clauses removed
- verify_navregroup: `LATER_SPRINT_PAGE_CHANGES` (usermgmt1 / bugfix1 page edits) and `LATER_SPRINT_NEW_PAGES` (bugfix1 comingSoon pages), plus the `NAVREGROUP_REF` working-tree fallback — unnecessary at navregroup's merge (checks pass without them).
- verify_bugfix1: the dealroom1 `/deal-room/*` exclusion — no dealroom1 route exists at b07c839.

### Task 5 — teardown moved to _verify_cleanup (all five)
Each: `teardown()` = VC.cleanup on the operator DSN (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP), wrapped in an outer transaction that sets the old `app.is_super_admin` GUC LOCAL (VC's own transaction becomes a savepoint); plan seeds = the old teardown's no-FK deletes (member_todos by related_id, audit_log by resource/user/org, user_identities, the old user predicates). Kept: authz1/usermgmt1 user-id materialisation; bugfix1 audit actor UPDATE + real-deal guard; dealroom1 privileged signature purge (unchanged, own GUC; VC is NOT given that GUC so a non-test signature still fails loudly) + real-deal guard. delete_tree / fk_order / delete_order removed (usermgmt1 keeps fk_children: used by its 1c reference listing, not the teardown). leftovers() += VC.leftover_count. `--cleanup-only` dispatched right after the connection opens.

### Proof (output in /tmp/vhk1/selfchecks/*.out, /tmp/vhk1/cleanup/*.out)
| script | --self-checks-only | --cleanup-only |
|---|---|---|
| verify_authz1 | TOTAL: 1 PASS, 0 FAIL | CLEANUP_SECONDS 19.1, LEFTOVERS 0, TOTAL 1 PASS 0 FAIL, rc 0 |
| verify_usermgmt1 | TOTAL: 1 PASS, 0 FAIL | 17.1, 0, 1/0, rc 0 |
| verify_navregroup | TOTAL: 4 PASS, 0 FAIL | 25.8, 0, 1/0, rc 0 |
| verify_bugfix1 | TOTAL: 4 PASS, 0 FAIL | 18.4, 0, 1/0, rc 0 |
| verify_dealroom1 | TOTAL: 5 PASS, 0 FAIL | 16.4, 0, 1/0, rc 0 |
No --cleanup-only removed any row (0 tables, 0 rows each).

### Not done / caveats
- The new teardowns were proven only on an empty fixture set (no full run allowed): VC's FK-closure safety refusal could fire in a real run where a fixture user owns a child row in a REAL org (bugfix1's U_RESTORER lives in the default org) — it would raise, roll back and fail loudly, never delete real rows.
- verify_authz1 y_real_users compares live users to a frozen report — a real-data drift risk outside VHK1's empty-data rule; left untouched.
- No helper changes needed; no commits.

### Group 2

### VHK1 group 2 — bulkfetch, bulkfetchui, bulkfetchui2, bulkfetchui3, edgarpipelinea

### Task 1 (1a / 1c / 1d)
| script | line (pre-edit) | what it compares | broke / would break | VHK1 action |
|---|---|---|---|---|
| verify_bulkfetchui3 | 1072 y_standard | `git show main:docs/SPRINT_WORKFLOW_STANDARD.md` vs WORKING TREE (old bullet → new bullet, rest byte-identical) | ALREADY BROKEN since its own merge fb4205d: today's main no longer has the old bullet ("main's copy still has the old bullet" FAILs); any later edit to the standard breaks it too | pinned: base = its merge's first parent (416a763 = main at the merge), end = its merge fb4205d (`self_standard`) |
| verify_bulkfetchui3 | 1099 y_routes | `main:docs/AUTHZ1_ROUTES.json` vs WORKING TREE: no gate changed, NO NEW ROUTE | every later sprint adding a route (rep1…rep9, dealroom*, lotmethod, marketdata1) | pinned 416a763 → fb4205d (`self_routes`); institutions route gated super_admin at the merge AND still today |
| verify_bulkfetchui3 | 1094 | `authz1_routes.py --check` | — (tree vs tree) | full run only |
| verify_bulkfetchui3 | 826 | `99dc9e1^:edgar_pipeline.py` (old classifier) vs today's classifier | before-only read | feature / before-only, left |
| verify_bulkfetchui3 | 1016–1045 | run_regressions.sh content / stub run | today's file (feature) | left, full run only |
| verify_bulkfetch, bulkfetchui, bulkfetchui2, edgarpipelinea | — | no since-base comparison found | — | VHK1 block with empty self_checks (end-ref check only) |
| verify_edgarpipelinea | MIGRATIONS / drift check | today's migration files vs today's schema | — | left (feature, not since-base) |
| 1c verify_bulkfetchui | 811 | live preview check `... and n_sql > 0` (real 'selected' JPMorgan 2025 filings) | fails once the real pipeline fetches them all | equality still asserted; emptiness → `VS.when_empty` FIND |
| 1c verify_bulkfetchui3 | 1211 | `real_before[0] > 0` (three real banks' filings present) | fails if none | `VS.when_empty` FIND; teardown checksum-unchanged check untouched |
| 1c verify_edgarpipelinea | ~1460 bucket probe | head_object on a stored real r2_key (None if no real object → FAIL) | empty corpus | `VS.when_empty` FIND; probe skipped only when no key |
| 1c verify_edgarpipelinea | 898 fetch_section | real never-fetched 'selected' sample (n_real >= 8, >= 5 groups) | depletes as the real pipeline fetches | empty sample → FIND + section returns (every fetch expectation derives from those filings); non-empty → original >=8 / >=5 check unchanged |
| [FIND] verify_edgarpipelinea | 1233 existing_documents | `identity_rows == 200 and linked == 200`, `st == 200` | frozen real-data SHAPE (fails if the real identity corpus ever grows/shrinks), not an empty-data check | NOT changed (rewriting it would change the assertion) — flagged |
| 1d (all five) | — | no module-level TODAY/NOW (date.today()/datetime.now inside functions only) | — | none |

Exclusion clauses removed: none (these scripts had none).

### Task 5 — moved to _verify_cleanup.py
All five: per-table teardown (`delete_order` + hand-ordered user/role/org deletes; bulkfetch's `teardown_gold`; edgarpipelinea's `teardown_fixtures`) → `VC.cleanup` on the operator DSN (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP via `_db_connect.resolve_dsn`), inside an outer transaction with `app.is_super_admin='true'` SET LOCAL (the GUC the old teardown used), `orgs=[ORG]`, `users=` fixture ids (+ sub / email matches), `plan=` the script's own old predicates verbatim (accession prefix, group prefix, CIKs, fixture runs/leases/workers; bulkfetch's gold/readings by fixture filing ids + fixture users; edgarpipelinea's STATE runs / workflow runs and its existing `trigger_source='verify'` sweep). Start-of-run and end-of-run both use it. Non-delete work kept exactly: edgarpipelinea's R2 prefix delete, sample manifest restore, fingerprints; bulkfetch* table counts + gold checksum; bulkfetchui* worker guards (full run). `--cleanup-only` dispatched right after the connection opens (before the worker preconditions; it deletes only fixture rows). `import _verify_cleanup as VC` in all five.

### Proof
| script | --self-checks-only | --cleanup-only |
|---|---|---|
| verify_bulkfetch | TOTAL: 1 PASS, 0 FAIL | CLEANUP_SECONDS 38.5, LEFTOVERS 0, TOTAL 1 PASS 0 FAIL (73 s wall) |
| verify_bulkfetchui | TOTAL: 1 PASS, 0 FAIL | CLEANUP_SECONDS 31.2, LEFTOVERS 0, TOTAL 1 PASS 0 FAIL (73 s wall) |
| verify_bulkfetchui2 | TOTAL: 1 PASS, 0 FAIL | CLEANUP_SECONDS 30.8, LEFTOVERS 0, TOTAL 1 PASS 0 FAIL (70 s wall) |
| verify_bulkfetchui3 | TOTAL: 8 PASS, 0 FAIL | CLEANUP_SECONDS 30.4, LEFTOVERS 0, TOTAL 1 PASS 0 FAIL (56 s wall) |
| verify_edgarpipelinea | TOTAL: 1 PASS, 0 FAIL | CLEANUP_SECONDS 34.9, LEFTOVERS 0, TOTAL 1 PASS 0 FAIL (71 s wall) |
Outputs: /tmp/vhk1/selfchecks/<s>.out, /tmp/vhk1/cleanup/<s>.out. py_compile OK on all five. Every cleanup removed 0 rows from 0 tables (no fixture rows existed), so every plan predicate was executed (EXISTS seeds) but the DELETE path itself was not exercised with real fixture rows — the next full run is the first that does. Pre-edit copies: /tmp/vhk1/g2/*.py.

Not done: edgarpipelinea --cleanup-only does not remove a killed run's R2 test objects (needs the bucket; the full run's teardown_and_restore still does). Module docstrings still describe the old FK-ordered teardown in prose (code comments updated). No commit.

### Group 3

### VHK1 group 3 — verify_rep1, rep3, rep4, dealroom2, dealroom3

### Task 1 (lines = before VHK1)
| script | line | what it compares | broke / would break by | VHK1 action |
|---|---|---|---|---|
| verify_rep1 | 1294–1323 | route gates BEFORE_REF fb4205d vs WORKING TREE; "10 new routes" by hard-coded path list | rep2, rep3 … every later sprint adding routes (by-path list was the workaround) | `self_routes`: BEFORE_REF → rep1's merge 14200f8; exact set == the 10 listed routes + gate names at the end ref; `still_gated` today |
| verify_rep3 | 1448–1453 | verify_rep1.py / verify_rep2.py contents read from WORKING TREE | any later edit to those scripts (VHK1 itself edits both) | `self_other_files`: read at rep3's merge 34b3691 (`VS.own_text`) |
| verify_rep3 | 1455–1456 | importer.py WORKING TREE == BEFORE_REF ("byte-identical") | any later sprint touching custody/importer.py | importer at rep3's merge == BEFORE_REF |
| verify_rep3 | 1556–1569 | route gates BEFORE_REF 93aaf23 vs WORKING TREE; new routes filtered by path `/api/v1/models` | rep4 (/replication routes) — the by-path filter was the workaround | `self_routes`: → 34b3691; ALL new routes must be under /models (28); `still_gated` |
| verify_rep3 | 775–779 | replica_terms.py today vs BEFORE_REF's MAKER_CHECKER_RULE | — | feature / before-only, left |
| verify_rep4 | 1457–1474 | route gates BEFORE_REF 3c09f92 vs WORKING TREE; "8 new routes" = everything new since base | rep5+ (count of all new routes) | `self_routes`: → 0b2f7ff; same 8-route assertions on the end ref; `still_gated` |
| verify_dealroom2 | 74, 1198–1219 | route gates BEFORE_REF 0823d72 vs WORKING TREE; new ∩ DEALROOM2_REF 994d8ce | rep1–rep7, dealroom3 (DEALROOM3 [FIND] fix) | `self_routes`: → merge 416a763; DEALROOM2_REF removed |
| verify_dealroom3 | 1410–1422 | route gates BEFORE_REF 376f8ef vs WORKING TREE; "19 new routes" = everything new | rep8, marketdata1, rep9 would (and any later sprint) | `self_routes`: → 5681d30; `still_gated` |
| all 5 | — | `authz1_routes.py --check` (tree vs tree) | — | stays in the full run only |
| 1c | — | no empty-real-data FAIL found in these 5 (real-data checks here are `not have` / fingerprint style: empty passes) | — | none — honest "no gap found" |
| 1d verify_rep4 | 108 | module-level `TODAY = datetime.now(timezone.utc).date()` (missing from the prompt's list — FIND) | midnight run | removed; `today()` at the call for 4 server-clock uses (3 `set_prices` freshness, 1 capability stub date); `seed_day()` = S["seed_day"] set in seed() for 8 snapshot as-of fixture dates (F1,H1,G1,K1,L1,F2,F3,Fnr — their spacing/order is the fixture) |

Exclusions / workarounds removed: rep1's "OWN routes by path" scoping (now an exact-set check), rep3's `/api/v1/models` path filter (now: every new route is under /models), dealroom2's DEALROOM2_REF intersection. Gate-unchanged checks keep the original `after`-only comparison (before → end ref).

### Task 5
rep1, rep3, rep4 moved onto `VC.cleanup` (+ `VC.leftover_count` in `cleanup_only`, `--cleanup-only` dispatched right after connect, `import _verify_cleanup as VC`; fk_children / delete_tree / fk_order / _FK_CACHE removed). GUCs kept (rep1 replica.teardown; rep3/rep4 models/replica/execution.teardown). Global org_id-NULL rows (term readings, source docs, holdings rows/snapshots, refresh events 2099, securities) are explicit plan seeds so the closure never has to reach them.
- rep3 keeps a model-family PRE-PASS (its old ordered deletes, same rows) inside the SAME transaction before VC.cleanup: the models ↔ model_library_entries cycle is row-level there (org-shelf entry → fixture model; library-sourced model → entry), which one DELETE per table cannot take. Not changeable without editing _verify_cleanup.py.
- dealroom2 was on VC but had NO --cleanup-only: added (no guard_prefix exists in it).

### Proof (outputs in /tmp/vhk1/selfchecks, /tmp/vhk1/cleanup)
| script | --self-checks-only | --cleanup-only |
|---|---|---|
| verify_rep1 | 5 PASS, 0 FAIL | 19.6 s, LEFTOVERS 0, 2 PASS 0 FAIL, rc 0, removed [] |
| verify_rep3 | 7 PASS, 0 FAIL | 15.7 s, LEFTOVERS 0, 2 PASS 0 FAIL, rc 0, removed [] |
| verify_rep4 | 4 PASS, 0 FAIL | 21.2 s, LEFTOVERS 0, 2 PASS 0 FAIL, rc 0, removed [] |
| verify_dealroom2 | 5 PASS, 0 FAIL | 24.9 s, LEFTOVERS 0, 1 PASS 0 FAIL, rc 0, removed [] |
| verify_dealroom3 | 5 PASS, 0 FAIL | 42.8 s, LEFTOVERS 0, 1 PASS 0 FAIL, rc 0, removed [] |
py_compile OK on all 5; AST scan: no undefined names. Not done: the full verifies were not run (by rule), so the new teardowns are proven on zero rows only.

### Group 4

### VHK1 group 4 — rep2, rep5, rep6 (dates / 1c only), rep7, rep7b

### Task 1 table
| script | line (pre-VHK1) | what it compares | broke / would break | VHK1 action |
|---|---|---|---|---|
| verify_rep2 | 1301 y_csv | csv_adapter/importer/base.py at BEFORE_REF == working tree | any later custody edit (rep3 left importer alone; rep9 order routing would) | self_csv: BEFORE_REF → rep2 merge b901a01 (VS.own_text) |
| verify_rep2 | 1303 y_csv | registry.py working tree startswith BEFORE_REF text | any later prepend/edit of registry.py | self_csv, end = rep2 merge |
| verify_rep2 | 1335 y_csv | BEFORE_REF custody pkg vs working-tree pkg, parsed in subprocesses | — (behaviour of today's code) | KEPT in full run: feature assertion on today's code (needs the app) |
| verify_rep2 | 1369–1390 y_routes | routes BEFORE_REF vs working tree; 21 new filtered by path prefix | rep3 /models, rep4 /replication (prefix filter was the workaround) | self_routes: VS.own_routes → rep2 merge; prefix filter now asserted on ALL new (len(new)==len(prefixed)==21); + still_gated; gates on R.end |
| verify_rep5 | 1497–1518 y_routes | same; 17 new by path filter | rep6, rep7, rep7b (/accounts/{id}/proposals/one-off) | self_routes → rep5 merge 2667e7e; filter asserted on all 17; + still_gated |
| verify_rep5 | 1396 | git ls-files sprint_prompts/logs empty | — | KEPT: today's-tree hygiene check |
| verify_rep7 | 1589–1625 y_routes | same; 19 new with exclusion lists | rep7b (fixed by rep7b fix 1 exclusion), lotmethod (/lot-method/ exclusion) | self_routes → rep7 merge 9ed75ae; exclusions REMOVED |
| verify_rep7b | 1379–1408 y_routes | routes vs working tree; own routes by path (`want`, rep9 fix) | lotmethod, rep8, marketdata1, rep9 (rep9 fix by-path) | self_routes → rep7b merge a5ffe9a: R.new must be exactly the 11 `want` paths, each naming its perm; + still_gated |
| verify_rep7b | 1434 regression | git diff -U0 BEFORE_REF..LOTMETHOD_BASE verify_rep7.py == REP7_ROUTE_SCOPE_DIFF | — (commit..commit already; LOTMETHOD_BASE == rep7b merge) | self_rep7_diff via VS.own_diff(BEFORE_REF, rep7b); REP7_ROUTE_SCOPE_DIFF kept (it IS rep7b's own edit) |
| verify_rep7b | 1436 regression | b1_diff_bad(LOTMETHOD_BASE, working-tree verify_rep7.py) | **already FAILING on main**: lotmethod itself edited rep7's y_routes (/lot-method/ exclusion), not B1; vhk1 edits too | pinned to lotmethod merge 376f8ef (VS.own_lines); lotmethod's exact 4 y_routes lines listed (LOTMETHOD_REP7_ROUTE_SCOPE) — [FIND] |
| verify_rep6 | — | (self-checks pinned by parent) | — | — |
| **1c** verify_rep7b | 843–880 y_migration | existing accounts outside fixture orgs / existing held-away drafts (rep9 fix 1: FIND + vacuous check) | lotmethod cleanup of REP2 leftovers | VS.when_empty: FIND when empty, rule asserted per row otherwise (no vacuous PASS) |
| 1c rep2/5/6/7 | — | real org_admin "missing perms" counts = 0 | cannot fail on empty | none needed (no gap found) |
| **1d** verify_rep2 | 130 | TODAY | midnight run | removed; 2 call-time (execute_by default, capability as_of), 2 seed-anchored (fixture position as_of, lot acquired) |
| 1d verify_rep4 | 108 | `TODAY = datetime.now(timezone.utc).date()` | — | NOT in my scope (prompt's list missed rep4) |
| 1d verify_rep5 | 158–160 | TODAY, VAL, YEAR_START | midnight / new year | removed; 3 call-time (plan years_to_complete, 2× execute_by), 17 seed-anchored (seed writes, fixture price_as_of == VAL, pure-engine sale dates on fixture lots, the valuation-move position) |
| 1d verify_rep6 | 255–258 | TODAY, VAL, YEAR, YEAR_START | midnight / new year | removed; 22 call-time (ctx_for default, profile bodies/years, future-date-refused declaration, execute_by, expiry→execute_by, harvest run_date, isolation bodies; MFS as_of now compared to the body's own as_of), 33 seed-anchored (seed, declared trades, wash windows +21/+26/+1, pure forward-edge Context, swap-back fixtures + scan dates, loss-bank tax_year, LBX sell) |
| 1d verify_rep7 | 167–168 | TODAY, YEAR (line ~1328 already date.today()) | midnight | removed; 26 call-time (ago(), imports & their expectations, wash contexts, realized YTD, execute_by, staleness, tax body, X-ray body), 3 seed-anchored (seed positions/cash, snapshot as-of); y_staleness asof_ha now reads the held-away import's own stored as-of S["ha_asof"] |

Anchors: `today()` (call-time) + `S["seed_day"/"seed_val"/"seed_year_start"]` set by `anchor_seed_dates()` / at the top of seed(). No module-level TODAY/VAL/YEAR/YEAR_START/NOW remains (AST check: none assigned, none referenced).

### Exclusions removed
- verify_rep7 y_routes: rep7b fix 1's `rep7b_added` set + `/api/v1/authority/` prefix, and lotmethod's `/api/v1/lot-method/` prefix (none exist at rep7's merge; count exact 19).
- verify_rep2 / rep5: by-path prefix FILTERS on the new-route count (now asserted on every route added instead).
- verify_rep7b: rep9 fix's by-path scoping of its own routes (`mine = paths in want`) → exact pinned set.
- Kept: verify_rep7b REP7_ROUTE_SCOPE_DIFF (rep7b's own exact edit); rep6's ok() clauses (parent).

### --self-checks-only (0 FAIL each; outputs in /tmp/vhk1/selfchecks/)
rep2 7 PASS · rep5 5 PASS · rep6 7 PASS · rep7 5 PASS · rep7b 6 PASS

### Cleanup
All five already on _verify_cleanup.py with --cleanup-only (confirmed; not re-run).

### Not done
- No full verify run (rule). The date conversions are proven by py_compile + an undefined-name AST scan only.
- verify_rep4's module-level TODAY is outside this group's scope.

### Group 5

### VHK1 group 5 — lotmethod, rep8, marketdata1, rep9

### Task 1 table (line numbers are from BEFORE the edit)
| script | line | what it compares | broke / would break | VHK1 action |
|---|---|---|---|---|
| verify_lotmethod | 1193–1214 y_routes | AUTHZ1_ROUTES.json at BEFORE_REF (a5ffe9a) vs the working tree: no gate changed/removed; "9 new routes all /lot-method/" = every route added since the base | dealroom3, rep8, marketdata1, rep9 each add routes (the count goes wrong at once) | self_routes: VS.own_routes(BEFORE_REF, "lotmethod") → end 376f8ef; same 9-route / permission assertions on R.end; + still_gated (today) |
| verify_lotmethod | 1172 y_b2_b5 (B2) | verify_rep5.py in the WORKING TREE: nested verify_rep2 timeout = 2700 | any later edit to verify_rep5's regression() (e.g. a new timeout) | self_b2_b5: VS.own_text("lotmethod", verify_rep5.py) |
| verify_lotmethod | 1179–1187 y_b2_b5 (B5) | verify_underlyingresolution / workflowmgr5 / workflowpermsfix in the working tree | any later edit to those scripts | own_text at 376f8ef; B4 (node --test tests/bugfix2/) stays in the full run |
| verify_lotmethod | 471, 533, 1118 | git_show(BEFORE_REF, …) only (frozen engine, verify_rep7 delete_tree, deal_lifecycle) | none — before-only | left as is |
| verify_lotmethod | 513 (1a git grep), y_b1 | today's code (callers of plan_sales) / runs five --cleanup-only (DB) | feature / DB | left in the full run |
| verify_lotmethod | 1c: 554 (1e) | `check(bool(sets) and every <= granted)` — FAILS when no real (non-is_test) org_admin role exists | data cleanup / a fresh DB | VS.when_empty([every] if sets else [], …, lambda e: e <= granted) — rule still asserted |
| verify_rep8 | 1200–1216 y_routes | ROUTES.json BEFORE_REF (5681d30) vs working tree; `new == NEW_ROUTES` (17) | marketdata1, rep9 add routes | self_routes: own_routes → 24962f1; NEW_ROUTES moved into the VHK1 block; + still_gated |
| verify_marketdata1 | 1287 y_mkt_unchanged | `git diff --stat BEFORE_REF -- services/market_data, market_data_nightly.py, routers/market_data.py` vs working tree | any later sprint touching the indicator layer | self_mkt_unchanged: VS.own_diff(BEFORE_REF, "marketdata1", …, --stat) → ab9d823; the MA import / security_data source checks (today's code) stay |
| verify_marketdata1 | 1358–1375 y_routes | ROUTES.json before vs working tree; `mine` = every TODAY route containing /security-data | rep9 (gates), any later /security-data route | own_routes → ab9d823; mine read from R.end; + still_gated |
| verify_marketdata1 | 644, 1271–1278 | today's code (rebalance_proposals/replica_viability readers, xray, vendor-name scan) | feature assertions | left as is |
| verify_rep9 | 1517–1536 y_routes | ROUTES.json before vs working tree (per-method keys); `git diff BEFORE_REF -- routers/blocks.py` (working tree) | any later sprint editing blocks.py or adding/changing a route | self_routes: before/end (afcae0b) per-method gates, VS.own_diff for blocks.py; the 30 routes exist, new and permissioned at the end; + still exist today with the same gate |
| verify_rep9 | 1559–1565 regression() | changed-line guard `git diff -U0 BEFORE_REF -- verify_rep7b.py` (working tree) == REP7B_ROUTE_SCOPE_DIFF exactly | vhk1 itself (adds --self-checks-only to verify_rep7b) and any later edit | self_rep7b_diff: VS.own_diff(…, args=("-U0",)) → afcae0b; REP7B_ROUTE_SCOPE_DIFF moved into the VHK1 block; the nested verify_rep7b run stays in regression() |
| all four | 1d | module-level TODAY/NOW | — | none: every expected date goes through a today() / now() function at the call |

### Exclusion lists removed
None. These four scripts had no exclusion clauses: lotmethod/rep8/marketdata1 counted every new route (now pinned instead); rep9 was already scoped to ROUTES / its own diff.

### Cleanup
All four already on _verify_cleanup.py with --cleanup-only (import VC + `"--cleanup-only" in sys.argv` dispatch) — confirmed; not run.

### --self-checks-only (python3, no DB / Doppler / network)
- verify_lotmethod.py: TOTAL: 7 PASS, 0 FAIL
- verify_rep8.py: TOTAL: 4 PASS, 0 FAIL
- verify_marketdata1.py: TOTAL: 5 PASS, 0 FAIL
- verify_rep9.py: TOTAL: 6 PASS, 0 FAIL
Outputs: /tmp/vhk1/selfchecks/<script>.out. py_compile clean on all four.

### Notes
- `authz1_routes.py --check` stays in each y_routes (full run only).
- `import _verify_cleanup as VC` in lotmethod moved up into the VHK1 block (BEFORE_REF = VC.LOTMETHOD_BASE needs it; stdlib only). Its later duplicate import was removed.
- rep9 keeps its own per-(method, path) gate keying (a local _gates_by_method) rather than VS.own_routes' methods-tuple keys, so its assertions stay the same; it reuses VS.gate_of / git_show / own_diff. No helper change needed.
- Not committed.

## --self-checks-only, every regression-set script (real tree, before commit)

| script | result |
|---|---|
| verify_authz1.py | TOTAL: 1 PASS, 0 FAIL |
| verify_usermgmt1.py | TOTAL: 1 PASS, 0 FAIL |
| verify_navregroup.py | TOTAL: 4 PASS, 0 FAIL |
| verify_bugfix1.py | TOTAL: 4 PASS, 0 FAIL |
| verify_dealroom1.py | TOTAL: 5 PASS, 0 FAIL |
| verify_dealroom2.py | TOTAL: 5 PASS, 0 FAIL |
| verify_dealroom3.py | TOTAL: 5 PASS, 0 FAIL |
| verify_bulkfetch.py | TOTAL: 1 PASS, 0 FAIL |
| verify_bulkfetchui.py | TOTAL: 1 PASS, 0 FAIL |
| verify_bulkfetchui2.py | TOTAL: 1 PASS, 0 FAIL |
| verify_bulkfetchui3.py | TOTAL: 8 PASS, 0 FAIL |
| verify_edgarpipelinea.py | TOTAL: 1 PASS, 0 FAIL |
| verify_rep1.py | TOTAL: 5 PASS, 0 FAIL |
| verify_rep2.py | TOTAL: 7 PASS, 0 FAIL |
| verify_rep3.py | TOTAL: 7 PASS, 0 FAIL |
| verify_rep4.py | TOTAL: 4 PASS, 0 FAIL |
| verify_rep5.py | TOTAL: 5 PASS, 0 FAIL |
| verify_rep6.py | TOTAL: 7 PASS, 0 FAIL |
| verify_rep7.py | TOTAL: 5 PASS, 0 FAIL |
| verify_rep7b.py | TOTAL: 6 PASS, 0 FAIL |
| verify_lotmethod.py | TOTAL: 7 PASS, 0 FAIL |
| verify_rep8.py | TOTAL: 4 PASS, 0 FAIL |
| verify_marketdata1.py | TOTAL: 5 PASS, 0 FAIL |
| verify_rep9.py | TOTAL: 6 PASS, 0 FAIL |
| verify_vhk1.py | TOTAL: 1 PASS, 1 FAIL before the commit; TOTAL: 2 PASS, 0 FAIL after it (401d419) |

verify_vhk1.py fails here only because nothing was committed yet: its one self-check is VHK1's own committed diff. It is re-run after the commit below.

## --cleanup-only, the 13 scripts moved onto `_verify_cleanup.py`

| script | CLEANUP_SECONDS | LEFTOVERS | TOTAL |
|---|---|---|---|
| verify_authz1.py | 19.1 | 0 | 1 PASS, 0 FAIL |
| verify_usermgmt1.py | 17.1 | 0 | 1 PASS, 0 FAIL |
| verify_navregroup.py | 25.8 | 0 | 1 PASS, 0 FAIL |
| verify_bugfix1.py | 54.3 | 0 | 1 PASS, 0 FAIL |
| verify_dealroom1.py | 16.4 | 0 | 1 PASS, 0 FAIL |
| verify_bulkfetch.py | 38.5 | 0 | 1 PASS, 0 FAIL |
| verify_bulkfetchui.py | 31.2 | 0 | 1 PASS, 0 FAIL |
| verify_bulkfetchui2.py | 30.8 | 0 | 1 PASS, 0 FAIL |
| verify_bulkfetchui3.py | 30.4 | 0 | 1 PASS, 0 FAIL |
| verify_edgarpipelinea.py | 34.9 | 0 | 1 PASS, 0 FAIL |
| verify_rep1.py | 19.6 | 0 | 2 PASS, 0 FAIL |
| verify_rep3.py | 15.7 | 0 | 2 PASS, 0 FAIL |
| verify_rep4.py | 21.2 | 0 | 2 PASS, 0 FAIL |

Every run removed 0 rows: no fixture rows existed. The new delete paths have not yet run against real fixture rows; the next full run of each script is the first. verify_bugfix1's teardown also seeds every FK child of `public.users` by the fixture user ids. U_RESTORER lives in the real default org, so without those seeds the shared cleanup's real-org refusal would trip.

## Simulation (a later sprint, in a temporary worktree)

The simulation ran after the work was committed (401d419), in `git worktree add -b vhk1-sim-task6 /tmp/vhk1-sim-task6 HEAD`, never in the operator's tree.

A throwaway commit there did two things:
- appended a fake route `GET /api/v1/vhk1-sim/fake` to `docs/AUTHZ1_ROUTES.json`. The rewrite also re-serialised the file with sorted keys, so it is a harsher change than a real sprint would make;
- appended one line to `apps/api/scripts/verify_rep5.py`.

Every regression-set script's `--self-checks-only` was then run from that worktree. Afterwards `git worktree remove --force`, `git worktree prune` and `git branch -D` ran, and the real tree's `git status --porcelain` was unchanged. No `git reset --hard` was used.

```
simulated commit: 5b48ee2 VHK1 simulation (throwaway)
 2 files changed, 14816 insertions(+), 14797 deletions(-)
verify_authz1.py             exit 0  TOTAL: 1 PASS, 0 FAIL
verify_usermgmt1.py          exit 0  TOTAL: 1 PASS, 0 FAIL
verify_navregroup.py         exit 0  TOTAL: 4 PASS, 0 FAIL
verify_bugfix1.py            exit 0  TOTAL: 4 PASS, 0 FAIL
verify_dealroom1.py          exit 0  TOTAL: 5 PASS, 0 FAIL
verify_dealroom2.py          exit 0  TOTAL: 5 PASS, 0 FAIL
verify_dealroom3.py          exit 0  TOTAL: 5 PASS, 0 FAIL
verify_bulkfetch.py          exit 0  TOTAL: 1 PASS, 0 FAIL
verify_bulkfetchui.py        exit 0  TOTAL: 1 PASS, 0 FAIL
verify_bulkfetchui2.py       exit 0  TOTAL: 1 PASS, 0 FAIL
verify_bulkfetchui3.py       exit 0  TOTAL: 8 PASS, 0 FAIL
verify_edgarpipelinea.py     exit 0  TOTAL: 1 PASS, 0 FAIL
verify_rep1.py               exit 0  TOTAL: 5 PASS, 0 FAIL
verify_rep2.py               exit 0  TOTAL: 7 PASS, 0 FAIL
verify_rep3.py               exit 0  TOTAL: 7 PASS, 0 FAIL
verify_rep4.py               exit 0  TOTAL: 4 PASS, 0 FAIL
verify_rep5.py               exit 0  TOTAL: 5 PASS, 0 FAIL
verify_rep6.py               exit 0  TOTAL: 7 PASS, 0 FAIL
verify_rep7.py               exit 0  TOTAL: 5 PASS, 0 FAIL
verify_rep7b.py              exit 0  TOTAL: 6 PASS, 0 FAIL
verify_lotmethod.py          exit 0  TOTAL: 7 PASS, 0 FAIL
verify_rep8.py               exit 0  TOTAL: 4 PASS, 0 FAIL
verify_marketdata1.py        exit 0  TOTAL: 5 PASS, 0 FAIL
verify_rep9.py               exit 0  TOTAL: 6 PASS, 0 FAIL
verify_vhk1.py               exit 0  TOTAL: 2 PASS, 0 FAIL
worktree removed: yes; branch deleted: yes
real tree git status unchanged
SIMULATION: 0 script(s) failing
```
