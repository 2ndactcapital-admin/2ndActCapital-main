"""PORTENTRY1 Task 7 (GAP-PORT-05) — net worth stays current after a write.

Net worth reads ``entity_holdings``, which only a rollup writes. Before this sprint the one writer was
``POST /portfolio/rollup`` (org-wide, manual), so a record's gross stayed 0 until somebody ran it.

NOW — after any position, valuation, commitment or liability write for a family, the writing path calls
:func:`after_write`. It:

1. finds the FAMILY (:func:`family_of`): the written entities, every member of every household they belong to (by
   membership or primary household), closed under ownership in BOTH directions (entity_relationships ownership and
   entity_ownership). Closed under ownership means every ancestor of every owner is in it, so a scoped rollup's
   look-through buckets are complete;
2. records ONE pending ``portfolio.rollup_requests`` row per (org, family) — a write inside the debounce window
   moves ``run_after`` forward instead of queueing a second run (trailing debounce);
3. runs it once ``run_after`` passes — a background task in the API process; :func:`drain_due` (the scheduler tick
   and the nightly) picks up anything a crashed process left behind.

The rollup is ``portfolio_rollup.rollup_entity_holdings(scope=family)``: it reads, deletes and writes ONLY the
family's buckets — never org-wide on a write. The org-wide run stays ``POST /portfolio/rollup`` and the nightly
(:func:`nightly_rollups`, every org, today's date).

``PORTFOLIO_ROLLUP_DEBOUNCE_S`` (default 2) sets the window; 0 runs the rollup inline before the write returns (the
verify uses it so "net worth after a write" is deterministic).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

from services.portfolio_assets import _OrgWrite

log = logging.getLogger(__name__)
T_REQ = "portfolio.rollup_requests"
DEFAULT_DEBOUNCE_S = 2.0


def debounce_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("PORTFOLIO_ROLLUP_DEBOUNCE_S", DEFAULT_DEBOUNCE_S)))
    except ValueError:
        return DEFAULT_DEBOUNCE_S


def family_key(entity_ids) -> str:
    return hashlib.sha256(",".join(sorted(str(e) for e in entity_ids)).encode()).hexdigest()[:32]


async def family_of(conn, org_id, entity_ids) -> list[str]:
    """The family scope of ``entity_ids`` (see the module docstring). Sorted ids; never empty for a real entity."""
    org = str(org_id)
    seed = {str(e) for e in entity_ids if e}
    if not seed:
        return []
    async with _OrgWrite(conn, org) as c:
        hh = await c.fetch(
            "SELECT DISTINCT m.entity_id::text AS id FROM household_memberships m WHERE m.household_id IN ("
            "  SELECT household_id FROM household_memberships WHERE entity_id = ANY($1::uuid[])"
            "  UNION SELECT primary_household_id FROM entities WHERE id = ANY($1::uuid[]) "
            "  AND primary_household_id IS NOT NULL AND valid_to IS NULL AND system_to IS NULL)"
            " UNION SELECT e.id::text FROM entities e WHERE e.org_id = $2::uuid AND e.primary_household_id IN ("
            "  SELECT primary_household_id FROM entities WHERE id = ANY($1::uuid[]) AND primary_household_id IS NOT NULL"
            "  AND valid_to IS NULL AND system_to IS NULL) AND e.valid_to IS NULL AND e.system_to IS NULL",
            list(seed), org)
        fam = seed | {r["id"] for r in hh}
        edges = await c.fetch(
            "SELECT from_entity_id::text AS a, to_entity_id::text AS b FROM entity_relationships "
            "WHERE org_id = $1::uuid AND relationship_type = 'ownership' AND valid_to IS NULL AND system_to IS NULL "
            "UNION SELECT parent_id::text, child_id::text FROM entity_ownership "
            "WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL", org)
    adj: dict[str, set] = {}
    for e in edges:
        adj.setdefault(e["a"], set()).add(e["b"])
        adj.setdefault(e["b"], set()).add(e["a"])
    todo = list(fam)
    while todo:
        x = todo.pop()
        for y in adj.get(x, ()):
            if y not in fam:
                fam.add(y)
                todo.append(y)
    return sorted(fam)


async def request_rollup(conn, org_id, entity_ids, *, reason: str, actor_id=None, delay_s: float | None = None) -> dict:
    """Record (or extend) the family's pending request. Returns {id, family_key, entity_ids, run_after}."""
    org = str(org_id)
    fam = await family_of(conn, org, entity_ids)
    if not fam:
        return {}
    key = family_key(fam)
    delay = debounce_seconds() if delay_s is None else delay_s
    async with _OrgWrite(conn, org) as c:
        row = await c.fetchrow(
            f"""INSERT INTO {T_REQ} (org_id, family_key, entity_ids, reason, requested_by, run_after)
                VALUES ($1::uuid, $2, $3::uuid[], $4, $5::uuid, now() + make_interval(secs => $6::float8))
                ON CONFLICT (org_id, family_key) WHERE status = 'pending' AND system_to IS NULL
                DO UPDATE SET run_after = EXCLUDED.run_after, entity_ids = EXCLUDED.entity_ids,
                              reason = {T_REQ}.reason || ' + ' || EXCLUDED.reason, requested_at = now()
                RETURNING id::text, family_key, run_after""",
            org, key, fam, reason[:300], str(actor_id) if actor_id else None, float(delay))
    return {"id": row["id"], "family_key": row["family_key"], "entity_ids": fam, "run_after": row["run_after"]}


