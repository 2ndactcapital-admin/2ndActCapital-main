"""Replication Studio (rep3) — model management: ONE registry for every model,
whatever its source.

A model comes from an uploaded file, the model library (copy or subscription),
a replicated fund (rep4) or other models by weight (a composite). All of them
land here with the same versions, approvals and log, so the account, tax and fee
machinery (rep5+) never asks where a model came from.

VERSIONS
──────────────────────────────────────────────────────────────────────────────
draft ─submit→ submitted ─approve→ approved ─(next approval)→ superseded
  │               └─reject (note)→ draft
  └─abandon (reason)→ abandoned        (a submitted version may be abandoned too;
                                        its open task is cancelled first)
* One approved version live per model and one open (draft/submitted) version
  per model — partial unique indexes. An approved version never changes: the
  service answers 409 and the trigger ``model_versions_guard`` refuses it in the
  database (the single exception: approved → superseded, nothing else touched).
* Everyone who uploads, edits or submits a draft is in ``maker_ids``. Approve /
  reject is refused (403, naming the rule) for any of them — and the CHECK
  ``model_versions_maker_checker_chk`` refuses a maker as ``checker_id``.
* A version with unresolved positions cannot be approved (409, naming them).
* Rule 3: models / versions / library entries / subscriptions archive on the
  SYSTEM axis (the live row keeps its id); positions, sleeves and mappings on
  the VALID axis (close + insert).

APPROVAL IS A GOVERNED HUMAN TASK (services.human_tasks, rep2). Submit opens a
``model_version_approval`` task (maker-checker at the engine too: the submitter
is the run's ``started_by``) for holders of ``approve_models``. A draft the
platform itself produced — a library subscription's new version, a composite
following a child — opens a ``model_version_review`` task with NO maker
(``system_open``), so any approver may decide it. Both kinds apply their
decision in ``on_complete`` (idempotent), and :func:`sync_task_outcome`
re-applies an outcome the engine recorded if the subject update ever failed.

THE CUSTODY CLIFF. Approving a version never trades. It emits the domain event
``model.version_approved`` (source_type 'model_version', payload = the diff
against the version it supersedes); rep5 turns that into rebalance PROPOSALS.
Then, still with no trade anywhere: every composite in the org that follows
the model LIVE gets a draft + review task, and every subscriber of a library
entry built on the model gets a draft + review task in its own org.

LIBRARY. Shelf 'platform' entries (org_id NULL) are published by Super Admin
from a model owned by the Hollisworks platform org, readable by every org
(RLS carve-out: the live approved version of a published platform entry's
model, read-only); shelf 'org' entries are published by an org admin with
manage_models and are visible to their own org only. Copy = an independent
org model (a later entry change does nothing). Subscribe = an org model that
receives a draft + review task on every new approved version of the entry's
model. ``requires_agreement`` entries need an org admin's logged "agreement on
file" confirmation before either.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from starlette.concurrency import run_in_threadpool

from services import human_tasks as HT
from services import model_upload as U
from services import storage

log = logging.getLogger(__name__)

T_MODEL = "portfolio.models"
T_VER = "portfolio.model_versions"
T_POS = "portfolio.model_version_positions"
T_SLV = "portfolio.model_sleeves"
T_UP = "portfolio.model_uploads"
T_UPE = "portfolio.model_upload_events"
T_MAP = "portfolio.model_column_mappings"
T_LIB = "portfolio.model_library_entries"
T_SUB = "portfolio.model_subscriptions"
T_EVT = "portfolio.model_events"

PLATFORM_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"
VIEW = "view_models"
MANAGE = "manage_models"
APPROVE = "approve_models"
ORG_ADMIN_PERMISSION = "manage_org_settings"
TASK_APPROVAL = "model_version_approval"
TASK_REVIEW = "model_version_review"
TASK_SUBJECT = "model_version"
RESERVED_PREFIX = "holdings of"
R2_PREFIX = "models/uploads"

SOURCE_LABELS = {"upload": "Uploaded file", "library_copy": "Library copy", "library_subscription": "Library subscription",
                 "fund_replica": "Replicated fund", "composite": "Composite (sleeves)"}
MODEL_STATUS_LABELS = {"draft": "Draft", "active": "Active", "retired": "Retired"}
VERSION_STATUS_LABELS = {"draft": "Draft", "submitted": "Awaiting approval", "approved": "Approved",
                         "superseded": "Superseded", "rejected": "Rejected", "abandoned": "Abandoned"}
ORIGIN_LABELS = {"upload": "Upload", "manual": "Entered by hand", "library_copy": "Copied from the library",
                 "subscription": "Subscription update", "composite_follow": "A sleeve's model changed",
                 "revision": "Revision of the approved version", "fund_build": "Built from the fund",
                 "fund_monitor": "The fund's holdings changed"}
UPLOAD_OUTCOME_LABELS = {"received": "In progress", "draft_created": "Draft created", "rejected": "Rejected",
                         "abandoned": "Abandoned", "duplicate": "Duplicate", "failed": "Failed"}
UPLOAD_SOURCE_LABELS = {"manual": "Uploaded by hand", "provider_email": "Provider email",
                        "provider_feed": "Provider feed", "library_publish": "Library publish"}
CADENCE_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "quarterly": "Quarterly",
                  "semiannual": "Twice a year", "annual": "Annually", "ad_hoc": "As needed"}
SHELF_LABELS = {"platform": "Platform shelf", "org": "Our shelf"}
FOLLOW_LABELS = {"live": "Follow its approved version", "pinned": "Pinned to one version"}
LIBRARY_STATUS_LABELS = {"published": "Published", "unpublished": "Unpublished", "retired": "Retired"}
EVENT_LABELS = {
    "create_model": "Model created", "edit_model": "Model details changed", "create_draft": "Draft created",
    "edit_positions": "Positions edited", "edit_sleeves": "Sleeves edited", "submit": "Submitted for approval",
    "approve": "Approved", "reject": "Returned to draft", "abandon": "Abandoned", "save_mapping": "Column mapping saved",
    "publish": "Published to the library", "unpublish": "Unpublished", "retire_entry": "Library entry retired",
    "agreement_confirmed": "Agreement on file confirmed", "copy": "Copied from the library",
    "subscribe": "Subscribed", "subscription_draft": "Subscription update drafted",
    "composite_follow_draft": "Composite redrafted for a sleeve change", "follow_skipped": "Update not drafted",
    "fund_build_joined": "Existing fund model offered", "holdings_monitor": "Fund holdings checked",
    "terms_monitor": "Fund terms changed", "terms_review_done": "Terms change reviewed",
}


class ModelError(ValueError):
    """A request the caller can fix (422)."""

    def __init__(self, message: str, messages: list[dict] | None = None):
        super().__init__(message)
        self.messages = messages or []


class ModelNotFound(LookupError):
    """Not visible to this org (404) — never says whether it exists elsewhere."""


class ModelConflict(RuntimeError):
    """The target is not in a state that takes this action (409)."""


class ModelPermissionError(PermissionError):
    """403 naming what is missing."""


class MakerCheckerViolation(PermissionError):
    """The approver is a maker of the version (403)."""


MAKER_CHECKER_RULE = ("Maker-checker: you uploaded, edited or submitted this version, so a different holder of "
                      f"{APPROVE} must decide it.")


# ── small helpers ───────────────────────────────────────────────────────────
def _dumps(obj) -> str:
    return json.dumps(obj, default=str, sort_keys=True)


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def _uuid(v, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        raise ModelNotFound(f"{what} not found") from None


def _num(v):
    return None if v is None else str(v)


def _iso(v):
    return v.isoformat() if v else None


async def tolerance_pct(conn, org_id) -> Decimal:
    from services.org_settings import get_setting

    try:
        v = await get_setting(conn, uuid.UUID(str(org_id)), U.TOLERANCE_SETTING_KEY)
        d = Decimal(str(v)) if v is not None else U.DEFAULT_TOLERANCE_PCT
        return d if Decimal(0) < d <= Decimal(5) else U.DEFAULT_TOLERANCE_PCT
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the stated default
        return U.DEFAULT_TOLERANCE_PCT


def check_name(name: str, source: str) -> str:
    n = (name or "").strip()
    if not n:
        raise ModelError("a model needs a name")
    if len(n) > 200:
        raise ModelError("a model name is at most 200 characters")
    if source != "fund_replica" and n.lower().startswith(RESERVED_PREFIX):
        raise ModelError("names beginning \"Holdings of\" are reserved for models built from a fund or a 13F filer "
                         "— choose another name")
    return n


async def _archive(conn, table: str, row_id) -> str:
    archived = await conn.fetchval(
        f"""INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {table} t WHERE t.id = $1::uuid AND t.system_to IS NULL
            RETURNING id""", str(row_id))
    if archived is None:
        raise ModelConflict(f"{table} row {row_id} is not live — refusing to edit it")
    return str(archived)


JSONB_COLS = frozenset({"totals", "diff", "tradability", "alternates", "mapping", "messages", "detail"})


async def _update(conn, table: str, row_id, fields: dict) -> dict:
    """System-axis edit: archive the outgoing state, then change the live row."""
    await _archive(conn, table, row_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" + ("::jsonb" if c in JSONB_COLS else "") for i, c in enumerate(cols))
    vals = [(_dumps(fields[c]) if c in JSONB_COLS and fields[c] is not None else fields[c]) for c in cols]
    row = await conn.fetchrow(f"UPDATE {table} SET {sets}, system_from = now() WHERE id = $1::uuid RETURNING *",
                              str(row_id), *vals)
    return dict(row)


# ── the event log ───────────────────────────────────────────────────────────
@dataclass
class EventRef:
    id: str
    action: str
    org_id: str
    payload: dict = field(default_factory=dict)
    actor_id: str | None = None
    event_type: str | None = None
    source_type: str = "model_event"


async def _event(conn, *, org_id, action: str, actor_id, model_id=None, version_id=None, entry_id=None,
                 upload_id=None, detail: dict | None = None) -> EventRef:
    """One model_events row + its audit_log row, inside the caller's transaction."""
    eid = await conn.fetchval(
        f"INSERT INTO {T_EVT} (org_id, model_id, version_id, library_entry_id, upload_id, action, actor_id, detail) "
        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7::uuid, $8::jsonb) RETURNING id",
        str(org_id), str(model_id) if model_id else None, str(version_id) if version_id else None,
        str(entry_id) if entry_id else None, str(upload_id) if upload_id else None, action,
        str(actor_id) if actor_id else None, _dumps(detail or {}))
    payload = {"model_id": str(model_id) if model_id else None, "version_id": str(version_id) if version_id else None,
               "library_entry_id": str(entry_id) if entry_id else None,
               "upload_id": str(upload_id) if upload_id else None, **(detail or {})}
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, 'model_event', $4::uuid, $5::jsonb)",
        str(org_id), str(actor_id) if actor_id else None, f"models.{action}", str(eid), _dumps(payload))
    return EventRef(str(eid), action, str(org_id), payload, str(actor_id) if actor_id else None)


async def publish(pool, refs: list[EventRef]) -> list[dict]:
    """After commit: one domain event per ref (publish_event never raises)."""
    from services.domain_events import publish_event

    out = []
    for r in refs:
        out.append(await publish_event(pool, r.org_id, r.event_type or f"model.{r.action}", r.source_type, r.id,
                                       r.payload, created_by=r.actor_id))
    return out


async def _audit_only(conn, org_id, user_id, action: str, resource_type: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, action, resource_type, str(resource_id), _dumps(payload))


# ── reads ───────────────────────────────────────────────────────────────────
async def load_model(conn, org_id, model_id, *, lock: bool = False) -> dict:
    mid = _uuid(model_id, "model")
    row = await conn.fetchrow(f"SELECT * FROM {T_MODEL} WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
                              f"AND system_to IS NULL {'FOR UPDATE' if lock else ''}", mid, str(org_id))
    if row is None:
        raise ModelNotFound("model not found")
    return dict(row)


async def load_version(conn, org_id, model_id, version_id, *, lock: bool = False) -> dict:
    vid = _uuid(version_id, "model version")
    row = await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1 AND model_id = $2::uuid AND org_id = $3::uuid "
                              f"AND system_to IS NULL {'FOR UPDATE' if lock else ''}", vid, str(model_id), str(org_id))
    if row is None:
        raise ModelNotFound("model version not found")
    return dict(row)


async def live_approved(conn, model_id, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE model_id = $1::uuid AND status = 'approved' "
                              f"AND system_to IS NULL {'FOR UPDATE' if lock else ''}", str(model_id))
    return dict(row) if row else None


async def open_version(conn, model_id, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE model_id = $1::uuid AND status IN ('draft', 'submitted') "
                              f"AND system_to IS NULL {'FOR UPDATE' if lock else ''}", str(model_id))
    return dict(row) if row else None


