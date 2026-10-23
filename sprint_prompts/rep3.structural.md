REP3 — Replication Studio, sprint 3 of 15: model management — one model
registry for every source, Excel/CSV upload with column mapping, a permanent
upload log, a model library (platform + org shelves, copy or subscribe),
multi-sleeve models, approvals on the governed human-task path (and rep1's
terms approvals moved onto it). No accounts, proposals or trades yet. 10
tasks + verification.

Context: spec "Replication Studio — ETF & Fund-to-SMA Spec" (Joe,
2026-10-22), section "Model management". A model can come from three places:
a replicated fund (rep4 builds those), an uploaded file, or the model
library. All land in ONE registry with the same versions, approvals and log,
so the account, tax and fee machinery (rep5+) never cares where a model came
from. Decisions (Joe):
  - Upload .xlsx / .xls / .csv, with a downloadable template; a column-
    mapping step that recognises common headers and is saved per provider.
  - Keep a record of EVERY upload (approved, rejected, abandoned or
    duplicate), exportable for compliance.
  - A drop-down library of models to load as a resource: platform shelf
    (published by admin.hollisworks) and org shelf; copy (independent) or
    subscribe (follows provider updates with a review task).
  - Sleeves: a model can combine other models by weight.
  - Every human action is a workflow-engine task (rep2's human_tasks).
  - Model names: the prefix "Holdings of" is RESERVED for models built from a
    fund (rep4) or a 13F filer (later); uploaded and library models may not
    use it.
  - Custody cliff: approving a model version never trades. It emits a domain
    event that rep5 will turn into rebalance PROPOSALS.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after "merge rep2"; Task 1
confirms exact signatures against the code):
- rep2: services/human_tasks.py — ensure_definition (system definition
  'system:human_task:<permission>' seeded per org, advisory-locked), open and
  complete/cancel functions; the subject rides in workflow_runs.context
  .human_task; maker ≠ checker via complete_user_task's MakerCheckerError and
  the workflow_run_steps_maker_checker CHECK; workflow_steps
  .assigned_permission (nullable) limits completers and notifications; todos
  via workflow_todos.sync_user_task_todos; agents can neither open nor
  complete tasks. Custodian capabilities (public.custodian_capabilities,
  platform, versioned) with a capability() helper where 'unknown' means NOT
  available; trade-adapter map in services/custody/registry.py
  (register_trade_adapter / trade_adapter_for); execution_instructions.
  rbac.ensure_org_admin_role grants the replication and execution
  permissions to a new org's first org_admin.
- rep1: portfolio.replica_securities / replica_terms_versions /
  replica_terms_actions / replica_holdings_* / replica_term_readings; terms
  approval today = replica_terms_actions + domain_events + member_todos for
  approve_replication holders (NOT yet a human task); maker-checker CHECK
  replica_terms_versions_maker_checker_chk; permissions view/manage/
  approve_replication.
- Security master: services/securities_global.py — get_by_identifier,
  get_by_identifiers_bulk (merge-following; cusip/isin/figi/lei unique,
  ticker/sedol not), create_security, add_identifier, add_relationship(s)
  (unresolved → link_state 'unresolved' queue). The ONE resolver; never
  guess a match.
- Fee module: fee_schedules (versioned: DRAFT edited in place, APPROVED
  never mutated, RETIRED); assignments by scope with derived precedence.
- Files: R2 via services/storage (bucket hollisworks-docs); reportlab/pypdf
  pinned. openpyxl / xlrd: Task 1 checks what is installed.
- Menu: apps/web/lib/menuVisibility.mjs, Portfolio group (tabs Replication,
  Instructions).
- authz1: every route gated + AUTHZ1_ROUTES.json (--check CURRENT); never
  change an existing gate; Super Admin first; envelope pattern, no client
  fallback; org_id only from the session; request models extra='forbid'.
- Regression policy: this sprint changes rep1's approval path → re-run
  verify_rep1.py ONCE (after updating ONLY its approval-task checks to the
  human-task path). Scripts print 'TOTAL: N PASS, M FAIL'; accept 'RESULT:'.
- Known test defect to fix here (rep2): verify_rep2.py's CSV-unchanged check
  expects "1 exception" in the importer's dry-run summary, but that summary
  has no exception count (keys: accounts_new/changed/unchanged,
  balances_*, flows_*, unmatched, records_total). Read the exception count
  from where the importer actually reports it; change nothing in the
  importer.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. No network calls. Uploaded files are UNTRUSTED:
