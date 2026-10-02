"""Cascade step (e): COMPARE IN CODE.

A field is VERIFIED at no further cost when both readers returned it, their
normalised values agree, AND at least one of their quotes is found verbatim in
the filing AND (where the check applies) the value appears in that quote.
Agreement on "absent" (both null) is ``agreed_null``. Anything else — a
disagreement, an agreement whose quote cannot be found (a fabricated quote is
rejected here), or only one usable reader — is DISPUTED and carries its
distinct candidate values onward to Jev. Rules and EdgarTools readings join a
disputed field's candidates; they never overrule two agreeing, verified readers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.note_extraction.documents import FilingDocument, QuoteLocation, locate_quote
from services.note_extraction.schema import FieldSpec, normalize, value_in_quote


@dataclass
class Evidence:
    """One source's answer for one field, with its quote checked."""
    source: str                       # rules | edgartools | model_1 | model_2 | escalation | jev
    value: object
    quote: str | None
    normalized: str | None
    location: QuoteLocation | None
    quote_verified: bool | None
    value_in_quote: bool | None
    reading_key: str | None = None    # set by the cascade: links back to the stored reading

    @property
    def supports(self) -> bool:
        """The quote is real and (where checkable) contains the value."""
        return bool(self.quote_verified) and self.value_in_quote is not False


def evidence(doc: FilingDocument, spec: FieldSpec, source: str, value, quote) -> Evidence:
    norm = normalize(spec, value)
    loc = locate_quote(doc, quote) if quote else None
    verified = None if not quote else loc is not None
    in_quote = value_in_quote(spec, value, quote) if (value is not None and quote) else (
        None if value is None else False)
    return Evidence(source, value if norm is not None else None, quote, norm, loc, verified, in_quote)


@dataclass
class Candidate:
    normalized: str | None            # None = "not stated"
    value: object
    sources: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def best(self) -> Evidence | None:
        for e in self.evidence:
            if e.supports:
                return e
        return self.evidence[0] if self.evidence else None


@dataclass
class FieldComparison:
    spec: FieldSpec
    outcome: str                      # 'verified_agreement' | 'agreed_null' | 'disputed'
    value: object = None
    winner: Evidence | None = None
    candidates: list[Candidate] = field(default_factory=list)
    reason: str = ""


def compare_field(spec: FieldSpec, m1: Evidence | None, m2: Evidence | None,
                  extra: list[Evidence] | None = None) -> FieldComparison:
    """``m1``/``m2`` are None when that reader's call was not usable."""
    readers = [e for e in (m1, m2) if e is not None]
    if len(readers) == 2 and m1.normalized == m2.normalized:
        if m1.normalized is None:
            return FieldComparison(spec, "agreed_null", None, None, [], "both readers: absent")
        winner = next((e for e in (m1, m2) if e.supports), None)
        if winner is not None:
            return FieldComparison(spec, "verified_agreement", winner.value, winner, [],
                                   "both readers agree and the quote verifies")
        reason = "readers agree but no quote verifies (fabricated or missing quote)"
    elif len(readers) == 2:
        reason = "readers disagree"
    else:
        reason = f"only {len(readers)} usable reader(s)"

    cands: dict[str | None, Candidate] = {}
    for e in readers + list(extra or []):
        if e.source in ("rules", "edgartools") and e.normalized is None:
            continue  # a rule that found nothing is silence, not a "not stated" vote
        c = cands.setdefault(e.normalized, Candidate(e.normalized, e.value))
        c.sources.append(e.source)
        c.evidence.append(e)
    ordered = sorted(cands.values(), key=lambda c: (c.normalized is None, -len(c.sources)))
    return FieldComparison(spec, "disputed", None, None, ordered, reason)


def skip_second_reader_would_be_safe(critical_keys, m1_fields: dict[str, Evidence],
                                     rule_fields: dict[str, list[Evidence]],
                                     comparisons: dict[str, FieldComparison]) -> bool | None:
    """MEASURED, never acted on in B1. The skip condition: for every critical
    field Model 1 answered, rules or EdgarTools independently gave the same
    normalised value AND Model 1's quote verifies. When the condition holds,
    skipping would have been SAFE iff Model 2 then changed nothing (every
    critical field was a verified agreement or an agreed null). Returns None
    when the condition does not hold (skipping would not have happened)."""
    for key in critical_keys:
        e1 = m1_fields.get(key)
        if e1 is None:
            return None
        if e1.normalized is None:
            continue
        independent = rule_fields.get(key) or []
        if not any(r.normalized == e1.normalized for r in independent) or not e1.supports:
            return None
    return all(comparisons[k].outcome in ("verified_agreement", "agreed_null")
               for k in critical_keys if k in comparisons)