async def positions_of(conn, version_id) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT p.*, g.name AS security_name
            FROM {T_POS} p LEFT JOIN portfolio.securities_global g ON g.id = p.global_security_id
            WHERE p.version_id = $1::uuid AND p.valid_to IS NULL AND p.system_to IS NULL ORDER BY p.row_num""",
        str(version_id))
    out = []
    for r in rows:
        d = dict(r)
        d["alternates"] = _loads(d["alternates"]) or []
        out.append(d)
    return out


async def sleeves_of(conn, version_id) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT s.*, m.name AS child_name, cv.version_no AS resolved_version_no
            FROM {T_SLV} s LEFT JOIN {T_MODEL} m ON m.id = s.child_model_id AND m.system_to IS NULL
            LEFT JOIN {T_VER} cv ON cv.id = s.resolved_child_version_id
            WHERE s.parent_version_id = $1::uuid AND s.valid_to IS NULL AND s.system_to IS NULL
            ORDER BY m.name""", str(version_id))
    return [dict(r) for r in rows]


def _pos_key(p: dict) -> str:
    if p["resolution_state"] == "cash":
        return "cash"
    if p.get("global_security_id"):
        return str(p["global_security_id"])
    ident = p.get("raw_cusip") or p.get("raw_isin") or p.get("raw_sedol") or p.get("raw_ticker") or p.get("raw_name") \
        or f"row{p['row_num']}"
    return f"unresolved:{str(ident).upper()}"


def _pos_label(p: dict) -> str:
    if p["resolution_state"] == "cash":
        return "Cash"
    return p.get("raw_ticker") or p.get("security_name") or p.get("raw_name") or p.get("raw_cusip") or "?"


async def look_through(conn, version_id, *, _path: tuple = ()) -> dict[str, dict]:
    """Effective positions of a version: its own positions plus, for every
    sleeve, sleeve weight × the child version's effective weights, merged by
    security. Exact Decimal. A cycle is refused (it can only exist if the
    edit-time check was bypassed)."""
    v = await conn.fetchrow(f"SELECT id, model_id FROM {T_VER} WHERE id = $1::uuid", str(version_id))
    if v is None:
        raise ModelConflict(f"model version {version_id} is not readable here")
    if v["model_id"] in _path:
        raise ModelConflict("these sleeves form a cycle — a model cannot contain itself")
    out: dict[str, dict] = {}

    def add(key, weight: Decimal, label, gsid, state):
        cur = out.setdefault(key, {"weight": Decimal(0), "label": label, "global_security_id": gsid,
                                   "resolution_state": state})
        cur["weight"] += weight

    for p in await positions_of(conn, version_id):
        add(_pos_key(p), p["target_weight"], _pos_label(p),
            str(p["global_security_id"]) if p["global_security_id"] else None, p["resolution_state"])
    for s in await sleeves_of(conn, version_id):
        child = await look_through(conn, s["resolved_child_version_id"], _path=_path + (v["model_id"],))
        for k, c in child.items():
            add(k, s["weight"] * c["weight"], c["label"], c["global_security_id"], c["resolution_state"])
    return out


async def _effective_in_memory(conn, positions: list[dict], sleeves: list[dict], model_id) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in positions:
        cur = out.setdefault(_pos_key(p), {"weight": Decimal(0), "label": _pos_label(p),
                                           "global_security_id": str(p["global_security_id"]) if p.get(
                                               "global_security_id") else None,
                                           "resolution_state": p["resolution_state"]})
        cur["weight"] += Decimal(str(p["target_weight"]))
    for s in sleeves:
        child = await look_through(conn, s["resolved_child_version_id"], _path=(uuid.UUID(str(model_id)),))
        for k, c in child.items():
            cur = out.setdefault(k, {"weight": Decimal(0), "label": c["label"],
                                     "global_security_id": c["global_security_id"],
                                     "resolution_state": c["resolution_state"]})
            cur["weight"] += Decimal(str(s["weight"])) * c["weight"]
    return out


def lt_out(lt: dict[str, dict]) -> list[dict]:
    return [{"key": k, "label": v["label"], "global_security_id": v["global_security_id"],
             "resolution_state": v["resolution_state"], "weight": str(v["weight"])}
            for k, v in sorted(lt.items(), key=lambda kv: (-kv[1]["weight"], kv[0]))]


def _totals(positions: list[dict], sleeves: list[dict], effective: dict[str, dict]) -> dict:
    return {
        "positions": len(positions),
        "resolved": sum(1 for p in positions if p["resolution_state"] == "resolved"),
        "unresolved": sum(1 for p in positions if p["resolution_state"] == "unresolved"),
        "cash": sum(1 for p in positions if p["resolution_state"] == "cash"),
        "weight_total": str(sum((Decimal(str(p["target_weight"])) for p in positions), Decimal(0))),
        "sleeves": len(sleeves),
        "sleeve_weight_total": str(sum((Decimal(str(s["weight"])) for s in sleeves), Decimal(0))),
        "effective_total": str(sum((v["weight"] for v in effective.values()), Decimal(0))),
        "effective_unresolved": sum(1 for v in effective.values() if v["resolution_state"] == "unresolved"),
    }


async def _diff_vs(conn, effective: dict[str, dict], base_version_id) -> dict:
    base = await look_through(conn, base_version_id) if base_version_id else {}
    d = U.diff_positions({k: {"weight": v["weight"], "label": v["label"]} for k, v in base.items()},
                         {k: {"weight": v["weight"], "label": v["label"]} for k, v in effective.items()})
    d["base_version_id"] = str(base_version_id) if base_version_id else None
    return d


# ── writing positions / sleeves ─────────────────────────────────────────────
POS_COLS = ("row_num", "global_security_id", "raw_ticker", "raw_cusip", "raw_isin", "raw_sedol", "raw_name",
            "target_weight", "asset_class", "sleeve_label", "drift_min", "drift_max", "alternates", "restrictions",
            "notes", "resolution_state", "resolution_note")


async def _insert_positions(conn, org_id, version_id, positions: list[dict], actor_id) -> None:
    if not positions:
        return
    cols = {c: [p.get(c) for p in positions] for c in POS_COLS}
    cols["alternates"] = [_dumps(a or []) for a in cols["alternates"]]
    cols["global_security_id"] = [str(g) if g else None for g in cols["global_security_id"]]
    cols["target_weight"] = [U.q(Decimal(str(w))) for w in cols["target_weight"]]
    for b in ("drift_min", "drift_max"):
        cols[b] = [U.q(Decimal(str(x))) if x is not None else None for x in cols[b]]
    await conn.execute(
        f"""INSERT INTO {T_POS} (org_id, version_id, created_by, row_num, global_security_id, raw_ticker, raw_cusip,
                raw_isin, raw_sedol, raw_name, target_weight, asset_class, sleeve_label, drift_min, drift_max, alternates,
                restrictions, notes, resolution_state, resolution_note)
            SELECT $1::uuid, $2::uuid, $3::uuid, r, g, t, c, i, s, n, w, ac, sl, dmin, dmax, alt::jsonb, rs, nt, st, rn
            FROM unnest($4::int[], $5::uuid[], $6::text[], $7::text[], $8::text[], $9::text[], $10::text[],
                        $11::numeric[], $12::text[], $13::text[], $14::numeric[], $15::numeric[], $16::text[],
                        $17::text[], $18::text[], $19::text[], $20::text[])
                 AS x(r, g, t, c, i, s, n, w, ac, sl, dmin, dmax, alt, rs, nt, st, rn)""",
        str(org_id), str(version_id), str(actor_id) if actor_id else None,
        *[cols[c] for c in POS_COLS])


async def _insert_sleeves(conn, org_id, version_id, sleeves: list[dict], actor_id) -> None:
    for s in sleeves:
        await conn.execute(
            f"INSERT INTO {T_SLV} (org_id, parent_version_id, child_model_id, weight, follow, pinned_version_id, "
            "resolved_child_version_id, created_by) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::uuid, $7::uuid, "
            "$8::uuid)", str(org_id), str(version_id), str(s["child_model_id"]), U.q(Decimal(str(s["weight"]))),
            s["follow"], str(s["pinned_version_id"]) if s.get("pinned_version_id") else None,
            str(s["resolved_child_version_id"]), str(actor_id) if actor_id else None)


async def _next_version_no(conn, model_id) -> int:
    return int(await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) + 1 FROM {T_VER} WHERE model_id = $1::uuid "
                                   "AND system_to IS NULL", str(model_id)))


