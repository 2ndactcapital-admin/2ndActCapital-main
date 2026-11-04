REP12 — Replication Studio, sprint 12 of 15: the note-versus-replica check
across NEW issues — every time a structured note's terms are extracted (the
daily document-reading run or a manual extraction), rep11's verdict is
computed automatically, a DRAFT replica is prepared for replicable notes
whose underlying is confirmed, the replica's cost is compared with the
note's price when quotes exist, and new issues land in a ranked feed ("how
expensive does this look against what it is made of") with alerts to staff.
Plus a faster human path to confirm underlyings, which today block every
replica. 8 tasks + verification.

Context: spec roadmap phase 3 — "rep11 note replication from the 424B2
schema; rep12 note-versus-replica check across new issues". The thesis
(Joe): public EDGAR filings show what a note really is; the platform scores
each new issue against a transparent replica, which no issuer-paid platform
will do. rep11 (merged 5354e99) built verdicts, replicas, pricing and the
X-ray marker over the 54 notes with terms; it found: verdicts static 11 /
approximate 3 / not replicable 40; every note's underlying edges are
unresolved (28) or ambiguous (69) — "building needs exactly one RESOLVED
edge (a Super Admin confirms it)"; routing_decision is NULL on all 54 (STP
routing never ran), so "approved" is read from extraction_confidence = 'high'
and field_status; distribution / fees live only in note_term_readings.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after "merge staffroles1",
d529b18; Task 1 confirms signatures):
- services/note_replication.py: current_of, live_terms(conn, note_ids,
  terms_ids), underlyings_of, readings_of, underlying_state(edges), assess(t,
  n_underlyings, readings) → verdict + reasons, fingerprint(t, …), spec_in /
  spec_out / spec_complete, legs_for(spec, spread), replica_value /
  replica_return, approximation, computed_terms, grid, payoff_points;
  tables portfolio/public note_replicability, note_replicas,
  note_replica_legs, note_replica_comparisons (Task 1: exact schema).
- services/underlying_resolution.py: propose_resolution (NEVER writes
  link_state='resolved'; a registry hit → 'ambiguous' with
  proposed_global_security_id; else 'unresolved' / needs_manual_match;
  idempotent; Super Admin only, flag checked first), propose_all_unresolved,
  confirm_resolution (the ONE code site that writes 'resolved' — a human
  action), reject_proposal, load_queue. verify_underlyingresolution asserts
  "EXACTLY ONE code site writes link_state='resolved'". THIS RULE STAYS: no
  automatic confirmation of any underlying.
- services/ai_daily_runs.py (aibudget1): the daily document-reading run —
  _note_queue / _note_read for note terms, _replica_queue / _replica_read for
  fund terms, budgets per model, one run per job per day, run now; all four
  AI jobs are currently UNASSIGNED (no model chosen), so nothing is being
  extracted yet.
- rep8 adviser quotes (portfolio.adviser_quotes, insert-only) and the
  security-data layer are the only price sources; no price history exists.
- rep10 hypothetical-performance policy gates member visibility of any
  embedded-fee figure; staff always see it labelled "estimate".
- bugfix2 / staffroles1: staff-data reads use require_internal (every
  internal role); approvals keep their permissions; members see only their
  own family.
- Human tasks: services/human_tasks.py; notifications via member_todos /
  notifications for staff alerts; scheduler sweeps skip is_test orgs.
- vhk1/vhk2: apps/api/scripts/_verify_self.py; docs/VERIFY_RULES.md (no
  frozen counts of live state; server-time rule); every verify supports
  --self-checks-only and --cleanup-only.
- Regression: rep12 drives rep11's code on new notes → re-run
  verify_rep11.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- A HollisMuse browser pass may be running against production during this
  sprint: never touch real notes, terms, readings, underlyings or users; the
  verify uses fixture rows only.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; never start a script in the background or poll; write the verify
but DO NOT RUN IT; no network calls; no model calls (the verify fakes the
extraction step); never print a secret. Column names from the live DB.
Bi-temporal rule 3 for every new table; RLS for every operation. Decimal
everywhere. A number that needs data the platform does not have reads "not
priced — enter a quote" with what is missing. NO automatic underlying
confirmation and no automatic replica approval: the system prepares,
humans confirm. Every human action is a human task. Light/cream UI with the
existing tokens; labels and vocabularies from the API envelope. SQL
parameters inside CASE / COALESCE get explicit casts. Commit ALL sprint files
(excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Where a note's terms land after extraction (which function writes the
      live securities_global_note_terms row, sync and from the daily run) —
      the one hook point for "a new or restated terms version exists".
  1b. rep11's tables exactly; whether note_replicability is keyed by terms
      version (so a restatement re-assesses).
  1c. The underlying-resolution queue today: how many edges per state, how
      many 'ambiguous' edges carry a single registry proposal (one click to
      confirm) vs several vs none; the existing confirm screen (if any).
  1d. What "a new issue" means for the feed: filing date / pricing date /
      first extracted — pick one and state it.
  1e. How staff are alerted today (member_todos, notifications, human tasks)
      and which internal roles should receive note alerts (replication
      permissions).

