"""SECMASTER1 — the security-master jobs, run on the workflow engine (Task 7).

WHERE THEY LIVE (Task 1c). Workflows, triggers and runs have no platform scope: every row carries an org_id and
the RLS on workflow_runs is ``org_id = current org OR is_super_admin``. The platform's own org — Hollisworks
(bb347258-…), whose only member is platform staff — already exists, so the jobs are SYSTEM workflows in THAT org:

  * a 2nd Act org admin's Runs screen filters ``r.org_id = <their org>`` (and RLS agrees) → never sees them;
  * a Super Admin's Runs screen lists every org → sees them, with "Run now";
  * a held run alerts ``started_by`` (the trigger's creator: the platform org's Super Admin) and the platform org's
    manage_org_settings holders — never a client org's admins;
  * the names start ``system:`` → the library shows no Delete and the API refuses one (PERMS1 WF-06).

THE ACTIONS are ``workflow_invocable`` Service Task verbs, tier 2 (they write reference data; none of the custody
cliff's four classes — no trade, money, filing or GL posting — is reachable). Each:
  * refuses to run outside a platform-job org (PLATFORM_JOB_ORG_IDS) — a client org cannot borrow it;
  * requires REQUIRED_PERMISSION, a key deliberately NOT in the permissions catalog: the engine re-checks it
    against the run's starter and only a Super Admin passes (Super Admin bypass first), and the assistant never
    lists it (a Super Admin's grants are the catalog's names);
  * works inside a deadline (STEP_DEADLINE_S) so a step stays inside the scheduler tick — the rest resumes next run;
  * writes one portfolio.security_data_runs row (the job history) and returns its counts + the review items it
    created as the step's output (workflow_run_steps.result → handler_data);
  * RAISES on failure → the engine HOLDS the run and raises the held-run alert. A held run blocks the trigger
    until a Super Admin closes it (close_held_run) — deliberate: a broken feed must not re-fire every night.

The one-time load never runs here (scripts/secmaster_initial_load.py, run by the operator)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from services.action_registry import REGISTRY, AssistantAction

PLATFORM_ORG_ID = uuid.UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")
#: orgs whose runs may execute these actions (the verify adds its own fixture platform org, by id)
PLATFORM_JOB_ORG_IDS: set = {PLATFORM_ORG_ID}
REQUIRED_PERMISSION = "run_platform_jobs"
STEP_DEADLINE_S = 200.0
NIGHTLY_MAX_NEW = 2000          # new securities per source per night (the operator's load does the bulk)
OPENFIGI_REQUESTS_PER_STEP = 40  # keyless: 400 identifiers in ~100 s
MUTUAL_FUNDS_MAX_NEW = 5000     # SECMASTER2: new share classes per monthly run (the operator's load does the bulk)


@dataclass(frozen=True)
class StepSpec:
    key: str
    action: str
    display_name: str


@dataclass(frozen=True)
class JobSpec:
    key: str
    name: str
    description: str
    cron: str
    timezone: str
    schedule_note: str
    steps: tuple


JOBS: dict[str, JobSpec] = {
    "security_master_nightly": JobSpec(
        "security_master_nightly", "system:Security master — nightly",
        "Nasdaq Trader universe + SEC company tickers → matcher → delisting check; then OpenFIGI for new "
        "identifiers only (within budget). Platform job (SECMASTER1).",
        "45 18 * * 1-5", "America/New_York",
        "Weekdays 18:45 New York (22:45 UTC in summer, 23:45 UTC in winter) — after the 16:00 close and the "
        "symbol directory's evening refresh",
        (StepSpec("universe", "security_master.universe", "Nasdaq + SEC tickers → matcher → delisting"),
         StepSpec("openfigi", "security_master.openfigi_new", "OpenFIGI for new identifiers"))),
    "security_master_13f": JobSpec(
        "security_master_13f", "system:Security master — 13F list",
        "Checks the SEC Official List of Section 13(f) Securities daily; loads a new quarter once; maps pending "
        "CUSIPs through OpenFIGI within budget. Platform job (SECMASTER1).",
        "30 7 * * *", "America/New_York", "Daily 07:30 New York (11:30 / 12:30 UTC)",
        (StepSpec("sec13f", "security_master.sec13f", "13(f) list: new quarter + CUSIP mapping"),)),
    "security_master_attributes": JobSpec(
        "security_master_attributes", "system:Security master — attributes",
        "FinanceDatabase sector / industry / country / fund category for securities already in the master. "
        "Platform job (SECMASTER1).",
        "0 9 * * 6", "America/New_York", "Saturdays 09:00 New York (13:00 / 14:00 UTC)",
        (StepSpec("attributes", "security_master.attributes", "FinanceDatabase attributes"),)),
    "security_data_nightly": JobSpec(
        "security_data_nightly", "system:Security data — nightly",
        "marketdata1's nightly (refresh within budgets → platform quality → org quality → summary), moved onto the "
        "workflow engine: its Render cron is no longer needed. Platform job (SECMASTER1).",
        "30 19 * * 1-5", "America/New_York", "Weekdays 19:30 New York (23:30 / 00:30 UTC)",
        (StepSpec("security_data", "security_data.nightly", "Security data nightly"),)),
    # SECMASTER2 — seeded INACTIVE (scripts/_apply_secmaster2.py --seed-jobs); the operator decides
    "security_master_mutual_funds": JobSpec(
        "security_master_mutual_funds", "system:Security data — mutual funds",
        "SEC mutual-fund share classes (company_tickers_mf.json: CIK, series, class, ticker) → matcher: an existing "
        "ticker is matched, a new mutual-fund ticker is created, a conflict goes to review. Platform job "
        "(SECMASTER2).",
        "0 8 3 * *", "America/New_York", "Monthly, the 3rd at 08:00 New York (12:00 / 13:00 UTC)",
        (StepSpec("mutual_funds", "security_master.mutual_funds", "SEC mutual-fund share classes"),)),
    # PRICES1 — seeded INACTIVE (scripts/_apply_prices1.py --seed-jobs); the operator switches it on after the backfill
    "security_data_prices": JobSpec(
        "security_data_prices", "system:Security data — prices",
        "End-of-day prices for the coverage set (held, modelled, replicated, option underlyings, benchmarks) through "
        "the price receptor, in rank order with fallback: the last few days for every covered security (gap fill) and "
        "full history for new members, within each source's daily budget; the rest resumes next night. Platform job "
        "(PRICES1).",
        "15 21 * * 1-5", "America/New_York",
        "Weekdays 21:15 New York (01:15 UTC in summer, 02:15 UTC in winter) — after the 16:00 close and after mutual "
        "funds strike and publish their NAVs (~18:00–20:00 New York)",
        (StepSpec("prices", "security_data.prices", "End-of-day prices (gap fill + new history)"),)),
}
JOB_BY_NAME = {j.name: j for j in JOBS.values()}

#: the network boundary for the actions — the verify replaces it with a recorded-fixture factory
TRANSPORT_FACTORY = None
#: verify only (production leaves both empty): extra SyncContext fields (the delisting scope) and run_nightly kwargs
SYNC_OVERRIDES: dict = {}
NIGHTLY_KWARGS: dict = {}
#: PRICES1 verify only: extra services.security_data.prices.nightly kwargs (the fixture securities, the fixture rank)
PRICES_KWARGS: dict = {}


def make_transport():
    if TRANSPORT_FACTORY is not None:
        return TRANSPORT_FACTORY()
    from services.security_data.providers.reference_common import RealSourceTransport

    return RealSourceTransport()


class PlatformJobError(RuntimeError):
    pass


# ── BPMN + seeding ──────────────────────────────────────────────────────────
def bpmn_xml(spec: JobSpec) -> str:
    pid = "p_" + spec.key
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n'
             f'<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="D_{spec.key}" '
             f'targetNamespace="https://hollisworks.com/bpmn/system"><bpmn:process id="{pid}" isExecutable="true">',
             '<bpmn:startEvent id="start"><bpmn:outgoing>f0</bpmn:outgoing></bpmn:startEvent>']
    for i, s in enumerate(spec.steps):
        parts.append(f'<bpmn:serviceTask id="{s.key}" name="{s.display_name.replace("&", "and")}">'
                     f'<bpmn:incoming>f{i}</bpmn:incoming><bpmn:outgoing>f{i + 1}</bpmn:outgoing></bpmn:serviceTask>')
    n = len(spec.steps)
    parts.append(f'<bpmn:endEvent id="end"><bpmn:incoming>f{n}</bpmn:incoming></bpmn:endEvent>')
    nodes = ["start"] + [s.key for s in spec.steps] + ["end"]
    for i in range(n + 1):
        parts.append(f'<bpmn:sequenceFlow id="f{i}" sourceRef="{nodes[i]}" targetRef="{nodes[i + 1]}"/>')
    parts.append("</bpmn:process></bpmn:definitions>")
    return "".join(parts)


async def platform_job_owner(conn, org_id=PLATFORM_ORG_ID):
    """The platform org's admitted Super Admin — the trigger's creator, so a held run alerts a Super Admin."""
    uid = await conn.fetchval(
        "SELECT id FROM users WHERE org_id = $1::uuid AND role = 'super_admin' AND is_active AND "
        "COALESCE(status, 'active') = 'active' ORDER BY created_at NULLS LAST, id LIMIT 1", str(org_id))
    if uid is None:
        raise PlatformJobError(f"the platform org {org_id} has no active Super Admin to own its scheduled jobs")
    return uid


async def ensure_platform_jobs(conn, *, org_id=PLATFORM_ORG_ID, created_by, jobs: dict | None = None,
                               active: bool = True) -> dict:
    """Idempotent: each job's system definition (v1, its service-task steps) and ONE scheduled trigger, in
    ``org_id``. An existing definition is validated (same steps) and left alone. Caller holds a transaction with
    platform write context."""
    out = {}
    for key, spec in (jobs or JOBS).items():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"{org_id}:{spec.name}")
        row = await conn.fetchrow(
            "SELECT d.id, v.id AS vid FROM workflow_definitions d JOIN workflow_versions v ON "
            "v.workflow_definition_id = d.id AND v.is_current WHERE d.org_id = $1::uuid AND d.name = $2 "
            "ORDER BY d.created_at LIMIT 1", str(org_id), spec.name)
        created = row is None
        if row is None:
            def_id = await conn.fetchval(
                "INSERT INTO workflow_definitions (org_id, name, description, is_active, created_by) "
                "VALUES ($1::uuid, $2, $3, true, $4::uuid) RETURNING id", str(org_id), spec.name, spec.description,
                str(created_by))
            vid = await conn.fetchval(
                "INSERT INTO workflow_versions (workflow_definition_id, org_id, version_number, bpmn_xml, "
                "change_summary, is_current, created_by) VALUES ($1, $2::uuid, 1, $3, $4, true, $5::uuid) RETURNING id",
                def_id, str(org_id), bpmn_xml(spec), "SECMASTER1 platform job (seeded)", str(created_by))
            for s in spec.steps:
                await conn.execute(
                    "INSERT INTO workflow_steps (workflow_version_id, org_id, step_key, step_type, autonomy_tier, "
                    "action_registry_key, display_name) VALUES ($1, $2::uuid, $3, 'service', 3, $4, $5)",
                    vid, str(org_id), s.key, s.action, s.display_name)
        else:
            def_id, vid = row["id"], row["vid"]
            have = sorted(r["action_registry_key"] or "" for r in await conn.fetch(
                "SELECT action_registry_key FROM workflow_steps WHERE workflow_version_id = $1", vid))
            if have != sorted(s.action for s in spec.steps):
                raise PlatformJobError(f"{spec.name}: its current version's steps {have} are not the job's — "
                                       "refusing to schedule a different process")
        trig = await conn.fetchval(
            "SELECT id FROM workflow_triggers WHERE workflow_definition_id = $1 AND org_id = $2::uuid AND "
            "trigger_type = 'scheduled' ORDER BY created_at LIMIT 1", def_id, str(org_id))
        if trig is None:
            trig = await conn.fetchval(
                "INSERT INTO workflow_triggers (workflow_definition_id, org_id, trigger_type, schedule_cron, timezone, "
                "is_active, created_by) VALUES ($1, $2::uuid, 'scheduled', $3, $4, $5, $6::uuid) RETURNING id",
                def_id, str(org_id), spec.cron, spec.timezone, active, str(created_by))
        out[key] = {"definition_id": def_id, "version_id": vid, "trigger_id": trig, "created": created}
    return out


# ── the actions ─────────────────────────────────────────────────────────────
def _trigger_kind(run_context: dict | None) -> str:
    rc = run_context or {}
    if rc.get("secmaster_test"):
        return "test"
    return "scheduled" if rc.get("trigger_type") == "scheduled" else "manual"


def _assert_platform(org_id) -> None:
    if org_id is None or uuid.UUID(str(org_id)) not in PLATFORM_JOB_ORG_IDS:
        raise PlatformJobError(f"a platform job runs only in the platform org — not in org {org_id}")


async def _run(job: str, fn, *, pool, org_id, run_context, workflow_run_id) -> dict:
    from services.market_data.adapters import scrub_error
    from services.security_master_sync import SyncContext, record_run

    _assert_platform(org_id)
    started = datetime.now(timezone.utc)
    trig = _trigger_kind(run_context)
    transport = make_transport()
    ctx = SyncContext(pool=pool, transport=transport, run_id=workflow_run_id, deadline_s=STEP_DEADLINE_S,
                      throttle=not (run_context or {}).get("secmaster_test"), **SYNC_OVERRIDES)
    try:
        summary = await fn(ctx, run_context or {})
    except Exception as exc:
        err = scrub_error(exc)
        try:
            await record_run(pool, job=job, trigger=trig, status="failed", started_at=started,
                             summary={"error": err, "workflow_run_id": str(workflow_run_id)})
        finally:
            await transport.aclose()
        raise PlatformJobError(f"{job}: {err}") from None
    await transport.aclose()
    status = "partial" if (summary.get("timed_out") or summary.get("deferred")) else "success"
    await record_run(pool, job=job, trigger=trig, status=status, started_at=started,
                     summary={**{k: v for k, v in summary.items() if k != "review_item_ids"},
                              "workflow_run_id": str(workflow_run_id)})
    reviews = summary.get("review_item_ids") or []
    return {"data": {"job": job, "status": status, **summary, "review_items_created": len(reviews),
                     "review_item_ids": reviews},
            "text": f"{job}: {status}; {len(reviews)} review item(s) created"}


async def _universe(ctx, rc) -> dict:
    from services import security_master_sync as SS

    uni = await SS.sync_universe(ctx, max_new=NIGHTLY_MAX_NEW)
    sec = await SS.sync_sec_tickers(ctx, max_new=NIGHTLY_MAX_NEW)
    return {"nasdaq_trader": {k: v for k, v in uni.items() if k != "review_item_ids"},
            "sec_tickers": {k: v for k, v in sec.items() if k != "review_item_ids"},
            "timed_out": bool(uni.get("timed_out") or sec.get("timed_out")),
            "deferred": int(uni.get("deferred") or 0) + int(sec.get("deferred") or 0),
            "review_item_ids": (uni.get("review_item_ids") or []) + (sec.get("review_item_ids") or [])}


async def _openfigi(ctx, rc) -> dict:
    from services import security_master_sync as SS

    return await SS.sync_openfigi_new(ctx, max_requests=OPENFIGI_REQUESTS_PER_STEP)


async def _sec13f(ctx, rc) -> dict:
    from services import security_master_sync as SS

    return await SS.sync_13f(ctx, map_requests=OPENFIGI_REQUESTS_PER_STEP)


async def _attributes(ctx, rc) -> dict:
    from services import security_master_sync as SS

    return await SS.sync_attributes(ctx)


async def _mutual_funds(ctx, rc) -> dict:
    from services import security_master_sync as SS

    return await SS.sync_mutual_funds(ctx, max_new=MUTUAL_FUNDS_MAX_NEW)


async def _security_data(ctx, rc) -> dict:
    from services.security_data import nightly

    res = await nightly.run_nightly(ctx.pool, trigger="manual" if _trigger_kind(rc) != "scheduled" else "scheduled",
                                    **NIGHTLY_KWARGS)
    s = res.summary() if hasattr(res, "summary") else {}
    if res.errors:
        raise PlatformJobError(f"security data nightly {res.status}: " + "; ".join(str(e) for e in res.errors[:5]))
    return {"security_data_run_id": str(res.run_id), "status": res.status, **s}


async def _prices(ctx, rc) -> dict:
    """PRICES1 — gap fill + new members' history. A source failure raises (→ the run HOLDS, Super Admin alerted)."""
    from services.security_data import prices as P

    summary = await P.nightly(ctx.pool, run_id=ctx.run_id, **PRICES_KWARGS)
    deferred = sum(int((v.get("gap_fill") or {}).get("deferred") or 0) + int((v.get("new_history") or {}).get(
        "deferred") or 0) + int(v.get("new_deferred_to_next_night") or 0) for v in summary["providers"].values())
    return {**summary, "deferred": deferred}