async def _create_version(conn, *, org_id, model: dict, origin: str, maker_ids: list, created_by,
                          positions: list[dict], sleeves: list[dict] | None = None, origin_ref=None, upload_id=None,
                          notes: str | None = None, tradability: list | None = None) -> dict:
    """Insert a DRAFT version with its positions and sleeves, totals and the
    diff against the live approved version computed first (one insert)."""
    sleeves = sleeves or []
    if await open_version(conn, model["id"], lock=True):
        raise ModelConflict(f"{model['name']} already has an open draft — finish or abandon it first")
    approved = await live_approved(conn, model["id"])
    effective = await _effective_in_memory(conn, positions, sleeves, model["id"])
    diff = await _diff_vs(conn, effective, approved["id"] if approved else None)
    cash = [p for p in positions if p["resolution_state"] == "cash"]
    vid = await conn.fetchval(
        f"""INSERT INTO {T_VER} (org_id, model_id, version_no, status, origin, origin_ref, maker_ids, upload_id,
                parent_version_id, notes, cash_target, totals, diff, tradability, created_by)
            VALUES ($1::uuid, $2::uuid, $3, 'draft', $4, $5::uuid, $6::uuid[], $7::uuid, $8::uuid, $9, $10, $11::jsonb,
                    $12::jsonb, $13::jsonb, $14::uuid) RETURNING id""",
        str(org_id), str(model["id"]), await _next_version_no(conn, model["id"]), origin,
        str(origin_ref) if origin_ref else None, [uuid.UUID(str(m)) for m in maker_ids],
        str(upload_id) if upload_id else None, str(approved["id"]) if approved else None, notes,
        U.q(Decimal(str(cash[0]["target_weight"]))) if cash else None,
        _dumps(_totals(positions, sleeves, effective)), _dumps(diff), _dumps(tradability or []),
        str(created_by) if created_by else None)
    await _insert_positions(conn, org_id, vid, positions, created_by)
    await _insert_sleeves(conn, org_id, vid, sleeves, created_by)
    return dict(await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE id = $1", vid))


async def _refresh_version(conn, version: dict, *, makers_add=None) -> dict:
    """Recompute totals + diff of a draft after an edit (system-axis archive)."""
    positions = await positions_of(conn, version["id"])
    sleeves = await sleeves_of(conn, version["id"])
    effective = await look_through(conn, version["id"])
    diff = await _diff_vs(conn, effective, version["parent_version_id"])
    makers = list(version["maker_ids"] or [])
    if makers_add is not None and uuid.UUID(str(makers_add)) not in makers:
        makers.append(uuid.UUID(str(makers_add)))
    cash = [p for p in positions if p["resolution_state"] == "cash"]
    return await _update(conn, T_VER, version["id"], {
        "totals": _totals(positions, sleeves, effective), "diff": diff, "maker_ids": makers,
        "cash_target": cash[0]["target_weight"] if cash else None})


# ── models ──────────────────────────────────────────────────────────────────
async def create_model(conn, *, org_id, actor_id, name: str, source: str, description: str | None = None,
                       base_currency: str = "USD", provider_name: str | None = None,
                       library_entry_id=None) -> tuple[dict, EventRef]:
    if source not in SOURCE_LABELS:
        raise ModelError(f"unknown model source {source!r}")
    if source == "fund_replica":
        raise ModelError("fund-replica models are created from the Replication registry (rep4), not here")
    n = check_name(name, source)
    cur = (base_currency or "USD").strip().upper()
    if len(cur) != 3 or not cur.isalpha():
        raise ModelError("base currency is a three-letter code, e.g. USD")
    dup = await conn.fetchval(f"SELECT 1 FROM {T_MODEL} WHERE org_id = $1::uuid AND lower(btrim(name)) = lower($2) "
                              "AND valid_to IS NULL AND system_to IS NULL", str(org_id), n)
    if dup:
        raise ModelConflict(f"a model named {n!r} already exists")
    mid = await conn.fetchval(
        f"INSERT INTO {T_MODEL} (org_id, name, description, source, status, base_currency, provider_name, "
        "library_entry_id, created_by) VALUES ($1::uuid, $2, $3, $4, 'draft', $5, $6, $7::uuid, $8::uuid) RETURNING id",
        str(org_id), n, (description or "").strip() or None, source, cur, (provider_name or "").strip() or None,
        str(library_entry_id) if library_entry_id else None, str(actor_id) if actor_id else None)
    model = dict(await conn.fetchrow(f"SELECT * FROM {T_MODEL} WHERE id = $1", mid))
    ref = await _event(conn, org_id=org_id, action="create_model", actor_id=actor_id, model_id=mid,
                       detail={"name": n, "source": source})
    return model, ref


async def create_fund_model(conn, *, org_id, actor_id, name: str, replica_security_id, disclosure: str,
                            description: str | None = None, base_currency: str = "USD") -> tuple[dict, EventRef]:
    """rep4: the ONLY way a fund_replica model is created — from the Replication
    registry (services.replica_models.build_model), inside the caller's
    transaction. The name is the rep1 "Holdings of <TICKER>" display name (the
    reserved prefix is allowed for this source alone) and the rep1 disclosure
    line is stored on the model."""
    n = check_name(name, "fund_replica")
    if not n.lower().startswith(RESERVED_PREFIX):
        raise ModelError('a fund-built model is named "Holdings of <TICKER>"')
    if not (disclosure or "").strip():
        raise ModelError("a fund-built model carries the fund's disclosure line")
    dup = await conn.fetchval(f"SELECT 1 FROM {T_MODEL} WHERE org_id = $1::uuid AND lower(btrim(name)) = lower($2) "
                              "AND valid_to IS NULL AND system_to IS NULL", str(org_id), n)
    if dup:
        raise ModelConflict(f"a model named {n!r} already exists")
    mid = await conn.fetchval(
        f"INSERT INTO {T_MODEL} (org_id, name, description, source, status, base_currency, replica_security_id, "
        "disclosure, created_by) VALUES ($1::uuid, $2, $3, 'fund_replica', 'draft', $4, $5::uuid, $6, $7::uuid) "
        "RETURNING id",
        str(org_id), n, (description or "").strip() or None, (base_currency or "USD").strip().upper(),
        str(replica_security_id), disclosure.strip(), str(actor_id))
    model = dict(await conn.fetchrow(f"SELECT * FROM {T_MODEL} WHERE id = $1", mid))
    ref = await _event(conn, org_id=org_id, action="create_model", actor_id=actor_id, model_id=mid,
                       detail={"name": n, "source": "fund_replica", "replica_security_id": str(replica_security_id)})
    return model, ref


async def update_model(pool, *, org_id, actor_id, model_id, changes: dict) -> dict:
    allowed = {"name", "description", "base_currency", "default_fee_schedule_id", "status"}
    bad = set(changes) - allowed
    if bad:
        raise ModelError(f"cannot change {sorted(bad)}")
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        model = await load_model(conn, org_id, model_id, lock=True)
        fields: dict[str, Any] = {}
        if "name" in changes:
            fields["name"] = check_name(changes["name"], model["source"])
            dup = await conn.fetchval(f"SELECT 1 FROM {T_MODEL} WHERE org_id = $1::uuid AND lower(btrim(name)) = "
                                      "lower($2) AND id <> $3 AND valid_to IS NULL AND system_to IS NULL",
                                      str(org_id), fields["name"], model["id"])
            if dup:
                raise ModelConflict(f"a model named {fields['name']!r} already exists")
        if "description" in changes:
            fields["description"] = (changes["description"] or "").strip() or None
        if "base_currency" in changes:
            cur = (changes["base_currency"] or "").strip().upper()
            if len(cur) != 3 or not cur.isalpha():
                raise ModelError("base currency is a three-letter code, e.g. USD")
            fields["base_currency"] = cur
        if "default_fee_schedule_id" in changes:
            fs = changes["default_fee_schedule_id"]
            if fs is None:
                fields["default_fee_schedule_id"] = None
            else:
                row = await conn.fetchrow("SELECT id, status FROM public.fee_schedules WHERE id = $1::uuid AND "
                                          "org_id = $2::uuid", str(_uuid(fs, "fee schedule")), str(org_id))
                if row is None:
                    raise ModelNotFound("fee schedule not found")
                if row["status"] != "APPROVED":
                    raise ModelError("only an APPROVED fee schedule can be a model's default (that exact version is "
                                     "referenced; it is never mutated)")
                fields["default_fee_schedule_id"] = row["id"]
        if "status" in changes:
            if changes["status"] != "retired":
                raise ModelError("a model's status changes only by retiring it (approval makes it active)")
            if await open_version(conn, model["id"]):
                raise ModelConflict("abandon the open draft before retiring the model")
            fields["status"] = "retired"
        if not fields:
            return model
        new = await _update(conn, T_MODEL, model["id"], fields)
        ref = await _event(conn, org_id=org_id, action="edit_model", actor_id=actor_id, model_id=model["id"],
                           detail={"fields": sorted(fields), "before": {k: model[k] for k in fields},
                                   "after": {k: new[k] for k in fields}})
    await publish(pool, [ref])
    return new


# ── sleeves ─────────────────────────────────────────────────────────────────
async def _children_of_model(conn, model_id) -> set:
    rows = await conn.fetch(
        f"""SELECT DISTINCT s.child_model_id FROM {T_SLV} s JOIN {T_VER} v ON v.id = s.parent_version_id
            WHERE v.model_id = $1::uuid AND v.system_to IS NULL AND v.status IN ('draft', 'submitted', 'approved')
              AND s.valid_to IS NULL AND s.system_to IS NULL""", str(model_id))
    return {r["child_model_id"] for r in rows}


async def would_cycle(conn, parent_model_id, child_model_id) -> bool:
    """True when ``parent`` is reachable from ``child`` through the sleeves of
    any live (draft, submitted or approved) version — or child IS parent."""
    target = uuid.UUID(str(parent_model_id))
    start = uuid.UUID(str(child_model_id))
    if start == target:
        return True
    seen, frontier = {start}, [start]
    while frontier:
        nxt = []
        for m in frontier:
            for c in await _children_of_model(conn, m):
                if c == target:
                    return True
                if c not in seen:
                    seen.add(c)
                    nxt.append(c)
        frontier = nxt
    return False


async def _resolve_sleeves(conn, org_id, parent_model: dict, sleeves: list[dict], tol: Decimal) -> list[dict]:
    if parent_model["source"] != "composite":
        raise ModelError("only a composite model has sleeves")
    if not sleeves:
        raise ModelError("a composite needs at least one sleeve")
    out, seen = [], set()
    total = Decimal(0)
    for s in sleeves:
        child = await load_model(conn, org_id, s["child_model_id"])
        if child["id"] in seen:
            raise ModelError(f"{child['name']} appears twice")
        seen.add(child["id"])
        try:
            w = U.to_decimal(s.get("weight_pct"))
        except ValueError as exc:
            raise ModelError(f"{child['name']}: weight {exc}") from None
        if w is None or w <= 0 or w > 100:
            raise ModelError(f"{child['name']}: a sleeve weight is above 0% and at most 100%")
        w = w / 100
        total += w
        follow = s.get("follow") or "live"
        if follow not in FOLLOW_LABELS:
            raise ModelError(f"follow is one of {list(FOLLOW_LABELS)}")
        if await would_cycle(conn, parent_model["id"], child["id"]):
            raise ModelConflict(f"adding {child['name']} would make a cycle — {parent_model['name']} would contain "
                                "itself")
        if follow == "live":
            live = await live_approved(conn, child["id"])
            if live is None:
                raise ModelConflict(f"{child['name']} has no approved version to follow yet")
            resolved, pinned = live["id"], None
        else:
            pv = await conn.fetchrow(f"SELECT id, status FROM {T_VER} WHERE id = $1::uuid AND model_id = $2::uuid "
                                     "AND system_to IS NULL", str(_uuid(s.get("pinned_version_id"), "pinned version")),
                                     child["id"])
            if pv is None or pv["status"] not in ("approved", "superseded"):
                raise ModelError(f"{child['name']}: pin an approved (or superseded) version of that model")
            resolved = pinned = pv["id"]
        out.append({"child_model_id": child["id"], "weight": w, "follow": follow, "pinned_version_id": pinned,
                    "resolved_child_version_id": resolved, "child_name": child["name"]})
    if abs(total - 1) > tol / 100:
        raise ModelError(f"sleeve weights sum to {U.pct_text(total)}, not 100% (±{tol}%)")
    return out


async def create_composite(pool, *, org_id, actor_id, name: str, description: str | None, sleeves: list[dict],
                           base_currency: str = "USD") -> tuple[dict, dict]:
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        tol = await tolerance_pct(conn, org_id)
        model, r1 = await create_model(conn, org_id=org_id, actor_id=actor_id, name=name, source="composite",
                                       description=description, base_currency=base_currency)
        resolved = await _resolve_sleeves(conn, org_id, model, sleeves, tol)
        version = await _create_version(conn, org_id=org_id, model=model, origin="manual", maker_ids=[actor_id],
                                        created_by=actor_id, positions=[], sleeves=resolved,
                                        tradability=await U.tradability(conn, org_id))
        r2 = await _event(conn, org_id=org_id, action="create_draft", actor_id=actor_id, model_id=model["id"],
                          version_id=version["id"], detail={"origin": "manual", "sleeves": len(resolved)})
    await publish(pool, [r1, r2])
    return model, version


async def set_sleeves(pool, *, org_id, actor_id, model_id, version_id, sleeves: list[dict]) -> dict:
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        tol = await tolerance_pct(conn, org_id)
        model = await load_model(conn, org_id, model_id)
        version = await load_version(conn, org_id, model_id, version_id, lock=True)
        _need_draft(version)
        before = await sleeves_of(conn, version["id"])
        resolved = await _resolve_sleeves(conn, org_id, model, sleeves, tol)
        await conn.execute(f"UPDATE {T_SLV} SET valid_to = now() WHERE parent_version_id = $1 AND valid_to IS NULL "
                           "AND system_to IS NULL", version["id"])
        await _insert_sleeves(conn, org_id, version["id"], resolved, actor_id)
        new = await _refresh_version(conn, version, makers_add=actor_id)
        ref = await _event(conn, org_id=org_id, action="edit_sleeves", actor_id=actor_id, model_id=model["id"],
                           version_id=version["id"],
                           detail={"before": [{"child_model_id": str(s["child_model_id"]), "weight": str(s["weight"]),
                                               "follow": s["follow"]} for s in before],
                                   "after": [{"child_model_id": str(s["child_model_id"]), "weight": str(s["weight"]),
                                              "follow": s["follow"]} for s in resolved]})
    await publish(pool, [ref])
    return new


# ── drafts and edits ────────────────────────────────────────────────────────
def _need_draft(version: dict) -> None:
    if version["status"] == "approved":
        raise ModelConflict(f"version {version['version_no']} is approved — an approved version is never changed; "
                            "start a new draft")
    if version["status"] != "draft":
        raise ModelConflict(f"version {version['version_no']} is {VERSION_STATUS_LABELS[version['status']].lower()} — "
                            "only a draft is edited")


def _copy_position(p: dict) -> dict:
    return {c: p.get(c) for c in POS_COLS}


async def new_draft(pool, *, org_id, actor_id, model_id) -> dict:
    """A new draft from the live approved version (positions copied; a sleeve
    that follows live is re-resolved to its child's current approved version)."""
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        model = await load_model(conn, org_id, model_id)
        live = await live_approved(conn, model["id"])
        if live is None:
            raise ModelConflict("there is no approved version to start from — upload a file or edit the open draft")
        positions = [_copy_position(p) for p in await positions_of(conn, live["id"])]
        sleeves = await _carry_sleeves(conn, live["id"])
        version = await _create_version(conn, org_id=org_id, model=model, origin="revision", maker_ids=[actor_id],
                                        created_by=actor_id, positions=positions, sleeves=sleeves,
                                        origin_ref=live["id"], tradability=await U.tradability(conn, org_id))
        ref = await _event(conn, org_id=org_id, action="create_draft", actor_id=actor_id, model_id=model["id"],
                           version_id=version["id"], detail={"origin": "revision", "from_version_id": str(live["id"])})
    await publish(pool, [ref])
    return version


async def _carry_sleeves(conn, version_id) -> list[dict]:
    out = []
    for s in await sleeves_of(conn, version_id):
        resolved = s["resolved_child_version_id"]
        if s["follow"] == "live":
            live = await live_approved(conn, s["child_model_id"])
            if live is not None:
                resolved = live["id"]
        out.append({"child_model_id": s["child_model_id"], "weight": s["weight"], "follow": s["follow"],
                    "pinned_version_id": s["pinned_version_id"], "resolved_child_version_id": resolved})
    return out


EDIT_TEXT_FIELDS = ("raw_ticker", "raw_cusip", "raw_isin", "raw_sedol", "raw_name", "asset_class", "sleeve_label",
                    "restrictions", "notes")


def _apply_change(base: dict | None, ch: dict, row_num: int) -> dict:
    p = dict(base) if base else {c: None for c in POS_COLS}
    p["row_num"] = row_num
    for f in EDIT_TEXT_FIELDS:
        if f in ch:
            p[f] = (str(ch[f]).strip() or None) if ch[f] is not None else None
    for src, dst in (("weight_pct", "target_weight"), ("drift_min_pct", "drift_min"), ("drift_max_pct", "drift_max")):
        if src in ch:
            try:
                d = U.to_decimal(ch[src])
            except ValueError as exc:
                raise ModelError(f"row {row_num}: {src.replace('_pct', '').replace('_', ' ')} {exc}") from None
            p[dst] = (d / 100) if d is not None else None
    if "alternates" in ch:
        alts = ch["alternates"]
        if isinstance(alts, str):
            alts = [a for a in alts.replace(",", ";").split(";")]
        p["alternates"] = [str(a).strip().upper() for a in (alts or []) if str(a).strip()]
    if "is_cash" in ch:
        p["resolution_state"] = "cash" if ch["is_cash"] else "unresolved"
    if p.get("resolution_state") is None:
        p["resolution_state"] = "unresolved"
    if p.get("resolution_state") != "cash" and ("raw_ticker" in ch or "raw_cusip" in ch or "raw_isin" in ch
                                                or "raw_sedol" in ch or "is_cash" in ch):
        p["global_security_id"] = None
    return p


async def edit_positions(pool, *, org_id, actor_id, model_id, version_id, changes: list[dict]) -> tuple[dict, list]:
    """Edit a draft's positions with the upload's own validation. Each change
    is ``{"op": "upsert" | "remove", "row_num"?: int, ...fields}``; weights in
    percent. Changed rows are closed and re-added (valid axis); the editor
    joins maker_ids; the edit is logged with before/after."""
    if not changes:
        raise ModelError("no changes")
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        tol = await tolerance_pct(conn, org_id)
        model = await load_model(conn, org_id, model_id)
        version = await load_version(conn, org_id, model_id, version_id, lock=True)
        _need_draft(version)
        current = {p["row_num"]: p for p in await positions_of(conn, version["id"])}
        new = {k: _copy_position(v) for k, v in current.items()}
        touched: set[int] = set()
        next_row = max(current, default=0) + 1
        for ch in changes:
            op = ch.get("op")
            rn = ch.get("row_num")
            if op == "remove":
                if rn not in new:
                    raise ModelError(f"row {rn} is not in this draft")
                del new[rn]
                touched.add(rn)
            elif op == "upsert":
                if rn is None:
                    rn, next_row = next_row, next_row + 1
                elif rn not in new:
                    raise ModelError(f"row {rn} is not in this draft (leave row_num out to add a row)")
                new[rn] = _apply_change(new.get(rn), ch, rn)
                touched.add(rn)
            else:
                raise ModelError("each change is an upsert or a remove")
        rows, msgs = await U.validate_positions(conn, list(new.values()), tolerance_pct=tol, require_sum=False)
        errors = [m.as_dict() for m in msgs if m.severity == "error"]
        if errors:
            raise ModelError(f"{len(errors)} problem(s) — nothing was changed", errors)
        resolved = {r.row_num: r.as_position() for r in rows}
        for rn in touched:
            if rn in current:
                await conn.execute(f"UPDATE {T_POS} SET valid_to = now() WHERE id = $1", current[rn]["id"])
        await _insert_positions(conn, org_id, version["id"], [resolved[rn] for rn in sorted(touched) if rn in resolved],
                                actor_id)
        out = await _refresh_version(conn, version, makers_add=actor_id)
        total = sum((r.weight for r in rows if r.weight is not None), Decimal(0))
        notes = [m.as_dict() for m in msgs if m.severity != "error"]
        if abs(total - 1) > tol / 100:
            notes.append(U.Message("weight_sum", "warning", f"weights now sum to {U.pct_text(total)} — they must sum "
                                                            f"to 100% (±{tol}%) before the draft is submitted",
                                   None, "weight").as_dict())
        ref = await _event(conn, org_id=org_id, action="edit_positions", actor_id=actor_id, model_id=model["id"],
                           version_id=version["id"],
                           detail={"rows": sorted(touched),
                                   "before": {str(rn): _pos_brief(current[rn]) for rn in sorted(touched) if rn in current},
                                   "after": {str(rn): _pos_brief(resolved[rn]) for rn in sorted(touched) if rn in resolved}})
    await publish(pool, [ref])
    return out, notes


def _pos_brief(p: dict) -> dict:
    return {"identifier": p.get("raw_cusip") or p.get("raw_isin") or p.get("raw_ticker") or p.get("raw_sedol"),
            "weight": str(p.get("target_weight")), "state": p.get("resolution_state"),
            "global_security_id": str(p["global_security_id"]) if p.get("global_security_id") else None}


# ── submit / decide / abandon ───────────────────────────────────────────────
def _title(model: dict, version: dict, review: bool = False) -> str:
    return f"{'Review' if review else 'Approve'} model: {model['name']} v{version['version_no']}"


async def submit(pool, *, org_id, actor_id, model_id, version_id) -> dict:
    """draft → submitted; opens the approval task (maker = the submitter)."""
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        tol = await tolerance_pct(conn, org_id)
        model = await load_model(conn, org_id, model_id)
        version = await load_version(conn, org_id, model_id, version_id)
        _need_draft(version)
        totals = _loads(version["totals"]) or {}
        if not totals.get("positions") and not totals.get("sleeves"):
            raise ModelError("the draft has no positions")
        eff = Decimal(str(totals.get("effective_total") or "0"))
        if abs(eff - 1) > tol / 100:
            raise ModelError(f"the draft's weights sum to {U.pct_text(eff)}, not 100% (±{tol}%) — fix them before "
                             "submitting")
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_APPROVAL, subject_type=TASK_SUBJECT, subject_id=version["id"],
        maker_id=actor["id"], eligible_permission=APPROVE, title=_title(model, version),
        payload={"model_id": str(model["id"]), "version_no": version["version_no"],
                 "positions": totals.get("positions"), "unresolved": totals.get("unresolved")},
        action_path=f"/portfolio/models/{model['id']}")
    try:
        async with pool.acquire() as conn:
            cur = await load_version(conn, org_id, model_id, version_id, lock=True)
            _need_draft(cur)
            makers = list(cur["maker_ids"] or [])
            if actor["id"] not in makers:
                makers.append(actor["id"])
            new = await _update(conn, T_VER, cur["id"], {
                "status": "submitted", "approval_task_id": uuid.UUID(task["id"]), "maker_ids": makers,
                "submitted_by": actor["id"], "submitted_at": datetime.now(timezone.utc)})
            ref = await _event(conn, org_id=org_id, action="submit", actor_id=actor["id"], model_id=model["id"],
                               version_id=cur["id"], detail={"task_id": task["id"], "notified": task.get("notified", [])})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the model version could not be submitted")
        raise
    await publish(pool, [ref])
    return new


