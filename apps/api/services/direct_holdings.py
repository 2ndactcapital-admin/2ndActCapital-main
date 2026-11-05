"""PORTENTRY1 Task 5 — direct assets (GAP-PORT-03) and private investments with commitments (GAP-PORT-04).

DIRECT ASSET — real estate, a collectible, an operating business, an insurance policy's cash value, other. One
``portfolio.assets`` row in the org (never the platform master), one PERCENT-basis position per owner (owner entity +
ownership %, ``market_value`` NULL, cost basis on the position), and ``portfolio.valuations`` rows for its value. The
rollup values a percent position as pct × the asset's resolved valuation (portfolio_rollup._position_value), so a new
valuation moves net worth with no position edit. A NEW VALUATION IS A NEW ROW (record_valuation never touches the
prior one) — the history is the list of valuations.

PRIVATE INVESTMENT — fund / direct deal / SPV / SAFE / note. An asset (``valuation_method = 'nav'``), the owner's
100%-percent position, a ``portfolio.commitments`` row (commitment amount, date, vintage), the called and distributed
to date as OPENING ledger rows on the position (``call_investment`` / ``dist_roc``, gross amount — because called /
distributed / unfunded are DERIVED from portfolio.transactions by portfolio_commitments.recompute_commitment, never
typed into the commitment), and the NAV as a valuation. ``unfunded = commitment − called + recallable`` (the module's
UNFUNDED_FORMULA) is computed by the recompute. The TA projection reads that commitment and — for a position with no
stored market value — the same ``position_current_value`` the rollup uses, i.e. the latest NAV.

Money is Decimal end to end; a float amount is refused (``_dec``). Every write calls portfolio_refresh.after_write.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from services import portfolio_assets as PA
from services import portfolio_commitments as PC
from services.portfolio_assets import TABLE_ASSETS, TABLE_POSITIONS, TABLE_VALUATIONS, _OrgWrite

DIRECT_KINDS = {"real_estate": "Real estate", "collectible": "Collectible", "operating_business": "Operating business",
                "insurance_cash_value": "Insurance policy cash value", "other": "Other"}
PRIVATE_KINDS = {"fund": "Private fund", "direct_deal": "Direct deal", "spv": "SPV", "safe": "SAFE", "note": "Note"}
VALUATION_METHODS = {"appraisal": "Appraisal", "estimate": "Estimate", "statement": "Statement"}
#: method → valuations.status (valuations_status_chk): an estimate is 'estimated', an appraisal / statement 'final'
_STATUS_OF = {"appraisal": "final", "estimate": "estimated", "statement": "final"}
_SHAPE = {"real_estate": ("hard_asset", "appraisal"), "collectible": ("hard_asset", "appraisal"),
          "operating_business": ("financial", "mark_to_model"), "insurance_cash_value": ("financial", "mark_to_model"),
          "other": ("hard_asset", "appraisal")}
HUNDRED = Decimal(100)
AUTHORITY, SOURCE = "manual", "manual"
CALL_CODE, DIST_CODE = "call_investment", "dist_roc"


class HoldingError(ValueError):
    """Refused for a reason the caller can fix (422)."""


class HoldingNotFound(LookupError):
    pass


def _dec(v, label: str, *, required=True, positive_ok_zero=True) -> Decimal | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise HoldingError(f"{label} is required")
        return None
    if isinstance(v, (bool, float)):
        raise HoldingError(f"{label}: send the amount as text (e.g. \"1250000.00\") — a float is refused")
    try:
        d = Decimal(str(v).strip().replace(",", ""))
    except InvalidOperation:
        raise HoldingError(f"{label}: '{v}' is not a number") from None
    if not d.is_finite() or d < 0 or (d == 0 and not positive_ok_zero):
        raise HoldingError(f"{label}: a non-negative number")
    return d


def _day(v, label: str, *, required=True) -> date | None:
    if v in (None, ""):
        if required:
            raise HoldingError(f"{label} is required")
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        raise HoldingError(f"{label}: a date as YYYY-MM-DD") from None


async def _entity(conn, org_id, entity_id) -> dict:
    async with _OrgWrite(conn, str(org_id)) as c:
        r = await c.fetchrow("SELECT id, display_name, entity_type::text AS t FROM entities WHERE id = $1::uuid AND "
                             "org_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL", str(entity_id), str(org_id))
    if r is None:
        raise HoldingNotFound("Entity not found")
    return dict(r)


def _valuation_in(v: dict | None) -> dict:
    v = v or {}
    method = (v.get("method") or "").strip().lower()
    if method not in VALUATION_METHODS:
        raise HoldingError(f"valuation method is one of {', '.join(VALUATION_METHODS)}")
    return {"value": _dec(v.get("value"), "valuation value"), "date": _day(v.get("date"), "valuation date"),
            "method": method, "source": (v.get("source") or "").strip()[:300] or None}


async def _record_value(conn, org_id, asset_id, val: dict, *, nav: bool = False) -> str:
    return await PA.record_valuation(
        conn, org_id=str(org_id), asset_id=str(asset_id), valuation_date=val["date"], value=val["value"],
        value_basis=PA.TOTAL, status="final" if nav else _STATUS_OF[val["method"]], purpose="market",
        currency_code="USD", valuation_method="statement" if nav else val["method"],
        valuation_source=val.get("source"))


# ── Direct assets ───────────────────────────────────────────────────────────────────────────────────────────────
async def add_direct_asset(pool, *, org_id, actor_id, entity_id, data: dict) -> dict:
    kind = (data.get("kind") or "").strip()
    if kind not in DIRECT_KINDS:
        raise HoldingError(f"kind is one of {', '.join(DIRECT_KINDS)}")
    name = (data.get("name") or "").strip()
    if not name:
        raise HoldingError("name is required")
    owners = data.get("owners") or [{"entity_id": str(entity_id), "ownership_pct": data.get("ownership_pct") or "100"}]
    plan, total = [], Decimal(0)
    for o in owners:
        pct = _dec(o.get("ownership_pct"), "ownership %", positive_ok_zero=False)
        if pct > HUNDRED:
            raise HoldingError("ownership % is at most 100")
        total += pct
        plan.append((str(o.get("entity_id")), pct, _dec(o.get("cost_basis"), "cost basis", required=False)))
    if total > HUNDRED:
        raise HoldingError(f"the owners' percentages add to {total}, more than 100")
    if not any(e == str(entity_id) for e, _, _ in plan):
        raise HoldingError("the record the asset is added on must be one of its owners")
    val = _valuation_in(data.get("valuation"))
    cost = _dec(data.get("cost_basis"), "cost basis", required=False)
    aclass, vmethod = _SHAPE[kind]
    async with pool.acquire() as conn:
        for e, _, _ in plan:
            await _entity(conn, org_id, e)
        async with conn.transaction():
            aid = await PA.create_asset(conn, org_id=str(org_id), name=name, asset_type=kind, asset_class=aclass,
                                        ownership_basis=PA.PERCENT, valuation_method=vmethod,
                                        default_taxonomy_key=(data.get("taxonomy_key") or None), currency_code="USD",
                                        include_in_performance=False)
            async with _OrgWrite(conn, str(org_id)) as c:
                await c.execute(f"UPDATE {TABLE_ASSETS} SET notes = $2, manual_terms = $3::jsonb WHERE id = $1::uuid "
                                "AND system_to IS NULL", aid, (data.get("notes") or "").strip() or None,
                                '{"sub_class": %s}' % _json_str(data.get("sub_class")))
            pids = []
            for e, pct, ocost in plan:
                own_cost = ocost if ocost is not None else (cost * pct / HUNDRED if cost is not None else None)
                pids.append(await PA.create_position(
                    conn, org_id=str(org_id), owner_entity_id=e, asset_id=aid, as_of_date=val["date"],
                    authority=AUTHORITY, source_system=SOURCE, ownership_basis=PA.PERCENT, ownership_pct=pct,
                    cost_basis=own_cost, taxonomy_key=(data.get("taxonomy_key") or None)))
            vid = await _record_value(conn, org_id, aid, val)
    from services import portfolio_refresh as PR

    refresh = await PR.after_write(pool, org_id, [e for e, _, _ in plan], reason=f"direct asset {aid}",
                                   actor_id=actor_id)
    return {"asset_id": aid, "position_ids": pids, "valuation_id": vid, "rollup": _brief(refresh)}


def _json_str(v) -> str:
    import json

    return json.dumps((v or "").strip() or None) if isinstance(v, (str, type(None))) else json.dumps(None)


def _brief(r: dict) -> dict:
    ran = (r or {}).get("ran") or {}
    return {"request_id": (r or {}).get("id"), "status": ran.get("status") or ("queued" if r else "not queued"),
            "as_of_date": ran.get("as_of_date")}


async def add_valuation(pool, *, org_id, actor_id, asset_id, data: dict, nav: bool = False) -> dict:
    val = _valuation_in({**data, "method": data.get("method") or ("statement" if nav else None)})
    async with pool.acquire() as conn:
        async with _OrgWrite(conn, str(org_id)) as c:
            a = await c.fetchrow(f"SELECT id FROM {TABLE_ASSETS} WHERE id = $1::uuid AND org_id = $2::uuid "
                                 "AND global_security_id IS NULL AND valid_to IS NULL AND system_to IS NULL",
                                 str(asset_id), str(org_id))
            owners = [r["owner_entity_id"] for r in await c.fetch(
                f"SELECT DISTINCT owner_entity_id FROM {TABLE_POSITIONS} WHERE asset_id = $1::uuid "
                "AND valid_to IS NULL AND system_to IS NULL", str(asset_id))]
        if a is None:
            raise HoldingNotFound("Asset not found")
        vid = await _record_value(conn, org_id, asset_id, val, nav=nav)
    from services import portfolio_refresh as PR

    refresh = await PR.after_write(pool, org_id, owners, reason=f"valuation {vid}", actor_id=actor_id)
    return {"valuation_id": vid, "asset_id": str(asset_id), "rollup": _brief(refresh)}


async def _valuations(c, asset_id) -> list[dict]:
    rows = await c.fetch(
        f"SELECT id::text, valuation_date, value, status, valuation_method, valuation_source, system_from "
        f"FROM {TABLE_VALUATIONS} WHERE asset_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
        f"ORDER BY valuation_date DESC, system_from DESC", str(asset_id))
    return [{"id": r["id"], "date": r["valuation_date"].isoformat(), "value": str(r["value"]), "status": r["status"],
             "method": r["valuation_method"], "source": r["valuation_source"]} for r in rows]


async def list_direct_assets(conn, org_id, entity_ids) -> list[dict]:
    from services.portfolio_rollup import position_current_value

    out = []
    async with _OrgWrite(conn, str(org_id)) as c:
        rows = await c.fetch(
            f"SELECT p.id::text AS position_id, p.owner_entity_id::text AS owner_entity_id, p.ownership_pct, "
            f"p.cost_basis, p.ownership_basis, p.quantity, p.market_value, a.id::text AS asset_id, a.name, "
            f"a.asset_type, a.default_taxonomy_key, a.notes, a.manual_terms, e.display_name AS owner_name "
            f"FROM {TABLE_POSITIONS} p JOIN {TABLE_ASSETS} a ON a.id = p.asset_id AND a.valid_to IS NULL "
            f"AND a.system_to IS NULL JOIN entities e ON e.id = p.owner_entity_id AND e.valid_to IS NULL "
            f"AND e.system_to IS NULL WHERE p.org_id = $1::uuid AND p.owner_entity_id = ANY($2::uuid[]) "
            f"AND a.asset_type = ANY($3::text[]) AND p.valid_to IS NULL AND p.system_to IS NULL ORDER BY a.name",
            str(org_id), [str(e) for e in entity_ids], list(DIRECT_KINDS))
        for r in rows:
            share, _ = await position_current_value(c, str(org_id), dict(r), None)
            hist = await _valuations(c, r["asset_id"])
            out.append({"position_id": r["position_id"], "asset_id": r["asset_id"], "name": r["name"],
                        "kind": r["asset_type"], "owner_entity_id": r["owner_entity_id"],
                        "owner_name": r["owner_name"], "ownership_pct": str(r["ownership_pct"]),
                        "cost_basis": None if r["cost_basis"] is None else str(r["cost_basis"]),
                        "taxonomy_key": r["default_taxonomy_key"], "notes": r["notes"],
                        "asset_value": hist[0]["value"] if hist else None,
                        "owner_value": None if share is None else str(share.quantize(Decimal("0.01"))),
                        "valuations": hist})
    return out


# ── Private investments ─────────────────────────────────────────────────────────────────────────────────────────
async def add_private_investment(pool, *, org_id, actor_id, entity_id, data: dict) -> dict:
    kind = (data.get("kind") or "").strip()
    if kind not in PRIVATE_KINDS:
        raise HoldingError(f"kind is one of {', '.join(PRIVATE_KINDS)}")
    name = (data.get("name") or "").strip()
    if not name:
        raise HoldingError("name is required")
    c_in = data.get("commitment") or {}
    amount = _dec(c_in.get("amount"), "commitment amount")
    c_date = _day(c_in.get("date"), "commitment date")
    vintage = c_in.get("vintage_year")
    if vintage is not None and not (isinstance(vintage, int) and 1900 <= vintage <= 2200):
        raise HoldingError("vintage year is a year, e.g. 2021")
    called = _dec(data.get("called_to_date"), "called to date", required=False) or Decimal(0)
    distributed = _dec(data.get("distributed_to_date"), "distributed to date", required=False) or Decimal(0)
    if called > amount:
        raise HoldingError(f"called to date ({called}) is more than the commitment ({amount})")
    nav_in = data.get("nav")
    nav = None
    if nav_in:
        nav = {"value": _dec(nav_in.get("value"), "NAV"), "date": _day(nav_in.get("date"), "NAV date"),
               "method": "statement", "source": (nav_in.get("source") or "").strip()[:300] or None}
    flow_day = _day(data.get("as_of"), "as-of", required=False) or (nav["date"] if nav else c_date)
    async with pool.acquire() as conn:
        await _entity(conn, org_id, entity_id)
        async with conn.transaction():
            aid = await PA.create_asset(conn, org_id=str(org_id), name=name, asset_type=f"private_{kind}",
                                        asset_class="financial", ownership_basis=PA.PERCENT, valuation_method="nav",
                                        default_taxonomy_key=(data.get("taxonomy_key") or None), currency_code="USD")
            pid = await PA.create_position(
                conn, org_id=str(org_id), owner_entity_id=str(entity_id), asset_id=aid, as_of_date=flow_day,
                authority=AUTHORITY, source_system=SOURCE, ownership_basis=PA.PERCENT, ownership_pct=HUNDRED,
                cost_basis=called if called else None, taxonomy_key=(data.get("taxonomy_key") or None))
            cid = await PC.create_commitment(conn, org_id=str(org_id), position_id=pid, commitment_amount=amount,
                                             commitment_date=c_date, vintage_year=vintage)
            for code, amt in ((CALL_CODE, called), (DIST_CODE, distributed)):
                if amt:
                    await PA.record_transaction(conn, org_id=str(org_id), position_id=pid,
                                                transaction_type_code=code, trade_date=flow_day, authority=AUTHORITY,
                                                source_system=SOURCE, gross_amount=amt, currency_code="USD",
                                                external_ref="opening balance (to date)")
            if nav:
                await _record_value(conn, org_id, aid, nav, nav=True)
            totals = await PC.recompute_commitment(conn, str(org_id), cid)
    from services import portfolio_refresh as PR

    refresh = await PR.after_write(pool, org_id, [entity_id], reason=f"private investment {cid}", actor_id=actor_id)
    return {"asset_id": aid, "position_id": pid, "commitment_id": cid, "unfunded": str(totals.unfunded),
            "called_to_date": str(totals.called_to_date), "distributed_to_date": str(totals.distributed_to_date),
            "rollup": _brief(refresh)}


async def add_flow(pool, *, org_id, actor_id, commitment_id, data: dict) -> dict:
    kind = (data.get("type") or "").strip()
    code = {"call": CALL_CODE, "distribution": DIST_CODE}.get(kind)
    if code is None:
        raise HoldingError("type is call or distribution")
    amt = _dec(data.get("amount"), "amount", positive_ok_zero=False)
    day = _day(data.get("date"), "date")
    async with pool.acquire() as conn:
        c = await PC.get_commitment(conn, org_id=str(org_id), commitment_id=str(commitment_id))
        if c is None:
            raise HoldingNotFound("Commitment not found")
        async with conn.transaction():
            await PA.record_transaction(conn, org_id=str(org_id), position_id=str(c["position_id"]),
                                        transaction_type_code=code, trade_date=day, authority=AUTHORITY,
                                        source_system=SOURCE, gross_amount=amt, currency_code="USD")
            totals = await PC.recompute_commitment(conn, str(org_id), str(commitment_id))
        async with _OrgWrite(conn, str(org_id)) as w:
            owner = await w.fetchval(f"SELECT owner_entity_id FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
                                     str(c["position_id"]))
    from services import portfolio_refresh as PR

    refresh = await PR.after_write(pool, org_id, [owner], reason=f"commitment flow {commitment_id}", actor_id=actor_id)
    return {"commitment_id": str(commitment_id), "unfunded": str(totals.unfunded),
            "called_to_date": str(totals.called_to_date), "distributed_to_date": str(totals.distributed_to_date),
            "rollup": _brief(refresh)}


async def list_private_investments(conn, org_id, entity_ids) -> list[dict]:
    from services.portfolio_rollup import position_current_value

    out = []
    async with _OrgWrite(conn, str(org_id)) as c:
        rows = await c.fetch(
            f"SELECT cm.id::text AS commitment_id, cm.commitment_amount, cm.commitment_date, cm.vintage_year, "
            f"cm.called_to_date, cm.distributed_to_date, cm.recallable_amount, cm.unfunded, p.id::text AS position_id, "
            f"p.owner_entity_id::text AS owner_entity_id, p.ownership_basis, p.ownership_pct, p.quantity, "
            f"p.market_value, a.id::text AS asset_id, a.name, a.asset_type, e.display_name AS owner_name "
            f"FROM portfolio.commitments cm JOIN {TABLE_POSITIONS} p ON p.id = cm.position_id AND p.valid_to IS NULL "
            f"AND p.system_to IS NULL JOIN {TABLE_ASSETS} a ON a.id = p.asset_id AND a.valid_to IS NULL "
            f"AND a.system_to IS NULL JOIN entities e ON e.id = p.owner_entity_id AND e.valid_to IS NULL "
            f"AND e.system_to IS NULL WHERE cm.org_id = $1::uuid AND p.owner_entity_id = ANY($2::uuid[]) "
            f"AND cm.valid_to IS NULL AND cm.system_to IS NULL ORDER BY a.name",
            str(org_id), [str(e) for e in entity_ids])
        for r in rows:
            nav, _ = await position_current_value(c, str(org_id), dict(r), None)
            out.append({"commitment_id": r["commitment_id"], "asset_id": r["asset_id"], "name": r["name"],
                        "kind": r["asset_type"].removeprefix("private_"), "owner_entity_id": r["owner_entity_id"],
                        "owner_name": r["owner_name"],
                        **{k: (None if r[k] is None else str(r[k])) for k in
                           ("commitment_amount", "called_to_date", "distributed_to_date", "recallable_amount",
                            "unfunded")},
                        "commitment_date": r["commitment_date"].isoformat() if r["commitment_date"] else None,
                        "vintage_year": r["vintage_year"],
                        "nav": None if nav is None else str(nav.quantize(Decimal("0.01"))),
                        "valuations": await _valuations(c, r["asset_id"])})
    return out


def vocab() -> dict:
    pairs = lambda d: [{"value": k, "label": v} for k, v in d.items()]  # noqa: E731
    return {"direct_asset_kinds": pairs(DIRECT_KINDS), "private_kinds": pairs(PRIVATE_KINDS),
            "valuation_methods": pairs(VALUATION_METHODS),
            "unfunded_formula": PC.UNFUNDED_FORMULA,
            "flow_types": [{"value": "call", "label": "Capital call"}, {"value": "distribution", "label": "Distribution"}]}
