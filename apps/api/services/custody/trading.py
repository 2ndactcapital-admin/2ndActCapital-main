"""Trade adapters — ALONGSIDE (not inside) the CSV ingestion interface (rep2).

``CustodyAdapter`` (base.py) reads a custodian's export; nothing about it
changes. A ``TradeAdapter`` is the other direction — how a trade list for one
account reaches whoever executes it — and it is registered per custodian in
the SAME registry module (``registry.register_trade_adapter``), keyed by the
lower-case custodian route key the capabilities table uses.

THE CUSTODY CLIFF. Trade execution never gets an agent, and in this build
nothing at all is sent to a custodian: there is no live Altruist credential and
no trading client. So:

* :class:`ManualTradeAdapter` turns every trade into a DRAFT execution
  instruction (services.execution) and returns their ids. Nothing leaves the
  system; a person approves it, sends it, and enters the fills.
* :class:`AltruistTradeAdapter` declares ``equity_trading_api`` from the
  capabilities table. While that is not an evidenced 'yes' (it is 'unknown'
  today) it routes to the manual adapter and SAYS SO in its result. Even at
  'yes' it routes manual in this build, because no trading client exists — no
  HTTP code for trading is written here.
* :func:`route_trade_list` picks the adapter from the account's custodian_code
  + capabilities. A held-away account always goes manual.
* cpsim03 — the ONE exception: an account of a SIMULATED Altruist-compatible
  custodian (services.custodian_execution.simulator_route, D2 — every condition,
  the real Altruist route never) gets the same DRAFT instructions with
  ``routed_via='custodian_api'``. Nothing is sent at submit (``sent_externally``
  stays False); after the unchanged maker-checker approval a PERSON presses
  "Send to custodian". The real Altruist route is unchanged (manual).

A missing capability DOWNGRADES (manual instruction, with a note per trade);
it never blocks the list.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── The typed trade list ────────────────────────────────────────────────────


def _decimal(v: Any, name: str) -> Decimal | None:
    """Decimal in, Decimal out — a JSON float is REFUSED (mode='before', so
    pydantic never gets the chance to coerce a binary float first)."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(v, float):
        raise ValueError(f"{name} must be sent as a decimal string (e.g. \"10.25\"), not a binary float")
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, str):
        try:
            d = Decimal(v.strip().replace(",", ""))
        except InvalidOperation:
            raise ValueError(f"{name} is not a number: {v!r}") from None
        if not d.is_finite():
            raise ValueError(f"{name} must be finite")
        return d
    raise ValueError(f"{name} must be a decimal string")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LotInstruction(_Strict):
    tax_lot_id: UUID
    quantity: Decimal

    @field_validator("quantity", mode="before")
    @classmethod
    def _q(cls, v):
        d = _decimal(v, "lot quantity")
        if d is None or d <= 0:
            raise ValueError("lot quantity must be greater than zero")
        return d


class PriceGuidance(_Strict):
    kind: Literal["market", "limit", "range"] = "market"
    limit_price: Decimal | None = None
    range_low: Decimal | None = None
    range_high: Decimal | None = None
    reference_price: Decimal | None = None
    reference_at: datetime | None = None

    @field_validator("limit_price", "range_low", "range_high", "reference_price", mode="before")
    @classmethod
    def _p(cls, v, info):
        return _decimal(v, info.field_name)

    @model_validator(mode="after")
    def _shape(self):
        if self.kind == "market":
            if any(x is not None for x in (self.limit_price, self.range_low, self.range_high)):
                raise ValueError("an at-market instruction carries no limit or range")
        elif self.kind == "limit":
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError("a limit instruction needs a limit price above zero")
            if self.range_low is not None or self.range_high is not None:
                raise ValueError("a limit instruction carries no range")
        else:
            if None in (self.range_low, self.range_high, self.reference_price, self.reference_at):
                raise ValueError("a range needs low, high, the reference price and the time of that price")
            if self.range_low <= 0 or self.range_high < self.range_low or self.reference_price <= 0:
                raise ValueError("a range needs 0 < low <= high and a reference price above zero")
            if self.limit_price is not None:
                raise ValueError("a range instruction carries no limit price")
        return self


