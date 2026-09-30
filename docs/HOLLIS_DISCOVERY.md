# Hollis Discovery — Is the First Agent Already Built?

Read-only discovery sprint (hollisdiscovery.lowrisk). No code or schema
changes. All file:line references are current as of this sprint's HEAD
(`9532f39`). Live-data checks ran against the production database
(`DATABASE_URL` from Doppler `prd`, role `app_service`) inside a
transaction with `SET LOCAL` RLS context set explicitly, then rolled back
— no writes.

## Lead finding — cross-member read exposure is real, not hypothetical

Three of the assistant's 17 registered actions scope their query **only by
`org_id`**, with no check against the member's own visibility set:

- `portfolio.show_allocation` → `services/assistant_actions/portfolio.py:6-40`
  → `services/allocation_lens.py:52-57` (`aggregate_allocation(pool, selector,
  org_id)` — no caller identity parameter at all)
- `portfolio.find_my_investment` → `services/assistant_actions/portfolio.py:43-89`
  (`WHERE e.org_id = $1`, no `user_id`/ownership filter)
- `entity_graph.show_hierarchy` → `services/assistant_actions/entity_graph.py:6-32`
  → `services/entity_graph.py:85-98, 167-181` (`get_subtree`/`get_lookthrough`
  take `org_id` + `entity_id`, never `user_id`)

All three carry `required_permission=None` (`portfolio.py:99,127`,
`entity_graph.py:157`) — reachable by any authenticated member, no
permission gate at all. `entity_id` is taken directly from the LLM tool-call
input, which the member's own free-text message controls.

Compare this to the module that **does** scope correctly:
`services/assistant_actions/queries.py:32-50` (`entities.count`,
`investments.count`) routes through `services.delegate_grants
.get_delegate_visible_entity_ids` for non-staff callers — the platform's
real, established member-visibility engine (also used by semantic search and
the ownership-tree API per `docs` history).

**Live check, run against production:**

- `delegate_grants` has **0 rows** for org `00000000-0000-0000-0000-000000000001`
  (`SELECT COUNT(*) FROM delegate_grants WHERE org_id = $1` → `0`). Confirmed
  this is a real RLS-authenticated read (transaction opened `SET LOCAL
  app.current_org_id`/`app.is_super_admin` first; `current_user` = `app_service`,
  `rolbypassrls` = `false`, confirmed by direct query).
- Consequence: under the **correct** visibility engine
  (`get_delegate_visible_entity_ids`), every member's visible-entity set is
  **empty** today — `entities.count`/`investments.count` would correctly
  report 0 for any real member.
- But `entity_graph.show_hierarchy`'s exact root-lookup query, run against a
  real entity in org 1 (`10000000-0000-0000-0000-000000000003`,
  `display_name = 'James Hargrove'`, `entity_type = 'individual'`), returns
  the row in full — because the handler never consults `delegate_grants` or
  any ownership table. The same is true for every one of the 34 entities in
  org 1: none is visible to any member under the real visibility rule, yet
  all 34 are reachable through these two actions by supplying the
  `entity_id`.

`portfolio.find_my_investment` additionally has an unrelated, independent
bug that currently prevents it from leaking data in practice (see next
section) — but that is schema drift, not a design safeguard, and the other
two actions have no such accident protecting them.

**This is a structural gap, not a one-off bug**: the pattern (scope by
`org_id`, ignore caller identity) recurs across three independently-written
modules. Any new read action copy-pasted from `portfolio.py` or
`entity_graph.py` inherits it.

## Schema-drift finding: `portfolio.find_my_investment` is broken today

`services/assistant_actions/portfolio.py:48-49, 55-57` selects
`mi.status`, `mi.current_stage`, `mi.committed_amount`, `mi.currency` from
`member_investments`. Live `information_schema.columns` for
`member_investments` has no such columns — the real ones are
`investment_stage`, `amount_committed`, `amount_funded`, plus a `user_id`
column the handler never reads:

```
id, org_id, deal_id, user_id, entity_id, investment_stage, amount_committed,
amount_funded, subdoc_sent_at, subdoc_executed_at, funded_at,
stage_updated_at, stage_updated_by, notes, valid_from, valid_to,
system_from, system_to, created_by, created_at, updated_at
```

Every call to this action raises `asyncpg.exceptions.UndefinedColumnError`
today. It is caught by `routers/assistant.py:214-223`'s per-tool
`try/except` and surfaced to the model as the literal string
`f"Error: {exc}"` — a loud failure, not a silent one, but the action has
never worked since it shipped. Confirmed live by running the exact SQL
from `portfolio.py` against the production database.

