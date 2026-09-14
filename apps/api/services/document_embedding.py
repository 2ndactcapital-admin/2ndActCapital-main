"""Chancery Phase 11b — document embedding service (semantic INDEX).

The SINGLE choke point for turning a document's extracted content into a stored
pgvector embedding, and for embedding a search query at RETRIEVE time. Mirrors
the provider-abstraction discipline of ``services/extraction.py`` (the Claude
choke point) so the embedding provider stays swappable by config, not code — the
"Mini-Bedrock" pattern extended to a new ``ai.embedding.*`` namespace.

PROVIDER ABSTRACTION — four named options, one functionally enabled
──────────────────────────────────────────────────────────────────────────────
Each org may SEE the real competitive landscape (Voyage, OpenAI, Google, Cohere)
in settings, but ONLY Voyage is wired to a real API call today. The other three
are named/stubbed: calling them NEVER attempts a real network request — they
raise ``EmbeddingProviderNotEnabled`` immediately. The authoritative enforcement
that an org cannot even SELECT a non-Voyage provider lives in
``services/org_settings.py`` (backend validation on write); this module is the
second line: even if a bad value existed, no non-Voyage API call is ever made.

DIMENSION
──────────────────────────────────────────────────────────────────────────────
Voyage's real current output dimension is 1024 (verified live at Task 1 for
voyage-3.5 / voyage-law-2 / voyage-finance-2). The ``document_embeddings.embedding``
column is ``vector(1024)``; we assert the returned vector length matches before
storing, so a provider/model that ever returned a different width fails loudly
rather than corrupting the column.

CREDENTIALS
──────────────────────────────────────────────────────────────────────────────
``VOYAGE_API_KEY`` comes from the environment (Render) and, for local/verify
runs, falls back to ``apps/api/.env`` — the same file the FastAPI Settings model
loads. The key is NEVER printed or logged.

LiteLLM Phase C (litellmphasec.structural) — Voyage embeddings now route
through the same self-hosted LiteLLM proxy as every text call
(``services/extraction.py``), instead of calling Voyage's API directly with raw
``httpx``. This closes the gap the original LiteLLM discovery sprint found:
embeddings bypassed the router entirely, had no fallback chain, and wrote no
``ai_decision_log`` row. The transport swap reuses ``services/extraction.py``'s
own transport resolver (``resolve_transport`` / the ``LITELLM_ROUTING_DISABLED``
rollback switch / ``LITELLM_BASE_URL`` / ``LITELLM_MASTER_KEY``) rather than a
second, parallel copy — one rollback switch disables LiteLLM routing for BOTH
text and embeddings at once, which is the correct blast radius for an ops
escape hatch. LiteLLM serves an OpenAI-shaped ``POST /v1/embeddings`` route
(confirmed live; ``/embeddings`` also answers identically, but ``/v1/embeddings``
is used to match the documented OpenAI-compatible surface). When LiteLLM is not
the transport (rollback engaged, or not configured), this module calls Voyage
directly — the same code path this module always used, now the explicit
fallback rather than the only path.

The embedding fallback chain is a SEPARATE ``ai.embedding.fallback_chain``
org_settings key, not a shared chain with ``ai.model.fallback_chain`` — see the
module docstring's dimension note above. A model swap mid-chain that returned a
different dimensionality would silently corrupt vector search, so a per-attempt
dimension check (against the org's configured ``ai.embedding.dimensions``) gates
every candidate in the chain, not just the one ultimately stored.
"""

import asyncio
import json
import os
import time
from decimal import Decimal

import httpx

from services.database import get_pool, reset_rls_context, set_rls_context
from services.org_settings import get_setting

# ── Provider registry ───────────────────────────────────────────────────────
# The FOUR options an org sees. Order is the display order for the admin dropdown
# (surfaced via GET below). Only VOYAGE is functionally enabled.
EMBEDDING_PROVIDERS = ["voyage", "openai", "google", "cohere"]
EMBEDDING_PROVIDER_LABELS = {
    "voyage": "Voyage AI",
    "openai": "OpenAI",
    "google": "Google",
    "cohere": "Cohere",
}
ENABLED_EMBEDDING_PROVIDERS = frozenset({"voyage"})