class TradeLine(_Strict):
    global_security_id: UUID
    #: rep13 (additive): 'sell_short' opens a short, 'buy_to_cover' closes one — by quantity only, with short_terms
    side: Literal["buy", "sell", "sell_short", "buy_to_cover"]
    quantity: Decimal | None = None
    notional: Decimal | None = None
    lot_instructions: list[LotInstruction] = Field(default_factory=list, max_length=200)
    price_guidance: PriceGuidance = Field(default_factory=PriceGuidance)
    wash_sale_notes: str | None = Field(default=None, max_length=4000)
    #: rep8 (additive): an option leg's terms — contracts, position effect (open / close), expiry, FLEX terms, the
    #: adviser quote behind its limit price. Only services.options_proposals sets it; a CHECK makes it manual-only.
    option_leg: dict | None = None
    #: rep13 (additive): a short's terms — position effect (open / close), the short lots a cover closes, the
    #: entered borrow rate. Required on a short side, refused on buy / sell (the database agrees).
    short_terms: dict | None = None

    @field_validator("quantity", "notional", mode="before")
    @classmethod
    def _amt(cls, v, info):
        return _decimal(v, info.field_name)

    @model_validator(mode="after")
    def _one_amount(self):
        if (self.quantity is None) == (self.notional is None):
            raise ValueError("give exactly one of quantity or notional")
        amt = self.quantity if self.quantity is not None else self.notional
        if amt <= 0:
            raise ValueError("quantity / notional must be greater than zero")
        if self.lot_instructions and self.side != "sell":
            raise ValueError("lot instructions apply to sells only")
        short_side = self.side in ("sell_short", "buy_to_cover")
        if short_side and self.quantity is None:
            raise ValueError("a short or a cover is a number of shares, never an amount")
        if short_side != (self.short_terms is not None):
            raise ValueError("a SELL SHORT / BUY TO COVER carries short terms; a buy or sell carries none")
        if short_side and self.short_terms.get("position_effect") != {"sell_short": "open",
                                                                      "buy_to_cover": "close"}[self.side]:
            raise ValueError("SELL SHORT opens (position effect open); BUY TO COVER closes (close)")
        if self.option_leg is not None and (self.quantity is None
                                            or self.option_leg.get("position_effect") not in ("open", "close")):
            raise ValueError("an option leg is a number of contracts with a position effect (open / close)")
        ids = [str(li.tax_lot_id) for li in self.lot_instructions]
        if len(ids) != len(set(ids)):
            raise ValueError("a tax lot is listed twice")
        if self.lot_instructions and self.quantity is not None:
            if sum((li.quantity for li in self.lot_instructions), Decimal(0)) > self.quantity:
                raise ValueError("the lots add up to more than the quantity to sell")
        return self


class TradeList(_Strict):
    """One account's trades plus who executes them and the deadline policy."""

    account_id: UUID
    trades: list[TradeLine] = Field(min_length=1, max_length=500)
    executor_type: Literal["custodian_desk", "broker", "member", "other_adviser", "trader"]
    executor_name: str | None = Field(default=None, max_length=300)
    executor_contact: str | None = Field(default=None, max_length=500)
    execute_by: date
    if_not_filled: Literal["cancel", "carry", "ask"] = "ask"
    source: Literal["manual", "proposal"] = "manual"
    source_ref: UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _src(self):
        if self.source == "proposal" and self.source_ref is None:
            raise ValueError("a proposal-sourced trade list names its proposal (source_ref)")
        if self.source == "manual" and self.source_ref is not None:
            raise ValueError("a manual trade list has no source_ref")
        return self


# ── Adapter interface ───────────────────────────────────────────────────────


@dataclass
class TradeContext:
    pool: Any
    org_id: str
    actor_id: str
    account: dict
    #: rep5 (additive): an upstream approval (an approved rebalance proposal)
    #: the instructions carry — services.execution.create_instructions re-reads it.
    upstream: dict | None = None


@dataclass
class SubmissionResult:
    adapter: str
    routed_to: str
    reason: str
    instruction_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sent_externally: bool = False
    #: cpsim03 (additive): the simulator route decision ``{route: bool, condition, reason}`` — set by the Altruist
    #: adapter only, and published only when set (every other route's dict is unchanged)
    simulator: dict | None = None

    def as_dict(self) -> dict:
        out = {"adapter": self.adapter, "routed_to": self.routed_to, "reason": self.reason,
               "instruction_ids": list(self.instruction_ids), "notes": list(self.notes),
               "sent_externally": self.sent_externally}
        if self.simulator is not None:
            out["simulator"] = dict(self.simulator)
        return out


