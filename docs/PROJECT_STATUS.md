# Project Status — open blockers and tracked follow-ups

Last updated: 2026-08-28 (Fee module fee35 — calculation engine)

## About this file

This file records work that is **blocked on something outside the codebase** —
an AWS console change, a vendor contract, a credential only Joe can provision —
so that a blocked item is tracked in one place instead of living in a code
comment that the next sprint deletes.

**A note on this file's own history, since it matters for how much to trust
older references to it:** several earlier sprints
(`verify_litellmreloadaction.py`, `verify_portfolioc.py`, `verify_portfolioux1.py`,
`verify_superadminmenu.py`) state that a follow-up was "recorded as a tracked
follow-up in docs/PROJECT_STATUS.md". **The file did not exist** — it was never
committed and git shows no deletion. Those sprints' follow-ups are therefore
*not* recorded here yet and have not been back-filled by this sprint. If you are
looking for one of them, it is in that sprint's verify script and log, not here.
This file starts with the email item below.

---

## 000. Fee module fee35 — calculation engine BUILT, three decisions owed by fee36 (2026-08-28)

**Status: built and verified, 22/22 PASS, 0 BLOCKED, 9 FIND.**
`apps/api/scripts/verify_fee35.py` — a pure unit suite that opens no database
connection and needs no credentials. `services/fee_calc.py` (the pipeline) and
`services/fee_calc_inputs.py` (the plain-data contracts). Nothing here is
blocked outside the codebase; the three items below are real decisions fee36
must make before a single invoice is produced.

1. **`fee_credits` has no amount column.** Its only numeric column is
   `offset_pct`, confined to `[0,1]`. A credit of "50% of the SPV management
   fee" has nowhere to record what the SPV management fee *was*.
   `CreditInput.basis_amount` is therefore a **required, caller-supplied**
   field with no column behind it. **fee36 must decide where that number comes
   from** — a `fee_runs` input, a second lookup, or a new column on
   `fee_credits`. Defaulting it to zero would make every credit silently
   worthless, which is why the engine refuses to construct a credit without it.

2. **`fee_discounts.value` has no scale and no CHECK constraint.** A `PCT_OFF`
   of `20` and one of `0.20` differ by 100x and both satisfy the column. The
   engine reads `PCT_OFF` as a **percent in [0,100]** and refuses anything
   outside that range. Note the deliberate contrast with
   `fee_credits.offset_pct`, which the deployed constraint confines to `[0,1]`
   — two adjacent tables express a proportion on two different scales and
   nothing in the schema says so. **A CHECK constraint on
   `fee_discounts.value`, scoped by `discount_type`, is the durable fix** and
   is not applied yet.

3. **No holiday calendar exists anywhere in this codebase.**
   `proration_method='BUSINESS_DAYS'` is a deployed, valid value and is
   currently calculated on a plain Mon–Fri count. A market holiday inside the
   period is counted as a business day, overstating the denominator and
   slightly understating a partial-period fee. The engine declares this in
   every affected result's `assumptions`, so it will appear on the fee line
   rather than only in a docstring — but a real NYSE calendar is owed before
   any client is billed on a BUSINESS_DAYS schedule.

**Two smaller things fee36 inherits rather than owes.** `POSITION_TAG` is a
deployed `basis_type` but `portfolio.positions` has no tag column (tags live
in `portfolio.udf_values`), so `PositionInput.tags` is caller-supplied; and
`accounts` has no `billing_group_id` (membership is `billing_group_members`),
so a `BILLING_GROUP`-scoped `minimum_fee` needs the caller to resolve it —
a missing one raises `GroupScopeMissingError` rather than silently degrading
to an account-scoped minimum.

**One bug this sprint's own suite caught and fixed.** An `ASSET_CLASS`
exclusion cannot use `startswith`. Under Rule 4's key scheme
`taxonomy_mc_3_2` is a child of `taxonomy_sc_3` and is *not* a string prefix
of it, while `taxonomy_sc_30` *is* a string prefix and is an unrelated class —
so prefix matching was wrong in both directions at once. Keys are now parsed
into numeric components and compared component-wise (`taxonomy_covers`), with
both directions asserted.

