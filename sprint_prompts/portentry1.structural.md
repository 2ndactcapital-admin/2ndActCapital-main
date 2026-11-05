PORTENTRY1 — close the gaps crmdetail1 found between the Olivo demo entry plan
and the front end: the CRM vocabulary gaps (GAP-CRM-01…06, with Joe's
decisions) and the portfolio entry gaps (GAP-PORT-01…05), so a staff user can
onboard a whole family — every holding type, direct assets, private funds and
commitments, integrated accounts awaiting a feed — and see a correct net worth.
Plus one verify-tooling fix. 9 tasks + verification.

Context: crmdetail1 (merged) walked every step of
docs/demo/olivo/HOLLISMUSE_OLIVO_DATA_ENTRY_v2.md against the UI and named the
gaps in docs/PROJECT_STATUS.md (CRMDETAIL1 entry, "Named gaps"). HollisMuse is
loading the CRM side of the Olivo family now; this sprint makes the second pass
(holdings, direct assets, private investments, reconciliation) possible.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (crmdetail1's PROJECT_STATUS entry and
repo, 2026-11-04; Task 1 confirms signatures):
- GAP-CRM-01: entity status vocabulary is prospect / active / inactive /
  archived (+ deceased); no client / contact.
- GAP-CRM-02: entities.marital_status column exists; no form field / vocabulary.
- GAP-CRM-03: sub-type choices are a hard-coded list (LLC has no real estate /
  operating / collectibles).
- GAP-CRM-04: detail-field registry (entity_attributes, prefix `detail.`,
  typed key registry) lacks fund_role (LP), ein_status (foundation) and
  insurance policy fields (ILIT).
- GAP-CRM-05: Ownership-tab writes use require_staff (org admin refused);
  verify_staffroles1 asserts that gate. The relationships panel uses
  internal + manage_members.
- GAP-CRM-06: support_staff lacks manage_members, so cannot add notes or
  addresses.
- GAP-PORT-01: held-away manual entry rejects rows that do not resolve to a
  security in the master or have no identifier (mutual funds, munis, cash,
  private, options, crypto).
- GAP-PORT-02: integrated custodian accounts with no feed have no positions
  path. POST /portfolio/import/positions exists (operator / super admin);
  portfolio_precedence decides which source wins.
- GAP-PORT-03: no direct-asset entry (asset + position by owner + valuation);
  portfolio.assets, portfolio.positions, portfolio.valuations exist.
- GAP-PORT-04: no route / UI for a private fund interest with a commitment
  (a create_commitment service function exists; portfolio.commitments has
  commitment_amount, called / distributed / recallable / unfunded, vintage).
- GAP-PORT-05: net worth reads entity_holdings, written only by
  POST /portfolio/rollup — a record's gross stays 0 until a rollup runs.
- Verify tooling: apps/api/scripts/_verify_self.py finds a sprint's end as its
  merge commit's second parent. perms1 reached main as a FAST-FORWARD
  (3fe2cef, no merge commit), so verify_perms1's routes self-check fails:
  "perms1: base 17ab37e NOT an ancestor of 01c0f57". All other merges have
  merge commits ("merge <name>" / "Merge branch '<name>-work'").
- JOE'S DECISIONS (2026-11-04): (1) org admins MAY write ownership %
  (same gate as the relationships panel: internal + manage_members);
  (2) support staff MAY edit CRM notes and addresses through a NARROW new
  permission that excludes legal, ownership, relationship, tax-id and detail
  fields; (3) integrated accounts in the demo are loaded by a one-time
  position file import, superseded by the custodian feed when it arrives.