_HANDLERS = {"security_master.universe": ("security_master_universe", _universe),
             "security_master.openfigi_new": ("security_master_openfigi", _openfigi),
             "security_master.sec13f": ("security_master_13f", _sec13f),
             "security_master.attributes": ("security_master_attributes", _attributes),
             "security_data.nightly": ("security_data_nightly", _security_data),
             "security_master.mutual_funds": ("security_master_mutual_funds", _mutual_funds),
             "security_data.prices": ("security_data_prices", _prices)}
_DESCRIPTIONS = {
    "security_master.universe": "Platform job: load the Nasdaq Trader listed universe and SEC company tickers "
                                "into the security master; mark securities absent 3 runs as delisted.",
    "security_master.openfigi_new": "Platform job: map new tickers to FIGIs through OpenFIGI within its budget.",
    "security_master.sec13f": "Platform job: load a new quarter of the SEC 13(f) list once; map pending CUSIPs.",
    "security_master.attributes": "Platform job: add FinanceDatabase sector / industry / country / fund category.",
    "security_data.nightly": "Platform job: marketdata1's security-data nightly (refresh, quality, summary).",
    "security_master.mutual_funds": "Platform job: load SEC mutual-fund share classes (ticker, series and class ids) "
                                    "into the security master.",
    "security_data.prices": "Platform job: end-of-day prices for the coverage set through the price receptor "
                            "(gap fill + new history, within budget).",
}