async def _submit_system(pool, *, org_id, model: dict, version: dict, why: str,
                         extra_payload: dict | None = None) -> dict:
    """A system-produced draft → submitted, with a review task that has no maker.
    rep4 (additive): ``extra_payload`` rides on the task (e.g. the holdings diff)."""
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_REVIEW, subject_type=TASK_SUBJECT, subject_id=version["id"], maker_id=None,
        eligible_permission=APPROVE, title=_title(model, version, review=True),
        payload={**(extra_payload or {}), "model_id": str(model["id"]), "version_no": version["version_no"],
                 "why": why},
        action_path=f"/portfolio/models/{model['id']}", actor_kind="system")
    try:
        async with pool.acquire() as conn:
            cur = await load_version(conn, org_id, model["id"], version["id"], lock=True)
            new = await _update(conn, T_VER, cur["id"], {
                "status": "submitted", "approval_task_id": uuid.UUID(task["id"]),
                "submitted_at": datetime.now(timezone.utc)})
    except Exception:
        log.exception("could not mark system draft %s submitted", version["id"])
        raise
    return {**new, "task_id": task["id"]}


async def _apply_outcome(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    """Apply a decided approval/review task to its version. Idempotent: a
    version no longer submitted on THIS task is left alone."""
    refs: list[EventRef] = []
    approved = None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {T_VER} WHERE approval_task_id = $1::uuid AND system_to IS NULL "
                                  "AND org_id = $2::uuid FOR UPDATE", task["id"], str(org_id))
        if row is None or row["status"] != "submitted":
            return dict(row) if row else None
        row = dict(row)
        model = await load_model(conn, org_id, row["model_id"], lock=True)
        if outcome == "approve":
            if uuid.UUID(str(actor_id)) in (row["maker_ids"] or []):
                raise MakerCheckerViolation(MAKER_CHECKER_RULE)
            unresolved = await conn.fetchval(f"SELECT count(*) FROM {T_POS} WHERE version_id = $1 AND resolution_state "
                                             "= 'unresolved' AND valid_to IS NULL AND system_to IS NULL", row["id"])
            if unresolved:
                raise ModelConflict(f"{unresolved} position(s) are unresolved — this version cannot be approved")
            prior = await live_approved(conn, model["id"], lock=True)
            if prior is not None:
                await _archive(conn, T_VER, prior["id"])
                await conn.execute(f"UPDATE {T_VER} SET status = 'superseded' WHERE id = $1", prior["id"])
            new = await _update(conn, T_VER, row["id"], {
                "status": "approved", "checker_id": uuid.UUID(str(actor_id)),
                "approved_at": datetime.now(timezone.utc), "decision_note": note})
            if model["status"] == "draft":
                await _update(conn, T_MODEL, model["id"], {"status": "active"})
            refs.append(await _event(conn, org_id=org_id, action="approve", actor_id=actor_id, model_id=model["id"],
                                     version_id=row["id"],
                                     detail={"version_no": row["version_no"], "task_id": task["id"], "note": note,
                                             "superseded_version_id": str(prior["id"]) if prior else None}))
            diff = _loads(new["diff"]) or {}
            refs.append(EventRef(str(row["id"]), "version_approved", str(org_id), {
                "model_id": str(model["id"]), "model_name": model["name"], "version_id": str(row["id"]),
                "version_no": row["version_no"], "superseded_version_id": str(prior["id"]) if prior else None,
                "approved_by": str(actor_id), "diff": diff,
                "note": "rep5 turns this into rebalance PROPOSALS; nothing is traded"},
                str(actor_id), event_type="model.version_approved", source_type=TASK_SUBJECT))
            approved = (model, new)
        else:
            new = await _update(conn, T_VER, row["id"], {
                "status": "draft", "approval_task_id": None, "rejected_by": uuid.UUID(str(actor_id)),
                "rejected_at": datetime.now(timezone.utc), "decision_note": note})
            refs.append(await _event(conn, org_id=org_id, action="reject", actor_id=actor_id, model_id=model["id"],
                                     version_id=row["id"], detail={"version_no": row["version_no"], "task_id": task["id"],
                                                                   "note": note}))
    await publish(pool, refs)
    if approved is not None:
        new = {**new, "follow_up": await after_approval(pool, org_id=org_id, model_id=approved[0]["id"],
                                                         version_id=approved[1]["id"])}
    return new


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply_outcome(pool, task["org_id"], task, outcome, actor_id, note)


