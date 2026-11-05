PORTENTRY2 — fix what HollisMuse hit while onboarding the Olivo family
through the front end (interim report 2026-11-05, stopped at I.0). The blocker:
legal entities cannot be household members and an account's owner can only be
a household PERSON, so 25 of 34 Olivo accounts (every trust / LLC / LP /
foundation account, incl. all eight integrated ones) cannot be created, which
blocks holdings, private investments, direct assets and reconciliation. Plus
one permissions defect in the UI, the ownership views disagreeing with each
other, form defects that corrupt dates, and smaller onboarding gaps. 9 tasks +
verification.

Context: Muse v3 pass, run by org admin jpl991723 and support staff
jpl991722. Done: 9 organisations, 16 people + notes, 62 addresses, 26 legal
entities, 116 / 117 relationships, 29 ownership rows (all owned entities total
100%), household "Olivo Family" (14 people), 8 / 34 accounts. All Olivo rows
are tagged demo-olivo and are REAL permanent data — never delete or alter them
in this sprint (fixtures only).

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (Muse findings, IDs as in the report;
Task 1 finds the code):
- MUSE-OLIVO-H-02 / G-02 (High, the blocker): the Add account "Owner" list
  offers only the household's member PEOPLE; the household member picker
  accepts entities but exists ONLY on the New household form — an existing
  household has no member add / remove control.
- MUSE-OLIVO-H-01 (High): Add account has no account Type (registration
  type), no tax status, no billable toggle; saved accounts show mask +
  custodian + "held away" only. public.accounts has registration_type,
  tax_status, is_billable, is_discretionary, is_held_away, service_model.
- MUSE-OLIVO-H-03 / H-04: custody "Another custodian — settled DVP" requires
  receiving custodian + DTC / participant number; the fields appear
  conditionally and are not marked required, so the save fails unexplained.
- MUSE-OLIVO-PERS-01 (High): support staff (no ownership permission since
  portentry1 — internal + manage_members) can OPEN the ownership-% Edit
  dialog with an editable % and an enabled Save. Save was not attempted.
- MUSE-OLIVO-E4-01 (High): the CRM record picker returns no results for a
  deceased / inactive record as a relationship TARGET (Dominic → former
  spouse → Theresa); adding FROM Theresa's record works.
- MUSE-OLIVO-B11-03 (High) / B11-01: the date input's year spin-button
  mangles typed digits (275760, 511951, 199999999 seen in unsaved dialogs);
  the segmented date input rejects a direct fill.