class ShortSaleNotDeclared(Exception):
    """rep13: an adapter whose custodian does not declare short_selling refuses a short at submit (409)."""


class TradeAdapter(ABC):
    """submit / status / cancel. Registered per custodian route key."""

    custodian_key: str = ""
    #: rep13 — the capabilities row an adapter reads for shorts (None: its own route key)
    capability_custodian: str | None = None

    async def refuse_undeclared_shorts(self, conn, trade_list) -> None:
        """rep13 — a SELL SHORT / BUY TO COVER is submitted only to a custodian that DECLARES short_selling
        ('unknown' is not declared). The manual route (a person carries the instruction out) is always offered."""
        from services.custodian_capabilities import available, capability

        shorts = [i for i, t in enumerate(trade_list.trades, start=1) if t.side in ("sell_short", "buy_to_cover")]
        if not shorts:
            return
        code = self.capability_custodian or self.custodian_key
        cap = await capability(conn, code, "short_selling")
        if not available(cap):
            raise ShortSaleNotDeclared(
                f"the {code} adapter refuses trade(s) {shorts}: {code} does not declare short_selling (it is "
                f"'{cap.value}' — unknown is not declared). The manual route is always offered: route the proposal "
                "manually and carry it out with the printed order sheet (SELL SHORT / BUY TO COVER).")

    @abstractmethod
    async def submit(self, ctx: TradeContext, trade_list: TradeList, *, reason: str | None = None) -> SubmissionResult:
        ...

    @abstractmethod
    async def status(self, ctx: TradeContext, submission: SubmissionResult) -> list[dict]:
        ...

    @abstractmethod
    async def cancel(self, ctx: TradeContext, submission: SubmissionResult, reason: str) -> list[dict]:
        ...


class ManualTradeAdapter(TradeAdapter):
    """Every trade → a DRAFT execution instruction. Nothing leaves the system."""

    custodian_key = "manual"

    async def submit(self, ctx, trade_list, *, reason=None, notes=None, routed_via: str | None = None
                     ) -> SubmissionResult:
        from services import execution

        why = reason or "manual route: every trade becomes an execution instruction carried out by a person"
        rows = await execution.create_instructions(
            ctx.pool, org_id=ctx.org_id, actor_id=ctx.actor_id, account=ctx.account, trade_list=trade_list,
            routed_via=routed_via or self.custodian_key, routing_reason=why, upstream=ctx.upstream)
        return SubmissionResult(adapter=self.custodian_key, routed_to="manual", reason=why,
                                instruction_ids=[str(r["id"]) for r in rows], notes=list(notes or []))

    async def status(self, ctx, submission) -> list[dict]:
        from services import execution

        async with ctx.pool.acquire() as conn:
            return [{"id": i, "status": (await execution.load(conn, ctx.org_id, i))["status"]}
                    for i in submission.instruction_ids]

    async def cancel(self, ctx, submission, reason) -> list[dict]:
        from services import execution

        out = []
        for i in submission.instruction_ids:
            row, _ = await execution.cancel(ctx.pool, org_id=ctx.org_id, instruction_id=i, actor_id=ctx.actor_id,
                                            reason=reason)
            out.append({"id": i, "status": row["status"]})
        return out


