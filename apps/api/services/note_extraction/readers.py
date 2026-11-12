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
Either way the raw text is validated PER FIELD (notefix1 Task 3,
``schema.parse_reader_output``): a field that fails its own shape is dropped
and recorded on ``ReaderCall.field_issues``, every other field is kept, and
the WHOLE call only fails (status 'invalid') when the response is not
parseable JSON at all.

PROVENANCE: the provider-reported model must match the deployment asked for
(``proxy.reported_model_matches``) and the proxy must report zero attempted
fallbacks; otherwise every reading from the call is 'model_mismatch' and none
of them can count toward agreement.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field

from services.note_extraction import proxy
from services.note_extraction.schema import FieldSpec, json_schema, parse_reader_output
from services.note_extraction.spend import SpendTracker, estimate_call_cost, recorded_cost

PROMPT_VERSION = "notefields.reader.v4"   # notefix2 Task 6: added the few-shot block

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

# notefix2 Task 6: four REAL corrected cases from the gold-1 second-reader
# review (docs/gold/gold1_second_reader_v1.json) — one BofA "threshold value"
# barrier, one Goldman buffer-RATE, one Barclays digital payout, one BofA
# issuer-affiliated agent. Kept under ~1,500 tokens (see FEW_SHOT_TOKEN_EST
# below, printed by verify_notefix2.py) — four examples, not a library.
FEW_SHOT = """Four real corrected examples, from hand-checking 50 SEC filings against their own extracted values:

1) Buffer vs barrier — a BofA note whose filing never says "barrier":
   Quote: "Threshold Value 60% of Starting Value" ... the hypothetical table shows a 59.99% \
ending value paying $599.90 per $1,000 — the FULL decline from the first dollar, not a cushioned loss.
   Correct: protection_type "barrier", barrier_pct 60, buffer_pct null. The filing's own word \
"Threshold" is not evidence either way — apply the test: at a final level just below it, does the \
investor lose the full (initial - final)/initial? Here yes, so it is a barrier, never a buffer.

2) A buffer RATE is not a buffer — a Goldman Sachs note:
   Quote: "Buffer amount: 10%" ... "the buffer rate, which equals approximately 111.11%".
   Correct: buffer_pct 10 (the buffer AMOUNT — the decline protected from). 111.11% is the buffer \
RATE, a downside leverage multiplier (100 / (100 - 10)) applied BEYOND the buffer — never a second \
buffer_pct, and never confused with the 10% amount.

3) A digital payout is not participation — a Barclays note:
   Quote: "Digital Percentage: 40.00%" ... "$1,000 + [$1,000 x Digital Percentage] ... if the \
Final Value is greater than or equal to the Barrier Value but less than the Initial Value".
   Correct: fixed_payout_amount 1400, fixed_payout_threshold_pct = the Barrier Value as a percent \
of the initial level. participation_rate and cap_pct are both null — a digital or fixed percentage \
is never upside participation, no matter how the filing phrases the dollar amount.

4) An issuer-affiliated agent stays affiliated even under a generic title — a BofA note:
   Quote: "Selling Agent: MLPF&S, an affiliate of BofA Finance" (BofA Finance is this note's own \
issuer).
   Correct: distribution includes {"name": "MLPF&S", "role": "issuer_affiliated_agent", ...}. The \
filing's own label ("Selling Agent") never overrides a stated affiliation to the issuer — a \
broker-dealer the filing itself calls an issuer affiliate is role issuer_affiliated_agent, not \
distribution_agent or dealer."""

USER_PREFIX = "Read the filing below and answer with the JSON object only.\n\n"


def few_shot_token_estimate() -> int:
    """chars/4 — the same rough estimator used throughout this codebase for
    unknown-tokenizer cost planning (trim.estimate_tokens_chars)."""
    return len(FEW_SHOT) // 4


@dataclass
class ReaderConfig:
    slot: str                      # 'model_1' | 'model_2' | 'escalation'
    deployment: str                # proxy deployment name, never a raw upstream id
    reasoning_effort: str | None = None
    max_tokens: int = 4000
    # readerfix1: the larger output allowance for the ONE retry of an answer
    # that came back empty because the allowance was used up (None = no retry).
    retry_max_tokens: int | None = None


