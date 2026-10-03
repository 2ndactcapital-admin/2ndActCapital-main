"""Query-level functions for the market data read API (mkt03).

    build_catalog(conn)                         → GET  /market/catalog
    read_series(conn, query)                    → GET  /market/series
    build_grid(conn, body, today)               → POST /market/grid
    build_correlations(conn, body, today)       → POST /market/correlations

Each takes the REQUEST connection and only reads. Request bodies are parsed
here (``parse_grid`` / ``parse_correlations`` / ``parse_series_query``) so that
every refusal goes through ONE sanitiser: a 422 names the field and the rule,
never the value the caller sent. FastAPI's default 422 echoes the offending
input back (pydantic's ``input``), which is why the router does not let
FastAPI validate these bodies itself.

Limits (docs/MARKET_DATA_DESIGN_V1.md, "API contract"):
  MAX_KEYS = 40 per request (series and grid keys; correlation candidates).
  MAX_POINTS_PER_SERIES = 30,000 — the largest active series (fred.dff) had
    26,391 active observations at mkt03 discovery; the cap leaves headroom for
    roughly 13 more years of daily data before it bites.
  MAX_TOTAL_POINTS = 300,000 per series request — just under the whole active
    table at discovery (313,156), so one request can never pull all of it.
  MAX_GRID_ROWS = 2,000; daily grid rows only for windows <= 400 days.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from services.market_data import correlation as corr
from services.market_data import palette
from services.market_data import read_repository as repo
from services.market_data.resample import (
    FREQUENCY_RANK,
    GRID_FREQUENCIES,
    REQUEST_FREQUENCIES,
    add_months,
    certainly_more_periods_than,
    effective_frequency,
    is_period_end,
    period_ends,
    resample_for_request,
)
from services.market_data.transforms import (
    DEFAULT_TRANSFORMS,
    MODES,
    Series,
    decimal_text,
    transform_column,
)

MAX_KEYS = 40
MAX_POINTS_PER_SERIES = 30_000
MAX_TOTAL_POINTS = 300_000
MAX_GRID_ROWS = 2_000
MAX_DAILY_GRID_DAYS = 400
MAX_BODY_BYTES = 64 * 1024
LAG_RANGE = (-24, 24)
MIN_PERIODS_RANGE = (12, 120)

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,119}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MODE_LABELS = {
    "index": "Indexed to 100 at the anchor",
    "sigma": "Standard deviations from the anchor",
    "level": "Level",
    "default": "Each series' default view",
}
TRANSFORM_LABELS = {
    "rebase_100": "Rebased to 100",
    "level": "Level",
    "yoy_pct": "Year-over-year change (%)",
    "mom_pct": "Month-over-month change (%)",
}
FREQUENCY_LABELS = {
    "native": "Native",
    "daily": "Daily",
    "weekly": "Weekly",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
}


# ═══════════════════════════════════════════════════════════════════════════
# Errors — never echo the request
# ═══════════════════════════════════════════════════════════════════════════
class MarketDataRequestError(Exception):
    """A refusal the router turns into ``{"detail": {...}}`` with ``status``.

    ``message`` and ``errors`` are built ONLY from field names this module
    declares, fixed rule text, and (for unknown/non-active keys) the offending
    keys themselves after they passed the well-formed-key pattern.
    """

    def __init__(self, message: str, *, errors: list[dict] | None = None, status: int = 422, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.errors = errors or []
        self.extra = extra

    def detail(self) -> dict:
        out = {"message": self.message}
        if self.errors:
            out["errors"] = self.errors
        out.update(self.extra)
        return out


def _err(field: str, kind: str, msg: str) -> dict:
    return {"loc": ["body", field] if field else ["body"], "type": kind, "msg": msg}


def _sanitise(exc: ValidationError) -> list[dict]:
    out: list[dict] = []
    extra_seen = False
    for e in exc.errors(include_url=False, include_context=False, include_input=False):
        if e["type"] == "extra_forbidden":
            # The NAME of an undeclared field came from the caller: not echoed.
            if not extra_seen:
                out.append(_err("", "extra_forbidden",
                                "The request contains a field this endpoint does not accept"))
                extra_seen = True
            continue
        loc = ["body", *[x if isinstance(x, int) else str(x) for x in e["loc"]]]
        out.append({"loc": loc, "type": e["type"], "msg": e["msg"]})
    return out


def _parse_body(model, raw: bytes):
    if len(raw) > MAX_BODY_BYTES:
        raise MarketDataRequestError("Request body is too large")
    try:
        data = json.loads(raw or b"null")
    except (ValueError, UnicodeDecodeError):
        raise MarketDataRequestError("Request body must be valid JSON") from None
    if not isinstance(data, dict):
        raise MarketDataRequestError("Request body must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise MarketDataRequestError("Request validation failed", errors=_sanitise(exc)) from None


def parse_date(text: str | None, field: str, *, required: bool) -> date | None:
    if text is None:
        if required:
            raise MarketDataRequestError("Request validation failed",
                                         errors=[_err(field, "missing", "Field required")])
        return None
    if not isinstance(text, str) or not _DATE_RE.match(text):
        raise MarketDataRequestError("Request validation failed",
                                     errors=[_err(field, "date_format", "Must be a calendar date, YYYY-MM-DD")])
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise MarketDataRequestError("Request validation failed",
                                     errors=[_err(field, "date_format", "Must be a calendar date, YYYY-MM-DD")]) from None


def _check_key_list(keys: list[str], field: str) -> list[str]:
    """Shape rules only: non-empty, <= MAX_KEYS, well-formed. Order-preserving dedupe."""
    if not keys:
        raise MarketDataRequestError("Request validation failed",
                                     errors=[_err(field, "empty", "At least one series key is required")])
    if len(keys) > MAX_KEYS:
        raise MarketDataRequestError("Request validation failed",
                                     errors=[_err(field, "too_many", f"At most {MAX_KEYS} keys per request")])
    bad = sum(1 for k in keys if not _KEY_RE.match(k))
    if bad:
        raise MarketDataRequestError(
            "Request validation failed",
            errors=[_err(field, "malformed_key", f"{bad} key(s) are not well-formed series keys")])
    return list(dict.fromkeys(keys))


async def resolve_active(conn, keys: list[str]) -> list[dict]:
    """Registry rows for ``keys``, in request order. Unknown or non-active
    keys → 422 naming only those keys."""
    found = await repo.series_by_keys(conn, keys)
    unknown = [k for k in keys if k not in found]
    inactive = [k for k in keys if k in found and found[k]["ingest_status"] != "active"]
    if unknown or inactive:
        raise MarketDataRequestError(
            "Every key must be an active registry series",
            unknown_keys=unknown, inactive_keys=inactive,
        )
    return [found[k] for k in keys]


# ═══════════════════════════════════════════════════════════════════════════
# Request models — extra='forbid': an org_id or user field is a 422
# ═══════════════════════════════════════════════════════════════════════════
class GridBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[StrictStr]
    anchor: StrictStr
    end: Optional[StrictStr] = None
    mode: Literal["index", "sigma", "level", "default"]
    frequency: Literal["daily", "weekly", "monthly", "quarterly"]


class CorrelationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focus_key: StrictStr
    keys: list[StrictStr]
    anchor: StrictStr
    end: Optional[StrictStr] = None
    lag_months: StrictInt = Field(0, ge=LAG_RANGE[0], le=LAG_RANGE[1])
    min_periods: StrictInt = Field(24, ge=MIN_PERIODS_RANGE[0], le=MIN_PERIODS_RANGE[1])


@dataclass
class SeriesQuery:
    keys: list[str]
    date_from: date | None
    date_to: date | None
    frequency: str


@dataclass
class GridRequest:
    keys: list[str]
    anchor: date
    end: date
    mode: str
    frequency: str


@dataclass
class CorrelationRequest:
    focus_key: str
    keys: list[str]
    anchor: date
    end: date
    lag_months: int
    min_periods: int


_SERIES_PARAMS = {"keys", "from", "to", "frequency"}


def parse_series_query(multi_items: list[tuple[str, str]]) -> SeriesQuery:
    names = [k for k, _ in multi_items]
    if any(n not in _SERIES_PARAMS for n in names):
        raise MarketDataRequestError("Request validation failed", errors=[{
            "loc": ["query"], "type": "extra_forbidden",
            "msg": "The request contains a query parameter this endpoint does not accept"}])
    if len(names) != len(set(names)):
        raise MarketDataRequestError("Request validation failed", errors=[{
            "loc": ["query"], "type": "duplicate", "msg": "Each query parameter may appear once"}])
    params = dict(multi_items)
    keys = [k.strip() for k in params.get("keys", "").split(",") if k.strip()]
    keys = _check_key_list(keys, "keys")
    date_from = parse_date(params.get("from"), "from", required=False)
    date_to = parse_date(params.get("to"), "to", required=False)
    if date_from and date_to and date_from > date_to:
        raise MarketDataRequestError("Request validation failed",
                                     errors=[_err("from", "window", "from must not be after to")])
    freq = params.get("frequency", "native")
    if freq not in REQUEST_FREQUENCIES:
        raise MarketDataRequestError("Request validation failed", errors=[_err(
            "frequency", "literal_error", "Must be one of: " + ", ".join(REQUEST_FREQUENCIES))])
    return SeriesQuery(keys, date_from, date_to, freq)


def _window(anchor_text: str, end_text: str | None, today: date) -> tuple[date, date]:
    anchor = parse_date(anchor_text, "anchor", required=True)
    end = parse_date(end_text, "end", required=False) or today
    if anchor > end:
        raise MarketDataRequestError("Request validation failed",
                                     errors=[_err("anchor", "window", "anchor must not be after end")])
    return anchor, end


def parse_grid(raw: bytes, today: date) -> GridRequest:
    body = _parse_body(GridBody, raw)
    keys = _check_key_list(body.keys, "keys")
    anchor, end = _window(body.anchor, body.end, today)
    return GridRequest(keys, anchor, end, body.mode, body.frequency)


def parse_correlations(raw: bytes, today: date) -> CorrelationRequest:
    body = _parse_body(CorrelationBody, raw)
    _check_key_list([body.focus_key], "focus_key")
    keys = _check_key_list(body.keys, "keys")
    anchor, end = _window(body.anchor, body.end, today)
    return CorrelationRequest(body.focus_key, keys, anchor, end, body.lag_months, body.min_periods)


# ═══════════════════════════════════════════════════════════════════════════
# Vocabularies
# ═══════════════════════════════════════════════════════════════════════════
def base_vocabularies() -> dict:
    return {
        "editable": [],
        "inline_editable": [],
        "modes": [{"key": m, "label": MODE_LABELS[m]} for m in MODES],
        "transforms": [{"key": t, "label": TRANSFORM_LABELS[t]} for t in DEFAULT_TRANSFORMS],
        "frequencies": [{"key": f, "label": FREQUENCY_LABELS[f]} for f in REQUEST_FREQUENCIES],
        "grid_frequencies": [{"key": f, "label": FREQUENCY_LABELS[f]} for f in GRID_FREQUENCIES],
        "limits": {
            "max_keys": MAX_KEYS,
            "max_points_per_series": MAX_POINTS_PER_SERIES,
            "max_total_points": MAX_TOTAL_POINTS,
            "max_grid_rows": MAX_GRID_ROWS,
            "max_daily_grid_days": MAX_DAILY_GRID_DAYS,
            "lag_months": list(LAG_RANGE),
            "min_periods": list(MIN_PERIODS_RANGE),
        },
    }


def category_key(category: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


# ═══════════════════════════════════════════════════════════════════════════
# GET /market/catalog
# ═══════════════════════════════════════════════════════════════════════════
async def build_catalog(conn) -> dict:
    series = await repo.active_series(conn)  # ordered by sort_order, series_key
    bounds = await repo.series_bounds(conn, [s["id"] for s in series])
    securities = await repo.catalog_securities(conn)

    cat_first_sort: dict[str, int] = {}
    groups: dict[str, list[str]] = {}
    for s in series:
        cat_first_sort.setdefault(s["category"], s["sort_order"])
        groups.setdefault(s["category"], []).append(s["series_key"])
    series_colors = palette.assign(groups, palette.category_base)
    categories = [
        {"key": category_key(c), "label": c, "color": palette.category_base(c), "sort_order": so}
        for c, so in sorted(cat_first_sort.items(), key=lambda kv: (kv[1], kv[0]))
    ]

    indicators = []
    for s in series:
        b = bounds.get(s["id"], {})
        indicators.append({
            "series_key": s["series_key"],
            "name": s["name"],
            "category": s["category"],
            "category_key": category_key(s["category"]),
            "region": s["region"],
            "frequency": s["frequency"],
            "units": s["units"],
            "seasonal_adjustment": s["seasonal_adjustment"],
            "default_transform": s["default_transform"],
            "color": series_colors[s["series_key"]],
            "source_provider": s["source_provider"],
            "license_class": s["license_class"],
            "cost_tier": s["cost_tier"],
            "first_observation_date": _iso(b.get("first")),
            "last_observation_date": _iso(b.get("last")),
            "security_global_id": str(s["security_global_id"]) if s["security_global_id"] else None,
            "ingest_status": s["ingest_status"],
            "sort_order": s["sort_order"],
        })

    sec_groups: dict[str, list[str]] = {}
    for g in securities:
        sec_groups.setdefault(g["security_type"], []).append(str(g["id"]))
    sec_base = {"index": palette.INDEX_SECURITY_BASE, "structured_note": palette.NOTE_SECURITY_BASE}
    sec_colors = palette.assign(sec_groups, lambda t: sec_base.get(t, palette.NEUTRAL))
    out_secs = []
    for g in securities:
        linked = g["series_key"] is not None
        out_secs.append({
            "id": str(g["id"]),
            "name": g["name"],
            "short_name": g["short_name"],
            "security_type": g["security_type"],
            "price_source": "indicator_series" if linked else "none",
            "series_key": g["series_key"],
            "selectable": linked,
            "unselectable_reason": None if linked else "no_price_history",
            "color": sec_colors[str(g["id"])],
        })

    vocab = base_vocabularies()
    vocab["license_classes"] = sorted({s["license_class"] for s in series})
    vocab["source_providers"] = sorted({s["source_provider"] for s in series})
    return {"categories": categories, "indicators": indicators, "securities": out_secs,
            "vocabularies": vocab}


# ═══════════════════════════════════════════════════════════════════════════
# GET /market/series
# ═══════════════════════════════════════════════════════════════════════════
async def read_series(conn, q: SeriesQuery) -> dict:
    rows = await resolve_active(conn, q.keys)
    ids = [r["id"] for r in rows]
    bounds = await repo.series_bounds(conn, ids)
    obs = await repo.observations(conn, ids, q.date_from, q.date_to)

    out = []
    over_cap: list[str] = []
    total = 0
    for r in rows:
        freq, pts = resample_for_request(obs[r["id"]], r["frequency"], q.frequency)
        if len(pts) > MAX_POINTS_PER_SERIES:
            over_cap.append(r["series_key"])
        total += len(pts)
        b = bounds.get(r["id"], {})
        out.append({
            "series_key": r["series_key"],
            "native_frequency": r["frequency"],
            "frequency": freq,
            "point_count": len(pts),
            "first_observation_date": _iso(b.get("first")),
            "last_observation_date": _iso(b.get("last")),
            "points": [[d.isoformat(), decimal_text(v)] for d, v in pts],
        })
    if over_cap:
        raise MarketDataRequestError(
            f"More than {MAX_POINTS_PER_SERIES} points for a series; ask for a coarser frequency "
            "or a shorter window", over_cap_keys=over_cap)
    if total > MAX_TOTAL_POINTS:
        raise MarketDataRequestError(
            f"More than {MAX_TOTAL_POINTS} points in one request; ask for fewer keys, a coarser "
            "frequency or a shorter window")
    return {
        "window": {"from": _iso(q.date_from), "to": _iso(q.date_to)},
        "requested_frequency": q.frequency,
        "series": out,
        "vocabularies": base_vocabularies(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# POST /market/grid
# ═══════════════════════════════════════════════════════════════════════════
def grid_row_dates(anchor: date, end: date, frequency: str) -> list[date]:
    """Period ends in [anchor, end], ascending — plus ``end`` itself when it is
    not a period end, so the newest row shows the latest data instead of last
    period's (that row has is_period_end = false). Raises past the caps."""
    if frequency == "daily" and (end - anchor).days + 1 > MAX_DAILY_GRID_DAYS:
        raise MarketDataRequestError("Request validation failed", errors=[_err(
            "frequency", "window_too_long",
            f"daily rows need a window of at most {MAX_DAILY_GRID_DAYS} days")])
    too_many = MarketDataRequestError("Request validation failed", errors=[_err(
        "frequency", "too_many_rows", f"At most {MAX_GRID_ROWS} grid rows; use a coarser frequency "
        "or a shorter window")])
    if certainly_more_periods_than(anchor, end, frequency, MAX_GRID_ROWS):
        raise too_many
    dates = period_ends(anchor, end, frequency)
    if frequency != "daily" and (not dates or dates[-1] != end):
        dates.append(end)
    if len(dates) > MAX_GRID_ROWS:
        raise too_many
    return dates


