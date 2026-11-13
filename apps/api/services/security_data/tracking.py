"""TRACK1 — "Track this security": a Super Admin puts any security-master security into the price coverage set on
purpose, one at a time, in bulk (by ids, or by a filter evaluated HERE on the server), or by a named list.

    track(conn, gsids, ...)            tracked rows (one live row per security, platform scope); already-tracked
                                       securities are left alone; unknown / merged ids reported, never invented
    untrack(conn, gsids, ...)          valid-axis restatement: the live 'tracked' row closed, an 'untracked' row
                                       inserted with who / why — history kept
    select_by_filter(conn, filters)    the ids a filter selects — THE predicate builder the master list and its counts
                                       use (security_master_summary._where), so the confirmed count is what is acted on
    estimate(conn, gsids)              calls / days at the daily budget, nights through the nightly alone, and the daily
                                       upkeep against the budget and the nightly's capacity (with warnings)
    upsert_list / list_rows / list_member_ids     the named lists (the operator's loader fills them)
    progress(conn)                     tracked securities and how many have a loaded series ("1,240 of 2,700 loaded")

Every write: caller holds platform scope (services.database.platform_scope / app.is_super_admin) — the route's gate is
require_super_admin, checked first; ONE audit_log row per action on the platform org (the BUGFIX4 schedule switch's
pattern). Nothing here writes org data: tracked securities are platform-wide.

prices.coverage_set reads live 'tracked' rows as the reason 'tracked' (prices.OPTIONAL_SOURCES); the backfill and the
nightly prices job pick them up from there — no new job type.
"""

from __future__ import annotations

import json
import math
from datetime import date

from services import security_master_summary as SUM

LIVE = "valid_to IS NULL AND system_to IS NULL"
T_TRACKED = "portfolio.tracked_securities"
T_LISTS = "portfolio.security_lists"
T_MEMBERS = "portfolio.security_list_members"

#: the most securities ONE bulk action (by ids or by filter) may touch. Justification (docs/TRACK1_NOTES.md):
#: the largest named list is the Russell 2000 (~2,000; S&P 500 ~503), so every intended list fits; 3,000 is 1.5 days of
#: the free source's 2,000-call budget for the history and already past what it can keep current daily — a filter
#: selecting more (all equities: 8,666; all funds: 6,133) is almost certainly a mis-filter, and is refused with the count.
TRACK_CAP = 3000
#: untrack is cheap (no fetch) but is held to the same cap so one click can never empty the whole tracked universe
UNTRACK_CAP = TRACK_CAP
NOTE_MAX = 2000
AUDIT_IDS_MAX = TRACK_CAP


class TrackingError(ValueError):
    """A refusal the route turns into 409 / 422 with this message (never echoes a caller's value)."""


class TrackingRefused(TrackingError):
    """Above the cap / an empty filter — 409."""


def _audit_org():
    from services import security_master_jobs as J

    return J.platform_org()


async def _audit(conn, *, actor_id, action: str, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
        "($1::uuid, $2::uuid, $3, 'tracked_securities', NULL, $4::jsonb)",
        str(_audit_org()), str(actor_id) if actor_id else None, action, json.dumps(payload, default=str))


def _clean_note(note: str | None) -> str | None:
    if note is None:
        return None
    n = str(note).strip()
    if len(n) > NOTE_MAX:
        raise TrackingError(f"the note is longer than {NOTE_MAX} characters")
    return n or None


async def _known(conn, gsids: list[str]) -> set[str]:
    rows = await conn.fetch(
        "SELECT id::text FROM portfolio.securities_global WHERE id = ANY($1::uuid[]) AND valid_to IS NULL AND "
        "system_to IS NULL AND merged_into_id IS NULL", gsids)
    return {r[0] for r in rows}


def _ids(gsids) -> list[str]:
    import uuid as _u

    out, seen = [], set()
    for g in gsids or []:
        try:
            s = str(_u.UUID(str(g)))
        except (ValueError, TypeError, AttributeError):
            raise TrackingError("every id must be a security id (uuid)") from None
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


async def live_rows(conn, gsids: list[str]) -> dict[str, dict]:
    rows = await conn.fetch(f"SELECT id, global_security_id::text AS g, status, list_key, note FROM {T_TRACKED} "
                            f"WHERE global_security_id = ANY($1::uuid[]) AND {LIVE}", gsids)
    return {r["g"]: dict(r) for r in rows}


