"""Saved chart views (mkt03b).

    build_views(conn, org_id, user_id)                → GET    /market/views
    create_view(conn, org_id, user_id, raw)           → POST   /market/views
    update_view(conn, org_id, user_id, id, raw)       → PUT    /market/views/{id}
    delete_view(conn, org_id, user_id, id)            → DELETE /market/views/{id}

Two kinds of row in market_data.saved_views:
  - platform presets (owner_scope 'platform', NULL org_id and user_id) —
    SEED-ONLY. Every write here filters ``owner_scope = 'user'``, so a preset id
    is simply "not found" to the API; RLS refuses it as well.
  - the caller's own views (owner_scope 'user'). RLS isolates the org; every
    query here ALSO filters by user_id from the verified session.

A view stores its anchor RESOLVED (a date, or "N years ago"), never a key-date
reference, and its series by stable key (series_key, or the securities_global
id). A selection whose series is no longer active is reported in
``unavailable`` on read and left in the stored config untouched: a read never
writes.

This module never imports platform_scope. The seed loader
(reference_seed.py) reuses ``validate_config`` and ``selection_problems``.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date
from typing import Annotated, Any, Literal, Optional, Union

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from services.market_data.key_dates import _lock_owner
from services.market_data.personal import (
    clean_name,
    not_found,
    parse_row_id,
    refuse,
    sanitise,
)
from services.market_data.read_service import MODE_LABELS, MarketDataRequestError, _parse_body

VIEWS_MAX = 50
NAME_MAX = 80
SELECTION_MAX = 40
CONFIG_MAX_BYTES = 20_000
RELATIVE_YEARS = (1, 60)

MSG_NAME_EMPTY = "Enter a name for this view."
MSG_NAME_LONG = "Names can be up to 80 characters."
MSG_DUPLICATE = "You already have a view with that name."
MSG_LIMIT = "You can save up to 50 views. Delete one first."
MSG_NOTHING = "Send a name, a config, or both."
MSG_CONFIG = "The view's settings are not valid."
MSG_KEYS = "Every selection must be an active series or a selectable security."

SCALE_LABELS = {"log": "Logarithmic", "linear": "Linear"}
ANCHOR_TYPE_LABELS = {"date": "A date", "relative": "Years before today"}
SELECTION_KIND_LABELS = {"indicator": "Indicator", "security": "Security"}

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,119}$")  # read_service's series-key shape
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ═══════════════════════════════════════════════════════════════════════════
# Canonical config schema — extra='forbid' at every level
# ═══════════════════════════════════════════════════════════════════════════
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SelectionItem(_Strict):
    kind: Literal["indicator", "security"]
    key: StrictStr


class DateAnchor(_Strict):
    type: Literal["date"]
    value: StrictStr


class RelativeAnchor(_Strict):
    type: Literal["relative"]
    years: StrictInt = Field(ge=RELATIVE_YEARS[0], le=RELATIVE_YEARS[1])


Anchor = Annotated[Union[DateAnchor, RelativeAnchor], Field(discriminator="type")]


class Overlays(_Strict):
    events: StrictBool
    band: StrictBool
    emphasis: StrictBool


class ViewConfig(_Strict):
    v: Literal[1]
    selection: list[SelectionItem]
    anchor: Anchor
    end: Optional[Anchor]  # required, may be null
    mode: Literal["index", "sigma", "default", "level"]
    scale: Literal["log", "linear"]
    overlays: Overlays


class CreateBody(_Strict):
    name: Any = None
    config: Any = None


class UpdateBody(_Strict):
    name: Any = None
    config: Any = None


def config_bytes(config) -> int:
    """Size as Postgres measures it in the CHECK: octet_length(config::text).
    jsonb's text form separates with ', ' and ': ', as json.dumps does."""
    return len(json.dumps(config, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


def _cfg_err(path: list, kind: str, msg: str) -> dict:
    return {"loc": ["body", "config", *path], "type": kind, "msg": msg}


def _bad_config(errors: list[dict]) -> MarketDataRequestError:
    return MarketDataRequestError(MSG_CONFIG, errors=errors)


def _parse_iso(text: str) -> date | None:
    if not _DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def validate_config(config) -> dict:
    """Shape rules (no database). Returns the canonical dict to store. Raises
    MarketDataRequestError naming fields and rules — never values."""
    if not isinstance(config, dict):
        raise _bad_config([_cfg_err([], "object_type", "config must be a JSON object")])
    if config_bytes(config) > CONFIG_MAX_BYTES:
        raise _bad_config([_cfg_err([], "too_large", f"config must be at most {CONFIG_MAX_BYTES:,} bytes")])
    try:
        cfg = ViewConfig.model_validate(config)
    except ValidationError as exc:
        raise _bad_config(sanitise(exc, ["config"])) from None

    n = len(cfg.selection)
    if n < 1:
        raise _bad_config([_cfg_err(["selection"], "too_short", "Select at least one series")])
    if n > SELECTION_MAX:
        raise _bad_config([_cfg_err(["selection"], "too_long", f"At most {SELECTION_MAX} selections")])
    malformed = 0
    for item in cfg.selection:
        if item.kind == "indicator":
            malformed += 0 if _KEY_RE.match(item.key) else 1
        else:
            try:
                malformed += 0 if str(uuid.UUID(item.key)) == item.key else 1
            except ValueError:
                malformed += 1
    if malformed:
        raise _bad_config([_cfg_err(["selection"], "malformed_key",
                                    f"{malformed} key(s) are not well-formed series keys or security ids")])
    seen: set = set()
    dupes: list[str] = []
    for item in cfg.selection:
        pair = (item.kind, item.key)
        if pair in seen and item.key not in dupes:
            dupes.append(item.key)
        seen.add(pair)
    if dupes:
        raise MarketDataRequestError(MSG_CONFIG, errors=[_cfg_err(["selection"], "duplicate",
                                                                   "Each series may be selected once")],
                                     duplicate_keys=dupes)

    dates: dict[str, date] = {}
    for field in ("anchor", "end"):
        a = getattr(cfg, field)
        if isinstance(a, DateAnchor):
            d = _parse_iso(a.value)
            if d is None:
                raise _bad_config([_cfg_err([field, "value"], "date_format", "Must be a calendar date, YYYY-MM-DD")])
            dates[field] = d
    if "anchor" in dates and "end" in dates and dates["anchor"] > dates["end"]:
        raise _bad_config([_cfg_err(["anchor"], "window", "anchor must not be after end")])
    return cfg.model_dump(mode="json")


async def selection_problems(conn, selection: list[dict]) -> dict[str, list[str]]:
    """{unknown_keys, inactive_keys, unselectable_keys} for a validated
    selection — each list holds only offending keys, in selection order."""
    ind = [s["key"] for s in selection if s["kind"] == "indicator"]
    sec = [s["key"] for s in selection if s["kind"] == "security"]
    status: dict[str, str] = {}
    if ind:
        rows = await conn.fetch(
            "SELECT series_key, ingest_status FROM market_data.indicator_series WHERE series_key = ANY($1::text[])",
            ind)
        status = {r["series_key"]: r["ingest_status"] for r in rows}
    ok_secs = await selectable_securities(conn, sec)
    return {
        "unknown_keys": [k for k in ind if k not in status],
        "inactive_keys": [k for k in ind if k in status and status[k] != "active"],
        "unselectable_keys": [k for k in sec if k not in ok_secs],
    }


async def selectable_securities(conn, ids: list[str]) -> set[str]:
    """The ids the mkt03 catalog marks ``selectable``: a current, non-merged
    securities_global row priced by an ACTIVE indicator series."""
    good = []
    for k in ids:
        try:
            good.append(str(uuid.UUID(k)))
        except ValueError:
            continue
    if not good:
        return set()
    rows = await conn.fetch(
        """SELECT g.id FROM portfolio.securities_global g
             JOIN market_data.indicator_series s
               ON s.security_global_id = g.id AND s.ingest_status = 'active'
            WHERE g.id = ANY($1::uuid[])
              AND g.valid_to IS NULL AND g.system_to IS NULL AND g.merged_into_id IS NULL""",
        good)
    return {str(r["id"]) for r in rows}


async def _validate_resolved(conn, config) -> dict:
    canonical = validate_config(config)
    problems = await selection_problems(conn, canonical["selection"])
    if any(problems.values()):
        raise MarketDataRequestError(MSG_KEYS, errors=[_cfg_err(["selection"], "unavailable_key", MSG_KEYS)],
                                     **problems)
    return canonical


# ═══════════════════════════════════════════════════════════════════════════
# Reads
# ═══════════════════════════════════════════════════════════════════════════
def _load_config(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _selection_of(config) -> list[dict]:
    sel = config.get("selection") if isinstance(config, dict) else None
    if not isinstance(sel, list):
        return []
    return [s for s in sel if isinstance(s, dict) and isinstance(s.get("key"), str)]


async def _availability(conn, configs: list) -> tuple[set[str], set[str]]:
    ind, sec = set(), set()
    for c in configs:
        for s in _selection_of(c):
            (ind if s.get("kind") == "indicator" else sec).add(s["key"])
    active: set[str] = set()
    if ind:
        rows = await conn.fetch(
            """SELECT series_key FROM market_data.indicator_series
                WHERE series_key = ANY($1::text[]) AND ingest_status = 'active'""", sorted(ind))
        active = {r["series_key"] for r in rows}
    return active, await selectable_securities(conn, sorted(sec))


def _unavailable(config, active: set[str], selectable: set[str]) -> list[str]:
    out = []
    for s in _selection_of(config):
        ok = s["key"] in active if s.get("kind") == "indicator" else s["key"] in selectable
        if not ok and s["key"] not in out:
            out.append(s["key"])
    return out


def _view_out(r, config, active, selectable) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "config": config,
            "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
            "unavailable": _unavailable(config, active, selectable)}


def vocabularies() -> dict:
    return {
        "editable": [],
        "inline_editable": [],
        "modes": [{"key": k, "label": v} for k, v in MODE_LABELS.items()],
        "scales": [{"key": k, "label": v} for k, v in SCALE_LABELS.items()],
        "anchor_types": [{"key": k, "label": v} for k, v in ANCHOR_TYPE_LABELS.items()],
        "selection_kinds": [{"key": k, "label": v} for k, v in SELECTION_KIND_LABELS.items()],
    }


async def build_views(conn, org_id, user_id) -> dict:
    presets = await conn.fetch(
        """SELECT id, name, config, created_at, updated_at FROM market_data.saved_views
            WHERE owner_scope = 'platform' ORDER BY lower(name), id""")
    own = await conn.fetch(
        """SELECT id, name, config, created_at, updated_at FROM market_data.saved_views
            WHERE owner_scope = 'user' AND org_id = $1::uuid AND user_id = $2::uuid
            ORDER BY lower(name), id""",
        str(org_id), str(user_id))
    p_cfg = [_load_config(r["config"]) for r in presets]
    o_cfg = [_load_config(r["config"]) for r in own]
    active, selectable = await _availability(conn, p_cfg + o_cfg)
    return {
        "presets": [_view_out(r, c, active, selectable) for r, c in zip(presets, p_cfg)],
        "views": [_view_out(r, c, active, selectable) for r, c in zip(own, o_cfg)],
        "vocabularies": vocabularies(),
        "limits": {"views_max": VIEWS_MAX, "name_max": NAME_MAX, "selection_max": SELECTION_MAX,
                   "config_max_bytes": CONFIG_MAX_BYTES, "relative_years": list(RELATIVE_YEARS)},
    }


# ═══════════════════════════════════════════════════════════════════════════
# Writes — always owner_scope = 'user' AND the caller's org_id AND user_id
# ═══════════════════════════════════════════════════════════════════════════
def _name(value) -> str:
    return clean_name(value, max_len=NAME_MAX, empty_msg=MSG_NAME_EMPTY, long_msg=MSG_NAME_LONG)


async def _name_taken(conn, org_id, user_id, name: str, exclude_id=None) -> bool:
    return bool(await conn.fetchval(
        """SELECT 1 FROM market_data.saved_views
            WHERE owner_scope = 'user' AND org_id = $1::uuid AND user_id = $2::uuid
              AND lower(name) = lower($3) AND ($4::uuid IS NULL OR id <> $4::uuid)""",
        str(org_id), str(user_id), name, exclude_id))


async def _row_out(conn, row) -> dict:
    config = _load_config(row["config"])
    active, selectable = await _availability(conn, [config])
    return _view_out(row, config, active, selectable)


async def create_view(conn, org_id, user_id, raw: bytes) -> dict:
    body = _parse_body(CreateBody, raw)
    name = _name(body.name)
    config = await _validate_resolved(conn, body.config)
    await _lock_owner(conn, "saved_views", org_id, user_id)
    if await _name_taken(conn, org_id, user_id, name):
        raise refuse(MSG_DUPLICATE, "name", "duplicate", status=409)
    count = await conn.fetchval(
        """SELECT count(*) FROM market_data.saved_views
            WHERE owner_scope = 'user' AND org_id = $1::uuid AND user_id = $2::uuid""",
        str(org_id), str(user_id))
    if count >= VIEWS_MAX:
        raise refuse(MSG_LIMIT, None, "limit")
    try:
        row = await conn.fetchrow(
            """INSERT INTO market_data.saved_views (owner_scope, org_id, user_id, name, config)
               VALUES ('user', $1::uuid, $2::uuid, $3, $4::jsonb)
               RETURNING id, name, config, created_at, updated_at""",
            str(org_id), str(user_id), name, json.dumps(config))
    except asyncpg.UniqueViolationError:
        raise refuse(MSG_DUPLICATE, "name", "duplicate", status=409) from None
    return await _row_out(conn, row)


async def update_view(conn, org_id, user_id, row_id: str, raw: bytes) -> dict:
    rid = parse_row_id(row_id)
    body = _parse_body(UpdateBody, raw)
    sent = body.model_fields_set
    if not ({"name", "config"} & sent):
        raise refuse(MSG_NOTHING, None, "missing")
    name = _name(body.name) if "name" in sent else None
    config = await _validate_resolved(conn, body.config) if "config" in sent else None

    await _lock_owner(conn, "saved_views", org_id, user_id)
    current = await conn.fetchrow(
        """SELECT id FROM market_data.saved_views
            WHERE id = $1 AND owner_scope = 'user' AND org_id = $2::uuid AND user_id = $3::uuid
            FOR UPDATE""",
        rid, str(org_id), str(user_id))
    if current is None:
        raise not_found()
    if name is not None and await _name_taken(conn, org_id, user_id, name, rid):
        raise refuse(MSG_DUPLICATE, "name", "duplicate", status=409)
    try:
        row = await conn.fetchrow(
            """UPDATE market_data.saved_views
                  SET name = COALESCE($4, name),
                      config = COALESCE($5::jsonb, config),
                      updated_at = now()
                WHERE id = $1 AND owner_scope = 'user' AND org_id = $2::uuid AND user_id = $3::uuid
            RETURNING id, name, config, created_at, updated_at""",
            rid, str(org_id), str(user_id), name, json.dumps(config) if config is not None else None)
    except asyncpg.UniqueViolationError:
        raise refuse(MSG_DUPLICATE, "name", "duplicate", status=409) from None
    if row is None:
        raise not_found()
    return await _row_out(conn, row)


async def delete_view(conn, org_id, user_id, row_id: str) -> dict:
    rid = parse_row_id(row_id)
    deleted = await conn.fetchval(
        """DELETE FROM market_data.saved_views
            WHERE id = $1 AND owner_scope = 'user' AND org_id = $2::uuid AND user_id = $3::uuid
        RETURNING id""",
        rid, str(org_id), str(user_id))
    if deleted is None:
        raise not_found()
    return {"deleted": True}
