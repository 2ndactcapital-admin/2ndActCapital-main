"""Cascade step (f): JEV ONLY ON DISAGREEMENTS — one call per note.

One call carries ALL of the note's disputed questions. Each question asks about
THE DOCUMENT, never "which model was right":

  * options are ``candidate_1 … candidate_n`` plus ``not_stated``;
  * the question's INSTRUCTIONS state the term (its general rule — e.g. what a
    buffer is versus a barrier) and list the candidate values;
  * the CRITERIA are GENERAL selection rules, the same words for every field —
    they never contain a candidate value, a source name or a model name, so
    nothing in a criterion can leak the expected answer.

The STATE is only the relevant clauses: every candidate's verified quote with
some surrounding context, merged and de-duplicated, capped to stay inside Jev's
32K-token limit (``JEV_MAX_STATE_CHARS`` at ~4 characters per token, leaving
room for the questions).

A choice is accepted when its probability reaches the field's threshold —
higher for critical fields. ``not_stated`` accepted resolves the field to null.
Anything below threshold goes to escalation.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from services.note_extraction import proxy
from services.note_extraction.compare import Candidate, FieldComparison
from services.note_extraction.documents import FilingDocument
from services.note_extraction.spend import SpendTracker, jev_cost_estimate

JEV_TOKEN_LIMIT = 32_000
JEV_MAX_STATE_CHARS = 80_000          # ~20K tokens of clauses; questions get the rest
CONTEXT_CHARS = 400
THRESHOLD_CRITICAL = 0.90
THRESHOLD_STANDARD = 0.75
NOT_STATED = "not_stated"

CRITERION_CANDIDATE = (
    "Choose this option when the clauses state the term's value as the candidate with this "
    "number in the question, in the units the question specifies. Equivalent wording counts; "
    "a different number, date, name or category does not."
)
CRITERION_NOT_STATED = (
    "Choose this option when the clauses do not state the term at all, or state it with a "
    "value that differs from every numbered candidate in the question."
)


def threshold_for(spec) -> float:
    return THRESHOLD_CRITICAL if spec.critical else THRESHOLD_STANDARD


def _display(value) -> str:
    if value is None:
        return "not stated"
    if isinstance(value, list):
        parts = []
        for v in value:
            if isinstance(v, dict):
                fee = ""
                if v.get("fee_pct") is not None or v.get("fee_type"):
                    fee = f", {v.get('fee_type') or 'fee'} {v.get('fee_pct')}%"
                parts.append(f"{v.get('name')} ({v.get('role')}{fee})")
            else:
                parts.append(str(v))
        return "; ".join(parts)
    return str(value)


@dataclass
class JevQuestion:
    field_key: str
    options: dict[str, Candidate | None]          # option key -> candidate (None = not stated)
    body: dict


def build_question(comp: FieldComparison) -> JevQuestion:
    spec = comp.spec
    options: dict[str, Candidate | None] = {}
    lines = []
    n = 0
    for c in comp.candidates:
        if c.normalized is None:
            continue
        n += 1
        key = f"candidate_{n}"
        options[key] = c
        lines.append(f"Candidate {n}: {_display(c.value)}")
    options[NOT_STATED] = None
    instructions = (
        f"Question about this structured-note filing: what is the '{spec.label}' of these notes? "
        f"{spec.description} Units: percentages as numbers like 70.0, dates as YYYY-MM-DD, fees "
        f"and prices as a percent of principal. " + " ".join(lines)
    )
    criteria = {k: CRITERION_CANDIDATE for k in options if k != NOT_STATED}
    criteria[NOT_STATED] = CRITERION_NOT_STATED
    return JevQuestion(spec.key, options, {"type": "choice", "instructions": instructions,
                                           "criteria": criteria})


def _windows(doc: FilingDocument, comps: list[FieldComparison], context: int) -> list[tuple[int, int]]:
    spans = []
    for comp in comps:
        for c in comp.candidates:
            for e in c.evidence:
                if e.location is not None:
                    spans.append((max(0, e.location.text_start - context),
                                  min(len(doc.text), e.location.text_end + context)))
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def build_state(doc: FilingDocument, comps: list[FieldComparison], fallback_text: str) -> str:
    """Only the relevant clauses, inside the 32K-token budget."""
    for context in (CONTEXT_CHARS, 200, 80):
        wins = _windows(doc, comps, context)
        state = "\n…\n".join(doc.text[s:e] for s, e in wins)
        if len(state) <= JEV_MAX_STATE_CHARS:
            break
    if not wins:
        state = fallback_text
    return state[:JEV_MAX_STATE_CHARS]


@dataclass
class JevAnswer:
    field_key: str
    choice: str | None
    probability: float | None
    candidate: Candidate | None
    accepted: bool
    resolves_to_null: bool = False


@dataclass
class JevCall:
    route: str
    status: str                       # 'ok' | 'failed'
    error: str | None = None
    provider_model: str | None = None
    answers: dict[str, JevAnswer] = field(default_factory=dict)
    request: dict | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    state_chars: int = 0
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def build_request(route: str, doc: FilingDocument, disputed: list[FieldComparison],
                  fallback_text: str) -> tuple[dict, dict[str, JevQuestion]]:
    questions = {c.spec.key: build_question(c) for c in disputed}
    state = build_state(doc, disputed, fallback_text)
    body = {"model": route, "state": state,
            "questions": {k: q.body for k, q in questions.items()}}
    return body, questions


async def ask(route: str, doc: FilingDocument, disputed: list[FieldComparison], *,
              fallback_text: str, spend: SpendTracker | None) -> JevCall:
    """ONE Jev call for every disputed field of this note."""
    body, questions = build_request(route, doc, disputed, fallback_text)
    call = JevCall(route=route, status="failed", request=body, state_chars=len(body["state"]))
    reservation = 0.0
    if spend is not None:
        reservation = await spend.reserve(jev_cost_estimate(), f"jev:{route}")
    resp = await proxy.jev(body)
    call.latency_ms = resp.latency_ms
    usage_cost = None
    if isinstance(resp.body, dict):
        usage_cost = (resp.body.get("usage") or {}).get("cost_usd")
    # Jev reports no price through the proxy: record the configured estimate
    # unless TypeSafe returned a cost, so the cap never under-counts it.
    actual = usage_cost if usage_cost is not None else (0.0 if resp.status != 200 else None)
    call.cost_usd = await spend.settle(reservation, actual) if spend is not None else actual
    if resp.status != 200 or not isinstance(resp.body, dict):
        call.error = f"HTTP {resp.status}: {resp.error or (resp.text or '')[:300]}"
        return call
    call.provider_model = resp.body.get("model")
    if call.provider_model != route and not os.environ.get("NOTE_EXTRACTION_JEV_ALLOW_ALIAS"):
        call.error = f"asked for pinned '{route}' but TypeSafe reported '{call.provider_model}'"
        return call
    answers = resp.body.get("answers") or {}
    for key, q in questions.items():
        a = answers.get(key) if isinstance(answers, dict) else None
        choice = a.get("choice") if isinstance(a, dict) else None
        probs = a.get("probabilities") if isinstance(a, dict) else None
        p = None
        if isinstance(probs, dict) and choice in probs:
            try:
                p = float(probs[choice])
            except (TypeError, ValueError):
                p = None
        comp = next(c for c in disputed if c.spec.key == key)
        ok = choice in q.options and p is not None and p >= threshold_for(comp.spec)
        call.answers[key] = JevAnswer(
            field_key=key, choice=choice, probability=p,
            candidate=q.options.get(choice) if choice in q.options else None,
            accepted=ok, resolves_to_null=ok and choice == NOT_STATED,
        )
    call.status = "ok"
    return call
