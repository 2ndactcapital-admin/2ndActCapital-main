"""Cascade step (a): RULES for labeled fields. Free, exact, and validated.

WHERE THE LABELS COME FROM (notefields.structural)
──────────────────────────────────────────────────────────────────────────────
Labeled lines ("Pricing Date: March 27, 2026", "CUSIP No.: 06745QXX1") are found
with the GENERATED per-bank label dictionary (label_dictionary_v1.json, built
from inventory run aa1c8c7c by scripts/generate_note_label_dictionary.py), then
the registry's own synonyms for the field. Labels are tried most-used first (by
how many banks print them), so a generic label only answers when no common one
does. Every hit records which label matched, where it came from, and which
banks use it (``detail``).

What is still pattern code here is STRUCTURE, not vocabulary: the "Per Note"
pricing table (price = fees + proceeds), the estimated-value / fee-based /
commission sentences, CUSIP and ISIN check digits.

Every pattern anchors on a label or sentence — never on a bare shape. A bare
9-character token is not a CUSIP: it must sit beside its label AND pass the
check digit. A token that fails is reported in ``rejected``, not used.

RANGES (decision C): "up to" / "as low as" / "not less than" / "between ... and"
are parsed into {min, max, bound}; the hit's quote runs from the label or
sentence start through the value, so the bound wording is kept as evidence.

Each hit carries its quote — the exact span — so a rules reading is
provenance-complete like any model reading. Rules never guess: an ambiguous
statement yields nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from services.edgar_pipeline import is_valid_cusip
from services.note_extraction import label_dictionary as ld
from services.note_extraction.schema import dates_in

_BULLET = r"^[ \t]*(?:[•■▪\-\*]\s*)?"
_TAIL = r"s?\s*(?:\(s\))?\s*(?:\(\d\)\s*)*\**\s*:?\s*\**[ \t]*"


@dataclass
class RuleHit:
    field_key: str
    value: object
    text_start: int
    text_end: int
    quote: str
    detail: dict = field(default_factory=dict)


@dataclass
class RuleResult:
    hits: dict[str, RuleHit] = field(default_factory=dict)
    rejected: list[dict] = field(default_factory=list)
    participant_names: list[dict] = field(default_factory=list)
    # Not fields: facts the self-checks need (decision A's stated initial
    # valuation date; the filing's hypothetical example rows).
    extras: dict[str, RuleHit] = field(default_factory=dict)

    def add(self, hit: RuleHit | None) -> None:
        if hit is not None and hit.field_key not in self.hits:
            self.hits[hit.field_key] = hit


def _hit(text: str, field_key: str, value, start: int, end: int, **detail) -> RuleHit:
    return RuleHit(field_key, value, start, end, text[start:end], detail)


def _dec(s: str | None) -> Decimal | None:
    if not s:
        return None
    try:
        return Decimal(str(s).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None


def _num(d: Decimal | None):
    if d is None:
        return None
    q = d.quantize(Decimal("0.0001")).normalize()
    f = float(q)
    return int(f) if f.is_integer() else f


_pct_value = _num


# ── Ranges (decision C) ──────────────────────────────────────────────────────
_AMT = r"\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?%?"
_BOUND_PHRASES = (
    ("up_to", r"up\s+to|not\s+more\s+than|no\s+more\s+than|at\s+most|a\s+maximum\s+of|maximum\s+of"),
    ("as_low_as", r"as\s+low\s+as|as\s+little\s+as"),
    ("not_less_than", r"not\s+less\s+than|no\s+less\s+than|at\s+least|a\s+minimum\s+of|minimum\s+of"),
)
_BETWEEN = re.compile(r"\b(?:between|from)\s+(" + _AMT + r")\s+(?:and|to)\s+(" + _AMT + r")", re.IGNORECASE)
_BOUNDED = re.compile(r"\b(?P<phrase>" + "|".join(p for _, p in _BOUND_PHRASES) + r")\s+(?:approximately\s+)?"
                      r"(?P<amt>" + _AMT + r")", re.IGNORECASE)
_PLAIN = re.compile(r"(?<![\w.])(?:approximately\s+)?(?P<amt>\$\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
                    r"|(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?%)", re.IGNORECASE)


@dataclass
class RangeParse:
    min: Decimal | None
    max: Decimal | None
    bound: str                       # schema.RANGE_BOUNDS
    start: int                       # span of the bound wording + amount(s), within the parsed text
    end: int
    is_pct: bool

    def as_value(self, scale: Decimal = Decimal(1)) -> dict:
        return {"min": _num(self.min * scale) if self.min is not None else None,
                "max": _num(self.max * scale) if self.max is not None else None,
                "bound": self.bound}


def _amount(s: str) -> tuple[Decimal | None, bool]:
    return _dec(s.replace("%", "")), "%" in s


def parse_range(text: str) -> RangeParse | None:
    """The FIRST amount statement in ``text`` as a range. "up to 2.50%" ->
    (None, 2.50); "as low as $977.50" / "not less than $977.50" -> (977.50,
    None); "between $976.25 and $1,000" -> (976.25, 1000); "$977.50" alone ->
    (977.50, 977.50)."""
    cands = []
    m = _BETWEEN.search(text or "")
    if m:
        (lo, lp), (hi, hp) = _amount(m.group(1)), _amount(m.group(4))
        if lo is not None and hi is not None and lo <= hi:
            cands.append(RangeParse(lo, hi, "between", m.start(), m.end(), lp or hp))
    m = _BOUNDED.search(text or "")
    if m:
        amt, pct = _amount(m.group("amt"))
        phrase = re.sub(r"\s+", " ", m.group("phrase").lower())
        bound = next(b for b, p in _BOUND_PHRASES if re.fullmatch(p, phrase))
        if amt is not None:
            lo, hi = (None, amt) if bound == "up_to" else (amt, None)
            cands.append(RangeParse(lo, hi, bound, m.start(), m.end(), pct))
    m = _PLAIN.search(text or "")
    if m:
        amt, pct = _amount(m.group("amt"))
        if amt is not None:
            bound = "approximately" if m.group(0).lower().startswith("approx") else "exact"
            cands.append(RangeParse(amt, amt, bound, m.start(), m.end(), pct))
    if not cands:
        return None
    # the earliest statement wins; at the same start the bounded reading wins
    # over the bare amount it contains
    cands.sort(key=lambda c: (c.start, c.bound in ("exact", "approximately")))
    return cands[0]


# ── Label -> value on the same line or the next non-empty line ───────────────
def label_value(text: str, label: str, *, max_lines: int = 2):
    """Yield (value_text, span_start, span_end) for each line starting with
    ``label`` (a regex). The span runs from the label through the value."""
    rx = re.compile(_BULLET + r"(" + label + r")" + _TAIL, re.IGNORECASE | re.MULTILINE)
    for m in rx.finditer(text):
        line_end = text.find("\n", m.end())
        line_end = len(text) if line_end == -1 else line_end
        rest = text[m.end():line_end].strip()
        if rest:
            yield rest, m.start(1), line_end
            continue
        pos = line_end
        for _ in range(max_lines):
            nxt = text.find("\n", pos + 1)
            nxt = len(text) if nxt == -1 else nxt
            candidate = text[pos + 1:nxt].strip()
            if candidate:
                yield candidate, m.start(1), nxt
                break
            pos = nxt
            if pos >= len(text):
                break


def label_regex(label: str) -> str:
    """A printed label as a whitespace-tolerant regex. A one-word label must be
    followed by a colon (so 'Form' never matches 'Form 424B2')."""
    words = [re.escape(w) for w in label.split()]
    rx = r"\s+".join(words)
    if len(words) == 1:
        rx += r"(?=\s*\**\s*:)"
    return rx


@dataclass(frozen=True)
class LabelChoice:
    label: str
    source: str                # 'dictionary' | 'synonym'
    issuers: tuple[str, ...]


def labels_for(field_key: str, *, synonyms=(), dictionary: dict | None = None,
               retired: bool = False) -> list[LabelChoice]:
    """The generated dictionary's labels for ``field_key`` (most-used first),
    then the registry's synonyms not already covered."""
    d = dictionary if dictionary is not None else ld.load_dictionary()
    bucket = ((d.get("retired" if retired else "fields") or {}).get(field_key) or {})
    by = bucket.get("by_issuer") or {}
    out: list[LabelChoice] = []
    seen: set[str] = set()
    for lab in bucket.get("labels") or []:
        users = tuple(sorted(i for i, ls in by.items() if any(x.lower() == lab.lower() for x in ls)))
        out.append(LabelChoice(lab, "dictionary", users))
        seen.add(lab.lower())
    out.sort(key=lambda c: (-len(c.issuers), c.label.lower()))
    for syn in synonyms or ():
        if syn.lower() not in seen and len(syn) >= 4:
            out.append(LabelChoice(syn, "synonym", ()))
            seen.add(syn.lower())
    return out