async def track(conn, gsids, *, actor_id, note: str | None = None, list_key: str | None = None,
                how: str = "ids", filters: dict | None = None, cap: int | None = None) -> dict:
    """Track ``gsids``. Returns {requested, tracked, already_tracked, unknown: [ids]}. Caller holds platform scope."""
    cap = TRACK_CAP if cap is None else cap
    ids = _ids(gsids)
    note = _clean_note(note)
    if not ids:
        raise TrackingError("nothing selected")
    if len(ids) > cap:
        raise TrackingRefused(f"{len(ids):,} securities selected — one action may track at most {cap:,}")
    if note is None and not list_key:
        raise TrackingError("say why: a note, or the list the securities come from")
    async with conn.transaction():
        known = await _known(conn, ids)
        unknown = [g for g in ids if g not in known]
        live = await live_rows(conn, [g for g in ids if g in known])
        already = [g for g in ids if g in known and (live.get(g) or {}).get("status") == "tracked"]
        todo = [g for g in ids if g in known and g not in set(already)]
        reopen = [g for g in todo if g in live]          # live 'untracked' rows: closed, then the tracked row inserted
        if reopen:
            await conn.execute(f"UPDATE {T_TRACKED} SET valid_to = now() WHERE global_security_id = ANY($1::uuid[]) "
                               f"AND status = 'untracked' AND {LIVE}", reopen)
        if todo:
            await conn.execute(
                f"INSERT INTO {T_TRACKED} (global_security_id, status, list_key, note, actor_id) "
                "SELECT g, 'tracked', $2, $3, $4::uuid FROM unnest($1::uuid[]) AS g", todo, list_key, note,
                str(actor_id) if actor_id else None)
        out = {"requested": len(ids), "tracked": len(todo), "already_tracked": len(already), "unknown": unknown,
               "list_key": list_key}
        await _audit(conn, actor_id=actor_id, action="security_data.tracked", payload={
            **out, "how": how, "filters": filters, "note": note, "ids": todo[:AUDIT_IDS_MAX]})
    return out


async def untrack(conn, gsids, *, actor_id, note: str | None, how: str = "ids", filters: dict | None = None,
                  cap: int | None = None) -> dict:
    """Untrack ``gsids`` (a note is required). Returns {requested, untracked, not_tracked}. Prices already loaded
    stay; the security simply leaves the coverage set unless another reason keeps it there."""
    cap = UNTRACK_CAP if cap is None else cap
    ids = _ids(gsids)
    note = _clean_note(note)
    if not ids:
        raise TrackingError("nothing selected")
    if len(ids) > cap:
        raise TrackingRefused(f"{len(ids):,} securities selected — one action may untrack at most {cap:,}")
    if note is None:
        raise TrackingError("say why the securities are untracked")
    async with conn.transaction():
        live = await live_rows(conn, ids)
        todo = [g for g in ids if (live.get(g) or {}).get("status") == "tracked"]
        if todo:
            hit = await conn.fetchval(
                f"WITH c AS (UPDATE {T_TRACKED} SET valid_to = now() WHERE global_security_id = ANY($1::uuid[]) AND "
                f"status = 'tracked' AND {LIVE} RETURNING 1) SELECT count(*) FROM c", todo)
            if int(hit) != len(todo):   # zero rows: an RLS-blocked write and a vanished row look the same — say neither
                raise TrackingError(f"{len(todo) - int(hit)} tracked rows were not closed (no row updated)")
            await conn.execute(
                f"INSERT INTO {T_TRACKED} (global_security_id, status, list_key, note, actor_id) "
                "SELECT g, 'untracked', x.list_key, $2, $3::uuid FROM unnest($1::uuid[]) AS g LEFT JOIN LATERAL "
                f"(SELECT list_key FROM {T_TRACKED} t WHERE t.global_security_id = g AND t.status = 'tracked' AND "
                "t.system_to IS NULL ORDER BY t.valid_to DESC NULLS FIRST LIMIT 1) x ON true", todo, note,
                str(actor_id) if actor_id else None)
        out = {"requested": len(ids), "untracked": len(todo), "not_tracked": len(ids) - len(todo)}
        await _audit(conn, actor_id=actor_id, action="security_data.untracked", payload={
            **out, "how": how, "filters": filters, "note": note, "ids": todo[:AUDIT_IDS_MAX]})
    return out


def clean_filters(filters: dict | None) -> dict:
    """Only the master list's filter names, with a non-empty value. Unknown names → TrackingError."""
    out = {}
    for k, v in (filters or {}).items():
        if k not in SUM.FILTERS:
            raise TrackingError(f"unknown filter (one of {', '.join(SUM.FILTERS)})")
        if v is not None and str(v).strip() != "":
            out[k] = str(v).strip()
    return out


