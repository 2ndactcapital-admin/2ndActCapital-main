"""CRMDETAIL1 Task 5 — liabilities (mortgages, credit lines, loans) and the net worth they reduce.

``portfolio.liabilities`` (docs/crmdetail1_part1.sql) — VALID axis (Rule 3): nothing references a liability's id,
so an edit closes the live version (``valid_to = now()``) and inserts a new one carrying the same ``lineage_id``;
ENDING (paid off / refinanced away) closes the live version and inserts nothing. Every version stays readable as
history; nothing is ever deleted.

Money is Decimal end to end: amounts arrive as strings (a float is refused), are stored numeric(20,2) and leave as
strings. Unfunded commitments are NOT liabilities (they stay in portfolio.commitments).

NET WORTH, THE ONE PLACE (Task 1c). Gross assets come from ``public.entity_holdings`` — the look-through buckets
``services.portfolio_rollup`` writes (an entity's row already carries its compounded share of everything owned
beneath it) — summed by ``services.households._sum_holdings``, which both household rollups and the entity figure
use. Liabilities are attributed on the SAME basis: each borrower's balance counts in full on the borrower and at
the compounded ownership fraction on every entity above it, with the weights from the same engine the asset rollup
uses (``portfolio_rollup._ownership_weights`` → ``entity_graph.get_lookthrough``). Net = gross − liabilities.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

from services import crm_vocab as V

TABLE = "portfolio.liabilities"
CENTS = Decimal("0.01")
ZERO = Decimal("0")
MAX_AMOUNT = Decimal("1000000000000")   # 1 trillion — anything larger is a typo

COLS = ("id, lineage_id, owner_entity_id, lender_entity_id, lender_name, name, kind, original_amount, "
        "current_balance, currency_code, as_of, rate_type, rate_text, maturity, collateral_account_id, "
        "collateral_asset_id, collateral_text, notes, change_reason, valid_from, valid_to, created_by")
#: the fields a caller sets (create) or changes (revise)
FIELDS = ("lender_entity_id", "lender_name", "name", "kind", "original_amount", "current_balance", "currency_code",
          "as_of", "rate_type", "rate_text", "maturity", "collateral_account_id", "collateral_asset_id",
          "collateral_text", "notes")


class LiabilityError(ValueError):
    """Refused for a reason the caller can fix (422)."""


class LiabilityNotFound(LookupError):
    """No live liability with that id in the caller's org (404)."""


def _u(v) -> UUID:
    return v if isinstance(v, UUID) else UUID(str(v))


def money(raw, label: str, *, required: bool) -> Decimal | None:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if required:
            raise LiabilityError(f"{label} is required")
        return None
    if isinstance(raw, (bool, float)):
        raise LiabilityError(f"{label}: send the amount as text (e.g. \"3500000.00\") — a float is refused so no "
                             "rounding enters")
    try:
        v = Decimal(str(raw).strip().replace(",", ""))
    except InvalidOperation:
        raise LiabilityError(f"{label}: an amount, not '{raw}'") from None
    if not v.is_finite() or v < 0:
        raise LiabilityError(f"{label}: zero or more")
    if v > MAX_AMOUNT:
        raise LiabilityError(f"{label}: larger than any plausible balance")
    if v != v.quantize(CENTS):
        raise LiabilityError(f"{label}: at most two decimal places")
    return v.quantize(CENTS)


def _date(raw, label: str, *, required: bool) -> date | None:
    if raw is None or raw == "":
        if required:
            raise LiabilityError(f"{label} is required")
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise LiabilityError(f"{label}: a date as YYYY-MM-DD, not '{raw}'") from None


def _text(raw, limit=2000) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if len(s) > limit:
        raise LiabilityError(f"text longer than {limit} characters")
    return s or None