- MUSE-OLIVO-F-02: the Ownership tab's "Ownership tree" says "No ownership
  recorded" on Olivo Holdings LP despite 7 saved owners (Overview tree and
  OWNED BY list are correct); its "Owns" list mixes look-through holdings
  with direct ones (Stag's Hollow twice on AOF). Two ownership tables exist
  (entity_ownership, entity_relationships type 'ownership') — perms1 WF-03
  made /tree read both.
- MUSE-OLIVO-F-03: hierarchy graph, look-through ON, shows holdings as
  "0.6% eff." / "0.8% eff." instead of 60.0% / 80.0% (a fraction printed
  with a % sign); owners' effective % are correct.
- MUSE-OLIVO-P.1: the security master was not searchable as org admin
  (Muse's precondition check) — 15,959 securities are loaded (secmaster1);
  the master screen is Super Admin; held-away entry resolves by ticker.
- Smaller (all from the report): C.check seasonal addresses show at once
  with no "current" designation; C-01 address form has no note field;
  D6/D7/D8/D18-01 no detail field for GRAT / CRUT remainder_to (a CRM
  record), bypass-trust shares, SLAT beneficiaries note; D-01 / B114-01 the
  Edit dialog's country and prefix selects show "Select…" though a value is
  stored; D-02 / D19-02 detail rows show "—" until reload after a save;
  D19-01 the picker matches contiguous substrings, not words ("Marguerite
  Olivo" finds nothing); D17-01 picker textbox shows a raw UUID after
  selection; E-01 the picker needs keystroke typing (a paste / fill gives no
  suggestions); E-02 a saved relationship can lag one refresh; F-01 OWNED BY
  / OWNS rows do not show the ownership note; B112-01 maiden name and date of
  death only on the Edit dialog, not New; G-01 no "Households" link in the
  nav (page at /portfolio/households); H-05 the household's account count
  does not update until reload; PERS-03 support staff sees "ADMIN —
  ORGANIZATION" with Workflows and Billing & Custody links (check each is
  allowed by its grants or hide it).
- Regression: this changes household membership, account ownership and the
  ownership read paths → re-run verify_portentry1.py ONCE, flat, after
  teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret or a full account number. Column
names from the live DB. RLS for every operation; members see only their own
family; staff-data reads via require_internal; every UI control is shown only
when the API's permissions envelope allows the action (never a control that
the server would refuse). Do NOT touch any demo-olivo row or any real row —
fixtures only. Light/cream UI with the existing tokens. Commit ALL sprint
files (excluding sprint_prompts/logs) on the current branch at the end with
message "portentry2: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Household membership: tables, routes, the New-household picker, why
      an existing household has no member editor; can a legal entity be a
      member today (DB) — and what reads membership (visibility closure,
      net worth, X-ray, held-away, billing).
  1b. Account ownership: accounts.primary_entity_id rules, the Add account
      owner list source, every reader that assumes the owner is a person.
  1c. The ownership tab's tree vs the Overview tree and graph: which table /
      route each reads; where "0.6%" is formatted.
  1d. The record picker: search query (substring vs tokens), inactive /
      deceased filter, debounce / input handling, what it puts in the box
      after selection.
  1e. The date input component(s) used on the person / entity forms.
  1f. Every UI edit control on CRM records whose permission differs from its
      route's gate (PERS-01 class), and PERS-03's menu items vs grants.
  1g. Security search for internal staff (P.1): which route the held-away
      entry and any security picker use, and its gate.

=== TASK 2: HOUSEHOLD MEMBERS AND ACCOUNT OWNERS (the blocker) ===
Existing household: "Members" panel — add / remove people AND legal
entities (any CRM record of the org that is not in another household unless
1a shows multi-household membership is supported — say which), audited,
internal + manage_members. Account owner on Add account = any member of the
household, person or legal entity, plus (option) "add a legal entity owned
by a member" which adds it as a member in the same step. Every 1a / 1b
reader handles an entity member / entity owner (visibility closure,
net-worth rollup, held-away, billing groups, X-ray). Demo protection still
blocks deleting demo rows; removing a demo member uses the app path
crmdetail1 provided.

=== TASK 3: ACCOUNT FIELDS ===
Add account and the account detail edit: registration type (vocabulary from
config: individual, joint, trust, llc, lp, corporation, foundation, ira,
roth_ira, 401k_rollover, 529, daf, insurance, other), tax status (taxable,
tax_deferred, tax_exempt), billable (default off for demo-tagged
households), discretionary, service model if 1b shows it is used; shown on
the account card. DVP custody: receiving custodian and DTC / participant
number visibly required, with the reason when missing.

=== TASK 4: PERMISSION-TRUE UI (PERS-01, PERS-03) ===
Every 1f control renders only when the record's permissions envelope allows
it (ownership edit / delete, detail fields, status, tags, tax ids,
relationships, accounts). Prove the server ALSO refuses support staff on an
ownership-% save (identical request admitted for the org admin). Menu items
shown to support staff match its grants (hide what it cannot open; keep what
it can).

=== TASK 5: OWNERSHIP VIEWS AGREE (F-01, F-02, F-03) ===
The Ownership tab tree reads the same source as the Overview tree and the
graph (both ownership tables, as perms1's /tree); "Owns" shows direct
holdings by default with look-through as a labelled toggle, no duplicates;
look-through effective % for holdings formatted as a percentage (60.0%);
OWNED BY / OWNS rows show the interest note.

=== TASK 6: PICKER AND FORMS ===
Picker: word-token search across display / legal / first / middle / surname
("Marguerite Olivo" finds Marguerite Meloni Olivo); works on paste and
programmatic input, not only keystrokes; an "include inactive / deceased"
option, ON by default for family relationship types (spouse,
former_spouse, parent_of, sibling, grantor); shows the name, never the UUID,
after selection; a saved relationship appears without a refresh. Dates: one
date input that accepts typed and pasted ISO / US dates and never mangles a
year (reject years outside 1800–2200 with a message). Edit dialogs show the
stored prefix / country. Detail and state rows refresh after a save. New
person form also offers maiden name and date of death. Address form: an
optional note; seasonal addresses show which one is current today (by
month) and the others as "seasonal, <months>". Detail registry: remainder_to
(CRM record) for GRAT and CRUT, shares (text) for bypass trusts,
beneficiaries_note (text) for trusts. Household nav link; the household's
account count updates without reload.

=== TASK 7: SECURITY SEARCH FOR STAFF (P.1) ===
Internal staff can search the security master (ticker, name, CUSIP, ISIN,
FIGI) from the held-away entry and any security picker — read-only, gated
require_internal; the master admin screen stays Super Admin.

=== TASK 8: REAL PROOF ===
  - A fixture household gains a trust, an LLC and an LP after creation; an
    account owned by each is created; removing a member is audited; the
    visibility closure, rollup and held-away entry accept entity owners
    (net worth includes the entity accounts).
  - Account fields save and re-read; DVP without receiving custodian / DTC
    refused with the reason.
  - Support staff: no ownership edit control rendered (envelope false) AND
    the server refuses the save (identical request admitted for org admin);
    menu shows only what its grants open.
  - Ownership tab tree == Overview tree == graph for a fixture LP with 7
    owners; no duplicates; 60.0% formatting; interest note shown.
  - Picker: token search, paste input, deceased target found when the option
    is on, name shown after selection.
  - Date input: typed / pasted dates round-trip; out-of-range years refused.
  - New detail keys round-trip; seasonal "current" correct for a fixture
    date; staff security search finds AAPL by ticker and by name, a member
    is refused.
  - node tests for the date input, picker and permission rendering.
  - Demo rows untouched (count + checksum on demo-olivo entities,
    relationships, households, accounts); real rows unchanged.
  - Org isolation both directions (rolbypassrls = false); own routes via
    _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_portentry1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (each Muse finding → fixed / not, with its ID);
docs/demo/olivo/README.md: note the household now holds the 26 legal
entities; add verify_portentry2.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_portentry2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (fixture org,
users, entities, households, memberships, accounts, ownership, relationships,
positions, entity_holdings); re-read every touched table for its exact
pre-test count. Fixture orgs and users is_test = true (assert it). Read
UNIQUE / CHECK / FK constraints from the catalog before seeding. Reserved
fixture prefixes that match no real row (assert it). Support
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
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates (users.last_login_at), and name them.
  - Immutability triggers and demo protection: the documented privileged
    path, by fixture id.
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1g
  [Y] Entity members on an existing household; entity account owners;
      every reader handles them
  [Y] Account fields; DVP required fields with reason
  [Y] Permission-true UI + server refusal (identical request); menu = grants
  [Y] Ownership views agree; 60.0%; notes shown
  [Y] Picker, dates, dialogs, detail keys, seasonal current, nav, live count
  [Y] Staff security search; member refused
  [Y] Demo and real rows untouched; isolation; own routes; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_portentry1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
