CATCHALL1 — the small-fixes sweep: bulk-approve wording, one shared busy
indicator, End / Edit for direct assets with history, defining a listed option
contract from the entry picker, and the HollisMuse Lows still open, plus three
known pre-existing verify FAILs. 9 tasks + verification. Branch
catchall1-work.

Source: project doc claude/CATCHALL_BACKLOG.md (Joe, 2026-11-05 → 07) and
the HollisMuse Olivo reports (fix-up v5, 2026-11-06). None is urgent; each is
small. The large verify-script migration (role seeding, whole-table
audit_log counts across ~60 older verifies) is NOT in this sprint — it is
its own sprint (verifyfix1), so do not touch those scripts here except the
three named in Task 8.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo + live DB 2026-11-07; Task 1
confirms signatures):
- Bulk approve (bulkapprove1): Approvals → Bulk approve. The first button
  "Approve selected" only moves the selection to a confirmation panel at the
  BOTTOM of the page; a second "Approve" acts. After pressing it nothing
  visible happens until the batch finishes (Joe, 2026-11-06).
- Slow actions with no busy state today (Joe): bulk actions, Recalculate,
  imports, uploads, roll-ups, generation, sync.
- Direct assets (portentry1 / bugfix4): Add asset exists on an entity's
  Assets & Investments tab; there is NO End, Edit or Delete control (MUSE
  F4-01). Kind can only be changed by the operator script
  backfill_taxonomy_keys.py --reclassify. Private investments already have
  Edit as a new version with history (PATCH
  /private-investments/assets/{id}, GET …/history) — the pattern to reuse.
  Example live row: K.19 "Family office working capital" (eddf9851…) shows
  valuation method "Estimate (mark to model)" where the plan said Statement —
  the sprint does NOT edit it (demo data); Edit will let Muse do it.
- Held-away entry, option line: the picker says "No option contract on SPY
  exists yet. A contract is defined in Replication → Options." (MUSE I-12 /
  F.2) — the Olivo written SPY call (Dec 2026, 660, −50) cannot be entered.
  rep8 built option contracts (Replication → Options) with their own service
  and gates.
- HollisMuse Lows (2026-11-05): liability kinds' labels do not quite match
  the plan's wording (docs/demo/olivo plan files); "display lag" — lists
  that render empty until re-read (bugfix3 fixed the common cause); the
  record picker needs keystroke typing in a few places (portentry2 fixed the
  main picker).
- Pre-existing verify FAILs (not from recent work): verify_goldset one check
  422 on `proceeds_to_issuer_usd` (inherited by verify_notefix1 /
  verify_notefix2); previewperf / previewperffix1 / vhk2 self-checks carry
  known FAILs (noted by portentry1).
- bugfix4: every role is decided from user_roles (rbac.grant_named_role /
  grant_org_admin); fixtures seed that way. The nightly refresh (~07:00 UTC)
  writes real rows: count live-written tables BY FIXTURE IDS ONLY.
- Demo rows (demo-olivo) are permanent: the sprint writes none.
- Regression: direct-asset End / Edit changes what the net-worth core reads
  → re-run verify_rollup2.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret. Decimal everywhere. Bi-temporal
rule 3; RLS for every operation. Light / cream UI. Commit ALL sprint files
(excluding sprint_prompts/logs) on the current branch at the end with message
"catchall1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. The bulk-approve screen's two-step flow (components, state).
  1b. Every slow action in the web app (button → API call) with no busy
      state; whether a shared component exists.
  1c. The direct-asset model: tables, the add path, how the net-worth core
      reads a direct asset and its valuations, what "ended" would mean
      (valid_to? a disposal row?), the private-investment edit pattern.
  1d. The held-away option line → picker → rep8 option-contract service and
      its gates; what fields a listed contract needs.
  1e. The three Lows: the plan's liability wording vs the labels; any list
      that still renders empty until re-read (name it); every record picker
      that refuses paste / autofill.
  1f. The three pre-existing verify FAILs: cause of each.

=== TASK 2: BULK APPROVE WORDING ===
The first button says what it does ("Review N selected →"); the confirmation
comes into view (scrolled to and focused, or a dialog) with the count, the
items and the final "Approve N" / "Reject N"; Escape / Cancel returns with
the selection kept.

=== TASK 3: ONE BUSY INDICATOR ===
One shared component / hook (button disabled, spinner, "Working…" label,
aria-busy; restores on error with the error shown) applied to every slow
action in 1b. No per-screen copies.

