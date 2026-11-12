"""Execution routers (rep9) — the provider-neutral interface a FIX router (Flyer
or similar) would implement for BLOCK trading. NO REAL ROUTER IS CONNECTED.

cpsim05: ``fix_sim`` — :class:`SimulatedFixRouter`, FIX 4.4 over HTTP to the
SIMULATED counterparty only (connected, ``simulated``). The real ``fix`` entry
below is unchanged: a NotConnectedRouter, refused with its reason.

No router is contracted, so this module is the INTERFACE and a registry whose
every entry says "not connected". There is no client code, no socket, no HTTP
and no FIX engine here — every method of the registered router raises
:class:`RouterNotConnected` with the reason, and choosing route ``fix`` for a
block is refused with that same reason (the database admits route 'manual'
only — trade_blocks_route_chk). The manual route (print an order execution
sheet, give it to a broker of choice, confirm fills by hand) is ALWAYS
available, including on the day a router is connected.

THE CUSTODY CLIFF. Even a connected router sends what a PERSON approved: a
block reaches a router only after its maker-checker approval, and nothing an
agent proposes can reach submit_block.

Adding a router — the checklist (also in docs/REPLICATION_STUDIO.md, rep9):
  1. A signed agreement with the router (and the executing broker behind it);
     the firm's best-execution review covers it.
  2. A subclass of :class:`ExecutionRouter` in its own module: submit_block
     (NewOrderSingle for the block, the executing account as the FIX account),
     cancel_block (OrderCancelRequest), execution_reports (ExecutionReport →
     block_fills rows entered_by the person who approved the connection's
     import), send_allocations (AllocationInstruction with each allocation's
     masked account reference and settlement instructions).
  3. Credentials in Doppler only, read at call time, never logged; the
     connection's own network allow-list.
  4. ``connected = True`` only after a certification run against the router's
     test venue, recorded with its evidence.
  5. A migration widening trade_blocks_route_chk to admit 'fix'; the verify
     proves manual is still offered and still the default.
  6. Fills from the router land as block_fills through the SAME allocation path
     (average price computed, never taken from the router's own average).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

ROUTES = ("manual", "fix", "fix_sim")
ROUTE_LABELS = {"manual": "Manual — print an order execution sheet for a broker of your choice",
                "fix": "FIX router — not connected",
                "fix_sim": "FIX — simulated counterparty (SIMULATED; every member at the simulated custodian)"}
DEFAULT_ROUTE = "manual"
#: cpsim05 — the simulated FIX counterparty's route key (instructions: routed_via / sent_channel; blocks: route)
SIM_ROUTE = "fix_sim"


class RouterNotConnected(RuntimeError):
    """The router exists only as an interface (no contract, no connection)."""


@dataclass
class ExecutionReport:
    exec_id: str
    quantity: str
    price: str
    transact_time: str
    venue: str | None = None


@dataclass
class RouterResult:
    accepted: bool
    reference: str | None = None
    notes: list[str] = field(default_factory=list)


class ExecutionRouter(ABC):
    key: str = ""
    label: str = ""
    protocol: str = "fix"
    connected: bool = False
    #: cpsim05 — a SIMULATED counterparty: connected, but never a real automated route (it does not make a manual
    #: block need a reason, and it is offered only when every member is at the simulated custodian)
    simulated: bool = False

    @abstractmethod
    async def submit_block(self, block: dict, allocations: list[dict]) -> RouterResult: ...

    @abstractmethod
    async def cancel_block(self, block: dict, reason: str) -> RouterResult: ...

    @abstractmethod
    async def execution_reports(self, block: dict) -> list[ExecutionReport]: ...

    @abstractmethod
    async def send_allocations(self, block: dict, allocations: list[dict]) -> RouterResult: ...


class NotConnectedRouter(ExecutionRouter):
    """The placeholder for a FIX router: every call refuses, with the reason."""

    def __init__(self, key: str, label: str, reason: str):
        self.key, self.label, self.reason = key, label, reason

    def _refuse(self):
        raise RouterNotConnected(self.reason)

    async def submit_block(self, block, allocations):
        self._refuse()

    async def cancel_block(self, block, reason):
        self._refuse()

    async def execution_reports(self, block):
        self._refuse()

    async def send_allocations(self, block, allocations):
        self._refuse()


class SimulatedFixRouter(ExecutionRouter):
    """cpsim05 (D3) — FIX 4.4 tag=value over HTTP to the SIMULATED counterparty (services/fix_sim_client.py). Connected
    because the counterparty is ours; ``simulated`` so it never stands in for a real router. Each method sends ONE
    message with a session the caller read (and released its connection for) and returns the client's
    ``{sent, received, replies, summary}``. Deciding WHETHER to send is the caller's: services/fix_sim_blocks.py (a
    person's block action, after the block's own maker-checker approval)."""

    key, label, protocol, connected, simulated = SIM_ROUTE, "FIX — simulated counterparty", "fix", True, True

    async def submit_block(self, block, allocations, *, session, sec, account_ref, transport=None):
        from services import fix_sim_client as FC

        fields = FC.new_order_single(cl_ord_id=str(block["id"]), account=account_ref, side=block["side"], sec=sec,
                                     quantity=block["total_quantity"], price_kind=block["price_kind"],
                                     limit_price=block.get("limit_price"), block=True)
        return await FC.send(session, "D", fields, transport=transport)

    async def cancel_block(self, block, reason, *, session, sec, transport=None):
        from services import fix_sim_client as FC

        fields = FC.cancel_request(cl_ord_id=f"{block['id']}:cxl", orig_cl_ord_id=str(block["id"]),
                                   side=block["side"], sec=sec, quantity=block["total_quantity"])
        return await FC.send(session, "F", fields, transport=transport)

    async def execution_reports(self, block, *, session, order_id=None, transport=None):
        from services import fix_sim_client as FC

        return await FC.send(session, "H", FC.status_request(cl_ord_id=str(block["id"]), order_id=order_id,
                                                             side=block["side"]), transport=transport)

    async def send_allocations(self, block, allocations, *, session, sec, order_id, avg_price, trade_date, legs,
                               transport=None):
        from services import fix_sim_client as FC

        fields = FC.allocation_instruction(alloc_id=f"{block['id']}:alloc", cl_ord_id=str(block["id"]),
                                           order_id=order_id, side=block["side"], sec=sec,
                                           quantity=sum((q for _, q in legs), 0), avg_price=avg_price,
                                           trade_date=trade_date, legs=legs)
        return await FC.send(session, "J", fields, transport=transport)


# rep9 fix 1 — the spec's word is "not connected"; the reason says so (it used to read "contracted or connected")
NOT_CONNECTED_REASON = ("the FIX router is not connected — none is contracted (rep9 builds the interface only — no "
                        "client code, no network). Use the manual route: print the order execution sheet, give it to a broker of "
                        "your choice and enter the fills by hand")

_REGISTRY: dict[str, ExecutionRouter] = {
    "fix": NotConnectedRouter("fix", "FIX router (Flyer or similar) — none contracted", NOT_CONNECTED_REASON),
    SIM_ROUTE: SimulatedFixRouter(),          # cpsim05 — the simulated counterparty only
}


def registry() -> dict[str, ExecutionRouter]:
    return dict(_REGISTRY)


def routers() -> list[dict]:
    return [{"key": r.key, "label": r.label, "protocol": r.protocol, "connected": bool(r.connected),
             "simulated": bool(getattr(r, "simulated", False)),
             "status": "connected" if r.connected else "not_connected",
             "reason": None if r.connected else getattr(r, "reason", NOT_CONNECTED_REASON)}
            for r in _REGISTRY.values()]


def any_connected() -> bool:
    """A REAL automated route is connected (cpsim05: the simulated counterparty does not count — it is offered only
    to an all-simulated block, and choosing manual never needs a reason because of it)."""
    return any(r.connected and not getattr(r, "simulated", False) for r in _REGISTRY.values())


def simulated_router() -> ExecutionRouter | None:
    r = _REGISTRY.get(SIM_ROUTE)
    return r if r is not None and r.connected and getattr(r, "simulated", False) else None


def route_refusal(route: str) -> str | None:
    """None when the route may be chosen; otherwise the plain reason. (cpsim05: 'fix_sim' is admitted here when its
    router is connected; whether a BLOCK may take it — every member simulated — is services.blocks' check.)"""
    if route == "manual":
        return None
    if route not in ROUTES:
        return f"route is one of {list(ROUTES)}"
    r = _REGISTRY.get(route)
    if r is None or not r.connected:
        return f"route {route} refused: {NOT_CONNECTED_REASON}"
    return None