**Deliberately not built:** anything that writes (`fee_runs`/`fee_run_lines`
is fee36), any resolution of *which* schedule/exclusions/discounts/credits
apply (fee32/fee34 own that; the engine consumes their output), and SPV
carry/waterfall, which the design doc defers to its own sprint.

---

## 001. Fee module fee34 — schedule catalog BUILT, four follow-ups owed (2026-08-27)

**Status: built and verified, 49/49 PASS, 0 BLOCKED, 3 FIND.**
`apps/api/scripts/verify_fee34.py`. Nothing here is blocked on anything outside
the codebase — the four items below are real, deliberate gaps a later fee
sprint has to close, recorded so they are not rediscovered as bugs.

### What now exists

- `services/fee_validation.py` — pure, zero database access. Tier contiguity,
  the `minimum_fee`/`minimum_fee_scope` pair, the `REDUCED_RATE`/`FLAT`
  exclusion rules, non-empty `reason`, `approved_by`, and the
  `ordering_policy` permutation. fee35 must **re-run this module**, never
  re-implement the checks.
- `services/fee_schedules.py` — create (always DRAFT v1), edit, submit,
  retire, assign, end, and precedence resolution.
- `routers/fee_schedules.py` — registered in `main.py` at
  `/api/v1/fee-schedules`.

**Part 1 was genuinely applied this time.** Confirmed live on the app's own DSN
before any code was written (`scripts/discover_fee34.py`), not on the MCP
endpoint alone. This is worth stating because fee33's prompt carried the
identical "already applied by Joe" sentence and it was **not** applied.

### Follow-ups owed

1. **`fee_assignments` has NO unique index.** Nothing in the database stops two
   active assignments on the same `scope_id` with overlapping effective dates,
   which would make precedence ambiguous *within* a rung.
   `create_assignment(replace_existing=True)` closes the incumbent first, so the
   application never creates one — but an import path or a manual SQL fix can.
   A partial unique index on `(org_id, scope_type, scope_id) WHERE valid_to IS
   NULL AND system_to IS NULL` would close it properly.
2. **No CRUD for `fee_exclusions` / `fee_discounts` / `fee_credits`.**
   Deliberately out of scope for fee34, which builds the catalog only. The
   validators for all three exist and are tested; the write paths do not. A
   later sprint must call `validate_exclusion` / `validate_discount` /
   `validate_credit` at those rows' own write time — they are **not** reachable
   from the schedule-approval gate (see finding 3 below).
3. **`ENTITY` and `ORG` scopes are unreachable on three of the six tables.**
   `fee_exclusions.scope_type` admits `ORG` (not `ORG_DEFAULT`) and no `ENTITY`;
   `fee_discounts` and `fee_credits` admit neither. Three different scope
   vocabularies, kept as three constants in `fee_validation.py` precisely so
   they cannot be collapsed into one. If the fee engine needs an entity-level
   discount, that is a schema change, not a code change.
4. **Nothing reads a schedule to produce a dollar.** By design — that is fee35.

### The three findings

1. `fee_schedules_code_version_uq` is `UNIQUE (org_id, code, version)` with **no
   partial predicate**, so a Rule 3 valid-axis restatement of a schedule is
   impossible: closing a row and re-inserting the same `(code, version)`
   collides with the row just closed. Versioning goes through `version+1` and a
   DRAFT edit is an in-place `UPDATE`. Not a style choice.
2. `fee_assignments.precedence` is `NOT NULL` with no default and **no tie to
   `scope_type` anywhere in the database**. A body carrying `precedence: 1` on
   an `ORG_DEFAULT` assignment would outrank every account-specific agreement in
   the org, silently. It is derived server-side from `scope_type` and the
   request model declares no such field.
3. **The exclusion rules are not reachable from the schedule-approval gate, and
   the fee34 prompt assumes they are.** `fee_exclusions` has no
   `fee_schedule_id` — only `alt_fee_schedule_id`, the REDUCED_RATE *target*.
   There is no join path from a schedule to "its" exclusions because a schedule
   does not have any. Folding them into `validate_schedule` would have produced
   a gate that always passes vacuously on an empty list.