# The single, exact message the backend returns when a non-Voyage provider is
# selected. Shared with services/org_settings.py so the write-time rejection and
# any call-time rejection read identically.
EMBEDDING_PROVIDER_DISABLED_MSG = "Voyage is the only model enabled right now"

# Settings keys (the new ai.embedding.* namespace, following the real
# ai.model.* convention in services/org_settings.py DEFAULT_SETTINGS).
EMBEDDING_PROVIDER_KEY = "ai.embedding.provider"
EMBEDDING_MODEL_KEY = "ai.embedding.model"
EMBEDDING_DIMENSIONS_KEY = "ai.embedding.dimensions"
# Phase C — the embedding-side fallback chain. Deliberately its own key, not
# ai.model.fallback_chain — see module docstring.
EMBEDDING_FALLBACK_CHAIN_KEY = "ai.embedding.fallback_chain"

# task_type values written to ai_decision_log — same table/shape text calls use.
EMBED_TASK_DOCUMENT = "embedding_document"
EMBED_TASK_QUERY = "embedding_query"

# USD per 1M input tokens, by provider. Voyage's real live price (verified via
# LiteLLM's GET /model/info -> input_cost_per_token == 6e-08 for voyage-3.5,
# i.e. $0.06 / 1M tokens) at Phase C registration time. Embeddings have no
# output tokens, unlike extraction.py's _MODEL_PRICING table.
_EMBEDDING_PRICING: dict[str, Decimal] = {
    "voyage": Decimal("0.06"),
}

# Voyage's real defaults (verified live at Task 1).
DEFAULT_EMBEDDING_PROVIDER = "voyage"
DEFAULT_EMBEDDING_MODEL = "voyage-3.5"
EMBEDDING_DIMENSIONS = 1024

VOYAGE_ENDPOINT = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_KEY_NAMES = ("VOYAGE_API_KEY", "VOYAGEAI_API_KEY", "VOYAGE_KEY")

# Cap on how much text we embed per document. Voyage models accept up to ~32k
# tokens; a generous character cap keeps a single high-signal embedding per
# document while bounding cost and staying well under the token limit.
_MAX_EMBED_CHARS = 20000


class EmbeddingProviderNotEnabled(RuntimeError):
    """Raised when a provider other than Voyage is asked to embed.

    A named-but-stubbed provider (openai/google/cohere) never makes a real API
    call — it raises this immediately so a misconfiguration can never leak data
    to an unintended vendor.
    """


class EmbeddingUnavailable(RuntimeError):
    """Raised when Voyage is selected but no usable credential / no response."""


# ── credential ──────────────────────────────────────────────────────────────
def _voyage_api_key() -> str | None:
    """Return the Voyage key from the env, falling back to apps/api/.env.

    Never returns whitespace-only values. Never logs the key.
    """
    for name in _VOYAGE_KEY_NAMES:
        v = os.environ.get(name)
        if v and v.strip():
            return v.strip()
    # Local/verify fallback: parse apps/api/.env (the Settings env_file).
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(os.path.dirname(here), ".env")
    try:
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, val = line.partition("=")
                if k.strip() in _VOYAGE_KEY_NAMES:
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return val
    except OSError:
        pass
    return None


def voyage_configured() -> bool:
    """True when a usable Voyage credential is present (cheap pre-check)."""
    return bool(_voyage_api_key())


# ── provider dispatch ─────────────────────────────────────────────────────────
# Rate-limit / transient handling. Voyage throttles by RPM+TPM; on 429 it sends
# a Retry-After. We honor it (bounded) and retry a few times so a burst of
# embeddings degrades to "slower" rather than "failed".
_VOYAGE_MAX_ATTEMPTS = 6
_VOYAGE_MAX_BACKOFF_S = 60.0