def _make_handler(action_key: str):
    job, fn = _HANDLERS[action_key]

    async def handler(*, pool=None, user_id=None, org_id=None, run_context=None, workflow_run_id=None, **_):
        return await _run(job, fn, pool=pool, org_id=org_id, run_context=run_context,
                          workflow_run_id=workflow_run_id)

    handler.__name__ = f"platform_job_{job}"
    return handler


def register_actions() -> None:
    for key in _HANDLERS:
        REGISTRY.register(AssistantAction(
            key=key, module="security_master", description=_DESCRIPTIONS[key], access_type="write",
            required_permission=REQUIRED_PERMISSION, tier=2, reversible=False, render_target="inline",
            handler=_make_handler(key), params_schema={"type": "object", "properties": {}, "required": []},
            workflow_invocable=True))


# ── Run now / close a held run / status ─────────────────────────────────────
async def job_rows(conn, org_id=PLATFORM_ORG_ID) -> dict:
    """{job key: {definition_id, version_id, trigger_id, schedule, last_run}} for the platform org's jobs."""
    names = [j.name for j in JOBS.values()]
    defs = await conn.fetch(
        "SELECT d.id, d.name, v.id AS vid, t.id AS tid, t.schedule_cron, t.timezone, t.is_active, t.last_fired_at "
        "FROM workflow_definitions d JOIN workflow_versions v ON v.workflow_definition_id = d.id AND v.is_current "
        "LEFT JOIN workflow_triggers t ON t.workflow_definition_id = d.id AND t.trigger_type = 'scheduled' "
        "WHERE d.org_id = $1::uuid AND d.name = ANY($2::text[])", str(org_id), names)
    out = {}
    for d in defs:
        spec = JOB_BY_NAME[d["name"]]
        last = await conn.fetchrow(
            "SELECT r.id, r.status, r.started_at, r.completed_at, r.error_detail FROM workflow_runs r JOIN "
            "workflow_versions v ON v.id = r.workflow_version_id WHERE v.workflow_definition_id = $1 AND "
            "r.org_id = $2::uuid ORDER BY r.started_at DESC LIMIT 1", d["id"], str(org_id))
        steps = []
        if last is not None:
            steps = [dict(x) for x in await conn.fetch(
                "SELECT ws.step_key, rs.status, rs.result FROM workflow_run_steps rs JOIN workflow_steps ws ON "
                "ws.id = rs.workflow_step_id WHERE rs.workflow_run_id = $1 ORDER BY ws.step_key", last["id"])]
        out[spec.key] = {"key": spec.key, "name": spec.name, "title": spec.name.removeprefix("system:"),
                         "description": spec.description, "schedule_cron": d["schedule_cron"],
                         "timezone": d["timezone"], "schedule_note": spec.schedule_note,
                         "trigger_active": d["is_active"], "last_fired_at": d["last_fired_at"],
                         "definition_id": d["id"], "version_id": d["vid"], "trigger_id": d["tid"],
                         "last_run": dict(last) if last else None, "last_run_steps": steps,
                         # BUGFIX4 — what it fetches; whether a held run blocks the schedule
                         "fetches": FETCHES.get(spec.key),
                         "blocked_by_held_run": bool(last is not None and last["status"] == "held"),
                         "blocked_note": HELD_BLOCKS_NOTE if last is not None and last["status"] == "held" else None}
    return out


