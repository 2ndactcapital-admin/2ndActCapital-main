BUGFIX3 — HollisMuse's Olivo pass (2026-11-05): one CRITICAL member-privacy
leak on the Positions page, plus the Highs and Mediums that block finishing
the demo family. Security first. 8 tasks + verification.

Context: HollisMuse v4 resume pass built the Olivo family (16 people, 26
entities, 34 accounts, 117 relationships, ownership 100%); integrated import
loaded 27 lines / $134,342,790; 56 held-away drafts ($348,681,200) await a
second person; 19 direct assets $157.1M and 7 liabilities $52.8M match the
plan; unfunded $12M verified. CRM member isolation held. Positions did not.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (Muse report + repo + live DB,
2026-11-05):
- CRITICAL: Member A (jpl991721, role member) opens /portfolio/positions and
  sees "Every holding across the firm" — all 23 live positions with owner
  names and values (the Olivo vineyard $28M, Harbor Point $22.3M, …).
  Cause confirmed in code: apps/api/routers/portfolio_positions.py
  GET /portfolio/positions (~:327) calls list_positions(org_id=…) after
  _tenant_gate(request, READ_PERMISSION) — org-scoped only, NO member /
  visibility filter; the member role holds view_portfolio. The same router's
  GET /portfolio/commitments (BUGFIX1) already filters by
  document_embedding.visible_entity_ids(pool, org_id, user_id,
  is_internal(request)) — the rule to reuse. Live DB: all 23 live positions in
  the 2nd Act org are demo-olivo — no real family's holdings were exposed;
  real positions will arrive with the Altruist sync, so this must ship first.
- bugfix2 classified 73 read routes (24 staff-only, 3 staff-or-org-admin, 18
  member-scoped); positions / position detail / assets were evidently not
  covered or regressed. 48 route decorators across apps/api/routers/
  portfolio.py, portfolio_ingest.py, portfolio_positions.py,
  portfolio_securities.py, portfolio_transactions.py (+ held-away, net
  worth, assets & investments, liabilities, rollup) need the same audit.
- HIGH: Add private investment REQUIRES a commitment amount; 7 of 11 Olivo
  private investments (hedge fund, direct deals, SPV, SAFE, convertible note)
  have none by design; entering 0 would compute a negative unfunded.
- HIGH: no household-level Recalculate for the org admin (portentry1 put
  Recalculate on the entity record); reconciliation needs it.
- HIGH: saved addresses cannot be edited — display-only cards, no Edit /
  end control (42 address notes could not be added).
- MEDIUM: the approved integrated import's positions are not visible to the
  org admin as live positions (portentry1 marked them authority 'stated',
  source 'manual', listed in portfolio.provisional_positions).
- MEDIUM: support staff are offered Add Relationship and New Entity
  controls they do not hold (portentry2 fixed ownership only).
- MEDIUM: whether the three Northline (NLF, fictional) lines in A1 / A6 / A8
  share one org-level manual asset or created three duplicates cannot be
  seen by an org admin.
- MEDIUM: listed_option entry has no contract picker (portentry1: an option
  resolves only through an EXISTING rep8 contract).
- LOW: costs under $1M render as scientific notation ("$9.0E…") — a
  str(Decimal) somewhere (BUGFIX1 already fixed one: format(v, "f"));
  private-investment form has no note field; lists render empty until
  re-read after load (the "display lag" seen across screens).
- Regression: member scoping of read routes is bugfix2's subject → re-run
  verify_bugfix2.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- HollisMuse may be idle mid-pass with Olivo data live: never touch
  demo-olivo or real rows; fixtures only.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret or a full account number. Fail
closed: a portfolio read that cannot decide the caller's visible set is
refused. A member's not-found is identical to a missing id. Members see only
their own family (bugfix2) — via the ONE shared visible-entity rule, not a
per-route reimplementation. Every UI control renders only when the API's
permissions envelope allows it. Light/cream UI with the existing tokens.
Commit ALL sprint files (excluding sprint_prompts/logs) on the current branch
at the end with message "bugfix3: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Every portfolio / held-away / assets & investments / net-worth /
      liabilities / rollup / commitments / transactions / securities READ
      route: gate, whether it filters by the caller's visible entities, what
      a member gets today. Table (the bugfix2 format), each marked
      staff-only / member-scoped / already-correct.
  1b. Why bugfix2's verify did not catch /portfolio/positions (no fixture
      position for another family? route not in its list?).
  1c. Provisional positions: why approved import rows are not shown as live.
  1d. Every place a Decimal reaches the UI via str() (the $9.0E class).
  1e. The "display lag": which list components render before their data
      (or need a second fetch) — the common cause.

