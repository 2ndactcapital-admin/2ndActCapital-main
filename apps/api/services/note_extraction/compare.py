"""Cascade step (e): COMPARE IN CODE.

A field is VERIFIED at no further cost when both readers returned it, their
normalised values agree, AND at least one of their quotes is found verbatim in
the filing AND (where the check applies) the value appears in that quote.
Agreement on "absent" (both null) is ``agreed_null``. Anything else — a
disagreement, an agreement whose quote cannot be found (a fabricated quote is
rejected here), or only one usable reader — is DISPUTED and carries its
distinct candidate values onward to Jev. Rules and EdgarTools readings join a
disputed field's candidates; they never overrule two agreeing, verified readers.
Two readers agreeing a field is ABSENT while a rule quotes a verified value is a
dispute too (notefields): the labeled-field rules are never silently outvoted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.note_extraction import rules
from services.note_extraction.documents import FilingDocument, QuoteLocation, locate_quote
from services.note_extraction.schema import KIND_LIST, FieldSpec, normalize, value_in_quote


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
    dropped: list[dict] = field(default_factory=list)   # notefix2 Task 3: hygiene-dropped list members

    @property
    def supports(self) -> bool:
        """The quote is real and (where checkable) contains the value."""
        return bool(self.quote_verified) and self.value_in_quote is not False


_MATURITY_LIKE_FIELDS = ("maturity_date", "final_valuation_date")
# notefix2 Task 5: fields a hypothetical-example section's own numbers (a
# placeholder coupon, a round placeholder initial level, a worked-example
# date) can masquerade as a real extracted value for. ``underlyings`` is
# handled separately below — the LIST itself is still real, only a member's
# ``initial_level`` is suspect when the field's quote comes from inside the
# section.
_HYPOTHETICAL_REJECT_FIELDS = ("coupon_rate_pa", "pricing_date", "issue_date", "maturity_date",
                               "barrier_pct", "buffer_pct")


def _within(loc: QuoteLocation | None, span: tuple[int, int] | None) -> bool:
    return bool(loc and span and span[0] <= loc.text_start and loc.text_end <= span[1])


def evidence(doc: FilingDocument, spec: FieldSpec, source: str, value, quote, *,
            reject_if_equals: str | None = None, hypothetical_span: tuple[int, int] | None = None) -> Evidence:
    """notefix2 Task 3: for ``underlyings`` / ``distribution``, list members
    are hygiene-filtered BEFORE they become this reading's value — a table
    header, a definition sentence, or a merged multi-member entry never
    reaches comparison, agreement or staging. Every source (rules, the
    model readers, EdgarTools, Jev, escalation) calls this ONE function to
    build its evidence, so filtering here covers all of them at a single
    choke point. Dropped members are kept on ``Evidence.dropped`` for the
    run's own report — never silently discarded.

    notefix2 Task 5: ``reject_if_equals`` (an ISO date, normally the rules'
    own independently-read pricing_date) rejects a candidate ``maturity_date``
    / ``final_valuation_date`` that equals it — a filing's trade/pricing
    date echoed into the wrong field is never a real maturity. ``
    hypothetical_span`` rejects a value whose quote lies ENTIRELY inside a
    detected hypothetical-example section for the fields in
    ``_HYPOTHETICAL_REJECT_FIELDS`` (it may still be used by the payoff
    check, which reads the section's own rows directly, not this reading);
    for ``underlyings`` it strips just ``initial_level`` from every member
    instead of rejecting the whole list, since the member names are
    typically still real."""
    dropped: list[dict] = []
    if spec.kind == KIND_LIST and isinstance(value, list):
        value, dropped = rules.filter_list_members(spec.key, value)
        if not value:
            value = None
    loc = locate_quote(doc, quote) if quote else None
    if hypothetical_span and _within(loc, hypothetical_span):
        if spec.key in _HYPOTHETICAL_REJECT_FIELDS and value is not None:
            dropped.append({"value": value, "drop_reason": "hypothetical_section"})
            value = None
        elif spec.key == "underlyings" and isinstance(value, list):
            stripped_any = False
            cleaned = []
            for m in value:
                if isinstance(m, dict) and m.get("initial_level") is not None:
                    dropped.append({"name": m.get("name"), "initial_level": m.get("initial_level"),
                                    "drop_reason": "hypothetical_section"})
                    m = {**m, "initial_level": None}
                    stripped_any = True
                cleaned.append(m)
            if stripped_any:
                value = cleaned
    if (spec.key in _MATURITY_LIKE_FIELDS and reject_if_equals and value is not None
            and normalize(spec, value) == normalize(spec, reject_if_equals)):
        dropped.append({"value": value, "drop_reason": f"equals the pricing date {reject_if_equals} — "
                        "rejected for maturity_date/final_valuation_date"})
        value = None
    norm = normalize(spec, value)
    verified = None if not quote else loc is not None
    in_quote = value_in_quote(spec, value, quote) if (value is not None and quote) else (
        None if value is None else False)
    return Evidence(source, value if norm is not None else None, quote, norm, loc, verified, in_quote,
                    dropped=dropped)


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
            # notefields: a labeled field the RULES found, with a quote that
            # verifies, is not "absent" just because both readers missed it —
            # it is a dispute (the rule's value is a candidate), never a
            # silent agreed-null and never an automatic rules win.
            found = [e for e in (extra or []) if e.normalized is not None and e.supports]
            if not found:
                return FieldComparison(spec, "agreed_null", None, None, [], "both readers: absent")
            reason = "both readers: absent, but an independent source quotes a value"
        else:
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