async def _validate(conn, org_id, owner_id, data: dict) -> dict:
    """Normalise + validate a full field set (create, or the merged row of a revision)."""
    out = {}
    out["name"] = _text(data.get("name"), 300)
    if not out["name"]:
        raise LiabilityError("name the liability (e.g. \"Coastal Bank — mortgage (Naples)\")")
    kind = data.get("kind")
    if kind not in V.LIABILITY_KIND_KEYS:
        raise LiabilityError(f"kind is one of {', '.join(k for k, _ in V.LIABILITY_KINDS)}")
    out["kind"] = kind
    rt = data.get("rate_type") or None
    if rt is not None and rt not in V.RATE_TYPE_KEYS:
        raise LiabilityError("rate type is fixed or floating")
    out["rate_type"] = rt
    out["original_amount"] = money(data.get("original_amount"), "Original amount", required=False)
    out["current_balance"] = money(data.get("current_balance"), "Current balance", required=True)
    cur = (data.get("currency_code") or "USD").strip().upper()
    if len(cur) != 3 or not cur.isalpha():
        raise LiabilityError("currency is a three-letter code, e.g. USD")
    out["currency_code"] = cur
    out["as_of"] = _date(data.get("as_of"), "As of", required=True)
    out["maturity"] = _date(data.get("maturity"), "Maturity", required=False)
    out["rate_text"] = _text(data.get("rate_text"), 200)
    out["lender_name"] = _text(data.get("lender_name"), 300)
    out["collateral_text"] = _text(data.get("collateral_text"))
    out["notes"] = _text(data.get("notes"))
    lender = data.get("lender_entity_id") or None
    if lender is not None:
        try:
            lender = _u(lender)
        except ValueError:
            raise LiabilityError("lender: choose a CRM record") from None
        if lender == _u(owner_id):
            raise LiabilityError("a record cannot lend to itself")
        if not await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
                                   "AND system_to IS NULL", lender, _u(org_id)):
            raise LiabilityError("lender: that record is not in this organisation's CRM")
    out["lender_entity_id"] = lender
    acct = data.get("collateral_account_id") or None
    if acct is not None:
        try:
            acct = _u(acct)
        except ValueError:
            raise LiabilityError("collateral account: choose an account") from None
        if not await conn.fetchval("SELECT 1 FROM accounts WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
                                   "AND system_to IS NULL", acct, _u(org_id)):
            raise LiabilityError("collateral account: not an account of this organisation")
    out["collateral_account_id"] = acct
    asset = data.get("collateral_asset_id") or None
    if asset is not None:
        try:
            asset = _u(asset)
        except ValueError:
            raise LiabilityError("collateral asset: choose an asset") from None
        if not await conn.fetchval("SELECT 1 FROM portfolio.assets WHERE id = $1 AND org_id = $2::uuid "
                                   "AND valid_to IS NULL AND system_to IS NULL", asset, _u(org_id)):
            raise LiabilityError("collateral asset: not an asset of this organisation")
    out["collateral_asset_id"] = asset
    return out


def to_json(row, names: dict | None = None) -> dict:
    names = names or {}
    d = {}
    for k, v in dict(row).items():
        if isinstance(v, Decimal):
            d[k] = format(v, "f")
        elif isinstance(v, (UUID,)):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        else:
            d[k] = v
    d["kind_label"] = dict(V.LIABILITY_KINDS).get(row["kind"], row["kind"])
    d["rate_type_label"] = dict(V.RATE_TYPES).get(row["rate_type"]) if row["rate_type"] else None
    d["lender_display"] = names.get(str(row["lender_entity_id"])) if row["lender_entity_id"] else row["lender_name"]
    d["ended"] = row["valid_to"] is not None
    return d


async def _names(conn, org_id, rows) -> dict:
    ids = {r["lender_entity_id"] for r in rows if r["lender_entity_id"]}
    if not ids:
        return {}
    return {str(r["id"]): r["display_name"] for r in await conn.fetch(
        "SELECT id, display_name FROM entities WHERE id = ANY($1::uuid[]) AND org_id = $2::uuid AND valid_to IS NULL "
        "AND system_to IS NULL", list(ids), _u(org_id))}