async def _embed_voyage(texts, model, *, input_type=None) -> list[list[float]]:
    """REAL Voyage embeddings call. ``input_type`` is 'document' | 'query'."""
    key = _voyage_api_key()
    if not key:
        raise EmbeddingUnavailable(
            "Voyage is the configured provider but no VOYAGE_API_KEY is set"
        )
    payload = {"input": texts, "model": model}
    if input_type:
        payload["input_type"] = input_type
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    last_status = last_body = None
    for attempt in range(_VOYAGE_MAX_ATTEMPTS):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(VOYAGE_ENDPOINT, json=payload, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            vectors = [row["embedding"] for row in data.get("data", [])]
            if not vectors:
                raise EmbeddingUnavailable("Voyage API returned no embeddings")
            return vectors
        last_status, last_body = resp.status_code, resp.text[:200]
        # 429 (rate limit) and 5xx (transient) are retryable.
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < _VOYAGE_MAX_ATTEMPTS - 1:
            retry_after = resp.headers.get("retry-after")
            try:
                wait = float(retry_after) if retry_after else (attempt + 1) * 20.0
            except ValueError:
                wait = (attempt + 1) * 20.0
            await asyncio.sleep(min(wait, _VOYAGE_MAX_BACKOFF_S))
            continue
        break
    raise EmbeddingUnavailable(
        f"Voyage API returned HTTP {last_status}: {last_body}"
    )


async def _embed_stub(provider):
    """A named-but-not-enabled provider. NEVER makes a real API call."""
    raise EmbeddingProviderNotEnabled(
        f"{EMBEDDING_PROVIDER_LABELS.get(provider, provider)} embedding is not "
        f"enabled. {EMBEDDING_PROVIDER_DISABLED_MSG}."
    )


# ── LiteLLM transport (Phase C) ───────────────────────────────────────────────
async def _embed_litellm(texts, model, *, input_type=None) -> tuple[list[list[float]], dict | None]:
    """REAL embeddings call through the LiteLLM proxy's OpenAI-shaped route.

    Confirmed live against hollisworks-litellm: both ``/v1/embeddings`` and
    ``/embeddings`` answer identically; ``/v1/embeddings`` is used to match the
    documented OpenAI-compatible surface. Returns ``(vectors, usage)`` — usage
    (token counts) feeds the cost estimate written to ai_decision_log, mirroring
    how extraction.py computes cost from Anthropic's usage object.
    """
    from services import extraction as ex

    base_url = os.environ[ex.LITELLM_BASE_URL_VAR].rstrip("/")
    master_key = os.environ[ex.LITELLM_MASTER_KEY_VAR]
    payload = {"model": model, "input": texts}
    if input_type:
        payload["input_type"] = input_type
    headers = {"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{base_url}/v1/embeddings", json=payload, headers=headers)
    if resp.status_code != 200:
        raise EmbeddingUnavailable(
            f"LiteLLM embeddings HTTP {resp.status_code} at {base_url}/v1/embeddings: "
            f"{resp.text[:200]}"
        )
    data = resp.json()
    vectors = [row["embedding"] for row in data.get("data", [])]
    if not vectors:
        raise EmbeddingUnavailable("LiteLLM /v1/embeddings returned no embeddings")
    return vectors, data.get("usage")


def _compute_embedding_cost(usage) -> Decimal | None:
    """Dollar cost of one embedding call from LiteLLM's usage object.

    Voyage is the only provider ever reaching this point (embed_texts gates on
    provider == 'voyage' before calling the chain), so a single, known-live
    price applies — no per-model lookup table needed today.
    """
    if not usage:
        return None
    tokens = usage.get("total_tokens") or usage.get("prompt_tokens") or 0
    if not tokens:
        return None
    price = _EMBEDDING_PRICING.get("voyage")
    if price is None:
        return None
    return ((Decimal(tokens) / Decimal(1_000_000)) * price).quantize(Decimal("0.00000001"))


async def resolve_embedding_fallback_chain(conn, org_id) -> list[str]:
    """The org's ordered embedding fallback chain.

    Mirrors services/extraction.py's resolve_fallback_chain, reading the
    embedding-specific EMBEDDING_FALLBACK_CHAIN_KEY instead of
    ai.model.fallback_chain — see the module docstring for why the two chains
    are deliberately separate.
    """
    from services.org_settings import DEFAULT_SETTINGS

    default = DEFAULT_SETTINGS.get(EMBEDDING_FALLBACK_CHAIN_KEY) or []
    chain = await get_setting(conn, org_id, EMBEDDING_FALLBACK_CHAIN_KEY)
    chain = chain if chain else default
    if isinstance(chain, str):  # tolerate a mis-stored scalar
        chain = [chain]
    return [m for m in (chain or []) if isinstance(m, str) and m]


def _embedding_credential_state() -> tuple[bool, str]:
    """Is there a usable path to a real embedding call right now?

    True when LiteLLM is the resolved transport (the proxy holds its own
    VOYAGE_API_KEY server-side — see render/Doppler prd_lite_llm), OR when this
    process has its own VOYAGE_API_KEY for the direct-Voyage fallback (rollback
    engaged, or LiteLLM not configured). Mirrors the two real paths
    _execute_embedding_chain can actually take.
    """
    from services import extraction as ex

    transport, reason = ex.resolve_transport()
    if transport == ex.TRANSPORT_LITELLM:
        return True, ""
    if voyage_configured():
        return True, ""
    return False, (
        f"no VOYAGE_API_KEY for the direct-Voyage fallback, and LiteLLM is not "
        f"the transport ({reason})"
    )


async def _execute_embedding_chain(
    texts, *, org_id, task_type, model, chain, input_type, expected_dims,
) -> list[list[float]]:
    """Walk the org's embedding fallback chain, timing/costing/logging every
    attempt — the embedding-side twin of extraction.py's _execute_chain.

    Tries ``model`` first, then each model in ``chain``, until one both
    succeeds AND returns a vector of ``expected_dims`` width (a wrong-width
    response is treated as a failed attempt, never silently stored — the
    embedding-compatibility rule). Writes exactly one ai_decision_log row via
    extraction.py's own _safe_log, so embeddings land in the SAME table with
    the SAME shape text calls use. Raises EmbeddingUnavailable when the whole
    chain is exhausted.
    """
    from services import extraction as ex

    transport, transport_reason = ex.resolve_transport()
    if transport_reason:
        print(f"[embedding_router] transport={transport}: {transport_reason}")

    attempts = list(dict.fromkeys([m for m in [model, *(chain or [])] if m]))
    t0 = time.monotonic()
    last_error = None
    for model_id in attempts:
        try:
            if transport == ex.TRANSPORT_LITELLM:
                vectors, usage = await _embed_litellm(texts, model_id, input_type=input_type)
            else:
                vectors = await _embed_voyage(texts, model_id, input_type=input_type)
                usage = None
        except Exception as exc:  # noqa: BLE001 — any transport/provider failure
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"[embedding_router] model '{model_id}' failed for task "
                  f"'{task_type}' via {transport}: {last_error}")
            continue

        if expected_dims and vectors and len(vectors[0]) != expected_dims:
            last_error = (f"model '{model_id}' returned dim {len(vectors[0])}, "
                           f"expected {expected_dims}")
            print(f"[embedding_router] {last_error}")
            continue

        latency_ms = max(1, int((time.monotonic() - t0) * 1000))
        fallback_used = model_id != model
        await ex._safe_log(
            org_id=org_id, task_type=task_type, model_requested=model,
            model_used=model_id, fallback_used=fallback_used,
            fallback_reason=(
                f"primary '{model}' failed: {last_error}" if fallback_used else None
            ),
            cost_usd=_compute_embedding_cost(usage),
            latency_ms=latency_ms, success=True, error_detail=None,
        )
        return vectors

    latency_ms = max(1, int((time.monotonic() - t0) * 1000))
    fallback_used = len(attempts) > 1
    await ex._safe_log(
        org_id=org_id, task_type=task_type, model_requested=model,
        model_used=(attempts[-1] if attempts else model),
        fallback_used=fallback_used,
        fallback_reason=(
            f"all {len(attempts)} model(s) in chain failed" if fallback_used else None
        ),
        cost_usd=None, latency_ms=latency_ms, success=False, error_detail=last_error,
    )
    raise EmbeddingUnavailable(
        f"All models failed for embedding task '{task_type}' (chain={attempts}): "
        f"{last_error}"
    )


