"""Assistant action modules (Sprint 11).

Import and call each module's register_actions() at app startup so they load
into the global REGISTRY before any request is served.
"""
from services.assistant_actions import (
    crm, edgar_ops, entity_graph, litellm_ops, marketplace, portfolio, propose,
    queries, spv, tasks,
)
# fee42b's carry proposer lives beside the rest of the fee/SPV services rather
# than in this package, because it is a service module that happens to expose
# one action — not an assistant feature. It is registered here so there is
# still exactly ONE place that lists every registered action.
from services import spv_carry_runs
# SECMASTER1 — the platform security-master jobs' Service Task verbs (Super Admin only; never listed to the assistant)
from services import security_master_jobs
# S28 — the AI drift monitor's Service Task verb (Super Admin only; never listed to the assistant)
from services import ai_drift
# CASH04 — the cash maturity waterfall's Service Task verb (Super Admin only; DRAFTS only; never listed to the assistant)
from services import cash_waterfall
# CASH06 — a cash rule's firing (drafts + submits payment instructions for approval). WORKFLOW-ONLY: registered with
# workflow_only=True, so the assistant never lists it and /assistant/confirm refuses it.
from services import cash_rules


def register_all() -> None:
    marketplace.register_actions()
    portfolio.register_actions()
    crm.register_actions()
    tasks.register_actions()
    spv.register_actions()
    entity_graph.register_actions()
    queries.register_actions()
    litellm_ops.register_actions()
    propose.register_actions()
    spv_carry_runs.register_actions()
    edgar_ops.register_actions()
    security_master_jobs.register_actions()
    ai_drift.register_actions()
    cash_waterfall.register_actions()
    cash_rules.register_actions()
