CASH07 FIX 1 — make verify_cash07.py pass. The seed crashed on an RLS
refusal inserting an org SoD rule, so EVERY fixture section (lifecycle,
approval (the floor), authority, rails + value dates, adapters,
reconciliation, disclosures, visibility + isolation, permissions + autonomy)
was gated off. Fix the seed, then run every section one-off and fix whatever
it finds — code or verify — this sprint. This is the money-movement sprint:
most of its real proof has NOT run yet; expect real findings. 4 tasks.
Branch cash07-work. Never weaken an assertion — above all the floor (no
self-approval for money movement, Super Admin included).

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_cash07 run, 2026-11-09 ~21:15–
21:30 UTC). FIRST read the end of sprint_prompts/logs/cash07_run.out: add any
FAIL after line 197 (incl. the nested verify_cash05.py / verify_s30.py) to
this fix with the same code-vs-verify rule.
- FAIL 11 (line 287, final TOTAL 114 PASS, 11 FAIL; verify_s30.py PASSED):
  nested verify_cash05.py 224 PASS, 1 FAIL — "cash.sweep_rate is registered
  with its fallback and shown ONLY on a member's overview (CASH07), in no
  other cash05 response — member overviews 2". This check was added /
  changed by THIS sprint (the CASH07 marker). Print what it counted (which
  responses, which members) and expected; decide whether the two member
  overviews are two legitimate fixture members (then the check's count is
  wrong — VERIFY) or the key leaks into a second / non-member response
  (CODE). Prove verify_cash05 one-off on that section → PASS.
- FAIL 1 (line 155) "seed raised — InsufficientPrivilegeError: new row
  violates row-level security policy for table "sod_rules"" at
  verify_cash07.py:922: the seed loops SEED_ROWS["public.sod_rules"] and
  INSERTs each with org_id = ORG_A on the seed connection `conn` (seed() at
  :864, inside conn.transaction()). sod_rules' RLS refuses it on that
  connection. verify_s30.py shows the two legal ways: an org rule is written
  by the app's own service (services/sod_admin.py — draft → a DIFFERENT
  admin approves; S30 maker-checker on rules), or by a fixture-privileged
  path with the RLS context set ON the connection inside the transaction
  (set_config('app.current_org_id', ORG_A, true) and the role the policy
  requires — read the policy from pg_policies). Decide with evidence:
    (a) VERIFY defect (expected): seed the org rule the way verify_s30 does
        (operator_conn() / the privileged fixture path, or the sod_admin
        service with two fixture admins), never by loosening the policy;
    (b) only if the policy refuses a path S30 documents as allowed → CODE,
        state which, with the policy text.
  Also check what the seeded org rule is FOR: per the cash07 prompt, the
  floor must hold "for an org that turned self-approval on for other item
  types" — the seeded rule must be exactly that (self-approval allowed for a
  NON-money item type), and it must not touch the payment_instruction item
  type (the floor forbids it — an attempt to relax it must be refused, which
  is itself a check).
- FAILs 2–10 "<section>: not run — the seed failed" — follow from FAIL 1.
  After the fix, EVERY one must run; run each one-off on fixtures until it
  passes and list them with their PASS counts. Any real defect found
  (lifecycle trigger, floor gate, authority checks, adapter, reconcile,
  ACH provisional / returns, number leaks) is fixed here and named CODE.
- Fixture "account numbers": the seed writes FULL_NUMBERS[key] into
  accounts.custodian_account_id — they must be obviously fake (reserved
  prefix, never a real account) and the "no full account number anywhere"
  scan must still pass on every response, sheet and audit row (it scans for
  exactly these values).
- WSL memory is limited: do NOT run the full verify_cash07.py,
  verify_cash05.py, verify_s30.py or npm run build. --self-checks-only,
  --cleanup-only and one-off sections MAY be run (`timeout 600` each; the
  runner kills a leg at 90 minutes — budget: if more than 4 sections need
  code fixes, commit after each one so nothing is lost).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret or a full account number.
Fixtures only; never write a real row; never call a real custodian; never
relax an RLS policy or the floor. Commit on cash07-work with message
"cash07 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === the sod_rules RLS policy; how verify_s30 seeds org
rules; what the seeded rule is for; any extra FAILs from the end of the log.
=== TASK 2: FIX === the seed; then each section's findings.
=== TASK 3: PROVE === seed one-off → completes; each of the nine sections
one-off → PASS (list each with PASS count); the floor holds for maker,
Super Admin and an org with self-approval on elsewhere; --cleanup-only →
zero fixture rows (payment_events with its teardown GUC); verify_cash07
--self-checks-only → 0 FAIL; real rows unchanged.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under cash07 listing
every fix (code vs verify).