def _labeled(text: str, field_key: str, parse, *, synonyms=(), dictionary=None, retired=False):
    """The first (label, value_text, start, end, parsed) whose value parses."""
    for choice in labels_for(field_key, synonyms=synonyms, dictionary=dictionary, retired=retired):
        for value_text, start, end in label_value(text, label_regex(choice.label)):
            parsed = parse(value_text)
            if parsed is not None:
                return choice, value_text, start, end, parsed
    return None


def _detail(choice: LabelChoice) -> dict:
    return {"label": choice.label, "label_source": choice.source, "label_issuers": list(choice.issuers)}


# ── Value parsers ────────────────────────────────────────────────────────────
def parse_one_date(v: str):
    found = sorted(dates_in(v[:160]))
    return found[0] if len(found) == 1 else None


def _isin_valid(isin: str) -> bool:
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10 == int(isin[-1])


_ISIN_TOKEN = re.compile(r"(?<![0-9A-Z])([A-Z]{2}[A-Z0-9]{9}[0-9])(?![0-9A-Z])")


def parse_money(v: str):
    m = re.match(r"\s*(?:US)?\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)", v)
    return _dec(m.group(1)) if m else None


def parse_pct_or_per_unit(v: str):
    m = re.match(r"\s*(\d{1,3}(?:\.\d+)?)\s?%", v)
    if m:
        return _dec(m.group(1)), "pct"
    m = re.match(r"\s*\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\s+per\s+\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)))?", v)
    if m:
        amt, den = _dec(m.group(1)), _dec(m.group(2)) if m.group(2) else None
        return (amt, den) if amt is not None else None
    return None


