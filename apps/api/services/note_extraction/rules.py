"""Cascade step (a): RULES for labeled fields. Free, exact, and validated.

Every pattern anchors on its LABEL ("CUSIP", "Pricing Date", "estimated value
of the notes", "Per Note", "fee-based", "Plan of Distribution") — never on a
bare shape. A bare 9-character token is not a CUSIP: a candidate must sit
beside the label AND pass the check digit (services.edgar_pipeline's own
validator, reused). A token that fails is reported in ``rejected``, not used.

Each hit carries its quote — the exact text span from the label through the
value — so a rules reading is provenance-complete like any model reading.
Rules never guess: an ambiguous or range-valued statement yields nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from services.edgar_pipeline import is_valid_cusip
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

    def add(self, hit: RuleHit | None) -> None:
        if hit is not None and hit.field_key not in self.hits:
            self.hits[hit.field_key] = hit


def _hit(text: str, field_key: str, value, start: int, end: int, **detail) -> RuleHit:
    return RuleHit(field_key, value, start, end, text[start:end], detail)


def _dec(s: str | None) -> Decimal | None:
    if not s:
        return None
    try:
        return Decimal(s.replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        return None


def _pct_value(d: Decimal) -> float:
    q = d.quantize(Decimal("0.0001")).normalize()
    f = float(q)
    return int(f) if f.is_integer() else f


# ── Label -> value on the same line or the next non-empty line ───────────────
def label_value(text: str, label: str, *, max_lines: int = 2):
    """Yield (value_text, span_start, span_end) for each line starting with
    ``label``. The span runs from the label through the value."""
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


# ── CUSIP ─────────────────────────────────────────────────────────────────
_CUSIP_LABEL = re.compile(r"\bCUSIP\b(?:\s*(?:No\.?|Number|/\s*ISIN))?\s*:?", re.IGNORECASE)
_CUSIP_TOKEN = re.compile(r"(?<![0-9A-Z])([0-9][0-9A-Z]{7}[0-9])(?![0-9A-Z])")
_CUSIP_WINDOW = 80


def find_cusip(text: str, result: RuleResult) -> None:
    for m in _CUSIP_LABEL.finditer(text):
        window = text[m.end(): m.end() + _CUSIP_WINDOW]
        for t in _CUSIP_TOKEN.finditer(window):
            token = t.group(1)
            if is_valid_cusip(token):
                end = m.end() + t.end()
                result.add(_hit(text, "cusip", token, m.start(), end))
                return
            result.rejected.append({"field_key": "cusip", "candidate": token,
                                    "reason": "fails the CUSIP check digit"})


# ── Dates ─────────────────────────────────────────────────────────────────
_DATE_LABELS = (
    ("pricing_date", r"(?:pricing|trade)\s+date"),
    ("initial_valuation_date", r"(?:initial\s+valuation|strike)\s+date"),
    ("final_valuation_date",
     r"(?:final\s+valuation|final\s+observation|final\s+determination|determination|valuation)\s+date"),
    ("maturity_date", r"(?:stated\s+)?maturity\s+date"),
)


def find_dates(text: str, result: RuleResult) -> None:
    for key, label in _DATE_LABELS:
        for value_text, start, end in label_value(text, label):
            # Exactly one date in the value; a schedule of several is not one date.
            found = sorted(dates_in(value_text[:160]))
            if len(found) == 1:
                result.add(_hit(text, key, found[0], start, end))
                break


# ── Estimated value ───────────────────────────────────────────────────────
_EV = re.compile(
    r"estimated\s+value\s+of\s+(?:the|your|each)\s+(?:notes?|securities|security)"
    r"[^.]{0,250}?(?:is|was|will\s+be|of|equals?)\s+(?:approximately\s+)?"
    r"(?:\$\s?(?P<amt>[\d,]+(?:\.\d{1,4})?)\s+per\s+(?:\$\s?(?P<den>[\d,]+)|(?P<unit>note|security|unit))"
    r"|(?P<pct>\d{2,3}(?:\.\d{1,4})?)\s?%\s+of\s+the\s+(?:principal|stated|face))",
    re.IGNORECASE,
)
_RANGE = re.compile(r"\bbetween\s+\$?[\d,.]+\s*%?\s+and\s+\$?[\d,.]+", re.IGNORECASE)


def find_estimated_value(text: str, result: RuleResult, denomination: Decimal | None = None) -> None:
    for m in _EV.finditer(text):
        if _RANGE.search(m.group(0)):
            continue  # a range is preliminary — never a value
        if m.group("pct"):
            d = _dec(m.group("pct"))
        else:
            amt = _dec(m.group("amt"))
            den = _dec(m.group("den")) if m.group("den") else denomination
            d = (amt / den * 100) if amt is not None and den else None
        if d is not None and Decimal("50") <= d <= Decimal("110"):
            result.add(_hit(text, "estimated_value_pct", _pct_value(d), m.start(), m.end()))
            return


# ── Price to public / fees / proceeds table ("Per Note" row) ─────────────────
_PER_UNIT = re.compile(r"^[ \t]*Per\s+(?:Note|Security|Unit|Certificate|\$1,000)\b.*$",
                       re.IGNORECASE | re.MULTILINE)
_AMOUNT = re.compile(r"^\s*(?:\$\s?(?P<d>[\d,]+(?:\.\d+)?)|(?P<p>\d{1,3}(?:\.\d+)?)\s?%)\s*(?:\(\d\))?\s*$")
_INLINE_AMOUNTS = re.compile(r"(?:\$\s?([\d,]+(?:\.\d+)?)|(\d{1,3}(?:\.\d+)?)\s?%)")


def _amounts_after(text: str, start: int, limit: int = 12):
    """Up to five amounts following a 'Per Note' label, on its own line or the
    next lines. Returns ([(Decimal, is_pct)], span_end)."""
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
    preferring an all-percent run (a '$1,000' issue-price column often precedes
    the percent columns)."""
    runs = [vals[i:i + 3] for i in range(len(vals) - 2)]
    ok = [r for r in runs if all(v is not None for v, _ in r)
          and abs(r[0][0] - (r[1][0] + r[2][0])) <= Decimal("0.011")]
    pct = [r for r in ok if all(p for _, p in r)]
    return (pct or ok or [None])[0]