async def embed_texts(
    texts, *, provider=DEFAULT_EMBEDDING_PROVIDER, model=DEFAULT_EMBEDDING_MODEL,
    input_type=None, org_id=None, task_type=EMBED_TASK_DOCUMENT,
    fallback_chain=None, expected_dims=None,
) -> list[list[float]]:
    """Embed a batch of texts through the given provider abstraction.

    Only Voyage is functionally wired; every other listed provider raises
    ``EmbeddingProviderNotEnabled`` WITHOUT touching the network. A Voyage call
    routes through LiteLLM (or its direct fallback) via the chain executor,
    which is what writes the ai_decision_log row — ``org_id``/``task_type`` are
    only meaningful for that path.
    """
    provider = (provider or DEFAULT_EMBEDDING_PROVIDER).lower()
    if provider == "voyage":
        return await _execute_embedding_chain(
            texts, org_id=org_id, task_type=task_type, model=model,
            chain=fallback_chain, input_type=input_type, expected_dims=expected_dims,
        )
    if provider in EMBEDDING_PROVIDERS:
        return await _embed_stub(provider)
    raise EmbeddingProviderNotEnabled(
        f"Unknown embedding provider {provider!r}. {EMBEDDING_PROVIDER_DISABLED_MSG}."
    )


# ── org config resolution ─────────────────────────────────────────────────────
async def resolve_embedding_config(conn, org_id) -> tuple[str, str, int]:
    """Return the org's ``(provider, model, dimensions)`` for embeddings.

    Reads the ai.embedding.* keys via the same get_setting resolver used for
    ai.model.*; falls back to the Voyage defaults for any unset key. org_id is
    always supplied by the caller (URL/JWT), never a request body.
    """
    provider = await get_setting(conn, org_id, EMBEDDING_PROVIDER_KEY)
    model = await get_setting(conn, org_id, EMBEDDING_MODEL_KEY)
    dims = await get_setting(conn, org_id, EMBEDDING_DIMENSIONS_KEY)
    return (
        (provider or DEFAULT_EMBEDDING_PROVIDER),
        (model or DEFAULT_EMBEDDING_MODEL),
        int(dims or EMBEDDING_DIMENSIONS),
    )