never evaluate formulas, never run macros, refuse .xlsm/.xlsb, cap size
(5 MB) and rows (5,000), refuse archives that expand beyond a sane ratio,
and escape spreadsheet-formula prefixes (= + - @ tab CR) in every CSV export.
Never print a secret. Column names from the live DB. Bi-temporal rule 3 for
every new table; RLS policies for EVERY operation each new table will take;
platform rows owner_scope 'platform' with org_id NULL. Decimal for weights
(never float). Labels and vocabularies from the API envelope. Light/cream
UI with the existing tokens. Commit ALL sprint files (new and modified,
excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. rep2's human_tasks API: exact function names and signatures (open,
      complete, cancel, list), the subject fields, how outcomes are returned,
      and how a module reacts when a task completes (callback, event, or the
      caller completing its own subject in the same transaction). Choose the
      pattern this sprint uses and say why.
  1b. rep1's approval path: every place that writes the approval todos and
      completes approval, and verify_rep1.py's checks that depend on it (by
      line). Plan the move to human_tasks with replica_terms_actions kept as
      the domain log.
  1c. Spreadsheet libraries in apps/api/venv (openpyxl, xlrd, pandas?) and
      their versions; what Render's build installs (requirements). Choose
      the parsers (pure Python) and pin them.
  1d. fee_schedules: how to reference an APPROVED schedule from another
      table (id? code + version?) without breaking its versioning, and
      whether an RLS read is possible from model code.
  1e. How the platform org (admin.hollisworks, bb347258-…) and Super Admin
      publish platform-scoped rows today (pattern from rep1/rep2 platform
      tables), so platform library entries follow it.

=== TASK 2: SCHEMA ===
Design from Task 1 (names indicative; models are ORG rows unless stated):
  - models: name, description, source (upload | library_copy |
    library_subscription | fund_replica | composite), status (draft |
    active | retired), base currency, optional default fee schedule
    reference, optional replica_security_id (rep4), library_entry_id when
    copied/subscribed, created_by. Name CHECK refuses the "Holdings of"
    prefix unless source is fund_replica (rep4) — test the refusal.
  - model_versions: model, version_no, status (draft | approved |
    superseded | rejected | abandoned), maker_ids, checker_id, approved_at,
    upload_id (nullable), parent_version_id, notes, cash_target, totals.
    One approved live per model and one open draft per model (partial
    uniques). Approved versions immutable (trigger).
  - model_version_positions: version, row_num, resolved global security
    (nullable) + raw identifiers, target weight (numeric, fraction), asset
    class, sleeve label, drift band min / max, alternates (jsonb list of
    identifiers), restrictions, notes, resolution state (resolved |
    unresolved | cash).
  - model_sleeves (composite models): parent version → child model, weight,
    follow (live | pinned version id). No cycles (enforced in code AND a
    test).
  - model_uploads (the LOG, append-only, never deleted): org, model (nullable
    until known), file name, size, SHA-256, R2 key of the ORIGINAL bytes,
    content type, uploader, uploaded_at, source (manual | provider_email |
    provider_feed | library_publish), provider name, mapping id used,
    sheet name, rows read / resolved / unresolved / cash, validation
    messages (jsonb with row numbers), outcome (draft_created | rejected |
    abandoned | duplicate | failed), resulting version id, duplicate_of
    upload id, decided_by / decided_at / reason. Status changes append
    events (model_upload_events) — the upload row's facts never change.
  - model_column_mappings: org, provider name, header → field map, sheet
    hint, created_by, last_used_at; versioned.
  - model_library_entries: shelf (platform | org), org_id NULL for platform,
    model id (the source model whose approved versions ARE the entry's
    content), title, provider, description, update cadence, licensing note,
    requires_agreement (bool), published_by, status. Platform entries are
    readable by every org; written only by Super Admin.
  - model_subscriptions: subscriber model ↔ library entry, last version
    applied, status.
Apply with scripts/_apply_rep3.py (dealroom2/rep1 pattern) through Doppler,
synchronously; VERIFY it landed (columns, constraints, partial uniques,
triggers, RLS enabled, a policy for every operation). Save
docs/rep3_part1.sql. Grants for app_service like existing tables.