async def run_request(conn, org_id, request_id, *, as_of: date | None = None) -> dict | None:
    """Claim a pending request whose run_after has passed and run the family-scoped rollup. None if not due / taken."""
    from services.portfolio_rollup import rollup_entity_holdings

    org = str(org_id)
    async with _OrgWrite(conn, org) as c:
        req = await c.fetchrow(
            f"UPDATE {T_REQ} SET status = 'running', started_at = now() WHERE id = $1::uuid AND org_id = $2::uuid "
            f"AND status = 'pending' AND run_after <= now() AND system_to IS NULL RETURNING id, entity_ids",
            str(request_id), org)
    if req is None:
        return None
    day = as_of or date.today()
    try:
        res = await rollup_entity_holdings(conn, org_id=org, as_of_date=day, scope={str(e) for e in req["entity_ids"]})
        out, status = res.as_dict(), "done"
    except Exception as exc:  # noqa: BLE001 — recorded on the request, never raised into the write that asked
        log.exception("family rollup %s failed", request_id)
        out, status = {"error": f"{type(exc).__name__}: {exc}"}, "failed"
    async with _OrgWrite(conn, org) as c:
        await c.execute(
            f"UPDATE {T_REQ} SET status = $3, finished_at = now(), as_of_date = $4, result = $5::jsonb, "
            f"valid_to = now() WHERE id = $1::uuid AND org_id = $2::uuid",
            str(request_id), org, status, day, json.dumps(out, default=str))
    return {"request_id": str(request_id), "status": status, **out}


async def _later(pool, org_id, request_id, delay: float) -> None:
    await asyncio.sleep(delay + 0.05)
    try:
        async with pool.acquire() as conn:
            await run_request(conn, org_id, request_id)
    except Exception:  # noqa: BLE001
        log.exception("debounced family rollup %s", request_id)


async def after_write(pool, org_id, entity_ids, *, reason: str, actor_id=None) -> dict:
    """Called by every position / valuation / commitment / liability write path. Never raises into the write: a
    rollup that cannot be queued is logged and left to the nightly."""
    try:
        async with pool.acquire() as conn:
            req = await request_rollup(conn, org_id, entity_ids, reason=reason, actor_id=actor_id)
            if not req:
                return {}
            delay = debounce_seconds()
            if delay <= 0:
                return {**req, "ran": await run_request(conn, org_id, req["id"])}
        asyncio.get_running_loop().create_task(_later(pool, org_id, req["id"], delay))
        return {**req, "ran": None}
    except Exception:  # noqa: BLE001
        log.exception("could not queue the family rollup (%s)", reason)
        return {}


async def recalculate(pool, org_id, entity_id, *, actor_id=None) -> dict:
    """The "Recalculate" control: the entity's family, now (no debounce)."""
    async with pool.acquire() as conn:
        req = await request_rollup(conn, org_id, [entity_id], reason="recalculate", actor_id=actor_id, delay_s=0)
        if not req:
            return {}
        ran = await run_request(conn, org_id, req["id"])
    return {**req, "ran": ran}


