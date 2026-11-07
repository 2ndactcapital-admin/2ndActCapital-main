"""BULKAPPROVE1 — bulk approval of maker-checker items: fewer clicks, the SAME rules.

ONE PATH, NO NEW WRITE PATH
───────────────────────────
A bulk decision is a loop over the single-approval service function, once per
item, each in its own transaction(s) exactly as the single route runs it:

    authority   services.authority.decide        (POST /authority/changes/{id}/approve|reject)
    held_away   services.held_away.decide_import (POST /held-away/imports/{id}/approve|reject)

so every rule — maker ≠ checker (service, human task, engine and the table's
CHECK), the approve_* permission (route gate here, rbac.has_permission in
HT.complete_task per item), the state (draft, task open, prechecks), the
immutability / bi-temporal archive and the audit + domain events — applies
unchanged. Each item is its own decision by its own approver; nothing here
decides "many at once". One item's failure never undoes another's.

What this module adds is reading (the pending list, the counts for the entry
points) and two audit rows: one per item (``approvals.bulk_item``: its result
and reason, refusals included) and one per request (``approvals.bulk``: who,
how many, which ids).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import asyncpg

from services import authority as AU
from services import held_away as HA
from services import human_tasks as HT

log = logging.getLogger(__name__)

MAX_ITEMS = 200
AUDIT_RESOURCE = "approvals"


@dataclass(frozen=True)
class BulkType:
    key: str
    label: str
    view: str
    approve: str
    task_kind: str


TYPES = {
    "authority": BulkType("authority", "Authority & custody", AU.VIEW, AU.APPROVE, AU.TASK),
    "held_away": BulkType("held_away", "Held-away entries", HA.VIEW, HA.APPROVE, HA.TASK_IMPORT),
}
TYPE_KEYS = tuple(TYPES)
APPROVE_KEYS = tuple(t.approve for t in TYPES.values())
READ_KEYS = tuple(p for t in TYPES.values() for p in (t.view, t.approve))

DECISIONS = {"approve": "Approve", "reject": "Reject"}
RESULT_LABELS = {"approved": "Approved", "rejected": "Rejected", "refused": "Refused"}
SUBTYPE_LABELS = {
    "authority": {"household": "Household authority", "account": "Account authority / custody"},
    "held_away": {k: f"Held-away {v.lower()}" for k, v in HA.SOURCE_KIND_LABELS.items()},
}
AGE_BUCKETS = [
    {"key": "any", "label": "Any age", "min_days": None, "max_days": None},
    {"key": "today", "label": "Today", "min_days": None, "max_days": 0},
    {"key": "week", "label": "Up to 7 days", "min_days": None, "max_days": 7},
    {"key": "older", "label": "Older than 7 days", "min_days": 8, "max_days": None},
]
BLOCK_LABELS = {
    "maker": "You made this — a different person must decide it",
    "permission": "You do not hold {perm}",
    "not_pending": "No longer pending — its approval task is {status}",
    "refused_file": "The file was refused — it can only be rejected",
}


class BulkError(Exception):
    """A request the rules refuse as a whole (422)."""


class BulkPermissionError(PermissionError):
    """The caller does not hold the type's approval permission (403, naming it)."""


def bulk_type(key: str) -> BulkType:
    t = TYPES.get(key)
    if t is None:
        raise BulkError(f"type is one of {', '.join(TYPE_KEYS)}")
    return t


def holds(principal, perm: str) -> bool:
    """Super Admin first, then the exact permission (services.authz semantics)."""
    return bool(principal.is_super_admin or perm in principal.permissions)


def _iso(v):
    return v.isoformat() if v is not None else None


def _dumps(v) -> str:
    return json.dumps(v, default=str)


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _age_days(ts, today: date) -> int | None:
    if ts is None:
        return None
    d = ts.date() if isinstance(ts, datetime) else ts
    return max(0, (today - d).days)


def _dec_sum(values) -> str | None:
    total = Decimal(0)
    for v in values:
        if v is None:
            return None
        try:
            total += Decimal(str(v))
        except (InvalidOperation, ValueError):
            return None
    return format(total, "f")