HT.register_kind(TASK_APPROVAL, label="Model approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)
HT.register_kind(TASK_REVIEW, label="Model update review", outcomes=("approve", "reject"), maker_checker=False,
                 on_complete=_on_task_complete, system_open=True)


async def sync_task_outcome(pool, *, org_id, model_id, version_id) -> None:
    """Recovery: a version still submitted on a task the engine already decided
    (the subject update failed after the engine committed) takes that outcome."""
    async with pool.acquire() as conn:
        try:
            v = await load_version(conn, org_id, model_id, version_id)
        except ModelNotFound:
            return
        if v["status"] != "submitted" or not v["approval_task_id"]:
            return
        task = await HT.get_task(conn, org_id, v["approval_task_id"])
    if task["status"] == "completed" and task["outcome"] in ("approve", "reject") and task["completed_by"]:
        await _apply_outcome(pool, org_id, task, task["outcome"], task["completed_by"], task["note"])


async def decide(pool, *, org_id, actor_id, model_id, version_id, outcome: str, note: str | None = None) -> dict:
    """Approve / reject through the version's open task. Refusals, each leaving
    the version unchanged: 404 not visible; 409 not submitted (an approved
    version is never changed); 403 a maker (MAKER_CHECKER_RULE — the engine and
    the DB CHECK back it); 409 unresolved positions (approve); 422 a rejection
    without a note."""
    if outcome not in ("approve", "reject"):
        raise ModelError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise ModelError("a rejection needs a note saying why")
    await sync_task_outcome(pool, org_id=org_id, model_id=model_id, version_id=version_id)
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        await load_model(conn, org_id, model_id)
        v = await load_version(conn, org_id, model_id, version_id)
        if v["status"] != "submitted":
            if v["status"] == "approved":
                raise ModelConflict(f"version {v['version_no']} is already approved — an approved version is never "
                                    "changed")
            raise ModelConflict(f"version {v['version_no']} is {VERSION_STATUS_LABELS[v['status']].lower()}, not "
                                "awaiting approval")
        if actor["id"] in (v["maker_ids"] or []):
            raise MakerCheckerViolation(MAKER_CHECKER_RULE)
        if outcome == "approve":
            unresolved = [r for r in await positions_of(conn, v["id"]) if r["resolution_state"] == "unresolved"]
            if unresolved:
                rows = ", ".join(str(r["row_num"]) for r in unresolved[:20])
                raise ModelConflict(f"{len(unresolved)} position(s) are unresolved (rows {rows}) — a version with "
                                    "unresolved rows cannot be approved; reject it back to draft and resolve or "
                                    "remove them")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=v["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise ModelPermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise ModelConflict(str(exc)) from exc
    except HT.HumanTaskError as exc:
        raise ModelError(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await load_version(conn, org_id, model_id, version_id)
    return result


async def abandon(pool, *, org_id, actor_id, model_id, version_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise ModelError("abandoning a draft needs a reason")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        model = await load_model(conn, org_id, model_id)
        v = await load_version(conn, org_id, model_id, version_id)
        if v["status"] not in ("draft", "submitted"):
            raise ModelConflict(f"version {v['version_no']} is {VERSION_STATUS_LABELS[v['status']].lower()} — only an "
                                "open draft can be abandoned")
    if v["status"] == "submitted" and v["approval_task_id"]:
        task = None
        async with pool.acquire() as conn:
            task = await HT.get_task(conn, org_id, v["approval_task_id"])
        if task["status"] == "open":
            await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                                 reason=f"the draft was abandoned: {reason.strip()}")
    async with pool.acquire() as conn:
        cur = await load_version(conn, org_id, model_id, version_id, lock=True)
        if cur["status"] not in ("draft", "submitted"):
            raise ModelConflict("this version is no longer open")
        new = await _update(conn, T_VER, cur["id"], {"status": "abandoned", "abandoned_by": actor["id"],
                                                     "abandoned_at": datetime.now(timezone.utc),
                                                     "abandon_reason": reason.strip()})
        if cur["upload_id"]:
            await conn.execute(
                f"INSERT INTO {T_UPE} (org_id, upload_id, event, model_id, version_id, reason, actor_id) "
                "VALUES ($1::uuid, $2, 'abandoned', $3, $4, $5, $6::uuid)",
                str(org_id), cur["upload_id"], model["id"], cur["id"], reason.strip(), str(actor["id"]))
        ref = await _event(conn, org_id=org_id, action="abandon", actor_id=actor["id"], model_id=model["id"],
                           version_id=cur["id"], upload_id=cur["upload_id"],
                           detail={"version_no": cur["version_no"], "reason": reason.strip()})
    await publish(pool, [ref])
    return new


# ── after an approval: composites following live, library subscribers ──────
async def after_approval(pool, *, org_id, model_id, version_id) -> dict:
    """Drafts (never trades) for every composite in the org that follows this
    model LIVE, and for every subscriber of a library entry built on it. A
    failure here never undoes the approval — it is reported and logged."""
    out = {"composite_drafts": [], "subscription_drafts": [], "skipped": [], "errors": []}
    async with pool.acquire() as conn:
        parents = [r["model_id"] for r in await conn.fetch(
            f"""SELECT DISTINCT v.model_id FROM {T_SLV} s JOIN {T_VER} v ON v.id = s.parent_version_id
                WHERE s.child_model_id = $1::uuid AND s.follow = 'live' AND s.valid_to IS NULL AND s.system_to IS NULL
                  AND v.status = 'approved' AND v.system_to IS NULL AND v.org_id = $2::uuid""",
            str(model_id), str(org_id))]
    for pid in parents:
        try:
            r = await _follow_draft(pool, org_id=org_id, parent_model_id=pid, child_version_id=version_id)
            out["composite_drafts" if r.get("version_id") else "skipped"].append(r)
        except Exception as exc:  # noqa: BLE001
            log.exception("composite follow draft failed for %s", pid)
            out["errors"].append({"model_id": str(pid), "error": str(exc)})
    subs = await _subscriptions_for(pool, model_id)
    from services.database import reset_rls_context, set_rls_context

    for s in subs:
        tokens = set_rls_context(str(s["org_id"]), False)
        try:
            r = await _subscription_draft(pool, sub=s, source_version_id=version_id)
            out["subscription_drafts" if r.get("version_id") else "skipped"].append(r)
        except Exception as exc:  # noqa: BLE001
            log.exception("subscription draft failed for %s", s["id"])
            out["errors"].append({"subscription_id": str(s["id"]), "org_id": str(s["org_id"]), "error": str(exc)})
        finally:
            reset_rls_context(tokens)
    return out


async def _subscriptions_for(pool, model_id) -> list[dict]:
    """Active subscriptions (in ANY org) to a published entry built on this
    model. Cross-org by nature: read with the platform scope SET LOCAL for this
    one statement's transaction (services.database.platform_scope's rule)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            rows = await conn.fetch(
                f"""SELECT s.id, s.org_id, s.model_id, s.library_entry_id FROM {T_SUB} s
                    JOIN {T_LIB} e ON e.id = s.library_entry_id
                    WHERE e.model_id = $1::uuid AND e.status = 'published' AND e.valid_to IS NULL AND e.system_to IS NULL
                      AND s.status = 'active' AND s.system_to IS NULL ORDER BY s.org_id""", str(model_id))
            await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
    return [dict(r) for r in rows]


async def _follow_draft(pool, *, org_id, parent_model_id, child_version_id) -> dict:
    async with pool.acquire() as conn:
        model = await load_model(conn, org_id, parent_model_id)
        if await open_version(conn, model["id"]):
            ref = await _event(conn, org_id=org_id, action="follow_skipped", actor_id=None, model_id=model["id"],
                               detail={"reason": "the composite already has an open draft",
                                       "child_version_id": str(child_version_id)})
            skipped = {"model_id": str(model["id"]), "reason": "open draft exists"}
            version = None
        else:
            live = await live_approved(conn, model["id"])
            positions = [_copy_position(p) for p in await positions_of(conn, live["id"])]
            sleeves = await _carry_sleeves(conn, live["id"])
            version = await _create_version(conn, org_id=org_id, model=model, origin="composite_follow", maker_ids=[],
                                            created_by=None, positions=positions, sleeves=sleeves,
                                            origin_ref=child_version_id, tradability=await U.tradability(conn, org_id))
            ref = await _event(conn, org_id=org_id, action="composite_follow_draft", actor_id=None,
                               model_id=model["id"], version_id=version["id"],
                               detail={"child_version_id": str(child_version_id)})
    await publish(pool, [ref])
    if version is None:
        return skipped
    sub = await _submit_system(pool, org_id=org_id, model=model, version=version,
                               why="a sleeve's model has a new approved version")
    return {"model_id": str(model["id"]), "version_id": str(version["id"]), "task_id": sub["task_id"]}


async def _source_positions(conn, source_version_id) -> list[dict]:
    """A copy of a version's content: its positions as they are, or — for a
    composite — its look-through flattened into positions."""
    sleeves = await sleeves_of(conn, source_version_id)
    if not sleeves:
        return [_copy_position(p) for p in await positions_of(conn, source_version_id)]
    lt = await look_through(conn, source_version_id)
    out = []
    for i, (k, v) in enumerate(sorted(lt.items(), key=lambda kv: (-kv[1]["weight"], kv[0])), start=1):
        out.append({c: None for c in POS_COLS} | {
            "row_num": i, "global_security_id": v["global_security_id"], "raw_name": v["label"],
            "target_weight": U.q(v["weight"]), "alternates": [], "resolution_state": v["resolution_state"],
            "raw_ticker": k.split(":", 1)[1] if k.startswith("unresolved:") else None})
    return out




async def _subscription_draft(pool, *, sub: dict, source_version_id) -> dict:
    """Runs in the SUBSCRIBER's org context (after_approval sets it). The
    source content is readable there through the platform carve-out (or, for
    an org-shelf entry, because it is the same org)."""
    org_id = str(sub["org_id"])
    version = None
    async with pool.acquire() as conn:
        model = await load_model(conn, org_id, sub["model_id"])
        if await open_version(conn, model["id"]):
            ref = await _event(conn, org_id=org_id, action="follow_skipped", actor_id=None, model_id=model["id"],
                               entry_id=sub["library_entry_id"],
                               detail={"reason": "the subscribed model already has an open draft",
                                       "source_version_id": str(source_version_id)})
        else:
            positions = await _source_positions(conn, source_version_id)
            version = await _create_version(conn, org_id=org_id, model=model, origin="subscription", maker_ids=[],
                                            created_by=None, positions=positions, origin_ref=source_version_id,
                                            tradability=await U.tradability(conn, org_id))
            await _update(conn, T_SUB, sub["id"], {"last_applied_version_id": uuid.UUID(str(source_version_id))})
            ref = await _event(conn, org_id=org_id, action="subscription_draft", actor_id=None, model_id=model["id"],
                               version_id=version["id"], entry_id=sub["library_entry_id"],
                               detail={"source_version_id": str(source_version_id)})
    await publish(pool, [ref])
    if version is None:
        return {"org_id": org_id, "model_id": str(model["id"]), "reason": "open draft exists"}
    s = await _submit_system(pool, org_id=org_id, model=model, version=version,
                             why="the library entry this model subscribes to has a new approved version")
    return {"org_id": org_id, "model_id": str(model["id"]), "version_id": str(version["id"]), "task_id": s["task_id"]}


# ── library ─────────────────────────────────────────────────────────────────
async def _entry(conn, org_id, entry_id, *, lock: bool = False, published_only: bool = True) -> dict:
    eid = _uuid(entry_id, "library entry")
    row = await conn.fetchrow(f"SELECT * FROM {T_LIB} WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL "
                              f"{'FOR UPDATE' if lock else ''}", eid)
    if row is None or (row["shelf"] == "org" and str(row["org_id"]) != str(org_id)):
        raise ModelNotFound("library entry not found")
    if published_only and row["status"] != "published":
        raise ModelConflict(f"this library entry is {row['status']}")
    return dict(row)


async def has_agreement(conn, org_id, entry_id) -> bool:
    return bool(await conn.fetchval(f"SELECT 1 FROM {T_EVT} WHERE org_id = $1::uuid AND library_entry_id = $2::uuid "
                                    "AND action = 'agreement_confirmed' LIMIT 1", str(org_id), str(entry_id)))


async def list_library(conn, org_id) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT e.*, portfolio.model_library_usage(e.id) AS usage_count,
                   v.version_no AS live_version_no, v.approved_at AS last_updated, v.totals AS live_totals
            FROM {T_LIB} e
            LEFT JOIN {T_VER} v ON v.model_id = e.model_id AND v.status = 'approved' AND v.system_to IS NULL
            WHERE e.valid_to IS NULL AND e.system_to IS NULL AND e.status = 'published'
              AND (e.shelf = 'platform' OR e.org_id = $1::uuid)
            ORDER BY e.shelf DESC, e.title""", str(org_id))
    mine = {}
    for r in await conn.fetch(f"SELECT id, name, source, library_entry_id FROM {T_MODEL} WHERE org_id = $1::uuid "
                              "AND library_entry_id IS NOT NULL AND valid_to IS NULL AND system_to IS NULL", str(org_id)):
        mine.setdefault(str(r["library_entry_id"]), []).append({"id": str(r["id"]), "name": r["name"],
                                                                "source": r["source"]})
    agreed = {str(r["library_entry_id"]) for r in await conn.fetch(
        f"SELECT DISTINCT library_entry_id FROM {T_EVT} WHERE org_id = $1::uuid AND action = 'agreement_confirmed'",
        str(org_id))}
    out = []
    for r in rows:
        totals = _loads(r["live_totals"]) or {}
        out.append({
            "id": str(r["id"]), "shelf": r["shelf"], "title": r["title"], "provider": r["provider"],
            "description": r["description"], "update_cadence": r["update_cadence"],
            "licensing_note": r["licensing_note"], "requires_agreement": r["requires_agreement"],
            "status": r["status"], "published_at": _iso(r["published_at"]), "last_updated": _iso(r["last_updated"]),
            "live_version_no": r["live_version_no"], "positions": totals.get("positions"),
            "usage_count": int(r["usage_count"] or 0), "agreement_on_file": str(r["id"]) in agreed,
            "loaded_as": mine.get(str(r["id"]), []), "model_id": str(r["model_id"]) if r["shelf"] == "org" else None,
        })
    return out


async def publish_entry(pool, *, org_id, actor_id, is_super_admin: bool, is_org_admin: bool, model_id, shelf: str,
                        title: str, provider: str, description: str | None, update_cadence: str,
                        licensing_note: str | None, requires_agreement: bool) -> dict:
    if shelf not in SHELF_LABELS:
        raise ModelError(f"shelf is one of {list(SHELF_LABELS)}")
    if update_cadence not in CADENCE_LABELS:
        raise ModelError(f"update cadence is one of {list(CADENCE_LABELS)}")
    if not (title or "").strip() or not (provider or "").strip():
        raise ModelError("a library entry needs a title and a provider")
    if shelf == "platform":
        if not is_super_admin:
            raise ModelPermissionError("Super Admin required: only Hollisworks publishes to the platform shelf")
        model_org = PLATFORM_ORG_ID
    else:
        if not (is_super_admin or is_org_admin):
            raise ModelPermissionError(f"Permission required: {ORG_ADMIN_PERMISSION} (an org admin publishes to the "
                                       "org shelf)")
        model_org = str(org_id)
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, model_org if shelf == "platform" else org_id)
        model = await load_model(conn, model_org, model_id)
        if model["source"] == "library_subscription" or model["source"] == "library_copy":
            raise ModelError("a model loaded from the library cannot be republished")
        if await live_approved(conn, model["id"]) is None:
            raise ModelConflict(f"{model['name']} has no approved version to publish")
        if await conn.fetchval(f"SELECT 1 FROM {T_LIB} WHERE model_id = $1 AND status = 'published' AND "
                               "system_to IS NULL", model["id"]):
            raise ModelConflict(f"{model['name']} is already published")
        eid = await conn.fetchval(
            f"""INSERT INTO {T_LIB} (shelf, owner_scope, org_id, model_id, title, provider, description, update_cadence,
                    licensing_note, requires_agreement, published_by)
                VALUES ($1, $1, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10::uuid) RETURNING id""",
            shelf, None if shelf == "platform" else str(org_id), model["id"], title.strip(), provider.strip(),
            (description or "").strip() or None, update_cadence, (licensing_note or "").strip() or None,
            bool(requires_agreement), str(actor_id))
        ref = await _event(conn, org_id=model_org, action="publish", actor_id=actor_id, model_id=model["id"],
                           entry_id=eid, detail={"shelf": shelf, "title": title.strip()})
        entry = dict(await conn.fetchrow(f"SELECT * FROM {T_LIB} WHERE id = $1", eid))
    await publish(pool, [ref])
    return entry


async def change_entry_status(pool, *, org_id, actor_id, is_super_admin: bool, is_org_admin: bool, entry_id,
                              status: str, reason: str) -> dict:
    if status not in ("unpublished", "retired"):
        raise ModelError("an entry is unpublished or retired")
    if not (reason or "").strip():
        raise ModelError("a reason is required")
    async with pool.acquire() as conn:
        # read WITHOUT a lock first: under RLS, SELECT ... FOR UPDATE also applies the UPDATE policy, so a
        # platform entry an org can see but not write would vanish and surface as a 404 instead of the 403
        entry = await _entry(conn, org_id, entry_id, published_only=False)
        if entry["shelf"] == "platform" and not is_super_admin:
            raise ModelPermissionError("Super Admin required: platform entries are written only by Hollisworks")
        if entry["shelf"] == "org" and not (is_super_admin or is_org_admin):
            raise ModelPermissionError(f"Permission required: {ORG_ADMIN_PERMISSION}")
        entry = await _entry(conn, org_id, entry_id, lock=True, published_only=False)
        if entry["status"] == "retired":
            raise ModelConflict("this library entry is retired")
        new = await _update(conn, T_LIB, entry["id"], {"status": status, "status_changed_by": uuid.UUID(str(actor_id)),
                                                       "status_changed_at": datetime.now(timezone.utc),
                                                       "status_reason": reason.strip()})
        ref = await _event(conn, org_id=PLATFORM_ORG_ID if entry["shelf"] == "platform" else org_id,
                           action="unpublish" if status == "unpublished" else "retire_entry", actor_id=actor_id,
                           model_id=entry["model_id"], entry_id=entry["id"], detail={"reason": reason.strip()})
    await publish(pool, [ref])
    return new


async def confirm_agreement(pool, *, org_id, actor_id, is_super_admin: bool, is_org_admin: bool, entry_id,
                            note: str | None) -> dict:
    if not (is_super_admin or is_org_admin):
        raise ModelPermissionError(f"Permission required: {ORG_ADMIN_PERMISSION} (an org admin confirms the "
                                   "agreement is on file)")
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        entry = await _entry(conn, org_id, entry_id)
        if not entry["requires_agreement"]:
            raise ModelConflict("this library entry does not require an agreement")
        ref = await _event(conn, org_id=org_id, action="agreement_confirmed", actor_id=actor_id,
                           model_id=None, entry_id=entry["id"],
                           detail={"note": (note or "").strip() or None, "title": entry["title"]})
    await publish(pool, [ref])
    return {"library_entry_id": str(entry["id"]), "event_id": ref.id, "agreement_on_file": True}


async def _load_from_library(pool, *, org_id, actor_id, entry_id, name: str | None, subscribe: bool) -> dict:
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        entry = await _entry(conn, org_id, entry_id)
        if entry["requires_agreement"] and not await has_agreement(conn, org_id, entry["id"]):
            raise ModelConflict("this library entry requires an agreement on file — an org admin must confirm it "
                                "before the model can be copied or subscribed to")
        source = await live_approved(conn, entry["model_id"])
        if source is None:
            raise ModelConflict("the library entry has no approved version to load")
        positions = await _source_positions(conn, source["id"])
        src = "library_subscription" if subscribe else "library_copy"
        model, r1 = await create_model(conn, org_id=org_id, actor_id=actor_id, name=name or entry["title"],
                                       source=src, description=entry["description"],
                                       provider_name=entry["provider"], library_entry_id=entry["id"])
        trad = await U.tradability(conn, org_id)
        if subscribe:
            version = await _create_version(conn, org_id=org_id, model=model, origin="subscription", maker_ids=[],
                                            created_by=None, positions=positions, origin_ref=source["id"],
                                            tradability=trad)
            await conn.execute(f"INSERT INTO {T_SUB} (org_id, model_id, library_entry_id, last_applied_version_id, "
                               "created_by) VALUES ($1::uuid, $2, $3, $4, $5::uuid)", str(org_id), model["id"],
                               entry["id"], source["id"], str(actor_id))
        else:
            version = await _create_version(conn, org_id=org_id, model=model, origin="library_copy",
                                            maker_ids=[actor_id], created_by=actor_id, positions=positions,
                                            origin_ref=source["id"], tradability=trad)
        r2 = await _event(conn, org_id=org_id, action="subscribe" if subscribe else "copy", actor_id=actor_id,
                          model_id=model["id"], version_id=version["id"], entry_id=entry["id"],
                          detail={"source_version_no": source["version_no"], "title": entry["title"]})
    await publish(pool, [r1, r2])
    out = {"model": model, "version": version}
    if subscribe:
        out["version"] = await _submit_system(pool, org_id=org_id, model=model, version=version,
                                              why="the first version of a library subscription")
    return out


async def copy_entry(pool, *, org_id, actor_id, entry_id, name: str | None = None) -> dict:
    return await _load_from_library(pool, org_id=org_id, actor_id=actor_id, entry_id=entry_id, name=name,
                                    subscribe=False)


async def subscribe_entry(pool, *, org_id, actor_id, entry_id, name: str | None = None) -> dict:
    return await _load_from_library(pool, org_id=org_id, actor_id=actor_id, entry_id=entry_id, name=name,
                                    subscribe=True)


# ── uploads (the log) ───────────────────────────────────────────────────────
OUTCOME_EVENTS = ("draft_created", "rejected", "abandoned", "failed")


async def _upload_event(conn, *, org_id, upload_id, event: str, actor_id, result: U.ParseResult | None = None,
                        model_id=None, version_id=None, mapping_id=None, reason=None, messages=None) -> str:
    counts = result.counts if result else {}
    return str(await conn.fetchval(
        f"""INSERT INTO {T_UPE} (org_id, upload_id, event, model_id, version_id, mapping_id, sheet_name, header_row,
                weight_basis, rows_read, rows_resolved, rows_unresolved, rows_cash, messages, reason, actor_id)
            VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5::uuid, $6::uuid, $7, $8, $9, $10, $11, $12, $13, $14::jsonb,
                    $15, $16::uuid) RETURNING id""",
        str(org_id), str(upload_id), event, str(model_id) if model_id else None,
        str(version_id) if version_id else None, str(mapping_id) if mapping_id else None,
        result.sheet if result else None, result.header_row if result and result.header_row else None,
        result.weight_basis if result else None, counts.get("read"), counts.get("resolved"), counts.get("unresolved"),
        counts.get("cash"), _dumps(messages if messages is not None else
                                   ([m.as_dict() for m in result.messages] if result else [])),
        reason, str(actor_id) if actor_id else None))


async def upload_state(conn, org_id, upload_id) -> tuple[dict, dict | None]:
    uid = _uuid(upload_id, "upload")
    up = await conn.fetchrow(f"SELECT * FROM {T_UP} WHERE id = $1 AND org_id = $2::uuid", uid, str(org_id))
    if up is None:
        raise ModelNotFound("upload not found")
    last = await conn.fetchrow(f"SELECT * FROM {T_UPE} WHERE upload_id = $1 AND event = ANY($2::text[]) "
                               "ORDER BY created_at DESC, id DESC LIMIT 1", uid, list(OUTCOME_EVENTS))
    return dict(up), (dict(last) if last else None)


def provider_key(name: str | None) -> str | None:
    k = (name or "").strip().lower()
    return k or None


async def live_mapping(conn, org_id, provider_name: str | None) -> dict | None:
    k = provider_key(provider_name)
    if not k:
        return None
    row = await conn.fetchrow(f"SELECT * FROM {T_MAP} WHERE org_id = $1::uuid AND provider_key = $2 AND valid_to IS NULL "
                              "AND system_to IS NULL", str(org_id), k)
    if row is None:
        return None
    d = dict(row)
    d["mapping"] = _loads(d["mapping"]) or {}
    return d


def _mapping_out(m: dict | None) -> dict | None:
    if not m:
        return None
    return {"id": str(m["id"]), "provider_name": m["provider_name"], "version_no": m["version_no"],
            "mapping": m["mapping"], "sheet_hint": m["sheet_hint"], "header_row_hint": m["header_row_hint"],
            "last_used_at": _iso(m["last_used_at"])}


def upload_out(up: dict, last: dict | None = None) -> dict:
    return {"id": str(up["id"]), "file_name": up["file_name"], "size_bytes": up["size_bytes"], "sha256": up["sha256"],
            "file_kind": up["file_kind"], "content_type": up["content_type"], "source": up["source"],
            "provider_name": up["provider_name"], "uploaded_at": _iso(up["uploaded_at"]),
            "uploaded_by": str(up["uploaded_by"]) if up["uploaded_by"] else None,
            "outcome": last["event"] if last else up["outcome"],
            "duplicate_of": str(up["duplicate_of"]) if up["duplicate_of"] else None,
            "messages": _loads(up["messages"]) or [],
            "version_id": str(last["version_id"]) if last and last["version_id"] else None,
            "model_id": str((last or {}).get("model_id") or up["model_id"]) if ((last or {}).get("model_id")
                                                                                 or up["model_id"]) else None}


async def receive_upload(pool, *, org_id, actor_id, data: bytes, filename: str, content_type: str | None,
                         provider_name: str | None = None, source: str = "manual", model_id=None,
                         size_bytes: int | None = None, sha256_hex: str | None = None) -> dict:
    """Log the file (always), store the ORIGINAL bytes once per (org, sha256),
    and read its sheets / header / suggested mapping. A refused file is logged
    'failed' and never stored; the same bytes again are logged 'duplicate'
    with no second object."""
    if source not in UPLOAD_SOURCE_LABELS:
        raise ModelError(f"upload source is one of {list(UPLOAD_SOURCE_LABELS)}")
    name = (filename or "upload").strip()[:255] or "upload"
    # the router hashes the WHOLE stream and keeps only the first MAX_BYTES + 1 bytes
    sha = sha256_hex or hashlib.sha256(data).hexdigest()
    size = size_bytes if size_bytes is not None else len(data)
    ext = U.extension(name)
    kind = ext[1:] if ext in U.ACCEPTED_EXTENSIONS else "refused"
    provider = (provider_name or "").strip() or None
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        target = await load_model(conn, org_id, model_id) if model_id else None
        try:
            if size > U.MAX_BYTES:
                raise U.UploadRefused("too_large", f"the file is {size:,} bytes; the limit is {U.MAX_BYTES:,} (5 MB)")
            kind = await run_in_threadpool(U.precheck, data, name)
        except U.UploadRefused as exc:
            msg = U.Message(exc.code, "error", exc.text).as_dict()
            row = await conn.fetchrow(
                f"INSERT INTO {T_UP} (org_id, model_id, file_name, size_bytes, sha256, content_type, file_kind, "
                "uploaded_by, source, provider_name, outcome, messages) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, "
                "$8::uuid, $9, $10, 'failed', $11::jsonb) RETURNING *",
                str(org_id), str(target["id"]) if target else None, name, size, sha, content_type, kind,
                str(actor_id), source, provider, _dumps([msg]))
            await _audit_only(conn, org_id, actor_id, "models.upload_refused", "model_upload", row["id"],
                              {"code": exc.code, "file_name": name})
            refused = {"upload": upload_out(dict(row)), "refused": msg}
        else:
            refused = None
            existing = await conn.fetchrow(f"SELECT id, r2_key FROM {T_UP} WHERE org_id = $1::uuid AND sha256 = $2 "
                                           "AND outcome = 'received'", str(org_id), sha)
    if refused:
        return refused
    if existing:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"INSERT INTO {T_UP} (org_id, model_id, file_name, size_bytes, sha256, r2_key, content_type, file_kind, "
                "uploaded_by, source, provider_name, outcome, duplicate_of) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, "
                "$7, $8, $9::uuid, $10, $11, 'duplicate', $12) RETURNING *",
                str(org_id), str(target["id"]) if target else None, name, size, sha, existing["r2_key"],
                content_type, kind, str(actor_id), source, provider, existing["id"])
            await _audit_only(conn, org_id, actor_id, "models.upload_duplicate", "model_upload", row["id"],
                              {"duplicate_of": str(existing["id"]), "sha256": sha})
            prev_up, prev_last = await upload_state(conn, org_id, existing["id"])
        return {"upload": upload_out(dict(row)), "duplicate_of": upload_out(prev_up, prev_last)}
    key = f"{R2_PREFIX}/{org_id}/{sha}"
    await run_in_threadpool(storage.upload_bytes, key, data, content_type or "application/octet-stream")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"INSERT INTO {T_UP} (org_id, model_id, file_name, size_bytes, sha256, r2_key, content_type, file_kind, "
            "uploaded_by, source, provider_name, outcome) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::uuid, "
            "$10, $11, 'received') RETURNING *",
            str(org_id), str(target["id"]) if target else None, name, size, sha, key, content_type, kind,
            str(actor_id), source, provider)
        await _audit_only(conn, org_id, actor_id, "models.upload_received", "model_upload", row["id"],
                          {"file_name": name, "sha256": sha, "size_bytes": size})
    up = dict(row)
    try:
        wb = await run_in_threadpool(U.read_workbook, data, name)
    except U.UploadRefused as exc:
        msg = U.Message(exc.code, "error", exc.text).as_dict()
        async with pool.acquire() as conn:
            await _upload_event(conn, org_id=org_id, upload_id=up["id"], event="failed", actor_id=actor_id,
                                messages=[msg], reason=exc.text)
            up, last = await upload_state(conn, org_id, up["id"])
        return {"upload": upload_out(up, last), "refused": msg}
    async with pool.acquire() as conn:
        saved = await live_mapping(conn, org_id, provider)
    return {"upload": upload_out(up), **_wizard(wb, None, saved)}


