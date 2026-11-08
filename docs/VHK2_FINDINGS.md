# VHK2 — verify housekeeping round 2: findings (2026-11-03)

Base: `vhk2-work` @ 265f15c (main c6f7077 "merge rep10" + schema snapshot). Input: the full regression run of
2026-11-02 on bfdfe16 (`sprint_prompts/logs/regressions/2026-11-02/*.out` in the main working tree): 25 scripts,
14 clean, 11 with failures.

**Outcome: every failure was a stale check or a code/DB timing gap. No real defect was proven.** No gate, route
record, nav visibility, router or schema changed (Task 2 is empty, and verify_vhk2 asserts it). Every stale check
is now a rule that holds for every current row, page, field, script or caller.

## 1a — every 2026-11-02 failure, stale vs real

| Script | Failure | Class | Evidence | Now |
|---|---|---|---|---|
| verify_authz1.py | "effective permissions == Task 3 report" ×4 (34 → 59, 2 → 27) | STALE | Counts frozen in `docs/AUTHZ1_REAL_USERS.json`. Super admin = the whole `permissions` catalogue, which rep1–rep10 grew from 34 to 59. org_admin grew by design in `rbac.ORG_ADMIN_DEFAULT_PERMISSIONS` (`services/rbac.py:252-286`, each block commented with its sprint): 26 + review_agent_proposals = 27. | For EVERY current real user: `services.authz` Principal == the SQL recompute from current role grants; a non-active user has none on both paths. The Task 3 report stays as the historical record; only its "lost only via default-allow" accounting is asserted. Probe: 9 real users, 0 break it. |
| verify_authz1.py | "every verify script sets the flag before importing the app" 208/209 | STALE | The 209th is `verify_vhk1.py`, which never loads the app (git / file / subprocess checks only). | Every script that loads the app (`import main`, `from main import`, `import_module('main')`, `TestClient(`, `ASGITransport(`, or a local `_helper` that does) sets the flag BEFORE the first load. Probe: 211 scripts, 0 violations. |
| verify_authz1.py | route matrix org_admin: 32 unexpected 2xx | STALE | The verify froze the org_admin persona's permissions as `{manage_org_settings, review_agent_proposals}`, but the fixture org admin is seeded through `ensure_org_admin_role` (26 permissions). Every one of the 32 routes is gated by a permission the org_admin role holds by design (see 1b). | Persona permissions are read from the DB Principal, plus a check that each persona's DB permissions == what its seeding documents (org_admin == `ORG_ADMIN_PERMISSION ∪ ORG_ADMIN_DEFAULT_PERMISSIONS`, read from code). The per-route assertion is unchanged. Probe: 0 unexpected 2xx, 0 allowed routes refused. |
| verify_navregroup.py | 23 pages without a 1a row | STALE | Every missing page was added by a later sprint (REP1–REP10, MARKETDATA1). At navregroup's own merge (a911ac7) every page had a row. | At its own merge (git only, also in `--self-checks-only`): every page then had a 1a row. Today: every page is a nav tab / group landing / redirect / sub-page, or has a stated reason in `NON_NAV_REASONS`, and has a traced gate. |
| verify_navregroup.py | "additions exactly the intended ones" ×2 | STALE | The extra hrefs are later sprints' tabs (REP1/2/3/5/6/9, MARKETDATA1). The harness as navregroup left it gives `additions_exact` for all 6 personas. | At its own merge: `additions_exact`. Today: `additions_ok` (every href added since the baseline is admitted by its traced server gate), `removals_exact`, no new refusal. |
| verify_navregroup.py | traced gate / added href refused / new refusal ×6 | STALE | The hand table `navregroup_server_gates.mjs` (2026-10-20) answers "refused" for any href it does not list, so "refused" meant "untraced". The real gates admit those personas (1c). | `navregroup_traced_gates.mjs`: the hand row, else the `AUTHZ1_ROUTES.json` record of every API the page calls. An untraced href still fails CLOSED. `y_live_roles` applies the rule to every real role in the default org, read at run time. |
| verify_dealroom1.py | "signed-URL downloads allowed exactly there" ×4 (-1) | STALE | `-1` is the script's own marker for "200 but no `https://r2.mock/` signed URL". dealroom3 made new rooms watermark-ON by default (`docs/dealroom3_part1.sql:48`; `sprint_prompts/dealroom3.structural.md:118`). An investor now gets `href` → the `/file` route, which streams a freshly watermarked PDF (`verify_dealroom3.py:763` asserts exactly this). | The rule reads the room's current settings at the check. Manager or watermark off → a signed URL to the original. Watermark on → no URL, and GET `/file` returns 200, a PDF, an `X-Watermark-Id` header, and bytes ≠ the original. view_only → no investor download. The allowed set == EXPECT[who], and every refusal is the same 404. Stricter than before: the stream is actually fetched. Fixtures are now real one-page PDFs. |
| verify_dealroom1.py | "teardown raised — deal_room_activity is append-only" | STALE (teardown) | dealroom3's trigger `deal_room_log_append_only()` (`docs/dealroom3_part1.sql:263`) admits DELETE only off `app_service`, with `dealroom.activity_teardown='fixture'`, on an is_test org (1e). | Task 4. |
| verify_bulkfetchui.py | "live preview returns under 2 s" (best of 3, 4.095 s) | STALE (load) | The run was measured while the regression suite was loading the laptop. | `PREVIEW_LIMIT_SECONDS = 2.0` is unchanged. Each round is best of 3. While over 2 s AND the 1-min load per CPU is > 0.75, wait 30 s and re-measure (up to 5 rounds). Every round's load is printed and goes in the detail. |
| verify_bulkfetchui3.py | "regression_set.txt is seeded with the 11 scripts" | STALE | Each later sprint appends itself to the list, as designed. | At bulkfetchui3's own merge (fb4205d): == the 11 seeds. Today: every seed is still listed, every name is an existing `verify_*.py`, no duplicates. |
| verify_edgarpipelinea.py | "only the EDGAR screens pass serverSide" | STALE | `AccessGrid.jsx` (dealroom1 → dealroom3) opted in on purpose. | At its own commit 4080f41: only Edgar*.jsx. Today: the mode is still opt-in, and the EDGAR monitor still passes it. |
| verify_edgarpipelinea.py | "8 real 424B2s across >= 5 issuer groups" (4 groups) | STALE (real-data sample) | The real pipeline fetched the never-fetched Goldman Sachs pool down to zero. Pools: JPMorgan 5706, Morgan Stanley 4236, RBC 944, UBS 3787. | At most 2 per group, all from `SAMPLE_GROUPS`. A missing group is a [FIND]. Fewer than the fetch cap is a [FIND] and the fetch tests skip. |
| verify_rep1.py | `_apply_rep1.py --verify` "[GAP] field registry seeded" | STALE | rep8 added critical fields scoped to buffer / income ETFs. rep1's five have `instrument_classes` NULL. | Among active critical fields with `instrument_classes IS NULL`: exactly rep1's five. Every class-scoped critical field names a non-empty class list. → SCHEMA OK. |
| verify_rep5.py | "an unpriced buy is by NOTIONAL" | STALE (cleared by rep10 fix 1) | Run on bfdfe16. marketdata1's custodian provider priced sec_B from the fixture IRA valuation. d24e82a leaves it unvalued and asserts the premise (`verify_rep5.py:1119-1126`). | Targeted: `rebalance_proposals._unheld_prices` for a security no org position values and no price row covers → `{}` (bought by notional). `--self-checks-only` 5/0. |
| verify_lotmethod.py | 2× missing `manage_hypothetical_policy` | STALE (code/DB timing; cleared) | The DB had rep10's grant applied, while the code run was bfdfe16, before rep10 added it to `ORG_ADMIN_DEFAULT_PERMISSIONS` (`rbac.py:285`). Both checks are already rules. | Targeted (the exact query from verify_lotmethod:594-601, current rbac): 2 real org_admin roles, every-held 26, missing from grant []. `--self-checks-only` 7/0. |
| verify_marketdata1.py | `_apply_marketdata1.py --verify` "[GAP] the vendor slot is in no platform rank" | STALE (rep10 deliberate) | `docs/rep10_part1.sql:108/128` ranks the new `factor_score` type csv_upload 1, vendor_slot 2 ("not connected — skipped until a contract exists"). `_apply_rep10.py:148` asserts it. Restoring "no rank" would break rep10. | The vendor slot is in no platform rank for marketdata1's OWN six data types. Wherever a later sprint ranks it, it is never first, and the provider is still `not_connected`, so the walk skips it. → SCHEMA OK. |
| verify_vhk1.py | "no uncommitted change outside the VHK1 scope" (`Claude outputs/`) | STALE | The check reads the working tree's `git status`, which after the merge holds other work. The folder is gone from disk in the main tree, but b9c186c (rep10 fix 1) had COMMITTED three prompt copies under it, which showed as ` D`. | The check applies only while vhk1 is unmerged; afterwards dirty paths are a [FIND]. VHK2 commits the operator's removal of `Claude outputs/` (3 files; still in history). |

