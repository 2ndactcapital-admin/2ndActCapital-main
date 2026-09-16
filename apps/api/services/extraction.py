"""AI extraction service (Sprint 10 — Client Intelligence Layer).

Wraps the Anthropic API to pull structured fields out of conversational
Foundation answers and free-text CRM notes. All calls use the Haiku model and
return parsed JSON; the model is asked to return JSON only and we strip any
accidental markdown fences before parsing.

Inserts/updates use the shared pool (statement_cache_size=0 — PgBouncer safe).

Sprint 27 (TaskRouter) — this module is the single choke point for every AI
call in the platform (assistant, dashboard, investment_profile, marketplace,
extraction, crm, document_classifier all route through the three call_claude_*
helpers below). It now owns:

  * a real per-org ORDERED fallback CHAIN (resolve_fallback_chain): try the
    primary model, on failure walk to the next model, until one succeeds or the
    chain is exhausted (then raise clearly — never silently return nothing);
  * a decision log (ai_decision_log) written for every call — model requested
    vs used, whether a fallback fired and why, cost, latency, success/error.
    Logging is NON-BLOCKING: a log-write failure can never break the AI call.

LiteLLM Phase B (litellmphaseb.structural) — the actual HTTP call now goes to
the self-hosted LiteLLM proxy instead of straight to Anthropic. This is a
transport swap at ONE point (``_build_ai_client``): the chain walk, cost model,
ai_decision_log shape and error handling are unchanged, and ai_decision_log
gained no columns. Two logs now describe every call, on purpose (design §4):
ai_decision_log says what TaskRouter DECIDED, LiteLLM's own LiteLLM_SpendLogs
says what actually EXECUTED and cost what.

``LITELLM_ROUTING_DISABLED=1`` is the ops rollback switch — see
LITELLM_DISABLE_VAR below.

LiteLLM Phase D1b (litellmphased1b.structural) — ROUTING + ATTRIBUTION. Every
call from this module is Anthropic (``_CALL_PROVIDER``). When the transport is
LiteLLM, ``_execute_chain`` now: (1) resolves this org's
``ai.credential_source.anthropic`` (services.litellm_credentials
.resolve_credential_source); (2) translates the model_id actually SENT to
LiteLLM via ``resolve_deployment_model`` — 'claude-sonnet' routes to the org's
own deployment when that org is 'org'-sourced, to the shared platform
deployment otherwise, and every other model_id is untouched, so the
caller-facing logical model name (what ai_decision_log records as
model_requested/model_used) never changes; (3) attaches real attribution
metadata (services.litellm_credentials.build_attribution) to the outgoing
request so LiteLLM's own spend log can answer "which org, against whose key" —
never a new ai_decision_log column. None of this touches the rollback
(direct-Anthropic) path: routing/attribution are LiteLLM-only concepts.

LiteLLM Phase D1c (litellmphased1c.structural) — CREDENTIAL-FAILURE
ALERTING. Scoped narrowly to what happens when an ORG'S OWN credential (as
opposed to LITELLM_MASTER_KEY, or a model simply being unavailable) is bad.
``_execute_chain`` now tells the two auth-failure causes apart by which
deployment the failed attempt actually used (positively, from routing state
already computed — see AIOrgCredentialError's docstring), never by parsing
LiteLLM's error text: an org-credential failure raises AIOrgCredentialError
(naming the provider, never converted to None) and STOPS the chain walk dead
— it never proceeds to a platform deployment, because that would silently
turn the org's own config problem into Hollisworks' bill. It also fires a
member_todos alert to the org's manage_org_settings holders (services.
workflow_todos.create_credential_failure_alerts — the same reused mechanism
create_held_run_alerts already established, never a second notification
path). A genuine model-unavailable failure (no auth error at all — LiteLLM
returns 400, not 401/403) is unaffected and keeps walking the fallback chain
exactly as it always has.

LiteLLM Phase D2 (litellmphased2.structural) — MODEL PICK-LIST ENFORCEMENT.
services.model_catalog owns two new tables: platform_model_catalog
(Hollisworks-curated, platform-wide) and org_model_selections (which curated
models one org has authorised). ``_execute_chain`` now filters the resolved
``attempts`` list down to the org's authorised set BEFORE any provider call —
a model the org has not authorised is dropped from the chain entirely, never
merely hidden by a UI. An org with NO explicit selection resolves to
unrestricted (``resolve_authorized_models`` returns ``None``), which is
byte-for-byte the pre-D2 behaviour — every org today is in that state. If
filtering empties the chain, ``AIModelNotAuthorizedError`` is raised before
any attempt (a policy refusal, not a provider failure) — logged to
ai_decision_log like every other terminal outcome, never silently swallowed.
"""

import json
import os
import time
from decimal import Decimal

# ---------------------------------------------------------------------------
# Model resolution (mini-bedrock sprint)
# ---------------------------------------------------------------------------
# Which model each call path uses is a CONFIG value, resolved per-org from
# org_settings (category 'ai'), NOT a hardcoded string. The only place a model
# string literally lives is DEFAULT_SETTINGS in services/org_settings.py (the
# fallback for orgs without an explicit override) and the seed/migration.
# Switching a client — or the whole platform (e.g. a future AWS Bedrock move) —
# to a different model becomes a settings change, not a code change.
DEFAULT_MODEL_KEY = "ai.model.default"      # primary: extraction, briefs, summaries
ASSISTANT_MODEL_KEY = "ai.model.assistant"  # tool-using assistant / narration
# Task-specific override for the open-set document-type classifier (Sprint 25).
# Same convention as ASSISTANT_MODEL_KEY: a dedicated ai.model.* key the
# classifier resolves FIRST, falling back to ai.model.default (Haiku) when the
# org has not set it. Lets an org_admin pick a stronger model for their own
# classifier while every org gets Haiku by default.
DOCUMENT_CLASSIFIER_MODEL_KEY = "ai.model.document_classifier"

# Sprint 27 — the ordered fallback chain key. A JSON array of model strings.
FALLBACK_CHAIN_KEY = "ai.model.fallback_chain"