async def select_by_filter(conn, filters: dict | None, *, cap: int | None = None, scope: list | None = None) -> dict:
    """{total, ids (only when total <= cap), filters} — the SAME predicates as the master's list
    (security_master_summary._where). An empty filter is refused (it would select the whole master)."""
    cap = TRACK_CAP if cap is None else cap
    f = clean_filters(filters)
    if not f:
        raise TrackingRefused("choose at least one filter — an empty filter selects the whole master")
    args: list = []
    w = [SUM.LIVE_S, SUM._scope(scope, args)] + SUM._where(f, args)
    where = " AND ".join(w)
    total = int(await conn.fetchval(f"SELECT count(*) FROM portfolio.securities_global s WHERE {where}", *args))
    ids = None
    if total <= cap:
        ids = [r[0] for r in await conn.fetch(
            f"SELECT s.id::text FROM portfolio.securities_global s WHERE {where} ORDER BY s.id", *args)]
    return {"total": total, "ids": ids, "filters": f, "cap": cap, "over_cap": total > cap}


async def track_by_filter(conn, filters: dict | None, *, actor_id, note: str | None, expected_total: int | None = None,
                          scope: list | None = None) -> dict:
    """Track every security the filter selects (re-evaluated HERE, inside the write's transaction). ``expected_total``
    — the count the Super Admin confirmed — must equal what the filter selects now, else nothing is written (409)."""
    sel = await select_by_filter(conn, filters, scope=scope)
    _confirm(sel, expected_total, "track")
    return await track(conn, sel["ids"], actor_id=actor_id, note=note, how="filter", filters=sel["filters"])


async def untrack_by_filter(conn, filters: dict | None, *, actor_id, note: str | None,
                            expected_total: int | None = None, scope: list | None = None) -> dict:
    sel = await select_by_filter(conn, filters, scope=scope, cap=UNTRACK_CAP)  # read at call time
    _confirm(sel, expected_total, "untrack")
    return await untrack(conn, sel["ids"], actor_id=actor_id, note=note, how="filter", filters=sel["filters"])


def _confirm(sel: dict, expected_total, verb: str) -> None:
    if sel["over_cap"]:
        raise TrackingRefused(f"the filter selects {sel['total']:,} securities — one action may {verb} at most "
                              f"{sel['cap']:,}; narrow the filter")
    if expected_total is not None and int(expected_total) != sel["total"]:
        raise TrackingRefused(f"the filter now selects {sel['total']:,} securities, not the {int(expected_total):,} "
                              "confirmed — nothing was changed; review the count and confirm again")