=== TASK 3: UPLOAD AND PARSE (services/model_upload.py) ===
  - Accept .xlsx (openpyxl, read-only, data_only=True — a formula cell with
    no cached value is a validation error, never evaluated), .xls (xlrd),
    .csv (utf-8 / utf-8-sig, sniffed delimiter). Store the ORIGINAL bytes
    in R2 under models/uploads/<org>/<sha256>; same SHA-256 already uploaded
    by this org → a new log row with outcome duplicate + duplicate_of, no
    second object.
  - Sheet choice for multi-sheet workbooks; header row detection (first row
    whose cells match known headers), with an override.
  - Column mapping: auto-suggest from a server vocabulary of synonyms
    (Ticker, Symbol, CUSIP, ISIN, SEDOL, Weight, Target %, Target Weight,
    Allocation, Asset Class, Sleeve, Min, Max, Alternate, Restriction,
    Notes, Cash); a saved mapping for the chosen provider is applied first;
    the user confirms; saving the mapping is a logged action.
  - Weights: accept percent (sums ~100) or fraction (sums ~1) — detect,
    state which, convert to fraction; Decimal throughout.
  - Validation (every message carries its row number):
      weights sum to 100% within ±0.05% (configurable) — a gap goes to cash
      only if the upload marks a cash target or the user chooses "fill with
      cash"; negative weights refused (long/short later); duplicates
      refused; every identifier resolved via the security master (cusip/isin
      first, ticker only when unambiguous; unresolved rows QUEUED, never
      guessed — an upload with unresolved rows can be saved as draft but a
      version with unresolved rows cannot be approved); drift bands within
      [0, 1] and min ≤ target ≤ max; alternates resolved the same way.
  - Tradability flags per custodian the org uses (capability()):
      informational on the version (e.g. "fractional_shares unknown at
      altruist"), never blocking.
  - Result: a DRAFT version (new model or new version of an existing model)
    with a diff against the live version (added, removed, weight changes
    over a threshold, total turnover), and the upload log row.
  - A downloadable template (.xlsx and .csv) generated from the server's
    field vocabulary.

=== TASK 4: VERSIONS AND APPROVAL ===
  - Draft edits in place (positions editable in the UI with the same
    validation); every edit logged and the editor joins maker_ids.
  - Submit → human task (approve_models; the checker is never a maker).
    Approve → version approved, previous approved → superseded, model
    active; emits domain event 'model.version_approved' with the diff
    (rep5 will create proposals from it — nothing else happens now).
    Reject (note required) → back to draft. Abandon a draft → logged, the
    upload outcome becomes abandoned.
  - Approved versions never change (trigger + 409).
  - Composite models: the effective look-through positions of a version are
    computed (sleeve weight × child live/pinned weights, merged by
    security) for display and for rep5; a child's new approved version
    creates a draft of every composite that follows it live, with a review
    task.

=== TASK 5: LIBRARY ===
  - Publish: Super Admin publishes a PLATFORM entry from a platform-org
    model; an org admin with manage_models publishes an ORG entry from an
    org model. Unpublish/retire logged.
  - Drop-down: the Models screen's "Load from library" lists both shelves
    with provider, cadence, last updated, licensing note, requires-agreement
    flag and usage count.
  - Copy: creates an independent org model (source library_copy) with a
    DRAFT version copied from the entry's live approved version; normal
    approval follows.
  - Subscribe: creates an org model (source library_subscription); each new
    approved version of the entry's model creates a DRAFT version in every
    subscriber with a review task in that org; the subscriber still
    approves (maker = system import, checker = an org approver).
  - requires_agreement = true → copy/subscribe needs an "agreement on file"
    confirmation by an org admin (logged); without it the action is refused.
  - Tenant isolation: an org never sees another org's org-shelf entries or
    models; everyone sees platform entries (read-only).

=== TASK 6: MOVE REP1 APPROVALS TO HUMAN TASKS ===
rep1's terms approval opens and completes a human task (approve_replication)
instead of writing member_todos directly; replica_terms_actions stays the
domain log; existing rep1 behaviour (maker-checker refusal text, 409s,
supersede) unchanged. Update verify_rep1.py ONLY where it asserts the old
todo path (lines from 1b) so it asserts the human-task path. Also fix the
verify_rep2.py CSV-summary check described in CONFIRMED FACTS.

=== TASK 7: API, PERMISSIONS, EXPORTS ===
New permissions view_models, manage_models, approve_models — granted to
org_admin by default and added to rbac.ensure_org_admin_role. Platform
library writes: Super Admin. Routes under /api/v1/models/… (list, detail,
versions, diff, positions edit, upload start / mapping / confirm, template
download, upload log list + CSV export, library list / publish / copy /
subscribe / agreement confirm, sleeves edit), each gated and in
AUTHZ1_ROUTES; envelopes with permissions and vocabularies (sources,
statuses, mapping synonyms, validation codes, cadence values).
Upload log CSV export: every row, formula-prefix escaped, gated
view_models.

=== TASK 8: SCREENS ===
Portfolio group → new tab "Models" (gated view_models):
  - Models list: name, source, live version, status, positions, last change,
    sleeves; filters; Upload model and Load from library buttons (manage
    only).
  - Upload wizard: file → sheet → header row → column mapping (suggestions,
    provider name, save mapping) → validation report (row-level messages,
    unresolved rows highlighted, tradability notes) → create draft.
  - Model page: positions table (sortable; resolved flag; band; sleeve),
    versions with diff view, sleeves editor for composites (weights must sum
    to 100%), Submit / Approve / Reject (approve only for approve_models and
    never for a maker), the model's upload history.
  - Library page: Platform shelf and Our shelf; copy / subscribe with the
    agreement confirmation where flagged.
  - Upload log page: every upload with outcome, filters, CSV export.
  - Template download links.

=== TASK 9: REAL PROOF ===
  - Upload (fixture files built in the test): an .xlsx, an .xls and a .csv
    of the same model produce identical draft versions; percent vs fraction
    detected; a formula cell without a cached value → row error, never
    evaluated; .xlsm refused; oversize and over-row refused; a CSV with a
    BOM parses; weights 99.0% → error unless "fill with cash"; negative
    weight and duplicate refused; an unresolvable CUSIP is queued, never
    matched by ticker to a decoy; same file twice → duplicate log row, one
    R2 object.
  - Mapping: synonyms auto-suggested; a saved provider mapping is applied
    on the next upload from that provider (re-read).
  - Upload log: every outcome (draft_created, rejected, abandoned,
    duplicate, failed) recorded with row-numbered messages; facts never
    change after the event (UPDATE refused); CSV export escapes =cmd cells.
  - Versions: diff exact (added / removed / weight moves / turnover);
    maker's approve refused on the identical request a checker succeeds
    with; unresolved rows block approval; approve supersedes; event
    'model.version_approved' emitted with the diff; approved immutable
    (trigger + 409); reject needs a note; abandon logged.
  - Names: "Holdings of X" refused for upload / library models.
  - Sleeves: look-through weights exact; cycle refused; a child's new
    approved version creates a draft + review task in each live-following
    composite, none for a pinned one.
  - Library: platform entry visible to both orgs, editable by neither;
    org entry invisible to the other org; copy is independent (a later
    entry change does nothing); subscribe creates a draft + task on each new
    entry version; requires_agreement blocks until confirmed (logged).
  - rep1 approvals now open/complete human tasks; rep1's maker-checker text
    and 409s unchanged.
  - Org isolation both directions under app_service with rolbypassrls =
    false; gates both ways on identical requests; node tests render no
    Upload / Approve / Publish / Edit controls without the flags.
  - New org's first org_admin gets view/manage/approve_models (re-read).
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep1.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    1200 s timeout.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (model registry, upload
log, library, sleeves, events for rep5), docs/HUMAN_TASKS.md (rep1 + rep3 as
users), and add verify_rep3.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep3.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. Mock R2;
no network calls. Wrap every section so an exception is a [FAIL]; teardown at
start and end in FK child-before-parent order from pg_constraint, deleting
ONLY rows this run created, by fixture id (never walk the FK graph from a
broad parent); clear audit_log, domain_events, member_todos, workflow
runs/steps/definitions/versions created by this run before fixture users;
reconnect once and re-count if a connection drops (a dropped connection is
never "clean"); then re-read every touched table for its exact pre-test
count. Fixture users created explicitly with the auth0_sub the mocked token
carries, is_test = true, using the users-RLS pattern from verify_rep1/2.
Read every UNIQUE (including partial unique indexes), CHECK and FK constraint
on the touched tables from the catalog before seeding and seed to satisfy
them. Fixture securities use a reserved prefix that matches no real row
(assert it).

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; it must be a real bool
    (wrap any `a and b` that could return a list in bool(...)).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Exclude this run's own fixtures from any "no leftovers" check.
  - Never spawn a background process. The regression subprocess gets
    VERIFY_NESTED=1, runs after teardown, 1200 s timeout; with
    VERIFY_NESTED=1 this script skips its regression section with one
    '[SKIP] nested run' line.
  - Every import the script uses is at the top (pathlib.Path included).
  - Never touch real models, securities, fee schedules, workflow rows or
    users: count + checksum of real rows before and after.

Assertions:
  [Y] Task 1 findings 1a–1e reported with files, signatures and the chosen
      patterns
  [Y] Schema landed: tables, constraints, partial uniques, triggers, RLS
      with a policy for every operation
  [Y] Upload: xlsx = xls = csv; untrusted-file rules; validation messages
      with row numbers; unresolved queued never guessed; duplicates logged
  [Y] Mapping suggestions and saved provider mappings
  [Y] Upload log complete for every outcome, immutable facts, safe CSV
      export
  [Y] Versions: exact diff; maker-checker on the identical request;
      unresolved blocks approval; supersede; event emitted; immutability
  [Y] "Holdings of" reserved
  [Y] Sleeves: look-through, no cycles, follow-live drafts + tasks
  [Y] Library: shelves, visibility, copy vs subscribe, agreement gate
  [Y] rep1 approvals on human tasks, behaviour unchanged; verify_rep2 CSV
      check corrected
  [Y] Org isolation both directions (rolbypassrls = false); gates both ways;
      node tests show no write/approve/publish controls without the flags
  [Y] New org's first org_admin gets the three model permissions
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no
      existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_rep1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