def skipped_of(result) -> dict:
    """BUGFIX3 FIX 1 — what a rollup left OUT, as a report (never a silent $0): count, the ids, how many were
    unclassified (no taxonomy key) vs unvalued, and the rows (capped). ``result`` is a RollupResult.as_dict() /
    the stored ``rollup_requests.result``; anything else reports nothing skipped."""
    from services.portfolio_rollup import NO_TAXONOMY_REASON

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = None
    rows = list((result or {}).get("skipped") or []) if isinstance(result, dict) else []
    unclassified = sum(1 for r in rows if r.get("reason") == NO_TAXONOMY_REASON)
    return {"count": len(rows), "unclassified": unclassified, "unvalued": len(rows) - unclassified,
            "position_ids": [r.get("position_id") for r in rows], "positions": rows[:100]}


async def last_run_for(conn, org_id, entity_ids) -> dict | None:
    """The latest family rollup touching any of ``entity_ids`` — status / when / as-of, and what it skipped."""
    ids = [str(e) for e in entity_ids or []]
    if not ids:
        return None
    async with _OrgWrite(conn, str(org_id)) as c:
        r = await c.fetchrow(
            f"SELECT id::text, status, finished_at, as_of_date, reason, result FROM {T_REQ} WHERE org_id = $1::uuid "
            f"AND entity_ids && $2::uuid[] AND system_to IS NULL ORDER BY requested_at DESC LIMIT 1",
            str(org_id), ids)
    if r is None:
        return None
    return {"id": r["id"], "status": r["status"], "finished_at": r["finished_at"].isoformat() if r["finished_at"]
            else None, "as_of_date": r["as_of_date"].isoformat() if r["as_of_date"] else None, "reason": r["reason"],
            "skipped": skipped_of(r["result"])}


async def last_run(conn, org_id, entity_id) -> dict | None:
    return await last_run_for(conn, org_id, [entity_id])


async def drain_due(conn) -> list[dict]:
    """Every org's overdue pending requests (a crashed process, a restart) — platform scope to find them, each run
    under its own org context. For the scheduler tick / nightly on a raw connection."""
    from services.database import platform_scope

    async with platform_scope(conn):
        due = await conn.fetch(f"SELECT id::text, org_id::text FROM {T_REQ} WHERE status = 'pending' "
                               f"AND run_after <= now() - interval '30 seconds' AND system_to IS NULL")
    out = []
    for d in due:
        r = await run_request(conn, d["org_id"], d["id"])
        if r:
            out.append(r)
    return out


async def nightly_rollups(conn, *, as_of: date | None = None) -> list[dict]:
    """The nightly: every org that holds a current position, org-wide, at today's date."""
    from services.database import platform_scope
    from services.portfolio_rollup import rollup_entity_holdings

    async with platform_scope(conn):
        orgs = [r["org_id"] for r in await conn.fetch(
            "SELECT DISTINCT org_id::text AS org_id FROM portfolio.positions WHERE valid_to IS NULL "
            "AND system_to IS NULL")]
    out = await drain_due(conn)
    # PORTENTRY1 (GAP-PORT-01) — org manual assets whose identifier now resolves in the master are relinked
    # (system axis, id kept) and REPORTED in portfolio.asset_relinks + the audit log — never silently.
    from services.portfolio_manual import relink_manual_assets

    async with platform_scope(conn):
        manual_orgs = [r["org_id"] for r in await conn.fetch(
            "SELECT DISTINCT org_id::text AS org_id FROM portfolio.assets WHERE global_security_id IS NULL "
            "AND valid_to IS NULL AND system_to IS NULL")]
    for org in manual_orgs:
        try:
            done = await relink_manual_assets(conn, org, trigger="nightly")
            if done:
                out.append({"org_id": org, "relinked": done})
        except Exception as exc:  # noqa: BLE001
            out.append({"org_id": org, "relink_error": f"{type(exc).__name__}: {exc}"})
    for org in orgs:
        try:
            res = await rollup_entity_holdings(conn, org_id=org, as_of_date=as_of or date.today())
            out.append({"org_id": org, **res.as_dict()})
        except Exception as exc:  # noqa: BLE001
            out.append({"org_id": org, "error": f"{type(exc).__name__}: {exc}"})
    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["after_write", "family_of", "recalculate", "request_rollup", "run_request", "drain_due",
           "nightly_rollups", "last_run", "last_run_for", "skipped_of", "debounce_seconds", "timedelta"]