def parse_text(v: str):
    v = re.sub(r"\s+", " ", v).strip()
    return v[:300] if len(v) >= 2 else None


# ── CUSIP / ISIN ─────────────────────────────────────────────────────────────
_CUSIP_LABEL = re.compile(r"\bCUSIP\b(?:\s*(?:No\.?|Number|/\s*ISIN))?\s*:?", re.IGNORECASE)
_CUSIP_TOKEN = re.compile(r"(?<![0-9A-Z])([0-9][0-9A-Z]{7}[0-9])(?![0-9A-Z])")
_CUSIP_WINDOW = 80


def find_cusip(text: str, result: RuleResult, dictionary=None) -> None:
    for m in _CUSIP_LABEL.finditer(text):
        window = text[m.end(): m.end() + _CUSIP_WINDOW]
        for t in _CUSIP_TOKEN.finditer(window):
            token = t.group(1)
            if is_valid_cusip(token):
                end = m.end() + t.end()
                label = re.sub(r"\s+", " ", m.group(0)).rstrip(": ")
                issuers = ld.issuers_using("cusip", label, dictionary)
                result.add(_hit(text, "cusip", token, m.start(), end, label=label,
                                label_source="dictionary" if issuers else "pattern", label_issuers=issuers))
                return
            result.rejected.append({"field_key": "cusip", "candidate": token,
                                    "reason": "fails the CUSIP check digit"})


def find_isin(text: str, result: RuleResult, synonyms=(), dictionary=None) -> None:
    def parse(v):
        for t in _ISIN_TOKEN.finditer(v[:120]):
            if _isin_valid(t.group(1)):
                return t.group(1)
            result.rejected.append({"field_key": "isin", "candidate": t.group(1),
                                    "reason": "fails the ISIN check digit"})
        return None
    r = _labeled(text, "isin", parse, synonyms=synonyms, dictionary=dictionary)
    if r:
        choice, _v, s, e, val = r
        result.add(_hit(text, "isin", val, s, e, **_detail(choice)))


# ── Dates ────────────────────────────────────────────────────────────────────
DATE_FIELDS = ("pricing_date", "issue_date", "final_valuation_date", "maturity_date")


def find_dates(text: str, result: RuleResult, synonyms: dict, dictionary=None) -> None:
    for key in DATE_FIELDS:
        r = _labeled(text, key, parse_one_date, synonyms=synonyms.get(key, ()), dictionary=dictionary)
        if r:
            choice, _v, s, e, val = r
            result.add(_hit(text, key, val, s, e, **_detail(choice)))
    # Decision A: a stated initial valuation date is not a field — it is kept
    # for the self-check that compares it with the pricing date.
    r = _labeled(text, "initial_valuation_date", parse_one_date, dictionary=dictionary, retired=True)
    if r:
        choice, _v, s, e, val = r
        result.extras["initial_valuation_date"] = _hit(text, "initial_valuation_date", val, s, e,
                                                       **_detail(choice))


