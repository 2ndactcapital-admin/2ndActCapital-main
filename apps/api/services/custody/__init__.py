"""Custody adapters — the account-layer ingestion substrate (Sprint fee31).

Importing this package registers every adapter that ships with it. Adapters
register as an IMPORT SIDE EFFECT, which is why ``csv_adapter`` is imported here
rather than lazily by the registry: a registry that imported adapters on demand
would have to know their module paths, and that mapping is exactly the
hardcoded if/else chain the registry exists to avoid.

A future adapter (SFTP puller, fixed-width parser, live REST client) is added by
writing the module, calling ``register_adapter`` at its bottom, and adding one
import line here. Nothing in registry.py, csv_adapter.py or importer.py changes.
"""

from services.custody.base import (  # noqa: F401
    AccountNumber,
    AccountRecord,
    BalanceRecord,
    ColumnMappingError,
    CustodyAdapter,
    CustodyError,
    FlowRecord,
    ParseOutcome,
    RowError,
)
from services.custody.csv_adapter import CsvCustodyAdapter  # noqa: F401
from services.custody.registry import (  # noqa: F401
    DEFAULT_PROFILES,
    CustodyProfile,
    UnknownAdapterError,
    UnknownCustodianError,
    build_adapter,
    get_adapter_class,
    get_or_create_salt,
    load_profiles,
    register_adapter,
    registered_adapters,
    registered_trade_adapters,
    register_trade_adapter,
    resolve_profile,
    trade_adapter_for,
    trade_custodian_key,
)

# rep2 — trade adapters register the same way (import side effect), in their
# own map: the CSV ingestion path above is untouched by them.
from services.custody.trading import (  # noqa: E402,F401
    AltruistTradeAdapter,
    ManualTradeAdapter,
    TradeAdapter,
    TradeList,
    route_trade_list,
)

register_trade_adapter("manual", ManualTradeAdapter)
register_trade_adapter("altruist", AltruistTradeAdapter)

__all__ = [
    "AccountNumber",
    "AccountRecord",
    "BalanceRecord",
    "ColumnMappingError",
    "CsvCustodyAdapter",
    "CustodyAdapter",
    "CustodyError",
    "CustodyProfile",
    "DEFAULT_PROFILES",
    "FlowRecord",
    "ParseOutcome",
    "RowError",
    "UnknownAdapterError",
    "UnknownCustodianError",
    "build_adapter",
    "get_adapter_class",
    "get_or_create_salt",
    "load_profiles",
    "register_adapter",
    "registered_adapters",
    "resolve_profile",
    "AltruistTradeAdapter",
    "ManualTradeAdapter",
    "TradeAdapter",
    "TradeList",
    "register_trade_adapter",
    "registered_trade_adapters",
    "route_trade_list",
    "trade_adapter_for",
    "trade_custodian_key",
]