def to_pgvector(vec) -> str:
    """Render a Python float list as a pgvector literal for ``$n::vector``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# ── content assembly (Task 1f) ────────────────────────────────────────────────
async def _assemble_document_text(conn, org_id, document_id) -> tuple[str, str]:
    """Build the highest-signal text to embed for a document, and label it.

    Preference order per document type:
      1. NARRATIVE docs — the distilled summary + key provisions produced by
         Phase 11a (document_narrative_extractions). Concise, high-signal.
      2. TABULAR/K-1 docs — the structured mapped_fields from Phase 3
         (document_template_extractions), flattened to "field: value" lines.
      3. Everything else — the raw extracted prose (document_extractions).
    Filename + doc_family are always prepended for lexical anchoring. All
    sources are concatenated (narrative/template ADD to, not replace, the prose)
    then truncated to _MAX_EMBED_CHARS. Returns (text, content_source_label).
    """
    doc = await conn.fetchrow(
        "SELECT original_filename, doc_family FROM documents "
        "WHERE id = $1 AND org_id = $2",
        document_id, org_id,
    )
    parts: list[str] = []
    sources: list[str] = []
    if doc:
        header = doc["original_filename"] or ""
        if doc["doc_family"]:
            header = f"{header} ({doc['doc_family']})"
        if header.strip():
            parts.append(header.strip())

    # 1. narrative summary + provisions
    nar = await conn.fetchrow(
        "SELECT summary, extracted_provisions FROM document_narrative_extractions "
        "WHERE document_id = $1 AND org_id = $2 ORDER BY created_at DESC LIMIT 1",
        document_id, org_id,
    )
    if nar:
        if nar["summary"]:
            parts.append(nar["summary"])
            sources.append("narrative_summary")
        provisions = nar["extracted_provisions"]
        if isinstance(provisions, (str, bytes)):
            try:
                provisions = json.loads(provisions)
            except (ValueError, TypeError):
                provisions = None
        if provisions:
            flat = _flatten_json(provisions)
            if flat:
                parts.append(flat)
                sources.append("narrative_provisions")

    # 2. template / K-1 mapped fields
    tmpl = await conn.fetchrow(
        "SELECT mapped_fields FROM document_template_extractions "
        "WHERE document_id = $1 AND org_id = $2 ORDER BY created_at DESC LIMIT 1",
        document_id, org_id,
    )
    if tmpl and tmpl["mapped_fields"]:
        fields = tmpl["mapped_fields"]
        if isinstance(fields, (str, bytes)):
            try:
                fields = json.loads(fields)
            except (ValueError, TypeError):
                fields = None
        flat = _flatten_json(fields)
        if flat:
            parts.append(flat)
            sources.append("template_fields")

    # 3. raw extracted prose
    prose = await conn.fetchval(
        "SELECT extracted_text FROM document_extractions "
        "WHERE document_id = $1 AND org_id = $2 ORDER BY created_at DESC LIMIT 1",
        document_id, org_id,
    )
    if prose and prose.strip():
        parts.append(prose.strip())
        sources.append("extracted_text")

    text = "\n\n".join(p for p in parts if p and p.strip())[:_MAX_EMBED_CHARS]
    label = "+".join(sources) if sources else "filename_only"
    return text, label


def _flatten_json(value) -> str:
    """Flatten a jsonb provision/field structure into readable "key: value" text."""
    lines: list[str] = []

    def walk(v, prefix=""):
        if isinstance(v, dict):
            for k, sub in v.items():
                walk(sub, f"{prefix}{k}: " if not prefix else f"{prefix}{k}: ")
        elif isinstance(v, list):
            for item in v:
                walk(item, prefix)
        else:
            s = str(v).strip()
            if s:
                lines.append(f"{prefix}{s}" if prefix else s)

    walk(value)
    return "\n".join(lines)


# ── INDEX: embed + store one document ─────────────────────────────────────────
async def embed_document(pool, doc, org_id) -> dict:
    """Embed a document's relevant content and upsert one row into
    document_embeddings. Sets its own RLS context so it is safe both nested in
    the SORT pipeline and standalone (mirrors narrative_extraction).

    Returns an outcome dict: {"outcome": "embedded"|"skipped", ...}. Never
    raises for the ordinary "no content" / "provider unavailable" cases — the
    SORT hook treats those like every other degrade-gracefully external dep.
    """
    document_id = doc["id"]
    tokens = set_rls_context(org_id, False)
    try:
        async with pool.acquire() as conn:
            provider, model, dims = await resolve_embedding_config(conn, org_id)
            chain = await resolve_embedding_fallback_chain(conn, org_id)
            text, source = await _assemble_document_text(conn, org_id, document_id)
        if not text.strip():
            return {"outcome": "skipped", "reason": "no extractable content"}
        if provider == "voyage":
            available, why = _embedding_credential_state()
            if not available:
                return {"outcome": "skipped", "reason": f"no Voyage credential: {why}"}

        vectors = await embed_texts(
            [text], provider=provider, model=model, input_type="document",
            org_id=org_id, task_type=EMBED_TASK_DOCUMENT, fallback_chain=chain,
            expected_dims=dims,
        )
        vec = vectors[0]
        if len(vec) != dims:
            raise EmbeddingUnavailable(
                f"provider returned dim {len(vec)}, expected {dims} "
                f"(column is vector({EMBEDDING_DIMENSIONS}))"
            )

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO document_embeddings
                    (document_id, org_id, provider, model, dimensions,
                     content_source, content_chars, embedding, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector, now())
                ON CONFLICT (document_id) DO UPDATE
                    SET org_id         = EXCLUDED.org_id,
                        provider       = EXCLUDED.provider,
                        model          = EXCLUDED.model,
                        dimensions     = EXCLUDED.dimensions,
                        content_source = EXCLUDED.content_source,
                        content_chars  = EXCLUDED.content_chars,
                        embedding      = EXCLUDED.embedding,
                        updated_at     = now()
                """,
                document_id, org_id, provider, model, dims,
                source, len(text), to_pgvector(vec),
            )
        return {
            "outcome": "embedded",
            "provider": provider,
            "model": model,
            "dimensions": dims,
            "content_source": source,
            "content_chars": len(text),
        }
    finally:
        reset_rls_context(tokens)