# ── Amount fields stated on a labeled line ───────────────────────────────────
def find_issue_size(text: str, result: RuleResult, synonyms=(), dictionary=None,
                    denomination: Decimal | None = None) -> None:
    floor = max(Decimal(10000), (denomination or 0) * 10)

    def parse(v):
        d = parse_money(v)
        return d if d is not None and d >= floor else None
    r = _labeled(text, "issue_size", parse, synonyms=synonyms, dictionary=dictionary)
    if r:
        choice, _v, s, e, val = r
        result.add(_hit(text, "issue_size", _num(val), s, e, **_detail(choice)))


_DENOM = re.compile(r"(?:minimum\s+)?denominations?\s+of\s+\$\s?(?P<amt>[\d,]+(?:\.\d+)?)", re.IGNORECASE)


def find_denomination(text: str, result: RuleResult, synonyms=(), dictionary=None) -> Decimal | None:
    def parse(v):
        d = parse_money(v)
        return d if d is not None and Decimal(0) < d <= Decimal(100000) else None
    r = _labeled(text, "denomination", parse, synonyms=synonyms, dictionary=dictionary)
    if r:
        choice, _v, s, e, val = r
        result.add(_hit(text, "denomination", _num(val), s, e, **_detail(choice)))
        return val
    m = _DENOM.search(text)
    if not m:
        return None
    d = _dec(m.group("amt"))
    if d is None or d <= 0:
        return None
    result.add(_hit(text, "denomination", _num(d), m.start(), m.end(), label="denominations of",
                    label_source="pattern", label_issuers=[]))
    return d


def _unit_principal(price: Decimal, denomination: Decimal | None) -> Decimal | None:
    """The per-unit principal a '$' amount is quoted against. A row is per
    NOTE, which is not the minimum denomination ($1,000 notes sold in minimum
    lots of $10,000), so a standard unit size within 10% of the price wins."""
    for unit in (Decimal(1000), Decimal(100), Decimal(25), Decimal(10)):
        if unit * Decimal("0.9") <= price <= unit * Decimal("1.1"):
            return unit
    return denomination


def find_price_to_public(text: str, result: RuleResult, synonyms=(), dictionary=None,
                         denomination: Decimal | None = None) -> None:
    if "price_to_public_pct" in result.hits:
        return

    def parse(v):
        p = parse_pct_or_per_unit(v)
        if p is None:
            return None
        amt, kind = p
        if kind == "pct":
            pct = amt
        else:
            base = kind or _unit_principal(amt, denomination)
            if not base:
                return None
            pct = amt / base * 100
        return pct if Decimal(50) <= pct <= Decimal(110) else None
    r = _labeled(text, "price_to_public_pct", parse, synonyms=synonyms, dictionary=dictionary)
    if r:
        choice, _v, s, e, val = r
        result.add(_hit(text, "price_to_public_pct", _num(val), s, e, **_detail(choice)))


# ── Price to public / fees / proceeds table ("Per Note" row) ─────────────────
_PER_UNIT = re.compile(r"^[ \t]*Per\s+(?:Note|Security|Unit|Certificate|\$1,000)\b.*$",
                       re.IGNORECASE | re.MULTILINE)
_AMOUNT = re.compile(r"^\s*(?:\$\s?(?P<d>[\d,]+(?:\.\d+)?)|(?P<p>\d{1,3}(?:\.\d+)?)\s?%)\s*(?:\(\d\))?\s*$")
_INLINE_AMOUNTS = re.compile(r"(?:\$\s?([\d,]+(?:\.\d+)?)|(\d{1,3}(?:\.\d+)?)\s?%)")


