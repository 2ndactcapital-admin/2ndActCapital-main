AIBUDGET1 — daily model budgets on the ensemble picker and a daily
document-reading run that stays inside them: when the super admin assigns
models to a job (Model 1, Model 2), each model gets a budget picked right
after the model — "unlimited" or a daily token allowance (or a daily dollar
amount); the document-reading jobs then run every day on their queue and
stop cleanly when either model's budget for the day is used up, resuming the
next day. Built for OpenAI's complimentary daily tokens on shared (public)
traffic; vendor-neutral. 7 tasks + verification.

Context: Joe (2026-11-03): "when assigning models to jobs, for this document
reading job, that is where you pick the budget after you pick the model for
both models. An option is 'unlimited'." OpenAI gives some organisations
daily complimentary tokens on API traffic shared with OpenAI (help article
10306912; the amounts are not published and have moved — in March 2026 the
settings screen showed up to 1M tokens/day for gpt-5.4 and older, up to 10M
for the mini group; users reported 250k / 2.5M). So the budget is a number
the super admin types from what OpenAI's dashboard shows — the code never
assumes the free amounts. Only PUBLIC documents may go to a sharing model
(OpenAI may use that traffic).

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main; Task 1 confirms exact
signatures):
- services/ai_ensembles.py (ensemblesystemone): one active config per
  task_key, platform-wide; KNOWN_TASK_KEYS = note_terms_hazard,
  note_terms_extraction, replica_terms_extraction, statement_positions;
  slots Model 1, Model 2 (catalog model_id = proxy deployment name; option
  selectable only if catalog availability 'available' AND the proxy reports
  mode 'chat') and System One; configs are IMMUTABLE (trigger
  ai_ensemble_configs_retire_only) — a change is a new row, the old one
  retired in the same transaction; activate_ensemble is the only write;
  *_version columns snapshot each slot. Routes: GET / POST
  /admin/ai/ensembles (super_admin) in routers/ai_ensembles.py; UI
  web/components/admin/EnsemblePanel.jsx.
- public.ai_ensemble_configs has 0 rows today (no job has models assigned).
- public.platform_model_catalog: model_id, provider, availability,
  public_data_only, manual_input_cost_per_mtok, manual_output_cost_per_mtok.
  Rows: gpt-5-mini and gpt-5-nano (openai, available, public_data_only =
  true); deepseek-v3.2, gpt-oss-120b, mistral-small-24b (deepinfra,
  available); claude-haiku, claude-sonnet (anthropic); qwen2.5-7b,
  gemini-2.5-flash-lite (disabled); voyage-3.5 (embedding).
- The public-data guard exists (notefields): extraction.py ~808-824
  PUBLIC_DATA_TASK_KEYS, public_data_only_model_ids,
  refuse_public_data_only — a public_data_only model is refused for a task
  that is not public-data. replica_terms.py refuses Claude models before and
  after the call; note_extraction/prefill.py refuses Claude and OpenRouter.
- Readers: replica_terms.active_ensemble(conn) (task
  replica_terms_extraction) and note_extraction/cascade.active_ensemble(conn,
  task_key); per-call rows portfolio.replica_term_readings and
  portfolio.note_term_readings carry deployment_name, input_tokens,
  output_tokens (note readings also cached_tokens), cost_usd, created_at,
  run ids; portfolio.note_extraction_runs (spend_cap_usd, spent_usd, status,
  stop_reason incl. 'stopped_spend_cap').
- services/note_extraction/spend.py: the per-run DOLLAR cap reserves each
  call's estimated cost first (prompt chars / 4 + max_tokens ceiling) and
  stops cleanly before a call would cross it; replaced by the recorded cost
  afterwards. Reuse this reserve-then-record pattern for tokens.
- Scheduler: workflow_scheduler_tick.py; sweeps skip is_test orgs. The
  live Render scheduler runs main's code.
