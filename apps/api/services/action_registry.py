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
    default_autonomy: Literal["suggest", "confirm", "auto"]
    reversible: bool
    render_target: Literal["inline", "screen", "auto"]
    handler: Callable    # async callable; for WRITE → confirm phase handler
    params_schema: dict = field(default_factory=dict)
    options: list[dict] = field(default_factory=list)  # choices for WRITE actions
    draft_handler: Callable | None = None  # optional; WRITE preview generator


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, AssistantAction] = {}

    def register(self, action: AssistantAction) -> None:
        self._actions[action.key] = action

    def get(self, key: str) -> AssistantAction | None:
        return self._actions.get(key)

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
          is_active            → always True on upsert
          registered_at        → now() on insert, preserved on update
        """
        async with pool.acquire() as conn:
            for a in self._actions.values():
                await conn.execute(
                    """
                    INSERT INTO assistant_action_catalog
                        (org_id, action_key, module, description, access_type,
                         required_permission, default_autonomy, reversible,
                         render_target, is_active)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true)
                    ON CONFLICT (org_id, action_key) DO UPDATE SET
                        module              = EXCLUDED.module,
                        description         = EXCLUDED.description,
                        access_type         = EXCLUDED.access_type,
                        required_permission = EXCLUDED.required_permission,
                        default_autonomy    = EXCLUDED.default_autonomy,
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
                    a.default_autonomy,
                    a.reversible,
                    a.render_target,
                )


REGISTRY = ActionRegistry()