## 1b — the 32 org_admin 2xx (verify_authz1 route matrix)

Reproduced live in the fixture orgs, then torn down (leftovers 0): 30 GETs + 2 POSTs. Today the frozen persona
yields 36, because rep10 added `/replication/backtests`, `/replication/hypothetical-policy`, `/tax/gifts` and
`/tax/gifts/donees`. Every record is `rule: perm`, `after: authz <perm>`, and the live gate is
`require_permission(<perm>)` (`authz1_routes.py --check` CURRENT). org_admin holds each permission through
`ORG_ADMIN_DEFAULT_PERMISSIONS`.

| Routes | Gate | Sprint | Group |
|---|---|---|---|
| GET /replication/registry, /replication/options/quotes, /replication/options/contracts, /xray/households | view_replication | rep1, rep8, rep7 | record correct; verify persona stale |
| GET /execution/instructions, /execution/accounts | view_execution | rep2 | same |
| GET /execution/blocks, /blocks/routers, /blocks/candidates, /blocks/settings/execution-accounts, /blocks/settings/policies | view_blocks | rep9 | same |
| GET /proposals/accounts, /proposals, /lot-method/vocabulary | view_proposals | rep5, lotmethod | same |
| GET /tax/overview, /tax/subjects, /tax/securities, /tax/profiles, /tax/carryforwards, /tax/substitutions, /tax/declared-trades, /harvest/runs | view_tax | rep6 | same |
| GET /held-away/providers | view_held_away | rep7 | same |
| GET /authority/vocabulary | view_authority | rep7b | same |
| GET /models, /models/template, /models/mappings, /models/uploads, /models/uploads/export.csv, /models/library | view_models | rep3 | same |
| POST /tax/substitutions/denials/seed, /tax/loss-bank/refresh | manage_tax | rep6 | same. The frozen set fired these writes with `{}` as an entitled org admin; teardown removed the rows. The re-based matrix fires a write only where the DB grant says deny. |