# ── RETRIEVE: embed a query ───────────────────────────────────────────────────
async def embed_query(pool, org_id, query_text) -> list[float]:
    """Embed a search query via the org's configured (currently always Voyage)
    provider. Raises EmbeddingUnavailable / EmbeddingProviderNotEnabled on a
    real problem so the search endpoint can surface a clean error."""
    tokens = set_rls_context(org_id, False)
    try:
        async with pool.acquire() as conn:
            provider, model, dims = await resolve_embedding_config(conn, org_id)
            chain = await resolve_embedding_fallback_chain(conn, org_id)
        vectors = await embed_texts(
            [query_text], provider=provider, model=model, input_type="query",
            org_id=org_id, task_type=EMBED_TASK_QUERY, fallback_chain=chain,
            expected_dims=dims,
        )
        return vectors[0]
    finally:
        reset_rls_context(tokens)


# ── Task 4: the re-indexing friction dialog ───────────────────────────────────
# Chars-per-token heuristic for the cost estimate below. Not exact (no
# tokenizer call is made for an estimate), but the SAME rough ratio the module
# already leans on implicitly via _MAX_EMBED_CHARS (~20000 chars / ~5000
# tokens, Voyage's stated ~32k-token ceiling) — consistent, not a new
# assumption.
_CHARS_PER_TOKEN_ESTIMATE = 4


