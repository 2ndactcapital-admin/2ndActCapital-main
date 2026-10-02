"""The readers' field spec — GENERATED from portfolio.note_terms_field_registry.

ONE SOURCE OF TRUTH
──────────────────────────────────────────────────────────────────────────────
``build_field_specs(registry_rows)`` turns every registry row into a field the
readers must answer, then appends the B1 extensions (estimated value, fees,
dates, underlyings, barrier observation, distribution, ...). A field added to
the registry appears in the schema with no code change; the overrides below
only add a description, a vocabulary and a unit to fields we already know.

A BUFFER AND A BARRIER NEVER SHARE A FIELD
──────────────────────────────────────────────────────────────────────────────
Protection is a TYPE (full / buffer / barrier / none) plus a level, and the
level lives in a field that belongs to exactly one type: ``protection_pct``
(full or partial principal protection only), ``buffer_pct``, ``barrier_pct``.
Collapsing them is the classic misread: "70% protection" can be a 30% buffer
(losses start after -30%) or a 70% barrier (breach it and the WHOLE decline
applies) — opposite payoffs. ``assert_no_shared_protection_field`` makes that a
checked property of the generated spec.

NOTE ON VOCABULARY: the payoff DSL's protection_type vocabulary
(models.note_terms.PROTECTION_TYPES = buffer/floor/none) predates this sprint.
These readers use Joe's full/buffer/barrier/none; B1 only STAGES, so mapping
onto the DSL is B2's job (a DSL 'floor' is not a barrier).

UNITS: percentages as 70.0 (never 0.7); dates YYYY-MM-DD; fees and prices as a
percent of principal ($25 per $1,000 = 2.5).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from models.note_terms import PRODUCT_ARCHETYPES, TERMS_STATUSES

SCHEMA_VERSION = "noteextractb1.v1"

KIND_NUMBER = "number"
KIND_TEXT = "text"
KIND_DATE = "date"
KIND_BOOLEAN = "boolean"
KIND_ENUM = "enum"
KIND_LIST = "list_text"
KIND_PARTICIPANTS = "participants"

PROTECTION_TYPES = ("full", "buffer", "barrier", "none")
BASKET_TYPES = ("single", "worst_of", "best_of", "weighted_basket")
RETURN_BASES = ("price", "total_return")
AUTOCALL_FREQUENCIES = ("monthly", "quarterly", "semi_annual", "annual", "none")
BARRIER_OBSERVATIONS = ("at_maturity", "daily_close", "continuous_intraday", "periodic", "none")
COUPON_TYPES = ("contingent", "fixed", "floating", "none")
CALL_TYPES = ("automatic", "issuer", "none")
PARTICIPANT_ROLES = (
    "issuer_affiliated_agent", "distribution_agent", "dealer", "placement_agent", "other",
)
FEE_TYPES = (
    "selling_commission", "structuring_fee", "platform_fee", "marketing_fee",
    "referral_fee", "other",
)

# Decision 5 — needs_review and the higher Jev threshold apply to these.
CRITICAL_FIELDS = frozenset({
    "protection_type", "protection_pct", "buffer_pct", "barrier_pct",
    "barrier_observation", "principal_conditional",
    "coupon_type", "coupon_memory", "coupon_barrier_pct",
    "autocall_barrier_pct", "autocall_frequency", "call_type",
    "basket_type", "underlyings", "maturity_date",
    "estimated_value_pct", "total_commissions_fees_pct", "fee_based_account_price_pct",
})

# The protection-level fields: each belongs to exactly ONE protection type.
PROTECTION_LEVEL_FIELDS = {
    "protection_pct": "full",
    "buffer_pct": "buffer",
    "barrier_pct": "barrier",
}


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str
    description: str
    enum: tuple[str, ...] | None = None
    critical: bool = False
    origin: str = "registry"          # 'registry' | 'extension'
    registry_data_type: str | None = None


# ── Overrides for KNOWN registry keys: description (+ vocabulary) only ───────
_REGISTRY_OVERRIDES: dict[str, dict] = {
    "product_archetype": {
        "kind": KIND_ENUM, "enum": tuple(sorted(PRODUCT_ARCHETYPES)),
        "description": "The product family the payoff belongs to.",
    },
    "protection_type": {
        "kind": KIND_ENUM, "enum": PROTECTION_TYPES,
        "description": (
            "How principal is exposed at maturity. 'full': repayment of principal at maturity "
            "does NOT depend on the underlying (principal-protected, possibly partially — put the "
            "level in protection_pct). 'buffer': losses begin only after the underlying falls more "
            "than X%, and only the decline beyond X% is lost (level in buffer_pct). 'barrier': "
            "once the underlying ends below (or breaches) a threshold, the FULL decline from the "
            "initial level applies (threshold in barrier_pct; may be called a downside threshold, "
            "trigger or knock-in level). 'none': every percent of decline is lost."),
    },
    "protection_pct": {
        "description": (
            "ONLY for protection_type 'full': the percent of principal repaid regardless of the "
            "underlying (e.g. 100.0, or 90.0 for partial protection). Null for buffer or barrier "
            "notes — never put a buffer or barrier level here."),
    },
    "basket_type": {
        "kind": KIND_ENUM, "enum": BASKET_TYPES,
        "description": (
            "'single' = one underlying. 'worst_of' = the single WORST performer among several "
            "drives the payoff. 'best_of' = the best performer drives it. 'weighted_basket' = a "
            "weighted average of several underlyings."),
    },
    "return_basis": {
        "kind": KIND_ENUM, "enum": RETURN_BASES,
        "description": "'price' = price return (no dividends); 'total_return' = dividends reinvested.",
    },
    "autocall_frequency": {
        "kind": KIND_ENUM, "enum": AUTOCALL_FREQUENCIES,
        "description": "How often the note can be called (automatic or issuer call). 'none' if it cannot be called.",
    },
    "terms_status": {
        "kind": KIND_ENUM, "enum": tuple(sorted(TERMS_STATUSES)),
        "description": "'final' for a priced pricing supplement; 'preliminary' for indicative / subject-to-completion terms.",
    },
    "is_decrement_index": {
        "description": "True only if the underlying is a decrement index (a fixed synthetic dividend or fee is deducted from its level).",
    },
    "notional_currency": {"description": "ISO currency code of the principal, e.g. USD."},
    "cap_pct": {"description": "Maximum return as a percent of principal (e.g. 25.0), or null if uncapped."},
    "participation_rate": {"description": "Upside participation as a percent (150% leverage = 150.0)."},
    "coupon_rate": {"description": "Coupon rate PER ANNUM as a percent (e.g. 9.25). Convert a per-period rate only if the filing states the per-annum figure."},
    "coupon_barrier_pct": {"description": "Coupon barrier (coupon threshold) as a percent of the initial level, e.g. 70.0."},
    "autocall_barrier_pct": {"description": "Level at or above which the note is automatically called, as a percent of the initial level, e.g. 100.0."},
    "has_no_call_period": {"description": "True if the note cannot be called for an initial period."},
    "no_call_months": {"description": "Length of the initial no-call period in months."},
    "initial_valuation_date": {"description": "The pricing/strike date when the initial level is set (YYYY-MM-DD)."},
    "final_valuation_date": {"description": "The final valuation/observation/determination date (YYYY-MM-DD)."},
    "tenor_years": {"description": "Term from issue to maturity in years, e.g. 1.5."},
}

_REGISTRY_KIND = {
    "numeric": KIND_NUMBER, "text": KIND_TEXT, "boolean": KIND_BOOLEAN, "date": KIND_DATE,
}

# ── B1 extensions (not in the registry) ──────────────────────────────────────
_EXTENSIONS: tuple[FieldSpec, ...] = (
    FieldSpec("cusip", "CUSIP", KIND_TEXT, "The 9-character CUSIP of these notes.", origin="extension"),
    FieldSpec("pricing_date", "Pricing Date", KIND_DATE, "The pricing / trade date (YYYY-MM-DD).", origin="extension"),
    FieldSpec("maturity_date", "Maturity Date", KIND_DATE, "The stated maturity date (YYYY-MM-DD).", origin="extension"),
    FieldSpec("underlyings", "Underlyings", KIND_LIST,
              "Every underlying (index, stock, ETF, rate) by name as stated, one entry each.", origin="extension"),
    FieldSpec("buffer_pct", "Buffer %", KIND_NUMBER,
              "ONLY for protection_type 'buffer': the buffer size X in 'losses begin only after the "
              "underlying falls more than X%' (e.g. 15.0). Never a barrier level.", origin="extension"),
    FieldSpec("barrier_pct", "Barrier %", KIND_NUMBER,
              "ONLY for protection_type 'barrier': the downside threshold as a percent of the initial "
              "level (e.g. 70.0); once breached the FULL decline applies. Never a buffer size and never "
              "the coupon barrier.", origin="extension"),
    FieldSpec("barrier_observation", "Barrier Observation", KIND_ENUM,
              "When the downside barrier is observed: 'at_maturity' (final valuation date only), "
              "'daily_close' (every trading day's close), 'continuous_intraday' (any time), "
              "'periodic' (on scheduled observation dates), 'none' if there is no barrier.",
              enum=BARRIER_OBSERVATIONS, origin="extension"),
    FieldSpec("principal_conditional", "Principal Conditional", KIND_BOOLEAN,
              "True if repayment of principal at maturity DEPENDS on the underlying's performance. "
              "Principal is protected only if repayment at maturity does not depend on the underlying.",
              origin="extension"),
    FieldSpec("coupon_type", "Coupon Type", KIND_ENUM,
              "'contingent' = paid only if a condition (e.g. a coupon barrier) is met; 'fixed' = paid "
              "regardless; 'floating' = rate-linked; 'none' = no coupon.", enum=COUPON_TYPES, origin="extension"),
    FieldSpec("coupon_memory", "Coupon Memory", KIND_BOOLEAN,
              "True if missed contingent coupons are paid later when the condition is next met (memory / "
              "snowball feature).", origin="extension"),
    FieldSpec("call_type", "Call Type", KIND_ENUM,
              "'automatic' = called automatically when a level is met; 'issuer' = the issuer MAY redeem at "
              "its option; 'none' = not callable.", enum=CALL_TYPES, origin="extension"),
    FieldSpec("estimated_value_pct", "Estimated Value %", KIND_NUMBER,
              "The issuer's estimated value of the notes as a percent of principal ($965.50 per $1,000 = "
              "96.55). Null if only a range is given.", origin="extension"),
    FieldSpec("agent_commission_pct", "Agent Commission %", KIND_NUMBER,
              "The agent's commission / underwriting discount as a percent of principal.", origin="extension"),
    FieldSpec("total_commissions_fees_pct", "Total Commissions & Fees %", KIND_NUMBER,
              "Total selling commissions plus structuring and other fees, as a percent of principal "
              "($22.50 per $1,000 = 2.25).", origin="extension"),
    FieldSpec("price_to_public_pct", "Price to Public %", KIND_NUMBER,
              "The price to the public as a percent of principal (usually 100.0).", origin="extension"),
    FieldSpec("fee_based_account_price_pct", "Fee-Based Account Price %", KIND_NUMBER,
              "The separate price for investors in fee-based / advisory accounts, as a percent of "
              "principal (e.g. 98.0). Never the price to public.", origin="extension"),
    FieldSpec("denomination_amount", "Minimum Denomination", KIND_NUMBER,
              "The minimum denomination in currency units (e.g. 1000).", origin="extension"),
    FieldSpec("distribution", "Distribution Participants", KIND_PARTICIPANTS,
              "EVERY selling agent, distributor, dealer or placement agent named in the (supplemental) "
              "plan of distribution, as a LIST: one entry per participant, with the name exactly as "
              "stated, its role, and the fee it receives (type and amount) if stated.",
              origin="extension"),
)


def build_field_specs(registry_rows) -> list[FieldSpec]:
    """Registry rows (``key -> attr`` or mapping) -> the readers' field list.

    Every registry row becomes a field (generated generically if it has no
    override), then the extensions are appended. Extensions never shadow a
    registry key; a registry key always wins.
    """
    specs: list[FieldSpec] = []
    seen: set[str] = set()
    for row in registry_rows:
        get = (lambda k: row[k]) if isinstance(row, dict) or hasattr(row, "keys") else (lambda k: getattr(row, k))
        key = get("field_key")
        data_type = get("data_type")
        label = get("display_label")
        o = _REGISTRY_OVERRIDES.get(key, {})
        kind = o.get("kind") or _REGISTRY_KIND.get(data_type, KIND_TEXT)
        desc = o.get("description") or f"{label} as stated in the filing."
        specs.append(FieldSpec(
            key=key, label=label, kind=kind, description=desc, enum=o.get("enum"),
            critical=key in CRITICAL_FIELDS, origin="registry", registry_data_type=data_type,
        ))
        seen.add(key)
    for ext in _EXTENSIONS:
        if ext.key in seen:
            continue
        specs.append(FieldSpec(
            key=ext.key, label=ext.label, kind=ext.kind, description=ext.description,
            enum=ext.enum, critical=ext.key in CRITICAL_FIELDS, origin="extension",
        ))
        seen.add(ext.key)
    return specs


def assert_no_shared_protection_field(specs: list[FieldSpec]) -> None:
    """Raise if any single field could hold both a buffer and a barrier level."""
    keys = {s.key for s in specs}
    for f in ("buffer_pct", "barrier_pct", "protection_pct"):
        if f not in keys:
            raise ValueError(f"protection level field '{f}' is missing from the spec")
    by_key = {s.key: s for s in specs}
    if "barrier" in by_key["buffer_pct"].description.lower().replace("never a barrier", ""):
        raise ValueError("buffer_pct's description admits a barrier level")
    if "buffer" in by_key["barrier_pct"].description.lower().replace("never a buffer", ""):
        raise ValueError("barrier_pct's description admits a buffer level")
    if "only for protection_type 'full'" not in by_key["protection_pct"].description.lower():
        raise ValueError("protection_pct is not restricted to full protection")


async def load_registry_rows(conn) -> list[dict]:
    rows = await conn.fetch(
        "SELECT field_key, display_label, data_type, applies_to_archetypes, hazard_field "
        "FROM portfolio.note_terms_field_registry ORDER BY field_key"
    )
    return [dict(r) for r in rows]


# ── JSON schema ──────────────────────────────────────────────────────────────
def _value_schema(spec: FieldSpec) -> dict:
    if spec.kind == KIND_NUMBER:
        return {"type": ["number", "null"]}
    if spec.kind == KIND_BOOLEAN:
        return {"type": ["boolean", "null"]}
    if spec.kind == KIND_DATE:
        return {"type": ["string", "null"], "description": "YYYY-MM-DD"}
    if spec.kind == KIND_ENUM:
        return {"type": ["string", "null"], "enum": list(spec.enum or ()) + [None]}
    if spec.kind == KIND_LIST:
        return {"type": ["array", "null"], "items": {"type": "string"}}
    if spec.kind == KIND_PARTICIPANTS:
        return {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "role", "fee_type", "fee_pct", "fee_per_unit", "quote"],
                "properties": {
                    "name": {"type": "string", "description": "exactly as stated"},
                    "role": {"type": "string", "enum": list(PARTICIPANT_ROLES)},
                    "fee_type": {"type": ["string", "null"], "enum": list(FEE_TYPES) + [None]},
                    "fee_pct": {"type": ["number", "null"], "description": "percent of principal"},
                    "fee_per_unit": {"type": ["number", "null"], "description": "currency per denomination"},
                    "quote": {"type": ["string", "null"]},
                },
            },
        }
    return {"type": ["string", "null"]}


def json_schema(specs: list[FieldSpec]) -> dict:
    props = {}
    for s in specs:
        props[s.key] = {
            "type": "object",
            "description": s.description,
            "additionalProperties": False,
            "required": ["value", "quote"],
            "properties": {
                "value": _value_schema(s),
                "quote": {"type": ["string", "null"],
                          "description": "a SHORT exact quote from the filing supporting the value; null if absent"},
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [s.key for s in specs],
        "properties": props,
    }


def schema_hash(specs: list[FieldSpec]) -> str:
    return hashlib.sha256(json.dumps(json_schema(specs), sort_keys=True).encode()).hexdigest()


# ── Pydantic (ALWAYS validate the raw JSON text with model_validate_json) ────
class Participant(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    role: Literal[PARTICIPANT_ROLES]  # type: ignore[valid-type]
    fee_type: Optional[Literal[FEE_TYPES]] = None  # type: ignore[valid-type]
    fee_pct: Optional[Decimal] = None
    fee_per_unit: Optional[Decimal] = None
    quote: Optional[str] = None


def _py_type(spec: FieldSpec):
    if spec.kind == KIND_NUMBER:
        return Decimal
    if spec.kind == KIND_BOOLEAN:
        return bool
    if spec.kind == KIND_DATE:
        return date
    if spec.kind == KIND_ENUM:
        return Literal[tuple(spec.enum)]  # type: ignore[valid-type]
    if spec.kind == KIND_LIST:
        return list[str]
    if spec.kind == KIND_PARTICIPANTS:
        return list[Participant]
    return str


_MODEL_CACHE: dict[str, type[BaseModel]] = {}


def pydantic_model(specs: list[FieldSpec]) -> type[BaseModel]:
    """A model with one ``{value, quote}`` object per field. A missing field or
    a null value is accepted (= absent); a wrong type is a ValidationError."""
    h = schema_hash(specs)
    if h in _MODEL_CACHE:
        return _MODEL_CACHE[h]
    fields = {}
    for s in specs:
        item = create_model(
            f"F_{s.key}",
            __config__=ConfigDict(extra="ignore"),
            value=(Optional[_py_type(s)], None),
            quote=(Optional[str], None),
        )
        fields[s.key] = (Optional[item], None)
    model = create_model("NoteTermsReading", __config__=ConfigDict(extra="ignore"), **fields)
    _MODEL_CACHE[h] = model
    return model


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_reader_output(specs: list[FieldSpec], raw_text: str | None) -> tuple[dict | None, str | None]:
    """Validate a reader's raw output. Returns ({key: {value, quote}}, None) or
    (None, error). A failed validation is a FAILED READING, never an exception."""
    if not raw_text or not raw_text.strip():
        return None, "empty response"
    text = raw_text
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        obj = pydantic_model(specs).model_validate_json(text)
    except ValidationError as exc:
        return None, f"schema validation failed: {exc.error_count()} error(s): {str(exc)[:400]}"
    out = {}
    for s in specs:
        item = getattr(obj, s.key, None)
        if item is None:
            out[s.key] = {"value": None, "quote": None}
            continue
        value = item.value
        if s.kind == KIND_PARTICIPANTS and value is not None:
            value = [to_jsonable(p.model_dump()) for p in value]
        out[s.key] = {"value": to_jsonable(value), "quote": item.quote}
    return out, None


def to_jsonable(value):
    if isinstance(value, Decimal):
        f = float(value)
        return int(f) if f.is_integer() and abs(f) < 1e15 else f
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    return value


# ── Normalisation (comparison in code) ───────────────────────────────────────
_NAME_STRIP = re.compile(r"[®™©*\"“”'’,.()]")
_SUFFIXES = {"llc", "inc", "incorporated", "corporation", "corp", "lp", "ltd", "limited",
             "plc", "co", "company", "na", "n.a", "ag", "sa", "the"}


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    s = _NAME_STRIP.sub(" ", name.lower()).replace("&", " and ")
    tokens = [t for t in s.split() if t not in _SUFFIXES]
    return " ".join(tokens)


def _dec(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("%", "").strip())
    except (InvalidOperation, ValueError):
        return None


def normalize(spec: FieldSpec, value) -> str | None:
    """Canonical string for comparing two readings. None = absent."""
    if value is None:
        return None
    if spec.kind == KIND_NUMBER:
        d = _dec(value)
        if d is None:
            return None
        q = d.quantize(Decimal("0.0001"))
        s = format(q.normalize(), "f")
        return s
    if spec.kind == KIND_DATE:
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            return None
    if spec.kind == KIND_BOOLEAN:
        return "true" if value is True else "false" if value is False else None
    if spec.kind == KIND_ENUM:
        return str(value).strip().lower()
    if spec.kind == KIND_LIST:
        if not isinstance(value, list):
            return None
        items = sorted({normalize_name(v) for v in value if isinstance(v, str) and v.strip()})
        return "|".join(items) if items else None
    if spec.kind == KIND_PARTICIPANTS:
        if not isinstance(value, list):
            return None
        items = sorted({normalize_name(p.get("name")) for p in value if isinstance(p, dict) and p.get("name")})
        return "|".join(items) if items else None
    if spec.kind == KIND_TEXT:
        return re.sub(r"\s+", " ", str(value)).strip().lower() or None
    return str(value)


# ── Does the value appear in its quote? ──────────────────────────────────────
_NUM_RE = re.compile(r"(?<![\w.])\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")
_DATE_PATTERNS = (
    (re.compile(r"\b([A-Z][a-z]{2,8})\.? (\d{1,2}), (\d{4})\b"), ("%B %d %Y", "%b %d %Y")),
    (re.compile(r"\b(\d{1,2}) ([A-Z][a-z]{2,8}) (\d{4})\b"), ("%d %B %Y", "%d %b %Y")),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), ("%m %d %Y",)),
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), ("%Y %m %d",)),
)


def numbers_in(text: str) -> list[Decimal]:
    out = []
    for m in _NUM_RE.finditer(text or ""):
        try:
            out.append(Decimal((m.group(1) + (m.group(2) or "")).replace(",", "")))
        except InvalidOperation:
            pass
    return out


def dates_in(text: str) -> set[str]:
    found: set[str] = set()
    for rx, fmts in _DATE_PATTERNS:
        for m in rx.finditer(text or ""):
            raw = " ".join(m.groups())
            for fmt in fmts:
                try:
                    found.add(datetime.strptime(raw, fmt).date().isoformat())
                    break
                except ValueError:
                    continue
    return found


def value_in_quote(spec: FieldSpec, value, quote: str | None) -> bool | None:
    """True/False when the check applies; None when it does not (enums and
    booleans are judgments, not strings — their quote must still verify)."""
    if value is None or not quote:
        return None if value is None else False
    if spec.kind in (KIND_ENUM, KIND_BOOLEAN):
        return None
    if spec.kind == KIND_NUMBER:
        d = _dec(value)
        if d is None:
            return False
        nums = numbers_in(quote)
        # A percent stated directly, or the same value per $1,000 ($965.50 -> 96.55).
        return any(n == d or n == d * 10 for n in nums)
    if spec.kind == KIND_DATE:
        return normalize(spec, value) in dates_in(quote)
    if spec.kind == KIND_LIST:
        q = normalize_name(quote)
        return all(normalize_name(v) in q for v in value if isinstance(v, str)) if value else None
    if spec.kind == KIND_PARTICIPANTS:
        q = normalize_name(quote)
        return all(normalize_name(p.get("name")) in q for p in value if isinstance(p, dict)) if value else None
    return normalize(spec, value) in re.sub(r"\s+", " ", quote).lower()


def spec_map(specs: list[FieldSpec]) -> dict[str, FieldSpec]:
    return {s.key: s for s in specs}


def subset(specs: list[FieldSpec], keys) -> list[FieldSpec]:
    keys = set(keys)
    return [s for s in specs if s.key in keys]