=== TASK 4: DIRECT ASSETS — END AND EDIT ===
On an entity's Assets & Investments tab, each direct asset gets:
  - Edit: name, kind (the direct kinds list, incl. Cash / working capital),
    value, as-of, valuation method, notes — saved as a NEW VERSION with
    history (same pattern as private investments: id kept, prior version
    archived, GET …/history), audited; a kind change re-keys the asset's
    taxonomy the way --reclassify does (one shared function, not a copy).
  - End: an end date and reason (sold / disposed / transferred / written
    off / entered in error), optional proceeds; the asset stops counting in
    net worth from that date (valid time), its history kept; "entered in
    error" ends it from its start (it never counted). Nothing is deleted.
  - History: a "History" view per asset (versions, valuations, end).
Same permission as Add asset today (state which); a member never sees the
controls. The net-worth core counts an ended asset only before its end date.

=== TASK 5: OPTION CONTRACT FROM THE PICKER ===
When the held-away option line's picker finds no contract, it offers
"Define this contract" inline: underlying (picked from the master), call /
put, strike, expiry, multiplier (default 100), style — saved through rep8's
option-contract service with its existing gates (maker-checker if rep8 has
it), then selectable in the line. A duplicate contract (same terms) is
found, never re-created.

=== TASK 6: THE LOWS ===
Liability kind labels match the plan's wording (labels only; stored values
unchanged); fix each list from 1e that renders empty until re-read; every
record picker from 1e accepts paste and autofill (not just keystrokes).

=== TASK 7: (none — kept for numbering with the backlog) ===

=== TASK 8: THREE PRE-EXISTING VERIFY FAILS ===
verify_goldset's 422 on proceeds_to_issuer_usd (code or verify, with
evidence — so notefix1 / notefix2 inherit a pass); previewperf /
previewperffix1 / vhk2 --self-checks-only FAILs (each: cause, fix). Run only
their --self-checks-only (and goldset's one failing check in isolation, if
it can run without a full verify).

=== TASK 9: REAL PROOF + PROJECT STATUS ===
  - Bulk approve: the first button's label; the confirmation is focused /
    in view (node render test); cancel keeps the selection.
  - Busy indicator: the shared component on every 1b action (node test
    asserting each uses it); disabled while pending; restored on error.
  - Direct assets (fixtures): Edit keeps the id and archives the prior
    version, history lists both; a kind change to Cash / working capital
    re-keys it to Cash & Equivalents and the net-worth core moves it
    (card == core == SQL); End on a date: counted before, not after;
    "entered in error": never counted; nothing deleted; a member sees no
    controls and is refused the routes (403 / 404, VALID bodies).
  - Option contract: defined from the picker, then selectable; the same
    terms again finds the existing contract; the rep8 gates still apply.
  - Lows: labels; the lists render without re-read; pickers accept paste.
  - The three verifies' --self-checks-only → 0 FAIL; goldset's check passes.
  - Org isolation both directions (rolbypassrls = false); real and demo rows
    unchanged (counted by fixture ids where real activity writes).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_rollup2.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.
docs/PROJECT_STATUS.md entry; mark each item done in a "CATCHALL1" section;
add verify_catchall1.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_catchall1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
(assert it). Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id; re-read every
touched table for its exact pre-test count, counting audit_log and any table
real activity writes to BY FIXTURE IDS ONLY. Fixture orgs and users is_test =
true (assert it); fixture roles granted through user_roles (assert the app
decides each as intended). Read UNIQUE / CHECK / FK constraints and unique
indexes from the catalog before seeding (FAIL if none are read). Reserved
fixture prefixes that match no real row (assert it). Support
--self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read — the same org context the route sets, set ON
    the connection inside its transaction (a Python contextvar alone is not
    an RLS context: rep13 fix 1); rolbypassrls = false asserted; an empty
    list never passes a check.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - Each fixture upload / file has distinct content (duplicate detection
    runs first: rep13 fix 1).
  - Count your own node tests by their file, not by title prefixes.
  - A recursive CTE has ONE anchor and ONE recursive term.
  - A test stub must match the CURRENT signature of what it replaces.
  - Never seed a state the database forbids (read the unique indexes first).
  - No check frozen against live state.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1f
  [Y] Bulk approve wording + confirmation in view
  [Y] One busy indicator on every slow action
  [Y] Direct assets: Edit (version, history, kind re-key), End (valid time,
      reasons, entered in error), net-worth core follows, member refused
  [Y] Option contract defined from the picker; duplicate found; gates kept
  [Y] Lows: labels, no re-read lists, pickers accept paste
  [Y] goldset / previewperf / previewperffix1 / vhk2 self-checks 0 FAIL
  [Y] Isolation; own routes; --check CURRENT; real + demo rows unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rollup2.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