---

## 00. LiteLLM Phase B — routing BUILT, three real blockers to a first success (2026-08-26)

**Status: built and verified, 68/68, with 5 BLOCKED.**
`apps/api/scripts/verify_litellmphaseb.py`. The routing change is complete and
the transport is proven against the live proxy. What is blocked is a *successful*
generation, and it is blocked on three things outside the codebase.

### What now exists

- `services/extraction.py` — the platform's single AI chokepoint — now sends its
  HTTP calls to the self-hosted LiteLLM proxy instead of straight to Anthropic.
  It is a **transport swap at one function** (`_build_ai_client`): the fallback
  chain, retry walk, cost model, `ai_decision_log` writes and error handling are
  untouched, and `ai_decision_log` gained no columns.
- **How, and why this way:** the Anthropic SDK is *pointed at* LiteLLM's base URL
  rather than replaced. LiteLLM serves a real Anthropic-shaped
  `POST /v1/messages` (confirmed live). Keeping the SDK means the response
  objects reaching every `extract()` closure and `_compute_cost` stay genuine
  Anthropic types, so `message.content[0].text`, `stop_reason`,
  `block.model_dump()` and `usage.input_tokens` all keep working unchanged. The
  OpenAI-shaped `/v1/chat/completions` route is *also* live and was measured, but
  using it would have required hand-writing an OpenAI→Anthropic response adapter
  (including `tool_calls`→`tool_use`) on the most load-bearing path in the
  platform. That is a rewrite, not a transport swap.
- **`LITELLM_ROUTING_DISABLED=1` — a real, tested rollback path.** Set it and
  every AI call goes straight back to Anthropic, never contacting LiteLLM.
  Verified both ways: the client's real `base_url` becomes `api.anthropic.com`,
  and **zero rows appear in LiteLLM's own spend log** after waiting the full
  flush window. It is an **environment variable, not an org_settings key**, on
  purpose — it must keep working when the database is the unhappy thing, and an
  `org_settings` read would need a working DB to report that the DB-independent
  fallback is on. This is **not** design §7.5's future per-org `force_anthropic`.
- **A wrong master key now fails loud.** `AILiteLLMAuthError` names the variable,
  the endpoint, the HTTP status and the remedy, is still recorded in
  `ai_decision_log`, and does **not** walk the chain (every model shares the one
  key). It deliberately propagates through all three `call_claude_*` wrappers
  instead of being flattened into their usual `None`.

### GAP CLOSED — `LITELLM_BASE_URL` now exists in Doppler