- vhk1: apps/api/scripts/_verify_self.py (own_routes, still_gated,
  when_empty); every verify supports --self-checks-only and --cleanup-only;
  teardown via scripts/_verify_cleanup.py; docs/VERIFY_RULES.md.
- Regression: this changes the ensemble picker → re-run
  verify_ensemblesystemone.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s. It predates vhk1: if it fails on a stale "since a base commit"
  self-check (not on behaviour), pin that check with _verify_self and say
  which line — never weaken a behaviour assertion.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any full verify script (--self-checks-only / --cleanup-only
  MAY be run).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground. Write the verify script but DO NOT RUN IT. NO real model calls
anywhere in the sprint or the verify (a fake proxy / injected caller only).
Never print a secret, an API key or a key's prefix. Column names from the
live DB. RLS / grants for every operation a new table takes. Decimal for
dollars; integers for tokens. A budget is never exceeded by a call: reserve
first, record after. Super Admin only. Light/cream UI with the existing
tokens; labels and vocabularies from the API envelope. Commit ALL sprint
files (excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Which jobs read documents today and how a run starts: replica terms
      (prospectuses), note terms (424B2 / FWP), statement_positions
      (member statements — NOT public); whether each reads the active
      ensemble now; how many documents are queued for each (counts only).
  1b. Where tokens are recorded per call (readings tables; any LiteLLM spend
      log the app reads) and whether every call path records input / output
      tokens — a path that does not cannot be budgeted: list it.
  1c. The day boundary: OpenAI's complimentary allowance resets daily — state
      the boundary you implement (UTC midnight unless Task 1 finds evidence
      otherwise) and show it on the screen.
  1d. Which proxy deployment / provider project each OpenAI catalog model
      uses (names only, never keys) and whether it is the data-sharing
      project; report, do not change proxy config.
  1e. The picker's current request / envelope shape and EnsemblePanel's
      layout, so the budget control sits immediately after each model
      select.

=== TASK 2: SCHEMA ===
Design from Task 1 (names indicative):
  - The budget is part of the assignment: on ai_ensemble_configs (new
    columns model_1_budget_kind, model_1_budget_value, model_2_budget_kind,
    model_2_budget_value) — kind unlimited | tokens_per_day | usd_per_day;
    value NULL iff unlimited, positive otherwise (CHECK). Configs stay
    immutable: changing a budget activates a new config row (same models,
    new budget) through activate_ensemble, the old row retired — say if
    Task 1 shows a separate versioned table is cleaner, and why.
  - ai_daily_usage (or a view over the readings tables if Task 1 shows every
    path records tokens): per model deployment per budget day — tokens in /
    out, cost, reserved — the single source the budget check reads.
  - ai_daily_runs: per task per day — status running | finished_queue_empty
    | stopped_budget (which model) | stopped_error; documents done; tokens /
    cost per model; started / finished.
Apply with scripts/_apply_aibudget1.py through Doppler, synchronously;
VERIFY it landed; save docs/aibudget1_part1.sql.

=== TASK 3: THE PICKER ===
  - After each model select: a budget control — Unlimited (default for a
    model with no public_data_only flag) | Tokens per day | Dollars per day;
    the number entered as a whole token count or dollars; the envelope
    carries the vocabularies and, for each model, today's usage across
    ALL jobs (the provider's allowance is shared by every job that uses the
    model) and the day boundary.
  - Semantics stated on screen: "this job stops for the day when <model> has
    used <budget> today across Hollisworks".
  - Validation: positive value for a limited kind; a public_data_only model
    is selectable only for a public-data job (the existing guard — the
    picker refuses it for statement_positions with the reason).
  - The history list shows each config's budgets.

=== TASK 4: ENFORCEMENT AT THE CALL ===
  - Every call made for an ensemble job, through the existing call path,
    reserves its estimated tokens (prompt chars / 4 + max_tokens) and
    dollars against the model's usage for the budget day; if the
    reservation would exceed a limited budget the call is not made and a
    BudgetReached (model, kind, budget, used) stops the run cleanly; after
    the call the reservation becomes the recorded usage. Unlimited = no
    check (the existing per-run dollar cap still applies when a run sets
    one).
  - Both models must be within budget: an ensemble compares two readings,
    so when either model is exhausted the job stops — never a one-model
    reading (stated).
  - Concurrent runs cannot overspend: the reserve is atomic (row lock or
    a single UPDATE … RETURNING).

=== TASK 5: THE DAILY RUN ===
  - A scheduler task (daily, after the budget day starts; skip is_test
    orgs) runs each public-data document job with an active config and a
    non-empty queue: replica_terms_extraction and note_terms_extraction
    (statement_positions never runs automatically — member data, triggered
    by its own flow only). It reads documents in the job's existing queue
    order until the queue is empty or a budget stops it; the next day
    resumes where it stopped (no document read twice unless the job's own
    rules re-read).
  - One run per job per day (unique live row); a run already going is not
    started twice.
  - Admin screen: today's run per job (status, documents done, tokens and
    cost per model, which budget stopped it), the last 30 days, and a "run
    now" button (same rules, same budget).

