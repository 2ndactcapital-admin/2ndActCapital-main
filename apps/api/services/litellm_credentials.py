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

LiteLLM Phase D1b (litellmphased1b.structural) — ROUTING + ATTRIBUTION.
D1a stopped at "can an org store its own key and get a deployment"; nothing
read ``ai.credential_source.*`` at call time. This sprint adds the two
functions below that ``services/extraction.py`` and
``services/document_embedding.py`` both call from their chain executors:

  * ``resolve_credential_source`` — this org's 'org'/'platform' flag for one
    provider, read fresh from org_settings (mirrors
    ``services.extraction.resolve_model``'s own fallback-to-default-on-error
    discipline, so a lookup failure can never silently grant org-key
    routing).
  * ``resolve_deployment_model`` — translates a logical model_id into the
    deployment that should actually serve the call. Translates ONLY when
    ``model_id`` is EXACTLY the platform deployment name this provider
    mirrors (``PROVIDER_PLATFORM_DEPLOYMENT[provider]``) AND the org's
    source is 'org' — every other model_id (an unregistered/dated string,
    or a provider still on 'platform') passes through unchanged. This is
    what keeps the caller-facing logical model name identical regardless of
    which deployment actually serves the call (the sprint's own stated
    requirement) — the caller never learns whether 'claude-sonnet' resolved
    to the platform deployment or the org's own mirror of it.
  * ``build_attribution`` — real metadata (Task 1b's probed field names:
    ``metadata.tags`` -> ``request_tags``, top-level ``user`` ->
    ``end_user``) so LiteLLM's own spend log can answer "which org, against
    whose key" — never a new ai_decision_log column, per the sprint's own
    "keep both logs, don't conflate them" precedent (§14.1 of the design
    doc). Platform-key-on-behalf-of-an-org is tagged distinctly from
    Hollisworks' own platform-key usage (``usage:platform_on_behalf_of_org``
    vs ``usage:hollisworks_platform``), and an org's own key gets its own
    tag (``usage:org_owned_key``) — three, not two, because "the org owns
    the key" and "Hollisworks' own usage" are both distinct from "platform
    key, but on someone else's behalf".
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

# Reverse of PROVIDER_PLATFORM_DEPLOYMENT — the deployment-name -> provider
# lookup ``resolve_deployment_model`` needs. Built once at import time; the
# forward map above is still the one source of truth (adding a provider
# there is all a future sprint needs to do).
PLATFORM_DEPLOYMENT_PROVIDER: dict[str, str] = {
    v: k for k, v in PROVIDER_PLATFORM_DEPLOYMENT.items()
}

# Hollisworks' own platform org (CLAUDE.md Rule 6) — the one org_id whose
# 'platform'-sourced calls are Hollisworks' OWN usage, not usage on behalf of
# a client org. A fixed, well-known constant, same convention
# routers/entities.py's own copy already uses.
HOLLISWORKS_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"


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


def list_deployments() -> list[dict]:
    """Public wrapper — every real LiteLLM deployment (Phase D2's
    services.model_catalog uses this for opportunistic context/pricing
    enrichment; never a second, parallel /model/info caller)."""
    return _model_info()


def _model_group_info() -> list[dict]:
    status, body = _http("/model_group/info")
    if status != 200:
        raise CredentialProvisionError(
            f"GET /model_group/info -> HTTP {status}: {body[:300]}"
        )
    return json.loads(body).get("data", [])


def list_model_group_info() -> list[dict]:
    """Public wrapper — LiteLLM Phase E. ``GET /model_group/info`` is the
    endpoint that actually reports ``supports_reasoning`` and
    ``supported_openai_params`` per registered model_name (probed live,
    Task 1c) — ``/model/info``'s per-deployment payload does carry
    ``supports_reasoning`` too, but ``/model_group/info`` is keyed on
    ``model_group`` (== our ``model_name``/model_id), a simpler match than
    ``/model/info``'s nested ``model_info``. Never a second, parallel
    caller — services.extraction and services.model_catalog both call this
    one wrapper."""
    return _model_group_info()


def reasoning_support_by_model() -> dict[str, bool]:
    """``{model_id: supports_reasoning}`` for every registered model_name,
    live. Best-effort: an unreachable proxy yields an empty dict rather than
    raising, so a caller gating an effort control fails CLOSED (no entry ->
    treated as unsupported -> no control, no parameter) rather than raising
    into an unrelated read path."""
    try:
        return {
            entry["model_group"]: bool(entry.get("supports_reasoning"))
            for entry in _model_group_info()
            if entry.get("model_group")
        }
    except Exception as exc:  # noqa: BLE001
        print(f"litellm_credentials: /model_group/info unavailable: {exc}")
        return {}


def chat_capable_models() -> set[str]:
    """model_group names with ``mode == "chat"`` — LiteLLM Phase E task
    assignment must refuse an embedding-only model (``voyage-3.5``) for a
    chat task dial (``ai.model.*``); the two axes (which models exist at
    all, and which of those speak the ``/v1/messages`` shape this module
    calls) are otherwise unrelated in platform_model_catalog, which has no
    ``mode`` column of its own. Best-effort: an unreachable proxy yields an
    empty set, same fail-closed discipline as ``reasoning_support_by_model``
    (an unknown model is simply not assignable rather than assumed chat)."""
    try:
        return {
            entry["model_group"] for entry in _model_group_info()
            if entry.get("model_group") and entry.get("mode") == "chat"
        }
    except Exception as exc:  # noqa: BLE001
        print(f"litellm_credentials: /model_group/info unavailable: {exc}")
        return set()