def _wizard(wb: U.Workbook, result: U.ParseResult | None, saved: dict | None, sheet=None, header_row=None) -> dict:
    if result is not None:
        sheet = result.sheet
    elif sheet is None and (saved or {}).get("sheet_hint") in wb.sheets:
        sheet = saved["sheet_hint"]
    if sheet not in wb.grids:
        sheet = wb.sheets[0]
    grid = wb.grids[sheet]
    hdr = ((result.header_row if result and result.header_row else None) or header_row
           or (saved or {}).get("header_row_hint") or U.detect_header(grid, (saved or {}).get("mapping")))
    headers = U.headers_of(grid, hdr) if hdr else []
    return {
        "sheets": [{"name": s, "rows": len(wb.grids[s])} for s in wb.sheets], "sheet": sheet, "header_row": hdr,
        "headers": headers, "suggestions": U.suggest_mapping(headers, (saved or {}).get("mapping")),
        "saved_mapping": _mapping_out(saved),
        "sample": [[U.cell_text(c) for c in r] for r in grid[:min(len(grid), 12)]],
    }


async def _parse_upload(conn, *, org_id, up: dict, sheet, header_row, mapping, provider_name, fill_with_cash):
    if up["outcome"] != "received" or not up["r2_key"]:
        raise ModelConflict(f"this upload is {up['outcome']} — upload the file again to continue")
    data = await run_in_threadpool(storage.download_bytes, up["r2_key"])
    if hashlib.sha256(data).hexdigest() != up["sha256"]:
        raise ModelConflict("the stored file does not match the upload's SHA-256 — it cannot be used")
    wb = await run_in_threadpool(U.read_workbook, data, up["file_name"])
    saved = await live_mapping(conn, org_id, provider_name or up["provider_name"])
    if sheet is None and saved and saved.get("sheet_hint") in wb.sheets:
        sheet = saved["sheet_hint"]
    if header_row is None and saved and saved.get("header_row_hint"):
        header_row = saved["header_row_hint"]
    tol = await tolerance_pct(conn, org_id)
    result = await U.parse_and_validate(conn, wb, sheet=sheet, header_row=header_row, mapping=mapping,
                                        saved_mapping=(saved or {}).get("mapping"), fill_with_cash=fill_with_cash,
                                        tolerance_pct=tol)
    return wb, result, saved