# ── the estimate ────────────────────────────────────────────────────────────
def nightly_capacity(spec, deadline_s: float) -> int:
    """Securities ONE slice of the nightly prices refresh can fetch inside its deadline (the backfill's own per-call
    timing). PRICES2: the day's refresh runs a slice every scheduler tick, so this is per slice, not per night."""
    per_call_s = spec.min_interval_s + 0.4
    return max(0, int(deadline_s // per_call_s)) * max(1, spec.max_symbols_per_call)


def estimate_numbers(*, selected: int, to_load: int, coverage_after: int, per_call: int, budget: int | None,
                     nightly_cap: int, nightly_max_new: int, new_per_day: int | None = None) -> dict:
    """Pure arithmetic (the verify recomputes it by hand)."""
    calls = math.ceil(to_load / max(1, per_call))
    days = (math.ceil(calls / budget) if calls else 0) if budget else None
    # PRICES2: new members' history is capped per DAY (nightly_max_new across the day's slices) — the caller passes
    # new_per_day; without it, the old per-run arithmetic (one slice a night) is kept
    new_per_night = new_per_day if new_per_day is not None else (min(nightly_max_new, nightly_cap) if nightly_cap else 0)
    nights = (math.ceil(to_load / new_per_night) if to_load else 0) if new_per_night else None
    upkeep = math.ceil(coverage_after / max(1, per_call))
    warnings = []
    if budget and upkeep > budget:
        warnings.append(f"keeping {coverage_after:,} securities current needs {upkeep:,} calls a day — more than the "
                        f"daily budget of {budget:,}; some will go stale")
    # PRICES2: the nightly refresh continues tick after tick through the day, so its limit is the DAILY BUDGET (budget
    # × securities per call), not one run's deadline (nightly_cap is now the capacity of ONE ~180 s slice)
    per_day = budget * max(1, per_call) if budget else None
    every = math.ceil(coverage_after / per_day) if per_day and coverage_after else (1 if coverage_after else 0)
    if per_day and coverage_after > per_day:
        warnings.append(f"the nightly price refresh keeps at most {per_day:,} securities current a day (the daily "
                        f"budget of {budget:,} calls); {coverage_after:,} covered means each is refreshed every "
                        f"{every} days, oldest first — held, modelled and benchmark securities every day")
    return {"selected": selected, "to_load": to_load, "estimated_calls": calls, "daily_budget": budget,
            "days_at_budget": days, "nights_nightly_only": nights, "coverage_after": coverage_after,
            "upkeep_calls_per_day": upkeep, "nightly_capacity": nightly_cap, "refreshes_per_day": per_day,
            "refresh_every_days": every, "warnings": warnings}


async def estimate(conn, gsids, *, removing: bool = False) -> dict:
    """What tracking (or untracking) ``gsids`` costs at the first active end-of-day source: the history to load
    (securities with no platform close yet), calls and days at its daily budget, nights through the nightly alone,
    and the daily upkeep of the coverage set afterwards."""
    from services.security_data import layer as L
    from services.security_data import prices as P
    from services.security_data import registry as R

    ids = _ids(gsids)
    order = await P.eod_rank(conn)
    if not order:
        return {"provider": None, "selected": len(ids), "warnings": ["no active end-of-day source in the platform rank"]}
    key = order[0]
    spec = L.code_registry()[key].spec
    budget = (await R.providers(conn)).get(key, {}).get("daily_call_budget")
    cov = {it.gsid for it in await P.coverage_set(conn)}
    if removing:
        live = await live_rows(conn, ids)
        leaving = [g for g in ids if (live.get(g) or {}).get("status") == "tracked"]
        others = await _other_reasons(conn, leaving)
        coverage_after = len(cov) - len([g for g in leaving if g in cov and not others.get(g)])
        to_load = 0
    else:
        coverage_after = len(cov | set(ids))
        priced = {r[0] for r in await conn.fetch(
            "SELECT DISTINCT global_security_id::text FROM portfolio.securities_global_prices WHERE "
            f"global_security_id = ANY($1::uuid[]) AND org_id IS NULL AND price_type = 'close' AND {LIVE}", ids)}
        to_load = len([g for g in ids if g not in priced])
    out = estimate_numbers(selected=len(ids), to_load=to_load, coverage_after=coverage_after,
                           per_call=spec.max_symbols_per_call, budget=int(budget) if budget is not None else None,
                           nightly_cap=nightly_capacity(spec, P.NIGHTLY_DEADLINE_S), nightly_max_new=P.NIGHTLY_MAX_NEW,
                           new_per_day=min(P.NIGHTLY_MAX_NEW, int(budget) * max(1, spec.max_symbols_per_call))
                           if budget is not None else P.NIGHTLY_MAX_NEW)
    return {"provider": key, "coverage_now": len(cov), **out}


async def _other_reasons(conn, gsids: list[str]) -> dict:
    """{gsid: [reasons other than 'tracked']} for the coverage set's members among ``gsids``."""
    from services.security_data import prices as P

    if not gsids:
        return {}
    return {it.gsid: [s for s in it.sources if s not in ("tracked", "requested")]
            for it in await P.coverage_set(conn, only_gsids=gsids)}


# ── progress ────────────────────────────────────────────────────────────────
async def progress(conn) -> dict:
    """{tracked, loaded, waiting} — loaded = a tracked security holding at least one live platform close."""
    row = await conn.fetchrow(
        f"SELECT count(*) AS tracked, count(*) FILTER (WHERE EXISTS (SELECT 1 FROM portfolio.securities_global_prices "
        "p WHERE p.global_security_id = t.global_security_id AND p.org_id IS NULL AND p.price_type = 'close' AND "
        f"p.valid_to IS NULL AND p.system_to IS NULL)) AS loaded FROM {T_TRACKED} t WHERE t.status = 'tracked' AND "
        f"t.{LIVE.replace(' AND ', ' AND t.')}")
    t, l_ = int(row["tracked"]), int(row["loaded"])
    return {"tracked": t, "loaded": l_, "waiting": t - l_, "label": f"{l_:,} of {t:,} loaded"}


# ── named lists ─────────────────────────────────────────────────────────────
async def list_rows(conn) -> list[dict]:
    """Every live list with its member / tracked / loaded counts (one query)."""
    rows = await conn.fetch(
        f"SELECT l.list_key, l.name, l.description, l.source_kind, l.source_detail, l.as_of, l.member_count, l.misses, "
        "l.valid_from AS refreshed_at, (SELECT count(*) FROM portfolio.security_list_members m JOIN "
        "portfolio.tracked_securities t ON t.global_security_id = m.global_security_id AND t.status = 'tracked' AND "
        "t.valid_to IS NULL AND t.system_to IS NULL WHERE m.list_key = l.list_key AND m.valid_to IS NULL AND "
        f"m.system_to IS NULL) AS tracked FROM {T_LISTS} l WHERE l.{LIVE.replace(' AND ', ' AND l.')} ORDER BY l.name")
    out = []
    for r in rows:
        d = dict(r)
        d["misses"] = json.loads(d["misses"]) if isinstance(d["misses"], str) else (d["misses"] or [])
        d["miss_count"] = len(d["misses"])
        out.append(d)
    return out


async def list_member_ids(conn, list_key: str) -> list[str]:
    return [r[0] for r in await conn.fetch(
        f"SELECT global_security_id::text FROM {T_MEMBERS} WHERE list_key = $1 AND {LIVE} ORDER BY global_security_id",
        list_key)]


async def upsert_list(conn, *, list_key: str, name: str, description: str | None, source_kind: str,
                      source_detail: str | None, as_of: date | None, members: list[dict], misses: list,
                      actor_id=None, run_id=None) -> dict:
    """Write a list and its members. ``members`` = [{gsid, ticker, description, matched_by, weight}]. The list row is
    restated (valid axis) only when something about it changed; a member is closed when it leaves, inserted when it
    joins, untouched when it stays. Caller holds platform scope. Audited."""
    by = {}
    for m in members:
        by.setdefault(str(m["gsid"]), m)
    async with conn.transaction():
        cur = await conn.fetchrow(f"SELECT * FROM {T_LISTS} WHERE list_key = $1 AND {LIVE} FOR UPDATE", list_key)
        have = set(await list_member_ids(conn, list_key))
        joins = [g for g in by if g not in have]
        leaves = [g for g in have if g not in by]
        misses_j = json.dumps(misses, default=str)
        same = cur is not None and (cur["name"], cur["description"], cur["source_kind"], cur["source_detail"],
                                    cur["as_of"], cur["member_count"]) == (name, description, source_kind,
                                                                           source_detail, as_of, len(by)) and \
            json.loads(cur["misses"] if isinstance(cur["misses"], str) else json.dumps(cur["misses"])) == \
            json.loads(misses_j) and not joins and not leaves
        if not same:
            if cur is not None:
                await conn.execute(f"UPDATE {T_LISTS} SET valid_to = now() WHERE id = $1", cur["id"])
            await conn.execute(
                f"INSERT INTO {T_LISTS} (list_key, name, description, source_kind, source_detail, as_of, member_count, "
                "misses, run_id, actor_id) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::uuid, $10::uuid)",
                list_key, name, description, source_kind, source_detail, as_of, len(by), misses_j,
                str(run_id) if run_id else None, str(actor_id) if actor_id else None)
        if leaves:
            await conn.execute(f"UPDATE {T_MEMBERS} SET valid_to = now() WHERE list_key = $1 AND global_security_id = "
                               f"ANY($2::uuid[]) AND {LIVE}", list_key, leaves)
        for g in joins:
            m = by[g]
            await conn.execute(
                f"INSERT INTO {T_MEMBERS} (list_key, global_security_id, ticker, description, matched_by, weight, "
                "run_id) VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::uuid)", list_key, g, m.get("ticker"),
                m.get("description"), m.get("matched_by") or "ticker", m.get("weight"),
                str(run_id) if run_id else None)
        out = {"list_key": list_key, "members": len(by), "joined": len(joins), "left": len(leaves),
               "misses": len(misses), "list_restated": not same}
        await _audit(conn, actor_id=actor_id, action="security_data.list_loaded", payload={
            **out, "as_of": as_of, "source": source_detail})
    return out


async def track_list(conn, list_key: str, *, actor_id, note: str | None = None,
                     expected_total: int | None = None) -> dict:
    """Track every live member of a named list (the tracked rows name the list)."""
    ids = await list_member_ids(conn, list_key)
    if not ids:
        raise TrackingError("that list has no members (the operator loads them: scripts/security_lists_load.py)")
    if expected_total is not None and int(expected_total) != len(ids):
        raise TrackingRefused(f"the list now has {len(ids):,} members, not the {int(expected_total):,} confirmed — "
                              "nothing was changed")
    return await track(conn, ids, actor_id=actor_id, note=note, list_key=list_key, how="list")