Note also: `member_investments` has a real `user_id` column that could
support per-member scoping directly (no join needed) — it is defined on the
table but read by none of the three handlers above.

## Task 1 — what exists

**Entry points** (one implementation, not seven):
- `GET /assistant/conversation`, `POST /assistant/message`,
  `POST /assistant/confirm`, `POST /assistant/activity/{id}/undo`,
  `GET /assistant/activities` — all in `routers/assistant.py:1-9, 351-611`.
- Frontend: `apps/web/app/api/assistant/*/route.js` forward to
  `/api/v1/assistant/*` (`apps/web/app/api/assistant/message/route.js`).
  `AssistantPanel.jsx` is mounted unconditionally inside `AppShell.jsx:16`,
  and `AppShell` is used by **both** member pages (`dashboard`, `portfolio/*`,
  `marketplace/*`, `spvs/*`) and staff/admin pages (`admin/*`, `crm/*`) —
  confirmed by grepping every `AppShell` import in `apps/web/app`.

**The loop**: `_run_loop` (`routers/assistant.py:67-263`). A bounded
`for _iteration in range(10)` (`:97`) calls `call_claude_with_tools`
(`services/extraction.py:1214+`) each turn. READ tools execute inline and
feed results back (`:193-223`); the first WRITE tool call stops the loop and
returns a `proposed_action` (`:169-191`) — it is never executed from the
loop itself, per the module's own docstring (`services/action_registry.py:6-8`).

**Tool selection**: filtered **only by `required_permission`**
(`services/action_registry.py:75-79`, `list_for_user`). `tier` is stored on
every `AssistantAction` (`:37`) but is **never read** by `list_for_user`,
`to_tool_specs`, or anywhere in `routers/assistant.py` — confirmed by grep:
the only two files that read `.tier` at all are `action_registry.py` itself
(storage/sync) and `services/workflow_engine.py` (the separate BPMN engine's
`compute_effective_tier`, `:77-95`). The assistant's tool set is not
filtered by tier at all.

There is also **no per-agent tool allowlist**. `services/agent_proposals.py
:30-39` documents seven named agents (Document & Custodial Ops, Portfolio &
Suitability, Deal & SPV, Fund Admin & Billing, Compliance Analyst, Authoring,
Hollis — the last one explicitly "read-only, never proposes") and an
`AGENT_KEYS` tuple (`:58-66`) including `"hollis"`. Neither string
(`"hollis"` nor `agent_key`) appears anywhere in `routers/assistant.py` or
`services/action_registry.py` — confirmed by direct grep, zero matches.
There is one generic registry and one generic loop; nothing in the running
code assigns a caller to one of the seven agents or restricts their tool set
accordingly. The "agent boundary is the tool allowlist" design principle
has no corresponding enforcement point in the real code.

**Model resolution**: `task_type="assistant"` (`routers/assistant.py:104`)
resolves via `ASSISTANT_MODEL_KEY = "ai.model.assistant"`
(`services/extraction.py:107`), one of three assignable dials
(`MODEL_TASK_REGISTRY`, `:149-186`), shared with `member_brief` and
`note_terms_hazard_ensemble`. Resolution goes through the per-org ordered
fallback chain (`resolve_fallback_chain`, `:534+`), same mechanism as every
other AI call site, routed through the central LiteLLM helpers per
CLAUDE.md's AI Provider Abstraction section.

**Who can use it**: both. `routers/assistant.py` has no role check — any
authenticated user with a `users` row reaches all five endpoints. The only
per-caller branching is `caller_is_staff = is_staff(request)`
(`:397`), which is threaded into handlers to select the staff-vs-member
visibility engine in `queries.py` — it does not gate which endpoints are
reachable, only which visibility engine two of the seventeen actions use
internally.

## Task 2 — the contract, constraint by constraint

**1. principal-as-user — MET.** `user_id`/`org_id` come from
`ensure_user(conn, request)` / `get_org_id(request)`
(`routers/assistant.py:359-360` etc.), both JWT-derived, never a system
identity. RLS context is set globally, per request, in
`main.py:390-467` (`rls_context_middleware`), which resolves `org_id` and
`is_super_admin` from the same request before any handler runs.
`main.py:399-401`'s own docstring claims this "is inert while the app
connects as the RLS-bypassing `postgres` role (current production)" — that
comment is **stale**: a live probe during this sprint confirms the
production connection is `current_user = app_service`,
`rolbypassrls = false`. The middleware is genuinely active, matching
CLAUDE.md's "RLS Is Now Genuinely Enforced" section, not the code comment.
Caveat: this gives **org**-level principal-as-user, not **member**-level —
which is exactly the gap the lead finding describes. RLS stops
cross-org reads; nothing at the RLS layer stops cross-member reads within
an org.