def _amounts_after(text: str, start: int, limit: int = 12):
    line_end = text.find("\n", start)
    line_end = len(text) if line_end == -1 else line_end
    same_line = text[start:line_end]
    inline = [(m.group(1), m.group(2)) for m in _INLINE_AMOUNTS.finditer(same_line)]
    if len(inline) >= 3:
        vals = [(_dec(d) if d else _dec(p), p is not None and d is None) for d, p in inline[:5]]
        return vals, line_end
    vals, pos, end = [], line_end, line_end
    for _ in range(limit):
        nxt = text.find("\n", pos + 1)
        nxt = len(text) if nxt == -1 else nxt
        line = text[pos + 1:nxt]
        pos = nxt
        if not line.strip():
            continue
        m = _AMOUNT.match(line)
        if not m:
            break
        vals.append((_dec(m.group("d")) if m.group("d") else _dec(m.group("p")), m.group("p") is not None))
        end = nxt
        if len(vals) == 5:
            break
    return vals, end


def _consistent_triple(vals):
    """The first (price, fees, proceeds) run where price = fees + proceeds,
    preferring an all-percent run."""
    runs = [vals[i:i + 3] for i in range(len(vals) - 2)]
    ok = [r for r in runs if all(v is not None for v, _ in r)
          and abs(r[0][0] - (r[1][0] + r[2][0])) <= Decimal("0.011")]
    pct = [r for r in ok if all(p for _, p in r)]
    return (pct or ok or [None])[0]


def find_pricing_table(text: str, result: RuleResult, denomination: Decimal | None) -> None:
    for m in _PER_UNIT.finditer(text):
        lab = re.match(r"[ \t]*Per\s+(?:Note|Security|Unit|Certificate|\$1,000)", m.group(0), re.IGNORECASE)
        vals, end = _amounts_after(text, m.start() + lab.end())
        triple = _consistent_triple(vals)
        if triple is None:
            continue
        (a, a_pct), (b, b_pct), (c, c_pct) = triple
        if a_pct and b_pct and c_pct:
            price, fee, proceeds = a, b, c
        else:
            base = _unit_principal(a, denomination)
            if not base:
                continue
            price, fee, proceeds = a / base * 100, b / base * 100, c / base * 100
        det = {"label": "Per Note", "label_source": "pattern", "table": "price = fees + proceeds"}
        result.add(_hit(text, "price_to_public_pct", _num(price), m.start(), end, **det))
        result.add(_hit(text, "total_fees_pct", _num(fee), m.start(), end, **det))
        result.add(_hit(text, "proceeds_to_issuer_pct", _num(proceeds), m.start(), end, **det))
        return


# ── Estimated value (decision B: whichever unit is STATED is extracted) ──────
_EV = re.compile(
    r"(?:estimated\s+value|estimated\s+initial\s+value|initial\s+estimated\s+value)"
    r"(?:\s+of\s+(?:the|your|each)\s+(?:notes?|securities|security))?"
    r"[^.]{0,250}?(?:is|was|will\s+be|of|equals?|:)\s+",
    re.IGNORECASE,
)
_PER = re.compile(r"\s*per\s+(?:\$\s?(?P<den>[\d,]+)|(?:note|security|unit))", re.IGNORECASE)


def find_estimated_value(text: str, result: RuleResult, denomination: Decimal | None = None,
                         dictionary=None) -> None:
    for m in _EV.finditer(text):
        tail = text[m.end(): m.end() + 160]
        rp = parse_range(tail)
        if rp is None or rp.start > 40:
            continue
        end = m.end() + rp.end
        det = {"label": re.sub(r"\s+", " ", m.group(0)).strip()[:80], "label_source": "pattern",
               "bound": rp.bound}
        if rp.is_pct:
            bounds = [v for v in (rp.min, rp.max) if v is not None]
            if all(Decimal(50) <= v <= Decimal(110) for v in bounds):
                result.add(_hit(text, "estimated_value_pct", rp.as_value(), m.start(), end, **det))
                return
            continue
        per = _PER.match(tail[rp.end:])
        den = _dec(per.group("den")) if per and per.group("den") else None
        bounds = [v for v in (rp.min, rp.max) if v is not None]
        if den is None:
            den = _unit_principal(bounds[0], denomination) if bounds else None
        if not den:
            continue
        scale = Decimal(1000) / den
        if per:
            end = m.end() + rp.end + per.end()
        if all(Decimal(500) <= v * scale <= Decimal(1100) for v in bounds):
            if scale != 1:
                det["scaled_from_per"] = _num(den)
            result.add(_hit(text, "estimated_value_per_1000", rp.as_value(scale), m.start(), end, **det))
            return