async def _task_status(conn, org_id, task_ids) -> dict[str, str]:
    ids = [uuid.UUID(str(t)) for t in task_ids if t]
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT rs.id, rs.status AS step_status, r.status AS run_status FROM workflow_run_steps rs "
        "JOIN workflow_runs r ON r.id = rs.workflow_run_id WHERE r.org_id = $1::uuid AND rs.id = ANY($2::uuid[])",
        str(org_id), ids)
    return {str(r["id"]): HT._status_of(r["step_status"], r["run_status"]) for r in rows}


async def _names(conn, ids) -> dict[str, str]:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = await conn.fetch("SELECT id, full_name FROM public.users WHERE id = ANY($1::uuid[])", ids)
    return {str(r["id"]): (r["full_name"] or "A colleague") for r in rows}


def _decidability(principal, bt: BulkType, maker_id, task_status: str | None, *, refused_file: bool = False) -> dict:
    """The server's own answer for one item: which decisions this viewer may send, or why none."""
    if not holds(principal, bt.approve):
        return {"actions": [], "blocked": "permission", "blocked_reason": BLOCK_LABELS["permission"].format(
            perm=bt.approve)}
    if str(maker_id) == str(principal.user_id):
        return {"actions": [], "blocked": "maker", "blocked_reason": BLOCK_LABELS["maker"]}
    if task_status != "open":
        return {"actions": [], "blocked": "not_pending",
                "blocked_reason": BLOCK_LABELS["not_pending"].format(status=task_status or "missing")}
    if refused_file:
        return {"actions": ["reject"], "blocked": "refused_file", "blocked_reason": BLOCK_LABELS["refused_file"]}
    return {"actions": ["approve", "reject"], "blocked": None, "blocked_reason": None}


# ── Pending lists ───────────────────────────────────────────────────────────
async def _authority_items(conn, org_id, principal, *, household_id=None, account_id=None,
                           today: date | None = None) -> list[dict]:
    today = today or date.today()
    bt = TYPES["authority"]
    hh = [dict(r) for r in await conn.fetch(
        f"SELECT a.*, h.name AS household_name FROM {AU.T_HH} a JOIN public.households h ON h.id = a.household_id "
        "WHERE a.org_id = $1::uuid AND a.status = 'draft' AND a.system_to IS NULL AND a.valid_to IS NULL "
        "AND ($2::uuid IS NULL OR a.household_id = $2::uuid) AND $3::uuid IS NULL ORDER BY a.created_at",
        str(org_id), household_id, account_id)]
    acc = [dict(r) for r in await conn.fetch(
        f"SELECT s.*, ac.account_number_masked, ac.household_id, ac.is_held_away, ac.custodian_code, "
        "ac.registration_type, ac.tax_status, ac.is_discretionary, ac.closed_on, ac.org_id AS account_org_id, "
        f"h.name AS household_name FROM {AU.T_ACC} s JOIN public.accounts ac ON ac.id = s.account_id "
        "AND ac.system_to IS NULL AND ac.valid_to IS NULL LEFT JOIN public.households h ON h.id = ac.household_id "
        "WHERE s.org_id = $1::uuid AND s.status = 'draft' AND s.system_to IS NULL AND s.valid_to IS NULL "
        "AND ($2::uuid IS NULL OR ac.household_id = $2::uuid) AND ($3::uuid IS NULL OR s.account_id = $3::uuid) "
        "ORDER BY s.created_at", str(org_id), household_id, account_id)]
    status = await _task_status(conn, org_id, [r["approval_task_id"] for r in hh + acc])
    names = await _names(conn, [r["maker_id"] for r in hh + acc])
    out = []
    for r in hh:
        cur = await AU.household_in_effect(conn, org_id, r["household_id"], today)
        frm = AU.AUTHORITY_LABELS[cur["authority"]] if cur else AU.SOURCE_LABELS["default"]
        out.append(_authority_item(r, "household", principal, bt, status, names, today, {
            "household_id": str(r["household_id"]), "household_name": r["household_name"], "account_id": None,
            "account_masked": None, "from_label": frm, "to_label": AU.AUTHORITY_LABELS[r["authority"]]}))
    for r in acc:
        account = {"id": r["account_id"], "org_id": r["account_org_id"],
                   "account_number_masked": r["account_number_masked"], "custodian_code": r["custodian_code"],
                   "registration_type": r["registration_type"], "tax_status": r["tax_status"],
                   "household_id": r["household_id"], "is_discretionary": r["is_discretionary"],
                   "is_held_away": r["is_held_away"], "closed_on": r["closed_on"]}
        await AU.attach(conn, org_id, account, today)
        eff = AU.authority_out(account)
        frm = f"{AU.AUTHORITY_LABELS.get(eff.get('authority'), eff.get('authority'))} · " \
              f"{AU.CUSTODY_LABELS.get(eff.get('custody'), eff.get('custody'))}"
        to_auth = (AU.AUTHORITY_LABELS[r["authority_override"]] if r["authority_override"]
                   else "Follows the household")
        out.append(_authority_item(r, "account", principal, bt, status, names, today, {
            "household_id": str(r["household_id"]) if r["household_id"] else None,
            "household_name": r["household_name"], "account_id": str(r["account_id"]),
            "account_masked": r["account_number_masked"], "from_label": frm,
            "to_label": f"{to_auth} · {AU.CUSTODY_LABELS[r['custody']]}"}))
    return out