- Regression: this changes CRM write gates and the positions entry paths
  crmdetail1 built on → re-run verify_crmdetail1.py ONCE, flat, after
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
names from the live DB. Bi-temporal rule 3 for every new table; RLS for every
operation; members see only their own family; staff-data reads via
require_internal. Decimal for money. Vocabularies and labels from the API
envelope. NEVER create a security in the PLATFORM master from org data — an
unmapped line is an ORG-scoped manual asset. Do not write Olivo data (Muse
enters it); generating the import FILE in Task 6 is allowed. Demo protection
(crmdetail1) stays intact. The bypass set (STAFF_ROLE_NAMES / is_staff) must
not grow. Light/cream UI with the existing tokens. Commit ALL sprint files
(excluding sprint_prompts/logs) on the current branch at the end with message
"portentry1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Where the entity status, marital status and sub-type lists live; every
      reader of entities.status (filters, reports, agents) — so adding
      client / contact breaks none.
  1b. The ownership write routes and every test asserting their gate.
  1c. The held-away manual entry path end to end; where it rejects
      unresolved lines; how an org-scoped manual asset is represented
      today (portfolio.assets with global_security_id NULL?).
  1d. POST /portfolio/import/positions: file format, validations, who may
      call it, how its rows are marked, how portfolio_precedence treats them
      against a later custodian feed.
  1e. create_commitment and the commitments model; how a private fund's NAV
      is stored (valuations); what the TA model reads.
  1f. POST /portfolio/rollup: cost, scope (org / household / entity), and
      what should trigger it.
  1g. _verify_self's end detection and every verify using it.

=== TASK 2: CRM VOCABULARIES (GAP-CRM-01…04) ===
Status: add client and contact (keep existing values; existing 'active' rows
unchanged unless 1a shows a safe mapping — say which); list filters updated.
Marital status: form field + vocabulary (single, married, divorced, widowed,
separated, domestic_partnership). Sub-types: vocabularies per entity type
from the config table (LLC: holding, real_estate, operating, collectibles,
investment, other; corporation, foundation, trust as 1a shows). Detail
registry: fund_role (lp: family_partnership / fund / spv / other),
ein_status (foundation: 501c3 / 501c4 / other), insurance policy group on
trusts (insurer, policy_type, face_amount, insureds — CRM records,
policy_number_masked).

=== TASK 3: GATES (GAP-CRM-05, 06) ===
Ownership writes: internal + manage_members (Joe), every ownership route;
update verify_staffroles1's ownership-gate assertion to the new rule (a
decided change of rule, not a weakening — assert org admin admitted, support
staff without the key refused, member refused, on the identical request).
New permission `edit_crm_contact`: notes, addresses, phones, emails on CRM
records the user may see — nothing legal, ownership, relationships, tax ids,
detail fields, status or tags. Grant it to the support_staff role
(ensure-function + migration for existing support_staff roles) and to the
"CSA / Ops" profile; never to client roles (bugfix2 rule).

=== TASK 4: MANUAL POSITIONS FOR ANY HOLDING (GAP-PORT-01) ===
The manual entry accepts, besides master-resolved securities, an ORG-scoped
manual asset: name, asset_class / sub-class (taxonomy), optional identifier
(CUSIP, ticker, internal), quantity / price or value, cost basis, as-of,
source. Types needed by the Olivo plan: mutual fund / money market (until the
master has them — auto-relink when an identifier later resolves, reported,
never silent), municipal bond (CUSIP optional, coupon, maturity), cash,
private / manual, listed option (prefer rep8's option contract in the master
when the underlying exists), digital asset. Maker-checker exactly as the
existing held-away entry.

=== TASK 5: DIRECT ASSETS AND PRIVATE FUNDS (GAP-PORT-03, 04) ===
On the CRM record (and the household): "Add asset" — real estate,
collectible, operating business, insurance policy cash value, other: name,
class, sub-class, owner entity + ownership %, cost basis, valuation (value,
date, method appraisal / estimate / statement, source), notes; new valuation
= new row, history kept. "Add private investment" — fund / direct deal /
SPV / SAFE / note: name, owner, commitment (amount, date, vintage), called,
distributed, unfunded (computed), latest NAV valuation; feeds commitments so
the TA projection sees it. Gates: internal + manage_members (org admin
admitted); members read their own.

