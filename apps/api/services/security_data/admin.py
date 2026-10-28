"""Admin reads and writes for the security-data screens (marketdata1, Task 7).

  Super Admin  providers (status, licence facts, budget, call log) and the
               platform rank per data type.
  Org admin    rank overrides within the allowed providers (a subset of the
               platform rank for that type, in the org's own order).
  Quality      the org's events + the platform's, newest first.

Every write is Rule 3 valid-axis: the live row is closed and the new one
inserted. Platform writes run with app.is_super_admin LOCAL in their own
transaction (rep1's platform_tx); org writes on the org's own RLS context."""

from __future__ import annotations

import json
from datetime import date, timedelta

from services.replica_holdings import platform_tx
from services.security_data import registry as R
from services.security_data import writes as W
from services.security_data.types import (DATA_TYPE_LABELS, DATA_TYPES, KIND_LABELS, MEMBER_DISPLAY,
                                          MEMBER_DISPLAY_LABELS, PROVIDER_STATUSES, PURPOSE_LABELS, QUALITY_LABELS,
                                          RETENTION, RETENTION_LABELS, STATUS_LABELS, SecurityDataError,
                                          SecurityDataNotFound)

PROVIDER_FIELDS = ("status", "lic_internal_use", "lic_member_display", "lic_redistribution", "retention_after_cancel",
                   "attribution", "terms_note", "confirmed_on", "daily_call_budget")
LIVE = "valid_to IS NULL AND system_to IS NULL"


def vocabularies() -> dict:
    return {"data_types": [{"key": k, "label": DATA_TYPE_LABELS[k]} for k in DATA_TYPES],
            "provider_kinds": [{"key": k, "label": v} for k, v in KIND_LABELS.items()],
            "provider_statuses": [{"key": k, "label": STATUS_LABELS[k]} for k in PROVIDER_STATUSES],
            "member_display": [{"key": k, "label": MEMBER_DISPLAY_LABELS[k]} for k in MEMBER_DISPLAY],
            "retention": [{"key": k, "label": RETENTION_LABELS[k]} for k in RETENTION],
            "purposes": [{"key": k, "label": v} for k, v in PURPOSE_LABELS.items()],
            "quality_kinds": [{"key": k, "label": v} for k, v in QUALITY_LABELS.items()]}


def _out(p: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()
            if k not in ("valid_from", "valid_to", "system_from", "system_to", "owner_scope", "org_id")}


async def list_providers(conn, *, today: date, days: int = 7) -> list[dict]:
    from services.security_data.layer import code_registry

    rows = await R.providers(conn)
    code = code_registry()
    used = {(r["provider_key"], r["call_date"]): int(r["n"]) for r in await conn.fetch(
        "SELECT provider_key, call_date, sum(calls) AS n FROM portfolio.provider_call_log WHERE call_date >= $1 "
        "GROUP BY 1, 2", today - timedelta(days=days - 1))}
    out = []
    for k, p in rows.items():
        mod = code.get(k)
        today_used = used.get((k, today), 0)
        budget = p["daily_call_budget"]
        out.append({**_out(p), "module_installed": mod is not None,
                    "connected": bool(mod is not None and getattr(mod, "connected", True)),
                    "module_capabilities": sorted(getattr(mod, "capabilities", ())),
                    "calls_today": today_used,
                    "budget_left_today": None if budget is None else max(0, int(budget) - today_used),
                    "calls_by_day": [{"date": (today - timedelta(days=i)).isoformat(),
                                      "calls": used.get((k, today - timedelta(days=i)), 0)} for i in range(days)]})
    return out


async def call_log(conn, *, provider_key: str | None = None, limit: int = 200) -> list[dict]:
    rows = await conn.fetch("SELECT provider_key, call_date, calls, data_type, ok, error, run_id, created_at FROM "
                            "portfolio.provider_call_log WHERE ($1::text IS NULL OR provider_key = $1) "
                            "ORDER BY created_at DESC LIMIT $2", provider_key, limit)
    return [dict(r) for r in rows]


async def update_provider(pool, *, key: str, changes: dict, actor_id) -> dict:
    from services.security_data.layer import code_registry

    bad = set(changes) - set(PROVIDER_FIELDS)
    if bad:
        raise SecurityDataError(f"not editable: {', '.join(sorted(bad))}")
    async with platform_tx(pool) as conn:
        cur = await conn.fetchrow(f"SELECT * FROM {R.T_PROVIDERS} WHERE key = $1 AND {LIVE} FOR UPDATE", key)
        if cur is None:
            raise SecurityDataNotFound(f"provider {key!r} is not registered")
        row = {**dict(cur), **changes}
        if row["status"] == "active":
            mod = code_registry().get(key)
            if mod is None or not getattr(mod, "connected", True):
                raise SecurityDataError(f"{key} cannot be set active: its provider module is not connected (a vendor "
                                        "needs its own module first — see 'Add a vendor')")
        if isinstance(row.get("confirmed_on"), str):
            row["confirmed_on"] = date.fromisoformat(row["confirmed_on"]) if row["confirmed_on"] else None
        await conn.execute(f"UPDATE {R.T_PROVIDERS} SET valid_to = now() WHERE id = $1", cur["id"])
        cols = [c for c in cur.keys() if c not in ("id", "created_at", "valid_from", "valid_to", "system_from",
                                                    "system_to")]
        await conn.execute(f"INSERT INTO {R.T_PROVIDERS} ({', '.join(cols)}) VALUES "
                           f"({', '.join(f'${i + 1}' for i in range(len(cols)))})", *[row[c] for c in cols])
        new = await conn.fetchrow(f"SELECT * FROM {R.T_PROVIDERS} WHERE key = $1 AND {LIVE}", key)
    return _out(dict(new))