# ── readerfix1: the ONE per-deployment reader setting (fund AND note readers) ──
# A reasoning model (gpt-5 family / o-series) spends its output allowance on
# hidden reasoning tokens first; at the provider's default (medium) effort
# gpt-5-nano used all 3,000 tokens on IVV's prospectus and returned no text.
# Evidence (live readings, docs/READERFIX1_NOTES.md): gpt-5-mini at medium
# effort used 2,406 output tokens on IVV (answer ~513) and p50 8,266 / p90
# 12,249 / max 15,426 on notes (answer median ~1,858, max ~4,808). So reasoning
# models get an explicit LOW effort and the task's own answer allowance PLUS a
# 12,000-token reasoning headroom (>= mini's medium-effort p90 on notes; the
# low effort needs less); the one retry doubles it (notes: 32,000 — the gold
# prefill's proven floor, MIN_MAX_TOKENS). Non-reasoning models: unchanged.
REASONING_EFFORT = "low"
REASONING_HEADROOM_TOKENS = 12_000
RETRY_FACTOR = 2
NOTE_ANSWER_TOKENS = 4000          # the note reader's answer allowance (unchanged for non-reasoning models)
ALLOWANCE_EXHAUSTED = "the model used its whole output allowance"
_REASONING_NAME = re.compile(r"(^|[/:])(gpt-5|o1|o3|o4)", re.I)


def is_reasoning_model(deployment: str | None, dep=None) -> bool:
    """True for a reasoning model: the proxy's ``supports_reasoning`` flag, or
    a gpt-5 / o-series name on the deployment or its upstream."""
    if getattr(dep, "supports_reasoning", None):
        return True
    return any(n and _REASONING_NAME.search(str(n)) for n in (deployment, getattr(dep, "upstream", None)))


def reader_settings(slot: str, deployment: str, *, answer_tokens: int, catalog: dict | None = None,
                    effort: str | None = None, max_tokens: int | None = None) -> ReaderConfig:
    """The reader setting for one deployment — used by BOTH the note reader
    (runner.reader_config) and the fund-terms reader (replica_terms).
    ``effort`` / ``max_tokens`` are explicit operator overrides (pilot / eval
    CLIs) and win when given."""
    reasoning = is_reasoning_model(deployment, (catalog or {}).get(deployment))
    first = int(max_tokens) if max_tokens else (
        answer_tokens + REASONING_HEADROOM_TOKENS if reasoning else answer_tokens)
    return ReaderConfig(slot=slot, deployment=deployment,
                        reasoning_effort=effort or (REASONING_EFFORT if reasoning else None),
                        max_tokens=first, retry_max_tokens=first * RETRY_FACTOR)


def allowance_exhausted(body: dict | None, max_tokens: int | None) -> bool:
    """An answer with NO text because the output allowance ran out: empty
    content AND (finish_reason 'length' OR output tokens >= the allowance)."""
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError):
        return False
    content = (choice.get("message") or {}).get("content")
    if content is not None and str(content).strip():
        return False
    _tin, tout, _c = proxy.response_usage(body)
    return choice.get("finish_reason") == "length" or (
        tout is not None and max_tokens is not None and tout >= max_tokens)


@dataclass
class Sent:
    resp: object                                  # the response whose content is used
    exhausted: bool                               # the used response is still empty from the allowance
    retry: dict | None                            # None = no retry was needed
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cost: float | None                            # summed over every attempt
    latency_ms: int | None


def _add(a, b):
    return b if a is None else (a if b is None else a + b)