# ── Agent commission (a range: "up to") ──────────────────────────────────────
_COMMISSION = re.compile(
    r"(?:(?:agent'?s?\s+|selling\s+|underwriting\s+)?commissions?|underwriting\s+discounts?)\b"
    r"[^.]{0,160}?(?=(?:up\s+to|not\s+more\s+than|as\s+low\s+as|between|of|equal\s+to|\$|\d))",
    re.IGNORECASE,
)


def find_commission(text: str, result: RuleResult) -> None:
    for m in _COMMISSION.finditer(text):
        tail = text[m.end(): m.end() + 120]
        rp = parse_range(tail)
        if rp is None or rp.start > 25:
            continue
        end = m.end() + rp.end
        det = {"label": re.sub(r"\s+", " ", m.group(0)).strip()[:80], "label_source": "pattern",
               "bound": rp.bound}
        if rp.is_pct:
            value = rp.as_value()
        else:
            per = re.match(r"\s*per\s+\$\s?([\d,]+)", tail[rp.end:], re.IGNORECASE)
            if not per:
                continue
            den = _dec(per.group(1))
            end += per.end()
            value = rp.as_value(Decimal(100) / den)
        bounds = [v for v in (value["min"], value["max"]) if v is not None]
        if bounds and all(0 <= v <= 10 for v in bounds):
            result.add(_hit(text, "agent_commission_pct", value, m.start(), end, **det))
            return


# ── Fee-based (advisory) account price (a range, per $1,000) ─────────────────
_FEE_BASED = re.compile(
    r"(?:fee[- ]based|advisory|wrap|fiduciary)\s+(?:advisory\s+)?(?:or\s+trust\s+)?(?:accounts?|programs?)"
    r"[^.]{0,220}?(?:price|pay)[^.$\d]{0,120}?",
    re.IGNORECASE,
)
_FEE_BASED_PRICE_FIRST = re.compile(
    r"(?:price|purchase\s+price|offering\s+price)[^.]{0,200}?(?:fee[- ]based|advisory|wrap|fiduciary)\s+"
    r"(?:advisory\s+)?(?:or\s+trust\s+)?(?:accounts?|programs?)[^.$\d]{0,80}?",
    re.IGNORECASE,
)


def find_fee_based_price(text: str, result: RuleResult, denomination: Decimal | None) -> None:
    matches = sorted(list(_FEE_BASED.finditer(text)) + list(_FEE_BASED_PRICE_FIRST.finditer(text)),
                     key=lambda m: m.start())
    for m in matches:
        tail = text[m.end(): m.end() + 160]
        rp = parse_range(tail)
        if rp is None or rp.start > 40:
            continue
        end = m.end() + rp.end
        det = {"label": re.sub(r"\s+", " ", m.group(0)).strip()[:80], "label_source": "pattern",
               "bound": rp.bound}
        bounds = [v for v in (rp.min, rp.max) if v is not None]
        if rp.is_pct:
            scale = Decimal(10)
        else:
            per = _PER.match(tail[rp.end:])
            den = _dec(per.group("den")) if per and per.group("den") else _unit_principal(bounds[0], denomination)
            if not den:
                continue
            if per:
                end += per.end()
            scale = Decimal(1000) / den
        if all(Decimal(800) <= v * scale <= Decimal(1050) for v in bounds):
            if rp.is_pct:
                det["converted_from"] = "pct"
            result.add(_hit(text, "fee_based_account_price", rp.as_value(scale), m.start(), end, **det))
            return


# ── Section 9: later, rules only ─────────────────────────────────────────────
_REG_NO = re.compile(r"\b(333-\d{5,6}(?:-\d{1,3})?)\b")
_RULE_NO = re.compile(r"(424\s?\(b\)\s?\(\d\)|433|\d{3}\(\w\))", re.IGNORECASE)
_SETTLE = re.compile(r"\bT\s?\+\s?(\d{1,2})\b")
_BIZDAY = re.compile(r"\b((?:modified\s+)?(?:following|preceding))\s+business\s+day(?:\s+convention)?\b", re.I)


def _inline(text: str, field_key: str, value_rx: re.Pattern, *, synonyms=(), dictionary=None,
            window: int = 80, want_date: bool = False):
    """A label anywhere in a line ("(To Prospectus dated December 20, 2023)")
    followed within ``window`` characters by its value."""
    for choice in labels_for(field_key, synonyms=synonyms, dictionary=dictionary):
        for m in re.finditer(label_regex(choice.label).replace(r"(?=\s*\**\s*:)", ""), text, re.IGNORECASE):
            seg = text[m.end(): m.end() + window]
            if want_date:
                d = parse_one_date(seg[:40])
                if d:
                    dm = next(iter(re.finditer(r"\d{4}", seg[:40])), None)
                    end = m.end() + (dm.end() if dm else len(seg[:40]))
                    return choice, d, m.start(), end
                continue
            v = value_rx.search(seg)
            if v:
                return choice, re.sub(r"\s+", "", v.group(1)), m.start(), m.end() + v.end()
    return None


