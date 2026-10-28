"""Options proposals (rep8) — through rep5's proposal table, submit and
decide; manual execution instructions per leg.

GENERATE — a person's proposal (``proposal_kind 'options'``, trigger
'manual', the person its maker) for ONE account that follows the package's
model: 'open' the approved package's option legs, 'close' a package's legs
the account holds, or 'roll' (close the expiring package's legs, open the
approved next package's). Sizing: contracts = the account's core value ×
the leg ratio ÷ (multiplier × start level), rounded half-even; outside the
hedge-ratio tolerance is a warning; zero contracts is refused (below the
minimum account). Each line's price is the leg contract's latest adviser
PREMIUM quote (source 'adviser_quote', its as-of) or "not priced"; an
unpriced line cannot be approved. Authority: MANAGED only (rep7b path
'options'): read-only and advise accounts never get an options sleeve.

APPROVAL — rep5's maker-checker task (approve_proposals, never the maker).
The options TAX GATE runs at generate (shown) and again as the approval
task's pre-check: any BLOCK refuses the decision (409, the rule named) and
the task stays open (services.options_tax_gate).

EXECUTE — never routed to an adapter: every leg becomes a MANUAL execution
instruction (``routed_via 'manual'``, a CHECK makes option terms manual-only)
for the custodian desk, a broker or a trader: contracts (not shares), a
LIMIT price from the adviser's quote with its as-of as the reference, the
expiry, and the FLEX terms when FLEX (``execution_instructions.option_terms``).
The proposal's maker / checker carry to the instructions (rep5's upstream
approval). Fills are entered on rep2's flow; :func:`services.options_sleeve.
book_fills` books the option lots.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal

from services import account_models as AM
from services import held_away as HA
from services import human_tasks as HT
from services import options_sleeve as OS
from services import rebalance_proposals as RP

ACTIONS = ("open", "close", "roll")
ACTION_LABELS = {"open": "Open the package's option legs", "close": "Close the package's option legs",
                 "roll": "Roll: close the expiring legs, open the next package's"}
EXECUTORS = ("custodian_desk", "broker", "trader")
WARNING_LABELS = {"outside_hedge_tolerance": "Contracts outside the hedge-ratio tolerance",
                  "not_priced": "Not priced — enter a premium quote", "leg_not_held": "A leg to close is not held",
                  "manual_only": "Manual execution only"}
ZERO, ONE = Decimal(0), Decimal(1)


def _side(direction: str, effect: str) -> str:
    """long open = buy; short open = sell (to open); closing reverses."""
    if effect == "open":
        return "buy" if direction == "long" else "sell"
    return "sell" if direction == "long" else "buy"


async def _line(conn, org_id, leg: dict, contract: dict, *, effect: str, contracts: Decimal, package: dict,
                reason: str) -> dict:
    q = await OS.latest_quote(conn, org_id, contract["global_security_id"], "premium")
    ol = {"role": leg["role"], "leg_id": str(leg["id"]), "package_id": str(package["id"]),
          "package_no": package["package_no"], "contract_id": str(contract["id"]), "position_effect": effect,
          "direction": leg["direction"], "option_type": contract["option_type"], "contracts": OS.dstr(contracts),
          "multiplier": OS.dstr(OS.D(contract["multiplier"])), "strike": OS.dstr(OS.D(contract["strike"])),
          "strike_pct": OS.dstr(OS.D(leg["strike_pct"])), "expiry": contract["expiry"].isoformat(),
          "exercise_style": contract["exercise_style"], "settlement": contract["settlement"],
          "is_flex": contract["is_flex"], "occ_symbol": contract["occ_symbol"],
          "quote_id": str(q["id"]) if q else None, "quote_as_of": q["as_of"].isoformat() if q else None,
          "quote_source": q["source"] if q else None, "quote_value": OS.dstr(OS.D(q["value"])) if q else None}
    return {"global_security_id": contract["global_security_id"], "side": _side(leg["direction"], effect),
            "quantity": contracts, "price": OS.D(q["value"]) if q else None,
            "price_source": "adviser_quote" if q else "none",
            "price_as_of": q["as_of"].astimezone(timezone.utc).date() if q else None, "reason": reason,
            "option_leg": ol}


async def _held_contracts(conn, org_id, account_id, contract_gsid) -> Decimal:
    v = await conn.fetchval(
        """SELECT p.quantity FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id
               AND a.system_to IS NULL
           WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid AND a.global_security_id = $3::uuid
             AND p.valid_to IS NULL AND p.system_to IS NULL ORDER BY p.as_of_date DESC, p.valid_from DESC LIMIT 1""",
        str(org_id), str(account_id), str(contract_gsid))
    return OS.D(v) or ZERO


async def generate(pool, *, org_id, account_id, package_id, maker_id, action: str = "open",
                   executor_type: str | None = None, executor_name: str | None = None,
                   executor_contact: str | None = None, today: date | None = None) -> dict:
    from services import authority as AU

    if action not in ACTIONS:
        raise RP.ProposalError(f"action is one of {list(ACTIONS)}")
    if executor_type is not None and executor_type not in EXECUTORS:
        raise RP.ProposalError(f"an options leg is executed by {' / '.join(EXECUTORS)} — never an adapter")
    today = today or date.today()
    async with RP.account_lock(pool, RP._uuid(account_id, "account")):
        async with pool.acquire() as conn:
            maker = await HT.assert_human(conn, maker_id, org_id)
            account = await AM.load_account(conn, org_id, account_id)
            if AM.is_closed(account, today):
                raise RP.ProposalConflict(f"account {account['account_number_masked']} is closed")
            HA.refuse(account, "an options proposal", RP.ProposalConflict, path="options")      # managed only
            try:
                pkg = await OS.load_package(conn, org_id, package_id)
            except OS.OptionsNotFound as exc:
                raise RP.ProposalNotFound(str(exc)) from None
            link = await AM.active_link(conn, account["id"])
            if link is None or str(link["model_id"]) != str(pkg["model_id"]):
                raise RP.ProposalConflict(f"{account['account_number_masked']} does not follow this options model — "
                                          "join the model first")
            if await RP.open_proposal(conn, account["id"]):
                raise RP.ProposalConflict("the account already has an open proposal — finish or let it expire first")
            e = AU.effective(account)
            ex = executor_type or ("broker" if e["custody"] == "other_custodian" else "custodian_desk")
            if not AU.path_allowed(account, "instructions", ex):
                raise RP.ProposalConflict(AU.refusal(account, "an options instruction", path="instructions",
                                                     executor=ex) or "executor not allowed")
            if ex in ("broker", "trader") and not (executor_name or "").strip() and e["custody"] != "other_custodian":
                raise RP.ProposalError("name the broker or trader who carries out the options legs")
            opening, closing = [], []
            if action in ("open", "roll"):
                if pkg["status"] != "approved":
                    raise RP.ProposalConflict(f"package #{pkg['package_no']} is {pkg['status']} — only an approved "
                                              "package is opened")
                opening = pkg
            if action == "close":
                closing = pkg
            if action == "roll":
                if not pkg["rolled_from_id"]:
                    raise RP.ProposalConflict("a roll opens a package that rolls an earlier one")
                closing = await OS.load_package(conn, org_id, pkg["rolled_from_id"])
            hold = await AM.load_holdings(conn, org_id, account["id"])
            opt = set((await OS.contracts_by_security(conn, [h.key for h in hold["holdings"]])).keys())
            core = [h for h in hold["holdings"] if h.key not in opt and h.quantity > 0]
            core_value = sum((h.market_value for h in core if h.market_value is not None), ZERO)
            unvalued = [h.label for h in core if h.market_value is None]
            tol = await OS._setting(conn, org_id, OS.TOLERANCE_SETTING, OS.DEFAULT_TOLERANCE, lo=Decimal("0.0001"),
                                    hi=Decimal("0.5"))
            lines, warnings = [], []
            if closing:
                for leg in await OS.legs_of(conn, closing["id"]):
                    if leg["role"] == "underlying" or not leg["contract_id"]:
                        continue
                    c = await OS.load_contract(conn, contract_id=leg["contract_id"])
                    held = await _held_contracts(conn, org_id, account["id"], c["global_security_id"])
                    if held == 0:
                        warnings.append({"code": "leg_not_held", "message": f"{OS.LEG_ROLE_LABELS[leg['role']]}: "
                                         "not held in this account — nothing to close"})
                        continue
                    lines.append(await _line(conn, org_id, {**leg, "direction": "long" if held > 0 else "short"}, c,
                                             effect="close", contracts=abs(held), package=closing,
                                             reason=f"close {OS.LEG_ROLE_LABELS[leg['role']].lower()} "
                                                    f"(package #{closing['package_no']})"))
            if opening:
                if core_value <= 0:
                    raise RP.ProposalConflict("cannot size the options sleeve: the account's core holdings carry no "
                                              "valuation" + (f" ({', '.join(unvalued[:3])})" if unvalued else ""))
                start = OS.D(opening["start_level"])
                for leg in await OS.legs_of(conn, opening["id"]):
                    if leg["role"] == "underlying":
                        continue
                    c = await OS.load_contract(conn, contract_id=leg["contract_id"])
                    per = OS.D(c["multiplier"]) * start
                    exact = core_value * OS.D(leg["ratio"]) / per
                    n = exact.to_integral_value(rounding=ROUND_HALF_EVEN)
                    if n <= 0:
                        raise RP.ProposalConflict(f"the account is below the minimum for this sleeve: {OS.dstr(exact)} "
                                                  f"contracts of {OS.LEG_ROLE_LABELS[leg['role']].lower()} round to "
                                                  "zero — see the minimum account")
                    target = core_value * OS.D(leg["ratio"])
                    err = abs(n * per - target) / target
                    if err > tol:
                        warnings.append({"code": "outside_hedge_tolerance",
                                         "message": f"{OS.LEG_ROLE_LABELS[leg['role']]}: {n} contracts cover "
                                                    f"{OS.dstr((n * per / target).quantize(Decimal('0.0001')))}× the "
                                                    f"target — outside the {OS.dstr(tol * 100)}% tolerance"})
                    lines.append(await _line(conn, org_id, leg, c, effect="open", contracts=n, package=opening,
                                             reason=f"open {OS.LEG_ROLE_LABELS[leg['role']].lower()} "
                                                    f"(package #{opening['package_no']}): {OS.dstr(exact)} → {n} "
                                                    "contracts on the core value"))
            if not lines:
                raise RP.ProposalConflict("nothing to trade for this action")
            if any(x["price"] is None for x in lines):
                warnings.append({"code": "not_priced", "message": "a leg has no premium quote: not priced — enter a "
                                 "quote; the proposal cannot be approved until every leg is priced"})
            via = await OS.options_viability(conn, account.get("custodian_code"), pkg["kind"])
            warnings.append({"code": "manual_only", "message": via["note"]})
            expiry_days = await RP._setting(conn, org_id, RP.EXPIRY_SETTING, RP.DEFAULT_EXPIRY_DAYS, lo=1, hi=60,
                                            integer=True)
        now = datetime.now(timezone.utc)
        ref = {"option_package_id": str(pkg["id"]), "action": action,
               "closing_package_id": str(closing["id"]) if closing else None,
               "executor": {"type": ex, "name": (executor_name or "").strip() or None,
                            "contact": (executor_contact or "").strip() or None},
               "core_value": str(core_value), "viability": via}
        async with pool.acquire() as conn:
            async with conn.transaction():
                pid = await conn.fetchval(
                    f"""INSERT INTO {RP.T_P} (org_id, account_id, link_id, model_id, model_version_id, trigger,
                            trigger_ref, status, advice_only, valuation_as_of, account_value, cash_value, warnings,
                            expires_at, maker_kind, maker_id, proposal_kind)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, 'manual', $6::jsonb, 'draft', false,
                                $7, $8, $9, $10::jsonb, $11, 'user', $12::uuid, 'options') RETURNING id""",
                    str(org_id), str(account["id"]), str(link["id"]), str(pkg["model_id"]),
                    str(pkg["model_version_id"]) if pkg["model_version_id"] else None, RP._dumps(ref),
                    hold["valuation_as_of"], core_value + hold["cash"], hold["cash"], RP._dumps(warnings),
                    now + timedelta(days=expiry_days), str(maker["id"]))
                for i, x in enumerate(lines, 1):
                    await conn.execute(
                        f"""INSERT INTO {RP.T_T} (org_id, proposal_id, line_no, global_security_id, side, quantity,
                                price, price_source, price_as_of, lots, reason, option_leg)
                            VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7, $8, $9, '[]'::jsonb, $10, $11::jsonb)""",
                        str(org_id), pid, i, str(x["global_security_id"]), x["side"], x["quantity"], x["price"],
                        x["price_source"], x["price_as_of"], x["reason"], RP._dumps(x["option_leg"]))
                await RP._audit(conn, org_id, maker["id"], "generated", pid,
                                {"kind": "options", "action": action, "package_id": str(pkg["id"]),
                                 "lines": len(lines)})
    from services import options_tax_gate as G

    gate = await G.run_gate(pool, org_id=org_id, proposal_id=pid, actor_id=maker["id"], today=today)
    await RP._publish(pool, org_id, "generated", pid, {"kind": "options", "action": action}, maker["id"])
    async with pool.acquire() as conn:
        out = await RP.detail(conn, org_id, pid)
    return {**out, "gate": gate}


async def execute(pool, *, org_id, p: dict, actor: dict) -> dict:
    """An APPROVED options proposal → one MANUAL instruction per leg. Called by
    rep5's execute; never reaches a trade adapter."""
    from services import execution as X
    from services.custody.trading import PriceGuidance, TradeLine, TradeList

    async with pool.acquire() as conn:
        account = await X.load_account(conn, org_id, p["account_id"])
        acct = await AM.load_account(conn, org_id, p["account_id"])
        HA.refuse(acct, "routing an options proposal to instructions", RP.ProposalConflict, path="options")
        lines = await RP.trades_of(conn, p["id"])
        within = await RP._setting(conn, org_id, RP.EXECUTE_WITHIN_SETTING, RP.DEFAULT_EXECUTE_WITHIN, lo=0, hi=90,
                                   integer=True)
    ref = RP._loads(p["trigger_ref"]) or {}
    ex = ref.get("executor") or {}
    trades = []
    for t in lines:
        ol = RP._loads(t.get("option_leg")) or {}
        if t["price"] is None or not ol.get("quote_as_of"):
            raise RP.ProposalConflict(f"line {t['line_no']} is not priced — an options instruction carries a limit "
                                      "price from an adviser quote")
        at = datetime.fromisoformat(ol["quote_as_of"])
        price = Decimal(str(t["price"]))
        trades.append(TradeLine(global_security_id=t["global_security_id"], side=t["side"],
                                quantity=Decimal(str(t["quantity"])),
                                price_guidance=PriceGuidance(kind="limit", limit_price=price, reference_price=price,
                                                             reference_at=at),
                                option_leg={**ol, "limit_from_quote": ol.get("quote_id"),
                                            "unit": "contracts"}))
    note = (ref.get("viability") or {}).get("note") or "manual execution only"
    tl = TradeList(account_id=p["account_id"], trades=trades, executor_type=ex.get("type") or "custodian_desk",
                   executor_name=ex.get("name"), executor_contact=ex.get("contact"),
                   execute_by=date.today() + timedelta(days=within), if_not_filled="ask", source="proposal",
                   source_ref=p["id"],
                   notes=f"From options proposal {p['id']} ({ref.get('action')}): quantities are CONTRACTS; limit "
                         f"prices are adviser-entered premium quotes (as-of on each line); {note}")
    upstream = {"proposal_id": str(p["id"]), "maker_id": str(p["maker_id"]), "checker_id": str(p["checker_id"])}
    rows = await X.create_instructions(
        pool, org_id=org_id, actor_id=actor["id"], account=account, trade_list=tl, routed_via="manual",
        routing_reason=f"options legs are manual execution instructions — never an adapter ({note})",
        upstream=upstream)
    ids = [str(r["id"]) for r in rows]
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            for t, iid in zip(lines, ids):
                await conn.execute(f"UPDATE {RP.T_T} SET valid_to = now() WHERE id = $1 AND valid_to IS NULL", t["id"])
                await conn.execute(
                    f"""INSERT INTO {RP.T_T} (org_id, proposal_id, line_no, global_security_id, side, quantity,
                            notional, price, price_source, price_as_of, lots, est_st_gain, est_lt_gain, reason,
                            wash_sale_flag, wash_sale, instruction_id, lot_method, option_leg)
                        SELECT org_id, proposal_id, line_no, global_security_id, side, quantity, notional, price,
                               price_source, price_as_of, lots, est_st_gain, est_lt_gain, reason, wash_sale_flag,
                               wash_sale, $2::uuid, lot_method, option_leg FROM {RP.T_T} WHERE id = $1""",
                    t["id"], iid)
            new = await RP._update(conn, p["id"], {"execution": {
                "instruction_ids": ids, "routing": {"routed_to": "manual", "adapter": None, "reason": note},
                "executor_type": tl.executor_type, "starts": "approved", "notes": [note],
                "routed_at": now.isoformat(), "routed_by": str(actor["id"])}})
            await RP._audit(conn, org_id, actor["id"], "routed", p["id"], {"instruction_ids": ids,
                                                                         "routed_to": "manual", "kind": "options"})
    await RP._publish(pool, org_id, "routed", p["id"], {"instruction_ids": ids}, actor["id"])
    return new


def vocabularies() -> dict:
    return {"option_actions": [{"key": k, "label": v} for k, v in ACTION_LABELS.items()],
            "option_executors": [{"key": k, "label": k.replace("_", " ").capitalize()} for k in EXECUTORS],
            "option_warnings": [{"key": k, "label": v} for k, v in WARNING_LABELS.items()]}

