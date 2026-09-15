"""Per-org AI provider credential storage — LiteLLM Phase D1a.

SCOPE (per docs/LITELLM_PHASE_D_DISCOVERY.md and the D1a sprint prompt): can an
org store its own provider key, and does a dedicated LiteLLM deployment get
created for it? Routing (which deployment a given call actually uses), spend
attribution, and failure alerting are explicitly LATER work — nothing here
reads ``ai.credential_source.*`` to pick a deployment at call time yet.

Task 1a — THE central finding this module is built on, proved live against the
real proxy (not inferred from docs): ``POST /model/new``'s ``litellm_params.
api_key`` accepts a LITERAL credential value, not only ``os.environ/<NAME>``
indirection. Proved by creating a real deployment with a literal
``ANTHROPIC_API_KEY`` value, making a genuine call through it (HTTP 200, real
model output), then deleting it. LiteLLM encrypts the value at rest with
``LITELLM_SALT_KEY`` and NEVER echoes ``api_key`` back through
``GET /model/info`` — confirmed for the two pre-existing platform deployments
AND for the literal-key probe deployment alike, regardless of which mechanism
supplied the credential. That is what makes this safe: the raw key crosses the
wire once, to LiteLLM, and is never stored in our own database, logged, or
readable back out of LiteLLM's own admin API either.

Task 1b — the credential SOURCE flag (which of 'org'/'platform' an org is
currently using for a given provider) lives in ``org_settings`` as
``ai.credential_source.<provider>`` — no schema change, same enum-validation
shape ``ai.embedding.provider`` already established. See
``services.org_settings._validate_setting``.

Task 3 — the org<->deployment MAPPING is deliberately NOT stored in our own
schema at all. ``app_service`` cannot read the ``litellm`` schema (CLAUDE.md),
so any code needing to know "does this org have a deployment" must ask
LiteLLM's own admin API regardless — storing a second, parallel copy of that
fact in our DB would just be a second place for it to drift out of sync with
what the proxy actually has. Instead the deployment's internal ``model_name``
is DETERMINISTIC (``_org_deployment_name``), so it can always be recomputed
from ``(provider, org_id)`` and looked up live. That name is INTERNAL only —
never returned by anything org-facing (Task 4's proof).

Task 2's real structural consequence, confirmed in the discovery doc: LiteLLM
treats same-``model_name`` deployments as one load-balanced GROUP, not
independently routable options. An org's own deployment therefore MUST use a
distinct ``model_name`` from the platform's logical name it mirrors — never
the same one, or a call meant to use the org's key could route to the
platform's deployment (or vice versa) nondeterministically.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from services.org_settings import set_setting

# Provider -> the platform's own live deployment `model_name` to mirror.
# Adding a provider here is the only step needed to support BYO-key for it.
# Code-level constant, same pattern org_settings.py's own docstring already
# uses for "platform default" (org_settings has no owner_scope column and no
# platform-scope ROW is possible — see docs/LITELLM_PHASE_D_DISCOVERY.md
# Task 4c), extended here to which providers exist at all.
PROVIDER_PLATFORM_DEPLOYMENT: dict[str, str] = {
    "anthropic": "claude-sonnet",
    "voyage": "voyage-3.5",
}

CREDENTIAL_SOURCE_ORG = "org"
CREDENTIAL_SOURCE_PLATFORM = "platform"
VALID_CREDENTIAL_SOURCES = (CREDENTIAL_SOURCE_ORG, CREDENTIAL_SOURCE_PLATFORM)


def credential_source_key(provider: str) -> str:
    return f"ai.credential_source.{provider}"


def _org_deployment_name(provider: str, org_id) -> str:
    """Deterministic, INTERNAL model_name for one org's own deployment.

    Never the platform's logical name (see module docstring) and never
    surfaced to any org-facing response.
    """
    return f"org-{provider}-{org_id}"


class CredentialProvisionError(RuntimeError):
    """A real call to LiteLLM's admin API failed, or the request was invalid."""


def _base_and_key() -> tuple[str, str]:
    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not base or not key:
        raise CredentialProvisionError(
            "LITELLM_BASE_URL / LITELLM_MASTER_KEY not configured — cannot "
            "reach the LiteLLM proxy's admin API."
        )
    return base, key


def _http(path: str, *, method: str = "GET", body: dict | None = None,
          timeout: float = 30.0) -> tuple[int, str]:
    """Never raises on an HTTP error status; never logs the request body."""
    base, key = _base_and_key()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _model_info() -> list[dict]:
    status, body = _http("/model/info")
    if status != 200:
        raise CredentialProvisionError(f"GET /model/info -> HTTP {status}: {body[:300]}")
    return json.loads(body).get("data", [])


def _find_deployment(model_name: str) -> dict | None:
    for entry in _model_info():
        if entry.get("model_name") == model_name:
            return entry
    return None


