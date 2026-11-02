"""Execution routers (rep9) — the provider-neutral interface a FIX router (Flyer
or similar) would implement for BLOCK trading. NONE IS CONNECTED.

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

ROUTES = ("manual", "fix")
ROUTE_LABELS = {"manual": "Manual — print an order execution sheet for a broker of your choice",
                "fix": "FIX router — not connected"}
DEFAULT_ROUTE = "manual"


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


# rep9 fix 1 — the spec's word is "not connected"; the reason says so (it used to read "contracted or connected")
NOT_CONNECTED_REASON = ("the FIX router is not connected — none is contracted (rep9 builds the interface only — no "
                        "client code, no network). Use the manual route: print the order execution sheet, give it to a broker of "
                        "your choice and enter the fills by hand")

_REGISTRY: dict[str, ExecutionRouter] = {
    "fix": NotConnectedRouter("fix", "FIX router (Flyer or similar) — none contracted", NOT_CONNECTED_REASON),
}


def registry() -> dict[str, ExecutionRouter]:
    return dict(_REGISTRY)


def routers() -> list[dict]:
    return [{"key": r.key, "label": r.label, "protocol": r.protocol, "connected": bool(r.connected),
             "status": "connected" if r.connected else "not_connected",
             "reason": None if r.connected else getattr(r, "reason", NOT_CONNECTED_REASON)}
            for r in _REGISTRY.values()]


def any_connected() -> bool:
    return any(r.connected for r in _REGISTRY.values())


def route_refusal(route: str) -> str | None:
    """None when the route may be chosen; otherwise the plain reason."""
    if route == "manual":
        return None
    if route not in ROUTES:
        return f"route is one of {list(ROUTES)}"
    r = _REGISTRY.get(route)
    if r is None or not r.connected:
        return f"route fix refused: {NOT_CONNECTED_REASON}"
    return None