=== TASK 6: INTEGRATED ACCOUNTS — ONE-TIME IMPORT (GAP-PORT-02) ===
A super-admin screen over POST /portfolio/import/positions: upload a CSV for
an integrated account, preview (rows, resolution per line, totals), approve
by a second person, rows marked as a provisional "stated" source that the
custodian feed supersedes via portfolio_precedence (prove it). Document the
CSV format in docs/demo/olivo/README.md and GENERATE
docs/demo/olivo/data/import/integrated_positions.csv from
docs/demo/olivo/data/holdings.csv for the eight integrated accounts (A7, A9,
A12, A14, A15, A20, A24, A29) — account identified by its mask, never a full
number. Do not load it.

=== TASK 7: NET WORTH STAYS CURRENT (GAP-PORT-05) ===
A household / entity rollup runs automatically after any position,
valuation, commitment or liability write for that family (debounced, scoped
to the family, never org-wide on a write), plus a "Recalculate" control for
internal staff and the nightly run. Net worth shows its as-of.

=== TASK 8: VERIFY TOOLING ===
_verify_self: when a sprint's branch reached main by fast-forward (no merge
commit), its end is the branch tip commit (find it from the sprint's own
commits / the recorded sprint commit), not a later merge's parent. Prove it
on perms1 (fast-forward) and on secmaster1 / crmdetail1 (merge commits); no
other verify's self-check changes result. Note in docs/VERIFY_RULES.md:
merges use --no-ff.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (each gap closed or still open, the new permission,
the import screen, operator steps: load integrated_positions.csv through the
screen, approve as a second person); mark the crmdetail1 named gaps resolved
with UPDATE lines; add verify_portentry1.py to scripts/regression_set.txt.

=== REAL PROOF (in the verify) ===
  - Vocabularies: client / contact, marital status, LLC sub-types and the new
    registry keys save and re-read; existing statuses unchanged.
  - Ownership: org admin admitted, support staff refused, member refused —
    identical request; verify_staffroles1 updated accordingly.
  - edit_crm_contact: support staff adds a note and an address; refused on
    ownership, relationship, tax id, detail field, status, tag; a member never
    holds it; bypass set unchanged.
  - Manual positions: each Task 4 type round-trips; nothing is created in the
    platform master (count + checksum); an unmapped mutual fund relinks when
    a fixture identifier resolves, reported.
  - Direct asset and private investment: values, valuation history,
    commitment with unfunded computed; TA projection reads it.
  - Import: preview, second-person approval, rows marked provisional; a
    later fixture custodian feed supersedes them; generated CSV covers the
    eight accounts, masks only, totals match holdings.csv.
  - Net worth updates after a write without a manual rollup; Recalculate
    works; as-of shown.
  - _verify_self: perms1 self-check passes; others unchanged.
  - Member scope and org isolation both directions (rolbypassrls = false);
    demo protection intact.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_crmdetail1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== VERIFICATION: apps/api/scripts/verify_portentry1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (fixture org,
users, entities, assets, positions, valuations, commitments, imports,
liabilities, entity_holdings, permission grants); re-read every touched table
for its exact pre-test count. Fixture orgs and users is_test = true (assert
it). Read UNIQUE / CHECK / FK constraints from the catalog before seeding.
Reserved fixture prefixes that match no real row (assert it). Support
--self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` (and any list / dict value) in bool(...).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read; app_service rolbypassrls = false asserted for
    isolation checks; an empty list never passes a check.
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
  [Y] CRM vocabularies; gates per Joe's decisions; edit_crm_contact narrow
  [Y] Manual positions for every type; platform master untouched
  [Y] Direct assets, private investments, commitments, TA reads them
  [Y] Integrated import: preview, maker-checker, superseded by feed; CSV generated
  [Y] Net worth current after writes; Recalculate; as-of
  [Y] _verify_self handles fast-forward merges
  [Y] Member scope, isolation, demo protection; own routes gated; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_crmdetail1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; real rows unchanged