async def run_now(pool, *, job_key: str, actor_id, org_id=PLATFORM_ORG_ID, context: dict | None = None) -> dict:
    """A Super Admin's manual start: the SAME engine entrypoint the scheduler uses, under the platform org's RLS
    context, started_by = the Super Admin (who is then alerted if it holds)."""
    from services import workflow_engine
    from services.database import platform_scope, reset_rls_context, set_rls_context

    if job_key not in JOBS:
        raise KeyError(job_key)
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            jobs = await job_rows(conn, org_id)
    if job_key not in jobs:
        raise PlatformJobError(f"{JOBS[job_key].name} is not seeded in the platform org "
                               "(run scripts/_apply_secmaster1.py --seed-jobs)")
    tokens = set_rls_context(str(org_id), False)
    try:
        return await workflow_engine.start_workflow_run(
            pool, jobs[job_key]["version_id"], uuid.UUID(str(org_id)),
            {"trigger_type": "manual", "run_now": True, "job": job_key, **(context or {})}, actor_id)
    finally:
        reset_rls_context(tokens)


# BUGFIX4 (MUSE-OLIVO-O-04-03) — what each job fetches, in plain words, and the price gap. Shown on the Security master
# screen beside the switch. Nothing in this sprint switched a job on: the triggers are seeded inactive by design
# (scripts/_apply_secmaster1.py --seed-jobs) and the operator decides.
FETCHES = {
    "security_master_nightly": "Nasdaq Trader symbol directories and the SEC company-tickers file (new listings, "
                               "delistings); OpenFIGI identifiers for new securities only. Reference data — no prices.",
    "security_master_13f": "The SEC Official List of Section 13(f) Securities (a new quarter once), CUSIPs mapped "
                           "through OpenFIGI. Reference data — no prices.",
    "security_master_attributes": "FinanceDatabase sector / industry / country / fund category (US equities, ETFs and "
                                  "mutual funds) for securities already in the master. Reference data — no prices.",
    "security_master_mutual_funds": "The SEC mutual-fund tickers file (one row per share class: CIK, series, class, "
                                    "ticker). New mutual-fund classes are added; existing tickers gain their SEC ids. "
                                    "Reference data — no prices.",
    "security_data_nightly": "marketdata1's nightly: every configured security-data provider within its budget (FRED "
                             "rates), then the quality checks. It does not fetch end-of-day prices — the prices job "
                             "does.",
    "security_data_prices": "End-of-day closes for the coverage set (held, modelled, replicated, option underlyings, "
                            "benchmarks) from the ranked price sources — today Yahoo Finance (internal use only, "
                            "never shown to members), Tiingo when its key is added. Gap fill for the last few days; "
                            "full history for new members; within each source's daily budget.",
}
PRICE_GAP_NOTE = ("End-of-day prices come from the prices job (internal use only for the free source). Until the "
                  "operator's backfill has run and the job is on, price coverage stays 'unknown' and holdings are "
                  "valued at their entered market value as of their statement date.")