def spend_by_tag(start_date: str, end_date: str) -> dict[str, dict]:
    """``{tag_name: {"spend": float, "log_count": int}}`` for the given date
    range (LiteLLM Phase G budgets) — ``GET /global/spend/tags``, a real,
    CORE (non-Enterprise) LiteLLM endpoint confirmed live on this
    self-hosted instance. ``/global/spend/report`` (LiteLLM's other
    aggregation endpoint) is Enterprise-gated — confirmed live, HTTP 400
    "You must be a LiteLLM Enterprise user" — so this is the one real
    aggregation surface this deployment has. D1b's attribution tags
    (``org:<uuid>``, ``usage:hollisworks_platform``,
    ``usage:platform_on_behalf_of_org``) are genuine entries in this
    response, not merely present per-row in ``/spend/logs``."""
    status, body = _http(f"/global/spend/tags?start_date={start_date}&end_date={end_date}")
    if status != 200:
        raise CredentialProvisionError(f"GET /global/spend/tags -> HTTP {status}: {body[:300]}")
    data = json.loads(body).get("spend_per_tag", [])
    return {
        e["name"]: {"spend": e.get("spend", 0.0), "log_count": e.get("log_count", 0)}
        for e in data if e.get("name")
    }


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


# ── D1b: routing + attribution ──────────────────────────────────────────────


async def resolve_credential_source(org_id, provider: str) -> str:
    """This org's ``ai.credential_source.<provider>`` value, read fresh from
    org_settings. Falls back to CREDENTIAL_SOURCE_PLATFORM — the status quo —
    on a missing org_id, an unknown provider, or any lookup error, mirroring
    services.extraction.resolve_model's own fail-to-default discipline. A
    lookup failure must never silently grant org-key routing."""
    if org_id is None or provider not in PROVIDER_PLATFORM_DEPLOYMENT:
        return CREDENTIAL_SOURCE_PLATFORM
    try:
        from services.database import get_pool
        from services.org_settings import get_setting

        pool = await get_pool()
        async with pool.acquire() as conn:
            value = await get_setting(conn, org_id, credential_source_key(provider))
        return value or CREDENTIAL_SOURCE_PLATFORM
    except Exception as exc:
        print(f"resolve_credential_source failed for {provider!r}, using "
              f"platform: {exc}")
        return CREDENTIAL_SOURCE_PLATFORM


def resolve_deployment_model(model_id: str, org_id, provider: str,
                              credential_source: str) -> str:
    """Which deployment should actually serve a call requesting ``model_id``.

    Translates ONLY when ``model_id`` is exactly the platform deployment
    ``provider`` mirrors (``PROVIDER_PLATFORM_DEPLOYMENT[provider]``) AND
    ``credential_source == 'org'`` — every other model_id (an
    unregistered/dated string never registered as its own deployment, or a
    provider still on 'platform') passes through completely unchanged. This
    is what keeps the caller-facing logical model name identical regardless
    of which deployment actually serves the call: a caller that asked for
    'claude-sonnet' never learns whether the platform deployment or the
    org's own mirror of it answered.
    """
    if credential_source != CREDENTIAL_SOURCE_ORG:
        return model_id
    if PROVIDER_PLATFORM_DEPLOYMENT.get(provider) != model_id:
        return model_id
    return _org_deployment_name(provider, org_id)


def build_attribution(org_id, provider: str, credential_source: str) -> dict:
    """Real, queryable metadata for LiteLLM's own spend log (Task 1b's
    probed field names — see module docstring): ``tags`` lands in the spend
    log's ``request_tags`` column, ``end_user`` is what the caller should
    send as the top-level ``user`` request field (lands in ``end_user``).

    ``usage`` is one of three labels, not two, because "the org owns the
    key", "Hollisworks is using the platform key for itself", and "the
    platform key is being used on behalf of some OTHER org" are three
    genuinely distinct things a spend-log reader needs to tell apart:

      * 'org_owned_key'            — this org supplied its own provider key.
      * 'hollisworks_platform'     — the platform key, used for Hollisworks'
                                      own org (HOLLISWORKS_ORG_ID).
      * 'platform_on_behalf_of_org' — the platform key, used for some OTHER
                                      org that has not (yet) supplied its own.
    """
    org_str = str(org_id)
    if credential_source == CREDENTIAL_SOURCE_ORG:
        usage = "org_owned_key"
    elif org_str == HOLLISWORKS_ORG_ID:
        usage = "hollisworks_platform"
    else:
        usage = "platform_on_behalf_of_org"
    tags = [
        f"org:{org_str}",
        f"provider:{provider}",
        f"credential_source:{credential_source}",
        f"usage:{usage}",
    ]
    return {"tags": tags, "end_user": f"org:{org_str}", "usage": usage}


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
