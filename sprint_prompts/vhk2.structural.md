VHK2 — verify housekeeping round 2: the full regression run of 2026-11-02
(25 scripts, 14 clean, 11 with failures) found checks frozen against LIVE
state — permission counts, page lists, field registries, real-data samples,
the regression list itself — that later sprints legitimately changed, plus
three items that may be real defects (org_admin routes answering 2xx where
AUTHZ1_ROUTES.json says refused; nav links the server refuses; a teardown
blocked by an append-only log). Decide each one with evidence: a stale check
is re-based on a rule that survives later sprints; a real defect is fixed in
the code. 7 tasks + verification.

Context: vhk1 (merged bfdfe16) pinned every "since a base commit" self-check
to its sprint's own merge. This round covers what vhk1 did not: checks that
compare against the CURRENT database, page tree or file lists. main is now
c6f7077 (rep10 merged after that run). aibudget1 is running in parallel on
another worktree — do not touch its files (services/ai_ensembles.py,
routers/ai_ensembles.py, EnsemblePanel.jsx, scheduler budget code); if both
sprints edit scripts/regression_set.txt or docs/PROJECT_STATUS.md, keep your
edits to your own lines so the merge is "keep both".

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from sprint_prompts/logs/
regressions/2026-11-02/*.out in the MAIN working tree at
/mnt/c/Users/Joe/2ndActCapital — read them there; the run was on bfdfe16,
before rep10):
- verify_authz1 (108/6): "effective permissions == Task 3 report" for 4
  users — 34 expected, now 59 (and 2 → 27): counts frozen at authz1; one
  check "every verify script sets the flag before importing the app" —
  208/209 (name the script); "route matrix — org_admin: every exercised
  route matches AUTHZ1_ROUTES.json (zero unexpected 2xx)" — 626 requests, 32
  mismatches, e.g. 'unexpected 2xx GET /api/v1/replication/registry → 200',
  'GET /api/v1/replication/options/quotes → 200'.
- verify_navregroup (118/9): pages added since navregroup missing from its
  1a table (/admin/allocation-policy, /admin/custodian-capabilities,
  /admin/executing-accounts, /admin/security-data, /admin/security-data/
  quality, /admin/security-data/sources, /portfolio/accounts/[id], …);
  "additions are exactly the listed intended ones" for
  org_admin_manage_org_settings and super_admin; and "every visible href has
  a traced server gate" / "no ADDED href is refused by its server" / "no href
  is refused that was not already refused before" — org_admin: /admin/
  custodian-capabilities, /admin/security-data/quality, /admin/security-
  data/sources; super_admin: allocation-policy, executing-accounts,
  security-data, portfolio/accounts, portfolio/blocks, portfolio/instru… .
- verify_dealroom1 (145/5): "signed-URL downloads allowed exactly there;
  every refusal the same 404" for signed / approved / named /
  anonymous_to_deal — the allowed downloads now return -1 (not 200) while
  refusals stay 404 (dealroom3 added view-only / watermark / expiry); and
  "teardown raised — RaiseError: deal_room_activity is append-only: DELETE
  refused" (dealroom3's trigger) → it leaves its test-org rows.
- verify_bulkfetchui (105/1): "live preview returns under 2 s" — best of 3
  4.095 s while the regression suite was loading the laptop.
- verify_bulkfetchui3 (111/1): "regression_set.txt is seeded with the 11
  scripts" — the list now has 29.
- verify_edgarpipelinea (102/2): "DataGrid gained an opt-in serverSide mode;
  only the EDGAR screens (Edgar*.jsx) pass it" — callers now include
  AccessGrid.jsx; "a fixed sample of 8 real 424B2s … across >= 5 issuer
  groups" — 4 groups (JPMorgan, Morgan Stanley, Royal Bank of Canada, UBS).
- verify_rep1 (135/1): `_apply_rep1.py --verify: SCHEMA OK` → "[GAP] field
  registry seeded …" — rep8 added option fields (buffer_pct, cap_pct,
  call_coverage_pct, call_strike_rule, call_tenor_days …).
- verify_vhk1 (143/1): untracked "Claude outputs/" in the repo root — since
  moved out by the operator; nothing to do but confirm.
- Expected to clear with rep10 merged (CONFIRM by --self-checks-only and the
  targeted checks, not by assumption): verify_rep5 (unpriced buy — fixed by
  rep10 fix 1), verify_lotmethod (2: manage_hypothetical_policy now granted
  by rbac.ensure_org_admin_role), verify_marketdata1 (1: "[GAP] the vendor
  slot is in no platform rank" from _apply_marketdata1.py --verify — find
  whether rep10 changed the ranks; if the vendor slot SHOULD be ranked,
  restore it; if rep10 deliberately removed it, the --verify rule changes
  with the reason).
- vhk1 helpers: apps/api/scripts/_verify_self.py (sprint_end_ref,
  own_routes, still_gated, own_lines, when_empty); docs/VERIFY_RULES.md;
  every regression-set script supports --self-checks-only and
  --cleanup-only; teardown via scripts/_verify_cleanup.py.
- WSL memory on the operator's laptop is limited and aibudget1 is running:
  the sprint must NOT run npm run build or any FULL verify script; --self-
  checks-only and --cleanup-only MAY be run, synchronously.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground. No network calls. Never print a secret. NEVER weaken an
assertion about behaviour: a frozen snapshot becomes a RULE that holds for
every current row (e.g. "every user's effective permissions == the
recompute from their roles", "every page has a nav row and a traced
gate"), never a bigger number. Application code may change ONLY where Task
1 proves a real defect (a gate looser than its route record and its sprint's
intent; a nav link shown to a role the server refuses; a teardown path) —
say which, with evidence. Commit ALL changed files on the current branch at
the end with message "vhk2: <one line per item, stale vs real>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. For EACH failure above: stale check or real defect, with evidence.
  1b. authz1 route matrix: for each of the 32 org_admin 2xx — the route's
      AUTHZ1_ROUTES.json record (after / expect), its live gate, the sprint
      that added it and that sprint's stated permission. Group them: record
      stale (the sprint granted org_admin on purpose — e.g. view_
      replication is org_admin by default) vs gate looser than intended.
  1c. navregroup: for each href a role sees but the server refuses — is the
      nav visibility wrong (shown without the permission) or the server gate
      wrong (refuses a role the sprint meant to admit)?
  1d. dealroom1 downloads: what the allowed paths return now (-1 = what?),
      whether dealroom3 intended them (watermarked stream / view-only), and
      the correct expectation.
  1e. dealroom1 teardown: how dealroom3's own verify deletes its activity
      rows (a GUC or privileged path) — reuse it.
  1f. Every OTHER regression-set check that compares against a frozen
      count / list of live rows, pages or files (scan all 29 scripts) — the
      same class, even if it has not failed yet. Table: script, line, what
      it freezes, the rule that replaces it.

=== TASK 2: FIX REAL DEFECTS (only those 1a–1c prove) ===
Gates / nav visibility corrected to the sprint's documented intent; each
change listed with the route or href, before → after, and the doc line it
follows. No new permission names.

=== TASK 3: RE-BASE STALE CHECKS ===
Every 1a / 1f stale check becomes a durable rule (no frozen counts, no
frozen lists of other sprints' pages / fields / scripts / callers). Real-data
samples (edgarpipelinea issuer groups) use _verify_self.when_empty-style
handling: assert the rule on what exists; a smaller real corpus is a [FIND],
not a FAIL. The perf check (bulkfetchui) measures under a stated load rule:
retry when the machine's load is high (report the load), never a raised
limit.

=== TASK 4: TEARDOWN ===
verify_dealroom1's teardown deletes its own activity rows the way
dealroom3's verify does (scoped by fixture id); --cleanup-only removes the
rows left by the 2026-11-02 run; zero fixture rows after.

=== TASK 5: THE rep10-RELATED THREE ===
Confirm verify_rep5's, verify_lotmethod's and verify_marketdata1's failures
are gone on c6f7077 (targeted: the exact check's query / function, plus
--self-checks-only) or fix them (marketdata1's vendor-slot rank per the
CONFIRMED note).

=== TASK 6: PROOF YOU MAY RUN (synchronously, quick) ===
py_compile every changed script; --self-checks-only for every regression-
set script → 0 FAIL; --cleanup-only for verify_dealroom1 → zero rows; for
each re-based rule, a one-off probe that evaluates it against the live DB
(read-only) and prints the result. Record everything in
docs/VHK2_FINDINGS.md.

=== TASK 7: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md; docs/VERIFY_RULES.md gains: "never freeze a count or
list of live rows, pages, fields, scripts or callers that a later sprint may
extend — assert the rule for each current one"; add verify_vhk2.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_vhk2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. No network, no npm.
It: (1) statically scans every regression-set script for frozen counts /
lists of live state (the 1f patterns) — none remain, or each remaining one
is listed in VHK2_FINDINGS.md with why it is a true invariant; (2) runs, as
synchronous subprocesses with VERIFY_NESTED=1 and a 1800 s timeout each,
the FULL scripts that failed on 2026-11-02 — verify_authz1, verify_
navregroup, verify_dealroom1, verify_bulkfetchui, verify_bulkfetchui3,
verify_edgarpipelinea, verify_rep1, verify_rep5, verify_lotmethod,
verify_marketdata1 — each → 0 FAIL (one at a time; before bulkfetchui, the
laptop bulk-worker check as run_regressions.sh does); (3) every Task 2 code
change proven both ways (the role refused / admitted on the identical
request; the nav href hidden / shown); (4) verify_dealroom1 --cleanup-only →
zero fixture rows. With VERIFY_NESTED=1 it skips (2)–(4)'s subprocesses
with one '[SKIP] nested run' line. Support --self-checks-only and
--cleanup-only.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top of the file.
  - A failing check's detail prints the script, line and output.
  - Never touch real rows; a subprocess's own teardown is checked after it.

Assertions:
  [Y] Task 1 findings 1a–1f (every failure classed stale vs real, evidence)
  [Y] Real defects fixed, both ways
  [Y] No frozen count / list of live state remains (or is a listed
      invariant)
  [Y] dealroom1 teardown works; leftover rows removed
  [Y] rep10-related three clear
  [Y] --self-checks-only in every regression-set script → 0 FAIL
  [Y] The ten previously failing scripts → 0 FAIL in full