HELD_BLOCKS_NOTE = "A held run blocks this schedule until a Super Admin closes it."


def platform_org():
    """The platform org, read at CALL time (a default argument would freeze it at import; a verify points it at its
    own fixture platform org so no real job is ever switched)."""
    return PLATFORM_ORG_ID


async def set_schedule(conn, *, job_key: str, active: bool, actor_id, org_id=None) -> dict:
    """BUGFIX4 — switch a platform job's scheduled trigger on or off (Super Admin only — the route's gate), audited.
    Caller holds platform scope. Returns {key, trigger_active, was, held_run} — ``held_run`` names a held run that
    will keep the schedule from firing until it is closed."""
    if job_key not in JOBS:
        raise KeyError(job_key)
    org_id = org_id or platform_org()
    jobs = await job_rows(conn, org_id)
    if job_key not in jobs or not jobs[job_key]["trigger_id"]:
        raise PlatformJobError(f"{JOBS[job_key].name} is not seeded in the platform org "
                               "(scripts/_apply_secmaster1.py --seed-jobs)")
    j = jobs[job_key]
    was = bool(j["trigger_active"])
    async with conn.transaction():
        hit = await conn.fetchval("UPDATE workflow_triggers SET is_active = $2 WHERE id = $1 AND org_id = $3::uuid "
                                  "RETURNING id", j["trigger_id"], bool(active), str(org_id))
        if hit is None:   # zero rows: an RLS-blocked write and a vanished trigger look the same — say neither
            raise PlatformJobError(f"the schedule of {JOBS[job_key].name} was not changed (no row updated)")
        await conn.execute(
            "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
            "($1::uuid, $2::uuid, 'security_master.schedule_switched', 'workflow_trigger', $3::uuid, $4::jsonb)",
            str(org_id), str(actor_id), str(j["trigger_id"]),
            __import__("json").dumps({"job": job_key, "from": was, "to": bool(active)}))
    held = (j["last_run"] or {}).get("status") == "held"
    return {"key": job_key, "trigger_active": bool(active), "was": was,
            "held_run": str(j["last_run"]["id"]) if held else None}