def _authority_item(r, subtype, principal, bt, status, names, today, extra) -> dict:
    tid = str(r["approval_task_id"]) if r["approval_task_id"] else None
    return {"id": str(r["id"]), "type": "authority", "subtype": subtype,
            "subtype_label": SUBTYPE_LABELS["authority"][subtype], **extra,
            "reason": r["reason"], "effective_from": _iso(r["effective_from"]),
            "maker_id": str(r["maker_id"]), "maker_name": names.get(str(r["maker_id"]), "A colleague"),
            "requested_at": _iso(r["created_at"]), "age_days": _age_days(r["created_at"], today),
            "task_id": tid, "task_status": status.get(tid) if tid else None,
            **_decidability(principal, bt, r["maker_id"], status.get(tid) if tid else None)}


async def _held_away_items(conn, org_id, principal, *, household_id=None, account_id=None,
                           today: date | None = None) -> list[dict]:
    today = today or date.today()
    bt = TYPES["held_away"]
    rows = [dict(r) for r in await conn.fetch(
        f"SELECT i.*, ac.account_number_masked, ac.household_id, h.name AS household_name FROM {HA.T_IMP} i "
        "JOIN public.accounts ac ON ac.id = i.account_id AND ac.system_to IS NULL AND ac.valid_to IS NULL "
        "LEFT JOIN public.households h ON h.id = ac.household_id WHERE i.org_id = $1::uuid AND i.status = 'draft' "
        "AND i.system_to IS NULL AND ($2::uuid IS NULL OR ac.household_id = $2::uuid) "
        "AND ($3::uuid IS NULL OR i.account_id = $3::uuid) ORDER BY i.created_at",
        str(org_id), household_id, account_id)]
    status = await _task_status(conn, org_id, [r["approval_task_id"] for r in rows])
    names = await _names(conn, [r["entered_by"] for r in rows])
    out = []
    for r in rows:
        tid = str(r["approval_task_id"]) if r["approval_task_id"] else None
        payload = _loads(r["payload"]) or {}
        positions = payload.get("positions") or []
        refused = bool((_loads(r["extraction"]) or {}).get("refused"))
        out.append({
            "id": str(r["id"]), "type": "held_away", "subtype": r["kind"],
            "subtype_label": SUBTYPE_LABELS["held_away"].get(r["kind"], r["kind"]),
            "household_id": str(r["household_id"]) if r["household_id"] else None,
            "household_name": r["household_name"], "account_id": str(r["account_id"]),
            "account_masked": r["account_number_masked"],
            "from_label": None, "to_label": f"{r['rows_accepted']} line(s) as of {_iso(r['as_of'])}",
            "reason": r["notes"], "as_of": _iso(r["as_of"]),
            "maker_id": str(r["entered_by"]), "maker_name": names.get(str(r["entered_by"]), "A colleague"),
            "requested_at": _iso(r["created_at"]), "age_days": _age_days(r["created_at"], today),
            "task_id": tid, "task_status": status.get(tid) if tid else None,
            "lines": {"positions": positions, "lots": payload.get("lots") or [],
                      "transactions": payload.get("transactions") or [],
                      "rejects": _loads(r["rejects"]) or []},
            "totals": {"rows_read": r["rows_read"], "rows_accepted": r["rows_accepted"],
                       "rows_rejected": r["rows_rejected"], "positions": len(positions),
                       "lots": len(payload.get("lots") or []),
                       "transactions": len(payload.get("transactions") or []),
                       "market_value": _dec_sum(p.get("market_value") for p in positions) if positions else "0"},
            **_decidability(principal, bt, r["entered_by"], status.get(tid) if tid else None, refused_file=refused)})
    return out


