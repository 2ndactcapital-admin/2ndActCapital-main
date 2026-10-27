REP7B — Replication Studio, sprint 7b: account AUTHORITY and CUSTODY as two
separate settings — a household-level authority option the adviser sets
(read-only | advise | managed), overridable per account and approved by a
second person; a custody field (integrated custodian | other custodian,
settled DVP | outside the platform); rep7's single held-away trade guard
re-keyed on authority; advise mode producing one-off instructions with a
letter of instruction; managed accounts at another custodian producing manual
DVP settlement instructions. 8 tasks + verification.

Context: spec "Replication Studio — ETF & Fund-to-SMA Spec", section
"Authority and custody — two separate settings per account (Joe, 26 Oct)"
and "Omnibus trading and DVP settlement (rep9)". Decision correction (Joe,
2026-10-26): his decision 6 was "DVP into held-away accounts", not read-only
visibility. rep7 merged the two meanings into accounts.is_held_away = never
traded; that stays the default for every existing held-away account (authority
read-only) until an adviser changes it. Joe: the long-term answer is a
household-level option the adviser sets — they can treat held-away accounts
either way. Block trading in an omnibus account, allocation and automated DVP
are rep9 — NOT this sprint; here a managed account at another custodian gets
MANUAL DVP settlement instructions. Billing and disclosure for advising on
held-away assets are an open decision for Joe and counsel — show nothing
about billing.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 9ed75ae, merge rep7; Task 1
confirms exact signatures):
- rep7's guard: services/held_away.py refusal(account, action) -> str | None
  (keys on account['is_held_away']) and refuse(account, action, exc_cls).
  Call sites (verify_rep7 asserts ONE per path): rebalance_proposals._generate
  (:672, every trigger), transition_preview / transition (:813), execute
  (:1078, routing a proposal to instructions), account_models.propose_change
  (join), harvest.scan_account, execution.create_instructions,
  custody.trading.route_trade_list. Envelopes hide join / model / settings /
  proposal / trade actions on a held-away account.
- public.accounts columns: id, org_id, account_number_masked,
  account_number_hash, custodian_code, custodian_account_id,
  registration_type, tax_status, primary_entity_id, household_id,
  advisor_of_record_id, service_model (free text), is_billable,
  is_discretionary, is_held_away, opened_on, closed_on, base_currency, …
  bi-temporal. is_held_away is set by custody/importer.py.
- public.households has only id, org_id, name, created_at, created_by — a
  household-level setting needs a new versioned table.
- rep5: account_models.propose_change sets advice_only = not
  account['is_discretionary'] (account_models.py:547); rebalance_proposals
  :503 sends advice-only links' instructions to the member. So "advise"
  already half-exists for managed non-discretionary accounts. Task 1 decides
  how authority and is_discretionary / advice_only relate so there is ONE
  source of truth (e.g. advise ⇔ not discretionary), and migrates without
  changing any existing account's behaviour.
- rep2: execution instructions — executor_type CHECK ('custodian_desk',
  'broker', 'member', 'other_adviser', 'trader'); LETTER_EXECUTORS =
  ('member', 'other_adviser'); services/execution_letter.py renders a letter
  of instruction PDF on demand (reportlab, never stored, mask unless the full
  number is explicitly supplied and checked against the hash);
  execution.letter(pool, *, org_id, instruction_id, actor_id,
  include_full_number, full_account_number). Instruction statuses draft →
  pending_approval → approved → sent → (partially_)filled → reconciled →
  closed | cancelled | expired; reconcile_method 'custodian_data' | 'manual';
  maker-checker CHECKs.
- rep7: held_away_sources / imports / records, staleness + the
  'confirm_held_away_data' task, held-away data in wash-sale / gain budget /
  loss bank, the X-ray ("Open join request" managed-only), permissions
  view / manage / approve_held_away; human_tasks.register_precheck.
- Existing live data: 1 DRAFT execution instruction sits on a held-away
  account (pre-rep7). Real accounts: 5. Do not change either except as the
  migration rule below says.
- Leftover fixture orgs from killed runs exist (REP2 99000000-…e2001/2/3,
  REP6 …e6001/2/3). Never touch, sweep or FK-walk them; exclude them from any
  leftover check (the next verify_rep2 / verify_rep6 run clears its own).
- authz1 rules; envelope pattern; org_id only from the session;
  extra='forbid'; Super Admin first. sprint_prompts/logs/ is gitignored.
- Regression: this sprint re-keys rep7's guard → re-run verify_rep7.py ONCE,
  flat, after teardown, VERIFY_NESTED=1 (it then skips its own nested
  verify_rep6), timeout 2700 s. verify_rep7 assumes every held-away account is
  read-only — with the default migration it must still pass unchanged except:
  FIX verify_rep7.py's TODAY bug (TODAY = date.today() fixed at script start
  while services read date.today() at call time — a run crossing midnight
  failed "a viewer cannot confirm; an adviser confirms …" with expires_on off
  by one): recompute the expected date at the call. Change nothing else in
  verify_rep7.py, and say which lines changed.
