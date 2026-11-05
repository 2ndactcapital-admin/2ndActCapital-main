# Verify rules

Rules every `apps/api/scripts/verify_*.py` follows, beyond the "Verify Script
Discipline" section of `CLAUDE.md` and the verification template in
`docs/SPRINT_WORKFLOW_STANDARD.md`. Each rule below exists because the lack of
it broke a real script. `apps/api/scripts/verify_vhk1.py` checks the
regression set (`scripts/regression_set.txt`) against them.

## Self-checks end at the sprint's own merge; empty real data is a FIND; dates at the call; fast scoped cleanup; --self-checks-only and --cleanup-only in every script (VHK1, 2026-11-02)

In two weeks, nine fix rounds went to checks that failed because a **later**
sprint added routes or edited an older verify script, or because test data was
cleaned up. The checked feature never broke. Examples: verify_rep6's "31 new
routes" (broken by rep7, rep7b, lotmethod, dealroom3 and rep8), verify_rep7's
route checks, the changed-line guards on verify_rep5 / verify_rep7,
verify_rep7b's "existing accounts" checks, and a `TODAY` fixed at script start
that went wrong on a run past midnight.

1. **Self-checks end at the sprint's own merge.** A check about what a sprint
   did compares its `BEFORE_REF` with **the sprint's own end point**, never
   the working tree. That covers its new routes, the existing gates it left
   alone, the lines it changed in another file, and the files it kept
   byte-identical. The end point is the sprint's own branch tip (its merge
   commit's **second parent**; see rule 9), or `HEAD` while the sprint is
   still on its branch. Use
   `apps/api/scripts/_verify_self.py`:
   - `VS.sprint_end_ref(key)`: the end point, and its description for the
     check's detail.
   - `VS.own_routes(BEFORE_REF, key)`: `.new`, `.changed` and `.removed`,
     before → end.
   - `VS.still_gated(R)`: each of the sprint's routes exists **today** with
     the same gate. This is the one part that reads the working tree on
     purpose.
   - `VS.own_lines(path, BEFORE_REF, key)` and `VS.own_diff(...)`: changed-line
     and diff guards over other files, before → end. A later sprint may edit
     that file freely. Its own guard covers its own edits.
   - Never compare against `main:` either. It moves with every merge.
   - A new sprint adds its merge to `VS.SPRINT_MERGES` when it merges. Until
     then the lookup finds `merge <key>` by subject, and an unmerged sprint
     ends at HEAD.
2. **Empty real data is a FIND.** Some rules hold over real rows: existing
   accounts, existing instructions, held-away drafts. Use
   `VS.when_empty(rows, label, rule, check=check, find=find)`. It reports a
   `[FIND]` ("none exist today") when there are no rows. When rows exist, it
   asserts the rule for **every** row. Fixtures prove the rule itself.
3. **Dates at the call.** No module-level `TODAY` / `NOW` / `VAL` / `YEAR`.
   If an expectation is compared with something the server computes from its
   own clock, compute the date at the check (`today()`). Fixture dates, and
   expectations derived from a fixture's stored date, use a date captured
   when the fixtures are seeded.
4. **Fast scoped cleanup.** Teardown and pre-run cleanup go through
   `_verify_cleanup.cleanup`. It is keyed by the script's own fixture ids and
   runs as one transaction, with an FK closure that only follows children
   holding rows. Immutability GUCs are passed as `gucs=`. Never write a
   recursive `delete_tree` walk or an unconditional TRUNCATE.
5. **`--self-checks-only` and `--cleanup-only` in every script.**
   - `--self-checks-only` runs the git / route / changed-line self-checks
     only: stdlib and git, no Doppler, no database, no network, no npm. It
     prints `TOTAL: N PASS, M FAIL` in seconds. The dispatch sits **before**
     `hydrate()` and every heavy import:
     `if __name__ == "__main__" and VS.requested(): sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))`.
   - `--cleanup-only` runs the teardown and the leftover check, then prints
     `CLEANUP_SECONDS`, `LEFTOVERS` and `TOTAL`.
   - `authz1_routes.py --check` (routes vs the docs, both from the working
     tree) is not a since-base check. It stays in the full run.

Proof that later sprints no longer break the set: verify_vhk1 makes a
throwaway commit in a temporary `git worktree`. The commit adds a fake route
and edits verify_rep5.py. Every script's `--self-checks-only` must still
report 0 FAIL from there. The record is in `docs/VHK1_SELF_CHECKS.md`.

## Never freeze a count or list of live rows, pages, fields, scripts or callers that a later sprint may extend — assert the rule for each current one (VHK2, 2026-11-03)