**Gate looser than intended: none. Stale record: none.** Only the verify's own persona set was stale.

## 1c — nav hrefs a role sees but the "server refuses"

Nav gate from `apps/web/lib/menuVisibility.mjs`; server gate = the `AUTHZ1_ROUTES.json` GET record of every API
the page calls.

| href | role | nav rule | server gate | sprint intent | verdict |
|---|---|---|---|---|---|
| /admin/custodian-capabilities | org_admin, super_admin | staff or manage_org_settings | `staff or authz manage_org_settings` | REP2 "staff and org admins read it, Super Admin edits it" | nav == server; stale table |
| /admin/security-data/sources, /quality | org_admin, super_admin | manage_org_settings | any(view_replication, manage_org_settings) | MARKETDATA1 | nav narrower than the server (never shown and refused); stale table |
| /admin/security-data | super_admin | super_admin | super_admin | MARKETDATA1 | equal; stale table |
| /admin/allocation-policy, /admin/executing-accounts, /portfolio/blocks | super_admin | view_blocks | view_blocks | REP9 | equal |
| /portfolio/accounts | super_admin | view_proposals | view_proposals | REP5 | equal |
| /portfolio/instructions | super_admin | view_execution | view_execution | REP2 | equal |
| /portfolio/models | super_admin | view_models | view_models | REP3 | equal |
| /portfolio/replication | super_admin | view_replication | view_replication | REP1 | equal |
| /portfolio/tax | super_admin | view_tax | view_tax | REP6 | equal |