class AltruistTradeAdapter(TradeAdapter):
    """Declares ``equity_trading_api`` from the capabilities table; routes to
    the manual adapter while it is not an evidenced 'yes', and says so. No
    REAL Altruist trading client exists in this build, so it routes manual even
    then. cpsim03 (D4): an account of a SIMULATED Altruist-compatible custodian
    that meets every D2 condition gets the same DRAFT instructions routed
    ``custodian_api`` — nothing is sent here. cpsim05 (D3): by default such an
    account's instructions are routed ``fix_sim`` instead (``simulator_protocol``)
    — still nothing is sent HERE; services.fix_sim_auto sends after approval."""

    custodian_key = "altruist"
    #: The capabilities row this adapter reads. Fixed, not the registry key: a
    #: profile registered under another custodian code (a test code, or a
    #: second Altruist export name) still declares ALTRUIST's capability.
    capability_custodian = "altruist"
    capability_key = "equity_trading_api"
    #: cpsim03 — the SIMULATED custodian's capability code: None → services.custodian_execution
    #: .SIMULATOR_CAPABILITY_CUSTODIAN ('altruist_simulator', D3), the ONE place a verify substitutes its fixture code
    simulator_capability_custodian: str | None = None
    #: cpsim05 (D3) — how a simulated-custodian account is traded: 'fix' (the default — FIX 4.4 over HTTP, routed
    #: 'fix_sim', sent / booked / reconciled AUTOMATICALLY after the proposal's approval) or 'rest' (cpsim03's orders
    #: API, routed 'custodian_api', Send / Import by a person — still callable: a subclass sets 'rest')
    simulator_protocol: str = "fix"

    async def declared(self, conn):
        from services.custodian_capabilities import capability

        return await capability(conn, self.capability_custodian, self.capability_key)

    async def submit(self, ctx, trade_list, *, reason=None, notes=None) -> SubmissionResult:
        from services import custodian_execution as CX
        from services.custodian_capabilities import available

        async with ctx.pool.acquire() as conn:
            cap = await self.declared(conn)
            sim = await CX.simulator_route(conn, ctx.org_id, ctx.account,
                                           capability_custodian=self.simulator_capability_custodian)
        if sim["ok"]:
            per_trade = CX.trade_refusal(trade_list.trades)
            if per_trade:
                sim = {**sim, "ok": False, "condition": "trade", "reason": per_trade}
        if sim["ok"] and self.simulator_protocol == "fix":
            from services import execution_routers as RT

            if RT.simulated_router() is None:
                sim = {**sim, "ok": False, "condition": "router",
                       "reason": "the simulated FIX router is not registered / connected"}
        if sim["ok"]:      # D4 — the SAME instructions, the route recorded; nothing is sent at submit
            if self.simulator_protocol == "fix":     # cpsim05 D3 / D4
                from services import execution_routers as RT

                route = RT.SIM_ROUTE
                why = (f"simulated FIX route — simulated counterparty {sim['slug']} (SIMULATED): once approved (the "
                       "proposal's maker-checker, or rep2's approval for a system proposal) the trade is sent as a FIX "
                       "NewOrderSingle automatically, its fills booked, synced and reconciled — demo only")
            else:
                route = CX.ROUTE
                why = (f"custodian API route — simulated custodian {sim['slug']} (SIMULATED): after approval a person "
                       "presses Send to custodian, then imports the fills")
            result = await ManualTradeAdapter().submit(ctx, trade_list, reason=why, notes=notes, routed_via=route)
            result.adapter = self.simulator_capability_custodian or CX.SIMULATOR_CAPABILITY_CUSTODIAN
            result.routed_to = route
            result.simulator = {"route": True, "condition": None, "reason": sim["reason"],
                                "protocol": self.simulator_protocol}
            return result
        if not available(cap):
            why = (f"Altruist {self.capability_key} is '{cap.value}' (evidence: {cap.evidence}) — unknown is "
                   "treated as not available, so every trade is routed to manual execution instructions")
        else:
            why = (f"Altruist {self.capability_key} is '{cap.value}', but no Altruist trading client exists in "
                   "this build — routed to manual execution instructions")
        result = await ManualTradeAdapter().submit(ctx, trade_list, reason=why, notes=notes)
        result.adapter = self.capability_custodian
        result.simulator = {"route": False, "condition": sim["condition"], "reason": sim["reason"]}
        return result

    async def status(self, ctx, submission) -> list[dict]:
        """cpsim03 — an instruction sent to a custodian API also reports its custodian order's status (read live)."""
        from services import custodian_execution as CX
        from services import execution

        out = []
        for item in await ManualTradeAdapter().status(ctx, submission):
            async with ctx.pool.acquire() as conn:
                row = await execution.load(conn, ctx.org_id, item["id"])
                if row.get("custodian_order_id"):
                    try:
                        order = await CX._order_of(conn, ctx.org_id, row, None)
                        item = {**item, "custodian_order_id": row["custodian_order_id"],
                                "custodian_status": order.get("status")}
                    except execution.ExecutionError as exc:
                        item = {**item, "custodian_order_id": row["custodian_order_id"], "custodian_error": str(exc)}
            out.append(item)
        return out

    async def cancel(self, ctx, submission, reason) -> list[dict]:
        # execution.cancel cancels a working custodian order at the custodian FIRST (cpsim03 D11)
        return await ManualTradeAdapter().cancel(ctx, submission, reason)