async def _live_price_per_million(model_name: str) -> tuple[Decimal | None, str]:
    """The REAL live per-1M-token price LiteLLM has for ``model_name``.

    Read from GET /model/info — never a hardcoded local price table — so the
    friction dialog can never show a stale or guessed number. Returns
    ``(None, reason)`` when no live price is available (LiteLLM not
    configured, unreachable, or the model is not a registered deployment);
    the caller surfaces ``reason`` in the dialog rather than silently omitting
    the cost line.
    """
    from services import extraction as ex

    base_url = os.environ.get(ex.LITELLM_BASE_URL_VAR)
    master_key = os.environ.get(ex.LITELLM_MASTER_KEY_VAR)
    if not base_url or not master_key:
        return None, "LiteLLM is not configured for this deployment"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/model/info",
                headers={"Authorization": f"Bearer {master_key}"},
            )
    except Exception as exc:  # noqa: BLE001
        return None, f"LiteLLM /model/info unreachable: {type(exc).__name__}"
    if resp.status_code != 200:
        return None, f"LiteLLM /model/info returned HTTP {resp.status_code}"
    for entry in resp.json().get("data", []):
        if entry.get("model_name") == model_name:
            price = (entry.get("model_info") or {}).get("input_cost_per_token")
            if price is None:
                return None, f"'{model_name}' has no input_cost_per_token on LiteLLM"
            return Decimal(str(price)) * Decimal(1_000_000), "live from LiteLLM GET /model/info"
    return None, f"'{model_name}' is not a registered deployment on the LiteLLM proxy"


async def reindex_estimate(conn, org_id, new_model: str) -> dict:
    """Real, live numbers for the embedding-model-change confirmation dialog.

    *** THE EMBEDDING COMPATIBILITY RULE *** embeddings from different models
    are not comparable — changing the model without re-indexing silently
    degrades search with no error. This function supplies the REAL numbers an
    admin needs to make that call: the org's actual current corpus size
    (COUNT(*) against document_embeddings, not an estimate), and a real cost
    estimate computed from the corpus's actual stored content_chars and the
    candidate model's live LiteLLM price. It does NOT block the change — the
    caller (the settings write path) still accepts it on confirmation; this is
    friction, not a lock.

    No re-indexing job exists anywhere in this codebase today (Task 1d) — the
    returned ``note`` says so plainly rather than implying one will run.
    """
    row = await conn.fetchrow(
        "SELECT count(*) AS n, coalesce(sum(content_chars), 0) AS chars, "
        "min(model) AS current_model "
        "FROM document_embeddings WHERE org_id = $1",
        org_id,
    )
    corpus_count = row["n"]
    total_chars = row["chars"]
    current_model = row["current_model"]
    est_tokens = total_chars // _CHARS_PER_TOKEN_ESTIMATE

    price_per_million, price_source = await _live_price_per_million(new_model)
    est_cost = None
    if price_per_million is not None:
        est_cost = (Decimal(est_tokens) / Decimal(1_000_000)) * price_per_million

    return {
        "org_id": str(org_id),
        "new_model": new_model,
        "current_model": current_model,
        "corpus_document_count": corpus_count,
        "corpus_total_content_chars": total_chars,
        "estimated_tokens": est_tokens,
        "price_per_million_tokens_usd": (
            str(price_per_million) if price_per_million is not None else None
        ),
        "price_source": price_source,
        "estimated_reindex_cost_usd": (
            str(est_cost.quantize(Decimal("0.000001"))) if est_cost is not None else None
        ),
        "reindex_mechanism_exists": False,
        "note": (
            "No automated re-indexing job exists in this platform yet. "
            "Confirming this change updates the setting only — the "
            f"{corpus_count} document(s) already embedded above keep their "
            "OLD-model vectors and will NOT be automatically re-embedded. "
            "Search results comparing old- and new-model vectors will "
            "silently degrade until every one of those documents is "
            "manually re-embedded."
        ),
    }


