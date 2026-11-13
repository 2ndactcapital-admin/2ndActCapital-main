"""SECMASTER2 Task 5 — what the security master holds, in one screen (Super Admin).

``summary(conn)`` — ONE QUERY PER SECTION, never one per row (Task 1d listed what the status panel lacked):

    by_type          live securities per security_type, funds split by fund_structure (mutual_fund / money_market /
                     etf / not stated)
    identifiers      per identifier type: securities holding one, and the identifier count
    providers        per provider: securities it states at least one attribute for
    attributes       per reference field: securities with a value from ANY provider (coverage of the total)
    prices           per price_coverage, and how many hold at least one platform price row
    review           the open review queue per kind
    jobs             the last job-history row per job (portfolio.security_data_runs)

Every count carries the ``filter`` that lists exactly those securities (``securities(conn, **filter)``, GET
/admin/security-data/master/securities) — or, for the review queue, the review filter — so the screen links each
number to its list and the two can never disagree: both are built from the SAME predicates (``_where``).

``scope`` (a list of security ids) restricts every section and the list to those securities — the verify's fixture
master; the routes pass None. The caller holds platform scope (app.is_super_admin) — the routes use platform_scope.
"""

from __future__ import annotations

import json
from typing import Any

from services import security_master_load as M

LIVE_S = "s.valid_to IS NULL AND s.system_to IS NULL AND s.merged_into_id IS NULL"
LIVE = "valid_to IS NULL AND system_to IS NULL"
#: the list endpoint's filters (query parameter → its meaning); anything else is refused (422)
FILTERS = ("security_type", "fund_structure", "id_type", "missing_id_type", "provider", "field", "missing_field",
           "price_coverage", "has_price",
           # TRACK1 — the Tracked view's filters, in THIS builder so a bulk action's count and its list agree
           "q", "exchange", "category", "tracked", "in_list")
NOT_STATED = "not_stated"
LIST_LIMIT_MAX = 500


class SummaryFilterError(ValueError):
    pass


def _where(filters: dict, args: list) -> list[str]:
    """SQL predicates over ``s`` (portfolio.securities_global) for the list filters — THE definition the summary's
    counts and the list share."""
    w: list[str] = []

    def arg(v) -> str:
        args.append(v)
        return f"${len(args)}"

    for k, v in filters.items():
        if v is None or v == "":
            continue
        if k == "security_type":
            w.append(f"s.security_type = {arg(v)}")
        elif k == "fund_structure":
            if v == NOT_STATED:
                w.append("NOT EXISTS (SELECT 1 FROM portfolio.security_reference_attributes a WHERE "
                         f"a.global_security_id = s.id AND a.field = 'fund_structure' AND a.{LIVE.replace(' AND ', ' AND a.')})")
            else:
                w.append("EXISTS (SELECT 1 FROM portfolio.security_reference_attributes a WHERE a.global_security_id = "
                         f"s.id AND a.field = 'fund_structure' AND a.value = {arg(v)} AND "
                         f"a.{LIVE.replace(' AND ', ' AND a.')})")
        elif k in ("id_type", "missing_id_type"):
            neg = "NOT " if k == "missing_id_type" else ""
            w.append(f"{neg}EXISTS (SELECT 1 FROM portfolio.securities_global_identifiers i WHERE i.global_security_id "
                     f"= s.id AND i.id_type = {arg(v)} AND i.{LIVE.replace(' AND ', ' AND i.')})")
        elif k == "provider":
            w.append("EXISTS (SELECT 1 FROM portfolio.security_reference_attributes a WHERE a.global_security_id = s.id "
                     f"AND a.provider_key = {arg(v)} AND a.{LIVE.replace(' AND ', ' AND a.')})")
        elif k in ("field", "missing_field"):
            neg = "NOT " if k == "missing_field" else ""
            w.append(f"{neg}EXISTS (SELECT 1 FROM portfolio.security_reference_attributes a WHERE a.global_security_id "
                     f"= s.id AND a.field = {arg(v)} AND a.{LIVE.replace(' AND ', ' AND a.')})")
        elif k == "price_coverage":
            w.append(f"s.price_coverage = {arg(v)}")
        elif k == "has_price":
            neg = "" if str(v).lower() in ("true", "1", "yes") else "NOT "
            w.append(f"{neg}EXISTS (SELECT 1 FROM portfolio.securities_global_prices p WHERE p.global_security_id = s.id "
                     f"AND p.org_id IS NULL AND p.{LIVE.replace(' AND ', ' AND p.')})")
        elif k == "q":
            # dailyops1's search predicate (market_data.read_repository.search_securities): name / short name
            # contains, ticker / CUSIP starts with — case-insensitive, LIKE wildcards escaped
            from services.market_data.read_repository import like_escape

            esc = like_escape(str(v).strip())
            a, b = arg(f"%{esc}%"), arg(esc.upper() + "%")
            w.append(f"(s.name ILIKE {a} OR s.short_name ILIKE {a} OR EXISTS (SELECT 1 FROM "
                     "portfolio.securities_global_identifiers i WHERE i.global_security_id = s.id AND i.id_type IN "
                     f"('ticker', 'cusip') AND upper(i.id_value) LIKE {b} AND i.{LIVE.replace(' AND ', ' AND i.')}))")
        elif k in ("exchange", "category"):
            fld = "exchange" if k == "exchange" else "fund_category"
            w.append("EXISTS (SELECT 1 FROM portfolio.security_reference_attributes a WHERE a.global_security_id = s.id "
                     f"AND a.field = '{fld}' AND a.value = {arg(v)} AND a.{LIVE.replace(' AND ', ' AND a.')})")
        elif k == "tracked":
            neg = "" if str(v).lower() in ("true", "1", "yes") else "NOT "
            w.append(f"{neg}EXISTS (SELECT 1 FROM portfolio.tracked_securities t WHERE t.global_security_id = s.id "
                     f"AND t.status = 'tracked' AND t.{LIVE.replace(' AND ', ' AND t.')})")
        elif k == "in_list":
            w.append("EXISTS (SELECT 1 FROM portfolio.security_list_members m WHERE m.global_security_id = s.id AND "
                     f"m.list_key = {arg(v)} AND m.{LIVE.replace(' AND ', ' AND m.')})")
        else:
            raise SummaryFilterError(f"unknown filter {k!r} (one of {', '.join(FILTERS)})")
    return w