**2. workflow-instance execution — ABSENT; genuine tension, not resolved
here.** Grepping `routers/assistant.py` and `services/action_registry.py`
for `workflow_run`/`workflow_instance`/`workflow_engine` returns zero
matches. `/assistant/message` is a synchronous HTTP request/response loop
with no tie to `services/workflow_engine.py`'s run/step machinery at all —
no `workflow_runs` row, no `workflow_steps` row, nothing. An interactive,
member-typed chat turn does not obviously map onto "runs as a workflow
instance" the way a scheduled or triggered agent action would; this sprint
does not attempt to resolve whether that constraint is meant to apply to
this shape of interaction.

**3. bounded loop — PARTIAL.** A hard cap exists:
`for _iteration in range(10)` (`routers/assistant.py:97`) — no token
budget, only a step count. But hitting the cap has **no escalation path**:
if the loop exhausts all 10 iterations without an `end_turn` or unhandled
`tool_use` break, the function falls through to `return` at `:257-263`
with whatever `final_text` happened to accumulate (possibly empty) and no
signal distinguishing "the model finished" from "the model was cut off."
`escalation_reason` is a real column, but it lives only on `agent_proposals`
(`services/agent_proposals.py:120,133,137`) and is set when a **proposal**
is created — nothing in `_run_loop` ever reaches that code path on a step-cap
exit, confirmed by grep: `escalation_reason` does not appear in
`routers/assistant.py` at all.

**4. per-step decision log — PARTIAL.** `call_claude_with_tools` writes one
`ai_decision_log` row per LLM call (`services/extraction.py:651-684`,
columns: `org_id, task_type, model_requested, model_used, fallback_used,
fallback_reason, cost_usd, latency_ms, success, error_detail,
effort_requested, effort_used, litellm_bypassed, bypass_reason`) — so one
row per loop iteration, but it records model/cost bookkeeping only. It has
no column for which tool was called, what arguments, or what the tool
returned, and no `user_id` column. The only place the actual tool-call/
tool-result content is recorded is the `assistant_conversations.messages`
jsonb blob, rewritten wholesale on every turn
(`UPDATE assistant_conversations SET messages = $1 ...`,
`routers/assistant.py:412-422`) — not an append-only or independently
queryable log. For confirmed **writes** only, `assistant_activities`
(`:477-496`) gives a structured, queryable per-row record (action_key,
rationale, result, undo_token) — but reads get no equivalent row at all.

**5. idempotency — ABSENT.** Grep for `idempoten` across
`routers/assistant.py`, `services/action_registry.py`, and every module in
`services/assistant_actions/` returns zero matches. `ConfirmBody`
(`routers/assistant.py:433-436`) carries no idempotency key. A retried
`POST /assistant/confirm` (double-click, client timeout-and-retry) re-runs
the handler in full: `spv.record_transaction`'s `confirm_and_allocate`
choice (`services/assistant_actions/spv.py:307-380`) inserts a **new**
`spv_transactions` row and re-runs `allocate_transaction` on every call —
two retries of the same confirm produce two posted allocations of the same
amount, with nothing to detect or collapse the duplicate.

**6. eval gate — ABSENT.** Grepping for `deepeval`/`DeepEval`/`eval_gate`/
`EvalGate` across the API finds `scripts/eval_document_classifier.py`,
`scripts/eval_correction_loop.py`, and `services/eval_metrics.py` — none of
which reference `assistant` or the assistant task type. No eval exists for
this loop today; it has never been measured before being reachable by real
members and staff.

## Task 3 — the write surface

All 7 write actions in the registry, with `access_type="write"`:

| action_key | required_permission | tier | handler effect on confirm |
|---|---|---|---|
| `crm.draft_note` | `None` (`crm.py:72`) | 2 | INSERT `entity_notes`, any `entity_id` (`crm.py:36-44`) |
| `entity.link_ownership` | `manage_deals` (`entity_graph.py:193`) | 1 | INSERT `entity_relationships` (`:102-116`) |
| `spv.subscribe` | `indicate_interest` (`spv.py:449`) | 1 | INSERT `spv_subscriptions`, status `'soft'` (`:171-184`) |
| `spv.record_transaction` | `manage_deals` (`spv.py:514`) | 1 | INSERT `spv_transactions` (status `'draft'`), optional `allocate_transaction` (`:307-380`) |
| `propose` | `None` (`propose.py:103`) | 2 | INSERT `agent_proposals` (`:66-73`) |
| `litellm.reload_model_cost_map` | `author_workflows` (`litellm_ops.py:176`) | 2 | external POST to LiteLLM proxy admin endpoint |
| `spv_carry.propose_from_realization` | `ACTION_PERMISSION` (`spv_carry_runs.py:1074`) | 1 | INSERT `spv_carry_run` (DRAFT only, `:1031-1055`) |

