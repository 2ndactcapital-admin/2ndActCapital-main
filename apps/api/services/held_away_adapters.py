"""Held-away aggregator / custodian-feed adapters (rep7) — the provider-
neutral INTERFACE only.

No aggregator is contracted. This module defines what any provider would
supply (accounts, positions, lots, transactions, each with an as-of) and a
registry of the providers the platform knows by name — every one "not
connected". There is no provider client here, no credential and no network
call: :func:`adapter_for` refuses every provider until a sprint adds a real
client behind this interface and registers it as connected.

Source order (Joe, the spec): a direct custodian feed, then an aggregator
(ByAllAccounts, Plaid …), then statements filed in Chancery and extracted, and
manual entry / a file as the fallback a person can always use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ProviderAccount:
    ref: str
    name: str
    account_number_masked: str
    as_of: date


@dataclass(frozen=True)
class ProviderPosition:
    account_ref: str
    identifier_type: str          # ticker | cusip | isin | sedol
    identifier: str
    quantity: Decimal
    market_value: Decimal | None
    as_of: date


@dataclass(frozen=True)
class ProviderLot:
    account_ref: str
    identifier_type: str
    identifier: str
    quantity: Decimal
    acquired_date: date | None
    cost: Decimal | None
    as_of: date


@dataclass(frozen=True)
class ProviderTransaction:
    account_ref: str
    identifier_type: str
    identifier: str
    side: str                     # buy | sell
    trade_date: date
    quantity: Decimal
    price: Decimal | None
    realized_gain_loss: Decimal | None = None
    term: str | None = None


class HeldAwayAdapter(ABC):
    """What a held-away provider supplies. Every method is read-only: a
    held-away account is never traded, so there is no submit / order here."""

    key: str = ""

    @abstractmethod
    async def accounts(self) -> list[ProviderAccount]: ...

    @abstractmethod
    async def positions(self, account_ref: str) -> list[ProviderPosition]: ...

    @abstractmethod
    async def lots(self, account_ref: str) -> list[ProviderLot]: ...

    @abstractmethod
    async def transactions(self, account_ref: str, since: date) -> list[ProviderTransaction]: ...

    @abstractmethod
    async def as_of(self, account_ref: str) -> date | None: ...


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    label: str
    kind: str                     # aggregator | custodian_feed
    connected: bool = False
    note: str = "not connected — no agreement and no client; enter positions manually, by file or from a statement"
    capabilities: tuple[str, ...] = field(default=("accounts", "positions", "lots", "transactions", "as_of"))


PROVIDERS: dict[str, ProviderInfo] = {
    "byallaccounts": ProviderInfo("byallaccounts", "ByAllAccounts (Morningstar)", "aggregator"),
    "plaid": ProviderInfo("plaid", "Plaid Investments", "aggregator"),
    "yodlee": ProviderInfo("yodlee", "Envestnet | Yodlee", "aggregator"),
    "custodian_direct": ProviderInfo("custodian_direct", "Direct custodian feed", "custodian_feed"),
}
_ADAPTERS: dict[str, type[HeldAwayAdapter]] = {}

NOT_CONNECTED = "not_connected"


class ProviderNotConnected(LookupError):
    pass


def providers() -> list[dict]:
    """Every known provider with its status — all "not connected" today."""
    return [{"key": p.key, "label": p.label, "kind": p.kind,
             "status": "connected" if (p.connected and p.key in _ADAPTERS) else NOT_CONNECTED,
             "note": p.note, "capabilities": list(p.capabilities)} for p in PROVIDERS.values()]


def provider(key: str) -> ProviderInfo | None:
    return PROVIDERS.get((key or "").strip().lower())


def adapter_for(key: str) -> HeldAwayAdapter:
    """The connected adapter for ``key`` — none exists, so this always refuses."""
    p = provider(key)
    if p is None:
        raise ProviderNotConnected(f"unknown provider {key!r}")
    cls = _ADAPTERS.get(p.key)
    if not p.connected or cls is None:
        raise ProviderNotConnected(f"{p.label} is not connected — no provider client exists yet")
    return cls()