# ---------------------------------------------------------------------------
# LiteLLM Phase E — per-task model assignment + effort
# ---------------------------------------------------------------------------
# Task 1a/1b's discovery finding, made structural: the platform has ~18 real
# task_type strings (grepped live — every distinct value passed as task_type=
# to call_claude_json/call_claude_text/call_claude_with_tools) but only THREE
# assignable dials, because that is the real granularity resolve_model has
# ever supported. A task with no dedicated key shares whichever dial its
# call site's model_key defaults to (DEFAULT_MODEL_KEY for call_claude_json/
# call_claude_text, ASSISTANT_MODEL_KEY for call_claude_with_tools).
#
# MODEL_TASK_REGISTRY is the ONE list an org-admin-facing "assign a model per
# task" screen reads — services/org_settings.py's read endpoint and the
# frontend both iterate this, never a second, hand-maintained copy of "which
# dials exist." A genuinely NEW dial (a fourth ai.model.* key) still needs a
# code change here (constant + entry below) and at whichever call site(s)
# pass its model_key — that part is NOT automatic and this sprint does not
# pretend otherwise (Task 1b). What IS automatic: once a key is in this list,
# the settings API, the settings UI, and permission/validation all pick it up
# with zero further edits — one registration point, not four.
#
# task_types listed per entry are DOCUMENTATION (what Task 1a found, so the
# UI can show an admin "this covers: extraction, briefs, summaries..." next
# to the dial) — resolution itself is driven entirely by which model_key a
# call site passes, never by this list.
EFFORT_KEY_BY_MODEL_KEY: dict[str, str] = {
    DEFAULT_MODEL_KEY: "ai.effort.default",
    ASSISTANT_MODEL_KEY: "ai.effort.assistant",
    DOCUMENT_CLASSIFIER_MODEL_KEY: "ai.effort.document_classifier",
}

MODEL_TASK_REGISTRY: list[dict] = [
    {
        "key": DEFAULT_MODEL_KEY,
        "effort_key": EFFORT_KEY_BY_MODEL_KEY[DEFAULT_MODEL_KEY],
        "label": "Extraction, Briefs & Summaries",
        "description": (
            "Every AI task with no dedicated dial of its own — Foundation "
            "answer extraction, CRM note extraction, client/deal briefs, "
            "fee narratives, note-terms extraction, VDR analysis, workflow "
            "narrative generation, and more."
        ),
        "task_types": [
            "extraction", "profile_extraction", "crm_extraction",
            "foundation_reply", "client_brief", "brief_themes",
            "deal_summary", "fee_narrative_polish", "narrative_extraction",
            "fee_schedule_spec", "note_terms_extraction",
            "note_terms_underlyings", "vdr_analysis", "workflow_generation",
            "crm_draft_note", "text_generation",
        ],
    },
    {
        "key": ASSISTANT_MODEL_KEY,
        "effort_key": EFFORT_KEY_BY_MODEL_KEY[ASSISTANT_MODEL_KEY],
        "label": "Assistant",
        "description": (
            "The tool-using member assistant, the dashboard member-brief "
            "narration, and the note-terms hazard-ensemble cross-check."
        ),
        "task_types": ["assistant", "member_brief", "note_terms_hazard_ensemble"],
    },
    {
        "key": DOCUMENT_CLASSIFIER_MODEL_KEY,
        "effort_key": EFFORT_KEY_BY_MODEL_KEY[DOCUMENT_CLASSIFIER_MODEL_KEY],
        "label": "Document Classifier",
        "description": "Open-set document-type classification on upload.",
        "task_types": ["document_classifier"],
    },
]

# Effort VALUES are not metadata LiteLLM reports (design doc §4) — it names
# the parameter (`thinking`/`reasoning_effort` in supported_openai_params),
# not its legal range. This is the small, local mapping that supplies it.
# Values are Anthropic `thinking.budget_tokens` (Task 1c, probed live: the
# Anthropic-shaped call this module already makes accepts a native
# `thinking={"type": "enabled", "budget_tokens": N}` kwarg, confirmed by a
# real call returning a genuine `thinking` content block plus
# `usage.output_tokens_details.thinking_tokens` — never OpenAI's
# `reasoning_effort` string enum, which is a DIFFERENT provider convention
# that only applies over LiteLLM's OpenAI-shaped route, not the
# Anthropic-shaped `/v1/messages` route this module calls). 1024 is
# Anthropic's own minimum budget_tokens.
EFFORT_LEVELS: dict[str, int] = {
    "low": 1024,
    "medium": 4096,
    "high": 12000,
}
# max_tokens must exceed thinking.budget_tokens for Anthropic to accept the
# request; this is how much room _apply_effort leaves for the actual answer
# on top of the thinking budget when a caller's own max_tokens is too small.
_EFFORT_MAX_TOKENS_HEADROOM = 512

# org_id is NOT NULL on ai_decision_log. Platform calls made with no org context
# (resolve_model(None)) are attributed to the default org for logging purposes.
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

# LiteLLM Phase D1b — every call this module makes is Anthropic. Used to
# resolve this org's ai.credential_source.anthropic and to build attribution
# metadata; see services.litellm_credentials.
_CALL_PROVIDER = "anthropic"


class AIChainExhausted(Exception):
    """Every model in the org's fallback chain failed for a call.

    Raised by ``_execute_chain`` so exhaustion is a clear, loud signal rather
    than a silent ``None``. The public call_claude_* wrappers catch it and
    return None to preserve their long-standing graceful-degradation contract
    (many call sites already branch on a None result) — but by that point the
    failure has been printed AND written to ai_decision_log (success=false).
    """


class AILiteLLMAuthError(RuntimeError):
    """LiteLLM rejected ``LITELLM_MASTER_KEY``.

    DELIBERATELY NOT a subclass of AIChainExhausted, and deliberately NOT
    converted to ``None`` by the public call_claude_* wrappers: a bad master key
    is an operator misconfiguration affecting every AI call in the platform at
    once, and every model in the chain shares that one key, so walking the chain
    cannot rescue it. Degrading it to the ordinary "returned None" path would
    make a total AI outage look exactly like a single unparseable response.
    """


class AIModelNotAuthorizedError(RuntimeError):
    """None of the models this call would have tried are on the org's
    authorised list (LiteLLM Phase D2 — services.model_catalog).

    Raised BEFORE any provider call is attempted — this is a policy refusal,
    not a provider failure, so it must never be confused with
    ``AIChainExhausted`` (every model failing AT the provider) or either auth
    error above. An org with NO explicit selection is unrestricted (Phase D2's
    own no-regression requirement), so this can only fire for an org that has
    authorised at least one model, none of which the resolved chain named.
    """