async def rank_view(conn, org_id) -> list[dict]:
    rows = await R.providers(conn)
    out = []
    for t in DATA_TYPES:
        plat = await R.platform_rank(conn, t)
        own = await R.org_rank(conn, org_id, t) if org_id else []
        out.append({"data_type": t, "label": DATA_TYPE_LABELS[t], "platform": plat, "org_override": own,
                    "effective": own or plat, "origin": "org_override" if own else "platform",
                    "allowed": plat,
                    "providers": {k: {"name": rows[k]["name"], "status": rows[k]["status"], "kind": rows[k]["kind"]}
                                  for k in plat if k in rows}})
    return out


async def _set_rank(conn, *, scope: str, org_id, data_type: str, keys: list[str], actor_id) -> None:
    R.check_type(data_type)
    if len(set(keys)) != len(keys):
        raise SecurityDataError("a provider may appear only once in a rank")
    await conn.execute(f"UPDATE {R.T_RANK} SET valid_to = now() WHERE owner_scope = $1 AND org_id IS NOT DISTINCT "
                       f"FROM $2::uuid AND data_type = $3 AND {LIVE}", scope, org_id, data_type)
    for i, k in enumerate(keys, start=1):
        await conn.execute(f"INSERT INTO {R.T_RANK} (data_type, provider_key, rank, owner_scope, org_id, set_by) VALUES "
                           "($1, $2, $3, $4, $5::uuid, $6::uuid)", data_type, k, i, scope, org_id,
                           str(actor_id) if actor_id else None)


async def set_platform_rank(pool, *, data_type: str, keys: list[str], actor_id) -> list[str]:
    async with platform_tx(pool) as conn:
        rows = await R.providers(conn)
        for k in keys:
            if k not in rows:
                raise SecurityDataError(f"provider {k!r} is not registered")
            if data_type not in (rows[k]["capabilities"] or []):
                raise SecurityDataError(f"{k} does not supply {data_type}")
        await _set_rank(conn, scope="platform", org_id=None, data_type=data_type, keys=keys, actor_id=actor_id)
        return await R.platform_rank(conn, data_type)


async def set_org_rank(pool, *, org_id, data_type: str, keys: list[str], actor_id) -> list[str]:
    """``keys`` empty = clear the override (the platform default applies again)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            allowed = await R.platform_rank(conn, data_type)
            outside = [k for k in keys if k not in allowed]
            if outside:
                raise SecurityDataError(f"not an allowed provider for {data_type}: {', '.join(outside)} — an org may "
                                        f"rank only {', '.join(allowed) or '(none)'}")
            await _set_rank(conn, scope="org", org_id=str(org_id), data_type=data_type, keys=keys, actor_id=actor_id)
            return await R.org_rank(conn, org_id, data_type)


async def quality_events(conn, org_id, *, kind: str | None = None, limit: int = 300) -> list[dict]:
    rows = await conn.fetch(
        "SELECT e.id, e.org_id, e.owner_scope, e.data_type, e.subject_key, e.global_security_id, e.event_date, e.kind, "
        "e.provider_key, e.detail, e.created_at, g.name AS security_name FROM portfolio.data_quality_events e "
        "LEFT JOIN portfolio.securities_global g ON g.id = e.global_security_id WHERE (e.org_id IS NULL OR "
        "e.org_id = $1::uuid) AND ($2::text IS NULL OR e.kind = $2) AND e.valid_to IS NULL AND e.system_to IS NULL "
        "ORDER BY e.event_date DESC, e.created_at DESC LIMIT $3", str(org_id), kind, limit)
    out = []
    for r in rows:
        x = dict(r)
        x["detail"] = json.loads(x["detail"]) if isinstance(x["detail"], str) else x["detail"]
        x["kind_label"] = QUALITY_LABELS.get(x["kind"], x["kind"])
        out.append(x)
    return out


async def latest_run(conn) -> dict | None:
    r = await conn.fetchrow("SELECT id, run_trigger, status, started_at, finished_at, summary FROM "
                            "portfolio.security_data_runs ORDER BY finished_at DESC LIMIT 1")
    if r is None:
        return None
    x = dict(r)
    x["summary"] = json.loads(x["summary"]) if isinstance(x["summary"], str) else x["summary"]
    return x


__all__ = ["vocabularies", "list_providers", "call_log", "update_provider", "rank_view", "set_platform_rank",
           "set_org_rank", "quality_events", "latest_run", "W"]