The full regression run of 2026-11-02 (25 scripts, 11 with failures) found
checks that compared against the CURRENT database, page tree or file lists,
frozen at the sprint that wrote them: verify_authz1's "34 effective
permissions" (59 after later sprints granted org_admin more), verify_navregroup's
page table and "intended additions" list, verify_bulkfetchui3's "the 11
scripts", verify_edgarpipelinea's "only Edgar*.jsx pass serverSide", verify_rep1's
critical-field list (rep8 added option fields), a ">= 5 issuer groups" real
sample. Each feature still worked; the snapshot did not.

6. **Never freeze a count or list of live rows, pages, fields, scripts or
   callers that a later sprint may extend — assert the rule for each current
   one.** "Every user's effective permissions == the recompute from their
   role grants", "every page under app/admin/** has a nav row and a traced
   gate", "every critical field rep1 seeded is still critical", "the scripts
   this sprint added are still listed". A list of the sprint's OWN fixture
   items, or of its own routes at its own merge (rule 1), is not live state
   and may stay literal. A real-data sample smaller than the rule wants is a
   `[FIND]` (rule 2), never a FAIL and never a lowered bar.
7. **A timing check states its load rule.** Measure, and when the machine is
   busy (`/proc/loadavg` 1-min load above the CPU count) wait and retry,
   reporting the load with every attempt. Never raise the limit.
8. **A teardown over an append-only table** passes that table's own
   immutability GUC to `_verify_cleanup.cleanup(gucs=...)` — the way the
   sprint that made it append-only tears down its own rows — scoped by the
   script's fixture ids.

9. **A sprint's own change set = its merge's second parent against the merge
   base** — `git diff <merge>^1...<merge>^2` (three-dot), i.e.
   `merge-base(<merge>^1, <merge>^2)..<merge>^2`. Never the merge commit
   itself, and never first-parent history: when another sprint merged into
   main while this one was on its branch, the merge commit's tree carries that
   sprint's work too. (VHK2 FIX 1, 2026-11-03: aibudget1 merged before vhk2,
   so "265f15c → merge vhk2" listed aibudget1's `routers/ai_ensembles.py`,
   `AUTHZ1_ROUTES.json` and `menuVisibility.mjs` as VHK2's — three false FAILs.)
   `VS.sprint_end_ref` returns the second parent; `VS.sprint_base_ref` the
   merge base; `VS.own_base_ok` requires `BEFORE_REF` to lie between them, and
   `own_routes` / `own_lines` / `own_diff` raise when it does not. Never take
   "main at the merge" as `<merge>^1` — use `VS.sprint_base_ref`.

`apps/api/scripts/verify_vhk2.py` scans the regression set for these
patterns; each remaining hit is a listed invariant in `docs/VHK2_FINDINGS.md`.

## Time the server, not the operator's network (BUGFIX2, 2026-11-03)

A latency check asserts the time the SERVER spent, never the end-to-end time
measured from the machine running the verify. verify_bulkfetchui's "live
preview returns under 2 s" timed the laptop's round trips to the database:
2.07–2.2 s end to end from a laptop while the server's own work took
151–598 ms, and production runs next to the database. The fix is NOT a raised
limit:

1. The endpoint reports its own `elapsed_ms` (measured inside the handler).
2. The check asserts that server-side number under the SAME limit, best of 3.
3. The end-to-end time is printed as `INFO` (not asserted) with the number of
   database round trips the endpoint makes, so a reader can see what the
   operator's network added.

Never raise a limit to absorb the operator's network, and never assert a
wall-clock number that includes it.

## Merges onto main use `--no-ff`; a fast-forwarded sprint ends at its own tip (PORTENTRY1, 2026-11-04)

Every sprint branch is merged onto main with `git merge --no-ff <branch>`, so
it has a merge commit and `VS.sprint_end_ref` finds its end as that merge's
second parent. perms1 reached main as a FAST-FORWARD (no merge commit; its
commits sit on main's first-parent line, ending at 3fe2cef), and the lookup
fell through to HEAD: `verify_perms1 --self-checks-only` failed with
"perms1: base … NOT an ancestor of 01c0f57" although nothing perms1 built had
changed. secmaster1's merge subject was git's own `Merge branch
'secmaster1-work'`, which the `merge <key>` pattern did not match — the same
fall-through.

`_verify_self` now:

1. recognises `<key>-work` in a merge subject (`Merge branch 'secmaster1-work'`);
2. for a sprint with no merge commit, takes its end as the NEWEST commit on
   main's first-parent line whose subject names the key (`<key>: …`,
   `<key> fix 1: …`, `sprint: <key>.…`) and its base as the parent of the
   OLDEST one — or the explicit `SPRINT_FASTFORWARD` entry (perms1:
   3fe2cef / 01c0f57). A merged branch's own commits are never on the
   first-parent line, so a merged sprint is never mistaken for a
   fast-forwarded one.

Still prefer `--no-ff`: the first-parent fallback cannot tell two sprints'
interleaved fast-forwarded commits apart.