class AIOrgCredentialError(RuntimeError):
    """This ORG'S OWN provider credential was rejected by the provider.

    LiteLLM Phase D1c. DELIBERATELY a sibling of ``AILiteLLMAuthError``, not a
    subclass of it (and not of ``AIChainExhausted`` either, for the same
    reason ``AILiteLLMAuthError`` isn't): the two auth failures share an HTTP
    401/403 symptom but are structurally different events. A bad
    ``LITELLM_MASTER_KEY`` is a platform-wide outage that no fallback can
    rescue. A bad ORG credential is that one org's own config problem — every
    OTHER org, and the platform key itself, are fine. Conflating the two
    would either bury an org-specific problem as a platform outage, or (worse)
    let the chain walk onto a platform deployment to "fix" what is actually a
    config problem the org needs to resolve — silently turning the org's
    broken credential into Hollisworks' bill. So this is raised instead,
    never converted to ``None``, and never followed by a further attempt in
    the chain. See ``_execute_chain`` for how it is told apart from
    ``AILiteLLMAuthError`` (by which deployment the failed attempt actually
    used, not by guessing at LiteLLM's error text).
    """


# ---------------------------------------------------------------------------
# Transport selection (Phase B — LiteLLM routing)
# ---------------------------------------------------------------------------
# Phase B routes this module's calls through the self-hosted LiteLLM proxy
# instead of straight at Anthropic. This is a TRANSPORT swap only: the fallback
# chain, retry walk, cost model, ai_decision_log shape and error handling below
# are untouched.
#
# HOW: the Anthropic SDK is pointed at LiteLLM's base URL rather than replaced.
# LiteLLM serves a real Anthropic-shaped ``POST /v1/messages`` route (confirmed
# live against hollisworks-litellm.onrender.com, which parsed and model-validated
# an Anthropic-format body). Keeping the SDK means the response objects handed to
# every ``extract()`` closure and to ``_compute_cost`` stay genuine Anthropic
# types — so ``message.content[0].text``, ``message.stop_reason``,
# ``block.model_dump()`` and ``usage.input_tokens`` all keep working unchanged.
# The alternative (raw httpx against /v1/chat/completions, which is also live)
# would require hand-writing an OpenAI->Anthropic response adapter, including
# tool_calls -> tool_use blocks, on the single most load-bearing path in the
# platform. That is a rewrite, not a transport swap.
LITELLM_BASE_URL_VAR = "LITELLM_BASE_URL"
LITELLM_MASTER_KEY_VAR = "LITELLM_MASTER_KEY"

# TASK 4 — the ops-level rollback switch. Set this to any of _TRUTHY and every
# call in this module goes straight back to Anthropic, never contacting LiteLLM.
#
# An ENVIRONMENT VARIABLE, not an org_settings key, and that is a deliberate
# choice grounded in what this codebase already does: external-service wiring
# follows the plain-env-var convention (services/portfolio_altruist.py, and
# LITELLM_ENV_VARS in services/assistant_actions/litellm_ops.py), while
# org_settings holds per-org POLICY. This switch is neither per-org nor policy —
# it is a blunt, platform-wide deployment escape hatch that must keep working
# when the database itself is the thing that is unhappy. An org_settings key
# would need a working DB read to tell us the DB-independent fallback is on.
#
# This is NOT design-doc §7.5's ``force_anthropic`` (a future, per-org,
# UI-driven, Hollis-admin-facing capability). Different audience, different
# lifetime, different mechanism.
LITELLM_DISABLE_VAR = "LITELLM_ROUTING_DISABLED"

TRANSPORT_LITELLM = "litellm"
TRANSPORT_ANTHROPIC = "anthropic"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def litellm_routing_disabled() -> bool:
    """True when the Task-4 ops rollback switch is engaged."""
    return (os.environ.get(LITELLM_DISABLE_VAR) or "").strip().lower() in _TRUTHY


def resolve_transport() -> tuple[str, str]:
    """Pick the transport for this call. Returns ``(transport, reason)``.

    ``reason`` is empty on the intended Phase-B path (LiteLLM, configured) and
    is a human-readable explanation on every other path, so a deployment that is
    NOT routing through LiteLLM says so out loud on every call rather than
    looking identical to one that is.

    The two non-LiteLLM outcomes are kept distinct on purpose, mirroring
    litellm_ops.py's own LiteLLMConfigError-vs-LiteLLMReloadError split:

      * rollback engaged      — an operator asked for direct Anthropic;
      * LiteLLM not configured — no base URL / master key exists to call.

    Note what is deliberately absent: there is no "LiteLLM is configured but
    the call failed -> quietly retry against Anthropic" path. A configured-but-
    broken proxy fails loudly through the normal chain machinery. Silently
    healing that would mean the platform could stop routing through LiteLLM
    entirely and nobody would ever find out.
    """
    if litellm_routing_disabled():
        return TRANSPORT_ANTHROPIC, (
            f"{LITELLM_DISABLE_VAR} is set — ops rollback engaged, calling "
            f"Anthropic directly and never contacting LiteLLM"
        )
    missing = [v for v in (LITELLM_BASE_URL_VAR, LITELLM_MASTER_KEY_VAR)
               if not os.environ.get(v)]
    if missing:
        return TRANSPORT_ANTHROPIC, (
            f"LiteLLM is not configured for this deployment (missing "
            f"{', '.join(missing)}) — falling back to direct Anthropic. This is "
            f"a DEGRADED state, not the intended Phase-B path: set "
            f"{' and '.join((LITELLM_BASE_URL_VAR, LITELLM_MASTER_KEY_VAR))} to "
            f"route through the proxy."
        )
    return TRANSPORT_LITELLM, ""