# ── RETRIEVE: visibility-scoped semantic search ───────────────────────────────
async def _visible_entity_ids(pool, org_id, user_id, is_staff) -> set:
    """Allowed entity-id set for the caller, reusing the SAME engines as the rest
    of the app (ownership tree, etc.) — never a bespoke visibility path.

    staff/admin → staff visibility engine (assignment + team + hierarchy; super
    admins get all org entities inside it). member → the member visibility engine
    (resolve_entity_set over the member's own delegate grants). BOTH are then
    wrapped by the restricted-access filter. Imported locally to keep this
    module importable without pulling the whole visibility stack at load time.
    """
    from services.delegate_grants import get_delegate_visible_entity_ids
    from services.restricted_access import filter_restricted
    from services.staff_visibility import get_staff_visible_entity_ids

    if is_staff:
        allowed = await get_staff_visible_entity_ids(pool, user_id, org_id)
    else:
        allowed = await get_delegate_visible_entity_ids(pool, org_id, user_id)
    allowed = await filter_restricted(pool, allowed, user_id, org_id)
    return {str(x) for x in allowed}


async def semantic_search(
    pool, org_id, user_id, query, *, is_staff=True, limit=20
) -> list[dict]:
    """Real semantic search: embed the query, rank org documents by pgvector
    cosine similarity, then enforce the SAME visibility engines as everything
    else before returning citations back to the source documents.

    Cross-org isolation is enforced twice: the SQL is ``WHERE e.org_id = $1`` and
    the document_embeddings RLS policy is org-scoped. Restricted-access and
    per-user entity visibility are enforced by ``_visible_entity_ids`` above.
    """
    limit = max(1, min(int(limit or 20), 100))
    qvec = await embed_query(pool, org_id, query)
    qlit = to_pgvector(qvec)

    tokens = set_rls_context(org_id, False)
    try:
        # Over-fetch (limit * 5, capped) so post-visibility filtering still has
        # enough candidates to return `limit` allowed hits.
        fetch_n = min(limit * 5, 500)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.document_id,
                       d.original_filename,
                       d.doc_family,
                       d.entity_id,
                       e.content_source,
                       (e.embedding <=> $2::vector) AS distance
                FROM document_embeddings e
                JOIN documents d ON d.id = e.document_id
                WHERE e.org_id = $1
                ORDER BY e.embedding <=> $2::vector
                LIMIT $3
                """,
                org_id, qlit, fetch_n,
            )
    finally:
        reset_rls_context(tokens)

    allowed = await _visible_entity_ids(pool, org_id, user_id, is_staff)

    results: list[dict] = []
    for r in rows:
        entity_id = r["entity_id"]
        if entity_id is None:
            # Org-general document (not tied to an entity): visible to staff of
            # the org; members only see documents on their own entities.
            visible = is_staff
        else:
            visible = str(entity_id) in allowed
        if not visible:
            continue
        distance = float(r["distance"])
        results.append({
            "document_id": str(r["document_id"]),
            "original_filename": r["original_filename"],
            "doc_family": r["doc_family"],
            "entity_id": str(entity_id) if entity_id else None,
            "content_source": r["content_source"],
            "distance": distance,
            "similarity": round(1.0 - distance, 6),
        })
        if len(results) >= limit:
            break
    return results
