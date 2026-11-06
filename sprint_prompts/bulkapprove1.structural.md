BULKAPPROVE1 — a bulk approval screen for maker-checker items, starting with
ACCOUNT AUTHORITY changes (today ~5 clicks per approval, one item at a time)
and HELD-AWAY manual entries / imports (the Olivo onboarding creates dozens of
each). Same rules as the single approval — only fewer clicks. 6 tasks +
verification.

Context: Joe (2026-11-05): "account authority approval needs a 'bulk'
screen — it's 5 clicks per to approve." HollisMuse is onboarding the Olivo
demo family: 34 accounts, many with non-default authority (rep7b: a
non-default authority = a maker-checker draft) and ~25 held-away drafts, each
approved by a second person.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo; Task 1 confirms signatures):
- rep7b: account AUTHORITY (read_only | advise | managed) and CUSTODY are
  household settings overridable per account; a non-default setting is a
  DRAFT approved by a second person (maker ≠ checker) on a governed human task
  (services/human_tasks.py); tables household_authority and
  account_authority_settings (immutability trigger — rows never deleted;
  fixtures use the documented execution.teardown = 'fixture' path).
- rep7: held-away manual entries and imports (held_away_imports: status,
  maker / approved_by, approval_task_id, decided_at, decision_note) — also
  maker-checker; portentry1 extended manual entry to every asset kind;
  portentry2 let entities own accounts.
- Approvals today open one item, its task, then confirm — no list action.
- perms1: the Principal = role ∪ profile ∪ permission-set grants; the
  approve_* keys (approve_authority, …) gate approvals; super admin may
  approve; maker ≠ checker is enforced in the DB for some tables (CHECK).
- Regression: this adds a second caller of the authority approval path →
  re-run verify_rep7b.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- A HollisMuse pass may be writing Olivo data in production during this
  sprint: never touch demo-olivo or real rows; fixtures only.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret or a full account number. NO new
write path: bulk approve / reject calls the SAME service function the single
approval uses, once per item, so every rule (maker ≠ checker, permission,
state, immutability, audit) applies unchanged. Each item is its own decision
by its own approver — never one decision for many. Light/cream UI with the
existing tokens; labels and vocabularies from the API envelope. Commit ALL
sprint files (excluding sprint_prompts/logs) on the current branch at the end
with message "bulkapprove1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. The single-approval path for authority / custody drafts and for
      held-away entries / imports: routes, service functions, human-task
      transitions, permission keys, where maker ≠ checker is enforced.
  1b. Where pending items are listed today (task inbox, account page) and
      the ~5 clicks.
  1c. Other maker-checker types that could reuse the screen later (models,
      proposals, policies, integrated import) — list only; build none.

=== TASK 2: THE SCREEN ===
Approvals → **Bulk approve** (tabs: Authority & custody | Held-away entries):
a table of PENDING items the viewer may decide — household, account (mask),
change (from → to, with the reason), maker, requested at; filters
(household, maker, type, age); select one / select all (on the filtered set);
items the viewer cannot approve are shown greyed with the reason (you made
it; no permission; no longer pending). Actions: Approve selected, Reject
selected (a note required for reject). One confirmation step that names the
count and lists the items. Held-away rows expand to show their lines and
totals before approving.

=== TASK 3: THE API ===
POST /api/v1/approvals/bulk {type, item_ids, decision, note}: for each item
calls the single-approval service in its own transaction; returns per-item
results (approved / rejected / refused + reason); one failure never undoes the
others; a hard cap per request (e.g. 200). Gated by the same permission as the
single approval for that type, checked per item. Audited per item and once for
the batch (who, how many, ids).

=== TASK 4: ENTRY POINTS ===
A "N pending — review in bulk" link from the household page, the account
list and the task inbox when more than one item is pending.

=== TASK 5: REAL PROOF ===
  - Fixture: 6 authority drafts + 3 held-away entries by maker M; checker C
    bulk-approves 5 authority drafts in ONE request → 5 approved, each with
    C as approver, its own human task closed, its own audit row; the settings
    take effect exactly as a single approval would (compare with one item
    approved through the single path).
  - M (the maker) selecting their own items: refused per item ("you made
    this"); nothing changed.
  - A user without the approval permission: whole request refused; identical
    request admitted for C.
  - Mixed batch: one item already approved / one withdrawn → those two
    refused with reasons, the rest approved.
  - Reject selected requires a note; rejected drafts change nothing.
  - Held-away: bulk approval creates the same positions as single approval
    (row-for-row).
  - Cap enforced; one bad id does not stop the others.
  - Org isolation both directions (rolbypassrls = false): org B's item ids in
    org A's request → refused as not found.
  - Demo and real rows untouched (count + checksum).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_rep7b.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 6: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (the screen, the one-path rule, the 1c candidates);
add verify_bulkapprove1.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_bulkapprove1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (fixture org,
users, households, accounts, authority rows, held-away imports / records,
positions, human tasks, audit rows) using the documented privileged paths for
immutable tables; re-read every touched table for its exact pre-test count.
Fixture orgs and users is_test = true (assert it). Read UNIQUE / CHECK / FK
constraints from the catalog before seeding. Reserved fixture prefixes that
match no real row (assert it). Support --self-checks-only and --cleanup-only.
The regression subprocess gets VERIFY_NESTED=1, after teardown, 2700 s; with
VERIFY_NESTED=1 skip it with one '[SKIP] nested run' line. Parse the nested
script's OWN final '^TOTAL:' line with the shared helper (portentry2 fix 1),
never the first match.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` (and any list / dict value) in bool(...).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read; app_service rolbypassrls = false asserted for
    isolation checks; an empty list never passes a check.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates (users.last_login_at), and name them.
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1c
  [Y] Screen: pending items, filters, select all, greyed with reasons,
      one confirmation
  [Y] API: per-item decisions through the single-approval service; partial
      results; cap; audit per item + batch
  [Y] Maker ≠ checker, permission, state, isolation — per item, identical
      request both ways
  [Y] Bulk result == single-approval result (authority and held-away)
  [Y] Demo / real rows untouched; own routes gated; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep7b.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