async def send(cfg: ReaderConfig, body: dict, dep=None, *, before_retry=None) -> Sent:
    """proxy.chat with the ONE empty-answer retry (readerfix1). Every attempt
    goes through proxy.chat, so inside a budget scope each one is reserved and
    settled against the daily budget. ``before_retry(body)`` (optional) runs
    before the retry — the note reader reserves its dollar cap there. A retry
    the budget / cap refuses is not made and the answer is recorded invalid."""
    from services import ai_budget
    from services.note_extraction.spend import SpendCapReached, recorded_cost

    def usage(resp):
        tin, tout, cached = proxy.response_usage(resp.body)
        return tin, tout, cached, recorded_cost(dep, proxy.header_cost(resp), tin, tout, cached)

    resp = await proxy.chat(body)
    tin, tout, cached, cost = usage(resp)
    out = Sent(resp, False, None, tin, tout, cached, cost, resp.latency_ms)
    if resp.status != 200 or not allowance_exhausted(resp.body, body.get("max_tokens")):
        return out
    first_mt = body.get("max_tokens")
    out.exhausted = True
    out.retry = {"made": False, "first_max_tokens": first_mt, "first_output_tokens": tout,
                 "first_finish_reason": (resp.body["choices"][0] or {}).get("finish_reason")}
    if not cfg.retry_max_tokens or (first_mt and cfg.retry_max_tokens <= first_mt):
        out.retry["reason"] = "no larger allowance configured"
        return out
    body2 = {**body, "max_tokens": cfg.retry_max_tokens}
    try:
        if before_retry is not None:
            await before_retry(body2)
        resp2 = await proxy.chat(body2)
    except (ai_budget.BudgetReached, SpendCapReached) as exc:
        out.retry["reason"] = f"not made: {type(exc).__name__}: {exc}"[:300]
        return out
    tin2, tout2, cached2, cost2 = usage(resp2)
    out.retry.update(made=True, retry_max_tokens=cfg.retry_max_tokens, retry_output_tokens=tout2)
    out.resp = resp2
    out.exhausted = resp2.status == 200 and allowance_exhausted(resp2.body, cfg.retry_max_tokens)
    out.input_tokens, out.output_tokens = _add(tin, tin2), _add(tout, tout2)
    out.cached_tokens, out.cost = _add(cached, cached2), _add(cost, cost2)
    out.latency_ms = _add(resp.latency_ms, resp2.latency_ms)
    return out


def exhausted_error(sent: Sent) -> str:
    r = sent.retry or {}
    if r.get("made"):
        return f"{ALLOWANCE_EXHAUSTED} (retried once with {r.get('retry_max_tokens')} tokens)"
    return f"{ALLOWANCE_EXHAUSTED} (retry {r.get('reason') or 'not made'})"


@dataclass
class ReaderCall:
    slot: str
    deployment: str
    status: str                    # 'ok' | 'failed' | 'model_mismatch' | 'invalid'
    error: str | None = None
    fields: dict = field(default_factory=dict)        # key -> {value, quote}
    field_issues: dict = field(default_factory=dict)  # key -> {error, raw_value, coerced} (notefix1)
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
    retry: dict | None = None                         # readerfix1: the empty-answer retry, when one was needed
    prompt_chars: int = 0
    filings_in_call: int = 1

    @property
    def usable(self) -> bool:
        return self.status == "ok"


def build_messages(specs: list[FieldSpec], filing_text: str, *, filer: str | None,
                   form_type: str | None) -> tuple[list[dict], str]:
    """(messages, prefix_hash). The filing text is the LAST thing in the call."""
    schema = json_schema(specs)
    system = (INSTRUCTIONS + "\n\n" + FEW_SHOT + "\n\nJSON schema:\n"
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

    async def reserve_retry(body2):
        nonlocal reservation
        if spend is not None:
            reservation += await spend.reserve(
                estimate_call_cost(dep, prompt_chars, body2["max_tokens"]), f"{cfg.slot}:{cfg.deployment}:retry")

    sent = await send(cfg, request_body(cfg, messages, specs, supports_schema, tags=tags), dep,
                      before_retry=reserve_retry)
    resp = sent.resp
    call.retry = sent.retry
    call.latency_ms = sent.latency_ms
    call.proxy_model_id = resp.headers.get("x-litellm-model-id")
    call.attempted_fallbacks = resp.headers.get("x-litellm-attempted-fallbacks")
    call.input_tokens, call.output_tokens, call.cached_tokens = (sent.input_tokens, sent.output_tokens,
                                                                 sent.cached_tokens)
    actual = sent.cost
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

    if sent.exhausted:
        call.status = "invalid"
        call.error = exhausted_error(sent)
        return call
    try:
        content = resp.body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        call.status = "invalid"
        call.error = "response carries no message content"
        return call
    parsed, fatal_error, field_issues = parse_reader_output(specs, content)
    if fatal_error:
        call.status = "invalid"
        call.error = fatal_error
        return call
    call.fields = parsed
    call.field_issues = field_issues
    call.status = "ok"
    return call