async def pending(conn, org_id, principal, type_key: str, *, household_id=None, account_id=None) -> list[dict]:
    bt = bulk_type(type_key)
    if bt.key == "authority":
        return await _authority_items(conn, org_id, principal, household_id=household_id, account_id=account_id)
    return await _held_away_items(conn, org_id, principal, household_id=household_id, account_id=account_id)


def vocabularies(items: list[dict], type_key: str) -> dict:
    """Filter vocabularies built from the items themselves (the screen never hardcodes them)."""
    hh = {}
    makers = {}
    for i in items:
        if i.get("household_id"):
            hh[i["household_id"]] = i.get("household_name") or "Household"
        makers[i["maker_id"]] = i.get("maker_name") or "A colleague"
    return {
        "types": [{"key": k, "label": t.label} for k, t in TYPES.items()],
        "subtypes": [{"key": k, "label": v} for k, v in SUBTYPE_LABELS[type_key].items()],
        "households": [{"key": k, "label": v} for k, v in sorted(hh.items(), key=lambda kv: kv[1])],
        "makers": [{"key": k, "label": v} for k, v in sorted(makers.items(), key=lambda kv: kv[1])],
        "ages": AGE_BUCKETS,
        "decisions": [{"key": k, "label": v} for k, v in DECISIONS.items()],
        "results": [{"key": k, "label": v} for k, v in RESULT_LABELS.items()],
        "blocked": [{"key": k, "label": v} for k, v in BLOCK_LABELS.items()],
    }


async def visible_item_ids(conn, org_id, principal, type_key: str, keep) -> set[str]:
    """VISIBILITY1 FIX 1 (E) — INTERNAL, ungated: the ids of the pending items of ``type_key`` that ``keep`` admits
    (a restricted caller's visible families). The decide route has already required APPROVE_KEYS; this adds no gate
    and checks no permission of its own — it only narrows which item ids the caller may decide."""
    return {str(i.get("id")) for i in await pending(conn, org_id, principal, type_key) if keep(i)}


async def summary(conn, org_id, principal, *, household_id=None, account_id=None, keep=None) -> dict:
    """Pending counts for the entry points: per readable type, the items this viewer may decide. ``keep``
    (VISIBILITY1) drops items of families the viewer cannot see, so the counts match the visible list."""
    out = {}
    for k, bt in TYPES.items():
        if not (holds(principal, bt.view) or holds(principal, bt.approve)):
            continue
        items = await pending(conn, org_id, principal, k, household_id=household_id, account_id=account_id)
        if keep is not None:
            items = [i for i in items if keep(i)]
        out[k] = {"label": bt.label, "pending": len(items),
                  "decidable": sum(1 for i in items if "approve" in i["actions"] or "reject" in i["actions"])}
    return out


# ── The bulk decision ───────────────────────────────────────────────────────
def _refusal(exc: Exception, bt: BulkType) -> tuple[int, str]:
    """The same classification the single routes apply (routers.authority / routers.held_away _raise)."""
    msg = str(exc).split("\n")[0]
    if isinstance(exc, (AU.AuthorityNotFound, HA.HeldAwayNotFound, HT.HumanTaskNotFound)):
        return 404, "Not found"
    if isinstance(exc, (AU.MakerCheckerViolation, HA.MakerCheckerViolation, HT.MakerCheckerError)):
        return 403, f"{BLOCK_LABELS['maker']} ({msg})"
    if isinstance(exc, (PermissionError, HT.HumanTaskPermissionError)):
        return 403, msg or f"Permission required: {bt.approve}"
    if isinstance(exc, (AU.AuthorityConflict, HA.HeldAwayConflict, HT.HumanTaskConflict)):
        return 409, f"No longer pending — {msg}"
    if isinstance(exc, asyncpg.UniqueViolationError):
        return 409, f"refused by the database: {exc.constraint_name}"
    if isinstance(exc, asyncpg.CheckViolationError):
        return 422, f"refused by the database: {exc.constraint_name}"
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        return 409, msg
    if isinstance(exc, (AU.AuthorityError, HA.HeldAwayError, HT.HumanTaskError, ValueError)):
        return 422, msg
    return 500, f"failed ({type(exc).__name__}) — nothing else in the batch was affected"


