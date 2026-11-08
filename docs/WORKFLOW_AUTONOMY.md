# Workflow autonomy (S29B — Workflow Manager Wave 4)

Scheduled and event-triggered workflows may run WRITE steps on their own, under S30's segregation of duties. The
rules live in ONE place, `services/workflow_autonomy.py`; the engine asks it once per Service Task
(`workflow_engine._run_service_step` → `workflow_autonomy.decide_step`).

**Money movement is excluded until counsel — cash stage C.** Money movement, custody, authority, role / permission
changes, trade submission and anything that contacts a member or an outside party are `never`: no switch, no
opt-in and no diagram turns them on. Stage C of the cash track (rules moving money without a person per occurrence)
needs S30 + S29B + counsel. S29B does not enable it.

## Classes (on the verb)

`AssistantAction.autonomy_class` is filled at `register()` from `workflow_autonomy.VERB_AUTONOMY`. An unlisted WRITE
verb is `never` (fail closed).

| class | meaning |
|---|---|
| `never` | never runs unattended. A trigger-started run SUSPENDS it for a person. Configuring an active trigger for a workflow whose non-Tier-1 write step is `never` is refused (422, steps named). A Tier-1 `never` step is allowed: it suspends anyway. |
| `eligible` | internal writes (tasks, notes, document filing, draft proposals, recalculations, data refresh, reminders): may run unattended behind both switches, after the shadow period, within the limits |
| `eligible_with_cap` | a write with a quantity: also under the per-run cap. A quantity the run cannot state holds the run (fail closed). |

Verbs per class today (docs/S29B_DISCOVERY.md 1a has the full table):

- `never` — `spv.record_transaction` (money), `spv.subscribe` (money, commitment), `entity.link_ownership` (custody).
- `eligible` — `crm.draft_note`, `propose`, `spv_carry.propose_from_realization` (Tier 1: it still suspends). The platform jobs
  `ai.drift_monitor`, `edgar.launch_pipeline_job`, `litellm.reload_model_cost_map`, `security_data.nightly`,
  `security_data.prices`, `security_master.attributes`, `security_master.mutual_funds`,
  `security_master.openfigi_new`, `security_master.sec13f`, `security_master.universe` are also `eligible`. Every
  read verb is `eligible`.
- `eligible_with_cap` — none registered yet.

## The decision, per step, in order

1. A run a PERSON started → attended, unchanged.
2. No handler (not `workflow_invocable`) or a read → unchanged.
3. Effective Tier 1 → suspends; the checker must pass services.sod against the run's starter AND the trigger owner (S30).
4. Platform kill switch or org kill switch on → suspends for a person.
5. A `platform_job` verb in the Hollisworks platform org with a Super Admin owner → runs under its own switch
   (bugfix4), as before S29B.
6. `never` → suspends for a person.
7. No named trigger owner → suspends.
8. Version in `shadow` → recorded, not executed.
9. Version `on` AND org switch `workflows.autonomy` on → limits → runs as the owner's delegate. Over a limit → HOLD.
   Otherwise → suspends for a person.

Each step reads the committed settings fresh, so turning a switch off stops the NEXT step of a run already going: the
current step finishes, then the next one suspends for a person. "Suspends for a person" is the Tier-1 mechanism
(`agent_proposals` + `resolve_tier_approval`, the same S30 checker rule); the reason is in the step's `error_detail`,
in the proposal payload, and on the run console.

## Switches

All governed settings live in `public.workflow_autonomy_settings`: versioned (valid-time, a new row per change, the
old one retired), maker-checker through services.sod, RLS on every operation, content frozen by a guard trigger.
`org_settings` could not carry it: that table is updated in place, with no history and no maker / checker pair.

| setting | scope | who asks | applies |
|---|---|---|---|
| `autonomy` (`workflows.autonomy`) | org | `manage_org_settings` | ON: a draft a DIFFERENT person decides (item type `workflow_autonomy_setting`); OFF: at once |
| `kill_switch` | org | `manage_org_settings` | at once, both ways (clearing it turns nothing on by itself) |
| `kill_switch` | platform | Super Admin | at once, both ways |
| `limits` | org | `manage_org_settings` | lowering at once; raising is a draft a different person decides |
| `shadow_runs_required` | org (default 3) | `manage_org_settings` | more at once; fewer is a draft |
| `version_autonomy` (`off` / `shadow` / `on`) | a workflow version | `author_workflows` | `off` / `shadow` at once; `on` is a draft (item type `workflow_autonomy_optin`) that needs N clean shadow runs when it is asked for AND when it is decided. The makers are the requester and EVERY owner of a trigger that starts the workflow, so the trigger owner can never be the checker. |