def _saved_mapping_used(saved: dict | None, result: U.ParseResult):
    """The saved mapping's id when the parse applied it: every saved header the
    file carries kept its saved field (other columns may map by synonym or by
    hand). None when nothing of it applied, or the caller overrode it."""
    if not saved:
        return None
    present = {U.norm_header(h) for h in result.headers}
    got = {U.norm_header(k): v for k, v in result.mapping.items()}
    want = {U.norm_header(k): v for k, v in saved["mapping"].items() if U.norm_header(k) in present}
    return saved["id"] if want and all(got.get(k) == v for k, v in want.items()) else None


async def preview_upload(pool, *, org_id, upload_id, sheet=None, header_row=None, mapping=None, provider_name=None,
                         fill_with_cash=False, model_id=None) -> dict:
    """Sheet → header row → mapping → validation report. Writes nothing."""
    async with pool.acquire() as conn:
        up, last = await upload_state(conn, org_id, upload_id)
        try:
            wb, result, saved = await _parse_upload(conn, org_id=org_id, up=up, sheet=sheet, header_row=header_row,
                                                    mapping=mapping, provider_name=provider_name,
                                                    fill_with_cash=fill_with_cash)
        except U.UploadRefused as exc:
            raise ModelError(exc.text, [U.Message(exc.code, "error", exc.text).as_dict()]) from None
        trad = await U.tradability(conn, org_id)
        diff = None
        if model_id:
            model = await load_model(conn, org_id, model_id)
            live = await live_approved(conn, model["id"])
            eff = await _effective_in_memory(conn, result.positions(), [], model["id"])
            diff = await _diff_vs(conn, eff, live["id"] if live else None)
    return {"upload": upload_out(up, last), **_wizard(wb, result, saved), "report": result.report(),
            "tradability": trad, "diff": diff}


async def confirm_upload(pool, *, org_id, actor_id, upload_id, sheet=None, header_row=None, mapping=None,
                         provider_name=None, fill_with_cash=False, model_id=None, model_name=None,
                         description=None) -> dict:
    """Create the DRAFT (a new model, or a new version of ``model_id``) — or,
    if validation finds an error, log the upload 'rejected' with its row-level
    messages and refuse (422)."""
    rejected = None
    refs: list[EventRef] = []
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        up, last = await upload_state(conn, org_id, upload_id)
        if last and last["event"] in ("draft_created", "abandoned"):
            raise ModelConflict(f"this upload is already {UPLOAD_OUTCOME_LABELS[last['event']].lower()}")
        try:
            wb, result, saved = await _parse_upload(conn, org_id=org_id, up=up, sheet=sheet, header_row=header_row,
                                                    mapping=mapping, provider_name=provider_name,
                                                    fill_with_cash=fill_with_cash)
        except U.UploadRefused as exc:
            raise ModelError(exc.text, [U.Message(exc.code, "error", exc.text).as_dict()]) from None
        mapping_id = _saved_mapping_used(saved, result)
        if result.blocking:
            await _upload_event(conn, org_id=org_id, upload_id=up["id"], event="rejected", actor_id=actor["id"],
                                result=result, mapping_id=mapping_id,
                                reason=f"{sum(1 for m in result.messages if m.severity == 'error')} validation error(s)")
            rejected = result
        else:
            if model_id:
                model = await load_model(conn, org_id, model_id)
                if model["source"] != "upload":
                    raise ModelConflict(f"{model['name']} is a {SOURCE_LABELS[model['source']].lower()} model — "
                                        "uploads version upload models only")
            else:
                model, r = await create_model(conn, org_id=org_id, actor_id=actor["id"],
                                              name=model_name or up["file_name"].rsplit(".", 1)[0], source="upload",
                                              description=description,
                                              provider_name=provider_name or up["provider_name"])
                refs.append(r)
            version = await _create_version(conn, org_id=org_id, model=model, origin="upload", maker_ids=[actor["id"]],
                                            created_by=actor["id"], positions=result.positions(), upload_id=up["id"],
                                            tradability=await U.tradability(conn, org_id))
            if mapping_id:
                await conn.execute(f"UPDATE {T_MAP} SET last_used_at = now() WHERE id = $1", mapping_id)
            await _upload_event(conn, org_id=org_id, upload_id=up["id"], event="draft_created", actor_id=actor["id"],
                                result=result, model_id=model["id"], version_id=version["id"], mapping_id=mapping_id)
            refs.append(await _event(conn, org_id=org_id, action="create_draft", actor_id=actor["id"],
                                     model_id=model["id"], version_id=version["id"], upload_id=up["id"],
                                     detail={"origin": "upload", "file_name": up["file_name"], **result.counts,
                                             "weight_basis": result.weight_basis}))
            up, last = await upload_state(conn, org_id, up["id"])
    if rejected is not None:
        raise ModelError(f"{sum(1 for m in rejected.messages if m.severity == 'error')} validation error(s) — the "
                         "upload is logged as rejected; fix the file or the mapping and try again",
                         [m.as_dict() for m in rejected.messages])
    await publish(pool, refs)
    return {"upload": upload_out(up, last), "model": model, "version": version, "report": result.report()}