def _build_ai_client() -> tuple[object | None, str, str, str]:
    """``(client, transport, reason, endpoint)``; client is None with no credential.

    A ``None`` client preserves this module's long-standing no-API-key contract:
    ``_execute_chain`` returns None and callers that already branch on a None
    result behave exactly as they did before Phase B.
    """
    transport, reason = resolve_transport()

    import anthropic as _anthropic

    if transport == TRANSPORT_LITELLM:
        base_url = os.environ[LITELLM_BASE_URL_VAR].rstrip("/")
        master_key = os.environ[LITELLM_MASTER_KEY_VAR]
        client = _anthropic.AsyncAnthropic(
            api_key=master_key,
            base_url=base_url,
            # The SDK authenticates with `x-api-key`; LiteLLM accepts that on its
            # Anthropic route but treats `Authorization: Bearer` as its primary
            # scheme. Sending both means auth does not hinge on which one this
            # proxy build happens to prefer.
            default_headers={"Authorization": f"Bearer {master_key}"},
        )
        return client, transport, reason, f"{base_url}/v1/messages"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, transport, reason, "https://api.anthropic.com/v1/messages"
    return (_anthropic.AsyncAnthropic(api_key=api_key), transport, reason,
            "https://api.anthropic.com/v1/messages")


def _is_auth_failure(exc: Exception) -> bool:
    """Did this exception come back as an HTTP 401/403?

    Reads ``status_code`` off the exception rather than importing
    ``anthropic.AuthenticationError``, so it stays correct if the SDK reshuffles
    its exception hierarchy.
    """
    return getattr(exc, "status_code", None) in (401, 403)


async def resolve_model(org_id=None, *, key: str = DEFAULT_MODEL_KEY) -> str:
    """Resolve which model to call for ``org_id`` from org_settings.

    Falls back to DEFAULT_SETTINGS (the platform default) when the org has no
    explicit override, when no org context is supplied, or on any lookup error —
    so existing behaviour is identical while the model becomes configurable.
    """
    # Local imports avoid any import cycle at module load.
    from services.org_settings import DEFAULT_SETTINGS, get_setting

    default = DEFAULT_SETTINGS.get(key)
    if org_id is None:
        return default
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            value = await get_setting(conn, org_id, key)
        return value or default
    except Exception as exc:
        print(f"resolve_model failed for {key}, using default: {exc}")
        return default


async def resolve_fallback_chain(
    org_id=None, *, primary_key: str = DEFAULT_MODEL_KEY
) -> list[str]:
    """The org's ordered fallback chain (models to try, primary-first-ish).

    Reads ``ai.model.fallback_chain`` from org_settings for ``org_id``, falling
    back to DEFAULT_SETTINGS when the org has no explicit chain, when no org
    context is supplied, or on any lookup error. Always returns a list of
    non-empty model strings (possibly empty). ``primary_key`` is accepted for
    forward-compatibility (per-primary chains) but a single global chain is used
    today — the same chain backs default/assistant/classifier calls.
    """
    from services.org_settings import DEFAULT_SETTINGS, get_setting

    default = DEFAULT_SETTINGS.get(FALLBACK_CHAIN_KEY) or []
    chain = default
    if org_id is not None:
        try:
            from services.database import get_pool

            pool = await get_pool()
            async with pool.acquire() as conn:
                value = await get_setting(conn, org_id, FALLBACK_CHAIN_KEY)
            chain = value if value else default
        except Exception as exc:
            print(f"resolve_fallback_chain failed, using default: {exc}")
            chain = default

    if isinstance(chain, str):  # tolerate a mis-stored scalar
        chain = [chain]
    return [m for m in (chain or []) if isinstance(m, str) and m]


async def resolve_effort(org_id, model_key: str) -> str | None:
    """This org's effort level for the task dial ``model_key`` resolves
    through, or ``None`` if unset/unknown — mirrors resolve_model's own
    fail-to-default (here, fail-to-None, i.e. "no effort control engaged")
    discipline. ``None`` is a real, common state: it means neither Hollisworks
    nor the org has ever set this task's effort, and no `thinking` parameter
    is sent — byte-for-byte pre-Phase-E behaviour.
    """
    effort_key = EFFORT_KEY_BY_MODEL_KEY.get(model_key)
    if effort_key is None:
        return None
    from services.org_settings import DEFAULT_SETTINGS, get_setting

    default = DEFAULT_SETTINGS.get(effort_key)
    value = default
    if org_id is not None:
        try:
            from services.database import get_pool

            pool = await get_pool()
            async with pool.acquire() as conn:
                value = await get_setting(conn, org_id, effort_key)
        except Exception as exc:
            print(f"resolve_effort failed for {effort_key}, using default: {exc}")
            value = default
    return value if value in EFFORT_LEVELS else None


def _dedupe(items) -> list[str]:
    """Order-preserving de-dup — a model already tried is not retried."""
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# Cost model (Sprint 27)
# ---------------------------------------------------------------------------
# USD per 1M tokens, (input, output), by model family prefix. Decimal per the
# standing rule for cost figures. Anthropic model strings are dated/versioned
# (claude-haiku-4-5-20251001) so the family prefix is a stable pricing key.
_MODEL_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus": (Decimal("15"), Decimal("75")),
    "claude-sonnet": (Decimal("3"), Decimal("15")),
    "claude-haiku": (Decimal("1"), Decimal("5")),
    "claude-fable": (Decimal("1"), Decimal("5")),
}
_DEFAULT_PRICING = (Decimal("1"), Decimal("5"))
_MILLION = Decimal(1_000_000)


def _price_for(model_id: str) -> tuple[Decimal, Decimal]:
    for prefix, price in _MODEL_PRICING.items():
        if model_id and model_id.startswith(prefix):
            return price
    return _DEFAULT_PRICING


def _compute_cost(model_id: str, usage) -> Decimal | None:
    """Dollar cost of one response from its token usage. None if no usage."""
    if usage is None:
        return None
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    in_price, out_price = _price_for(model_id)
    cost = (Decimal(in_tok) / _MILLION) * in_price + (
        Decimal(out_tok) / _MILLION
    ) * out_price
    return cost.quantize(Decimal("0.00000001"))


# ---------------------------------------------------------------------------
# Decision log (Sprint 27) — NON-BLOCKING
# ---------------------------------------------------------------------------
async def _write_ai_decision(
    *, org_id, task_type, model_requested, model_used, fallback_used,
    fallback_reason, cost_usd, latency_ms, success, error_detail,
    effort_requested=None, effort_used=None,
) -> None:
    """Insert one ai_decision_log row. May raise — always call via _safe_log.

    ``effort_requested``/``effort_used`` default to None so every pre-Phase-E
    caller (document_embedding.py's own chain executor reuses this via
    ``ex._safe_log`` and never carries effort — embeddings are always
    ``supports_reasoning: false``) keeps working unchanged. The Task 3
    fallback decision (drop effort silently, log it) is what makes the two
    genuinely differ on a chat-task row: ``effort_requested`` is set whenever
    the org asked for one, regardless of outcome; ``effort_used`` is set only
    when it was actually sent to the provider on THIS attempt. A row with
    ``effort_requested`` set and ``effort_used`` NULL is exactly the dropped
    case, queryable directly.
    """
    from services.database import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ai_decision_log
                (org_id, task_type, model_requested, model_used, fallback_used,
                 fallback_reason, cost_usd, latency_ms, success, error_detail,
                 effort_requested, effort_used)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            org_id or DEFAULT_ORG_ID, task_type, model_requested, model_used,
            fallback_used, fallback_reason, cost_usd, latency_ms, success,
            error_detail, effort_requested, effort_used,
        )


