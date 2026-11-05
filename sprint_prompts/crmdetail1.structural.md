CRMDETAIL1 — make the CRM able to hold a real multigenerational family:
governed relationship types (trustee, grantor, beneficiary, spouse, parent
of …) with a Relationships panel, typed detail fields for trusts / charities
/ LLCs / LPs, deceased and maiden-name fields for people, a LIABILITIES store
that nets into net worth, protection for permanent demo data, and the
missing entry screens a front-end onboarding needs. 9 tasks + verification.

Context: Joe wants a permanent fictional demo family (the Olivo family: 16
people, 26 legal entities, 8 outside organisations, 62 addresses incl.
seasonal homes, 117 relationships, 29 ownership links, 34 accounts, 95
holdings, 19 direct assets, 7 liabilities) entered by HollisMuse through the
FRONT END as the firm's staff would. The step-by-step entry plan is in
docs/demo/olivo/HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md (in the repo) — every step ID
there must be possible after this sprint, or listed as a known gap.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (live DB 2026-11-04; Task 1 confirms
the code side):
- public.entities: entity_type enum (individual, trust, llc, lp, gp,
  corporation, s_corp, c_corp, corp_uk, corp_eu, corp_cayman,
  corp_luxembourg, corp_other_intl, foundation, family_office, household,
  spv, account, other); columns include display_name, legal_name,
  name_prefix, first_name, middle_name, surname, name_suffix, date_of_birth,
  inception_date, end_date, is_active, status, sub_type (NULL on every row),
  tags text[], notes, country_of_formation, country_code, region_code,
  marital_status, citizenship_status, primary_email, primary_phone,
  primary_household_id, access_restricted. NO maiden_name, NO date_of_death.
- public.entity_addresses has is_seasonal, season_from_month,
  season_to_month, address_type enum (primary_residence, mailing, business,
  registered), is_primary. Whether the UI exposes seasonal fields: Task 1.
- public.entity_relationships: relationship_type is FREE TEXT with no CHECK;
  the only value ever used is 'ownership'; has ownership_pct, notes,
  effective_date, change_reason, bitemporal. public.entity_ownership
  (parent_id, child_id, ownership_pct 0<pct<=100) is a second ownership
  table; perms1's WF-03 made /tree and the graph read BOTH.
- public.entity_attributes (attribute_key, attribute_value, value_type,
  bitemporal) is a generic key/value store; keys in use today:
  behavioral_risk_indicators, investment_objectives, non_negotiables,
  risk_floor, time_horizon_years. public.entity_notes (note_type values in
  use: call, other). entity_employment, entity_tax_ids (encrypted + last4),
  trusted_contacts, households, household_memberships exist.
- There is NO liabilities / loans / mortgages table anywhere in public or
  portfolio. Assets: portfolio.assets (asset_class, asset_type,
  valuation_method, ownership_basis …), portfolio.valuations,
  portfolio.positions (owner_entity_id, account_id …), portfolio.commitments
  (commitment_amount, called / distributed / unfunded …), public.accounts
  (custodian_code, registration_type, tax_status, is_held_away,
  is_billable …). rep7 held-away manual entry exists.
- perms1 (merged first): internal staff with manage_members create
  households and add accounts; the Principal unions role + profile + sets;
  test personas jpl991723 org admin, jpl991722 support staff, jpl991721
  member, jlarizza@culmina.io super admin.
- Throwaway test rows carry the "MUSE –" prefix and clean-up scripts remove
  them (scripts/perms1_remove_muse_rows.py, _verify_cleanup.py by fixture id).
  The Olivo demo rows must NEVER be removed by any script.
- Regression: this changes the CRM relationship and ownership read paths
  perms1 just fixed → re-run verify_perms1.py ONCE, flat, after teardown,
  VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret. Column names from the live DB.
Bi-temporal rule 3 for every new table; RLS for every operation; members see
only their own family (bugfix2); staff-data reads via require_internal.
Decimal for money. Vocabularies and labels come from the API envelope, never
hard-coded in the page. Do not write any Olivo data — HollisMuse enters it.
Light/cream UI with the existing tokens. Commit ALL sprint files (excluding
sprint_prompts/logs) on the current branch at the end with message
"crmdetail1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. The CRM record page: which tabs / panels exist; how relationships are
      added and shown today (types offered, direction, reverse labels); how
      tags, sub_type, notes, addresses (seasonal fields?) and custom fields
      (entity_attributes) are edited.
  1b. Which code paths read entity_relationships.relationship_type (tree,
      graph, look-through, briefs, agents) and assume 'ownership'.
  1c. Where net worth / look-through value is computed (household, entity,
      X-ray) — the one place liabilities must subtract.
  1d. Front-end entry paths for: a real asset or collectible with a
      valuation; a private fund interest with a commitment; held-away
      positions (manual); positions on an INTEGRATED custodian account with
      no feed; a security not in the master.
  1e. Walk every step ID in docs/demo/olivo/HOLLISMUSE_OLIVO_DATA_ENTRY_v1.md
      (A–O): supported today / supported after this sprint / still missing.
      Table.
  1f. Every clean-up / removal script and verify teardown that deletes by
      name pattern, tag, or broad predicate (not by fixture id).

