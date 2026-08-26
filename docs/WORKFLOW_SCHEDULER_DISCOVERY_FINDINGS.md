# Workflow Scheduler — Discovery Findings

**Sprint:** `schedulerdiscovery` (discovery only — no code, schema, or config was changed)
**Date:** 2026-08-26
**Scope:** Establish the real, currently-deployed facts a workflow scheduler design must build on.

This is a **discovery record**. It reports what was found, not what should be
done. Every claim below was verified against the deployed database, the
committed source, the live Doppler `hollisworks/prd` config, or a live HTTP
probe — the method is stated per finding. Where something could not be
verified, that is stated explicitly rather than inferred.

**Environment queried:** Supabase dev Postgres (via MCP `execute_sql`), branch
`claude/inspiring-turing-v8hf1w`, Doppler project `hollisworks`, config `prd`.

---

## Task 1 — Run/step schema, and cost/duration capture

### 1.1 The real columns (from `docs/schema_snapshot.sql`, live introspection)

`workflow_runs` — 10 columns:

| column | type |
|---|---|
| `id` | uuid NOT NULL DEFAULT uuid_generate_v4() |
| `workflow_version_id` | uuid NOT NULL |
| `org_id` | uuid NOT NULL |
| `status` | text NOT NULL DEFAULT `'running'` |
| `context` | jsonb |
| `spiff_serialized_state` | jsonb |
| `started_by` | uuid |
| `started_at` | timestamptz NOT NULL DEFAULT now() |
| `completed_at` | timestamptz |
| `error_detail` | text |

PK `workflow_runs_pkey (id)`. No other constraint or unique index.

`workflow_run_steps` — 12 columns:

| column | type |
|---|---|
| `id` | uuid NOT NULL DEFAULT uuid_generate_v4() |
| `workflow_run_id` | uuid NOT NULL |
| `workflow_step_id` | uuid NOT NULL |
| `org_id` | uuid NOT NULL |
| `status` | text NOT NULL DEFAULT `'pending'` |
| `proposed_by` | uuid |
| `approved_by` | uuid |
| `result` | jsonb |
| `started_at` | timestamptz |
| `completed_at` | timestamptz |
| `error_detail` | text |
| `created_at` | timestamptz NOT NULL DEFAULT now() |

PK `workflow_run_steps_pkey (id)`. No other constraint or unique index.

**Neither table has any cost column, any token column, any model column, or
any duration column.** There is no `workflow_run_costs` table, no
`workflow_run_metrics` table, and no other workflow-adjacent table of any kind.
The complete set of `workflow_*` tables deployed is exactly six:
`workflow_definitions`, `workflow_versions`, `workflow_steps`,
`workflow_triggers`, `workflow_runs`, `workflow_run_steps`.

### 1.2 Duration capture — exists nominally, is meaningless for Service Tasks

Duration is only derivable as `completed_at - started_at`. Whether that number
means anything depends on the step type, and the two cases differ:

**Service Task — duration is structurally always zero.** In
`apps/api/services/workflow_engine.py:378-387`, after `_drive(workflow)` has
already executed the step, the engine writes:

```sql
UPDATE workflow_run_steps
SET status = 'completed', started_at = now(), completed_at = now(),
    result = $2::jsonb
WHERE id = $1
```

`started_at` and `completed_at` are set in the **same statement, after the work
is finished**. Both resolve to the same transaction timestamp. The stored
interval is therefore always exactly zero regardless of how long the step
actually took.

Confirmed against real deployed rows, not just read from source:

| step_type | status | started_at set | completed_at set | `completed_at - started_at` |
|---|---|---|---|---|
| service | completed | yes | yes | **0.000000 s** |
| user | completed | yes | yes | 0.847775 s |
| service | pending | no | no | null |
| user | pending | no | no | null |

**User Task — duration is real but measures human latency.** `started_at` is
set at activation (`workflow_engine.py:398`, `SET status = 'active', started_at
= now()`) and `completed_at` at approval (`:556`), so the interval is the
wall-clock wait for a human decision, not execution time.

The `result` jsonb carries no timing or cost either. Actual keys observed in
deployed rows:
- service step: `{resolved, access_type, executed_at, action_registry_key}`
- user step: `{decision}`