async def _safe_log(**fields) -> None:
    """Write a decision-log row, swallowing ANY error.

    This is the guarantee that logging is non-blocking: the AI call's real
    result is returned to the caller regardless of whether this row lands. We
    await (not fire-and-forget) so the log is durably written before the call
    returns — deterministic for readers/verification — but wrap the whole write
    so a broken log path degrades to a printed warning, never a raised error.
    """
    try:
        await _write_ai_decision(**fields)
    except Exception as exc:
        print(f"[ai_router] decision log write failed (non-blocking): {exc}")


async def _alert_org_credential_failure(org_id, provider: str, detail: str) -> None:
    """Best-effort: tell the org's manage_org_settings holders their own
    provider credential is broken (LiteLLM Phase D1c). Reuses the existing
    member_todos path (services.workflow_todos) — never a second
    notification mechanism.

    Non-blocking by the same discipline as ``_safe_log``: this function is
    called from inside the exception handler that is about to raise
    ``AIOrgCredentialError`` regardless, so a failure writing the alert must
    never replace or mask that already-loud failure with a DIFFERENT,
    unrelated exception.
    """
    try:
        from services.database import get_pool
        from services.workflow_todos import create_credential_failure_alerts

        pool = await get_pool()
        async with pool.acquire() as conn:
            await create_credential_failure_alerts(
                conn, org_id=org_id, provider=provider, error_detail=detail,
            )
    except Exception as exc:
        print(f"[ai_router] credential-failure alert write failed (non-blocking): {exc}")