def _sentence_with(text: str, field_key: str, *, synonyms=(), dictionary=None):
    """The first sentence containing one of the field's labels -> the sentence."""
    for choice in labels_for(field_key, synonyms=synonyms, dictionary=dictionary):
        m = re.search(r"\s+".join(re.escape(w) for w in choice.label.split()), text, re.IGNORECASE)
        if not m:
            continue
        s = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        e_dot = text.find(".", m.end())
        e = len(text) if e_dot == -1 else e_dot + 1
        sentence = re.sub(r"\s+", " ", text[s:e]).strip()
        if 4 <= len(sentence) <= 400:
            lead = len(text[s:e]) - len(text[s:e].lstrip())
            return choice, sentence, s + lead, e
    return None


def find_rules_only(text: str, result: RuleResult, synonyms: dict, dictionary=None) -> None:
    syn = lambda k: synonyms.get(k, ())  # noqa: E731
    r = _inline(text, "registration_number", _REG_NO, synonyms=syn("registration_number"), dictionary=dictionary)
    if r:
        choice, v, s, e = r
        result.add(_hit(text, "registration_number", v, s, e, **_detail(choice)))
    for key in ("prospectus_supplement_date", "prospectus_date"):
        r = _inline(text, key, _REG_NO, synonyms=syn(key), dictionary=dictionary, want_date=True)
        if r:
            choice, v, s, e = r
            result.add(_hit(text, key, v, s, e, **_detail(choice)))
    r = _inline(text, "filed_pursuant_to_rule", _RULE_NO, synonyms=syn("filed_pursuant_to_rule"),
                dictionary=dictionary, window=30)
    if r:
        choice, v, s, e = r
        result.add(_hit(text, "filed_pursuant_to_rule", v, s, e, **_detail(choice)))
    for key in ("listing_status", "form_of_notes"):
        r = _labeled(text, key, parse_text, synonyms=syn(key), dictionary=dictionary)
        if r:
            choice, v, s, e, val = r
            result.add(_hit(text, key, val, s, e, **_detail(choice)))
        elif key == "listing_status":
            r2 = _sentence_with(text, key, synonyms=syn(key), dictionary=dictionary)
            if r2:
                choice, sentence, s, e = r2
                result.add(_hit(text, key, sentence, s, e, **_detail(choice)))
    r = _sentence_with(text, "credit_risk_statement", synonyms=syn("credit_risk_statement"), dictionary=dictionary)
    if r:
        choice, sentence, s, e = r
        result.add(_hit(text, "credit_risk_statement", sentence, s, e, **_detail(choice)))
    m = _BIZDAY.search(text)
    if m:
        result.add(_hit(text, "business_day_convention", re.sub(r"\s+", " ", m.group(0)).lower(),
                        m.start(), m.end(), label="business day", label_source="pattern", label_issuers=[]))
    for m in _SETTLE.finditer(text):
        ctx = text[max(0, m.start() - 120): m.start()].lower()
        if "settle" in ctx or "settlement" in ctx:
            result.add(_hit(text, "settlement_lag", f"T+{m.group(1)}", m.start(), m.end(),
                            label="T+", label_source="pattern", label_issuers=[]))
            break


# ── Hypothetical example table (for the payoff self-check) ───────────────────
_HYPO_HEAD = re.compile(r"hypothetical", re.IGNORECASE)
_ROW_PCT = re.compile(r"[-−–]?\s?\d{1,3}(?:\.\d+)?\s?%")
_ROW_USD = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)")


def _signed_pct(s: str) -> Decimal | None:
    neg = s.strip().startswith(("-", "−", "–"))
    d = _dec(re.sub(r"[^\d.]", "", s))
    return None if d is None else (-d if neg else d)