async def _downgrade_notes(conn, custodian_key: str, trade_list: TradeList) -> list[str]:
    """Per-trade notes for capabilities a trade would need that the route
    does not have. Notes only — a downgrade never blocks the list."""
    from services.custodian_capabilities import available, capability

    notes = []
    frac = await capability(conn, custodian_key, "fractional_shares")
    for i, t in enumerate(trade_list.trades, start=1):
        if t.quantity is not None and t.quantity != t.quantity.to_integral_value() and not available(frac):
            notes.append(f"trade {i}: fractional quantity {t.quantity} — fractional_shares is '{frac.value}' for "
                         f"{custodian_key}; the executor may need to round")
        if t.notional is not None and not available(frac):
            notes.append(f"trade {i}: notional order — fractional_shares is '{frac.value}' for {custodian_key}; "
                         "the executor converts it to whole shares")
        if t.lot_instructions:
            lot_feed = await capability(conn, custodian_key, "lot_level_cost_basis_feed")
            if not available(lot_feed):
                notes.append(f"trade {i}: specific-lot instructions are carried on the instruction for a person to "
                             f"apply (lot_level_cost_basis_feed is '{lot_feed.value}')")
    return notes


async def route_trade_list(pool, *, org_id, actor_id, account: dict, trade_list: TradeList,
                           upstream: dict | None = None, manual: bool = False) -> SubmissionResult:
    """Pick the adapter from the account's custodian_code + capabilities.

    Held-away → REFUSED (rep7: a held-away account is never traded; before
    rep7 it was routed to manual). An account whose custodian
    has no registered trade adapter → manual. Otherwise the custodian's own
    adapter decides (Altruist → manual while its API is not evidenced).
    rep5: ``upstream`` (an approved rebalance proposal) rides on the context to
    the instruction creation — the routing itself is unchanged.
    """
    from services.custody.registry import trade_adapter_for, trade_custodian_key

    if str(account["id"]) != str(trade_list.account_id):
        raise ValueError("the trade list names a different account")
    ctx = TradeContext(pool=pool, org_id=str(org_id), actor_id=str(actor_id), account=account, upstream=upstream)
    key = trade_custodian_key(account.get("custodian_code"))
    async with pool.acquire() as conn:
        notes = await _downgrade_notes(conn, key if key else "manual", trade_list)
    from services import authority as AU
    from services.execution import ExecutionConflict
    from services.held_away import refuse

    # rep7 / rep7b — the authority × custody and the executor decide
    refuse(account, "an execution instruction", ExecutionConflict, path="route", executor=trade_list.executor_type)
    e = AU.effective(account)
    if e["custody"] != "integrated" or e["authority"] != "managed":
        # rep7b — advise, or managed at another custodian: manual instructions only, NEVER a custodian API
        why = ("advise: one-off instructions the member or another adviser executes (letter of instruction) — "
               "never a custodian API" if e["authority"] == "advise" else
               "managed at another custodian: manual DVP settlement instructions — never a custodian API")
        return await ManualTradeAdapter().submit(ctx, trade_list, notes=notes, reason=why)
    if manual:     # rep13 — the manual route, always offered: instructions a person carries out
        return await ManualTradeAdapter().submit(
            ctx, trade_list, notes=notes,
            reason="manual route chosen: every trade becomes an execution instruction carried out by a person "
                   "(the order sheet prints SELL SHORT / BUY TO COVER plainly)")
    adapter_cls = trade_adapter_for(account.get("custodian_code"))
    if adapter_cls is None:
        return await ManualTradeAdapter().submit(
            ctx, trade_list, notes=notes,
            reason=f"no trade adapter is registered for custodian {account.get('custodian_code')!r} — manual")
    adapter = adapter_cls()
    async with pool.acquire() as conn:     # rep13 — the adapter refuses a short its custodian does not declare
        await adapter.refuse_undeclared_shorts(conn, trade_list)
    return await adapter.submit(ctx, trade_list, notes=notes)
