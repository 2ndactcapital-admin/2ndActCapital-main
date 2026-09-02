"""Assistant action modules (Sprint 11).

Import and call each module's register_actions() at app startup so they load
into the global REGISTRY before any request is served.
"""
from services.assistant_actions import (
    crm, entity_graph, litellm_ops, marketplace, portfolio, queries, spv, tasks,
)
# fee42b's carry proposer lives beside the rest of the fee/SPV services rather
# than in this package, because it is a service module that happens to expose
# one action — not an assistant feature. It is registered here so there is
# still exactly ONE place that lists every registered action.
from services import spv_carry_runs


def register_all() -> None:
    marketplace.register_actions()
    portfolio.register_actions()
    crm.register_actions()
    tasks.register_actions()
    spv.register_actions()
    entity_graph.register_actions()
    queries.register_actions()
    litellm_ops.register_actions()
    spv_carry_runs.register_actions()
