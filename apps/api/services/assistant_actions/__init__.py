"""Assistant action modules (Sprint 11).

Import and call each module's register_actions() at app startup so they load
into the global REGISTRY before any request is served.
"""
from services.assistant_actions import crm, marketplace, portfolio, spv, tasks


def register_all() -> None:
    marketplace.register_actions()
    portfolio.register_actions()
    crm.register_actions()
    tasks.register_actions()
    spv.register_actions()