**Nav wrong: none. Server wrong: none.** Live-role probe over every real role in the default org: the only
refusals are navregroup's own pre-existing [FIND]s, `/portfolio/positions` and `/portfolio/securities` (nav
ungated, API view_portfolio) and `/admin/workflows` (profile-axis author_workflows).

[FIND] `/portfolio/households` (REP7 X-ray index) has no nav row and no link from any tab; it is reachable by URL
only. It is recorded in `NON_NAV_REASONS`, not changed.

## 1d — dealroom1 downloads

-1 = "answered 200 with no signed URL". By dealroom3's design, a watermark-on room serves investors the `/file`
rendition route instead of a signed URL. The correct expectation is the 1a rule above: an allowed download must
actually serve the file as the room's settings say, and the stream is fetched and checked. Read-only probe of
`services.deal_room_control.document_policy` over all 8 watermark × downloads × manager combinations: all match
the rule. The live column default is `'on'`; all 9 real rooms are `('off','allowed')` (migrated rooms keep off).

## 1e — dealroom1 teardown

verify_dealroom3 deletes its activity rows on the operator DSN with
`VC.cleanup(..., gucs=("dealroom.signature_teardown", "dealroom.activity_teardown"))`, after an is_test premise
refusal (`verify_dealroom3.py:110, 297-311`). verify_dealroom1 now does the same: `ACTIVITY_TEARDOWN_GUCS =
("dealroom.activity_teardown",)` passed as `gucs=`, scoped by its own fixture ids, and it refuses to delete if a
fixture org is not is_test. Its fixture orgs `99000000-…-d0101/d0102` are is_test (live query).

## 1f — other frozen counts / lists in the regression set

Re-based (beyond 1a):

| Script | Froze | Rule |
|---|---|---|
| verify_edgarpipelinea.py (existing documents) | `identity_rows == 200 and linked == 200`, `st == 200` | every pre-existing document is linked to a fetched manifest row; every pre-existing manifest row is fetched with no policy version (`VS.when_empty`) |
| verify_rep2.py (y_capabilities) | `len(alt) == 13`, all 'unknown' on the REAL Altruist row | a cell for every `custodian_capabilities.CAPABILITY_KEYS` key; every unknown cell unavailable; recorded values → [FIND] |
| verify_bulkfetchui.py:666 | `tg == {"laptop","render"}` (live CHECK vocabulary) | `{"laptop","render"} <= tg` (the same rule as run_kind beside it) |
| apps/web/tests/nav/navregroup.test.mjs | rendered sidebar == a hand list | rendered == `visibleSections(me)`, and ⊇ every entry navregroup gave each persona |
| menuvisibility_harness.mjs → verify_authz1 regression() | `additions_exact` over today's harness | `additions_ok` (verify_navregroup checks `additions_exact` at its own merge) |

Already rules or own-merge-scoped (no change): every `VS.own_routes` count (dealroom2 14, dealroom3 19, rep1 10,
rep2 21, rep3 28, rep5 17, rep6 31, rep7 19) is BEFORE_REF → the sprint's own merge; the "every real org_admin
role holds…" checks in lotmethod / rep5 / rep6 / rep7 / rep7b; edgarpipelinea's migration-declares-every-column
rule.

Timing note: the "real org_admin" rules go red in the window where a migration is applied to the DB before its code
is merged (lotmethod on 2026-11-02). That is timing, not staleness.

