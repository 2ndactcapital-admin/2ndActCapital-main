"""REP10 — shared plumbing for tilts, completion, gifting, backtests and the
hypothetical-performance policy: errors (mapped to HTTP codes by the routers),
the system-axis archive-then-update write (rep2 / rep9 precedent: the live row
keeps its id), the audit write, settings with stated defaults, and JSON.

Nothing here decides anything; each service states its own rules.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal

ZERO = Decimal(0)
CENT = Decimal("0.01")
Q = Decimal("0.0000000001")          # weights — numeric(14, 10)
QTY = Decimal("0.00000001")          # quantities and prices — numeric(28, 8)
ESTIMATE = "estimate"
HYPOTHETICAL = "hypothetical"
NEEDS_PRICES = "not estimated — needs prices"


class Rep10Error(ValueError):
    """A request the rules refuse (422)."""


class Rep10NotFound(Rep10Error, LookupError):
    """Not found in the caller's org (404)."""


class Rep10Conflict(Rep10Error):
    """The state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    """The maker may not decide their own request (403)."""


def dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else o.isoformat()
                      if isinstance(o, (date, datetime)) else str(o))


def loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def clean(v):
    """Decimals → strings, dates → ISO, recursively (an envelope never carries a float)."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {str(k): clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    return v


def uid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise Rep10NotFound(f"{what} not found") from None


def dec(v) -> Decimal | None:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def iso(v):
    return v.isoformat() if v is not None else None


def local_day(ts: datetime | None, tz=None) -> date | None:
    """The calendar day of a stored timestamp ON THE SAME CLOCK as ``date.today()`` (the server's local zone; ``tz``
    only for a test). rep10 fix 1: asyncpg returns timestamptz in UTC, so ``ts.date()`` is the UTC day — west of
    UTC, a letter sent at 21:55 ET on Nov 2 is "sent Nov 3" and a same-evening receipt dated Nov 2 (``date.today()``)
    was refused as "received before the letter was sent"."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.date()
    return ts.astimezone(tz).date()


def money(v: Decimal) -> Decimal:
    from decimal import ROUND_HALF_EVEN

    return v.quantize(CENT, rounding=ROUND_HALF_EVEN)


def qw(v: Decimal) -> Decimal:
    from decimal import ROUND_HALF_EVEN

    return v.quantize(Q, rounding=ROUND_HALF_EVEN)


async def archive(conn, table: str, row_id) -> None:
    """Copy the live state to a fresh id (archived_from_id = live id, system_to = now())."""
    got = await conn.fetchval(
        f"""INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {table} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(row_id))
    if got is None:
        raise Rep10Conflict("the row is not live — refusing to change it")


async def update(conn, table: str, row_id, fields: dict, *, touch: bool = True) -> dict:
    """Archive the live state (system axis), then change the live row in place (its id is stable)."""
    await archive(conn, table, row_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    extra = ", updated_at = now()" if touch else ""
    row = await conn.fetchrow(f"UPDATE {table} SET {sets}{extra}, system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(row_id), *[fields[c] for c in cols])
    if row is None:
        raise Rep10Conflict("the row changed underneath this request — reload and retry")
    return dict(row)


async def audit(conn, org_id, user_id, action: str, resource_type: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"rep10.{action}", resource_type, str(resource_id),
        dumps(payload))


async def setting(conn, org_id, key: str, default, *, lo=None, hi=None, integer: bool = False):
    """An org setting with a stated default (rep5's rule: an unset, unreadable or out-of-range value → default)."""
    from services.rebalance_proposals import _setting

    return await _setting(conn, org_id, key, default, lo=lo, hi=hi, integer=integer)


async def setting_text(conn, org_id, key: str, default: str) -> tuple[str, str]:
    from services.org_settings import get_setting_with_origin

    try:
        v, is_default = await get_setting_with_origin(conn, uuid.UUID(str(org_id)), key)
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the stated default
        return default, "default"
    if v is None or is_default or not str(v).strip():
        return default, "default"
    return str(v).strip(), "org_setting"


async def human(conn, actor_id, org_id) -> dict:
    from services import human_tasks as HT

    try:
        return await HT.assert_human(conn, actor_id, org_id)
    except HT.AgentActorRefused as exc:
        raise Rep10Error(str(exc)) from exc


async def withdraw_task(pool, task_id, reason: str) -> None:
    import logging

    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        logging.getLogger(__name__).info("task %s was not open to withdraw", task_id)


async def complete(pool, *, org_id, task_id, actor_id, outcome: str, note: str | None) -> None:
    """Decide a maker-checker task through the engine (its on_complete applies the outcome)."""
    from services import human_tasks as HT

    try:
        await HT.complete_task(pool, org_id=org_id, task_id=task_id, actor_id=actor_id, outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise Rep10Conflict(str(exc)) from exc


async def org_holders(conn, org_id, permission: str) -> set[str]:
    """Active users of the org holding a permission (through their roles) — a to-do's recipients."""
    return {str(r["id"]) for r in await conn.fetch(
        """SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id
           JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id
           JOIN role_permissions rp ON rp.role_id = ur.role_id
           JOIN permissions p ON p.id = rp.permission_id
           WHERE u.org_id = $1::uuid AND p.name = $2 AND u.is_active
             AND COALESCE(u.status, 'active') = 'active'""", str(org_id), permission)}


async def todo(conn, org_id, recipients, *, source: str, related_type: str, related_id, title: str, detail: str,
               action_key: str, category: str = "execution") -> list[str]:
    """One open to-do per recipient — skipped for a recipient who already has an open one for the same subject."""
    out = []
    for u in sorted({str(x) for x in recipients if x}):
        dup = await conn.fetchval("SELECT 1 FROM member_todos WHERE org_id = $1::uuid AND user_id = $2::uuid "
                                  "AND source = $3 AND related_id = $4::uuid AND status = 'open'",
                                  str(org_id), u, source, str(related_id))
        if dup:
            continue
        await conn.execute(
            "INSERT INTO member_todos (org_id, user_id, kind, category, source, related_type, related_id, title, "
            "detail, action_key, priority, status) VALUES ($1::uuid, $2::uuid, 'actual', $3, $4, $5, $6::uuid, $7, "
            "$8, $9, 15, 'open')",
            str(org_id), u, category, source, related_type, str(related_id), title, detail, action_key)
        out.append(u)
    return out


async def longest_history(conn, org_id, key: str, start: date, end: date) -> dict | None:
    """eod_price history for ONE security through the security-data layer, from the ranked source with the MOST
    points in [start, end] (never a combination of sources). Why: security_data.history takes the first ranked
    source with ANY point in the window, so a custodian's one-day valuation of a held position shadows a full
    uploaded series — a backtest or a tracking-error estimate would then never have the history it needs. Each
    source is still asked through the layer (rank, licence and purpose rules unchanged). None when no source has
    a point."""
    from services import security_data as SD
    from services.security_data.providers import build_providers

    best = None
    for k, mod in build_providers().items():
        r = await SD.history(conn, org_id, "eod_price", key, start=start, end=end, purpose="internal",
                             providers={k: mod})
        if not (r.found and r.value):
            continue
        pts = [(d_, Decimal(str(v))) for d_, v in r.value if v is not None and Decimal(str(v)) > 0]
        if best is None or len(pts) > len(best["points"]):
            best = {"points": pts, "provider": r.provider_key, "as_of": r.as_of}
    return best
