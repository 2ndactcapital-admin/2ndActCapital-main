"""EDGAR pipeline — the nightly workflow's one automated step.

Currently one action: ``edgar.launch_pipeline_job``.

The nightly EDGAR workflow is Start -> this Service Task -> End. The step
records a pipeline run and asks the Render API to start a ONE-OFF JOB on the
base service, then returns. It never does the heavy work itself: the scheduler
is a 5-minute Render cron tick, and discovery + selection + fetch can run for
hours.

WHY THE HANDLER NEVER RAISES FOR A LAUNCH THAT DID NOT HAPPEN. An exception from
a Service Task HOLDs the workflow run. A held run is non-terminal, and the
scheduler's overlap protection (services.workflow_scheduler._workflow_in_progress)
skips a trigger while its workflow has a non-terminal run — so one bad night
would silently skip every night after it until a person cleared it. That is
exactly the "waiting on a person" state the nightly job must never enter. A
refused or failed launch is instead recorded on the
``portfolio.edgar_pipeline_runs`` row (status 'refused' / 'launch_failed', with
the reason), which the monitoring screen shows, and the step completes.

``run_context["edgar_pipeline"]`` may narrow a run — ``stages`` (a subset of
discover/select/fetch, possibly empty) and ``fetch_cap`` — so a test can launch
the real job without changing the manifest. With no override the run uses every
stage and the default cap.
"""
from __future__ import annotations

from services.action_registry import REGISTRY, AssistantAction

ACTION_KEY = "edgar.launch_pipeline_job"


def _overrides(run_context: dict | None) -> dict:
    from services import edgar_pipeline

    raw = (run_context or {}).get("edgar_pipeline") or {}
    out: dict = {}
    if "stages" in raw:
        stages = list(raw["stages"] or [])
        bad = [s for s in stages if s not in edgar_pipeline.STAGES]
        if bad:
            raise ValueError(f"unknown pipeline stage(s) in run context: {bad}")
        out["stages"] = stages
    if raw.get("fetch_cap") is not None:
        out["fetch_cap"] = int(raw["fetch_cap"])
    return out


async def _launch_handler(pool=None, user_id=None, org_id=None, run_context=None,
                          workflow_run_id=None, **_):
    from services import edgar_pipeline

    overrides = _overrides(run_context)
    trigger_source = "nightly" if (run_context or {}).get("trigger_type") == "scheduled" else "manual"
    async with pool.acquire() as conn:
        row = await edgar_pipeline.launch_pipeline_run(
            conn,
            trigger_source=trigger_source,
            requested_by=user_id,
            workflow_run_id=workflow_run_id,
            **overrides,
        )
    data = {
        "pipeline_run_id": str(row["id"]),
        "status": row["status"],
        "render_job_id": row["render_job_id"],
        "fetch_cap": row["fetch_cap"],
        "stages": list(row["stages"]),
        "stop_reason": row["stop_reason"],
        "error": row["error"],
    }
    if row["status"] == "launched":
        text = f"EDGAR pipeline job launched on Render (job {row['render_job_id']}, run {row['id']})."
    else:
        text = (f"EDGAR pipeline job NOT launched — {row['status']}: "
                f"{row['stop_reason'] or row['error']} (run {row['id']}).")
    return {"data": data, "render": None, "text": text}


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key=ACTION_KEY,
            module="edgar",
            description=(
                "Launch one EDGAR pipeline job (incremental discovery, selection, "
                "fetch of public SEC filings to R2) as a Render one-off job and "
                "return immediately. Platform maintenance on public reference "
                "data — touches no member data and moves no money."
            ),
            # WRITE, honestly: it inserts a pipeline-run row and starts an
            # external job that writes the manifest and R2.
            access_type="write",
            # A real, seeded key (the litellm_ops precedent): the only intended
            # path is a BPMN Service Task authored by platform staff. The
            # engine's super-admin bypass covers the trigger's owner.
            required_permission="author_workflows",
            # Tier 2 (write; none of the three stakes tests fire). Tier 1 would
            # suspend the Service Task for a human approval — the nightly job
            # must contain no human step.
            tier=2,
            reversible=False,
            render_target="inline",
            handler=_launch_handler,
            params_schema={"type": "object", "properties": {}, "required": []},
            workflow_invocable=True,
        )
    )
