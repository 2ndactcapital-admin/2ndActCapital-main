VISIBILITY1 FIX 1 — make verify_visibility1.py pass, and stop the nightly
relink from re-attaching digital assets to ETFs. 7 FAILs + 1 live-data
defect found while reading them. 4 tasks. Branch visibility1-work. Decide
code vs verify with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_visibility1 run, 2026-11-07
06:19–07:03 UTC: TOTAL 201 PASS, 7 FAIL; the rule, closure, owner rule, RM
rule, scrub, RLS, isolation, the 11 new routes and their gates, node tests and
npm run build all PASSED — keep them):

A. "[FAIL] a manager does not inherit a report's relationship-managed /
   created families". The check compares scope_from_graph(g4, "M") with `m`,
   M's scope on the ORIGINAL graph. But g4 also carries assignment a3 (F3 /
   p3 assigned to S1), and S1 reports to M — so M now legitimately sees F3
   through his report's ASSIGNMENT (Joe's rule: assigned ∪ team ∪ reports).
   The difference is the assignment, not the RM change. Likely VERIFY defect:
   compare M on g4 with M on g3 (same assignments, only the RM differs), AND
   add the case the label claims: a family where M's report S1 is ONLY the
   relationship manager / creator (no assignment to S1) — M must not see it.
   Confirm with evidence (print both sets); if M does see an RM-only family of
   a report, that is a CODE defect.
B–D. "[FAIL] CRM list / households / positions: enforcement adds at most ONE
   statement (the graph, read once) — off (200, 11, 0) on (200, 16, 1)".
   Enforcement adds FIVE statements per request on each route, not one. List
   the 5 (statement text) and remove the extra ones: the visible set is
   computed ONCE per request (request state), the org switch rides on the
   Principal's statement (perms1 / previewperf budget), and the route adds one
   filter. CODE unless the evidence shows the budget assertion itself is
   counting statements the sprint did not add (then show exactly which).
E. "[FAIL] the only existing gates changed are the teams / assignments routes"
   — the culprit is POST /api/v1/approvals/bulk: its route record gained a
   second gate any(view_authority|approve_authority|view_held_away|
   approve_held_away), because decide() now calls BA.pending(), which checks
   the READ permissions. The teams / assignments routes in the list are the
   allowed ones. CODE: the visibility filter on the bulk decision must not add
   or change a gate — fetch the pending items through an internal, ungated
   helper (the caller already passed APPROVE_KEYS); the route record for POST
   /approvals/bulk returns to exactly its pre-sprint gate; keep the 404 for an
   item of a family the caller cannot see.
F. "[FAIL] no network call was attempted — three POSTs to
   https://hollisworks-litellm.onrender.com/v1/messages". Find which request
   in the verify reached an LLM call (stack / the route and service that
   called it — note extraction, TaskRouter, a notification summary?). The
   verify must make no network call: stub that client at its CURRENT
   signature for the run. If a sprint route calls an LLM where it should not
   (a visibility read must never), CODE.
G. Nested verify_staffroles1.py: 140 PASS, 1 FAIL — "public.audit_log at its
   pre-test count — 778 → 780". The 2 extra rows are REAL, not fixtures:
   action 'manual_asset_relinked', org 2nd Act, user_id NULL, at 07:00:56 UTC
   — the NIGHTLY portfolio refresh (services/portfolio_refresh.py:243–257,
   trigger 'nightly') ran during the nested verify. VERIFY defect in
   verify_staffroles1.py: a whole-table count on a table real activity writes
   to (docs/VERIFY_RULES.md). Count audit_log rows by the fixture orgs /
   fixture resource ids instead (and say which other verifies use the same
   whole-table audit_log count — list them; fix only staffroles1 here).
H. LIVE-DATA DEFECT found from G (CODE + operator repair):
   relink_manual_assets (services/portfolio_manual.py ~line 93) relinks every
   manual asset whose identifier resolves in the master, excluding only
   asset_type 'cash'. At 07:00:56 UTC the nightly relinked the Olivo demo's
   DIGITAL ASSETS "Bitcoin" (6426ce89-6f99-415b-8c1f-5aa78c8a40ee, ticker BTC)
   → "Grayscale Bitcoin Mini Trust (BTC)" (b3e02657…) and "Ether"
   (abf477fc-78c9-4b5f-8e80-f0a44bf8e323, ticker ETH) → "Grayscale Ethereum
   Staking Mini ETF" (d7719ee0…) — exactly what bugfix4 forbade ("cash /
   digital assets never resolve through the master") and what Muse fixed by
   hand in fix-up v5 F.3 hours earlier. Fix: relink skips every kind that
   never resolves through the master — the SAME list bugfix4's entry form uses
   (one shared constant: cash, cash_holding, money-market sweep / cash
   lines, digital_asset, and any other bugfix4 names); a digital asset's
   ticker is not a security ticker. Add scripts/revert_relinks.py: --org,
   --dry-run lists every asset_relinks row whose asset kind is in that list
   (asset, from / to security, when, trigger), the real run unlinks each as a
   new system version (id kept, prior version archived, audited,
   idempotent — CHANGED: 0 on a second run). The sprint runs ONLY --dry-run
   against the live org (read-only) and records the list (expect exactly the
   two above).
- WSL memory is limited: do NOT run verify_visibility1.py,
  verify_staffroles1.py or npm run build; --self-checks-only and one-off
  scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
demo-olivo or real row (revert_relinks.py: dry run only). Commit on
visibility1-work with message "visibility1 fix 1: <one line per fix, code vs
verify>".

=== TASK 1: DISCOVER === A–H: cause and evidence (A: both sets; B–D: the 5
statements; E: the route record before / after; F: the caller; G: the 2 rows;
H: the shared kind list and the dry-run list).
=== TASK 2: FIX === code and verify per the evidence; verify_visibility1
gains checks for H (a fixture digital asset with ticker BTC and a fixture
cash_holding are NOT relinked by relink_manual_assets; a fixture equity still
is; revert_relinks dry run / real / CHANGED: 0 on a fixture org).
=== TASK 3: PROVE === one-off on fixtures: A's two cases; the statement count
on the three routes off → on adds at most one; POST /approvals/bulk route
record identical to main's (authz1_routes.py --check CURRENT); no network
call with the stub; staffroles1's audit_log check counts only fixture rows;
H's checks. verify_visibility1 --self-checks-only and verify_staffroles1
--self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under visibility1, and
the operator step: revert_relinks.py --org <2nd Act> --dry-run, then the
real run, then household Recalculate.
