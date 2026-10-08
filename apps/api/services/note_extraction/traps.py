"""Trap detectors for the GOLD SET sampler (goldset.structural, Task 1d).

A trap is a note whose correct reading is easy to get wrong. The gold set must
hold at least ``TRAP_MINIMUMS[tag]`` notes of each kind (where the pool has
them), so that a model's score on the gold set says something about the hard
cases and not only the easy ones.

Every detector here is a RULE over the fetched filing text — no model. Each is
deliberately a little generous (a false positive costs one extra note in the
sample; a false negative hides a trap). Two of them are proxies whose precise
version needs the pre-fill readings (see ``NEEDS_READINGS``):

  named_distribution_agent  the rule sees a distribution / placement agent or
                            a dealer named in the plan of distribution; whether
                            that party is UNAFFILIATED with the issuer is a
                            judgement the pre-fill's ``distribution`` list
                            (role 'distribution_agent') makes, not a regex.
  daily_or_continuous_barrier
                            the rule sees daily / any-day / intraday monitoring
                            wording; whether it monitors the BARRIER (rather
                            than, say, a coupon) is the pre-fill's
                            ``barrier_observation`` reading.

``buffer_vs_barrier`` fires on protection wording that is easy to file under
the wrong field: a buffer and a barrier both stated, or a threshold / trigger /
knock-in term that hides which of the two it is.
"""
from __future__ import annotations

import re

from services.note_extraction import rules


def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


_WORST_OF = _rx(r"worst[- ]of|worst\s+performing|least\s+performing|lowest\s+performing")
_DAILY = _rx(r"(?:on\s+)?any\s+(?:scheduled\s+)?trading\s+day\s+during|each\s+(?:scheduled\s+)?trading\s+day\s+during|"
             r"daily\s+(?:closing|observation|monitoring)|at\s+any\s+time\s+during|intra-?day|"
             r"continuous(?:ly)?\s+(?:monitor|observ)")
_ISSUER_CALL = _rx(r"(?:redeem|call)\s+(?:all\s+(?:but\s+not\s+less\s+than\s+all\s+)?(?:or\s+a\s+portion\s+)?of\s+)?"
                   r"the\s+(?:notes|securities)[^.]{0,80}?(?:at\s+our\s+(?:sole\s+)?(?:option|discretion)|"
                   r"in\s+(?:our|its)\s+sole\s+discretion)|optional\s+(?:early\s+)?redemption|issuer\s+call|"
                   r"callable\s+at\s+the\s+option\s+of\s+the\s+issuer|early\s+redemption\s+at\s+(?:our|the\s+issuer'?s)\s+option")
_DIGITAL = _rx(r"\bdigital\b|fixed\s+(?:payment|return|payout)\s+(?:amount|percentage|of)|contingent\s+(?:minimum\s+)?return\b|"
               r"\bdigital\s+return|absolute\s+return\s+(?:barrier|buffer)?")
_BUFFER = _rx(r"\bbuffer(?:ed)?\b")
_BARRIER = _rx(r"\bbarrier\b")
_AMBIGUOUS_PROTECTION = _rx(r"downside\s+threshold|threshold\s+(?:level|value|price)|trigger\s+(?:level|value|price)|"
                            r"knock-?in|protection\s+(?:level|amount)")
_PRICE_RETURN = _rx(r"price\s+return(?:\s+(?:index|version))?|(?:does|will)\s+not\s+(?:reflect|include|take\s+into\s+account)\s+"
                    r"(?:the\s+payment\s+of\s+|any\s+)?dividends|without\s+(?:taking\s+into\s+account\s+)?dividends")
_COMMISSION_WORD = r"(?:commissions?|underwriting\s+discounts?|selling\s+concessions?|sales\s+commissions?)"
_BOUND_WORD = r"(?:up\s+to|not\s+(?:more|greater)\s+than|will\s+not\s+exceed|a\s+maximum\s+of|as\s+much\s+as)"
_UP_TO_COMMISSION = _rx(_COMMISSION_WORD + r"\b[^.]{0,160}?\b" + _BOUND_WORD + r"\s+\$?\s?\d|"
                        r"\b" + _BOUND_WORD + r"\s+\$?\s?[\d.,]+\s?%?[^.]{0,100}?\b" + _COMMISSION_WORD)
_DISTRIBUTION_AGENT = _rx(r"\b(?:distribution|placement|selling)\s+agent\b|\bacting\s+as\s+(?:a\s+|the\s+)?dealer\b|"
                          r"\bwill\s+act\s+as\s+(?:a\s+|the\s+)?(?:dealer|distributor)\b|"
                          r"\b(?:iCapital|InspereX|Incapital|Halo\s+Investing|SIMON\s+Markets)\b")


def _hypothetical(text: str) -> bool:
    res = rules.RuleResult()
    rules.find_hypothetical_rows(text, res)
    rows = res.extras.get("hypothetical_rows")
    return rows is not None and len(rows.value or []) >= 2


def _buffer_vs_barrier(text: str) -> bool:
    both = bool(_BUFFER.search(text)) and bool(_BARRIER.search(text))
    return both or bool(_AMBIGUOUS_PROTECTION.search(text))


# tag -> detector(text) -> bool. Order is the report order.
DETECTORS = {
    "worst_of": lambda t: bool(_WORST_OF.search(t)),
    "daily_or_continuous_barrier": lambda t: bool(_DAILY.search(t)),
    "issuer_call": lambda t: bool(_ISSUER_CALL.search(t)),
    "digital_fixed_payout": lambda t: bool(_DIGITAL.search(t)),
    "buffer_vs_barrier": _buffer_vs_barrier,
    "price_return_underlying": lambda t: bool(_PRICE_RETURN.search(t)),
    "up_to_commission": lambda t: bool(_UP_TO_COMMISSION.search(t)),
    "named_distribution_agent": lambda t: bool(_DISTRIBUTION_AGENT.search(t)),
    "hypothetical_example_table": _hypothetical,
}

# At least this many notes per tag in a gold batch, where the pool has them.
TRAP_MINIMUMS = {tag: 3 for tag in DETECTORS}

# Detectors that are rule PROXIES; the precise tag needs the pre-fill readings.
NEEDS_READINGS = {
    "named_distribution_agent": "unaffiliated vs issuer-affiliated agent: the pre-fill's distribution list "
                                "(member role 'distribution_agent') decides",
    "daily_or_continuous_barrier": "that the daily wording monitors the BARRIER (not a coupon): the pre-fill's "
                                   "barrier_observation reading decides",
}


def detect(text: str | None) -> list[str]:
    """Every trap tag whose detector fires on ``text``, in DETECTORS order."""
    t = text or ""
    return [tag for tag, fn in DETECTORS.items() if fn(t)]