# ---------------------------------------------------------------------------
# The chain executor (Sprint 27) — every call_claude_* helper routes through it
# ---------------------------------------------------------------------------
async def _execute_chain(
    *, task_type: str, org_id, model_key: str, model_override, make_call, extract,
):
    """Walk the org's model chain: time, cost, and log every call.

    ``make_call(client, model_id, attribution, effort_budget)`` performs the
    actual Anthropic request and returns the raw message (``attribution`` is
    the Phase-D1b metadata dict, or ``None`` on the direct-Anthropic
    transport, where routing/attribution do not apply; ``effort_budget`` is a
    Phase-E ``thinking.budget_tokens`` int, or ``None`` when no effort
    applies to this attempt); ``extract(message)`` shapes the public return
    value. Tries the primary (``model_override`` or resolve_model(org_id,
    model_key)) first, then each model in the org's fallback chain, until one
    responds. Writes exactly one ai_decision_log row per call (the outcome).
    Returns ``extract(message)`` on success; returns None when no API key is
    configured (preserving the pre-existing no-key contract); raises
    AIChainExhausted when every model in the chain fails.
    """
    # Phase B: the ONLY change here is where `client` points. Everything below
    # this block — the chain walk, timing, cost, logging, exhaustion — is the
    # pre-Phase-B code, unmodified.
    client, transport, transport_reason, endpoint = _build_ai_client()
    if client is None:
        print(
            f"[ai_router] no usable credential for transport '{transport}' "
            f"(task '{task_type}'): {transport_reason}"
        )
        return None
    if transport_reason:
        print(f"[ai_router] transport={transport}: {transport_reason}")

    primary = model_override or await resolve_model(org_id, key=model_key)
    chain = await resolve_fallback_chain(org_id, primary_key=model_key)
    attempts = _dedupe([primary, *chain])

    # LiteLLM Phase D2 — the org's authorised model list (services.model_catalog).
    # None means "no explicit selection" -> unrestricted, byte-for-byte the
    # pre-D2 behaviour (every existing org's real state today). A non-None set
    # is a genuine allow-list: any attempt not in it is dropped BEFORE the
    # provider is ever called, never merely hidden in the UI.
    from services.model_catalog import resolve_authorized_models

    authorized = await resolve_authorized_models(org_id)
    if authorized is not None:
        attempts = [m for m in attempts if m in authorized]
        if not attempts:
            detail = (
                f"None of the models task '{task_type}' would try "
                f"({_dedupe([primary, *chain])}) are on org {org_id}'s "
                f"authorised model list ({sorted(authorized)}). Add one of "
                f"them in the org's AI model settings, or authorise the "
                f"model this task actually resolves to."
            )
            print(f"[ai_router] {detail}")
            await _safe_log(
                org_id=org_id, task_type=task_type, model_requested=primary,
                model_used=primary, fallback_used=False, fallback_reason=None,
                cost_usd=None, latency_ms=1, success=False, error_detail=detail,
                effort_requested=await resolve_effort(org_id, model_key),
            )
            raise AIModelNotAuthorizedError(detail)

    # LiteLLM Phase E — per-task effort. resolve_effort returns None for the
    # vast majority of tasks (no dedicated effort_key, or one that exists but
    # has never been set), and when it does this block costs nothing further
    # below: reasoning_map stays empty and every attempt's effort_budget is
    # None — byte-for-byte pre-Phase-E behaviour. Only a genuinely SET effort
    # pays for the one extra live /model_group/info call, and only once per
    # chain walk (not once per attempt).
    selected_effort = await resolve_effort(org_id, model_key)
    reasoning_map: dict[str, bool] = {}
    if selected_effort is not None:
        from services.litellm_credentials import reasoning_support_by_model

        reasoning_map = reasoning_support_by_model()

    # LiteLLM Phase D1b — routing + attribution. Both are LiteLLM-only
    # concepts: the rollback (direct-Anthropic) path never touches either, so
    # a platform-key call's behaviour is byte-for-byte what it was before
    # this sprint. "platform" is litellm_credentials.CREDENTIAL_SOURCE_PLATFORM's
    # literal value — the value every org resolves to when unconfigured.
    credential_source = "platform"
    attribution = None
    if transport == TRANSPORT_LITELLM:
        from services.litellm_credentials import (
            CREDENTIAL_SOURCE_ORG,
            build_attribution,
            resolve_credential_source,
        )

        credential_source = await resolve_credential_source(org_id, _CALL_PROVIDER)
        attribution = build_attribution(
            org_id or DEFAULT_ORG_ID, _CALL_PROVIDER, credential_source
        )

    t0 = time.monotonic()
    last_error = None
    for model_id in attempts:
        call_model_id = model_id
        if transport == TRANSPORT_LITELLM:
            from services.litellm_credentials import resolve_deployment_model

            call_model_id = resolve_deployment_model(
                model_id, org_id or DEFAULT_ORG_ID, _CALL_PROVIDER, credential_source
            )

        # Task 3's settled decision: gate per ATTEMPT, on the logical model_id
        # (pre deployment-name translation — an org's own mirrored deployment
        # carries the identical upstream model and so the identical reasoning
        # capability; reasoning_map is keyed by the platform's logical
        # model_group names, not an org's synthetic deployment name). A task
        # whose primary supports reasoning but whose fallback chain lands on
        # one that does not gets effort on the first attempt and silently
        # none on the second — never a raised error either way.
        effort_budget = None
        effort_this_attempt = None
        if selected_effort is not None:
            if reasoning_map.get(model_id):
                effort_budget = EFFORT_LEVELS[selected_effort]
                effort_this_attempt = selected_effort
            else:
                print(
                    f"[ai_router] effort '{selected_effort}' requested for task "
                    f"'{task_type}' but model '{model_id}' does not report "
                    f"supports_reasoning — dropped silently, call proceeds "
                    f"without it (Phase E fallback decision)."
                )

        try:
            message = await make_call(client, call_model_id, attribution, effort_budget)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if transport == TRANSPORT_LITELLM and _is_auth_failure(exc):
                # D1c — a 401/403 here means one of two structurally different
                # things, and they must never be treated alike (see
                # AIOrgCredentialError's docstring). `call_model_id` only
                # differs from the logical `model_id` when
                # resolve_deployment_model just translated THIS attempt onto
                # the org's own dedicated deployment (its own docstring: it
                # translates ONLY for an 'org'-sourced credential, and ONLY
                # for the exact model_id it mirrors) — so if that translation
                # happened and the call then failed auth, the ORG's own
                # credential is what failed, not our shared master key. This
                # is a positive identification from routing state we already
                # computed, not a guess parsed out of LiteLLM's error text.
                if credential_source == CREDENTIAL_SOURCE_ORG and call_model_id != model_id:
                    detail = (
                        f"Org {org_id}'s own {_CALL_PROVIDER} credential was "
                        f"rejected by the provider (HTTP "
                        f"{getattr(exc, 'status_code', '?')}). This is a "
                        f"configuration problem for the org to fix — re-enter "
                        f"or clear the stored {_CALL_PROVIDER} credential in "
                        f"org settings. NEVER falling back to the "
                        f"Hollisworks platform key for this attempt: a "
                        f"broken org credential must not quietly become "
                        f"Hollisworks' bill. Underlying error: {last_error}"
                    )
                    print(f"[ai_router] {detail}")
                    await _safe_log(
                        org_id=org_id, task_type=task_type,
                        model_requested=primary, model_used=model_id,
                        fallback_used=False, fallback_reason=None,
                        cost_usd=None,
                        latency_ms=max(1, int((time.monotonic() - t0) * 1000)),
                        success=False, error_detail=detail,
                        effort_requested=selected_effort,
                    )
                    await _alert_org_credential_failure(org_id, _CALL_PROVIDER, detail)
                    raise AIOrgCredentialError(detail) from exc
                # Every model in the chain authenticates with the SAME master
                # key, so continuing the walk would just replay the identical
                # 401 once per model and then report the generic "all models
                # failed" — burying an operator misconfiguration under a
                # symptom. Stop here and say exactly what is wrong and how to
                # fix it. Logged first, so the failure is on the record even
                # though this path raises instead of returning None.
                detail = (
                    f"LiteLLM rejected {LITELLM_MASTER_KEY_VAR} with HTTP "
                    f"{getattr(exc, 'status_code', '?')} at {endpoint}. No model "
                    f"in the chain was tried beyond the first — they all use "
                    f"this one key. Fix {LITELLM_MASTER_KEY_VAR} in Doppler "
                    f"(and Render), or set {LITELLM_DISABLE_VAR}=1 to roll back "
                    f"to direct Anthropic while you do. Underlying error: "
                    f"{last_error}"
                )
                print(f"[ai_router] {detail}")
                await _safe_log(
                    org_id=org_id, task_type=task_type, model_requested=primary,
                    model_used=model_id, fallback_used=False,
                    fallback_reason=None, cost_usd=None,
                    latency_ms=max(1, int((time.monotonic() - t0) * 1000)),
                    success=False, error_detail=detail,
                    effort_requested=selected_effort,
                )
                raise AILiteLLMAuthError(detail) from exc
            print(
                f"[ai_router] model '{model_id}' failed for task "
                f"'{task_type}' via {transport}: {last_error}"
            )
            continue

        latency_ms = max(1, int((time.monotonic() - t0) * 1000))
        fallback_used = model_id != primary
        reason = (
            f"primary '{primary}' failed: {last_error}" if fallback_used else None
        )
        await _safe_log(
            org_id=org_id, task_type=task_type, model_requested=primary,
            model_used=model_id, fallback_used=fallback_used,
            fallback_reason=reason,
            cost_usd=_compute_cost(model_id, getattr(message, "usage", None)),
            latency_ms=latency_ms, success=True, error_detail=None,
            effort_requested=selected_effort, effort_used=effort_this_attempt,
        )
        return extract(message)

    # Chain exhausted — log the failure clearly, then raise (never silent).
    latency_ms = max(1, int((time.monotonic() - t0) * 1000))
    fallback_used = len(attempts) > 1
    await _safe_log(
        org_id=org_id, task_type=task_type, model_requested=primary,
        model_used=(attempts[-1] if attempts else primary),
        fallback_used=fallback_used,
        fallback_reason=(
            f"all {len(attempts)} model(s) in chain failed"
            if fallback_used else None
        ),
        cost_usd=None, latency_ms=latency_ms, success=False,
        error_detail=last_error, effort_requested=selected_effort,
    )
    raise AIChainExhausted(
        f"All models failed for task '{task_type}' (chain={attempts}): "
        f"{last_error}"
    )