async def build_grid(conn, req: GridRequest) -> dict:
    row_dates = grid_row_dates(req.anchor, req.end, req.frequency)
    rows_meta = await resolve_active(conn, req.keys)
    ids = [r["id"] for r in rows_meta]
    stats = await repo.series_stats(conn, ids)
    obs = await repo.observations(conn, ids, add_months(req.anchor, -12), req.end, include_prior=True)

    series_out = []
    columns = []
    for r in rows_meta:
        st = stats.get(r["id"], {})
        col = transform_column(
            Series(obs[r["id"]]), row_dates,
            mode=req.mode, default_transform=r["default_transform"], anchor=req.anchor,
            first_observation_date=st.get("first"), sd=st.get("sd"),
        )
        warnings = list(col.warnings)
        native_eff = effective_frequency(r["frequency"])
        if FREQUENCY_RANK.get(native_eff, 99) > FREQUENCY_RANK[req.frequency]:
            warnings.append("native_frequency_coarser_than_grid")
        if col.unavailable_reason is None and not obs[r["id"]]:
            col.unavailable_reason = "no_observations"
        columns.append(col)
        series_out.append({
            "series_key": r["series_key"],
            "name": r["name"],
            "native_frequency": r["frequency"],
            "default_transform": r["default_transform"],
            "applied_transform": col.applied,
            "floating": col.floating,
            "anchor_observation_date": _iso(col.anchor_date),
            "anchor_value": decimal_text(col.anchor_value),
            "warnings": warnings,
            "unavailable_reason": col.unavailable_reason,
            "first_observation_date": _iso(st.get("first")),
            "last_observation_date": _iso(st.get("last")),
        })

    keys = [r["series_key"] for r in rows_meta]
    rows = []
    for i in range(len(row_dates) - 1, -1, -1):  # newest first
        d = row_dates[i]
        rows.append({
            "date": d.isoformat(),
            "is_period_end": is_period_end(d, req.frequency),
            "cells": {k: decimal_text(c.values[i]) for k, c in zip(keys, columns)},
        })
    return {
        "anchor": req.anchor.isoformat(),
        "end": req.end.isoformat(),
        "mode": req.mode,
        "frequency": req.frequency,
        "row_count": len(rows),
        "series": series_out,
        "rows": rows,
        "vocabularies": base_vocabularies(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# POST /market/correlations
# ═══════════════════════════════════════════════════════════════════════════
async def build_correlations(conn, req: CorrelationRequest) -> dict:
    candidates = [k for k in req.keys if k != req.focus_key]  # the focus is never its own candidate
    rows_meta = await resolve_active(conn, [req.focus_key, *candidates])
    focus, cand_rows = rows_meta[0], rows_meta[1:]
    obs = await repo.observations(conn, [r["id"] for r in rows_meta], req.anchor, req.end)

    pairs = []
    for c in cand_rows:
        res = corr.correlate_pair(
            obs[focus["id"]], focus["frequency"], obs[c["id"]], c["frequency"],
            anchor=req.anchor, end=req.end, lag_months=req.lag_months, min_periods=req.min_periods,
        )
        pairs.append((c["series_key"], res))
    results = [{
        "series_key": key,
        "r": res.r,
        "n": res.n,
        "frequency": res.frequency,
        "overlap_from": _iso(res.overlap_from),
        "overlap_to": _iso(res.overlap_to),
        "unavailable_reason": res.unavailable_reason,
        "change_method": res.change_method,
        "warnings": res.warnings,
    } for key, res in corr.sort_results(pairs)]
    return {
        "focus_key": req.focus_key,
        "window": {"anchor": req.anchor.isoformat(), "end": req.end.isoformat()},
        "lag_months": req.lag_months,
        "effective_lag_months": req.lag_months,
        "lag_convention": "positive lag: the candidate leads the focus by that many months",
        "min_periods": req.min_periods,
        "results": results,
        "vocabularies": base_vocabularies(),
    }