async def close_held_run(conn, *, run_id, actor_id, note: str) -> dict | None:
    """Close a HELD platform-job run (→ 'cancelled', with who and why appended to error_detail) so its schedule
    fires again. Only a run of a platform job definition in a platform-job org (PLATFORM_JOB_ORG_IDS); only while
    held. Its alert todos are
    closed with it. Returns the run, or None when it is not a held platform-job run."""
    names = [j.name for j in JOBS.values()]
    row = await conn.fetchrow(
        "UPDATE workflow_runs r SET status = 'cancelled', completed_at = now(), error_detail = "
        "COALESCE(r.error_detail, '') || E'\\n— closed by ' || $3::text || ': ' || $4::text "
        "FROM workflow_versions v JOIN workflow_definitions d ON d.id = v.workflow_definition_id "
        "WHERE r.id = $1::uuid AND r.workflow_version_id = v.id AND r.org_id = ANY($2::uuid[]) AND r.status = 'held' "
        "AND d.name = ANY($5::text[]) RETURNING r.id, r.status", str(run_id), [str(o) for o in PLATFORM_JOB_ORG_IDS],
        str(actor_id), note, names)
    if row is None:
        return None
    from services.workflow_todos import TODO_SOURCE_RUN_HELD

    await conn.execute(
        "UPDATE member_todos SET status = 'done', detail = left(coalesce(detail, '') || $2, 2000), "
        "updated_at = now() WHERE source = $3 AND related_type = 'workflow_run' AND related_id = $1::uuid "
        "AND status = 'open'", str(run_id), " [Closed: a Super Admin closed the held run.]", TODO_SOURCE_RUN_HELD)
    return dict(row)