`confirm_action` (`routers/assistant.py:439-513`) checks exactly two things:
`action.access_type == "write"` (`:454-455`) and `action.required_permission
in permissions` (`:458-460`). **Tier is never read here.** There is no call
into `services/agent_proposals` or any maker-checker path from
`confirm_action` for any action except `propose` itself — and `propose` is
just one action among seven, not a required routing layer the other six
pass through.

**Can a member commit capital through the assistant, and how does it
execute?** Yes, directly. A member holding `indicate_interest` (a real,
member-held permission per `spv.py:440-449`'s own comment) types a request,
the LLM calls `spv.subscribe`, the loop returns a `proposed_action`
rendered by `apps/web/components/assistant/BoundedChoice.jsx` (`:7,20`:
"POST /assistant/confirm on tap"). One tap → one `POST /assistant/confirm`
→ `_execute_subscribe` runs immediately (`spv.py:149-184`), closing any
prior active subscription and inserting the new commitment row. **There is
no tier gate, no maker-checker, and no advisor step anywhere in this path.**
It executes on the member's own single confirmation.

This is a direct, structural departure from Hollis-as-designed
(read-only; any action-needing-write goes through `propose()`, owned by the
member's advisor). `propose()` exists, is registered, and is fully
functional (`agenticmakerchecker.structural`/`selfapproval.structural`) —
but nothing in the registry, the loop, or `confirm_action` routes
`spv.subscribe`, `spv.record_transaction`, `entity.link_ownership`, or
`crm.draft_note` through it. They are independent, directly-executing write
actions registered and exposed exactly like `propose()` is, and the choice
of which one the model calls is not constrained by anything that enforces
"only `propose()` for a member caller."

`crm.draft_note` is additionally reachable by **any** authenticated caller
(`required_permission=None`) against **any** `entity_id` in the org, with
no ownership check on that `entity_id` — the same org-only-scoping gap as
the lead finding, on a write path instead of a read.

## Task 4 — member-to-member isolation

Covered in the lead finding above. Summary: `queries.py`'s two actions
(`entities.count`, `investments.count`) are correctly scoped through
`get_delegate_visible_entity_ids`/`get_staff_visible_entity_ids` +
`filter_restricted` — the platform's real visibility engines, confirmed by
reading `services/assistant_actions/queries.py:32-50`. `portfolio.py`'s two
actions and `entity_graph.py`'s hierarchy action are not: they scope by
`org_id` alone. Live-database check: `delegate_grants` has zero rows for
org 1, so the *correct* visible set for every real member is empty, yet the
unscoped actions return real entity data (verified against
`10000000-0000-0000-0000-000000000003`, a live individual entity) regardless
of who is asking. `portfolio.find_my_investment` cannot currently leak
`member_investments` data in practice only because it is broken by
unrelated schema drift (see above) — not because anything scopes it
correctly.

## Task 5 — the custody cliff

No path found from the assistant to trade execution, money movement,
filing submission, or GL posting, across all 17 registered actions:

- No action calls anything under a trading/custodian/wire/ACH surface —
  none of the 17 action keys or their handlers reference custodial transfer
  logic. (The platform's Altruist custodian sync is a separate, non-agent
  subsystem; no assistant action calls into it.)
- `spv.record_transaction` inserts `spv_transactions` with a hardcoded
  `status = 'draft'` (`spv.py:338`) even on `choice_value="confirm_and_allocate"`
  — allocation only distributes the amount across subscriber rows
  (`spv_transaction_allocations`); it is a sub-ledger operation, not a GL
  post. Grep for `ledger_books`/`post_to_gl`/`GLPosting` across every
  `services/assistant_actions/*.py` and `services/spv_allocation.py` returns
  zero matches.
- `spv_carry.propose_from_realization` explicitly, in its own description
  and return text, "proposes only — it never posts, and it moves no money"
  (`spv_carry_runs.py:1064-1066, 1051-1054`) — drafts a `spv_carry_run`
  awaiting two human approvals.
- `spv.subscribe` commits capital (an obligation) but does not move cash —
  no wire, no ACH, no custodian call. It sits at the edge of the same
  three-test that defines Tier 1 (`services/action_registry.py:22-24`)
  without crossing into literal money movement; its real problem is the
  missing maker-checker gate documented under Task 3, not a custody-cliff
  breach.

No violation of the custody cliff exists in the current registry. The
nearest thing to a concern is `spv.subscribe`'s missing review gate
(Task 3), which is a maker-checker gap, not a custody-cliff one.
