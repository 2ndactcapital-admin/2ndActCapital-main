BUGFIX4 — HollisMuse Olivo final report (2026-11-06): the bugs that keep the
demo family from reconciling, plus identity and form defects. The data model
held (10 entities reconcile exactly; every other gap decomposes to named
lines). 9 tasks + verification.

Context: report saved as claude/HOLLISMUSE_OLIVO_FINAL_2026-11-06.md
(project). Muse finished; nothing else is writing. The Olivo rows are
permanent demo data (tag demo-olivo) — never delete them; repairs to them
are done by operator-run scripts (dry run first), never by the sprint.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (Muse report + live DB 2026-11-06):
- MUSE-OLIVO-O-04-02 (HIGH): 56 held-away manual drafts were approved but
  only 24 are live — exactly ONE line per account; the other 32 read
  "Approved · read 1, accepted 1, rejected 0" like the live ones, with no
  status saying they are not live. Muse saw "one Save as draft = one draft
  PER LINE" (I-11). Hypothesis to confirm in Task 1: rep7's held-away import
  is an account SNAPSHOT for an as-of date, so each later per-line import for
  the same account + as-of supersedes the earlier one.
- O-04-03 / roll-up (HIGH): every platform job (the four secmaster1 jobs and
  marketdata1's nightly) shows "schedule not active", last run "never"; every
  listed security has price_coverage 'unknown'. The net-worth roll-up
  EXCLUDES live listed lines without a price series ($7,545,600: CRUT SCHR,
  Cipolla Olivo SCMB, MTO cash-as-QQQ, MTO ETH) while the household X-ray
  values the same lines from their entered value.
- Entry-form defects: A25's cash line was saved against the QQQ security
  (the previous line's security carried into the next line — stale form
  state); "ETH" (asset kind digital asset) resolved to the Grayscale
  Ethereum Mini Trust ETF (ticker ETH) — a digital-asset line must never
  resolve through the master's tickers; cash / value-only lines require a
  quantity (entered as qty = amount @ 1.0).
- K.19 working capital ($1,400,000, kind "other (cash)") contributes $0 —
  bugfix3's backfill left kind 'other' unmapped; a cash-kind direct asset
  must map to the cash taxonomy.
- Household: no household-level Recalculate visible to the org admin on the
  household's own tabs (bugfix3 added one — find where it rendered, Muse did
  not see it); no household net-worth total anywhere; the Consolidate tab
  renders blank.
- Private investments: saved rows have no edit (notes, dates); simplified
  kinds (hedge fund, direct deal, SAFE, note) have no vintage / date field;
  commitment rows are not links, so the capital-call projection cannot be
  opened from the UI.
- Display: account lot quantities render in scientific notation ("3.25E+6",
  "7E+4"); the account Tax section labels every non-taxable account "IRA /
  Roth (tax-advantaged)" though the stored status is right.
- Picker side effect: the dashboard lists three stub entities "Complete
  profile: microsoft / alphabet / IBM" ("Stub entity created via picker") —
  the record picker creates a CRM stub from typed text.
- Identity: public.users has jlarizza@culmina.io (id 06fe93ed…, auth0_sub
  "auth0|06fe93ed-…" — its own id, never a real Auth0 subject, last login
  NULL) and a stub super admin "auth0|6a7c8b473069946d5a6d5400@placeholder.
  local" (id be8165db…, org bb347258…, logged in today) — the culmina.io
  sign-in lands on a stub user, not the real one. The integrated import
  (portfolio.provisional_imports cdb4d45d…, draft) was entered by
  jlarizza@gmail.com (f46eb620…). Both of Joe's sign-ins got "A different
  Super Admin must approve this import." Joe is linking the culmina.io login
  through People & Access (usermgmt1 "link a pending login") as an operator
  step; the sprint must NOT change real users.
