"""DAILYREBAL1 — the daily scheduled rebalancer.

ONCE PER US BUSINESS DAY, PER ORG, AFTER TODAY'S INPUTS ARE READY
──────────────────────────────────────────────────────────────────────────────
:func:`daily_tick` is a step of the Render cron tick (workflow_scheduler_tick.py, every 5 minutes). For every
NON-test org with an opted-in model link it calls :func:`org_daily`, which acts only

* on a US business day (services.us_business_days — weekends and NYSE holidays never run),
* at or after the org's run time (org setting ``replication.schedule.run_time``, New York, default 07:30),
* once the inputs are ready: the end-of-day prices day row for the LAST CLOSE (portfolio.price_refresh_days, scope
  'platform', price_day = the prior business day) reads ``complete`` — or the org's fallback time has passed
  (``replication.schedule.prices_fallback_time``, default 10:00 New York), and then the day is recorded as run on
  PARTIAL prices (``partial_prices``, the prices row's status, the oldest valuation date used) — AND the nightly
  portfolio refresh has run (the UTC clock past 07:15 and the org's ``entity_holdings`` rows ``source='portfolio'``
  at today's UTC date: PORTENTRY1's nightly records no run row, so its output is the signal).

ONE ROW PER ORG PER DAY: ``public.rebalance_schedule_days`` (unique (org_id, run_day)) — waiting (with the reason)
→ running (a lease, so two ticks never run one day) → completed | failed. Every account's outcome is on it with its
reason; a failure is recorded on it and is never raised into the tick.

WHICH ACCOUNTS — the link's OWN settings ask for it: ``rebalance_frequency`` set (daily / weekly / monthly /
quarterly / semiannually / annually — account_models.FREQUENCY_LABELS) or the link's own ``drift_threshold`` set.
Skipped, each with its reason: closed; restricted (the authority × custody rule — authority.refusal on the
'proposal:scheduled' path: read-only, advise, managed-outside); advice-only (non-discretionary — a person rebalances
it); a long/short model (its proposals are a person's); a model with no approved version; an OPEN proposal (any
status draft / awaiting approval / approved — never superseded by this step); not due (drift within the threshold
and the frequency period not elapsed); nothing to trade. A test org is never enumerated; a verify passes
``include_test_orgs=True`` for its own fixture org.

DUE: drift (rebalance_proposals.compute's drift_before) above the account's threshold (drift_threshold(): link →
model → org → default) → trigger ``drift``; else the frequency period elapsed since the LAST proposal generated for
the link (any trigger, any status, the link's whole version group; New York dates) → trigger ``scheduled``.

WHAT IT DOES — ``rebalance_proposals.generate(trigger, maker_kind='system')``, under the account lock with the open
check inside it. The system SUBMITS its own proposal for review (rep5): a ``rebalance_proposal_review`` task a
PERSON holding approve_proposals decides. Nothing here approves, executes, routes or sends — the custody cliff; see
services.workflow_autonomy ``rebalance.scheduled_daily`` (eligible, draft_only). Then ONE summary to-do per
approve_proposals holder per org per day ("3 proposals waiting — Olivo Household"), on top of the engine's own
per-task to-dos (the approval controls).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from services import account_models as AM
from services import us_business_days as BD

log = logging.getLogger(__name__)

T_DAY = "public.rebalance_schedule_days"
NY = ZoneInfo("America/New_York")
ACTION_KEY = "rebalance.scheduled_daily"          # services.workflow_autonomy / services.sod
RUN_TIME_SETTING = "replication.schedule.run_time"
FALLBACK_SETTING = "replication.schedule.prices_fallback_time"
DEFAULT_RUN_TIME = time(7, 30)
DEFAULT_FALLBACK_TIME = time(10, 0)
NIGHTLY_READY_UTC = time(7, 15)                    # the nightly rollup window is 07:00–07:15 UTC
PRICES_SCOPE = "platform"
LEASE_S = 900
TODO_SOURCE = "rebalance_schedule"
TODO_RELATED = "rebalance_schedule_day"
TODO_ACTION = "/portfolio/accounts"
TODO_CATEGORY = "execution"
FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "semiannually": 6, "annually": 12}
FREQUENCY_DAYS = {"daily": 1, "weekly": 7}
STATUS_LABELS = {"waiting": "Waiting for today's inputs", "running": "Running", "completed": "Completed",
                 "failed": "Failed"}
SKIP_LABELS = {"closed": "Account closed", "restricted": "Restricted — the authority does not allow a model trade",
               "advice_only": "Advice only — a person rebalances a non-discretionary account",
               "long_short": "Long/short model — its proposals are a person's",
               "no_version": "The model has no approved version", "open_proposal": "An open proposal already exists",
               "not_due": "Not due", "nothing_to_trade": "Nothing to trade", "error": "Failed"}
ACTION_LABELS = {"proposed": "Proposal made", "skipped": "Skipped", "failed": "Failed"}


# ── Pure rules (the self-checks call these directly) ───────────────────────
def parse_hhmm(v, default: time) -> time:
    try:
        hh, mm = str(v).strip().strip('"').split(":")
        t = time(int(hh), int(mm))
        return t
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the stated default
        return default


def local_now(now_utc: datetime) -> datetime:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(NY)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = (date(y + (m // 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def next_due(freq: str | None, last: date | None) -> date | None:
    """The first New York date the frequency is due again after a proposal made on ``last`` (None = due now)."""
    if not freq or freq not in AM.FREQUENCY_LABELS:
        return None
    if last is None:
        return date.min
    if freq in FREQUENCY_DAYS:
        return last + timedelta(days=FREQUENCY_DAYS[freq])
    return add_months(last, FREQUENCY_MONTHS[freq])


def frequency_due(freq: str | None, last: date | None, today: date) -> bool:
    nd = next_due(freq, last)
    return nd is not None and nd <= today


def last_close_day(today: date) -> date:
    """The last US business day BEFORE ``today`` — the close whose prices a morning run needs.
    (us_business_days.prior_business_day(d) is ``d`` itself on a business day, so it is asked about the day before.)"""
    return BD.prior_business_day(today - timedelta(days=1))


def readiness(*, prices_status: str | None, nightly_ok: bool, now_local: datetime, fallback: time) -> dict:
    """{ready, partial, waiting_reason}. Prices complete → ready; past the fallback → ready on partial prices;
    the nightly refresh is required either way."""
    complete = prices_status == "complete"
    past_fallback = now_local.time() >= fallback
    if not nightly_ok:
        return {"ready": False, "partial": False,
                "waiting_reason": "the nightly portfolio refresh has not run for today yet"}
    if complete:
        return {"ready": True, "partial": False, "waiting_reason": None}
    if past_fallback:
        return {"ready": True, "partial": True, "waiting_reason": None}
    return {"ready": False, "partial": False,
            "waiting_reason": (f"prices for the last close are {prices_status or 'not started'} — waiting until "
                               f"complete or {fallback.strftime('%H:%M')} New York")}


def choose_trigger(*, drift: Decimal, threshold: Decimal, freq_due: bool) -> str | None:
    if drift > threshold:
        return "drift"
    if freq_due:
        return "scheduled"
    return None


def summary_title(proposals: list[dict]) -> str:
    n = len(proposals)
    names = []
    for p in proposals:
        h = p.get("household") or "No household"
        if h not in names:
            names.append(h)
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    return f"{n} proposal{'s' if n != 1 else ''} waiting — {shown}"


# ── Reads ───────────────────────────────────────────────────────────────────
async def _setting_time(conn, org_id, key: str, default: time) -> time:
    from services.org_settings import get_setting_with_origin

    try:
        v, is_default = await get_setting_with_origin(conn, uuid.UUID(str(org_id)), key)
    except Exception:  # noqa: BLE001
        return default
    return default if (v is None or is_default) else parse_hhmm(v, default)


async def prices_day_row(conn, price_day: date, scope: str = PRICES_SCOPE) -> dict | None:
    row = await conn.fetchrow("SELECT status, price_day FROM portfolio.price_refresh_days WHERE scope = $1 "
                              "AND price_day = $2", scope, price_day)
    return dict(row) if row else None


async def nightly_ran(conn, org_id, now_utc: datetime) -> bool:
    if now_utc.astimezone(timezone.utc).time() < NIGHTLY_READY_UTC:
        return False
    return bool(await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM entity_holdings WHERE org_id = $1::uuid AND source = 'portfolio' "
        "AND as_of_date >= $2::date)", str(org_id), now_utc.astimezone(timezone.utc).date()))


async def opted_in_links(conn, org_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {AM.T_LINK} WHERE org_id = $1::uuid AND status = 'active' AND valid_to IS NULL "
        "AND system_to IS NULL AND (rebalance_frequency IS NOT NULL OR drift_threshold IS NOT NULL) "
        "ORDER BY account_id", str(org_id))]


async def last_proposal_day(conn, link: dict) -> date | None:
    from services import rebalance_proposals as RP

    ts = await conn.fetchval(
        f"SELECT max(p.created_at) FROM {RP.T_P} p JOIN {AM.T_LINK} l ON l.id = p.link_id "
        "WHERE l.link_group_id = $1::uuid AND p.archived_from_id IS NULL", str(link["link_group_id"]))
    return ts.astimezone(NY).date() if ts else None


async def _household(conn, account: dict) -> str | None:
    if not account.get("household_id"):
        return None
    return await conn.fetchval("SELECT name FROM households WHERE id = $1::uuid", str(account["household_id"]))


async def _approvers(conn, org_id) -> list[str]:
    rows = await conn.fetch(
        """SELECT DISTINCT u.id FROM users u
           JOIN user_roles ur ON ur.user_id = u.id
           JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id
           JOIN role_permissions rp ON rp.role_id = ur.role_id
           JOIN permissions p ON p.id = rp.permission_id
           WHERE u.org_id = $1::uuid AND p.name = $2 AND u.is_active
             AND COALESCE(u.status, 'active') = 'active' ORDER BY u.id""", str(org_id), AM.APPROVE)
    return [str(r["id"]) for r in rows]


# ── One account ─────────────────────────────────────────────────────────────
def _skip(account_id, reason: str, detail: str, **kw) -> dict:
    return {"account_id": str(account_id), "action": "skipped", "reason": reason, "detail": detail, **kw}


async def evaluate_account(pool, *, org_id, link: dict, today: date, day_id) -> dict:
    """Decide one opted-in account and (when due) generate its system proposal. The open check and the generate run
    under ONE account lock, so this step never supersedes a proposal a person (or another scan) just made."""
    from services import authority as AU
    from services import long_short as LS
    from services import rebalance_proposals as RP

    async with RP.account_lock(pool, str(link["account_id"])):
        async with pool.acquire() as conn:
            account = await AM.load_account(conn, org_id, link["account_id"])
            base = {"account": account["account_number_masked"], "household": await _household(conn, account),
                    "frequency": link.get("rebalance_frequency"),
                    "link_drift_threshold": str(link["drift_threshold"]) if link.get("drift_threshold") is not None
                    else None}
            if AM.is_closed(account, today):
                return _skip(account["id"], "closed", f"closed {account['closed_on'].isoformat()}", **base)
            why = AU.refusal(account, "a scheduled rebalance", path="proposal:scheduled")
            if why:
                return _skip(account["id"], "restricted", why, **base)
            if link.get("advice_only") or not account.get("is_discretionary"):
                return _skip(account["id"], "advice_only", SKIP_LABELS["advice_only"], **base)
            model = await AM.model_brief(conn, org_id, link["model_id"])
            base["model"] = model["name"]
            if not model["live_version_id"]:
                return _skip(account["id"], "no_version", SKIP_LABELS["no_version"], **base)
            if await LS.model_kind(conn, model["id"]) is not None:
                return _skip(account["id"], "long_short", SKIP_LABELS["long_short"], **base)
            cur = await RP.open_proposal(conn, account["id"])
            if cur:
                return _skip(account["id"], "open_proposal",
                             f"proposal {cur['id']} is {RP.STATUS_LABELS.get(cur['status'], cur['status']).lower()} "
                             f"({RP.TRIGGER_LABELS.get(cur['trigger'], cur['trigger'])})",
                             open_proposal_id=str(cur["id"]), **base)
            last = await last_proposal_day(conn, link)
            voa = await AM.valuation_as_of(conn, org_id, account["id"])
            c = await RP.compute(conn, org_id, account, link, model["live_version_id"], today=today)
        r = c["result"]
        drift, thr = r.drift_before, c["threshold"]
        fdue = frequency_due(link.get("rebalance_frequency"), last, today)
        nd = next_due(link.get("rebalance_frequency"), last)
        base.update({"drift": str(drift), "threshold": str(thr), "threshold_source": c.get("threshold_source"),
                     "last_proposal_day": last.isoformat() if last else None,
                     "valuation_as_of": voa.isoformat() if voa else None,
                     "next_due": nd.isoformat() if nd and nd != date.min else None})
        trig = choose_trigger(drift=drift, threshold=thr, freq_due=fdue)
        if trig is None:
            return _skip(account["id"], "not_due",
                         f"drift {drift:.4%} is within {thr:.4%}"
                         + (f"; next scheduled rebalance due {nd.isoformat()}" if nd and nd != date.min else ""),
                         **base)
        if not c["lines"]:
            return _skip(account["id"], "nothing_to_trade",
                         f"{'drift' if trig == 'drift' else 'the schedule'} is due but the trade list is empty",
                         **base)
        ref = {"schedule_day_id": str(day_id), "drift": str(drift), "threshold": str(thr),
               "frequency": link.get("rebalance_frequency"), "last_proposal_day": base["last_proposal_day"]}
        p = await RP.generate(pool, org_id=org_id, account_id=account["id"], trigger=trig, maker_kind="system",
                              trigger_ref=ref, today=today)
    return {"account_id": str(account["id"]), "action": "proposed", "reason": trig, "proposal_id": str(p["id"]),
            "status": p["status"], "trades": len(c["lines"]),
            "detail": (f"drift {drift:.4%} above {thr:.4%}" if trig == "drift"
                       else f"{AM.FREQUENCY_LABELS[link['rebalance_frequency']].lower()} rebalance due"), **base}


# ── The day row ─────────────────────────────────────────────────────────────
async def _day(conn, org_id, run_day: date) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_DAY} WHERE org_id = $1::uuid AND run_day = $2", str(org_id), run_day)
    return dict(row) if row else None


def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else o.isoformat()
                      if isinstance(o, (date, datetime)) else str(o))


async def _summary_todo(conn, org_id, day_id, proposals: list[dict]) -> list[str]:
    """ONE open to-do per approve_proposals holder for the org's day (idempotent on the day row)."""
    if not proposals:
        return []
    title = summary_title(proposals)
    lines = [f"{p.get('household') or 'No household'} · {p.get('account')} · "
             f"{'Drift over threshold' if p['reason'] == 'drift' else 'Scheduled rebalance'} ({p.get('trades')} trades)"
             for p in proposals]
    detail = "Scheduled rebalancing made these proposals; each needs a person's approval.\n" + "\n".join(lines)
    ids = []
    for uid in await _approvers(conn, org_id):
        existing = await conn.fetchval(
            "SELECT id FROM member_todos WHERE org_id = $1::uuid AND user_id = $2::uuid AND source = $3 "
            "AND related_type = $4 AND related_id = $5::uuid LIMIT 1", str(org_id), uid, TODO_SOURCE, TODO_RELATED,
            str(day_id))
        if existing:
            await conn.execute("UPDATE member_todos SET title = $2, detail = $3, updated_at = now() WHERE id = $1",
                               existing, title, detail)
            ids.append(str(existing))
            continue
        ids.append(str(await conn.fetchval(
            """INSERT INTO member_todos (org_id, user_id, kind, category, source, related_type, related_id, title,
                   detail, action_key, priority, status)
               VALUES ($1::uuid, $2::uuid, 'actual', $3, $4, $5, $6::uuid, $7, $8, $9, 15, 'open') RETURNING id""",
            str(org_id), uid, TODO_CATEGORY, TODO_SOURCE, TODO_RELATED, str(day_id), title, detail, TODO_ACTION)))
    return ids