async def _owner(conn, org_id, entity_id):
    return await conn.fetchrow("SELECT id, entity_type::text AS entity_type FROM entities WHERE id = $1 AND "
                               "org_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL", _u(entity_id),
                               _u(org_id))


async def list_for_entity(conn, org_id, entity_id) -> dict:
    if await _owner(conn, org_id, entity_id) is None:
        raise LiabilityNotFound("Entity not found")
    rows = await conn.fetch(f"SELECT {COLS} FROM {TABLE} WHERE org_id = $1::uuid AND owner_entity_id = $2 "
                            "AND system_to IS NULL ORDER BY (valid_to IS NULL) DESC, name, valid_from DESC",
                            _u(org_id), _u(entity_id))
    names = await _names(conn, org_id, rows)
    current = [to_json(r, names) for r in rows if r["valid_to"] is None]
    history = [to_json(r, names) for r in rows if r["valid_to"] is not None]
    total = sum((Decimal(c["current_balance"]) for c in current), ZERO)
    return {"current": current, "history": history, "total_balance": format(total.quantize(CENTS), "f")}


async def create(conn, *, org_id, actor_id, entity_id, data: dict) -> dict:
    if await _owner(conn, org_id, entity_id) is None:
        raise LiabilityNotFound("Entity not found")
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise LiabilityError(f"not a liability field: {', '.join(unknown)}")
    v = await _validate(conn, org_id, entity_id, data)
    lineage = uuid.uuid4()
    row = await conn.fetchrow(
        f"""
        INSERT INTO {TABLE} (lineage_id, org_id, owner_entity_id, lender_entity_id, lender_name, name, kind,
                             original_amount, current_balance, currency_code, as_of, rate_type, rate_text, maturity,
                             collateral_account_id, collateral_asset_id, collateral_text, notes, change_reason,
                             created_by)
        VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18,
                'entered on the CRM record', $19)
        RETURNING {COLS}
        """,
        lineage, _u(org_id), _u(entity_id), v["lender_entity_id"], v["lender_name"], v["name"], v["kind"],
        v["original_amount"], v["current_balance"], v["currency_code"], v["as_of"], v["rate_type"], v["rate_text"],
        v["maturity"], v["collateral_account_id"], v["collateral_asset_id"], v["collateral_text"], v["notes"],
        _u(actor_id) if actor_id else None)
    return to_json(row, await _names(conn, org_id, [row]))


async def _live(conn, org_id, liability_id):
    return await conn.fetchrow(f"SELECT {COLS} FROM {TABLE} WHERE id = $1 AND org_id = $2::uuid "
                               "AND valid_to IS NULL AND system_to IS NULL", _u(liability_id), _u(org_id))


async def revise(conn, *, org_id, actor_id, liability_id, changes: dict, reason: str | None = None) -> dict:
    """A new version: close the live row, insert the merged row with the same lineage. Validates the MERGED row."""
    cur = await _live(conn, org_id, liability_id)
    if cur is None:
        raise LiabilityNotFound("Liability not found, or already ended")
    unknown = sorted(set(changes) - set(FIELDS))
    if unknown:
        raise LiabilityError(f"not a liability field: {', '.join(unknown)}")
    if not changes:
        raise LiabilityError("nothing to change")
    merged = {k: cur[k] for k in FIELDS}
    for k in ("original_amount", "current_balance"):
        merged[k] = format(merged[k], "f") if merged[k] is not None else None
    merged.update(changes)
    v = await _validate(conn, org_id, cur["owner_entity_id"], merged)
    async with conn.transaction():
        await conn.execute(f"UPDATE {TABLE} SET valid_to = now() WHERE id = $1 AND org_id = $2::uuid "
                           "AND valid_to IS NULL AND system_to IS NULL", cur["id"], _u(org_id))
        row = await conn.fetchrow(
            f"""
            INSERT INTO {TABLE} (lineage_id, org_id, owner_entity_id, lender_entity_id, lender_name, name, kind,
                                 original_amount, current_balance, currency_code, as_of, rate_type, rate_text,
                                 maturity, collateral_account_id, collateral_asset_id, collateral_text, notes,
                                 change_reason, created_by)
            VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)
            RETURNING {COLS}
            """,
            cur["lineage_id"], _u(org_id), cur["owner_entity_id"], v["lender_entity_id"], v["lender_name"],
            v["name"], v["kind"], v["original_amount"], v["current_balance"], v["currency_code"], v["as_of"],
            v["rate_type"], v["rate_text"], v["maturity"], v["collateral_account_id"], v["collateral_asset_id"],
            v["collateral_text"], v["notes"], _text(reason, 500) or "edited on the CRM record",
            _u(actor_id) if actor_id else None)
    return to_json(row, await _names(conn, org_id, [row]))