def _unit_principal(price: Decimal, denomination: Decimal | None) -> Decimal | None:
    """The per-unit principal a '$' price row is quoted against. The row is per
    NOTE, which is not the minimum denomination ($1,000 notes sold in minimum
    lots of $10,000), so a standard unit size within 10% of the price wins."""
    for unit in (Decimal(1000), Decimal(100), Decimal(25), Decimal(10)):
        if unit * Decimal("0.9") <= price <= unit * Decimal("1.1"):
            return unit
    return denomination


def find_pricing_table(text: str, result: RuleResult, denomination: Decimal | None) -> None:
    for m in _PER_UNIT.finditer(text):
        lab = re.match(r"[ \t]*Per\s+(?:Note|Security|Unit|Certificate|\$1,000)", m.group(0), re.IGNORECASE)
        vals, end = _amounts_after(text, m.start() + lab.end())
        triple = _consistent_triple(vals)
        if triple is None:
            continue
        (a, a_pct), (b, b_pct), _ = triple
        if a_pct and b_pct:
            price_pct, fee_pct = a, b
        else:
            base = _unit_principal(a, denomination)
            if not base:
                continue
            price_pct, fee_pct = a / base * 100, b / base * 100
        result.add(_hit(text, "price_to_public_pct", _pct_value(price_pct), m.start(), end))
        result.add(_hit(text, "total_commissions_fees_pct", _pct_value(fee_pct), m.start(), end))
        return


_COMMISSION = re.compile(
    r"(?:(?:agent'?s?\s+)?commissions?|underwriting\s+discounts?)\b[^.]{0,160}?"
    r"\$\s?(?P<amt>[\d,]+(?:\.\d+)?)\s+per\s+\$\s?(?P<den>[\d,]+)",
    re.IGNORECASE,
)


def find_commission(text: str, result: RuleResult) -> None:
    for m in _COMMISSION.finditer(text):
        amt, den = _dec(m.group("amt")), _dec(m.group("den"))
        if amt is not None and den:
            d = amt / den * 100
            if d <= 10:
                result.add(_hit(text, "agent_commission_pct", _pct_value(d), m.start(), m.end()))
                return


_PRICE_TO_PUBLIC = r"(?:issue\s+)?price\s+to\s+(?:the\s+)?public|original\s+issue\s+price|issue\s+price"