async def org_daily(pool, *, org_id, now_utc: datetime | None = None, include_test_orgs: bool = False,
                    prices_scope: str = PRICES_SCOPE, links: list[dict] | None = None) -> dict:
    """The org's daily step (the caller set its RLS context). Never raises: a failure is recorded on the day row."""
    from services import orgs as ORGS

    now_utc = now_utc or datetime.now(timezone.utc)
    now_l = local_now(now_utc)
    today = now_l.date()
    if not BD.is_business_day(today):
        return {"action": "not_business_day", "day": today.isoformat(),
                "reason": BD.holiday_name(today) or "weekend"}
    async with pool.acquire() as conn:
        test = await ORGS.is_test_org(conn, org_id)
        if test and not include_test_orgs:
            return {"action": "skipped", "reason": "test org (organizations.is_test) — schedulers never act on one"}
        run_t = await _setting_time(conn, org_id, RUN_TIME_SETTING, DEFAULT_RUN_TIME)
        if now_l.time() < run_t:
            return {"action": "before_time", "run_time": run_t.strftime("%H:%M")}
        fallback = await _setting_time(conn, org_id, FALLBACK_SETTING, DEFAULT_FALLBACK_TIME)
        await conn.execute(
            f"INSERT INTO {T_DAY} (org_id, run_day, run_time_local, is_fixture) VALUES ($1::uuid, $2, $3, $4) "
            "ON CONFLICT (org_id, run_day) DO NOTHING", str(org_id), today, run_t.strftime("%H:%M"), bool(test))
        day = await _day(conn, org_id, today)
        if day["status"] in ("completed", "failed"):
            return {"action": "already", "day_id": str(day["id"]), "status": day["status"]}
        if day["status"] == "running" and day["lease_until"] and day["lease_until"] > now_utc:
            return {"action": "busy", "day_id": str(day["id"])}
        prices_day = last_close_day(today)
        prow = await prices_day_row(conn, prices_day, prices_scope)
        nightly = await nightly_ran(conn, org_id, now_utc)
        rd = readiness(prices_status=prow["status"] if prow else None, nightly_ok=nightly, now_local=now_l,
                       fallback=fallback)
        if not rd["ready"]:
            await conn.execute(f"UPDATE {T_DAY} SET waiting_reason = $2, prices_day = $3, prices_status = $4, "
                               "nightly_ok = $5 WHERE id = $1 AND status = 'waiting'", day["id"], rd["waiting_reason"],
                               prices_day, prow["status"] if prow else None, nightly)
            return {"action": "waiting", "day_id": str(day["id"]), "reason": rd["waiting_reason"]}
        claimed = await conn.fetchrow(
            f"UPDATE {T_DAY} SET status = 'running', lease_until = $2, attempts = attempts + 1, started_at = $3, "
            "waiting_reason = NULL, prices_day = $4, prices_status = $5, partial_prices = $6, nightly_ok = $7 "
            "WHERE id = $1 AND (status = 'waiting' OR (status = 'running' AND (lease_until IS NULL OR lease_until < $3)))"
            " RETURNING *", day["id"], now_utc + timedelta(seconds=LEASE_S), now_utc, prices_day,
            prow["status"] if prow else None, rd["partial"], nightly)
        if claimed is None:
            return {"action": "busy", "day_id": str(day["id"])}
    day_id = day["id"]
    results, errors = [], []
    status = "completed"
    try:
        if links is None:
            async with pool.acquire() as conn:
                links = await opted_in_links(conn, org_id)
        for ln in links:
            try:
                results.append(await evaluate_account(pool, org_id=org_id, link=ln, today=today, day_id=day_id))
            except Exception as exc:  # noqa: BLE001 — one account never stops the others
                log.exception("scheduled rebalance failed for account %s", ln.get("account_id"))
                msg = f"{type(exc).__name__}: {str(exc)[:400]}"
                results.append({"account_id": str(ln.get("account_id")), "action": "failed", "reason": "error",
                                "detail": msg})
                errors.append(f"account {ln.get('account_id')}: {msg}")
    except Exception as exc:  # noqa: BLE001 — the step itself failed: recorded, never raised
        log.exception("scheduled rebalancing failed for org %s", org_id)
        status = "failed"
        errors.append(f"step: {type(exc).__name__}: {str(exc)[:400]}")
    proposed = [r for r in results if r["action"] == "proposed"]
    vdates = [r["valuation_as_of"] for r in results if r.get("valuation_as_of")]
    todo_ids: list[str] = []
    try:
        async with pool.acquire() as conn:
            todo_ids = await _summary_todo(conn, org_id, day_id, proposed)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"summary to-do: {type(exc).__name__}: {str(exc)[:300]}")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE {T_DAY} SET status = $2, finished_at = now(), lease_until = NULL, checked = $3, proposed = $4, "
            "skipped = $5, failed = $6, results = $7::jsonb, errors = $8::jsonb, todo_ids = $9::uuid[], "
            "oldest_price_date = $10 WHERE id = $1 RETURNING *", day_id, status, len(results), len(proposed),
            sum(1 for r in results if r["action"] == "skipped"), sum(1 for r in results if r["action"] == "failed"),
            _dumps(results), _dumps(errors), todo_ids, date.fromisoformat(min(vdates)) if vdates else None)
    return {"action": "ran", "day_id": str(day_id), "status": row["status"], "checked": row["checked"],
            "proposed": row["proposed"], "skipped": row["skipped"], "failed": row["failed"],
            "partial_prices": row["partial_prices"], "errors": errors, "results": results, "todo_ids": todo_ids}