async def end(conn, *, org_id, liability_id, reason: str | None = None) -> dict:
    row = await conn.fetchrow(
        f"UPDATE {TABLE} SET valid_to = now(), change_reason = $3 WHERE id = $1 AND org_id = $2::uuid "
        f"AND valid_to IS NULL AND system_to IS NULL RETURNING {COLS}",
        _u(liability_id), _u(org_id), _text(reason, 500) or "ended (repaid or closed)")
    if row is None:
        raise LiabilityNotFound("Liability not found, or already ended")
    return to_json(row, await _names(conn, org_id, [row]))


async def owner_of(conn, org_id, liability_id):
    """The owner entity of a liability (any version) in the org — for the route's entity-access check."""
    return await conn.fetchval(f"SELECT owner_entity_id FROM {TABLE} WHERE id = $1 AND org_id = $2::uuid",
                               _u(liability_id), _u(org_id))


# ── Net worth ───────────────────────────────────────────────────────────────────────────────────────────────────
async def lookthrough_liabilities(conn, org_id, entity_ids, *, as_of: date | None = None) -> dict:
    """{entity_id(str): Decimal} — each entity's own live balances plus its compounded share of every borrower
    beneath it (the asset rollup's weights). ``as_of`` keeps balances stated on or before that day."""
    from services.portfolio_rollup import _ownership_weights

    wanted = {str(e) for e in entity_ids}
    if not wanted:
        return {}
    as_of = as_of or date.today()
    rows = await conn.fetch(f"SELECT owner_entity_id::text AS o, current_balance FROM {TABLE} WHERE org_id = $1::uuid "
                            "AND valid_to IS NULL AND system_to IS NULL AND as_of <= $2", _u(org_id), as_of)
    own: dict[str, Decimal] = {}
    for r in rows:
        own[r["o"]] = own.get(r["o"], ZERO) + r["current_balance"]
    out = {e: own.get(e, ZERO) for e in wanted}
    if own:
        weights = await _ownership_weights(conn, str(org_id), set(own))
        for ancestor in wanted:
            for borrower, fraction in (weights.get(ancestor) or {}).items():
                out[ancestor] += own[borrower] * fraction
    return {e: v.quantize(CENTS, rounding=ROUND_HALF_UP) for e, v in out.items()}


def net_worth_lines(gross: Decimal, liabilities: Decimal) -> dict:
    gross = (gross or ZERO).quantize(CENTS, rounding=ROUND_HALF_UP)
    liabilities = (liabilities or ZERO).quantize(CENTS, rounding=ROUND_HALF_UP)
    return {"gross": format(gross, "f"), "liabilities": format(liabilities, "f"),
            "net": format(gross - liabilities, "f"), "currency": "USD"}


async def entity_net_worth(conn, org_id, entity_id, *, as_of: date | None = None) -> dict:
    from services.households import _sum_holdings

    as_of = as_of or date.today()
    gross = (await _sum_holdings(conn, org_id, [str(entity_id)], as_of))["total_value"]
    liab = (await lookthrough_liabilities(conn, org_id, [entity_id], as_of=as_of)).get(str(entity_id), ZERO)
    return {"entity_id": str(entity_id), "as_of": as_of.isoformat(), **net_worth_lines(gross, liab),
            "basis": "look-through holdings (entity_holdings) − look-through liabilities"}