- ROLE SOURCES DISAGREE (found 2026-11-06): super-admin status is read ONLY
  from users.role (services/rbac.is_super_admin; authz Principal :216), but
  People & Access writes the role to user_roles. Joe made the org-admin test
  login a super admin in the UI → user_roles 'super_admin', users.role still
  'member' → "Super Admin required" everywhere. (Claude aligned that one
  user's users.role by hand, 2026-11-06, at Joe's request.)
- TEST LOGINS ARE PLACEHOLDER USERS: the four jpl9917xx rows
  (jpl991721 / 22 / 23) have auth0_sub NULL and have never signed in; the
  logins Muse used are placeholder users created 2026-11-03
  ("auth0|6aea55…@placeholder.local" etc.), all three with contact_email
  jpl99172@gmail.com and users.role 'member', their real role only in
  user_roles. Joe's jlarizza@gmail.com and jlarizza@culmina.io sign-ins both
  resolve to ONE user (the gmail row; Auth0 identities linked), so there is
  no second super admin for maker-checker.
- AGG / VTI anomaly: the top-holdings view showed AGG $24,831,150 vs
  $19,500,250 in the file and VTI $15,848,700 vs $15,989,700; VOO / SGOV /
  SMA-CORE matched. Unexplained.
- Regression: this changes the held-away apply path and the manual entry
  form portentry1 extended → re-run verify_portentry1.py ONCE, flat, after
  teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret or a full account number. Never
write real or demo-olivo rows; repair scripts are written and dry-run-tested
on fixtures, the operator runs them. Do not turn on any scheduled job for a
real org — the operator decides. Members see only their own family. Light /
cream UI. Commit ALL sprint files (excluding sprint_prompts/logs) on the
current branch at the end with message "bugfix4: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. The held-away apply semantics (snapshot vs additive) and why 32 of 56
      Olivo lines are not live — evidence from the Olivo rows (read-only).
  1b. The roll-up's valuation rule for listed positions without a price
      series vs the X-ray's.
  1c. Why every platform job is "schedule not active" (trigger is_active?
      the Hollisworks org? by design pending operator switch-on?).
  1d. The entry form's state handling (stale security), digital-asset
      resolution, the quantity requirement for cash / value lines.
  1e. Where the stub entities come from (which picker, which flow).
  1f. The AGG / VTI aggregation in the top-holdings view.
  1g. Why the culmina.io sign-in became a stub user (first-login
      provisioning vs a pre-seeded row with a fake auth0_sub) and what the
      approval check compares.

=== TASK 2: HELD-AWAY ENTRY = ONE DRAFT PER ACCOUNT ===
Manual entry builds ONE draft per account with all its lines (add / edit /
remove lines before saving); approving applies all of them. If 1a confirms
snapshot semantics, a second draft for the same account + as-of is shown as
"replaces the earlier snapshot" before saving, and the earlier one is marked
superseded — never silently. Each draft and line shows live / superseded.
scripts/repair_heldaway_snapshots.py: for an account whose approved per-line
drafts superseded each other, re-apply the union of its approved lines as one
snapshot (dry run lists accounts, lines and totals; real run idempotent;
audited). Operator runs it.

=== TASK 3: ROLL-UP VALUES WHAT IT HAS ===
A listed position without a price series is valued at its entered market
value as of its as-of date (labelled "entered value"), the same basis the
X-ray uses; a priced position uses the price. Roll-up and X-ray agree on a
fixture. Cash-kind direct assets map to the cash taxonomy (and the bugfix3
backfill maps 'other (cash)').

=== TASK 4: JOBS SWITCH ===
The Runs / Security master screen shows each platform job's schedule state
and lets the Super Admin switch it on / off (audited); nothing is switched
on by the sprint. Document what each job fetches and that no free EOD price
source exists yet (so price coverage stays 'unknown' until the prices
sprint).

=== TASK 5: ENTRY FORM ===
No field carries over between lines; digital assets resolve only to an org
manual asset (or a future digital-asset source), never a master ticker;
cash and value-only lines take an amount with no quantity.