def _apply_attribution(kwargs: dict, attribution: dict | None) -> dict:
    """Merge Phase-D1b attribution into a messages.create() kwargs dict.

    ``metadata.tags`` is a native Anthropic SDK param name (the SDK sends it
    straight through as JSON) that LiteLLM's Anthropic-shaped route reads
    into LiteLLM_SpendLogs.request_tags (probed live — Task 1b). The
    top-level ``user`` field is NOT part of the SDK's typed surface, so it
    goes through ``extra_body``; LiteLLM reads it into
    LiteLLM_SpendLogs.end_user. A no-op when ``attribution`` is None (the
    direct-Anthropic transport, where this concept does not apply).
    """
    if attribution:
        kwargs["metadata"] = {"tags": attribution["tags"]}
        kwargs["extra_body"] = {"user": attribution["end_user"]}
    return kwargs


def _apply_effort(kwargs: dict, effort_budget: int | None) -> dict:
    """Merge Phase-E effort into a messages.create() kwargs dict.

    ``effort_budget`` is a ``thinking.budget_tokens`` int (already resolved
    to a real number by ``_execute_chain`` — only for an attempt whose model
    reports ``supports_reasoning: true``) or ``None``. A no-op when None, so
    a task with no effort assigned, or one whose current attempt does not
    support reasoning, sends byte-for-byte the same request it always has —
    no ``thinking`` key at all, never an empty/disabled one.

    Anthropic requires ``max_tokens`` to exceed ``thinking.budget_tokens``;
    every call site here picks its own ``max_tokens`` for its own reasons
    (a 300-token classification prompt, a 2000-token assistant turn), most
    of which are smaller than a real thinking budget. Rather than reject a
    task's effort setting for being "too small" for its own max_tokens, this
    raises max_tokens just enough to fit the budget plus headroom for the
    actual answer — never lowers a caller's own larger value.
    """
    if effort_budget is None:
        return kwargs
    kwargs["thinking"] = {"type": "enabled", "budget_tokens": effort_budget}
    floor = effort_budget + _EFFORT_MAX_TOKENS_HEADROOM
    if kwargs.get("max_tokens", 0) < floor:
        kwargs["max_tokens"] = floor
    return kwargs


def _response_text(message) -> str:
    """The response's real text block, tolerant of a leading ``thinking``
    block.

    Phase E bug, caught before it shipped: with effort enabled, Anthropic's
    real content order is ``[thinking, text]`` (confirmed live — Task 1c's
    probe), so the pre-Phase-E ``message.content[0].text`` would have raised
    AttributeError on a thinking block (which has ``.thinking``, not
    ``.text``) the moment any task's effort was ever set. Every
    ``call_claude_json``/``call_claude_text`` caller goes through this
    instead of indexing ``content[0]`` directly.
    """
    for block in message.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return message.content[0].text  # no typed block at all — surface the same error as before


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        # Drop the opening fence (``` or ```json) and the trailing fence.
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
        # Remove a leading "json" language tag if it survived.
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    return t.strip()


async def call_claude_json(
    system: str,
    user: str,
    max_tokens: int = 400,
    *,
    org_id=None,
    model: str | None = None,
    model_key: str = DEFAULT_MODEL_KEY,
    task_type: str = "extraction",
) -> dict | None:
    """Call Claude and return parsed JSON, or None if unavailable/unparseable.

    Routes through the Sprint-27 chain executor: per-org fallback chain +
    ai_decision_log. Returns None (preserving the original contract) when there
    is no API key, when the whole model chain is exhausted, or when the response
    is unparseable — every one of those is printed and, for chain outcomes, also
    written to ai_decision_log.
    """
    async def make_call(client, model_id, attribution, effort_budget):
        kwargs = _apply_attribution(dict(
            model=model_id,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ), attribution)
        kwargs = _apply_effort(kwargs, effort_budget)
        return await client.messages.create(**kwargs)

    def extract(message):
        return json.loads(_strip_fences(_response_text(message)))

    try:
        return await _execute_chain(
            task_type=task_type, org_id=org_id, model_key=model_key,
            model_override=model, make_call=make_call, extract=extract,
        )
    except AIChainExhausted as exc:
        print(f"call_claude_json exhausted: {exc}")
        return None
    except (AILiteLLMAuthError, AIOrgCredentialError, AIModelNotAuthorizedError):
        # MUST come before the bare `except Exception` below, which would
        # otherwise flatten a platform-wide auth misconfiguration, an org's
        # own broken credential (D1c), or a Phase-D2 policy refusal into the
        # same silent None as a single malformed JSON response.
        raise
    except Exception as exc:  # unparseable response — preserve None contract
        print(f"call_claude_json failed: {exc}")
        return None


async def call_claude_text(
    system: str,
    messages: list[dict],
    max_tokens: int = 400,
    model: str | None = None,
    *,
    org_id=None,
    model_key: str = DEFAULT_MODEL_KEY,
    task_type: str = "text_generation",
) -> str | None:
    """Call Claude with a message history and return the text response.

    Routes through the Sprint-27 chain executor (per-org fallback chain +
    ai_decision_log). Returns None on no key / exhausted chain, as before.
    """
    async def make_call(client, model_id, attribution, effort_budget):
        kwargs = _apply_attribution(dict(
            model=model_id,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ), attribution)
        kwargs = _apply_effort(kwargs, effort_budget)
        return await client.messages.create(**kwargs)

    def extract(message):
        return _response_text(message)

    try:
        return await _execute_chain(
            task_type=task_type, org_id=org_id, model_key=model_key,
            model_override=model, make_call=make_call, extract=extract,
        )
    except AIChainExhausted as exc:
        print(f"call_claude_text exhausted: {exc}")
        return None