def find_hypothetical_rows(text: str, result: RuleResult, *, max_rows: int = 40) -> None:
    """Rows of the filing's hypothetical payment table: (underlying return %,
    payment per $1,000). A row is a line whose FIRST token is a percent and that
    carries a $ amount; it is taken only from text after a 'hypothetical'
    heading. Stored as an extra — never a field."""
    rows = []
    for h in _HYPO_HEAD.finditer(text):
        window = text[h.start(): h.start() + 6000]
        for line in window.split("\n"):
            line = line.strip()
            pm = _ROW_PCT.match(line)
            if not pm:
                continue
            usd = _ROW_USD.search(line[pm.end():])
            if not usd:
                continue
            r, pay = _signed_pct(pm.group(0)), _dec(usd.group(1))
            if r is None or pay is None:
                continue
            rows.append({"underlying_return_pct": _num(r), "payment_per_1000": _num(pay), "line": line[:200]})
            if len(rows) >= max_rows:
                break
        if rows:
            start = h.start()
            result.extras["hypothetical_rows"] = RuleHit("hypothetical_rows", rows, start,
                                                         start + len(window), window[:300])
            return


# ── Distribution participant NAMES (plan of distribution only) ───────────────
_ENTITY = re.compile(
    r"(?<![\w.])((?:(?:[A-Z]|i[A-Z])[\w&'.\-]*[ \t]+){1,6}?)(?:,[ \t])?"
    r"(LLC|L\.L\.C\.|Inc\.|Incorporated|Corporation|Corp\.|L\.P\.|LP|Ltd\.|Limited|plc|PLC|AG|N\.A\.|& Co\.)"
    r"(?![\w])"
)
_LEADING_NOISE = re.compile(
    r"^(?:Each|The|Our|Its|In|Under|See|As|If|We|Any|This|That|These|Such|Neither|Either|Both|"
    r"Accordingly|Additionally|Also|When|Where|Upon|For|From|With|To|By|Of|And|Or|Affiliates?)\s+"
)
_SENTENCE_BREAK = re.compile(r"(?<=[a-z]{3})\.\s+")


def find_participant_names(text: str, spans: list[tuple[int, int]]) -> list[dict]:
    """Every entity name stated inside the plan-of-distribution spans, with its
    first quote and how often it appears. Matching to the participants table
    is participants.match_names' job; this never drops a name."""
    seen: dict[str, dict] = {}
    for s, e in spans:
        chunk = text[s:e]
        for m in _ENTITY.finditer(chunk):
            name = (m.group(1).strip() + (", " if ", " + m.group(2) in m.group(0) else " ") + m.group(2)).strip()
            name = re.sub(r"\s+", " ", name)
            name = _SENTENCE_BREAK.split(name)[-1]
            while _LEADING_NOISE.match(name):
                name = _LEADING_NOISE.sub("", name, count=1)
            if len(name) < 5:
                continue
            key = name.lower()
            if key in seen:
                seen[key]["count"] += 1
                continue
            start = s + m.start()
            seen[key] = {"name": name, "count": 1, "text_start": start, "text_end": s + m.end(),
                         "quote": text[max(s, start - 60): min(e, s + m.end() + 80)]}
    return list(seen.values())


# ── Orchestration ────────────────────────────────────────────────────────────
def synonyms_by_key(specs) -> dict[str, tuple[str, ...]]:
    return {s.key: tuple(s.synonyms) for s in (specs or [])}


def run_rules(text: str, *, distribution_spans: list[tuple[int, int]] | None = None,
              specs=None, dictionary: dict | None = None) -> RuleResult:
    """``specs`` supplies the registry synonyms (a fallback after the generated
    dictionary); ``dictionary`` defaults to label_dictionary_v1.json."""
    syn = synonyms_by_key(specs)
    d = dictionary if dictionary is not None else ld.load_dictionary()
    result = RuleResult()
    find_cusip(text, result, d)
    find_isin(text, result, syn.get("isin", ()), d)
    find_dates(text, result, syn, d)
    denomination = find_denomination(text, result, syn.get("denomination", ()), d)
    find_issue_size(text, result, syn.get("issue_size", ()), d, denomination)
    find_estimated_value(text, result, denomination, d)
    find_pricing_table(text, result, denomination)
    find_price_to_public(text, result, syn.get("price_to_public_pct", ()), d, denomination)
    find_commission(text, result)
    find_fee_based_price(text, result, denomination)
    find_rules_only(text, result, syn, d)
    find_hypothetical_rows(text, result)
    if distribution_spans:
        result.participant_names = find_participant_names(text, distribution_spans)
    return result