The item below (and `render.yaml`'s note) recorded `LITELLM_BASE_URL` as the one
`LITELLM_*` variable genuinely missing from Doppler. **It was still missing, and
this sprint added it** to `hollisworks/prd`, pointing at the live
`https://hollisworks-litellm.onrender.com`. Verified by re-reading it back.

### ACTION REQUIRED — three blockers to a first successful call

None of these are code. All three need console access.

1. **The proxy has ZERO model deployments.** `GET /v1/models` returns
   `{"data":[]}`; `GET /model/info` returns HTTP 500 *"LLM Model List not loaded
   in"*. LiteLLM cannot route any model name. Corroborated independently: **every
   row LiteLLM has ever written to its own spend log is `status=failure`** — the
   proxy has never successfully served a single call.
2. **Doppler's `LITELLM_MASTER_KEY` is NOT the proxy's master key.** LiteLLM
   reports `role=internal_user` and refuses `POST /model/new` with HTTP 403
   *"only if you are a PROXY_ADMIN"*. It is a virtual/internal-user key. So this
   sprint could not fix blocker 1 either. **This also corrects the note below:**
   adding `LITELLM_BASE_URL` does *not* by itself unblock
   `litellm.reload_model_cost_map` — that admin endpoint needs PROXY_ADMIN, so it
   will still fail, now with a 403 rather than a `LiteLLMConfigError`.
3. **`ANTHROPIC_API_KEY` exists nowhere** — not in Doppler `prd` (all 35 secret
   names enumerated), not in `~/.bashrc`, not in `apps/api/.env`. Neither
   LiteLLM's upstream nor the direct-Anthropic rollback path has a provider
   credential. AWS Bedrock is not an alternative: the existing `AWS_*` creds are
   the Textract-only IAM user and `bedrock:ListFoundationModels` returns
   `AccessDeniedException`.

**Order to unblock:** obtain the real PROXY_ADMIN master key (2) → store a
provider key (3) → register a model deployment (1) → re-run
`verify_litellmphaseb.py`, which will convert the 5 BLOCKED items to PASS.

### What IS proven, against the live proxy

- Requests genuinely reach LiteLLM. The error text returned — *"Invalid model
  name passed in model=…"* — is generated by LiteLLM itself; neither our code nor
  `api.anthropic.com` emits that string.
- The fallback chain still walks every model, in order, via LiteLLM, proven with
  a real forced-failure primary.
- **Dual visibility is real.** The same call appears in *both* `ai_decision_log`
  and `litellm."LiteLLM_SpendLogs"`, naming the same models in the same order and
  agreeing on the outcome. **Measured, and it matters: LiteLLM flushes that table
  asynchronously, seconds after answering.** A before/after count taken around
  the call sees nothing — an earlier draft of the verify script reported a false
  negative for exactly this reason. Any assertion about that log, presence *or*
  absence, must poll across the flush window.
- `ai_decision_log`'s shape is unchanged, compared column-by-column and
  type-by-type against a real pre-sprint row.

### Deploy note

`LITELLM_ROUTING_DISABLED` is **not** declared in `render.yaml` — it is an
break-glass switch, and a declared-but-unset variable invites someone to set it
permanently. Set it directly in the Render dashboard if the proxy misbehaves.
Until blockers 1–3 clear, production is on the **degraded** path: LiteLLM is
configured, so calls route to it, and they will fail. **If Phase B is deployed
before those blockers clear, set `LITELLM_ROUTING_DISABLED=1` in Render at the
same time** — but note blocker 3 means direct Anthropic has no key either, so AI
features are non-functional in production regardless, exactly as they were
before this sprint.

---

## 0. Workflow scheduler — core engine BUILT (2026-08-26)

**Status: built and verified, 65/65.** `apps/api/scripts/verify_schedulercore.py`.
Not blocked on anything. Recorded here because it closes two items this file's
predecessors kept referring to, and because it opened one new deployment action.

### What now exists

- **`workflow_triggers.schedule_cron` is no longer dead code.** Before this
  sprint a repo-wide grep found exactly two readers — a `SELECT` in
  `routers/workflows.py` and a display cell in `WorkflowTriggerScheduler.jsx`.
  **Nothing fired anything on a schedule.** The one `scheduled` row in the
  database had been inserted by a verify script, because there was no API path
  to create one.
- `docs/schedulercore_part1.sql` adds six columns to `workflow_triggers`:
  `timezone` (IANA, `NOT NULL DEFAULT 'UTC'`), `start_date`, `end_date`,
  `max_occurrences`, `occurrence_count`, `last_fired_at`.
- `services/workflow_schedule.py` — pure recurrence evaluation. Translates the
  stored cron expression into a real `dateutil.rrule` (preserving cron's
  day-of-month **OR** day-of-week semantics, which rrule ANDs) and resolves it
  in the trigger's **own** timezone.
- `services/workflow_scheduler.py` — the firing loop. Scans all orgs, evaluates,
  checks workflow-level overlap, claims the occurrence atomically, and fires
  through the **real** `workflow_engine.start_workflow_run`.
- `apps/api/workflow_scheduler_tick.py` — the minimal process entrypoint.
- `POST /admin/workflow-triggers` now accepts `trigger_type='scheduled'` plus
  the recurrence fields, with the cron expression and IANA zone validated at the
  boundary. The pre-existing three-field event body is unchanged and still works.

**Per-org timezone lives in our code, not in Render.** Render cron schedules are
UTC-only and cannot be made timezone-aware. The service ticks every 5 minutes in
UTC and each trigger's local schedule is resolved in Python. The 5-minute cadence
and the evaluator's 60-minute lookback window are a matched pair — change one and
re-check the other.

### `render.yaml`'s LiteLLM section — CORRECTED

The blueprint asserted that "the LiteLLM proxy is NOT DEPLOYED … there is no
LiteLLM service in this blueprint, and Doppler holds no `LITELLM_*` secret."
**Both halves were out of date.** `hollisworks-litellm.onrender.com` answers HTTP
200 on `/health/liveliness`, and Doppler holds four `LITELLM_*` secrets against a
migrated 77-table `litellm` schema. `LITELLM_BASE_URL` and `LITELLM_MASTER_KEY`
are now declared on the API service. **`LITELLM_BASE_URL` is still absent from
Doppler**, which is why `litellm.reload_model_cost_map` still raises
`LiteLLMConfigError` even though the proxy is up — see item 2.

> **SUPERSEDED by item 00 (LiteLLM Phase B, same day).** `LITELLM_BASE_URL` has
> now been added to Doppler `prd`. And the inference in the last sentence was
> wrong: adding it does **not** unblock `litellm.reload_model_cost_map`. The
> stored `LITELLM_MASTER_KEY` is an `internal_user` virtual key, not the proxy's
> PROXY_ADMIN master key, so that admin endpoint still fails — now with HTTP 403
> instead of `LiteLLMConfigError`.

### ACTION REQUIRED — deploy the cron service

`render.yaml` now declares a third service, `2ndactcapital-workflow-scheduler`
(`type: cron`, `plan: starter`, `schedule: "*/5 * * * *"`). **Until the blueprint
is applied in Render, nothing fires in production** — the engine is built and
proven, but no process is running it. A Render cron job has no free plan and a
**$1/month minimum**, billed by the second of active runtime.

Also unresolved and recorded rather than papered over: the `hollisworks-litellm`
service is live but is **not** declared as a block in `render.yaml`. It was
created outside the blueprint, so adopting it is a migration, not an edit. That
is a real remaining gap in this manifest's coverage.

### Two bugs this sprint found and fixed

**1. A held run vanished entirely when started through the real pool.**
`workflow_engine.start_workflow_run` documents that the run row is persisted "in
their own committed transaction … rather than vanishing on rollback". It was not.
`services.database._RLSPool.acquire()` opens an **outer** transaction (that is
how the RLS `SET LOCAL` GUCs are scoped), so asyncpg nested the engine's
`conn.transaction()` as a **savepoint**. When `start_workflow_run` re-raised
after holding, the exception escaped `pool.acquire()`, the outer transaction
rolled back, and the `workflow_runs` row, its `error_detail` and every
`create_held_run_alerts` todo were erased together — a failed run left **no trace
at all**.

Every prior verify script built a **raw** `asyncpg.create_pool`, where the inner
commit is real; that is precisely why this never surfaced. The deployed
event-trigger path (`chancery_workflow_bridge`) passes the actual RLS pool and
has always been exposed to it. Fixed by `_independent_acquire()`: the up-front
persist and the hold/alert now run on a connection that is not enlisted in any
caller's transaction, with the RLS context re-applied.

**2. The scheduler process had an empty action registry.**
`services.assistant_actions.register_all()` was called from exactly one place —
`main.py`'s FastAPI `startup` hook. The cron process never starts FastAPI. An
empty registry does **not** fail loudly: `_execute_service_task` resolves every
action to `None` and the engine marks the step **completed**. Every scheduled
workflow would have reported success having invoked nothing. `run_scheduler_tick`
now registers the actions itself, once per tick.

### Deliberately out of scope

Cost/duration correlation to a run. Zero workflow run steps have ever invoked
AI, `ai_decision_log` carries no run identifier, and there is nothing to
correlate yet. **Still true after Sprint 4** — the Run History screen ships with
no cost column for exactly this reason.

### Sprint 3 (CRUD UX) and Sprint 4 (Run History) are since complete

- **Sprint 3 — scheduler CRUD UX**, 91/91 (`ec6ef24`). Not blocked.
- **Sprint 4 — Run History**, 82/82 (`schedulerhistory.structural`). Not blocked.
  Server-side status and time-period filters, a step timeline, scheduled-vs-manual
  origin read from the run's stored context, and a held run's real `error_detail`
  plus the exact `member_todos` alert set.

**One correction this file's readers should carry forward:** *per-run duration*
appeared in the Sprint 4 plan as though it were sound data. It is not. Postgres
`now()` is the transaction timestamp; the engine inserts the run row on an
independent connection and completes it on the caller's, whose transaction opened
first — so a run that finishes inside its own `start_workflow_run` call has
`completed_at` **before** `started_at`. Measured at **-0.36s** on a real run
during verification. Both the API and the screen now report "not measured" for any
non-positive interval instead of a number. Anything downstream that plans to
aggregate run durations needs to know this before it starts averaging.

### Next

**Sprint 5 — notifications.** Largely satisfied already: `create_held_run_alerts`
really fires, really reaches the starter plus every `org_admin`, and Sprint 4
verified the exact recipient set against `member_todos`. The genuinely missing
pieces are enumerated in `docs/OUTSTANDING_TODO_LIST.md` §2 — the largest being
that a **User Task with no `assigned_role_profile_id` notifies nobody, silently**,
and that the only out-of-band channel (email) is blocked on §1 of this file.

**Still the binding blocker for this whole subsystem: the Render cron service has
not been applied.** Everything above is proven in verification and fires nowhere
in production until the blueprint is applied.

---

## 1. Email sending (invites) — BLOCKED on two AWS-side actions

**Status: built, wired, verified — and NOT working end-to-end.** Real delivery
is blocked on AWS-account changes that cannot be made from this codebase.

### What now exists in the code (done, verified)

Before this sprint there was **no email-sending code anywhere in the API** — no
SES, SMTP, SendGrid, Postmark or Resend client in `services/` or `routers/`.
That blocked an already-shipped feature: `POST /admin/invites` mints a real
invite and returns an `enrollment_url` for the admin to share **by hand**,
because there was no way to mail it.

Now shipped:

- `apps/api/services/email.py` — the single AWS SES choke point. Credential gate
  (`credential_state()` / `probe()`, following the `portfolio_altruist.py`
  pattern), one `send_email()` that returns SES's own `MessageId`, and an error
  taxonomy that classifies a failure as `credentials` / `iam` /
  `identity_or_sandbox` / `paused` / `transport`.
- `render_invite_email()` — a plain-text + minimal-HTML invite carrying the
  **inviting org's own** name, that org's `enrollment_url`, and the expiry
  derived from that org's configurable `invite.expiry_days` setting.
- `services/invites.py` — `create_invite()` now attempts a real send and records
  the outcome in `result["email_delivery"]` on **every** path.
- `routers/invites.py` — the create response returns `email_delivery`, and
  `GET /admin/email/status` re-probes SES live so the AWS actions below can be
  confirmed done without a redeploy.

**The fallback is announced, not silent.** When a send cannot happen the invite
still succeeds and `enrollment_url` is still returned — but `email_delivery`
carries `status: "blocked"`, `manual_share_required: true`, and a reason naming
the exact AWS action to take. Returning the URL as though mail had gone out is
the failure mode this sprint exists to prevent.

### Why it does not work today (measured live, not assumed)

Verified against the credentials **Doppler** actually serves to Render, on
2026-08-26:

1. **The IAM permission does not exist.** The credentials are valid and live —
   `sts:GetCallerIdentity` resolves them — but they belong to
   `arn:aws:iam::645767464372:user/Texttrac-Ripasso`, the **Textract-only** IAM
   user. A real authorization probe on the send action returns:

   ```
   AccessDeniedException: User '…:user/Texttrac-Ripasso' is not authorized
   to perform 'ses:SendEmail'
   ```

   This is the **same gap as the earlier invite sprint**. Tonight's Doppler
   credential rotation restored *working keys*; it did not change *what those
   keys are allowed to do*. Rotating them again will not help.

2. **The SES sandbox state cannot even be read.** `ses:GetAccount`,
   `ses:GetAccountSendingEnabled` and `sesv2:ListEmailIdentities` are **all**
   denied for this principal, so this deployment cannot determine whether the
   AWS account is out of sandbox. The code reports that as *unknown* rather than
   guessing. This is an **independent second blocker**: a sandboxed SES account
   can only deliver to verified addresses, so granting `ses:SendEmail` alone
   could still cause invites to real prospective members to be rejected.

3. **No verified sender is configured.** Doppler holds `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION` — and **no** `SES_*`
   variable at all. `SES_FROM_EMAIL` is unset.

### ACTION ITEMS — Joe, outside this sprint

| # | Action | Where |
|---|--------|-------|
| 1 | Attach an IAM policy granting `ses:SendEmail` and `ses:SendRawEmail` (and `ses:GetAccount` so the status endpoint can report sandbox state) to the principal the deployment uses — either to `Texttrac-Ripasso` or, preferably, to a **new dedicated IAM user for mail**, whose keys then replace `AWS_*` in Doppler. | AWS IAM console |
| 2 | Verify a sender identity in SES — the address or, better, the sending domain (DKIM). | AWS SES console, `us-east-1` |
| 3 | **Request SES production access** (exit sandbox) for this account/region. Until this is done, delivery is restricted to verified addresses and real member invites will fail. | AWS SES console → Account dashboard |
| 4 | Set `SES_FROM_EMAIL` (and optionally `SES_FROM_NAME`, `SES_CONFIGURATION_SET`) in Doppler; confirm they reach Render. | Doppler |

**To confirm when done:** call `GET /admin/email/status` as an admin. It makes
one real SES call and reports `ok`, the gap, and `sandbox_known` /
`production_access`. Or re-run `apps/api/scripts/verify_smtpservice.py`, which
exits non-zero while this is blocked and will attempt a real send — and assert a
real `MessageId` — the moment the gate reports usable.

### Verification result (2026-08-26)

`apps/api/scripts/verify_smtpservice.py` → **9 PASS, 0 FAIL, 1 BLOCKED**, exit 2.

Everything that can be verified is: the discovery findings, the loud-failure
messages (including a **real** refused SES call proving the IAM message names
`ses:SendEmail`), the announced manual-URL fallback, cross-org content
correctness, output safety, and zero-leftover teardown. The single BLOCKED line
is real delivery, per the action items above. It is deliberately *not* reported
as a pass: "we correctly reported that we cannot send email" is not "email
works".

### One real bug this sprint found and fixed

`DEFAULT_SETTINGS["brand.name"]` is the literal string `"2nd Act Capital"`, and
**Hollisworks has no `brand.name` row**. Resolving the sender name with the
ordinary `get_setting()` would therefore have signed **every Hollisworks invite
email with 2nd Act Capital's name**. `resolve_org_display_name()` uses
`get_setting_with_origin()` instead and falls back to `organizations.name` (which
is per-org and `NOT NULL`), so the platform default is unreachable on this path.

This is the same "silently inherit the other tenant's value" shape as the Auth0
`domain ?? AUTH0_DOMAIN`, `appBaseUrl ?? APP_BASE_URL` and `audience` bugs — and
worse here, because the recipient sees it.

---

## 2. Notes for whoever runs the verify scripts

Working database credentials live in **Doppler**. The copies in `apps/api/.env`
and `~/.bashrc` are **stale** and their passwords are rejected by Postgres for
both the `postgres` and `app_service` roles; that stale copy is what produced
several sprints of false "blocked on credentials" results.

`apps/api/scripts/_doppler_env.py` hydrates `os.environ` from Doppler over its
**HTTPS API** using `DOPPLER_TOKEN` (stdlib only, no CLI, never prints a value).
`verify_smtpservice.py` uses it and overwrites the ambient values deliberately —
deferring to what is already set would preserve exactly the stale-copy bug.