=== TASK 2: RELATIONSHIP TYPES ===
A governed vocabulary (reference table or code constant from 1a/1b — say
which) with, per type: key, label, reverse label, allowed from / to entity
types, symmetric yes/no. At least: spouse, former_spouse, partner,
parent_of (reverse child_of), sibling, grantor, trustee, successor_trustee,
co_trustee, trust_protector, distribution_committee, beneficiary,
income_beneficiary, remainder_beneficiary, annuitant, managing_member,
sole_member, manager, general_partner, limited_partner,
investment_committee, board_chair, board_member, officer, donor_advisor,
sponsor, owner, advisor, ownership (existing, unchanged). The API refuses an
unknown type or a disallowed pair, plainly. A Relationships panel on every
CRM record: add (type, other record, note, effective date), list grouped
both directions with the reverse label, end a relationship (valid_to,
never deleted). 1b's readers that assume ownership keep working (filter on
'ownership' explicitly).

=== TASK 3: ENTITY DETAIL FIELDS ===
Typed detail fields per entity type, editable on the record, stored per 1a
(entity_attributes with a typed key registry, or a detail table — decide and
say why), each with a label and type from the API:
  - trust: trust_type (revocable, irrevocable, dynasty, slat, grat, bypass,
    2503c, ilit, crut, crat, clat, idgt, qprt, other) → also sets sub_type;
    revocable; situs_state; gst_exempt; vesting_age; termination;
    annuity_term_years; annuity_end; unitrust_rate_pct; crummey_notices;
    funded_with (text).
  - foundation / charity: charity_type (private_foundation, daf,
    public_charity, supporting_org); sponsor (a CRM record);
    min_distribution_pct.
  - llc / lp / gp / family_office: management (member_managed,
    manager_managed), state_of_formation, purpose (text),
    valuation_policy (text).
  - state of formation for every legal entity (region_code if 1a shows it
    is that; otherwise a field).

=== TASK 4: PEOPLE FIELDS ===
maiden_name; date_of_death (sets status deceased and is_active false; the
record and its relationships stay, never deleted); "minor" shown from
date_of_birth (under 18 at today's date) on the record and in lists.

=== TASK 5: LIABILITIES ===
A liabilities store (schema from Task 1; names indicative):
portfolio.liabilities — owner entity, lender (a CRM record, optional),
kind (mortgage, securities_backed_line, margin, term_loan,
asset_secured_line, personal_loan, other), original_amount,
current_balance, as_of, rate_type (fixed | floating), rate_text,
maturity, collateral (account / asset / text), notes, bitemporal. RLS; staff
write with manage_members (or manage_portfolio — Task 1 picks the existing
key); members read their own family's. A Liabilities tab on the CRM record
and a Liabilities section on the household; net worth at 1c = assets −
liabilities, shown as three numbers (gross, liabilities, net). Unfunded
commitments are NOT liabilities (they stay in commitments). Apply with
scripts/_apply_crmdetail1.py through Doppler, synchronously; VERIFY it
landed; save docs/crmdetail1_part1.sql.

=== TASK 6: PERMANENT DEMO DATA ===
Any record tagged `demo-*` (entities.tags) or belonging to a household
whose members are all demo-tagged is protected: every 1f script and teardown
refuses to delete it (a guard in _verify_cleanup and the removal scripts,
asserting zero demo rows touched). docs/demo/README.md (an index; docs/demo/olivo/README.md is already written — keep it, add only the protection rule to it): what the Olivo demo
is, the tag, that HollisMuse enters it, and how to find it.

=== TASK 7: ENTRY GAPS FROM 1e ===
For each Muse step marked "still missing" whose API already exists, add the
smallest entry UI (e.g. seasonal months on the address form, tags on the
record, a valuation on a real asset, a commitment on a private fund). For
each that needs new backend work beyond this sprint, list it in
PROJECT_STATUS as a named gap (positions on an integrated account with no
feed is expected here — record the options, build nothing).

=== TASK 8: REAL PROOF ===
  - Relationships: each type round-trips; reverse label shown on the other
    record; unknown type and disallowed pair refused with the reason; ending
    one keeps it in history; tree / graph / look-through unchanged for
    ownership (same output as before on a fixture).
  - Detail fields: a fixture trust, foundation and LLC save and re-read every
    field; trust_type sets sub_type; wrong types refused.
  - People: date_of_death → deceased, inactive, still listed with
    relationships; minor flag from DOB.
  - Liabilities: create, edit (new version), end; net worth = gross −
    liabilities on the household and the entity (exact Decimal); a member
    sees own family's liabilities only; a member of another family refused;
    org isolation both directions (rolbypassrls = false).
  - Demo protection: a demo-tagged fixture survives _verify_cleanup and each
    removal script; a MUSE fixture is still removed.
  - 1e table: every Muse step supported or named as a gap.
  - Permission gates both ways on the identical request (member refused /
    support staff per its grants / org admin admitted).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_perms1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (the vocabulary, detail fields, liabilities, demo
protection, the 1e table and the named gaps); add verify_crmdetail1.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_crmdetail1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (fixture entities,
relationships, attributes, addresses, notes, liabilities, households);
re-read every touched table for its exact pre-test count. Fixture orgs and
users is_test = true (assert it). Read UNIQUE / CHECK / FK constraints from
the catalog before seeding. Reserved fixture prefixes that match no real row
(assert it). Support --self-checks-only and --cleanup-only. The regression
subprocess gets VERIFY_NESTED=1, after teardown, 2700 s; with
VERIFY_NESTED=1 skip it with one '[SKIP] nested run' line.

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
  - Immutability triggers: the documented privileged path, by fixture id.
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1f and the 1e table
  [Y] Relationship vocabulary + panel; ownership readers unchanged
  [Y] Detail fields per type; people fields
  [Y] Liabilities: schema, RLS, net worth exact, member scope, isolation
  [Y] Demo protection both ways
  [Y] Entry gaps built or named
  [Y] Gates both ways; own routes gated; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_perms1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; real rows unchanged