def _platform_upstream_model(provider: str) -> str:
    """The real, LIVE litellm_params.model string the platform deployment for
    this provider currently uses — read fresh, never hardcoded, so a future
    platform model change is picked up automatically on next provisioning."""
    platform_name = PROVIDER_PLATFORM_DEPLOYMENT.get(provider)
    if platform_name is None:
        raise CredentialProvisionError(f"Unknown/unsupported provider: {provider!r}")
    entry = _find_deployment(platform_name)
    if entry is None:
        raise CredentialProvisionError(
            f"Platform deployment '{platform_name}' for provider {provider!r} "
            f"does not exist on the live proxy — cannot mirror its upstream "
            f"model string."
        )
    upstream = entry.get("litellm_params", {}).get("model")
    if not upstream:
        raise CredentialProvisionError(
            f"Platform deployment '{platform_name}' has no litellm_params.model"
        )
    return upstream


def _delete_deployment(model_id: str) -> None:
    status, body = _http("/model/delete", method="POST", body={"id": model_id})
    if status != 200:
        raise CredentialProvisionError(f"POST /model/delete -> HTTP {status}: {body[:300]}")


def provision_org_deployment(provider: str, org_id, api_key: str) -> str:
    """Create (or replace) org_id's own dedicated deployment for provider.

    Returns the internal model_id. Deletes any pre-existing deployment for the
    same (provider, org) pair FIRST — LiteLLM does not reject a duplicate
    model_name, it silently load-balances across same-named deployments
    (confirmed live in Phase D discovery), which would make routing
    non-deterministic. This also makes the function safe to call again for
    key rotation.
    """
    if provider not in PROVIDER_PLATFORM_DEPLOYMENT:
        raise CredentialProvisionError(f"Unknown/unsupported provider: {provider!r}")
    if not api_key or not api_key.strip():
        raise CredentialProvisionError("api_key must be a non-empty string")

    name = _org_deployment_name(provider, org_id)
    existing = _find_deployment(name)
    if existing is not None:
        _delete_deployment(existing["model_info"]["id"])

    upstream_model = _platform_upstream_model(provider)
    status, body = _http("/model/new", method="POST", body={
        "model_name": name,
        "litellm_params": {"model": upstream_model, "api_key": api_key},
    })
    if status != 200:
        raise CredentialProvisionError(f"POST /model/new -> HTTP {status}: {body[:300]}")
    return json.loads(body).get("model_info", {}).get("id")


def deprovision_org_deployment(provider: str, org_id) -> bool:
    """Delete org_id's dedicated deployment for provider, if any.

    Idempotent — returns whether a deployment was actually found and deleted,
    so a caller can distinguish "removed" from "there was nothing to remove"
    without treating the latter as an error.
    """
    name = _org_deployment_name(provider, org_id)
    existing = _find_deployment(name)
    if existing is None:
        return False
    _delete_deployment(existing["model_info"]["id"])
    return True


def org_deployment_exists(provider: str, org_id) -> bool:
    return _find_deployment(_org_deployment_name(provider, org_id)) is not None


# ── org_settings-facing surface ─────────────────────────────────────────────


async def get_credential_status(conn, org_id, provider: str) -> dict:
    """{"provider", "source"} — deliberately never a deployment name or model
    id (Task 4's proof: an org must never see one)."""
    from services.org_settings import get_setting
    source = await get_setting(conn, org_id, credential_source_key(provider))
    return {"provider": provider, "source": source or CREDENTIAL_SOURCE_PLATFORM}


async def set_org_provider_credential(
    conn, pool, org_id, provider: str, api_key: str, updated_by, *, principal=None
) -> dict:
    """Provision a dedicated deployment, THEN flip credential_source to 'org'.

    Provisioning first, setting second, on purpose: if the live proxy call
    fails, the org's resolved source must stay exactly what it was — never
    claim 'org' with no real deployment behind it.
    """
    if provider not in PROVIDER_PLATFORM_DEPLOYMENT:
        raise CredentialProvisionError(f"Unknown/unsupported provider: {provider!r}")

    provision_org_deployment(provider, org_id, api_key)
    await set_setting(
        conn, org_id, credential_source_key(provider), CREDENTIAL_SOURCE_ORG,
        updated_by, principal=principal, pool=pool,
    )
    return {"provider": provider, "source": CREDENTIAL_SOURCE_ORG}


async def clear_org_provider_credential(
    conn, pool, org_id, provider: str, updated_by, *, principal=None
) -> dict:
    """Deprovision the dedicated deployment FIRST, then reset credential_source
    to 'platform'.

    Reversed order from set, on purpose: the prompt names the real hazard
    directly — "a stale deployment holding a revoked key". If the LiteLLM
    delete call fails, credential_source must stay 'org' (accurately
    reflecting that a deployment likely still exists) rather than silently
    reporting 'platform' while a live, still-keyed deployment sits unaccounted
    for on the proxy.
    """
    if provider not in PROVIDER_PLATFORM_DEPLOYMENT:
        raise CredentialProvisionError(f"Unknown/unsupported provider: {provider!r}")

    deprovision_org_deployment(provider, org_id)
    await set_setting(
        conn, org_id, credential_source_key(provider), CREDENTIAL_SOURCE_PLATFORM,
        updated_by, principal=principal, pool=pool,
    )
    return {"provider": provider, "source": CREDENTIAL_SOURCE_PLATFORM}