async def status(conn, org_id=PLATFORM_ORG_ID) -> dict:
    """The Security master status panel (Super Admin): counts by type / source, identifiers by type, last run per
    job and its result, review-queue counts, 13(f) unmapped counts by reason."""
    live = "valid_to IS NULL AND system_to IS NULL"
    by_type = {r["security_type"]: r["n"] for r in await conn.fetch(
        f"SELECT security_type, count(*) AS n FROM portfolio.securities_global WHERE {live} AND merged_into_id IS NULL "
        "GROUP BY 1 ORDER BY 2 DESC")}
    ids = {r["id_type"]: r["n"] for r in await conn.fetch(
        f"SELECT id_type, count(*) AS n FROM portfolio.securities_global_identifiers WHERE {live} GROUP BY 1")}
    by_source = {r["provider_key"]: r["n"] for r in await conn.fetch(
        f"SELECT provider_key, count(DISTINCT global_security_id) AS n FROM portfolio.security_reference_attributes "
        f"WHERE {live} GROUP BY 1")}
    listing = {r["value"]: r["n"] for r in await conn.fetch(
        f"SELECT value, count(*) AS n FROM portfolio.security_reference_attributes WHERE {live} AND field = "
        "'listing_status' AND provider_key = 'nasdaq_trader' GROUP BY 1")}
    review = {r["kind"]: r["n"] for r in await conn.fetch(
        f"SELECT kind, count(*) AS n FROM portfolio.security_master_review WHERE {live} AND status = 'open' "
        "GROUP BY 1")}
    q = await conn.fetchval(f"SELECT max(quarter) FROM portfolio.sec_13f_list WHERE {live}")
    f13 = {}
    if q:
        f13 = {"quarter": q, "by_status": {r["map_status"]: r["n"] for r in await conn.fetch(
            f"SELECT map_status, count(*) AS n FROM portfolio.sec_13f_list WHERE {live} AND quarter = $1 GROUP BY 1",
            q)}, "unmapped_reasons": {r["map_note"]: r["n"] for r in await conn.fetch(
                f"SELECT map_note, count(*) AS n FROM portfolio.sec_13f_list WHERE {live} AND quarter = $1 AND "
                "map_status IN ('not_mapped', 'pending') GROUP BY 1 ORDER BY 2 DESC LIMIT 20", q)}}
    f13["unmapped"] = sum(v for k, v in (f13.get("by_status") or {}).items() if k in ("not_mapped", "pending"))
    history = [dict(r) for r in await conn.fetch(
        "SELECT DISTINCT ON (summary->>'job') summary->>'job' AS job, status, run_trigger, started_at, finished_at, "
        "summary FROM portfolio.security_data_runs WHERE summary ? 'job' ORDER BY summary->>'job', finished_at DESC")]
    return {"securities_by_type": by_type, "securities_total": sum(by_type.values()), "identifiers_by_type": ids,
            "securities_by_source": by_source, "listing_status": listing, "review_open_by_kind": review,
            "review_open": sum(review.values()), "sec_13f": f13, "jobs": await job_rows(conn, org_id),
            "job_history": history}