## Invariants kept

Each is still hit by verify_vhk2's static scan, and each is listed in its `INVARIANTS`:

| Script | Line (substring) | Why it is a true invariant |
|---|---|---|
| verify_navregroup.py | `set(perm_ids) == {"view_deals", "manage_deals"}` | the query is filtered to those two names: "both exist" |
| verify_dealroom2.py | `pages[3][0] == 404` | the script's own fixture pages |
| verify_bulkfetch.py | `len(stamps) == 36` | the script's own fixture request stamps |
| verify_bugfix2.py | `len(b) == 73` | (CATCHALL1) the BEFORE record read at the pinned git ref BEFORE_REF — history, never live |
| verify_bugfix2.py | `len(table) == 73` | (CATCHALL1) the same pinned BEFORE record, classified |
| verify_bugfix2.py | `len(from_open) == 45` | (CATCHALL1) BEFORE_REF → bugfix2's own end ref (both pinned) |
| verify_bugfix2.py | `for l in ls] == ["/portfolio/securities"]` | (CATCHALL1) the links for the script's own fixture principals |
| verify_crmdetail1.py | `len(db) == 11` | (CATCHALL1) the live detail rows of the script's own fixture trust |
| verify_bugfix3.py | `len(made) == 17` | (CATCHALL1) the script's own fixture creations (4 + 6 + 6 + cash) |
| verify_bugfix3.py | `set(plan) == {"real_estate", "other"}` | (CATCHALL1) the backfill plan over the script's own fixture org |
| verify_secmaster2.py | `len(etf) == 12` | (CATCHALL1) the committed fixture CSVs (fd_etfs_PCX / fd_funds_NAS) |
| verify_prices1.py | `len(sp) == 30`, `len(fr) == 71`, `len(days) == 71` | (CATCHALL1) the script's own synthetic bar series (SYN) |

**Handed off, not an invariant (CATCHALL1):** verify_bugfix2.py `len(changed) == 45` compares bugfix2's BEFORE
record with TODAY's AUTHZ1_ROUTES.json, so any later sprint that gates one more of those routes breaks it. It is
frozen against live state; verify_vhk2 reports it as a `[FIND]` (`HANDED_OFF`) owned by verifyfix1 (read it at
bugfix2's own end ref, as its line-165 check already does), rather than listing it here.
| verify_edgarpipelinea.py | `all(n.startswith("Edgar") for n in at_end)` | pinned to edgarpipelinea's own commit 4080f41 (rule 1), not today |
| verify_edgarpipelinea.py | `len(res["rows"]) == 50` | the API page size |
| verify_rep2.py | `set(acct) == {"id", "name", "raw"}` | a mocked payload the script builds |
| verify_rep5.py, verify_rep6.py, verify_rep7.py, verify_rep7b.py, verify_rep9.py | `codes == {"buy", "sell"}` | the query is `WHERE code = ANY(['buy','sell'])`: "these two exist" |
| verify_rep7b.py | `len(results) == 4 and all(len(o) == 14 …)` | its own fixture matrix (4 cells × 14 own paths) |
| verify_rep8.py | `set(roles) == {"underlying", …}` | its own fixture legs |
| verify_marketdata1.py | `set(rej) == {"unresolved", …}` | its own fixture upload's rejects |
| verify_vhk1.py | `len(names) >= 25` | a floor on a list that only grows, plus "vhk1 is listed" |
| _apply_rep1.py | `perms == {"view_replication", …}` | the query is `LIKE '%_replication'`: the sprint's own three |
| _apply_rep1.py | `crit == {"expense_ratio_gross_pct", …}` | now over all-class critical fields only (`instrument_classes IS NULL`), i.e. rep1's own five; class-scoped fields (rep8) are excluded by the rule |
| _apply_rep2.py | `perms == {"view_execution", …}` | `LIKE '%_execution'`: its own three |
| _apply_rep1.py, _apply_rep2.py, _apply_rep3.py | "granted to org_admin and no other role" | design invariant: these permissions are org_admin-only. A second holder is a fixture role (rep3 excludes them) or a custom role the operator should see. Re-base candidate if a custom-role feature ships: "org_admin holds all three; any other holder is a [FIND]". |

Kept by the other forks' scans: verify_authz1's three named super-admin accounts (named identities, not a count);
verify_authz1's fixture member == `C.MEMBER_PERMS` (its own fixture); navregroup's `AUTHZ1_REMOVALS` /
`BUGFIX1_COMING_SOON` (a removal from a menu must be stated; later sprints only add); `len(routed) >= 8` (a
floor); verify_dealroom1's within-run before/after table counts (the CLAUDE.md backstop: `audit_log`,
`notifications` and `member_todos` are whole-table counts, so a concurrent real write during a run would trip it;
flagged, left as is) and its nine standard folders (dealroom1's design constant).

## Task 2 — real defects fixed

None proven, so no application change. `docs/AUTHZ1_ROUTES.json` and `apps/web/lib/menuVisibility.mjs` are
byte-identical across VHK2; verify_vhk2 asserts both, and asserts VHK2's diff touches no application code. The
nav / server rule is still proven both ways on identical hrefs (verify_vhk2 `y_task2`, node over
`menuVisibility.mjs` + `navregroup_traced_gates.mjs`):

| href | shown AND admitted | hidden AND refused |
|---|---|---|
| /admin/custodian-capabilities | org_admin (manage_org_settings) | view_replication-only member |
| /admin/security-data/sources, /quality | org_admin | no-permission member |
| /portfolio/blocks, /admin/security-data | super_admin | org_admin (manage_org_settings only) |
| /admin/vhk2-does-not-exist | — | untraced → refused even for Super Admin (fails closed) |

On the server side, the same identical-request proof is verify_authz1's route matrix (org_admin vs member vs
other_org, per route, with the persona permissions now read from the DB). It is part of verify_vhk2 (2).