=== TASK 2: THE LEAK (CRITICAL) ===
Every 1a route a member can reach filters to visible_entity_ids (the
commitments rule) — list, detail (not-found when outside), totals, exports,
filters' value lists, asset lists, taxonomy rollups; staff-only routes
refuse members. The Positions page title / text for a member reads as their
own holdings, never "across the firm". A member-scoped position detail of
another family = not-found identical to a missing id.

=== TASK 3: PRIVATE INVESTMENTS WITHOUT A COMMITMENT (HIGH) ===
Commitment fields required only for types that have one (fund; optional for
SPV); hedge fund / direct deal / SAFE / convertible note: amount invested and
latest value, no commitment, unfunded not computed (shown "—"); a note field.

=== TASK 4: HOUSEHOLD RECALCULATE AND LIVE IMPORT ROWS (HIGH / MEDIUM) ===
Recalculate on the household (internal + manage_members | manage_portfolio),
running the family-scoped rollup; approved provisional (import) positions show
in the Positions grid and the household as live, labelled "provisional —
until the custodian feed".

=== TASK 5: ADDRESSES EDITABLE (HIGH) ===
Edit and end (valid_to, history kept) on each saved address, for
edit_crm_contact and manage_members; seasonal fields and note editable.

=== TASK 6: SMALLER FIXES (MEDIUM / LOW) ===
Permission-true UI for Add Relationship, New Entity and every CRM create /
edit control (support staff sees only what its grants allow); org admin can
list the org's manual assets with how many positions use each (answers the
Northline question; if the three lines made three assets, report it — do not
merge real / demo data, add a merge action for staff that is maker-checker,
build only if 1a shows it is small, else name the gap); listed_option entry
offers a picker of existing rep8 contracts for the underlying, or says none
exist; Decimals rendered fixed-point everywhere (1d); the 1e list-render fix.

=== TASK 7: REAL PROOF ===
  - BEFORE the fix (fixture org, two families F1 / F2, member of F1):
    GET /portfolio/positions as the member returns F2's positions (reproduce);
    AFTER: only F1's; F2's position detail → not-found identical to a missing
    id; totals / exports / asset list / filter vocabularies contain no F2
    value (seed a distinctive F2 asset name and prove it never appears).
  - Every 1a member-reachable route: member gets own family only; staff-only
    routes refuse the member — identical request admitted for org admin.
  - Private investment without commitment saves; unfunded "—"; a fund still
    requires its commitment; note round-trips.
  - Household Recalculate updates net worth; approved import rows appear as
    live positions labelled provisional.
  - Address edit / end with history; support staff (edit_crm_contact)
    admitted, member refused.
  - Support staff sees no control it cannot use (envelope false) and the
    server refuses the action (identical request admitted for org admin).
  - A $900,000 cost renders "900,000.00", never scientific notation.
  - Demo and real rows untouched (count + checksum); org isolation both
    directions (rolbypassrls = false).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_bugfix2.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s (parse its OWN final ^TOTAL line).

=== TASK 8: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (the 1a table, the leak and its fix, each Muse finding
fixed / not with its ID); add verify_bugfix3.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_bugfix3.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (fixture org,
users, entities, households, accounts, assets, positions, valuations,
commitments, addresses, provisional rows, entity_holdings, rollup requests),
using documented privileged paths for immutable tables; re-read every touched
table for its exact pre-test count. Fixture orgs and users is_test = true
(assert it). Read UNIQUE / CHECK / FK constraints from the catalog before
seeding. Reserved fixture prefixes that match no real row (assert it). Support
--self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

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
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1e and the route table
  [Y] The leak reproduced, then closed on every member-reachable route
  [Y] Private investments without commitment; note
  [Y] Household Recalculate; import rows live (provisional)
  [Y] Addresses editable with history
  [Y] Permission-true CRM controls; manual-asset list; option picker;
      fixed-point money; list render
  [Y] Demo / real rows untouched; isolation; own routes; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_bugfix2.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