def _scope(scope, args: list) -> str:
    if scope is None:
        return "TRUE"
    args.append([str(x) for x in scope])
    return f"s.id = ANY(${len(args)}::uuid[])"


async def summary(conn, *, scope: list | None = None) -> dict:
    out: dict[str, Any] = {}
    a: list = []
    sc = _scope(scope, a)
    # 1. by type (funds split by structure) — one query
    rows = await conn.fetch(
        f"SELECT s.security_type, CASE WHEN s.security_type = 'fund' THEN COALESCE(fs.value, '{NOT_STATED}') END "
        "AS structure, count(*) AS n FROM portfolio.securities_global s LEFT JOIN (SELECT DISTINCT ON "
        "(global_security_id) global_security_id, value FROM portfolio.security_reference_attributes WHERE field = "
        f"'fund_structure' AND {LIVE} ORDER BY global_security_id, provider_key) fs ON fs.global_security_id = s.id "
        f"WHERE {LIVE_S} AND {sc} GROUP BY 1, 2 ORDER BY 1, 2", *a)
    types: dict[str, dict] = {}
    for r in rows:
        t = types.setdefault(r["security_type"], {"security_type": r["security_type"], "count": 0, "structures": [],
                                                 "filter": {"security_type": r["security_type"]}})
        t["count"] += int(r["n"])
        if r["structure"] is not None:
            t["structures"].append({"structure": r["structure"], "count": int(r["n"]),
                                    "filter": {"security_type": r["security_type"], "fund_structure": r["structure"]}})
    out["total"] = sum(t["count"] for t in types.values())
    out["by_type"] = sorted(types.values(), key=lambda t: -t["count"])
    # 2. identifiers — one query
    a = []
    sc = _scope(scope, a)
    out["identifiers"] = [
        {"id_type": r["id_type"], "securities": int(r["secs"]), "identifiers": int(r["n"]),
         "missing": out["total"] - int(r["secs"]), "filter": {"id_type": r["id_type"]},
         "missing_filter": {"missing_id_type": r["id_type"]}}
        for r in await conn.fetch(
            "SELECT i.id_type, count(DISTINCT i.global_security_id) AS secs, count(*) AS n FROM "
            "portfolio.securities_global_identifiers i JOIN portfolio.securities_global s ON s.id = "
            f"i.global_security_id WHERE i.{LIVE.replace(' AND ', ' AND i.')} AND {LIVE_S} AND {sc} GROUP BY 1 "
            "ORDER BY 1", *a)]
    # 3 + 4. providers and attribute coverage — one query each
    a = []
    sc = _scope(scope, a)
    out["providers"] = [
        {"provider_key": r["provider_key"], "securities": int(r["secs"]), "filter": {"provider": r["provider_key"]}}
        for r in await conn.fetch(
            "SELECT a.provider_key, count(DISTINCT a.global_security_id) AS secs FROM "
            "portfolio.security_reference_attributes a JOIN portfolio.securities_global s ON s.id = "
            f"a.global_security_id WHERE a.{LIVE.replace(' AND ', ' AND a.')} AND {LIVE_S} AND {sc} GROUP BY 1 "
            "ORDER BY 1", *a)]
    a = []
    sc = _scope(scope, a)
    have = {r["field"]: int(r["secs"]) for r in await conn.fetch(
        "SELECT a.field, count(DISTINCT a.global_security_id) AS secs FROM portfolio.security_reference_attributes a "
        f"JOIN portfolio.securities_global s ON s.id = a.global_security_id WHERE a.{LIVE.replace(' AND ', ' AND a.')} "
        f"AND {LIVE_S} AND {sc} GROUP BY 1", *a)}
    out["attributes"] = [{"field": f, "securities": have.get(f, 0), "missing": out["total"] - have.get(f, 0),
                          "filter": {"field": f}, "missing_filter": {"missing_field": f}}
                         for f in M.REFERENCE_FIELDS]
    # 5. prices — one query
    a = []
    sc = _scope(scope, a)
    out["prices"] = [
        {"price_coverage": r["price_coverage"], "securities": int(r["n"]), "with_price": int(r["priced"]),
         "filter": {"price_coverage": r["price_coverage"]},
         "with_price_filter": {"price_coverage": r["price_coverage"], "has_price": "true"}}
        for r in await conn.fetch(
            "SELECT s.price_coverage, count(*) AS n, count(*) FILTER (WHERE EXISTS (SELECT 1 FROM "
            "portfolio.securities_global_prices p WHERE p.global_security_id = s.id AND p.org_id IS NULL AND "
            f"p.{LIVE.replace(' AND ', ' AND p.')})) AS priced FROM portfolio.securities_global s WHERE {LIVE_S} "
            f"AND {sc} GROUP BY 1 ORDER BY 1", *a)]
    # 6. review queue — one query (scoped: items about a scoped security)
    a = []
    rsc = "TRUE" if scope is None else "r.global_security_id = ANY($1::uuid[])"
    if scope is not None:
        a.append([str(x) for x in scope])
    review = [{"kind": r["kind"], "label": M.REVIEW_LABELS.get(r["kind"], r["kind"]), "open": int(r["n"]),
               "review_filter": {"status": "open", "kind": r["kind"]}}
              for r in await conn.fetch(
                  "SELECT r.kind, count(*) AS n FROM portfolio.security_master_review r WHERE r.status = 'open' AND "
                  f"r.{LIVE.replace(' AND ', ' AND r.')} AND {rsc} GROUP BY 1 ORDER BY 1", *a)]
    out["review"] = {"open": sum(x["open"] for x in review), "by_kind": review}
    # 7. last run per job — one query (the platform's job history; not security-scoped)
    out["jobs"] = [{"job": r["job"], "status": r["status"], "trigger": r["run_trigger"],
                    "started_at": r["started_at"], "finished_at": r["finished_at"]}
                   for r in await conn.fetch(
                       "SELECT DISTINCT ON (summary->>'job') summary->>'job' AS job, status, run_trigger, started_at, "
                       "finished_at FROM portfolio.security_data_runs WHERE summary ? 'job' "
                       "ORDER BY summary->>'job', finished_at DESC NULLS LAST")]
    return out