async def _decide_one(pool, bt: BulkType, *, org_id, actor_id, item_id, outcome, note) -> dict:
    if bt.key == "authority":
        return await AU.decide(pool, org_id=org_id, actor_id=actor_id, change_id=item_id, outcome=outcome, note=note)
    return await HA.decide_import(pool, org_id=org_id, actor_id=actor_id, import_id=item_id, outcome=outcome,
                                  note=note)


async def _audit(pool, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
                str(org_id), str(user_id), action, AUDIT_RESOURCE, str(resource_id) if resource_id else None,
                _dumps(payload))


def _normalise_ids(item_ids) -> list[tuple[str, str | None]]:
    """[(as sent, canonical uuid or None)], duplicates dropped (first kept), order kept."""
    seen, out = set(), []
    for raw in item_ids:
        s = str(raw).strip()
        try:
            canon = str(uuid.UUID(s))
        except (ValueError, AttributeError, TypeError):
            canon = None
        key = canon or s
        if key in seen:
            continue
        seen.add(key)
        out.append((s, canon))
    return out


async def decide_bulk(pool, *, org_id, caller, type_key: str, item_ids, decision: str,
                      note: str | None = None) -> dict:
    """Each item through the single-approval service, in turn; per-item results; one batch audit row.
    ``caller`` is the request's services.authz Principal."""
    principal = caller
    bt = bulk_type(type_key)
    if not holds(principal, bt.approve):
        raise BulkPermissionError(f"Permission required: {bt.approve}")
    if decision not in DECISIONS:
        raise BulkError("decision is approve or reject")
    note = (note or "").strip() or None
    if decision == "reject" and not note:
        raise BulkError("a rejection needs a note saying why")
    ids = _normalise_ids(item_ids or [])
    if not ids:
        raise BulkError("select at least one item")
    if len(ids) > MAX_ITEMS:
        raise BulkError(f"at most {MAX_ITEMS} items per request ({len(ids)} sent)")
    batch_id = uuid.uuid4()
    results = []
    for sent, canon in ids:
        if canon is None:
            res = {"id": sent, "result": "refused", "code": 422, "reason": "Not a valid item id"}
        else:
            try:
                await _decide_one(pool, bt, org_id=org_id, actor_id=principal.user_id, item_id=canon,
                                  outcome=decision, note=note)
                res = {"id": canon, "result": "approved" if decision == "approve" else "rejected", "code": 200,
                       "reason": None}
            except Exception as exc:  # noqa: BLE001 — one item's failure never stops or undoes another's
                code, reason = _refusal(exc, bt)
                if code == 500:
                    log.exception("bulk %s %s on %s failed", bt.key, decision, canon)
                res = {"id": canon, "result": "refused", "code": code, "reason": reason}
        res["result_label"] = RESULT_LABELS[res["result"]]
        results.append(res)
        try:
            await _audit(pool, org_id, principal.user_id, "approvals.bulk_item", canon,
                         {"batch_id": str(batch_id), "type": bt.key, "decision": decision, "item_id": res["id"],
                          "result": res["result"], "reason": res["reason"]})
        except Exception:  # noqa: BLE001 — the decision itself stands (it was audited by its own path)
            log.exception("bulk item audit not written for %s", res["id"])
    counts = {k: sum(1 for r in results if r["result"] == k) for k in RESULT_LABELS}
    await _audit(pool, org_id, principal.user_id, "approvals.bulk", batch_id,
                 {"type": bt.key, "decision": decision, "note": note, "requested": len(ids),
                  "item_ids": [r["id"] for r in results], "counts": counts,
                  "at": datetime.now(timezone.utc).isoformat()})
    return {"batch_id": str(batch_id), "type": bt.key, "decision": decision, "requested": len(ids),
            "counts": counts, "results": results}