async def call_claude_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
    max_tokens: int = 2000,
    *,
    org_id=None,
    model_key: str = ASSISTANT_MODEL_KEY,
    task_type: str = "assistant",
) -> dict | None:
    """Call Claude with tool-use and return the raw response dict.

    The model resolves from org_settings (``ai.model.assistant`` by default) —
    pass ``model`` to override. Returns a dict with keys: stop_reason, content
    (list of blocks). Returns None when the API key is absent or the whole model
    chain fails. Routes through the Sprint-27 chain executor (per-org fallback
    chain + ai_decision_log).
    """
    async def make_call(client, model_id, attribution, effort_budget):
        kwargs: dict = dict(
            model=model_id,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools
        kwargs = _apply_attribution(kwargs, attribution)
        kwargs = _apply_effort(kwargs, effort_budget)
        return await client.messages.create(**kwargs)

    def extract(message):
        return {
            "stop_reason": message.stop_reason,
            "content": [b.model_dump() for b in message.content],
        }

    try:
        return await _execute_chain(
            task_type=task_type, org_id=org_id, model_key=model_key,
            model_override=model, make_call=make_call, extract=extract,
        )
    except AIChainExhausted as exc:
        print(f"call_claude_with_tools exhausted: {exc}")
        return None


# ---------------------------------------------------------------------------
# Foundation answer extraction
# ---------------------------------------------------------------------------
_ANSWER_SYSTEM = (
    "You are extracting structured investment profile data from a client's "
    "conversational answer. Return ONLY valid JSON, no other text.\n\n"
    "Extract whatever fields are relevant from the answer. Common fields to "
    "look for:\n"
    "- time_horizon_years (integer)\n"
    "- investment_objectives (array of strings)\n"
    "- risk_floor (description of catastrophic scenario to avoid)\n"
    "- liquidity_needs_description (text)\n"
    "- decision_makers (array of names/roles)\n"
    "- past_bad_advice (description)\n"
    "- non_negotiables (array)\n"
    "- complexity_preference (low/medium/high)\n"
    "- money_meaning (text)\n"
    "- advisor_contact_preference (text)\n"
    "- behavioral_risk_indicators (array)\n"
    "- key_concerns (array)\n\n"
    "Only include fields where the answer provides clear evidence. "
    "Confidence 0-1.\n\n"
    "Return format:\n"
    '{"fields": {"field_name": "value"}, "confidence": 0.85, '
    '"summary": "One sentence summary"}'
)


async def extract_from_answer(
    pool, org_id, entity_id, question_id, answer_id, question_text, answer_text
) -> dict:
    """Extract structured fields from one Foundation answer and persist them."""
    model = await resolve_model(org_id)
    parsed = await call_claude_json(
        _ANSWER_SYSTEM,
        f"Question: {question_text}\nAnswer: {answer_text}",
        max_tokens=500,
        model=model,
        org_id=org_id,
        task_type="profile_extraction",
    )
    fields = (parsed or {}).get("fields") or {}
    confidence = (parsed or {}).get("confidence")
    summary = (parsed or {}).get("summary")
    payload = {"fields": fields, "summary": summary}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO investment_profile_extractions
                (org_id, entity_id, question_id, answer_id, extracted_fields,
                 extraction_model, confidence)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            RETURNING id
            """,
            org_id, entity_id, question_id, answer_id,
            json.dumps(payload),
            model if parsed is not None else None,
            confidence,
        )
    return {"id": str(row["id"]), "fields": fields, "confidence": confidence,
            "summary": summary}


async def extract_all_for_entity(pool, org_id, entity_id) -> list[dict]:
    """Run extraction on every answered Foundation question for an entity."""
    async with pool.acquire() as conn:
        answers = await conn.fetch(
            """
            SELECT a.id AS answer_id, a.question_id, a.answer_value,
                   q.question_text
            FROM investment_profile_answers a
            JOIN investment_profile_questions q ON q.id = a.question_id
            WHERE a.entity_id = $1 AND a.org_id = $2
              AND a.valid_to IS NULL AND a.system_to IS NULL
              AND q.category = 'foundation'
              AND a.answer_value IS NOT NULL AND a.answer_value <> ''
            ORDER BY q.display_order
            """,
            entity_id, org_id,
        )
        # Avoid duplicate extractions for answers already processed.
        done = await conn.fetch(
            "SELECT answer_id FROM investment_profile_extractions WHERE entity_id = $1",
            entity_id,
        )
    done_ids = {str(r["answer_id"]) for r in done}

    results = []
    for a in answers:
        if str(a["answer_id"]) in done_ids:
            continue
        results.append(
            await extract_from_answer(
                pool, org_id, entity_id, a["question_id"], a["answer_id"],
                a["question_text"], a["answer_value"],
            )
        )
    return results


# ---------------------------------------------------------------------------
# CRM note extraction
# ---------------------------------------------------------------------------
_NOTE_SYSTEM = (
    "You are extracting CRM updates from an advisor's meeting note. Return ONLY "
    "JSON.\n\n"
    "Look for updates to:\n"
    "- contact info changes (email, phone, address)\n"
    "- life events (marriage, divorce, death, birth, graduation, illness)\n"
    "- financial events (liquidity event, inheritance, business sale, home "
    "purchase)\n"
    "- relationship changes (new advisor, new accountant, family member added)\n"
    "- preference updates (communication style, meeting frequency, topics of "
    "interest)\n"
    "- risk indicator changes (new concerns, changed risk appetite)\n\n"
    "Return format:\n"
    '{"entity_updates": {"field": "new_value"}, '
    '"new_attributes": {"key": "value"}, "summary": "One sentence"}'
)


async def extract_from_note(pool, org_id, note_id, entity_id, note_text) -> dict:
    """Extract CRM field updates from a meeting note and store on the note row.

    Updates are stored as suggestions in extracted_fields — never auto-applied to
    the entity record; the advisor confirms via the UI.
    """
    model = await resolve_model(org_id)
    parsed = await call_claude_json(
        _NOTE_SYSTEM, f"Meeting note: {note_text}", max_tokens=500, model=model,
        org_id=org_id, task_type="crm_extraction",
    )
    if parsed is None:
        # No API key or call failed — mark skipped so the UI doesn't hang.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE entity_notes SET extraction_status = 'skipped', "
                "updated_at = now() WHERE id = $1",
                note_id,
            )
        return {}

    payload = {
        "entity_updates": parsed.get("entity_updates") or {},
        "new_attributes": parsed.get("new_attributes") or {},
        "summary": parsed.get("summary"),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE entity_notes
            SET extracted_fields = $2::jsonb,
                extraction_model = $3,
                extraction_status = 'completed',
                updated_at = now()
            WHERE id = $1
            """,
            note_id, json.dumps(payload), model,
        )
    return payload
