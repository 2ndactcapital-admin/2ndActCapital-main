"""Cascade step (d): the two READERS (Model 1, Model 2) and the escalation model.

PROMPT LAYOUT — fixed instructions and schema FIRST, the filing text LAST:

    system:  INSTRUCTIONS + the JSON schema (generated from the registry)
    user:    USER_PREFIX + "Filer: … / Form type: …" + FILING TEXT

Everything up to and including USER_PREFIX is byte-identical for every note
read with the same field set, so providers' repeated-prompt (prefix cache)
discounts apply; ``prefix_hash`` is the sha256 of exactly that prefix and is
recorded on every reading. One call carries ONE filing — there is no batching
path at all.

STRUCTURED OUTPUT: ``response_format = json_schema`` when the deployment's
price-list entry says ``supports_response_schema``, otherwise ``json_object``.
Either way the raw text is validated with Pydantic ``model_validate_json``; a
failed validation is a FAILED READING (status 'invalid'), never a crash.

PROVENANCE: the provider-reported model must match the deployment asked for
(``proxy.reported_model_matches``) and the proxy must report zero attempted
fallbacks; otherwise every reading from the call is 'model_mismatch' and none
of them can count toward agreement.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field

from services.note_extraction import proxy
from services.note_extraction.schema import FieldSpec, json_schema, parse_reader_output
from services.note_extraction.spend import SpendTracker, estimate_call_cost, recorded_cost

PROMPT_VERSION = "notefields.reader.v3"

INSTRUCTIONS = """You read ONE US structured-note pricing supplement (SEC 424B2 or FWP) and \
report its terms as ONE JSON object that matches the JSON schema below exactly.

