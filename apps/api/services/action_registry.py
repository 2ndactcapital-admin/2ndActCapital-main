"""Assistant action registry (Sprint 11).

Central catalog of AI-executable actions.  All actions are registered via
module-level ``register_actions()`` calls in their respective service modules.

READ actions execute automatically inside the assistant LLM loop.
WRITE actions are NEVER executed from the LLM loop — they surface as a
``proposed_action`` for explicit user confirmation via POST /assistant/confirm.
"""
import json
from dataclasses import dataclass, field
from typing import Callable, Literal


@dataclass
class AssistantAction:
    key: str             # e.g. 'marketplace.show_new_deals'
    module: str          # e.g. 'marketplace'
    description: str
    access_type: Literal["read", "write"]
    required_permission: str | None  # None → no gating
    # Stakes class, per the three-test rule (does it move money or create an
    # obligation; does it produce an artifact a third party relies on; does it
    # mutate ownership, economic terms, or a posted ledger line):
    #   1 = highest stakes (capital commitments, ownership/economic mutations)
    #   2 = write, but none of the three tests fire on money/ownership terms
    #   3 = lowest stakes (all reads, plus writes whose only real gate is a
    #       downstream human review step, e.g. propose())
    # COUNTERINTUITIVE ON PURPOSE: 1 is the DANGEROUS end, not the safe one.
    # A future agent-side ``max_tier`` column names the deepest (numerically
    # LOWEST) tier that agent may reach — max_tier=1 is the MOST permissive
    # agent (can reach Tier 1), max_tier=3 is the LEAST permissive (reads
    # only). Read "ceiling" as "how far down toward 1 this agent may go," not
    # as a small number meaning a small allowance — that reading is backwards
    # and will grant a low-trust agent the highest-stakes actions. Eligibility
    # is therefore ``action.tier >= agent.max_tier``, never ``<=``.
    tier: int
    # Only ever read for WRITE actions (routers/assistant.py's confirm_action
    # and undo_activity — both 400 before this field matters if
    # access_type != "write"). Meaningless on a READ action; every read
    # carries reversible=False for schema-level consistency, not because
    # anything decided it applies. Confirmed by grep, not assumed — see
    # docs/PROJECT_STATUS.md's actionregistryfix entry before changing this.
    reversible: bool
    render_target: Literal["inline", "screen", "auto"]
    handler: Callable    # async callable; for WRITE → confirm phase handler
    params_schema: dict = field(default_factory=dict)
    options: list[dict] = field(default_factory=list)  # choices for WRITE actions
    draft_handler: Callable | None = None  # optional; WRITE preview generator
    # OPT-IN: may a BPMN Service Task actually INVOKE this handler during a
    # workflow run?  Phase 1 of the Workflow Manager deliberately only *resolved*
    # a Service Task's action key without running it.  Turning invocation on for
    # every action at once would silently change the behaviour of every existing
    # workflow, so it is opt-in per action.  The engine additionally re-checks
    # ``required_permission`` against the member who started the run — a workflow
    # must never become a permission bypass.
    workflow_invocable: bool = False


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, AssistantAction] = {}

    def register(self, action: AssistantAction) -> None:
        self._actions[action.key] = action

    def get(self, key: str) -> AssistantAction | None:
        return self._actions.get(key)

    def all(self) -> list[AssistantAction]:
        """Every registered action (unfiltered) — the closed reference list a
        workflow generator draws valid Service Task action keys from."""
        return list(self._actions.values())

    def list_for_user(self, user_id: str, permissions: set[str]) -> list[AssistantAction]:
        return [
            a for a in self._actions.values()
            if a.required_permission is None or a.required_permission in permissions
        ]

    def to_tool_specs(self, actions: list[AssistantAction]) -> list[dict]:
        specs = []
        for a in actions:
            schema = a.params_schema or {
                "type": "object",
                "properties": {},
                "required": [],
            }
            specs.append({
                "name": a.key.replace(".", "_"),
                "description": a.description,
                "input_schema": schema,
            })
        return specs

    async def sync_catalog(self, pool, org_id: str) -> None:
        """Upsert every registered action into assistant_action_catalog.

        Column mapping (deployed schema):
          AssistantAction.key  → action_key
          .required_permission → required_permission (may be NULL)
          .tier                → tier
          is_active            → always True on upsert
          registered_at        → now() on insert, preserved on update
        """
        async with pool.acquire() as conn:
            for a in self._actions.values():
                await conn.execute(
                    """
                    INSERT INTO assistant_action_catalog
                        (org_id, action_key, module, description, access_type,
                         required_permission, tier, reversible,
                         render_target, is_active)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true)
                    ON CONFLICT (org_id, action_key) DO UPDATE SET
                        module              = EXCLUDED.module,
                        description         = EXCLUDED.description,
                        access_type         = EXCLUDED.access_type,
                        required_permission = EXCLUDED.required_permission,
                        tier                = EXCLUDED.tier,
                        reversible          = EXCLUDED.reversible,
                        render_target       = EXCLUDED.render_target,
                        is_active           = true
                    """,
                    org_id,
                    a.key,
                    a.module,
                    a.description,
                    a.access_type,
                    a.required_permission,
                    a.tier,
                    a.reversible,
                    a.render_target,
                )


REGISTRY = ActionRegistry()
