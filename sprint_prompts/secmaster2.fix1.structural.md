SECMASTER2 FIX 1 — make verify_secmaster2.py pass. 8 FAILs, all from ONE
cause in the verify fixtures (and the same cause in the nested
verify_secmaster1.py): the fixture Super Admin / job runner is not a Super
Admin under the ONE role source bugfix4 introduced. 4 tasks. Branch
secmaster2-work. Decide code vs verify with evidence; never weaken an
assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_secmaster2 run, 2026-11-07
~06:50 ET: TOTAL 126 PASS, 8 FAIL; everything not listed below PASSED — keep
it):
- The 8 FAILs:
  1. GET /summary as Super Admin → 403 {"detail": "Super Admin required"}
  2. GET /securities?fund_structure=money_market → 403
  3. unknown filter value (expected 422) → 403 "Super Admin required"
  4. a 404 HOLDS the attributes run → WorkflowEngineError: Service Task
     'attributes' invokes action 'security_master.attributes', which requires
     permission 'run_platform_jobs'. The member who started this run
     (99000000-0000-0000-0000-00000054d101) does not h[ave it]
  5. POST …/runs/{id}/close as Super Admin → 403 "Super Admin required"
  6. …the trigger fires again → fired=0 skipped=1 (follows from 4 / 5)
  7. the mutual-fund job runs through the engine → errors=1 (same runner)
  8. nested verify_secmaster1.py (own final TOTAL): 121 PASS, 17 FAIL —
     "library shows no Delete for a platform job — 403", "API refuses to
     delete a platform job (409) — 403 Permission required:
     author_workflows", "system:Security master — nightly … errors=1",
     "… requires permission 'run_platform_jobs'. The member who started this
     run (99000000-0000-0000-0000-00000053c101) does n[ot have it]" …
- Cause: since bugfix4 (merged 2026-11-06), EVERY role, Super Admin
  included, is decided from user_roles; users.role is only a mirror.
  verify_secmaster2.py (USERS line ~205) and verify_secmaster1.py (USERS line
  ~154) seed their fixture users with the role STRING only — no user_roles
  row — so the fixture "Super Admin" and the fixture platform-job runner are
  neither Super Admin nor holders of run_platform_jobs / author_workflows.
  verify_secmaster1 last passed BEFORE bugfix4; nothing in secmaster2's code
  caused its 17 FAILs. Verifies written after bugfix4 (rollup2:
  `INSERT INTO user_roles (user_id, role_id) …` with ROLE_IDS, teardown of
  public.user_roles by fixture user) already seed correctly.
- The REAL platform runner is fine: be8165db… (the Hollisworks platform
  org's only user) holds super_admin in user_roles, so real platform jobs are
  unaffected. Confirm this in Task 1 (read-only), do not change it.
- VERIFY defect in both scripts. Do:
    (a) Seed fixture roles the way the app decides them: a user_roles row per
        fixture user (and the platform-org runner) through the same helper /
        pattern rollup2 and bugfix4 use, plus the users.role mirror; teardown
        public.user_roles by fixture user id.
    (b) Re-check each of the 8 FAILs' expectations after (a); if any still
        fails for a DIFFERENT reason, that is new evidence — find its cause
        and fix code or verify accordingly (say which).
    (c) List every other verify script that seeds a Super Admin / org admin
        by the role string only (read-only grep; do not fix them here) —
        they will break the same way when next run; record the list for the
        catch-all backlog.
- WSL memory is limited: do NOT run verify_secmaster2.py,
  verify_secmaster1.py or npm run build; --self-checks-only and one-off
  scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real row (the real users and their user_roles are read-only here). Commit on
secmaster2-work with message "secmaster2 fix 1: <one line per fix, code vs
verify>".

=== TASK 1: DISCOVER === the fixture seeding in both scripts; the role
resolver (rbac.is_super_admin / the Principal) on a fixture with and without
a user_roles row; the real runner's user_roles (read-only); the list (c).
=== TASK 2: FIX === (a) in both scripts; (b) per evidence.
=== TASK 3: PROVE === one-off on fixtures: the fixture Super Admin gets 200
on GET /summary and /securities, 422 on an unknown filter, closes a held run;
the attributes run HOLDS on a 404 fixture with its reason; the trigger fires
again and completes; the mutual-fund job completes through the engine; the
secmaster1 platform-job delete is refused 409 and its nightly completes.
verify_secmaster2 --self-checks-only and verify_secmaster1
--self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under secmaster2, with
the (c) list.