def find_price_to_public(text: str, result: RuleResult, denomination: Decimal | None) -> None:
    if "price_to_public_pct" in result.hits:
        return
    for value_text, start, end in label_value(text, _PRICE_TO_PUBLIC):
        pct = re.match(r"\s*(\d{1,3}(?:\.\d+)?)\s?%", value_text)
        if pct:
            result.add(_hit(text, "price_to_public_pct", _pct_value(_dec(pct.group(1))), start, end))
            return
        amt = re.match(r"\s*\$\s?([\d,]+(?:\.\d+)?)\s+per\s+\$\s?([\d,]+)", value_text)
        if amt:
            a, den = _dec(amt.group(1)), _dec(amt.group(2))
            if a is not None and den:
                result.add(_hit(text, "price_to_public_pct", _pct_value(a / den * 100), start, end))
                return


# ── Fee-based (advisory) account price ────────────────────────────────────
_FEE_BASED = re.compile(
    r"(?:fee[- ]based|advisory|wrap)\s+(?:advisory\s+)?(?:or\s+trust\s+)?(?:accounts?|programs?)"
    r"[^.]{0,220}?(?:price|purchase\s+price|offering\s+price)[^.]{0,120}?"
    r"(?:\$\s?(?P<amt>[\d,]+(?:\.\d+)?)\s+per\s+(?:\$\s?(?P<den>[\d,]+)|(?:note|security|unit))"
    r"|(?P<pct>\d{2,3}(?:\.\d+)?)\s?%)",
    re.IGNORECASE,
)


# The same statement with the price named BEFORE the account type ("the public
# offering price for investors purchasing the notes in fee-based advisory
# accounts will be $980.00 per note").
_FEE_BASED_PRICE_FIRST = re.compile(
    r"(?:price|purchase\s+price|offering\s+price)[^.]{0,200}?(?:fee[- ]based|advisory|wrap)\s+"
    r"(?:advisory\s+)?(?:or\s+trust\s+)?(?:accounts?|programs?)[^.]{0,80}?"
    r"(?:\$\s?(?P<amt>[\d,]+(?:\.\d+)?)\s+per\s+(?:\$\s?(?P<den>[\d,]+)|(?:note|security|unit))"
    r"|(?P<pct>\d{2,3}(?:\.\d+)?)\s?%)",
    re.IGNORECASE,
)


def find_fee_based_price(text: str, result: RuleResult, denomination: Decimal | None) -> None:
    matches = sorted(list(_FEE_BASED.finditer(text)) + list(_FEE_BASED_PRICE_FIRST.finditer(text)),
                     key=lambda m: m.start())
    for m in matches:
        if _RANGE.search(m.group(0)) or re.search(r"\bas\s+low\s+as\b|\bup\s+to\b", m.group(0), re.I):
            continue  # "between $976.25 and $1,000" / "as low as" is a range, not a price
        if m.group("pct"):
            d = _dec(m.group("pct"))
        else:
            amt = _dec(m.group("amt"))
            den = _dec(m.group("den")) or denomination
            d = (amt / den * 100) if amt is not None and den else None
        if d is not None and Decimal("80") <= d <= Decimal("105"):
            result.add(_hit(text, "fee_based_account_price_pct", _pct_value(d), m.start(), m.end()))
            return


# ── Denominations ────────────────────────────────────────────────────────
_DENOM = re.compile(r"(?:minimum\s+)?denominations?\s+of\s+\$\s?(?P<amt>[\d,]+(?:\.\d+)?)", re.IGNORECASE)


def find_denomination(text: str, result: RuleResult) -> Decimal | None:
    m = _DENOM.search(text)
    if not m:
        return None
    d = _dec(m.group("amt"))
    if d is None or d <= 0:
        return None
    result.add(_hit(text, "denomination_amount", _pct_value(d), m.start(), m.end()))
    return d


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
            name = _SENTENCE_BREAK.split(name)[-1]   # "...Securities. UBS Securities LLC"
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


# ── Orchestration ─────────────────────────────────────────────────────────
def run_rules(text: str, *, distribution_spans: list[tuple[int, int]] | None = None) -> RuleResult:
    result = RuleResult()
    find_cusip(text, result)
    find_dates(text, result)
    denomination = find_denomination(text, result)
    find_estimated_value(text, result, denomination)
    find_pricing_table(text, result, denomination)
    find_price_to_public(text, result, denomination)
    find_commission(text, result)
    find_fee_based_price(text, result, denomination)
    if distribution_spans:
        result.participant_names = find_participant_names(text, distribution_spans)
    return result