async def securities(conn, *, filters: dict, scope: list | None = None, limit: int = 100, offset: int = 0) -> dict:
    """The filtered list behind a summary count: {rows, total, filters}. One count query + one page query."""
    args: list = []
    w = [LIVE_S, _scope(scope, args)] + _where(filters, args)
    where = " AND ".join(w)
    total = await conn.fetchval(f"SELECT count(*) FROM portfolio.securities_global s WHERE {where}", *args)
    lim = max(1, min(int(limit), LIST_LIMIT_MAX))
    off = max(0, int(offset))
    rows = await conn.fetch(
        f"SELECT s.id, s.name, s.security_type, s.price_coverage, "
        "(SELECT json_object_agg(i.id_type, i.id_value) FROM (SELECT DISTINCT ON (id_type) id_type, id_value FROM "
        "portfolio.securities_global_identifiers WHERE global_security_id = s.id AND valid_to IS NULL AND system_to "
        "IS NULL ORDER BY id_type, is_primary DESC, is_alternate, id_value) i) AS identifiers, "
        # TRACK1 — per row: tracked on purpose? any platform close loaded?
        "EXISTS (SELECT 1 FROM portfolio.tracked_securities t WHERE t.global_security_id = s.id AND t.status = "
        "'tracked' AND t.valid_to IS NULL AND t.system_to IS NULL) AS tracked, "
        "EXISTS (SELECT 1 FROM portfolio.securities_global_prices p WHERE p.global_security_id = s.id AND p.org_id IS "
        "NULL AND p.valid_to IS NULL AND p.system_to IS NULL) AS has_price "
        f"FROM portfolio.securities_global s WHERE {where} ORDER BY s.name, s.id LIMIT {lim} OFFSET {off}", *args)
    out = []
    for r in rows:
        d = dict(r)
        d["identifiers"] = json.loads(d["identifiers"]) if isinstance(d["identifiers"], str) else (d["identifiers"] or {})
        out.append(d)
    return {"rows": out, "total": int(total), "filters": {k: v for k, v in filters.items() if v not in (None, "")},
            "limit": lim, "offset": off}