=== TASK 6: REAL PROOF ===
  - Picker: budgets saved per model; unlimited stores NULL value; a bad
    value refused and nothing written; a budget change makes a new config
    row and retires the old (immutability trigger intact); a
    public_data_only model refused for statement_positions; super_admin
    only, both ways on identical requests.
  - Enforcement with a FAKE caller (no network): tokens_per_day 1,000 with
    calls of ~300 → the 4th call is never made, run stopped_budget naming
    the model; usd_per_day likewise; unlimited runs to the queue's end;
    usage counted across jobs (job A's usage counts against job B's budget
    on the same model); two concurrent runs cannot exceed the budget
    (asyncio.gather of two runs → total ≤ budget).
  - Day boundary: usage from the previous budget day does not count (rows
    dated at the call, not module start).
  - Daily run: one per job per day; resumes the next day at the next
    document; statement_positions never scheduled; is_test orgs skipped;
    "run now" obeys the same budget.
  - Routes: this sprint's own via _verify_self.own_routes(BEFORE_REF,
    "aibudget1") + still_gated; authz1_routes.py --check CURRENT.
  - Regression: verify_ensemblesystemone.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 7: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md; docs/LITELLM_INTEGRATION_DESIGN_V1.md (budgets, the
day boundary, the shared-allowance rule, the public-data rule for sharing
models, operator steps: enable sharing on the OpenAI project, read the
dashboard's daily amounts, enter them as budgets, assign models to the two
document jobs); add verify_aibudget1.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_aibudget1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. NO model
calls and no network: the caller is injected / faked. Wrap every section so
an exception is a [FAIL]; teardown via scripts/_verify_cleanup.py at start
and end, by fixture id; re-read every touched table for its exact pre-test
count. Any ensemble config the verify activates must be restored: record the
active row per task before (none today) and leave exactly that state after.
Compute every expected date at the call. Fixture orgs / users is_test = true
(assert it). Read UNIQUE / CHECK / FK constraints from the catalog before
seeding. Support --self-checks-only and --cleanup-only. The regression
subprocess gets VERIFY_NESTED=1, runs after teardown, 2700 s timeout; with
VERIFY_NESTED=1 this script skips its regression section with one
'[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - "One live row per X per day": count live rows, not one run's rows.
  - A failing check's detail prints the response body / culprit rows.
  - Never touch real readings, runs, filings or catalog rows: count +
    checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Schema landed; CHECKs; immutability intact
  [Y] Picker budgets (unlimited / tokens / dollars), validation, history,
      public-data refusal, super_admin both ways
  [Y] Enforcement: stop before the crossing call, both kinds, unlimited,
      shared across jobs, concurrency safe, day boundary
  [Y] Daily run: one per job per day, resume, statement_positions never
      scheduled, is_test skipped, run now
  [Y] Own routes gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_ensemblesystemone.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown: zero fixture rows; the ensemble state exactly as before;
      real rows unchanged