Rules:
- Answer only from the filing text you are given. If a term is not stated, its value is null.
- For EVERY field give a SHORT EXACT quote copied verbatim from the filing (one sentence or \
table row, under 300 characters) that states the value; quote null when the value is null.
- Percentages as numbers like 70.0 (never 0.7). Dates as YYYY-MM-DD. Fees and prices as a \
percent of principal ($22.50 per $1,000 = 2.25).
- Protection: a buffer and a barrier are different things. Buffer = losses begin only after \
the underlying falls more than X%. Barrier = once breached, the FULL decline applies. \
Principal is protected only if repayment at maturity does not depend on the underlying. Put \
each level only in its own field.
- Ranges: an amount stated with "up to", "as low as", "not less than" or "between" is a MIN \
and MAX with its bound ("up to 2.50%" -> min null, max 2.50, bound "up_to"); a single stated \
amount sets min = max (bound "exact"). Quote the bound wording.
- Lists (underlyings, observation schedule, distribution): one entry per member, every \
member its own object. Never merge members into one string.
- Distribution: list EVERY participant named in the plan of distribution separately, with \
its own role and fee.
- Output JSON only — no prose, no markdown."""

USER_PREFIX = "Read the filing below and answer with the JSON object only.\n\n"


@dataclass
class ReaderConfig:
    slot: str                      # 'model_1' | 'model_2' | 'escalation'
    deployment: str                # proxy deployment name, never a raw upstream id
    reasoning_effort: str | None = None
    max_tokens: int = 4000


@dataclass
class ReaderCall:
    slot: str
    deployment: str
    status: str                    # 'ok' | 'failed' | 'model_mismatch' | 'invalid'
    error: str | None = None
    fields: dict = field(default_factory=dict)        # key -> {value, quote}
    provider_model: str | None = None
    proxy_model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    prefix_hash: str | None = None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    response_format: str | None = None
    reasoning_effort: str | None = None
    attempted_fallbacks: str | None = None
    prompt_chars: int = 0
    filings_in_call: int = 1

    @property
    def usable(self) -> bool:
        return self.status == "ok"


def build_messages(specs: list[FieldSpec], filing_text: str, *, filer: str | None,
                   form_type: str | None) -> tuple[list[dict], str]:
    """(messages, prefix_hash). The filing text is the LAST thing in the call."""
    schema = json_schema(specs)
    system = (INSTRUCTIONS + "\n\nJSON schema:\n"
              + json.dumps(schema, sort_keys=True, separators=(",", ":")))
    per_note = f"Filer: {filer or 'unknown'}\nForm type: {form_type or 'unknown'}\n\nFILING TEXT:\n"
    user = USER_PREFIX + per_note + filing_text
    prefix_hash = hashlib.sha256((system + "\x00" + USER_PREFIX).encode("utf-8")).hexdigest()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], prefix_hash


def request_body(cfg: ReaderConfig, messages: list[dict], specs: list[FieldSpec],
                 supports_schema: bool, *, tags: list[str] | None = None) -> dict:
    body: dict = {"model": cfg.deployment, "messages": messages, "max_tokens": cfg.max_tokens}
    if supports_schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "note_terms", "schema": json_schema(specs), "strict": False},
        }
    else:
        body["response_format"] = {"type": "json_object"}
    if cfg.reasoning_effort:
        body["reasoning_effort"] = cfg.reasoning_effort
    body["metadata"] = {"tags": list(tags or []) + ["usage:note_extraction", f"slot:{cfg.slot}"]}
    return body


async def read(cfg: ReaderConfig, specs: list[FieldSpec], filing_text: str, *,
               filer: str | None, form_type: str | None, catalog: dict,
               spend: SpendTracker | None, tags: list[str] | None = None) -> ReaderCall:
    """One reader call. Never raises for a provider/validation problem — the
    result carries the status. Raises SpendCapReached BEFORE calling when the
    cap would be exceeded."""
    dep = catalog.get(cfg.deployment)
    messages, prefix_hash = build_messages(specs, filing_text, filer=filer, form_type=form_type)
    prompt_chars = sum(len(m["content"]) for m in messages)
    call = ReaderCall(slot=cfg.slot, deployment=cfg.deployment, status="failed",
                      prefix_hash=prefix_hash, reasoning_effort=cfg.reasoning_effort,
                      prompt_chars=prompt_chars)
    supports_schema = bool(dep and dep.supports_response_schema)
    call.response_format = "json_schema" if supports_schema else "json_object"

    reservation = 0.0
    if spend is not None:
        reservation = await spend.reserve(
            estimate_call_cost(dep, prompt_chars, cfg.max_tokens), f"{cfg.slot}:{cfg.deployment}")

    if dep is not None and dep.duplicate:
        call.error = (f"deployment '{cfg.deployment}' has more than one deployment behind it on the "
                      f"proxy and would be load-balanced — refused")
        if spend is not None:
            call.cost_usd = await spend.settle(reservation, 0.0)
        return call

    resp = await proxy.chat(request_body(cfg, messages, specs, supports_schema, tags=tags))
    call.latency_ms = resp.latency_ms
    call.proxy_model_id = resp.headers.get("x-litellm-model-id")
    call.attempted_fallbacks = resp.headers.get("x-litellm-attempted-fallbacks")
    call.input_tokens, call.output_tokens, call.cached_tokens = proxy.response_usage(resp.body)
    actual = recorded_cost(dep, proxy.header_cost(resp), call.input_tokens, call.output_tokens,
                           call.cached_tokens)
    if resp.status != 200:
        actual = actual if actual is not None else 0.0   # a refused call is not billed
    if spend is not None:
        call.cost_usd = await spend.settle(reservation, actual)
    else:
        call.cost_usd = actual

    if resp.status != 200 or resp.body is None:
        detail = resp.error or (resp.text or "")[:300]
        call.error = f"HTTP {resp.status}: {detail}"
        return call

    call.provider_model = resp.body.get("model")
    if call.attempted_fallbacks not in (None, "0"):
        call.status = "model_mismatch"
        call.error = f"the proxy attempted {call.attempted_fallbacks} fallback(s)"
        return call
    upstream = dep.upstream if dep else None
    if not proxy.reported_model_matches(upstream, call.provider_model):
        call.status = "model_mismatch"
        call.error = (f"asked for '{cfg.deployment}' ({upstream}) but the provider reported "
                      f"'{call.provider_model}'")
        return call
    if dep and dep.deployment_id and call.proxy_model_id and call.proxy_model_id != dep.deployment_id:
        call.status = "model_mismatch"
        call.error = (f"served by deployment {call.proxy_model_id}, not {dep.deployment_id}")
        return call

    try:
        content = resp.body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        call.status = "invalid"
        call.error = "response carries no message content"
        return call
    parsed, error = parse_reader_output(specs, content)
    if error:
        call.status = "invalid"
        call.error = error
        return call
    call.fields = parsed
    call.status = "ok"
    return call
