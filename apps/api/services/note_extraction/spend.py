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
import dataclasses
import os
from dataclasses import dataclass, field

from services.note_extraction.trim import estimate_tokens_chars

DEFAULT_JEV_COST_ESTIMATE = 0.01
UNKNOWN_PRICE_PER_TOKEN = (1e-06, 4e-06)   # pessimistic when a deployment has no price


class UnpricedModelError(RuntimeError):
    """A bulk run named a model with no proxy price and no manual price."""

    def __init__(self, models: list[str]):
        super().__init__(
            "bulk runs refuse a model whose price the proxy does not know and that has no manual price "
            "on its platform_model_catalog entry (manual_input_cost_per_mtok / manual_output_cost_per_mtok): "
            + ", ".join(models))
        self.models = models


# ── Bulk-run price guard (notefields.structural) ────────────────────────────
# Pilot, evaluation, inventory and B2 extraction runs are BULK: a model whose
# price is unknown would make the spending cap meaningless (the pessimistic
# UNKNOWN_PRICE_PER_TOKEN guess is fine for one call, not for thousands). So a
# bulk run first resolves every model it will call through
# ``priced_catalog_for_bulk``: the proxy's own price wins; a model the proxy has
# no price for takes the catalog entry's manual price (per million tokens,
# price_source 'manual'), and both estimates and recorded costs then use it;
# a model with neither is refused before any call. Jev (System One) is not a
# catalog model and keeps its explicit per-call estimate.
def has_price(dep) -> bool:
    return dep is not None and dep.input_cost_per_token is not None and dep.output_cost_per_token is not None


async def load_manual_prices(conn) -> dict[str, tuple[float, float]]:
    rows = await conn.fetch(
        "SELECT model_id, manual_input_cost_per_mtok, manual_output_cost_per_mtok FROM platform_model_catalog "
        "WHERE manual_input_cost_per_mtok IS NOT NULL AND manual_output_cost_per_mtok IS NOT NULL")
    return {r["model_id"]: (float(r["manual_input_cost_per_mtok"]), float(r["manual_output_cost_per_mtok"]))
            for r in rows}


def apply_manual_prices(catalog: dict, manual: dict[str, tuple[float, float]]) -> dict:
    """A copy of ``catalog`` where every deployment the proxy has no price for
    takes its manual price (per token = per million / 1e6). A proxy price is
    never overridden."""
    out = {}
    for name, dep in catalog.items():
        if not has_price(dep) and name in manual:
            pin, pout = manual[name]
            dep = dataclasses.replace(dep, input_cost_per_token=pin / 1e6, output_cost_per_token=pout / 1e6,
                                      cache_read_cost_per_token=None, price_source="manual")
        elif not has_price(dep):
            dep = dataclasses.replace(dep, price_source="none")
        out[name] = dep
    return out


def assert_bulk_priced(catalog: dict, deployments) -> None:
    missing = sorted({d for d in deployments if d and not has_price(catalog.get(d))})
    if missing:
        raise UnpricedModelError(missing)


async def priced_catalog_for_bulk(conn, catalog: dict, deployments) -> dict:
    """The catalog a bulk run must use: manual prices applied, and every model
    in ``deployments`` priced — else UnpricedModelError before any call."""
    priced = apply_manual_prices(catalog, await load_manual_prices(conn))
    assert_bulk_priced(priced, deployments)
    return priced


def recorded_cost(dep, header_cost: float | None, input_tokens, output_tokens, cached_tokens=None) -> float | None:
    """The cost to record for one call. The proxy's response-cost header is its
    live price; when the price is MANUAL the proxy has none (its header is 0 or
    absent), so the call is priced from the manual rate instead."""
    if dep is not None and getattr(dep, "price_source", "proxy") == "manual":
        return priced_cost(dep, input_tokens, output_tokens, cached_tokens)
    if header_cost is not None:
        return header_cost
    return priced_cost(dep, input_tokens, output_tokens, cached_tokens)


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
