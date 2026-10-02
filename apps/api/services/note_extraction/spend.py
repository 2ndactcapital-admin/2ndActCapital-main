"""The hard spending cap per run, and the cost estimates the dry run prints.

The cap is enforced IN CODE FROM RECORDED COSTS: every call first RESERVES its
estimated cost; if ``spent + estimate`` would exceed the cap the call is never
made and ``SpendCapReached`` is raised, which the run catches to stop cleanly
(status ``stopped_spend_cap``, every finished note kept). After the call, the
reservation is replaced by the cost actually recorded on the reading (the
proxy's ``x-litellm-response-cost`` from its live price list; the token-priced
estimate only when the proxy reports none). ``spent`` therefore always equals
the sum of ``cost_usd`` written for the run.

Estimates: input tokens = prompt characters / 4 (trim.estimate_tokens), output
tokens = the call's max_tokens ceiling (pessimistic on purpose), priced from
the deployment's own entry in /model/info. Jev has no proxy price, so its
per-call estimate is ``NOTE_EXTRACTION_JEV_COST_ESTIMATE_USD`` (default 0.01).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from services.note_extraction.trim import estimate_tokens_chars

DEFAULT_JEV_COST_ESTIMATE = 0.01
UNKNOWN_PRICE_PER_TOKEN = (1e-06, 4e-06)   # pessimistic when a deployment has no price


class SpendCapReached(RuntimeError):
    def __init__(self, cap: float, spent: float, needed: float, what: str):
        super().__init__(
            f"spending cap ${cap:.4f} reached: spent ${spent:.6f}, next call ({what}) "
            f"estimated ${needed:.6f}")
        self.cap, self.spent, self.needed, self.what = cap, spent, needed, what


def jev_cost_estimate() -> float:
    raw = (os.environ.get("NOTE_EXTRACTION_JEV_COST_ESTIMATE_USD") or "").strip()
    try:
        return float(raw) if raw else DEFAULT_JEV_COST_ESTIMATE
    except ValueError:
        return DEFAULT_JEV_COST_ESTIMATE


def estimate_call_cost(deployment, prompt_chars: int, max_out_tokens: int) -> float:
    tin = estimate_tokens_chars(prompt_chars)
    pin = getattr(deployment, "input_cost_per_token", None) if deployment else None
    pout = getattr(deployment, "output_cost_per_token", None) if deployment else None
    if pin is None or pout is None:
        pin, pout = UNKNOWN_PRICE_PER_TOKEN
    return tin * float(pin) + max_out_tokens * float(pout)


def priced_cost(deployment, input_tokens: int | None, output_tokens: int | None,
                cached_tokens: int | None = None) -> float | None:
    if deployment is None or input_tokens is None or output_tokens is None:
        return None
    pin = deployment.input_cost_per_token
    pout = deployment.output_cost_per_token
    if pin is None or pout is None:
        return None
    cached = cached_tokens or 0
    pcache = deployment.cache_read_cost_per_token if deployment.cache_read_cost_per_token is not None else pin
    return (input_tokens - cached) * pin + cached * pcache + output_tokens * pout


@dataclass
class SpendTracker:
    cap_usd: float
    spent_usd: float = 0.0
    reserved_usd: float = 0.0
    calls: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def reserve(self, estimate: float, what: str) -> float:
        async with self._lock:
            if self.spent_usd + self.reserved_usd + estimate > self.cap_usd:
                raise SpendCapReached(self.cap_usd, self.spent_usd + self.reserved_usd, estimate, what)
            self.reserved_usd += estimate
            return estimate

    async def settle(self, reservation: float, actual: float | None) -> float:
        """Replace a reservation with the recorded cost (the estimate when the
        call reported none). Returns the amount recorded."""
        recorded = reservation if actual is None else float(actual)
        async with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - reservation)
            self.spent_usd += recorded
            self.calls += 1
        return recorded

    @property
    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd - self.reserved_usd)


@dataclass
class PlannedCall:
    reference_filing_id: str
    slot: str
    deployment: str
    prompt_chars: int
    est_input_tokens: int
    est_output_tokens: int
    est_cost_usd: float


@dataclass
class Plan:
    calls: list[PlannedCall] = field(default_factory=list)
    notes: int = 0
    notes_skipped: list[dict] = field(default_factory=list)
    jev_calls_assumed: int = 0
    escalation_calls_assumed: int = 0

    @property
    def total_usd(self) -> float:
        return sum(c.est_cost_usd for c in self.calls)

    def summary(self) -> dict:
        by_slot: dict[str, dict] = {}
        for c in self.calls:
            s = by_slot.setdefault(f"{c.slot}:{c.deployment}", {"calls": 0, "est_cost_usd": 0.0,
                                                                  "est_input_tokens": 0})
            s["calls"] += 1
            s["est_cost_usd"] += c.est_cost_usd
            s["est_input_tokens"] += c.est_input_tokens
        return {"notes": self.notes, "calls": len(self.calls), "est_total_usd": round(self.total_usd, 6),
                "by_slot": by_slot, "notes_skipped": self.notes_skipped[:20],
                "jev_calls_assumed": self.jev_calls_assumed,
                "escalation_calls_assumed": self.escalation_calls_assumed}
