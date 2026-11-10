CASH06 FIX 1 — make verify_cash06.py pass. The seed's role check failed, so
the five fixture sections (validation, governance (the floor), engine,
instructions (nothing sent), visibility + isolation) were gated off and never
ran. Fix the seed, then run every section one-off and fix whatever it finds —
code or verify — this sprint. Most of cash06's real proof has NOT run yet;
expect real findings. 4 tasks. Branch cash06-work. Never weaken an assertion
— above all the money-movement floor and "a rule never approves or sends".

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_cash06 run, 2026-11-10
~01:40 UTC; the node render tests (8) and the static / schema checks before
line 208 PASSED — keep them). FIRST read the end of
sprint_prompts/logs/cash06_run.out: add any FAIL after line 213 (incl. the
nested verify_cash07.py / verify_s29b.py) to this fix with the same rule.
- FAIL 1 (line 208) "every fixture role is a user_roles grant the app
  decides as meant (… workflow admins hold the S29b settings …)" — culprits:
  [('adm1', ['workflow_admin'], ['manage_org_settings', 'view_portfolio'],
  False), ('adm2', … same …)]. The verify's PERMS["workflow_admin"]
  (verify_cash06.py ~:277) expects {view_portfolio, manage_org_settings,
  author_workflows, view_workflow_runs, configure_workflow_triggers}; the app
  decides only manage_org_settings + view_portfolio. So author_workflows,
  view_workflow_runs and configure_workflow_triggers were NOT granted by the
  way the verify seeds the role. Precedent: wftemplates1 fix 1 /
  verify_tiergating — "the maker's view_workflow_runs comes from its
  PROFILE (SOC phase 1 profiles / permission sets), not a role". Decide with
  evidence:
    (a) VERIFY (expected): those three are profile / permission-set
        permissions; seed them the way verify_tiergating does
        (user_permission_sets with the REAL columns — s29b fix 1: no org_id —
        and a fixture permission set), never by inventing a role string;
    (b) CODE only if the permission catalogue says a role CAN carry them and
        rbac drops them — then name the resolver defect and every route
        affected.
  Print, per fixture user: roles granted, permission sets granted, the
  permissions the app decides (Principal), expected — and make the check's
  detail show only mismatches.
- FAILs 2–6 "<section>: not run — the seed failed" — follow from FAIL 1.
  After the fix, EVERY one must run; run each one-off on fixtures until it
  passes and list them with their PASS counts. Any real defect (evaluator
  rounding / holds, engine idempotency / caps / kill switch, S29b refusing
  approve / send from the rule path, the cash_rule floor, owner ≠ checker on
  drafted instructions, the write-scope pattern below) is fixed here and
  named CODE.
- Known cross-cutting defect (CATCHALL_BACKLOG, found by cash03 fix 1 and
  cash07 fix 1): authz.member_entity_scope on a WRITE route scopes an
  internal non-staff role (advisor) to an EMPTY set → 404 on its own
  family. Check every route cash06 added (rules create / edit / activate /
  pause / retire / preview): if one uses member_entity_scope on a write,
  fix it with the same VISIBILITY1 pattern routers/payments.py now uses
  (cash07 fix 1) — reuse that code path, do not copy a third variant.
- WSL memory is limited: do NOT run the full verify_cash06.py,
  verify_cash07.py, verify_s29b.py or npm run build. --self-checks-only,
  --cleanup-only and one-off sections MAY be run (`timeout 600` each; the
  runner kills a leg at 90 minutes — COMMIT after each finished section,
  message "cash06 fix 1 (wip): <section>").

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret or a full account number.
Fixtures only; never write a real row; never call a real custodian; never
relax an RLS policy, the floor or an S29b class. Final commit on cash06-work
with message "cash06 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === per-user roles / sets / decided permissions vs
expected; the permission catalogue for the three missing permissions; the
write-scope pattern in cash06's routes; any extra FAILs from the end of the
log.
=== TASK 2: FIX === the seed; then each section's findings.
=== TASK 3: PROVE === the role check → PASS; each of the five sections
one-off → PASS (list each with PASS count); an advisor-type fixture can
create a rule on a visible family, 404 on an invisible one; a firing drafts
+ submits only, nothing sent; --cleanup-only → zero fixture rows;
verify_cash06 --self-checks-only → 0 FAIL; real rows unchanged.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under cash06 listing
every fix (code vs verify).
