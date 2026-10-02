"""The ONE HTTP chokepoint for ensemble calls through the LiteLLM proxy.

WHY NOT services.extraction.call_claude_json
──────────────────────────────────────────────────────────────────────────────
That helper is the chokepoint for the app's Anthropic-shaped calls: it speaks
``/v1/messages`` and walks the APP fallback chain (``ai.model.fallback_chain``)
on failure — the exact mechanism that silently collapsed the old two-model
hazard ensemble into one model agreeing with itself. An ensemble slot needs
the opposite: ONE attempt at ONE named deployment, no fallback anywhere,
structured output, and proof of which model answered. So ensemble calls go
through here instead — still only through the proxy, still with the app's
LiteLLM key, never to a provider directly, never via the litellm SDK.

WHAT EVERY CALL CARRIES (measured on v1.96.2, Task 1e)
──────────────────────────────────────────────────────────────────────────────
  * ``disable_fallbacks: true`` — honoured per request (proven live in the
    ensemblemodels sprint). The proxy has no router fallbacks configured
    either; ``x-litellm-attempted-fallbacks`` must come back 0.
  * ``metadata._complexity_router_return_raw_model_name: true`` — WITHOUT it
    the proxy rewrites the response's ``model`` to the alias we asked for, so
    the body would only echo the request. With it the body carries the model
    the PROVIDER reported (e.g. ``claude-haiku-4-5-20251001``). That is the
    value recorded as ``provider_model``.
  * ``x-litellm-model-id`` (response header) — the exact deployment that
    served the call; ``x-litellm-response-cost`` — the proxy's cost from its
    LIVE price list.

A deployment name that is not registered returns HTTP 400 — it is never
answered by some other model. A model name with two deployments behind it
would be load-balanced, so ``deployment_catalog`` refuses such a name.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import httpx

RAW_MODEL_METADATA_KEY = "_complexity_router_return_raw_model_name"
DEFAULT_TIMEOUT = 180.0

# Every request that leaves this module is counted. Dry runs assert this stays 0.
CALLS: dict[str, int] = {"chat": 0, "jev": 0}


class ProxyConfigError(RuntimeError):
    pass


def _base_and_key() -> tuple[str, str]:
    base = (os.environ.get("LITELLM_BASE_URL") or "").rstrip("/")
    key = os.environ.get("LITELLM_MASTER_KEY") or ""
    if not base or not key:
        raise ProxyConfigError("LITELLM_BASE_URL / LITELLM_MASTER_KEY are not configured")
    return base, key


@dataclass
class ProxyResponse:
    status: int
    body: dict | None
    text: str
    headers: dict = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None


_HEADER_KEYS = (
    "x-litellm-model-id", "x-litellm-model-name", "x-litellm-model-group",
    "x-litellm-attempted-fallbacks", "x-litellm-response-cost", "x-litellm-call-id",
    "x-litellm-version",
)


async def _post(path: str, body: dict, timeout: float) -> ProxyResponse:
    """The single network function. Tests replace THIS to mock providers."""
    base, key = _base_and_key()
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}{path}", json=body,
                                  headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        return ProxyResponse(0, None, "", {}, int((time.monotonic() - t0) * 1000),
                             f"proxy unreachable: {type(exc).__name__}")
    latency = int((time.monotonic() - t0) * 1000)
    try:
        parsed = r.json()
    except ValueError:
        parsed = None
    headers = {k: r.headers.get(k) for k in _HEADER_KEYS if r.headers.get(k) is not None}
    return ProxyResponse(r.status_code, parsed if isinstance(parsed, dict) else None,
                         r.text[:2000], headers, latency)


async def chat(body: dict, *, timeout: float = DEFAULT_TIMEOUT) -> ProxyResponse:
    """POST /v1/chat/completions with the no-fallback + raw-model flags forced on."""
    body = dict(body)
    body["disable_fallbacks"] = True
    meta = dict(body.get("metadata") or {})
    meta[RAW_MODEL_METADATA_KEY] = True
    body["metadata"] = meta
    CALLS["chat"] += 1
    return await _post("/v1/chat/completions", body, timeout)


async def jev(body: dict, *, timeout: float = 120.0) -> ProxyResponse:
    """POST /typesafe/v1/systemone — Jev through the proxy's pass-through. The
    TypeSafe key is injected by the proxy; this process never holds it."""
    CALLS["jev"] += 1
    return await _post("/typesafe/v1/systemone", body, timeout)


# ── Deployment catalogue (from the proxy's own /model/info) ──────────────────
@dataclass
class Deployment:
    name: str
    deployment_id: str | None
    upstream: str | None
    price_key: str | None
    input_cost_per_token: float | None
    output_cost_per_token: float | None
    cache_read_cost_per_token: float | None
    supports_response_schema: bool
    max_input_tokens: int | None
    duplicate: bool = False


def deployment_catalog() -> dict[str, Deployment]:
    """{model_name: Deployment} from GET /model/info. A name served by more
    than one deployment is marked ``duplicate`` (it would load-balance) and
    callers must refuse it."""
    from services.litellm_credentials import list_deployments

    out: dict[str, Deployment] = {}
    for d in list_deployments():
        name = d.get("model_name")
        if not name:
            continue
        info = d.get("model_info") or {}
        lp = d.get("litellm_params") or {}
        dep = Deployment(
            name=name,
            deployment_id=info.get("id"),
            upstream=lp.get("model"),
            price_key=info.get("key"),
            input_cost_per_token=info.get("input_cost_per_token"),
            output_cost_per_token=info.get("output_cost_per_token"),
            cache_read_cost_per_token=info.get("cache_read_input_token_cost"),
            supports_response_schema=bool(info.get("supports_response_schema")),
            max_input_tokens=info.get("max_input_tokens"),
        )
        if name in out:
            out[name].duplicate = True
            continue
        out[name] = dep
    return out


def reported_model_matches(upstream: str | None, reported: str | None) -> bool:
    """Does the provider-reported model belong to the deployment we asked for?

    Compared on the LAST path component, case-insensitively, allowing a dated
    suffix on either side: upstream ``openai/gpt-5-nano`` matches a reported
    ``gpt-5-nano-2025-08-07``; ``deepinfra/openai/gpt-oss-120b`` matches
    ``openai/gpt-oss-120b``. A different model family never matches."""
    if not upstream or not reported:
        return False
    a = upstream.rsplit("/", 1)[-1].lower()
    b = reported.rsplit("/", 1)[-1].lower()
    return a == b or b.startswith(a + "-") or a.startswith(b + "-") or b.startswith(a + "@")


def header_cost(resp: ProxyResponse) -> float | None:
    raw = resp.headers.get("x-litellm-response-cost")
    try:
        v = float(raw) if raw not in (None, "", "None") else None
    except ValueError:
        return None
    return v


def response_usage(body: dict | None) -> tuple[int | None, int | None, int | None]:
    usage = (body or {}).get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = usage.get("cache_read_input_tokens")
    return usage.get("prompt_tokens"), usage.get("completion_tokens"), cached


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