- The live Render scheduler (main) skips is_test orgs; every fixture org MUST
  be is_test = true.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. No network calls. Never print a secret or a full
account number (masked numbers only; the letter's full-number path stays as
rep2 built it). Column names from the live DB. Bi-temporal rule 3 for every
new table; RLS policies for EVERY operation each new table will take. Decimal
everywhere. No existing account's behaviour changes without an approved
authority change. Labels and vocabularies from the API envelope. Light/cream
UI with the existing tokens. Commit ALL sprint files (new and modified,
excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every reader of is_held_away, is_discretionary, advice_only and
      service_model (services, routers, web, verify scripts) — what each
      decides today.
  1b. The mapping: authority (read_only | advise | managed) × custody
      (integrated | other_custodian | outside) → which paths are allowed.
      Proposed (confirm or correct with reasons):
        read_only  — any custody: data only (rep7's refusal everywhere).
        advise     — any custody: proposals with trigger 'manual' only (no
                     join, no model, no drift / model_change / cash_flow /
                     harvest); approved proposals become instructions with
                     executor member or other_adviser and a letter of
                     instruction; never routed to a custodian API.
        managed    — integrated: as today (adapter routing).
                     other_custodian: every trigger, join, harvest allowed;
                     instructions are manual DVP settlement instructions
                     (executor broker / trader), never sent to an API.
                     outside: not allowed (managed needs a custodian) —
                     refuse the setting with a plain reason.
      And how is_discretionary / advice_only fold in (one source of truth).
  1c. The migration: every existing account gets custody (from
      is_held_away and custodian_code) and authority (held-away → read_only;
      others → managed, with discretionary / advice-only preserved) so that
      every existing path behaves exactly as before. List the 5 real
      accounts' resulting values (masked) without changing behaviour.
  1d. Where the household / account authority must be read (the guard, the
      envelopes, the X-ray button, rep5's advice_only, staleness), and how
      the existing DRAFT instruction on a held-away account behaves under
      read_only (approval must be refused with the plain reason; it is
      neither deleted nor cancelled by the migration).
  1e. What a DVP settlement instruction needs beyond today's columns
      (receiving custodian, DTC / participant number, agent bank, settlement
      date, the masked account at the receiving custodian, contra party) and
      whether execution_letter can render it or a separate "DVP settlement
      instruction" PDF is cleaner.

=== TASK 2: SCHEMA ===
Design from Task 1 (org-scoped; names indicative):
  - household_authority (versioned): household, authority, effective_from,
    reason, maker, approved_by (human task 'approve_authority_change'),
    status draft | approved | rejected; one live approved row per household.
  - account authority override + custody: columns on accounts or a versioned
    account_settings table (Task 1 chooses; bi-temporal either way):
    authority_override (null = household's), custody
    ('integrated' | 'other_custodian' | 'outside'), receiving custodian
    details for other_custodian (custodian name, DTC / participant number,
    agent bank — no full account numbers; masked only).
  - settlement detail for instructions: columns or a child table
    (settlement_type 'dvp', receiving custodian, participant number, agent
    bank, settlement date, contra / executing broker).
  - CHECKs: managed + outside refused; advise / read_only never carry
    settlement detail.
Apply with scripts/_apply_rep7b.py through Doppler, synchronously; VERIFY it
landed; save docs/rep7b_part1.sql. Run the migration (1c) in the same script
and print the before / after mapping counts (masked).

=== TASK 3: THE GUARD, RE-KEYED ===
  - held_away.refusal becomes an authority-aware policy (keep the function
    name and the one-call-per-path structure verify_rep7 asserts):
    effective_authority(account) = account override ?? household authority ??
    the migrated default; refusal(account, action) returns None when the
    action is allowed by 1b's matrix, else a plain-words reason naming the
    account (masked), its authority and custody, and what would allow it.
  - Envelopes list exactly the actions the matrix allows.
  - The existing DRAFT instruction on a held-away read-only account: approval
    refused with the reason (via the same policy).

=== TASK 4: AUTHORITY CHANGES (maker-checker) ===
Household → "Authority" panel: current setting, history, a change request
(new authority, effective date, reason) that opens a human task
'approve_authority_change' (approve_held_away or a new approve_authority —
Task 1 chooses and says why; maker ≠ checker); per-account override in the
account page, same approval. A change never alters open proposals: an open
proposal the new authority would not allow is withdrawn with a plain reason
(rep5's withdraw path), never silently executed.

=== TASK 5: ADVISE MODE ===
  - A manual one-off proposal for an advise account (rep5's manual trigger;
    the full wash-sale / gain-budget / staleness machinery applies).
  - Approved → instructions with executor member or other_adviser (adviser
    picks; contact recorded), source 'proposal', and the rep2 letter of
    instruction available on each (or one letter per proposal listing every
    trade — Task 1 chooses).
  - Confirmation: the executor's fill is entered by the adviser (manual) or
    reconciled when the held-away feed shows the trade (reconcile_method
    'custodian_data' with the held-away record as reference).
  - X-ray: on an advise account the line offers "Propose a one-off trade"
    (opens a draft manual proposal selling the fund) instead of "Open join
    request".

=== TASK 6: MANAGED AT ANOTHER CUSTODIAN ===
  - Proposals (every trigger), joins and harvest allowed; routing never calls
    a custodian API: instructions are manual DVP settlement instructions
    (executor broker or trader) carrying the account's receiving-custodian
    details and the settlement date (trade date + 1 business day; holidays
    from the existing calendar if one exists, else weekends only and say so).
  - A "DVP settlement instruction" PDF (or the letter extended — per 1e),
    masked account numbers only unless the rep2 full-number path is used.
  - Fills entered manually or reconciled from the custodian feed / held-away
    import; lots booked at the fill price.
  - Staleness applies (the account's data comes from outside the integrated
    feed).

=== TASK 7: REAL PROOF ===
  - Migration: every existing account keeps its behaviour (5 real accounts:
    paths allowed / refused before = after; count + checksum of accounts,
    links, proposals, instructions unchanged apart from the new columns /
    table rows); the existing held-away DRAFT instruction is refused approval
    with the reason, not deleted or cancelled.
  - Matrix: for each authority × custody cell, every path (join, each
    proposal trigger, transition, harvest, execute, create_instructions,
    route_trade_list) allowed / refused exactly as 1b, on identical requests;
    managed + outside refused as a setting.
  - Authority change: maker refused, checker approves; account override
    beats household; history kept; an open proposal the new authority
    forbids is withdrawn with the reason.
  - Advise: manual proposal → approved → instructions to member /
    other_adviser with the letter (PDF bytes start %PDF, mask only);
    drift / model_change / harvest / join refused; reconcile from a held-away
    record; X-ray offers the one-off trade, not the join.
  - Managed other-custodian: rebalance + harvest proposals → manual DVP
    settlement instructions with the receiving-custodian details and T+1
    settlement date; never routed to an adapter API; the settlement PDF masks
    numbers; staleness applies.
  - Org isolation both directions (rolbypassrls = false); gates both ways on
    identical requests; node tests show no authority-change / trade controls
    the matrix does not allow.
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep7.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    2700 s timeout; only its TODAY fix changed.

=== TASK 8: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (authority × custody
matrix, migration rule, advise flow, manual DVP settlement until rep9), and
add verify_rep7b.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep7b.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls. Wrap every section so an exception is a [FAIL]; teardown at start and
end in FK child-before-parent order from pg_constraint, deleting ONLY rows
this run created, by fixture id (never walk the FK graph from a broad parent,
never sweep other scripts' leftover fixture orgs); clear audit_log,
domain_events, member_todos, workflow rows created by this run before fixture
users; reconnect once and re-count if a connection drops; then re-read every
touched table for its exact pre-test count. Compute every expected date at the
moment of the call (never a TODAY fixed at script start). Fixture orgs
is_test = true (assert it). Fixture users created explicitly with the
auth0_sub the mocked token carries, is_test = true, using the users-RLS
pattern from verify_rep1–7. Read every UNIQUE (including partial unique
indexes), CHECK and FK constraint on the touched tables from the catalog
before seeding, and check every seed literal against its lookup table or
CHECK (executor_type, instruction status, custody, authority, registration_type,
tax_status, proposal trigger/status) before inserting. Fixture households,
accounts (masked numbers only), positions, lots and securities use reserved
prefixes that match no real row (assert it).

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool (wrap any
    `a and b` that could return a list in bool(...)).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Exclude this run's own fixtures and other scripts' known leftover fixture
    orgs from any "no leftovers" check; ignore fixture-org roles in any grant
    check.
  - Never spawn a background process. The regression subprocess gets
    VERIFY_NESTED=1, runs after teardown, 2700 s timeout; with
    VERIFY_NESTED=1 this script skips its regression section with one
    '[SKIP] nested run' line.
  - Every import at the top of the file.
  - Never touch real accounts, positions, lots, transactions, proposals,
    instructions or users beyond the migration's new columns / rows: count +
    checksum before and after (the checksum excludes only the new columns).
  - A failing check's detail names the culprit rows (ids, trigger / source,
    maker, created_at).

Assertions:
  [Y] Task 1 findings 1a–1e reported, with the final matrix and migration
      mapping
  [Y] Schema landed: tables / columns, CHECKs (managed + outside refused),
      partial uniques, RLS with a policy for every operation
  [Y] Migration: every existing account's behaviour unchanged; the existing
      held-away draft instruction refused approval with the reason, kept
  [Y] Matrix: every authority × custody cell, every path, allowed / refused
      on identical requests
  [Y] Authority changes: maker-checker, override beats household, history,
      forbidden open proposals withdrawn with the reason
  [Y] Advise: manual proposal → member / other-adviser instructions + letter
      (%PDF, masked), other triggers refused, reconcile from held-away
      record, X-ray one-off trade
  [Y] Managed at another custodian: proposals + harvest → manual DVP
      settlement instructions with details and T+1, never an API, masked PDF,
      staleness applies
  [Y] Org isolation both directions (rolbypassrls = false); gates both ways;
      node tests show no controls the matrix forbids
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no
      existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_rep7.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL; only its TODAY fix changed
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