async def abandon_upload(pool, *, org_id, actor_id, upload_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise ModelError("abandoning an upload needs a reason")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        up, last = await upload_state(conn, org_id, upload_id)
    if up["outcome"] != "received":
        raise ModelConflict(f"this upload is {up['outcome']} — there is nothing to abandon")
    if last and last["event"] == "abandoned":
        raise ModelConflict("this upload is already abandoned")
    if last and last["event"] == "draft_created":
        async with pool.acquire() as conn:
            v = await conn.fetchrow(f"SELECT model_id, status FROM {T_VER} WHERE id = $1 AND system_to IS NULL",
                                    last["version_id"])
        if v is None or v["status"] not in ("draft", "submitted"):
            raise ModelConflict("the draft this upload created is no longer open")
        await abandon(pool, org_id=org_id, actor_id=actor["id"], model_id=v["model_id"], version_id=last["version_id"],
                      reason=reason)
    else:
        async with pool.acquire() as conn:
            await _upload_event(conn, org_id=org_id, upload_id=up["id"], event="abandoned", actor_id=actor["id"],
                                reason=reason.strip())
    async with pool.acquire() as conn:
        up, last = await upload_state(conn, org_id, upload_id)
    return upload_out(up, last)


async def save_mapping(pool, *, org_id, actor_id, provider_name: str, mapping: dict, sheet_hint: str | None = None,
                       header_row_hint: int | None = None, upload_id=None) -> dict:
    """Save the provider's column mapping as its next version (valid axis) —
    applied first on that provider's next upload. A logged action."""
    k = provider_key(provider_name)
    if not k:
        raise ModelError("a saved mapping needs a provider name")
    clean = {}
    for h, f in (mapping or {}).items():
        if not f:
            continue
        if f not in U.FIELDS:
            raise ModelError(f"unknown field {f!r} for column {h!r}")
        if not str(h).strip():
            continue
        clean[str(h).strip()] = f
    fields = list(clean.values())
    if len(set(fields)) != len(fields):
        raise ModelError("each field may be mapped from one column only")
    if "weight" not in fields or not set(fields) & set(U.IDENTIFIER_FIELDS):
        raise ModelError("a mapping needs a weight column and at least one identifier column")
    if header_row_hint is not None and int(header_row_hint) < 1:
        raise ModelError("the header row is 1 or more")
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        if upload_id:
            await upload_state(conn, org_id, upload_id)
        live = await conn.fetchrow(f"SELECT * FROM {T_MAP} WHERE org_id = $1::uuid AND provider_key = $2 AND valid_to "
                                   "IS NULL AND system_to IS NULL FOR UPDATE", str(org_id), k)
        if live is not None and (_loads(live["mapping"]) == clean and live["sheet_hint"] == sheet_hint
                                 and live["header_row_hint"] == header_row_hint):
            row = dict(live)
        else:
            if live is not None:
                await conn.execute(f"UPDATE {T_MAP} SET valid_to = now() WHERE id = $1", live["id"])
            row = dict(await conn.fetchrow(
                f"INSERT INTO {T_MAP} (org_id, provider_name, provider_key, mapping, sheet_hint, header_row_hint, "
                "version_no, created_by) VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6, $7, $8::uuid) RETURNING *",
                str(org_id), provider_name.strip(), k, _dumps(clean), sheet_hint, header_row_hint,
                (live["version_no"] + 1) if live is not None else 1, str(actor_id)))
        if upload_id:
            await conn.execute(f"INSERT INTO {T_UPE} (org_id, upload_id, event, mapping_id, actor_id) VALUES "
                               "($1::uuid, $2::uuid, 'mapping_saved', $3, $4::uuid)", str(org_id), str(upload_id),
                               row["id"], str(actor_id))
        ref = await _event(conn, org_id=org_id, action="save_mapping", actor_id=actor_id, upload_id=upload_id,
                           detail={"provider": provider_name.strip(), "mapping_id": str(row["id"]),
                                   "version_no": row["version_no"], "mapping": clean})
    await publish(pool, [ref])
    row["mapping"] = _loads(row["mapping"])
    return _mapping_out(row)


async def upload_log(conn, org_id, *, outcome: str | None = None, provider: str | None = None, model_id=None,
                     since=None, until=None, limit: int | None = 500) -> list[dict]:
    sql = f"""
        SELECT u.*, COALESCE(uu.full_name, uu.email) AS uploader_name,
               e.event AS last_event, e.model_id AS e_model_id, e.version_id AS e_version_id, e.sheet_name,
               e.header_row, e.weight_basis, e.rows_read, e.rows_resolved, e.rows_unresolved, e.rows_cash,
               e.messages AS e_messages, e.actor_id AS decided_by, e.created_at AS decided_at, e.reason,
               e.mapping_id, COALESCE(du.full_name, du.email) AS decided_by_name, m.name AS model_name,
               v.version_no
        FROM {T_UP} u
        LEFT JOIN LATERAL (SELECT * FROM {T_UPE} x WHERE x.upload_id = u.id AND x.event = ANY($2::text[])
                           ORDER BY x.created_at DESC, x.id DESC LIMIT 1) e ON true
        LEFT JOIN users uu ON uu.id = u.uploaded_by
        LEFT JOIN users du ON du.id = e.actor_id
        LEFT JOIN {T_MODEL} m ON m.id = COALESCE(e.model_id, u.model_id) AND m.system_to IS NULL
        LEFT JOIN {T_VER} v ON v.id = e.version_id AND v.system_to IS NULL
        WHERE u.org_id = $1::uuid"""
    args: list = [str(org_id), list(OUTCOME_EVENTS)]
    if outcome:
        if outcome not in UPLOAD_OUTCOME_LABELS:
            raise ModelError(f"unknown outcome {outcome!r}")
        args.append(outcome)
        sql += f" AND COALESCE(e.event, u.outcome) = ${len(args)}"
    if provider:
        args.append(provider.strip().lower())
        sql += f" AND lower(u.provider_name) = ${len(args)}"
    if model_id:
        args.append(str(_uuid(model_id, "model")))
        sql += f" AND COALESCE(e.model_id, u.model_id) = ${len(args)}::uuid"
    if since:
        args.append(since)
        sql += f" AND u.uploaded_at >= ${len(args)}"
    if until:
        args.append(until)
        sql += f" AND u.uploaded_at < ${len(args)}"
    sql += " ORDER BY u.uploaded_at DESC, u.id"
    if limit:
        args.append(int(limit))
        sql += f" LIMIT ${len(args)}"
    out = []
    for r in await conn.fetch(sql, *args):
        msgs = _loads(r["e_messages"]) if r["last_event"] else _loads(r["messages"])
        out.append({
            "id": str(r["id"]), "uploaded_at": _iso(r["uploaded_at"]), "file_name": r["file_name"],
            "size_bytes": r["size_bytes"], "sha256": r["sha256"], "file_kind": r["file_kind"],
            "content_type": r["content_type"], "uploaded_by": str(r["uploaded_by"]) if r["uploaded_by"] else None,
            "uploader_name": r["uploader_name"], "source": r["source"], "provider_name": r["provider_name"],
            "outcome": r["last_event"] or r["outcome"],
            "duplicate_of": str(r["duplicate_of"]) if r["duplicate_of"] else None,
            "model_id": str(r["e_model_id"] or r["model_id"]) if (r["e_model_id"] or r["model_id"]) else None,
            "model_name": r["model_name"], "version_id": str(r["e_version_id"]) if r["e_version_id"] else None,
            "version_no": r["version_no"], "sheet_name": r["sheet_name"], "header_row": r["header_row"],
            "weight_basis": r["weight_basis"], "rows_read": r["rows_read"], "rows_resolved": r["rows_resolved"],
            "rows_unresolved": r["rows_unresolved"], "rows_cash": r["rows_cash"], "messages": msgs or [],
            "mapping_id": str(r["mapping_id"]) if r["mapping_id"] else None,
            "decided_by": str(r["decided_by"]) if r["decided_by"] else None, "decided_by_name": r["decided_by_name"],
            "decided_at": _iso(r["decided_at"]), "reason": r["reason"],
        })
    return out


CSV_COLUMNS = ("uploaded_at", "file_name", "size_bytes", "sha256", "file_kind", "uploader_name", "source",
               "provider_name", "outcome", "duplicate_of", "model_name", "version_no", "sheet_name", "header_row",
               "weight_basis", "rows_read", "rows_resolved", "rows_unresolved", "rows_cash", "messages",
               "decided_by_name", "decided_at", "reason", "id")


def upload_log_csv(rows: list[dict]) -> bytes:
    """Every row; every cell through model_upload.csv_safe (formula prefixes
    = + - @ tab CR neutralised)."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for r in rows:
        vals = []
        for c in CSV_COLUMNS:
            v = r.get(c)
            if c == "messages":
                v = " | ".join(f"row {m.get('row')}: {m.get('text')}" if m.get("row") else str(m.get("text"))
                               for m in (v or []))
            vals.append(U.csv_safe(v))
        w.writerow(vals)
    return buf.getvalue().encode("utf-8")


# ── reads for the API ───────────────────────────────────────────────────────
def version_out(v: dict | None) -> dict | None:
    if not v:
        return None
    return {
        "id": str(v["id"]), "model_id": str(v["model_id"]), "version_no": v["version_no"], "status": v["status"],
        "origin": v["origin"], "origin_ref": str(v["origin_ref"]) if v["origin_ref"] else None,
        "maker_ids": [str(m) for m in (v["maker_ids"] or [])], "checker_id": str(v["checker_id"]) if v["checker_id"] else None,
        "approved_at": _iso(v["approved_at"]), "submitted_by": str(v["submitted_by"]) if v["submitted_by"] else None,
        "submitted_at": _iso(v["submitted_at"]),
        "approval_task_id": str(v["approval_task_id"]) if v["approval_task_id"] else None,
        "upload_id": str(v["upload_id"]) if v["upload_id"] else None,
        "parent_version_id": str(v["parent_version_id"]) if v["parent_version_id"] else None,
        "notes": v["notes"], "cash_target": _num(v["cash_target"]), "totals": _loads(v["totals"]) or {},
        "diff": _loads(v["diff"]) or {}, "tradability": _loads(v["tradability"]) or [],
        "decision_note": v["decision_note"], "rejected_at": _iso(v["rejected_at"]),
        "abandoned_at": _iso(v["abandoned_at"]), "abandon_reason": v["abandon_reason"],
        "created_at": _iso(v["created_at"]), "created_by": str(v["created_by"]) if v["created_by"] else None,
        **({"follow_up": v["follow_up"]} if "follow_up" in v else {}),
    }


def model_out(m: dict) -> dict:
    return {"id": str(m["id"]), "name": m["name"], "description": m["description"], "source": m["source"],
            "status": m["status"], "base_currency": m["base_currency"], "provider_name": m["provider_name"],
            "default_fee_schedule_id": str(m["default_fee_schedule_id"]) if m["default_fee_schedule_id"] else None,
            "replica_security_id": str(m["replica_security_id"]) if m["replica_security_id"] else None,
            "disclosure": m.get("disclosure"),
            "library_entry_id": str(m["library_entry_id"]) if m["library_entry_id"] else None,
            "created_at": _iso(m["created_at"])}


def position_out(p: dict) -> dict:
    return {"id": str(p["id"]), "row_num": p["row_num"],
            "global_security_id": str(p["global_security_id"]) if p["global_security_id"] else None,
            "security_name": p.get("security_name"), "raw_ticker": p["raw_ticker"], "raw_cusip": p["raw_cusip"],
            "raw_isin": p["raw_isin"], "raw_sedol": p["raw_sedol"], "raw_name": p["raw_name"],
            "target_weight": _num(p["target_weight"]), "asset_class": p["asset_class"], "sleeve_label": p["sleeve_label"],
            "drift_min": _num(p["drift_min"]), "drift_max": _num(p["drift_max"]), "alternates": p["alternates"] or [],
            "restrictions": p["restrictions"], "notes": p["notes"], "resolution_state": p["resolution_state"],
            "resolution_note": p["resolution_note"]}


def sleeve_out(s: dict) -> dict:
    return {"id": str(s["id"]), "child_model_id": str(s["child_model_id"]), "child_name": s.get("child_name"),
            "weight": _num(s["weight"]), "follow": s["follow"],
            "pinned_version_id": str(s["pinned_version_id"]) if s["pinned_version_id"] else None,
            "resolved_child_version_id": str(s["resolved_child_version_id"]),
            "resolved_version_no": s.get("resolved_version_no")}


async def list_models(conn, org_id, *, source=None, status=None, q=None) -> list[dict]:
    sql = f"""
        SELECT m.*, av.version_no AS live_version_no, av.approved_at, av.totals AS live_totals,
               ov.version_no AS open_version_no, ov.status AS open_status, ov.id AS open_version_id,
               GREATEST(m.system_from, av.system_from, ov.system_from) AS last_change
        FROM {T_MODEL} m
        LEFT JOIN {T_VER} av ON av.model_id = m.id AND av.status = 'approved' AND av.system_to IS NULL
        LEFT JOIN {T_VER} ov ON ov.model_id = m.id AND ov.status IN ('draft', 'submitted') AND ov.system_to IS NULL
        WHERE m.org_id = $1::uuid AND m.valid_to IS NULL AND m.system_to IS NULL"""
    args: list = [str(org_id)]
    if source:
        args.append(source)
        sql += f" AND m.source = ${len(args)}"
    if status:
        args.append(status)
        sql += f" AND m.status = ${len(args)}"
    if q:
        args.append(f"%{q.strip().lower()}%")
        sql += f" AND lower(m.name) LIKE ${len(args)}"
    sql += " ORDER BY lower(m.name)"
    out = []
    for r in await conn.fetch(sql, *args):
        t = _loads(r["live_totals"]) or {}
        out.append({**model_out(dict(r)), "live_version_no": r["live_version_no"], "approved_at": _iso(r["approved_at"]),
                    "positions": t.get("positions"), "sleeves": t.get("sleeves"),
                    "open_version_no": r["open_version_no"], "open_status": r["open_status"],
                    "open_version_id": str(r["open_version_id"]) if r["open_version_id"] else None,
                    "last_change": _iso(r["last_change"])})
    return out


async def version_detail(conn, org_id, model_id, version_id) -> dict:
    v = await load_version(conn, org_id, model_id, version_id)
    positions = await positions_of(conn, v["id"])
    sleeves = await sleeves_of(conn, v["id"])
    lt = await look_through(conn, v["id"]) if sleeves else None
    task = await HT.get_task(conn, org_id, v["approval_task_id"]) if v["approval_task_id"] else None
    return {"version": version_out(v), "positions": [position_out(p) for p in positions],
            "sleeves": [sleeve_out(s) for s in sleeves], "look_through": lt_out(lt) if lt is not None else None,
            "task": task}


async def model_detail(conn, org_id, model_id) -> dict:
    m = await load_model(conn, org_id, model_id)
    versions = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_VER} WHERE model_id = $1 AND system_to IS NULL ORDER BY version_no DESC", m["id"])]
    live = next((v for v in versions if v["status"] == "approved"), None)
    opn = next((v for v in versions if v["status"] in ("draft", "submitted")), None)
    sub = await conn.fetchrow(f"SELECT * FROM {T_SUB} WHERE model_id = $1 AND system_to IS NULL", m["id"])
    entries = [dict(r) for r in await conn.fetch(
        f"SELECT id, shelf, title, status, published_at FROM {T_LIB} WHERE model_id = $1 AND system_to IS NULL "
        "AND valid_to IS NULL", m["id"])]
    events = await conn.fetch(
        f"SELECT e.action, e.created_at, e.version_id, e.detail, COALESCE(u.full_name, u.email) AS actor_name "
        f"FROM {T_EVT} e LEFT JOIN users u ON u.id = e.actor_id WHERE e.model_id = $1 ORDER BY e.created_at DESC "
        "LIMIT 100", m["id"])
    children = []
    if m["source"] == "composite":
        children = [{"id": str(r["id"]), "name": r["name"], "live_version_no": r["version_no"]} for r in await conn.fetch(
            f"SELECT mm.id, mm.name, v.version_no FROM {T_MODEL} mm JOIN {T_VER} v ON v.model_id = mm.id AND "
            "v.status = 'approved' AND v.system_to IS NULL WHERE mm.org_id = $1::uuid AND mm.id <> $2 AND "
            "mm.valid_to IS NULL AND mm.system_to IS NULL ORDER BY mm.name", str(org_id), m["id"])]
    fee_schedules = [{"id": str(r["id"]), "code": r["code"], "version": r["version"], "name": r["name"]}
                     for r in await conn.fetch(
                         "SELECT id, code, version, name FROM public.fee_schedules WHERE org_id = $1::uuid AND status = "
                         "'APPROVED' AND system_to IS NULL ORDER BY code, version", str(org_id))]
    return {
        "model": model_out(m),
        "versions": [version_out(v) for v in versions],
        "live": await version_detail(conn, org_id, m["id"], live["id"]) if live else None,
        "open": await version_detail(conn, org_id, m["id"], opn["id"]) if opn else None,
        "uploads": await upload_log(conn, org_id, model_id=m["id"], limit=100),
        "events": [{"action": e["action"], "created_at": _iso(e["created_at"]), "actor_name": e["actor_name"],
                    "version_id": str(e["version_id"]) if e["version_id"] else None,
                    "detail": _loads(e["detail"]) or {}} for e in events],
        "subscription": {"id": str(sub["id"]), "status": sub["status"],
                         "library_entry_id": str(sub["library_entry_id"]),
                         "last_applied_version_id": str(sub["last_applied_version_id"]) if sub[
                             "last_applied_version_id"] else None} if sub else None,
        "library_entries": [{"id": str(e["id"]), "shelf": e["shelf"], "title": e["title"], "status": e["status"],
                             "published_at": _iso(e["published_at"])} for e in entries],
        "available_children": children,
        "fee_schedules": fee_schedules,
    }


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {
        "sources": pairs(SOURCE_LABELS), "model_statuses": pairs(MODEL_STATUS_LABELS),
        "version_statuses": pairs(VERSION_STATUS_LABELS), "origins": pairs(ORIGIN_LABELS),
        "upload_outcomes": pairs(UPLOAD_OUTCOME_LABELS), "upload_sources": pairs(UPLOAD_SOURCE_LABELS),
        "cadences": pairs(CADENCE_LABELS), "shelves": pairs(SHELF_LABELS), "follow_modes": pairs(FOLLOW_LABELS),
        "library_statuses": pairs(LIBRARY_STATUS_LABELS), "events": pairs(EVENT_LABELS),
        **U.vocabulary(),
    }