async def daily_tick(pool, *, now_utc: datetime | None = None) -> dict:
    """The scheduler entrypoint's step: every NON-test org with an opted-in link, each in its own RLS context.
    Never raises."""
    from services import orgs as ORGS
    from services.database import reset_rls_context, set_rls_context

    now_utc = now_utc or datetime.now(timezone.utc)
    if not BD.is_business_day(local_now(now_utc).date()):
        return {"action": "not_business_day"}
    out: dict = {}
    try:
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                orgs = [r["org_id"] for r in await conn.fetch(
                    f"SELECT DISTINCT org_id FROM {AM.T_LINK} x WHERE status = 'active' AND valid_to IS NULL "
                    "AND system_to IS NULL AND (rebalance_frequency IS NOT NULL OR drift_threshold IS NOT NULL) AND "
                    + ORGS.not_test_org_sql("x.org_id"))]
        finally:
            reset_rls_context(tokens)
    except Exception as exc:  # noqa: BLE001
        return {"action": "failed", "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
    for org in orgs:
        tokens = set_rls_context(str(org), False)
        try:
            out[str(org)] = await org_daily(pool, org_id=org, now_utc=now_utc)
        except Exception as exc:  # noqa: BLE001 — one org never stops the others
            out[str(org)] = {"action": "failed", "errors": [f"{type(exc).__name__}: {str(exc)[:400]}"]}
        finally:
            reset_rls_context(tokens)
    return {"action": "ran", "orgs": out}


# ── The panel ───────────────────────────────────────────────────────────────
def day_out(d: dict | None) -> dict | None:
    if not d:
        return None
    res = d["results"] if isinstance(d["results"], list) else json.loads(d["results"] or "[]")
    errs = d["errors"] if isinstance(d["errors"], list) else json.loads(d["errors"] or "[]")
    return {"id": str(d["id"]), "run_day": d["run_day"].isoformat(), "status": d["status"],
            "status_label": STATUS_LABELS.get(d["status"], d["status"]), "run_time_local": d["run_time_local"],
            "started_at": d["started_at"].isoformat() if d["started_at"] else None,
            "finished_at": d["finished_at"].isoformat() if d["finished_at"] else None,
            "prices_day": d["prices_day"].isoformat() if d["prices_day"] else None,
            "prices_status": d["prices_status"], "partial_prices": d["partial_prices"],
            "oldest_price_date": d["oldest_price_date"].isoformat() if d["oldest_price_date"] else None,
            "nightly_ok": d["nightly_ok"], "waiting_reason": d["waiting_reason"],
            "checked": d["checked"], "proposed": d["proposed"], "skipped": d["skipped"], "failed": d["failed"],
            "results": [{**r, "action_label": ACTION_LABELS.get(r.get("action"), r.get("action")),
                         "reason_label": (SKIP_LABELS.get(r.get("reason"), r.get("reason"))
                                          if r.get("action") != "proposed" else
                                          ("Drift over threshold" if r.get("reason") == "drift"
                                           else "Scheduled rebalance"))} for r in res],
            "errors": errs}


async def panel(conn, org_id, *, now_utc: datetime | None = None) -> dict:
    """Today's run (or the latest) and the proposals waiting from it — for Portfolio → Accounts."""
    from services import rebalance_proposals as RP

    today = local_now(now_utc or datetime.now(timezone.utc)).date()
    row = await conn.fetchrow(f"SELECT * FROM {T_DAY} WHERE org_id = $1::uuid ORDER BY run_day DESC LIMIT 1",
                              str(org_id))
    d = dict(row) if row else None
    run_t = await _setting_time(conn, org_id, RUN_TIME_SETTING, DEFAULT_RUN_TIME)
    fb = await _setting_time(conn, org_id, FALLBACK_SETTING, DEFAULT_FALLBACK_TIME)
    opted = int(await conn.fetchval(
        f"SELECT count(*) FROM {AM.T_LINK} WHERE org_id = $1::uuid AND status = 'active' AND valid_to IS NULL "
        "AND system_to IS NULL AND (rebalance_frequency IS NOT NULL OR drift_threshold IS NOT NULL)", str(org_id)))
    waiting = []
    if d:
        ids = [r.get("proposal_id") for r in (day_out(d)["results"]) if r.get("proposal_id")]
        if ids:
            for p in await conn.fetch(f"SELECT id, account_id, status, trigger, created_at FROM {RP.T_P} WHERE "
                                      "org_id = $1::uuid AND id = ANY($2::uuid[]) AND system_to IS NULL "
                                      "ORDER BY created_at", str(org_id), ids):
                waiting.append({"id": str(p["id"]), "account_id": str(p["account_id"]), "status": p["status"],
                                "status_label": RP.STATUS_LABELS.get(p["status"], p["status"]),
                                "trigger": p["trigger"], "trigger_label": RP.TRIGGER_LABELS.get(p["trigger"])})
    return {"today": today.isoformat(), "is_business_day": BD.is_business_day(today),
            "run_time_local": run_t.strftime("%H:%M"), "prices_fallback_local": fb.strftime("%H:%M"),
            "opted_in_accounts": opted, "day": day_out(d), "is_today": bool(d and d["run_day"] == today),
            "proposals": waiting}


__all__ = ["daily_tick", "org_daily", "evaluate_account", "panel", "readiness", "frequency_due", "next_due",
           "choose_trigger", "summary_title", "parse_hhmm", "last_close_day", "ACTION_KEY"]
