"""LiteLLM proxy operations — assistant/workflow actions.

Currently one action: ``litellm.reload_model_cost_map``.

LiteLLM keeps its per-model price table (``model_prices_and_context_window.json``)
in memory.  When that upstream table changes — a provider cuts prices, a new
model lands — a running proxy keeps quoting the stale numbers until it is either
restarted or told to reload.  LiteLLM exposes the latter as an admin endpoint:

    POST {LITELLM_BASE_URL}/reload/model_cost_map
    Authorization: Bearer {LITELLM_MASTER_KEY}

*** REAL, CURRENT BLOCKER — READ BEFORE ASSUMING THIS WORKS ***
The LiteLLM proxy is NOT DEPLOYED.  Phase A of docs/LITELLM_INTEGRATION_DESIGN_V1.md
(§14) — "LiteLLM proxy deployed on Render, own Supabase schema, render.yaml gap
fixed" — has not been done.  Confirmed, not assumed: the Doppler ``development``
config contains no ``LITELLM_*`` secret of any kind, and ``render.yaml`` declares
no LiteLLM service.  So in production TODAY every invocation of this action
raises ``LiteLLMConfigError``.  That is the intended behaviour: this action fails
LOUD and specifically.  It must NEVER degrade to a silent no-op, because a silent
no-op here means the platform bills against a stale price table and nobody finds
out.

Configuration follows the established external-service convention (the
``services/portfolio_altruist.py`` precedent, not org_settings): plain process
environment variables, sourced from Doppler, declared as ``sync: false`` entries
in ``render.yaml`` once real values exist.

  LITELLM_BASE_URL    — proxy base URL, no trailing slash
  LITELLM_MASTER_KEY  — admin bearer token (LiteLLM's ``sk-...`` master key)

Note deliberately NOT used here: ``LITELLM_SALT_KEY``.  It encrypts stored
provider credentials, is unrotatable after first use, and has no business being
read by application code (design doc §10).
"""
from __future__ import annotations

import os

from services.action_registry import AssistantAction, REGISTRY

ACTION_KEY = "litellm.reload_model_cost_map"

# The two variables this action needs. Order matters only for the error message.
LITELLM_ENV_VARS: tuple[str, ...] = ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY")

# LiteLLM's real documented admin endpoint path (appended to the base URL).
RELOAD_PATH = "/reload/model_cost_map"

DEFAULT_TIMEOUT_SECONDS = 30.0


class LiteLLMConfigError(RuntimeError):
    """LiteLLM is not configured — no call was attempted.

    Distinct from ``LiteLLMReloadError`` on purpose: "we never had an endpoint to
    call" and "we called and it failed" are different operational problems with
    different fixes, and collapsing them into one exception hides which one
    happened.
    """


class LiteLLMReloadError(RuntimeError):
    """A real call was made to LiteLLM and it did not succeed."""


def credential_state() -> tuple[bool, tuple[str, ...]]:
    """``(all_present, missing_var_names)`` — reads the environment, nothing else."""
    missing = tuple(v for v in LITELLM_ENV_VARS if not os.environ.get(v))
    return (not missing), missing


def _config_error_message(missing: tuple[str, ...]) -> str:
    return (
        f"Cannot reload the LiteLLM model cost map: the LiteLLM proxy is not "
        f"configured for this deployment. Missing environment variable(s): "
        f"{', '.join(missing)}. No HTTP call was attempted — there is no endpoint "
        f"to call. This is the EXPECTED state until Phase A of "
        f"docs/LITELLM_INTEGRATION_DESIGN_V1.md ships (LiteLLM proxy deployed on "
        f"Render). To fix: deploy the proxy, then set {' and '.join(LITELLM_ENV_VARS)} "
        f"in Doppler and declare them in render.yaml."
    )


async def reload_model_cost_map(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """POST to LiteLLM's ``/reload/model_cost_map``. Raises on ANY failure.

    Returns a dict of the real observed outcome (status code, endpoint, the
    opening characters of the response body) so the workflow run-step audit trail
    records what actually happened rather than a bare boolean.
    """
    present, missing = credential_state()
    if not present:
        raise LiteLLMConfigError(_config_error_message(missing))

    base_url = os.environ["LITELLM_BASE_URL"].rstrip("/")
    master_key = os.environ["LITELLM_MASTER_KEY"]
    url = f"{base_url}{RELOAD_PATH}"

    import httpx  # local import — only needed when the proxy is actually configured

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {master_key}",
                },
            )
    except Exception as exc:  # noqa: BLE001 — every transport failure must be loud
        raise LiteLLMReloadError(
            f"LiteLLM is configured (LITELLM_BASE_URL={base_url}) but the real "
            f"POST to {url} failed at the transport layer: "
            f"{type(exc).__name__}: {exc}. The model cost map was NOT reloaded; "
            f"LiteLLM is still pricing against its previously loaded table."
        ) from exc

    body = (response.text or "")[:500]
    if response.status_code >= 400:
        raise LiteLLMReloadError(
            f"LiteLLM refused the model cost map reload: POST {url} returned "
            f"HTTP {response.status_code}. Response body (first 500 chars): "
            f"{body!r}. A 401/403 here means LITELLM_MASTER_KEY is wrong or is "
            f"not the proxy's master key. The model cost map was NOT reloaded."
        )

    return {
        "endpoint": url,
        "status_code": response.status_code,
        "response_body": body,
    }


async def _reload_handler(pool=None, user_id=None, org_id=None, **_):
    """Registry handler. Signature matches every other AssistantAction handler.

    Touches no database and no member data — ``pool``/``user_id``/``org_id`` are
    accepted only because the registry calls every handler the same way. Raises
    (never returns a failure dict) so a workflow run HOLDs loudly on failure.
    """
    outcome = await reload_model_cost_map()
    return {
        "data": outcome,
        "render": None,
        "text": (
            f"LiteLLM model cost map reloaded — HTTP {outcome['status_code']} "
            f"from {outcome['endpoint']}."
        ),
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key=ACTION_KEY,
            module="litellm_ops",
            description=(
                "Reload the LiteLLM proxy's in-memory model cost map from the "
                "upstream price table, so per-model pricing and cost attribution "
                "reflect current provider prices. Platform maintenance only — "
                "touches no member data and moves no money."
            ),
            # WRITE is the honest classification: the call mutates state inside an
            # external service. It reads and writes nothing in our database, but
            # calling it "read" to make the tier default fall out more
            # conveniently would be a lie encoded in the catalog.
            access_type="write",
            # There is no "manage AI infrastructure" key in the global permissions
            # catalog and this sprint ships no Part-1 SQL to add one, so we reuse a
            # REAL, already-seeded key rather than referencing a permission that
            # does not exist. `author_workflows` is the right fit: the only
            # intended invocation path is a BPMN Service Task, and the people who
            # author workflows are exactly the people who should be able to fire a
            # cost-map reload. It is granted to no seeded Profile by default.
            required_permission="author_workflows",
            default_autonomy="confirm",
            reversible=False,  # nothing to undo; re-running is the remedy
            render_target="inline",
            handler=_reload_handler,
            params_schema={"type": "object", "properties": {}, "required": []},
            # Opt in to real invocation from a BPMN Service Task (see
            # AssistantAction.workflow_invocable).
            workflow_invocable=True,
        )
    )