=== TASK 2: SCHEMA ===
Design from Task 1 (names indicative): note_checks (per terms version: when
checked, verdict snapshot, replica draft id, comparison id, status
new | reviewed | dismissed, reviewer), note_check_settings (org: alert
threshold on embedded-fee estimate %, which archetypes / issuers / underlyings
to watch). Apply with scripts/_apply_rep12.py through Doppler,
synchronously; VERIFY it landed; save docs/rep12_part1.sql.

=== TASK 3: THE AUTOMATIC CHECK ===
On every new or restated live terms version (the 1a hook, plus a daily sweep
that catches anything missed; idempotent by terms version): compute the
verdict (rep11 assess); if static / approximate AND exactly one RESOLVED
underlying AND terms approved → prepare a DRAFT replica (rep11 builder,
status draft, never approved automatically); if quotes exist for the note
price and every leg → compute the comparison; otherwise record exactly what
is missing. Write a note_checks row. Never re-check an unchanged terms
version.

=== TASK 4: THE FEED AND ALERTS ===
Replication → Notes → "New issues": newest first, filters (verdict,
archetype, issuer, underlying, date), sortable by embedded-fee estimate
(only where priced; unpriced rows sort last with "not priced — needs …"),
each row linking to the note page. An alert (human task or notification) to
internal staff with replication access when a priced note's embedded-fee
estimate exceeds the org threshold, or when a watched issuer / underlying
issues. Mark reviewed / dismissed (a human action, recorded).

=== TASK 5: FASTER UNDERLYING CONFIRMATION (human) ===
A Super Admin queue screen grouping edges by proposal: for an 'ambiguous'
edge with ONE registry proposal, a one-click "confirm" (calls
confirm_resolution — still the only writer of 'resolved'); multi-select to
confirm several one-proposal edges in one action, each recorded as its own
confirmation by that user; reject; edges with several or no proposals open
the existing manual match. After a confirmation, the affected notes are
re-checked (Task 3). Show the counts per state at the top.

=== TASK 6: API, PERMISSIONS, SCREENS ===
Reuse view_replication / manage_replication / approve_replication; the
underlying queue stays Super Admin. Routes under /api/v1/replication/notes/
checks/… and the underlying queue, gated, in AUTHZ1_ROUTES; envelopes with
vocabularies; staff-data reads via require_internal + the permission.

=== TASK 7: REAL PROOF ===
  - A fixture note's new terms version (faked extraction) → one note_checks
    row, verdict correct; re-running the hook or the sweep adds nothing; a
    restatement → a new check.
  - Static note + one resolved underlying + approved terms → a DRAFT replica
    (never approved); unresolved underlying → no replica, reason recorded;
    not-replicable → no replica.
  - Quotes present → comparison exact (rep11 math); one missing → "not
    priced", naming it.
  - Feed order, filters, unpriced last; alert fires above the threshold and
    for a watched issuer, not below; reviewed / dismissed recorded with the
    user.
  - Underlying queue: one-click confirm goes through confirm_resolution (the
    single 'resolved' writer — prove by code scan and by row); multi-confirm
    writes one confirmation per edge with the user; a non-super is refused;
    the affected note is re-checked.
  - Member: no feed, no alerts; staff without view_replication refused;
    super / org admin with the permission admitted — identical requests.
  - Org isolation both directions (rolbypassrls = false); own routes via
    _verify_self; authz1_routes.py --check CURRENT.
  - Real notes, terms, readings, underlyings untouched (count + checksum).
  - Regression: verify_rep11.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 8: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (the automatic check,
the feed, alerts, the confirmation queue, "no automatic confirmation" rule,
the operator steps: assign models + budgets to the note-extraction job,
work the underlying queue, enter note price / option quotes); add
verify_rep12.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep12.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network,
no model calls (fake the extraction write through the same function the hook
watches). Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (platform fixture
notes / securities / edges included); re-read every touched table for its
exact pre-test count. Fixture orgs and users is_test = true (assert it).
Compute every expected date at the call. Read UNIQUE / CHECK / FK
constraints from the catalog before seeding. Reserved fixture prefixes that
match no real row (assert it). Support --self-checks-only and --cleanup-only.
Real-data checks use _verify_self.when_empty. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read; app_service rolbypassrls = false asserted for
    isolation checks; an empty list never passes a check.
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key from the catalog; exclude
    only columns real activity updates, and name them.
  - "One row per X": count live rows, not one run's rows.
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Schema landed, RLS for every operation
  [Y] Automatic check: once per terms version, restatement re-checks, draft
      replica only when eligible, never approved
  [Y] Comparison exact with quotes; "not priced" naming the gap
  [Y] Feed, filters, alerts above threshold / watched; reviewed / dismissed
  [Y] Underlying queue: one-click and multi-confirm via confirm_resolution
      only; super only; re-check after confirm
  [Y] Member none; permission gates both ways; org isolation
  [Y] Own routes gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep11.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; real rows unchanged