## Task 4 — dealroom1 teardown and leftovers

`verify_dealroom1.py --cleanup-only` (2026-11-03, operator DSN, synchronous):
```
[INFO] cleanup verify_dealroom1: 27 table(s), 625 row(s), 678 queries in 29.5 s
CLEANUP_SECONDS: 31.0
LEFTOVERS: 0
[PASS] --cleanup-only: zero fixture rows remain — 0
TOTAL: 1 PASS, 0 FAIL
```
Rows removed included deal_room_activity 8, deals 8, users 12, organizations 2. Before, both fixture orgs were
present; after, 0.

## Task 5 — the rep10-related three

All three are clear on c6f7077 (details in 1a): rep5 by rep10 fix 1's fixture; lotmethod because rep10's code
now grants `manage_hypothetical_policy`; marketdata1's `--verify` rule changed, because rep10 ranked the vendor
slot deliberately. Read-only probe outputs:
```
lotmethod 1e: 2 real org_admin roles; every-held 26; missing from grant []
lotmethod new-org: grant covers every-held = True; manage_hypothetical_policy in grant = True
rep5 unpriced: security d526209e-… (valued by no org position, no price row) -> _unheld_prices {} (absent = bought by notional)
_apply_marketdata1.py --verify: [OK] wherever a later sprint ranks the vendor slot, it is never first and it is
  still not connected — ranked for ['factor_score'] … SCHEMA OK
_apply_rep1.py --verify: [OK] field registry seeded; all-class critical … 35 fields, all-class critical
  ['expense_ratio_gross_pct','expense_ratio_net_pct','index_name','rebalance_frequency','weighting_method'] … SCHEMA OK
```

## Task 6 — proof run (synchronous; no full verify, no npm)

Per-rule probes (read-only, `doppler run -p hollisworks -c prd -- apps/api/venv/bin/python`):