### 1.3 Cost capture — `ai_decision_log` only, and it does not reach a run

`ai_decision_log` is the only cost-bearing table in the `public` schema. A grep
of the full schema snapshot for `cost_usd|latency_ms|duration_ms|elapsed`
returns exactly two `public` hits, both on this table; every other hit is in
the `litellm` schema (LiteLLM's own 77 tables, not application tables).

`ai_decision_log` — 12 columns: `id`, `org_id`, `task_type`,
`model_requested`, `model_used`, `fallback_used`, `fallback_reason`,
`cost_usd` (numeric), `latency_ms` (integer), `success`, `error_detail`,
`created_at`. PK on `id`. **No `workflow_run_id`. No `workflow_run_step_id`.
No `user_id`. No correlation id of any kind.**

Written by `services/extraction.py::_write_ai_decision` (`:173-192`), called
from `_safe_log` inside `_execute_chain` (`:257-263` on success, `:269-278` on
chain exhaustion) — exactly one row per AI call. `cost_usd` is computed
locally by `_compute_cost` from `message.usage` input/output tokens against a
hardcoded per-model price table; `latency_ms` is measured across the whole
chain attempt from `t0` before the first model.

### 1.4 The real join path from an AI cost back to a run: **there is none**

This is stated as a positive finding, not an omission. Two independent facts
make it true:

1. **Structural.** `ai_decision_log` carries no run, step, or request
   identifier. The only columns that could correlate anything are `org_id`,
   `task_type`, and `created_at`. Correlating a cost to a run would require a
   timestamp-proximity heuristic on `(org_id, task_type)` — and `org_id` is
   defaulted to `DEFAULT_ORG_ID` when the caller has no org context
   (`extraction.py:189`), so even that key is lossy.

2. **Behavioural — no run step has ever made an AI call.** The only AI call
   anywhere in the workflow subsystem is
   `services/workflow_nl_generator.py:244` (`call_claude_text`, `task_type =
   "workflow_generation"`, constant at `:37`). Its sole caller is
   `POST /admin/workflows` (`routers/workflows.py:250`) — i.e. **authoring**
   an NL description into BPMN, which happens outside any run.
   `workflow_engine.py`, `workflow_steps_deriver.py`, and `workflow_editor.py`
   contain zero `call_claude_*` calls.

   Service Tasks only invoke actions that opt in with
   `AssistantAction.workflow_invocable = True` (`workflow_engine.py:212`).
   A repo-wide grep finds **exactly one** such action:
   `litellm.reload_model_cost_map`
   (`services/assistant_actions/litellm_ops.py:184`), which makes an HTTP POST
   to the LiteLLM proxy and no AI call.

Live counts at time of discovery: `ai_decision_log` = 277 rows, of which 34
have `task_type = 'workflow_generation'`. All 34 are authoring-time rows. Zero
of the 277 rows are attributable to a workflow run, because zero runs have ever
invoked AI.

---

## Task 2 — Permission model for workflow authoring

### 2.1 The three real permission names

Defined as constants in `apps/api/routers/workflows.py:132-134`:

| constant | permission key | covers |
|---|---|---|
| `PERM_AUTHOR` | `author_workflows` | library, editor, save, versions |
| `PERM_VIEW_RUNS` | `view_workflow_runs` | run console + run drill-in |
| `PERM_CONFIGURE_TRIGGERS` | `configure_workflow_triggers` | scheduler / triggers |

All three exist in the deployed global `permissions` catalog, verified by
query:

| name | resource | action |
|---|---|---|
| `author_workflows` | `workflows` | `author` |
| `view_workflow_runs` | `workflows` | `view_runs` |
| `configure_workflow_triggers` | `workflows` | `configure_triggers` |

The `permissions` table is **global** — columns are `id`, `name`, `resource`,
`action` only. There is no `org_id`. Uniqueness is on `name` and on
`(resource, action)`.

### 2.2 Which endpoint requires which permission

Enforced by `_require_workflow_permission(request, permission_key)`
(`routers/workflows.py:150-165`). All nine endpoints in the router are gated:

| endpoint | permission |
|---|---|
| `GET /admin/workflows` | `author_workflows` |
| `POST /admin/workflows` | `author_workflows` |
| `GET /admin/workflows/{definition_id}` | `author_workflows` |
| `POST /admin/workflows/{definition_id}/versions` | `author_workflows` |
| `GET /admin/workflows/{definition_id}/versions` | `author_workflows` |
| `GET /admin/workflow-runs` | `view_workflow_runs` |
| `GET /admin/workflow-runs/{run_id}` | `view_workflow_runs` |
| `GET /admin/workflow-triggers` | `configure_workflow_triggers` |
| `POST /admin/workflow-triggers` | `configure_workflow_triggers` |

There is **no delete endpoint for a workflow definition** — creating, editing
(as a new version), and reading are the only authoring operations that exist.
Editing is version-append, not in-place mutation.

### 2.3 The gate's real evaluation order

```python
org_id = get_org_id(request)
actor_id = await ensure_user(conn, request)
principal = await load_principal(conn, actor_id)
if not is_super_admin(principal):
    if not await user_has_permission(pool, actor_id, permission_key):
        raise HTTPException(403, f"Permission required: {permission_key}")
return actor_id, org_id, principal
```

Two things follow:

- **Super Admin bypasses first** (`services/rbac.py:143`,
  `_field(user, "role") == SUPER_ADMIN_ROLE`) — the documented
  escape-hatch-checked-first convention. It is deliberately org-blind.
- The non-super-admin check is `services/profiles.py:69-76`
  `user_has_permission`, which is **the profiles layer only**:
  `permission_key in await get_effective_permissions(pool, user_id)`, i.e.
  the union of `profile_permissions` and `permission_set_permissions`. Its own
  docstring states it "does not consult roles or the Super/Org Admin flags".
  **`role_permissions` is not consulted by this gate at all.**

### 2.4 The real deployed grants: **all three workflow permissions have zero grants**

Verified by direct count across all three grant tables:

| grant table | rows granting any `workflows` permission |
|---|---|
| `role_permissions` (joined to `permissions.resource = 'workflows'`) | **0** |
| `profile_permissions` (`permission_key LIKE '%workflow%'`) | **0** |
| `permission_set_permissions` (`permission_key LIKE '%workflow%'`) | **0** |

**Consequence, today, in the deployed database:** only a `super_admin` can
reach any workflow endpoint. Every other user — including an `org_admin` —
receives `403 Permission required: <key>`. The permission rows exist and are
grantable through the Profiles / Permission-Sets admin UI, but nobody has been
granted them.

User role distribution in the deployed DB: `member` = 15, `org_admin` = 3,
`super_admin` = 3.

### 2.5 The `view_portfolio` / `manage_portfolio` pattern, for comparison

The portfolio permissions are the proven precedent named in the sprint prompt.
Their real deployed grants:

`permissions` catalog rows:
- `view_portfolio` → resource `portfolio`, action `view`
- `manage_portfolio` → resource `portfolio`, action `manage`

`role_permissions` grants (all in org `2ndactcapital`):

| permission | roles granted |
|---|---|
| `manage_portfolio` | `admin`, `advisor`, `super_admin` |
| `view_portfolio` | `admin`, `advisor`, `investment_staff`, `member`, `super_admin`, `support_staff` |

`profile_permissions` grants (all in org `2ndactcapital`):

| permission | profiles granted |
|---|---|
| `manage_portfolio` | `Adviser` |
| `view_portfolio` | `Adviser`, `CSA / Ops`, `Member` |

Zero `permission_set_permissions` grants for either.

**The structural difference:** the portfolio permissions are granted on **both**
axes — `role_permissions` (consumed by `services/rbac.has_permission`) and
`profile_permissions` (consumed by `services/profiles.user_has_permission`).
The workflow permissions are granted on **neither**. Note also that the two
axes use different vocabularies for the same idea: RBAC roles are lowercase
snake_case (`advisor`, `investment_staff`), SOC profiles are display names
(`Adviser`, `CSA / Ops`). They are not the same objects and are not kept in
sync by anything.

---

## Task 3 — The real existing notification mechanism

There are **two** distinct, real, working mechanisms. They are not layered;
they are separate systems with separate storage and separate UI surfaces.

### 3.1 `member_todos` — what the Workflow Manager actually uses today

`services/workflow_todos.py`. Its module docstring is explicit that the
workflow task/alert surface "REUSES the existing `member_todos` infrastructure
(the AI-dashboard todo/alert surface) rather than a new notification system."

**Failure alerting — the directly relevant precedent.** The exact real
invocation point is `services/workflow_engine.py:449-470`, `_hold_run`:

```python
async def _hold_run(conn, run_id, org_id, started_by, exc: Exception) -> None:
    error_detail = f"{type(exc).__name__}: {exc}"
    async with conn.transaction():
        await conn.execute(
            "UPDATE workflow_runs SET status = 'held', error_detail = $2 WHERE id = $1",
            run_id, error_detail,
        )
        await workflow_todos.create_held_run_alerts(
            conn, org_id=org_id, run_id=run_id,
            started_by=started_by, error_detail=error_detail,
        )
```

Called from the bare `except Exception` around run execution
(`workflow_engine.py:434-439`), in **its own transaction** because the
execution transaction has already rolled back, then re-raises.

`create_held_run_alerts(conn, *, org_id, run_id, started_by, error_detail)`
signature and behaviour:
- recipients = `{started_by}` ∪ every `users` row where
  `org_id = $1 AND role = 'org_admin'` (a literal SQL query, not an RBAC call)
- `error_detail` truncated to 2000 chars; default text
  `"The run stopped after an error and needs review."` when null
- one `_upsert_todo` per recipient with:
  `source = 'workflow_run_held'`, `related_type = 'workflow_run'`,
  `related_id = run_id`, `title = "Workflow run held — needs attention"`,
  `priority = 5`, `action_key = '/admin/workflows/runs'`,
  `category = 'workflow'`
- returns the list of todo ids

**Idempotency is hand-rolled and must stay that way.** The module documents a
verified constraint: `member_todos` has **no unique constraint beyond its `id`
PK**, so `ON CONFLICT DO NOTHING` can only fire on the generated id and cannot
dedupe. `_upsert_todo` therefore does an explicit SELECT-then-INSERT/UPDATE
keyed on `(user_id, org_id, source, related_type, related_id)`.

Status vocabulary in real use: `open` / `done` / `dismissed`. Only
`status = 'open'` is surfaced by `list_todos` (`routers/dashboard.py`).

The other two functions, for completeness:
- `sync_user_task_todos(conn, *, org_id, run_step_id, step_key, display_name,
  assigned_role_profile_id)` — called at `workflow_engine.py:414` and `:613`
  when a User Task activates; creates an `open` todo
  (`source = 'workflow_user_task'`, `related_type = 'workflow_run_step'`) for
  every user whose `users.profile_id` matches the step's assigned role profile.
- `complete_user_task_todos(conn, *, run_step_id)` — called at `:569`; flips
  those todos to `done`.

Every function takes an **open `conn`**, not a pool, specifically so the engine
can enlist these writes in its own transaction.

### 3.2 `notification_bus` — the Sprint 9 event bus

`services/notifications.py`, singleton `notification_bus = NotificationBus()`
at `:261`. Backed by three deployed tables: `notifications`,
`notification_recipients`, `notification_delivery_log`.

Real publish signature (`:31-46`):

```python
await notification_bus.publish(
    pool, org_id, event_type, title, body, recipient_user_ids,
    *, resource_type=None, resource_id=None, payload=None,
    priority="normal", created_by=None, channels=None,
)  # -> notification id, or None when there are no recipients
```

Behaviour: one `notifications` row, one `notification_recipients` row per
deduped recipient, one `notification_delivery_log` row per (recipient,
channel). `DEFAULT_CHANNELS = ["in_app"]`; the `in_app` channel is marked
`delivered` synchronously so the bell reflects it immediately, and every other
channel is written `pending` for an out-of-process worker to claim and report
back via `update_delivery_status`. All of it runs inside one
`conn.transaction()`.

**The only real production caller is `routers/marketplace.py`**, through two
never-raising wrappers, `_safe_notify_users` (`:225-237`) and
`_safe_notify_roles` (`:240-255`, which expands roles to users via
`get_users_by_role`). Both swallow every exception and `print()` — a failed
notification never fails the business operation. Six real call sites, with
these `event_type` values:

| line | event_type | fan-out |
|---|---|---|
| 1124 | `ioi_confirmed` | roles |
| 1374 | `compliance_override_requested` | roles |
| 1497 | `compliance_override_approved` | users |
| 1506 | `compliance_override_denied` | users |
| 1569 | `document_approved` | users |
| 1578 | `document_rejected` | users |
| 1768 | `deal_stage_changed` | users |

Read side: `routers/notifications.py` — `GET /notifications`,
`GET /notifications/count` (polled by the bell), plus mark-read / mark-acted /
dismiss / mark-all-read. Scoped to the authenticated caller only; **no
permission gate**, just `ensure_user` + `get_org_id`.

### 3.3 Live row counts

| table | rows |
|---|---|
| `notifications` | 0 |
| `notification_recipients` | 0 |
| `notification_delivery_log` | 0 |

The bus is wired and verified (`scripts/verify_sprint9.py`) but has **never
been used in this environment** — zero rows. `member_todos`, by contrast, is
the path the workflow engine actually calls.

### 3.4 Current workflow-subsystem live data

| table | rows |
|---|---|
| `workflow_definitions` | 2 |
| `workflow_versions` | 3 |
| `workflow_runs` | 3 |
| `workflow_run_steps` | 4 |
| `workflow_triggers` | 2 |

The two trigger rows (both created 2026-08-26 13:29 UTC by a verify script,
definitions named `WFMGR4 Definition` / `WFMGR4 OtherOrg`):

| trigger_type | schedule_cron | event_type | is_active |
|---|---|---|---|
| `scheduled` | `0 9 * * *` | null | true |
| `event` | null | `deal.created` | true |

**Nothing reads `schedule_cron` to act on it.** A repo-wide grep (excluding
verify scripts and build artifacts) returns exactly two hits: the `SELECT` in
`routers/workflows.py:469` and the display cell in
`apps/web/components/admin/WorkflowTriggerScheduler.jsx:115`. The
`POST /admin/workflow-triggers` body model
(`routers/workflows.py:118-124`) accepts only `workflow_definition_id`,
`event_type` (default `"document_confirmed"`) and `is_active` — there is no API
path to create a scheduled trigger at all. The one `scheduled` row above was
inserted directly by a verify script.

---

## Task 4 — Render service topology and service types

### 4.1 What could and could not be verified, and why

**Could not enumerate the account's services.** There is no Render credential
available anywhere in this environment:
- Doppler `hollisworks/prd` holds 35 secrets; **none** contains `RENDER` in its
  name. The full name list is: `APP_BASE_URL`, `APP_SERVICE_DATABASE_URL`,
  `AUTH0_AUDIENCE`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_DOMAIN`,
  `AWS_ACCESS_KEY_ID`, `AWS_DEFAULT_REGION`, `AWS_SECRET_ACCESS_KEY`,
  `DATABASE_URL`, `DB_PASSWORD`, `DOPPLER_CONFIG`, `DOPPLER_ENVIRONMENT`,
  `DOPPLER_PROJECT`, `EDGAR_USER_AGENT`, `HOLLISWORKS_AUTH0_AUDIENCE`,
  `HOLLISWORKS_AUTH0_CLIENT_ID`, `HOLLISWORKS_AUTH0_CLIENT_SECRET`,
  `HOLLISWORKS_AUTH0_DOMAIN`, `LITELLM_DATABASE_URL`, `LITELLM_DB_PASSWORD`,
  `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `NEXT_PUBLIC_SUPABASE_URL`,
  `R2_ACCESS_KEY_ID`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`,
  `R2_SECRET_ACCESS_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_JWKS_URL`,
  `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`,
  `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_URL`, `VOYAGE_API_KEY`.
- No `RENDER*` variable in the ambient process environment.
- The Render CLI is not installed (`which render` → not found).
- `GET https://api.render.com/v1/services` is reachable and returns
  **HTTP 401** without a key — so the API works, we simply cannot authenticate.

**Could verify:** the committed blueprint, live HTTP probes of `.onrender.com`
hostnames, and Render's live public documentation (fetched 2026-08-26).

### 4.2 The committed blueprint (`render.yaml`, repo root)

Declares exactly **two** services, both `type: web`:

| name | type | runtime | rootDir |
|---|---|---|---|
| `2ndactcapital-web` | web | node | `.` |
| `2ndactcapital-api` | web | python | `apps/api` |

The `2ndactcapital-web` block carries an in-file annotation dated 2026-08-25
marking it **STALE**: the live frontend is served by Vercel, and the block is
retained only as a Render fallback definition.

`render.yaml` declares **no LiteLLM service**, and its "DELIBERATELY NOT
DECLARED" section still states that the LiteLLM proxy "is NOT DEPLOYED … there
is no LiteLLM service in this blueprint, and Doppler holds no `LITELLM_*`
secret." **Both halves of that statement are now out of date** — see 4.3 and
4.4. The blueprint has drifted from reality.

### 4.3 Live HTTP probes of `.onrender.com` hostnames (2026-08-26)

Render returns the header `x-render-routing: no-server` for a hostname with no
live service behind it. Using a known-bogus hostname as the control:

| hostname | probe | status | `x-render-routing` |
|---|---|---|---|
| `hollisworks-litellm.onrender.com` | `GET /health/liveliness` | **200** | *(absent — live service)* |
| `2ndactcapital-api.onrender.com` | `GET /api/v1/health` | 404 | `no-server` |
| `2ndactcapital-api.onrender.com` | `GET /docs` | 404 | `no-server` |
| `2ndactcapital-web.onrender.com` | `GET /` | 404 | `no-server` |
| `no-such-service-xyz123.onrender.com` (control) | `GET /` | 404 | `no-server` |

**`hollisworks-litellm` is real and live.** It answers 200 on LiteLLM's own
`/health/liveliness` endpoint and serves HTML at `/`. This confirms the second
Render service described in the sprint prompt.

**`2ndactcapital-api.onrender.com` is indistinguishable from a hostname that
does not exist.** It returns the identical `no-server` response as the bogus
control. Eight name variants were probed (`2ndactcapital-api`, `ripasso-api`,
`twoactcapital-api`, `secondactcapital-api`, `hollisworks-api`, `2ndact-api`,
`2ndactcapital-api-1`, `ripasso`) — all `no-server`. `api.2ndactcapital.com`
and `api.hollisworks.com` do not resolve in DNS at all (they are Auth0
*audience identifiers*, per `apps/web/lib/auth0.js:24`, not URLs).
`https://2ndactcapital.com/` returns 200 with `Server: Vercel`, confirming the
frontend is on Vercel.

**Unresolved:** the API's real public hostname could not be determined from
outside. The frontend reaches it via `NEXT_PUBLIC_API_URL`
(`apps/web/lib/api.js:3-4`, `apiForward.js:4`, `tenant.js:3`,
`themeServer.js:4`), whose value lives only in the Vercel project's
environment. The Vercel MCP server in this session is unauthenticated, so that
value could not be read. Either the API service was renamed, or it is reachable
only on a custom domain whose name is not in the repo.

### 4.4 LiteLLM configuration state has changed since the last discovery

Doppler `hollisworks/prd` now contains four `LITELLM_*` secrets:
`LITELLM_DATABASE_URL`, `LITELLM_DB_PASSWORD`, `LITELLM_MASTER_KEY`,
`LITELLM_SALT_KEY`. The deployed Postgres now has a **`litellm` schema with 77
tables** (against 102 in `public`), so LiteLLM's own schema is migrated.

**`LITELLM_BASE_URL` is still absent** from Doppler. That is one of the two
variables `services/assistant_actions/litellm_ops.py` requires
(`LITELLM_ENV_VARS = ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY")`), so
`litellm.reload_model_cost_map` still raises `LiteLLMConfigError` even though
the proxy is now live and answering health checks.

### 4.5 Render service types — verified against Render's live documentation

Fetched 2026-08-26 from `https://render.com/docs/service-types` and
`https://render.com/docs/blueprint-spec`. **Caveat: these are Render's public
docs, not an account-scoped API response.** Account-specific availability could
not be confirmed without a Render API key (see 4.1).

Render's current docs state: *"Render provides six different service types for
running code"* —

1. Web service
2. Static site
3. Private service
4. Background worker
5. **Cron job**
6. **Workflow**

**Yes — Cron Job is a genuine, distinct service type, not a Web Service
variant.** The `render.yaml` reference gives the `type` field as a closed enum:

| `type` value | meaning |
|---|---|
| `web` | web service or static site (static also sets `runtime: static`) |
| `pserv` | private service |
| `worker` | background worker |
| **`cron`** | **cron job** |
| `keyvalue` | Render Key Value (`redis` is a deprecated alias) |

`type` **cannot be modified after creation**. A cron job additionally requires
the `schedule` field (a cron expression), which must be omitted for every other
type.

Real, documented properties of a Render cron job:

- **Plan / cost.** `plan` enum is `free`, `starter`, `standard`, `pro`,
  `pro plus` — and `free` is explicitly **"not available for private services,
  background workers, or cron jobs"**. Minimum is `starter`. Billing is
  prorated by the second on active running time, with a **minimum monthly
  charge of $1 per cron job service**. (`pro max` / `pro ultra` are available
  only to web services, private services, and background workers — not cron
  jobs.)
- **Single-run guarantee.** Render guarantees at most one run of a given cron
  job is active at a time. If a run is still active when the next scheduled run
  is due, Render **delays** the next run until the active one finishes. A
  manual "Trigger Run" **cancels** any active run first.
- **Hard timeout.** Render stops an active run after **12 hours**. The docs
  direct longer or continuous work to a workflow or background worker instead.
- **No persistent disk.** Cron jobs cannot provision or access one.
- **Timezone.** All schedules and time ranges are **UTC**.
- **Docker.** For a Docker-based cron job the `Command` field is absent;
  Render uses the image's `ENTRYPOINT`/`CMD` unless overridden.
- **Environment variables.** Cron jobs set env vars like any other service and
  can join an environment group.
- **Git or registry.** Can build from a connected Git repo (rebuilt on push;
  in-progress runs unaffected) or pull a prebuilt Docker image before each run
  (images are not retained between runs).

Render also now documents a separate **Workflow** service type with its own
TypeScript and Python SDKs, tasks, and run-triggering model. This was not
present in earlier discovery notes and is recorded here as a factual
observation only; its suitability was not assessed.

---

## Summary of the facts established

1. `workflow_runs` (10 cols) and `workflow_run_steps` (12 cols) have **no cost
   and no duration columns**. Service-Task duration is derivable but always
   exactly zero, because the engine stamps `started_at` and `completed_at` in
   the same post-hoc `UPDATE` — confirmed on real rows.
2. `ai_decision_log` is the only cost store, and it carries **no run or step
   identifier**. Separately, **no workflow run step has ever made an AI call**
   — the only workflow AI call is authoring-time NL→BPMN generation
   (`task_type = 'workflow_generation'`). There is no join path, and nothing
   yet to join.
3. The three real permission names are `author_workflows`,
   `view_workflow_runs`, and `configure_workflow_triggers`; all exist in the
   deployed `permissions` catalog under resource `workflows`. **All three have
   zero grants** in `role_permissions`, `profile_permissions`, and
   `permission_set_permissions` — so only `super_admin` can use any workflow
   endpoint today. `view_portfolio` / `manage_portfolio`, by contrast, are
   granted on both the RBAC-role axis and the SOC-profile axis.
4. The mechanism the workflow engine really uses for alerting is
   `member_todos` via `services/workflow_todos.py`. The precise failure-alert
   entry point is `workflow_engine.py::_hold_run` →
   `create_held_run_alerts(conn, org_id=, run_id=, started_by=,
   error_detail=)`, which alerts the run starter plus every
   `users.role = 'org_admin'` in the org, deduped by an explicit
   SELECT-then-upsert on `(user_id, org_id, source, related_type,
   related_id)`. The separate Sprint 9 `notification_bus` exists and works but
   is called only from `routers/marketplace.py` and has **zero rows** in this
   environment.
5. `hollisworks-litellm` on Render is **live** (200 on `/health/liveliness`).
   `2ndactcapital-api.onrender.com` returns `x-render-routing: no-server`,
   identical to a nonexistent host, so the API's real public hostname is
   unresolved from outside. `render.yaml` still declares only the two original
   `type: web` services and is now out of date. Render genuinely offers `type:
   cron` as a distinct service type (12-hour run cap, single-run guarantee, no
   free plan, $1/month minimum, UTC-only) — verified from Render's live public
   docs, **not** from an account-scoped API call, which was impossible without
   a Render API key.