The rule is simple: tightening applies at once, loosening needs a second person. The DB backs it up:
`was_checked_chk` (a loosening row is never active without a checker), `was_on_checked_chk` (autonomy ON or a version
ON always carries a checker), and `was_maker_checker_chk`.

## Shadow period

A version in `shadow` runs normally, but each would-be unattended write is recorded and NOT executed. The record sits
on the existing rows:

- `workflow_run_steps.result.shadow` — `verb`, `arguments` (org, acting user = the owner, run id, run context),
  `target` (what the verb writes), `would_produce` (the verb's own pure preview if it declares one; otherwise a line
  naming the handler and its arguments — no handler and no draft handler is ever called);
- `workflow_run_steps.autonomy_mode = 'shadow'` and `workflow_runs.autonomy_mode = 'shadow'`.

The trigger owner (any login of the same person) reviews each completed shadow run as clean or not clean (a note is
required): `workflow_runs.shadow_review_outcome` / `shadow_reviewed_by` / `shadow_reviewed_at` / `shadow_review_note`.
The opt-in needs `shadow_runs_required` clean reviews, and the most recent review must be clean.

## Execution

- **Delegate.** The verb runs with the trigger owner as its acting user (the permission re-check is against the
  owner). Audit: `audit_log.action = 'workflow.unattended_write'`, payload `line` = "on behalf of <owner> by workflow
  <name> v<n>". The step row carries `delegate_for`.
- **Idempotency.** Before the verb runs, the step is claimed once, committed on its own connection:
  `workflow_run_steps.idempotency_key = 'wfstep:<run step id>'` (unique index), `autonomy_claimed_at`, status
  `executing`. A retry that finds the claim returns the recorded result if the step completed. It HOLDS the run
  (never writes again) if the step started and did not finish.
- **Limits** (per org, defaults): `runs_per_day` 20, `writes_per_run` 5, `writes_per_day` 50, `cap_per_run` 100,
  `event_runs_per_hour` 20. Days are UTC, counted from `autonomy_claimed_at`. Over a limit the run HOLDS
  (`workflow_runs.status = 'held'`, the reason in `error_detail`), and the trigger owner and every org admin get a
  to-do (`member_todos.source = 'workflow_autonomy_hold'`).
- **Failure** holds the run, with the same alert. The step keeps its claim, marked `failed`. There is no retry storm:
  the scheduler does not re-fire a workflow with a held run, and a forced retry of the step holds.
- **Kill switches** stop every unattended step at once, platform jobs included. Each Service Task re-reads them.

## Events

`domain_events.publish_event` (and the run it starts) follows the same per-step rules.

- **De-duplicated.** One run per event per workflow version. A claim row (`domain_event_deliveries.status =
  'STARTING'`, `workflow_version_id`) is inserted under the unique index `domain_event_deliveries_event_version_uq`
  BEFORE the run starts. A second trigger on the same version, or a concurrent publish, matches nothing.
  `domain_events_source_dedupe_uq` still makes one source row at most one event of a type.
- **Rate-limited.** At most `event_runs_per_hour` runs per workflow VERSION from events in the last hour. Over it, a
  `RATE_LIMITED` delivery is recorded (no run), and the trigger owner and org admins get a to-do
  (`workflow_event_rate_limited`).
- Trigger event types: `workflow_autonomy.TRIGGER_EVENT_TYPES`.

## Visibility

- Run console (`RunDetailPane`, `GET /admin/workflow-runs/{id}`): each step's `autonomy_mode` (unattended / shadow /
  platform job / held for a person), the delegate line, the shadow record, why it was held. The run shows shadow
  vs unattended and its review.
- Admin → Workflows → Autonomy (`/admin/workflows/autonomy`, `GET /admin/workflow-autonomy`): switches, limits with
  today's use, the verbs by class, requests awaiting a decision, shadow runs awaiting review, both kill switches.
  The permission envelope fails closed (`lib/workflowAutonomy.mjs`).
- Daily digest: once per day per org with unattended writes, each org admin gets a to-do
  (`workflow_autonomy_digest`) summarising yesterday's unattended writes. It is written by the scheduler tick and
  refreshed, never duplicated.

## Operator notes

- Schema: `docs/s29b_part1.sql`, applied with `apps/api/scripts/_apply_s29b.py --all` (2026-11-08: SCHEMA OK).
  `--effect` prints Task 0's live effect, read-only.
- Autonomy is OFF on every real org and nothing in S29B switches it on. Turning it on for a firm: an org admin asks,
  a second admin approves. Then, per workflow version: shadow, N clean reviews by the trigger owner, an author asks,
  a different eligible person (not the trigger owner) signs off.