| Rule | Result |
|---|---|
| authz1: Principal == recompute, every real user | 9 users, 0 break it (59 / 27 / 10 permissions by role) |
| authz1: flag before app load | 211 scripts, 0 violations |
| authz1: route matrix with DB persona permissions (fixtures seeded and torn down) | 510 requests, 0 unexpected 2xx, 0 allowed-but-refused; leftovers 0 |
| navregroup: every real role's visible hrefs admitted by their traced gate | refusals ⊆ {/admin/workflows, /portfolio/positions, /portfolio/securities} (pre-existing FINDs) |
| navregroup: offline run of the changed sections (DB rows from the probe) | 72 PASS, 0 FAIL; `node --test tests/nav` 20 pass |
| dealroom1: download rule vs `document_policy` | 8/8 combinations match |
| bulkfetchui3: list rule | 26 listed; missing seeds []; no such file []; dups [] |
| edgarpipelinea: callers at 4080f41 / opt-in today | ['EdgarPipelineMonitor.jsx'] / True |
| edgarpipelinea: sample pool | 4 groups; Goldman Sachs absent → [FIND] |
| edgarpipelinea: pre-existing documents | identity 200, linked 200, pre_rows 200, pre_bad 0 |
| rep2: Altruist capability rule | keys == CAPABILITY_KEYS (13); recorded {} |
| bulkfetchui: load rule | 1-min load per CPU 0.14 (retry threshold 0.75) |

py_compile: every changed script OK. The `--self-checks-only` results for the whole regression set are below
(section "Self-checks run").

## Self-checks run

`python3 apps/api/scripts/<name> --self-checks-only` for every script in `scripts/regression_set.txt`, synchronously, at af17b96 (2026-11-03): **27 scripts, 0 FAIL.** verify_vhk1's static rules plus helper checks, run in-process over the new set: 149 PASS, 0 FAIL. `verify_vhk2.py --cleanup-only` with VERIFY_NESTED=1: LEFTOVERS 0.

| Script | Exit | Total |
|---|---|---|
| verify_authz1.py | 0 | TOTAL: 1 PASS, 0 FAIL |
| verify_usermgmt1.py | 0 | TOTAL: 1 PASS, 0 FAIL |
| verify_navregroup.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_bugfix1.py | 0 | TOTAL: 4 PASS, 0 FAIL |
| verify_dealroom1.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_dealroom2.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_dealroom3.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_bulkfetch.py | 0 | TOTAL: 1 PASS, 0 FAIL |
| verify_bulkfetchui.py | 0 | TOTAL: 1 PASS, 0 FAIL |
| verify_bulkfetchui2.py | 0 | TOTAL: 1 PASS, 0 FAIL |
| verify_bulkfetchui3.py | 0 | TOTAL: 8 PASS, 0 FAIL |
| verify_edgarpipelinea.py | 0 | TOTAL: 1 PASS, 0 FAIL |
| verify_rep1.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_rep2.py | 0 | TOTAL: 7 PASS, 0 FAIL |
| verify_rep3.py | 0 | TOTAL: 7 PASS, 0 FAIL |
| verify_rep4.py | 0 | TOTAL: 4 PASS, 0 FAIL |
| verify_rep5.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_rep6.py | 0 | TOTAL: 7 PASS, 0 FAIL |
| verify_rep7.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_rep7b.py | 0 | TOTAL: 6 PASS, 0 FAIL |
| verify_lotmethod.py | 0 | TOTAL: 7 PASS, 0 FAIL |
| verify_rep8.py | 0 | TOTAL: 4 PASS, 0 FAIL |
| verify_marketdata1.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_rep9.py | 0 | TOTAL: 6 PASS, 0 FAIL |
| verify_rep10.py | 0 | TOTAL: 5 PASS, 0 FAIL |
| verify_vhk1.py | 0 | TOTAL: 2 PASS, 0 FAIL |
| verify_vhk2.py | 0 | TOTAL: 36 PASS, 0 FAIL |

Not run (sprint rule: limited WSL memory, aibudget1 in parallel): any FULL verify, npm. verify_vhk2 (2) runs the ten previously failing scripts in full. That is the operator's run.