=== TASK 6: HOUSEHOLD, PRIVATE INVESTMENTS, DISPLAY ===
Household page: a net-worth total (gross / liabilities / net, as-of) and
Recalculate, visible to the org admin; Consolidate renders (or says why it is
empty). Private-investment rows: edit notes / dates / vintage (new version,
history kept); simplified kinds gain vintage and date; commitment rows link
to the capital-call projection. Lot quantities fixed-point; the Tax section
reads the stored status.

=== TASK 7: PICKER STUBS, IDENTITY, ROLES, AGG / VTI ===
ONE role source: super admin (and every role) is decided from the same table
the UI writes (or the UI writes users.role too — pick one, migrate, prove
both agree for every real user and report any that differ today, changing
none of them). People & Access lists placeholder logins with their sign-in
id and contact email, and links a placeholder to a pre-created user (one row
per person, history kept). A maker-checker refusal names both people when
two logins resolve to the same person.
The record picker never creates a CRM record silently: "Create '<text>'"
must be an explicit choice with a confirmation; list the three existing
stubs in PROJECT_STATUS for Joe (do not delete). Approval refusals name who
made the item ("Entered by <name> — a different Super Admin must approve").
People & Access shows a stub / placeholder login prominently with "link to
an existing user". Fix the AGG / VTI aggregation per 1f.

=== TASK 8: REAL PROOF ===
  - Before / after on a fixture account: three single-line approvals → before:
    one live line (reproduce); after: one draft with three lines → three live;
    a replacing draft is labelled and the old one superseded.
  - Repair script: dry run on a fixture lists the superseded lines; real run
    makes them live once; second run changes nothing.
  - Roll-up == X-ray for an unpriced listed fixture line; cash direct asset
    counted.
  - Job switch: Super Admin only (org admin refused, identical request);
    audited; no real job switched.
  - Form: stale security impossible; ETH digital asset never matches a
    fixture ETF with ticker ETH; cash without quantity saves.
  - Household total + Recalculate; private-investment edit with history;
    projection link opens; fixed-point quantities; Tax label correct.
  - Picker: typing never creates an entity without the explicit choice.
  - Approval refusal message names the maker; People & Access flags a
    placeholder login and links it to a pre-created user.
  - A role set in People & Access (super admin, org admin, support staff)
    takes effect on the identical next request — the server's role and the
    UI's role agree; a report lists real users whose two role sources
    differ today (read-only).
  - AGG / VTI fixture aggregates exactly.
  - Members: own family only on every changed route; org isolation both
    directions (rolbypassrls = false); demo and real rows untouched.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_portentry1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s (its OWN final ^TOTAL line).

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (each Muse finding → fixed / not, with its ID; the
operator steps: repair script dry run → real run; Recalculate the Olivo
household; switch on the platform jobs if wanted); add verify_bugfix4.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_bugfix4.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id, using the
documented privileged paths for immutable tables (held_away_imports,
authority); re-read every touched table for its exact pre-test count. Fixture
orgs and users is_test = true (assert it). Read UNIQUE / CHECK / FK
constraints from the catalog before seeding. Reserved fixture prefixes that
match no real row (assert it). Support --self-checks-only and --cleanup-only.
The regression subprocess gets VERIFY_NESTED=1, after teardown, 2700 s; with
VERIFY_NESTED=1 skip it with one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read (the same org context the route sets);
    app_service rolbypassrls = false asserted; an empty list never passes.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - A recursive CTE has ONE anchor and ONE recursive term.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates, and name them.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1g
  [Y] One draft per account; supersede labelled; repair script (fixture)
  [Y] Roll-up values unpriced lines like the X-ray; cash direct assets count
  [Y] Job switch, Super Admin only, nothing switched on
  [Y] Entry form: no stale security, digital assets, cash without quantity
  [Y] Household total + Recalculate; private-investment edit; projection
      link; display fixes
  [Y] Picker never silently creates; approval message names the maker;
      placeholder logins flagged; AGG / VTI exact
  [Y] Member scope, isolation, demo / real rows untouched; own routes;
      --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_portentry1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
