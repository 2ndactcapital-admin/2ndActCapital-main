"""The note-terms field spec — GENERATED from portfolio.note_terms_field_registry.

ONE SOURCE OF TRUTH (notefields.structural)
──────────────────────────────────────────────────────────────────────────────
``build_field_specs(registry_rows)`` turns every LIVE registry row (retired_at
IS NULL) into a FieldSpec, carrying the registry's own description (used
verbatim in reader prompts and in the inventory's mapping check), section,
criticality, value shape, unit, vocabulary, synonyms, trap rule, extraction
method and derivation inputs. There is no code-side field list, no override
table and no extension list: a field added to the registry appears with no
code change; a field retired there disappears.

``reader_specs(specs)`` is the subset the MODELS answer
(extraction_method = 'model'). 'rules' fields are extracted by
services.note_extraction.rules only; 'derived' fields are computed by
services.note_extraction.derive only.

WHAT STAYS IN CODE: the member structure of the three LIST fields
(underlyings, observation_schedule, distribution) — a Pydantic model each
(``LIST_MEMBER_MODELS``). The registry says a field IS a list; this module
says what one member of it looks like. A registry list field with no member
model here is refused (``UnknownListField``), never read as free text.

A BUFFER AND A BARRIER NEVER SHARE A FIELD
──────────────────────────────────────────────────────────────────────────────
``buffer_pct`` (losses begin after -X%) and ``barrier_pct`` (breach it and the
WHOLE decline applies) are opposite payoffs. ``assert_no_shared_protection_field``
makes their separation a checked property of the generated spec.

UNITS: percentages as 70.0 (never 0.7); dates YYYY-MM-DD; 'usd_per_1000' is
currency per $1,000 of principal. A RANGE value is {"min", "max", "bound"}
(decision C): "up to 2.50%" -> min null, max 2.50; "as low as $977.50" -> min
977.50, max null; a single amount -> min = max.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError, create_model, model_validator

SCHEMA_VERSION = "notefields.v3"

KIND_NUMBER = "number"
KIND_TEXT = "text"
KIND_DATE = "date"
KIND_BOOLEAN = "boolean"
KIND_ENUM = "enum"
KIND_RANGE = "range"
KIND_LIST = "list"

METHOD_MODEL = "model"
METHOD_RULES = "rules"
METHOD_DERIVED = "derived"

# The bound wording a range value records (decision C).
RANGE_BOUNDS = ("exact", "up_to", "as_low_as", "not_less_than", "between", "approximately")


class UnknownListField(ValueError):
    pass


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str
    description: str
    enum: tuple[str, ...] | None = None
    critical: bool = False
    origin: str = "registry"
    registry_data_type: str | None = None
    section: str | None = None
    sort_order: int | None = None
    value_shape: str = "scalar"
    unit: str | None = None
    synonyms: tuple[str, ...] = ()
    trap_rule: str | None = None
    extraction_method: str = METHOD_MODEL
    derived_from: tuple[str, ...] = ()
    former_keys: tuple[str, ...] = ()


# ── List members (Task 3) ────────────────────────────────────────────────────
UNDERLYING_KINDS = ("index", "etf", "single_stock", "rate", "other")
RETURN_BASES = ("price_return", "total_return", "decrement")
FX_TREATMENTS = ("none", "quanto", "composite")
PARTICIPANT_ROLES = ("issuer_affiliated_agent", "distribution_agent", "dealer", "placement_agent")


class _Member(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Underlying(_Member):
    name: str
    ticker: Optional[str] = None
    kind: Optional[Literal[UNDERLYING_KINDS]] = None  # type: ignore[valid-type]
    weight_pct: Optional[Decimal] = None
    initial_level: Optional[Decimal] = None
    return_basis: Optional[Literal[RETURN_BASES]] = None  # type: ignore[valid-type]
    fx_treatment: Optional[Literal[FX_TREATMENTS]] = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _check(self):
        if not self.name.strip():
            raise ValueError("an underlying needs a name")
        if self.weight_pct is not None and not (Decimal(0) < self.weight_pct <= Decimal(100)):
            raise ValueError("weight_pct must be in (0, 100]")
        if self.initial_level is not None and self.initial_level <= 0:
            raise ValueError("initial_level must be positive")
        return self


class Observation(_Member):
    observation_date: date
    payment_date: Optional[date] = None
    coupon_barrier_pct: Optional[Decimal] = None
    coupon_amount: Optional[Decimal] = None
    call_level_pct: Optional[Decimal] = None
    call_amount: Optional[Decimal] = None

    @model_validator(mode="after")
    def _check(self):
        if self.payment_date is not None and self.payment_date < self.observation_date:
            raise ValueError("payment_date precedes observation_date")
        for f in ("coupon_barrier_pct", "coupon_amount", "call_level_pct", "call_amount"):
            v = getattr(self, f)
            if v is not None and v < 0:
                raise ValueError(f"{f} must not be negative")
        return self


class DistributionMember(_Member):
    name: str
    role: Literal[PARTICIPANT_ROLES]  # type: ignore[valid-type]
    fee_min_pct: Optional[Decimal] = None
    fee_max_pct: Optional[Decimal] = None

    @model_validator(mode="after")
    def _check(self):
        if not self.name.strip():
            raise ValueError("a participant needs a name")
        for f in ("fee_min_pct", "fee_max_pct"):
            v = getattr(self, f)
            if v is not None and not (Decimal(0) <= v <= Decimal(100)):
                raise ValueError(f"{f} must be a percent in [0, 100]")
        if (self.fee_min_pct is not None and self.fee_max_pct is not None
                and self.fee_min_pct > self.fee_max_pct):
            raise ValueError("fee_min_pct exceeds fee_max_pct")
        return self


LIST_MEMBER_MODELS: dict[str, type[_Member]] = {
    "underlyings": Underlying,
    "observation_schedule": Observation,
    "distribution": DistributionMember,
}

# How two members of the same list are recognised as THE SAME member, and
# whether list order carries meaning. Underlyings and participants are sets
# (order-insensitive); the schedule is ordered by its own dates, so sorting by
# observation_date is its canonical order and a reordered copy still agrees.
_LIST_IDENTITY = {
    "underlyings": lambda m: normalize_name(m.get("name")),
    "observation_schedule": lambda m: str(m.get("observation_date") or ""),
    "distribution": lambda m: normalize_name(m.get("name")),
}


class RangeValue(BaseModel):
    model_config = ConfigDict(extra="ignore")
    min: Optional[Decimal] = None
    max: Optional[Decimal] = None
    bound: Optional[Literal[RANGE_BOUNDS]] = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _check(self):
        if self.min is None and self.max is None:
            raise ValueError("a range needs a min or a max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("range min exceeds max")
        return self


def validate_list(key: str, value) -> tuple[list[dict] | None, str | None]:
    """(members as JSON-able dicts, None) or (None, error). Never raises."""
    model = LIST_MEMBER_MODELS.get(key)
    if model is None:
        return None, f"no member model for list field '{key}'"
    if not isinstance(value, list):
        return None, f"'{key}' must be a list"
    out = []
    for i, m in enumerate(value):
        try:
            out.append(to_jsonable(model.model_validate(m).model_dump()))
        except ValidationError as exc:
            return None, f"'{key}'[{i}]: {exc.error_count()} error(s): {str(exc)[:300]}"
    return out, None


# ── Registry -> specs ───────────────────────────────────────────────────────
_REGISTRY_KIND = {"numeric": KIND_NUMBER, "text": KIND_TEXT, "boolean": KIND_BOOLEAN, "date": KIND_DATE}


def _get(row, k):
    if isinstance(row, dict) or hasattr(row, "keys"):
        return row[k] if k in row.keys() else None
    return getattr(row, k, None)


def _kind(data_type: str, value_shape: str, enum_values) -> str:
    if value_shape == "list":
        return KIND_LIST
    if value_shape == "range":
        return KIND_RANGE
    if enum_values:
        return KIND_ENUM
    return _REGISTRY_KIND.get(data_type, KIND_TEXT)


def build_field_specs(registry_rows) -> list[FieldSpec]:
    """Every LIVE registry row -> one FieldSpec, ordered by sort_order. A row
    without the v3 properties (no description) is refused, not guessed."""
    specs: list[FieldSpec] = []
    for row in registry_rows:
        if _get(row, "retired_at") is not None:
            continue
        key = _get(row, "field_key")
        desc = _get(row, "description")
        if not desc:
            raise ValueError(f"registry field '{key}' has no description — load docs/NOTE_FIELDS.md first")
        shape = _get(row, "value_shape") or "scalar"
        if shape == "list" and key not in LIST_MEMBER_MODELS:
            raise UnknownListField(f"registry list field '{key}' has no member model in schema.py")
        enum_values = _get(row, "enum_values")
        specs.append(FieldSpec(
            key=key, label=_get(row, "display_label"),
            kind=_kind(_get(row, "data_type"), shape, enum_values),
            description=desc, enum=tuple(enum_values) if enum_values else None,
            critical=bool(_get(row, "is_critical")), origin="registry",
            registry_data_type=_get(row, "data_type"), section=_get(row, "section"),
            sort_order=_get(row, "sort_order"), value_shape=shape, unit=_get(row, "unit"),
            synonyms=tuple(_get(row, "synonyms") or ()), trap_rule=_get(row, "trap_rule"),
            extraction_method=_get(row, "extraction_method") or METHOD_MODEL,
            derived_from=tuple(_get(row, "derived_from") or ()),
            former_keys=tuple(_get(row, "former_keys") or ()),
        ))
    specs.sort(key=lambda s: (s.sort_order if s.sort_order is not None else 10**6, s.key))
    return specs


def reader_specs(specs: list[FieldSpec]) -> list[FieldSpec]:
    """The fields the two readers (and escalation) answer."""
    return [s for s in specs if s.extraction_method == METHOD_MODEL]


def critical_keys(specs: list[FieldSpec]) -> frozenset[str]:
    return frozenset(s.key for s in specs if s.critical)


def assert_no_shared_protection_field(specs: list[FieldSpec]) -> None:
    """Raise unless buffer_pct and barrier_pct are two separate live fields,
    each restricted to its own protection type, and no third field could hold
    either level."""
    by_key = {s.key: s for s in specs}
    for f in ("buffer_pct", "barrier_pct", "protection_type"):
        if f not in by_key:
            raise ValueError(f"protection field '{f}' is missing from the spec")
    if "protection_pct" in by_key:
        raise ValueError("protection_pct is live again — a level field shared by every protection type")
    buf, bar = by_key["buffer_pct"], by_key["barrier_pct"]
    if buf.kind != KIND_NUMBER or bar.kind != KIND_NUMBER:
        raise ValueError("buffer_pct and barrier_pct must both be scalar numbers")
    if "barrier" in buf.description.lower().replace("never a barrier", ""):
        raise ValueError("buffer_pct's description admits a barrier level")
    if "buffer" in bar.description.lower().replace("never a buffer", ""):
        raise ValueError("barrier_pct's description admits a buffer level")
    if "only for protection_type 'buffer'" not in buf.description.lower():
        raise ValueError("buffer_pct is not restricted to buffer protection")
    if "only for protection_type 'barrier'" not in bar.description.lower():
        raise ValueError("barrier_pct is not restricted to barrier protection")
    if set(s.lower() for s in buf.synonyms) & set(s.lower() for s in bar.synonyms):
        raise ValueError("buffer_pct and barrier_pct share a synonym")


REGISTRY_SELECT = (
    "SELECT field_key, display_label, data_type, applies_to_archetypes, hazard_field, description, "
    "section, sort_order, is_critical, value_shape, unit, enum_values, synonyms, trap_rule, "
    "extraction_method, derived_from, former_keys, retired_at, replaced_by, replacement_rule "
    "FROM portfolio.note_terms_field_registry"
)


async def load_registry_rows(conn, *, include_retired: bool = False) -> list[dict]:
    rows = await conn.fetch(REGISTRY_SELECT + ("" if include_retired else " WHERE retired_at IS NULL")
                            + " ORDER BY sort_order NULLS LAST, field_key")
    return [dict(r) for r in rows]


async def load_specs(conn) -> list[FieldSpec]:
    return build_field_specs(await load_registry_rows(conn))


# ── JSON schema ──────────────────────────────────────────────────────────────
_NUM = {"type": ["number", "null"]}
_DATE = {"type": ["string", "null"], "description": "YYYY-MM-DD"}


def _member_schema(key: str) -> dict:
    if key == "underlyings":
        props = {
            "name": {"type": "string", "description": "exactly as stated"},
            "ticker": {"type": ["string", "null"], "description": "Bloomberg ticker if given"},
            "kind": {"type": ["string", "null"], "enum": list(UNDERLYING_KINDS) + [None]},
            "weight_pct": {**_NUM, "description": "null for single and worst-of"},
            "initial_level": _NUM,
            "return_basis": {"type": ["string", "null"], "enum": list(RETURN_BASES) + [None]},
            "fx_treatment": {"type": ["string", "null"], "enum": list(FX_TREATMENTS) + [None]},
        }
    elif key == "observation_schedule":
        props = {
            "observation_date": {"type": "string", "description": "YYYY-MM-DD"},
            "payment_date": _DATE,
            "coupon_barrier_pct": _NUM, "coupon_amount": {**_NUM, "description": "per $1,000"},
            "call_level_pct": _NUM, "call_amount": {**_NUM, "description": "per $1,000"},
        }
    elif key == "distribution":
        props = {
            "name": {"type": "string", "description": "exactly as stated"},
            "role": {"type": "string", "enum": list(PARTICIPANT_ROLES)},
            "fee_min_pct": {**_NUM, "description": "percent of principal"},
            "fee_max_pct": {**_NUM, "description": "percent of principal"},
        }
    else:
        raise UnknownListField(key)
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def _value_schema(spec: FieldSpec) -> dict:
    if spec.kind == KIND_NUMBER:
        return dict(_NUM)
    if spec.kind == KIND_BOOLEAN:
        return {"type": ["boolean", "null"]}
    if spec.kind == KIND_DATE:
        return dict(_DATE)
    if spec.kind == KIND_ENUM:
        return {"type": ["string", "null"], "enum": list(spec.enum or ()) + [None]}
    if spec.kind == KIND_RANGE:
        return {
            "type": ["object", "null"], "additionalProperties": False,
            "required": ["min", "max", "bound"],
            "properties": {"min": _NUM, "max": _NUM,
                           "bound": {"type": ["string", "null"], "enum": list(RANGE_BOUNDS) + [None]}},
        }
    if spec.kind == KIND_LIST:
        return {"type": ["array", "null"], "items": _member_schema(spec.key)}
    return {"type": ["string", "null"]}


def json_schema(specs: list[FieldSpec]) -> dict:
    props = {}
    for s in specs:
        desc = s.description + (f" Unit: {s.unit}." if s.unit else "")
        props[s.key] = {
            "type": "object",
            "description": desc,
            "additionalProperties": False,
            "required": ["value", "quote"],
            "properties": {
                "value": _value_schema(s),
                "quote": {"type": ["string", "null"],
                          "description": "a SHORT exact quote from the filing supporting the value; null if absent"},
            },
        }
    return {"type": "object", "additionalProperties": False, "required": [s.key for s in specs],
            "properties": props}


def schema_hash(specs: list[FieldSpec]) -> str:
    return hashlib.sha256(json.dumps(json_schema(specs), sort_keys=True).encode()).hexdigest()


# ── Pydantic (ALWAYS validate the raw JSON text with model_validate_json) ────
def _py_type(spec: FieldSpec):
    if spec.kind == KIND_NUMBER:
        return Decimal
    if spec.kind == KIND_BOOLEAN:
        return bool
    if spec.kind == KIND_DATE:
        return date
    if spec.kind == KIND_ENUM:
        return Literal[tuple(spec.enum)]  # type: ignore[valid-type]
    if spec.kind == KIND_RANGE:
        return RangeValue
    if spec.kind == KIND_LIST:
        return list[LIST_MEMBER_MODELS[spec.key]]
    return str


_MODEL_CACHE: dict[str, type[BaseModel]] = {}


def pydantic_model(specs: list[FieldSpec]) -> type[BaseModel]:
    """One ``{value, quote}`` object per field. A missing field or a null value
    is accepted (= absent); a wrong type is a ValidationError."""
    h = schema_hash(specs)
    if h in _MODEL_CACHE:
        return _MODEL_CACHE[h]
    fields = {}
    for s in specs:
        item = create_model(
            f"F_{s.key}", __config__=ConfigDict(extra="ignore"),
            value=(Optional[_py_type(s)], None), quote=(Optional[str], None),
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
        if isinstance(value, BaseModel):
            value = value.model_dump()
        elif isinstance(value, list):
            value = [v.model_dump() if isinstance(v, BaseModel) else v for v in value]
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
    s = _NAME_STRIP.sub(" ", str(name).lower()).replace("&", " and ")
    tokens = [t for t in s.split() if t not in _SUFFIXES]
    return " ".join(tokens)


def _dec(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("%", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _norm_number(value) -> str | None:
    d = _dec(value)
    if d is None:
        return None
    return format(d.quantize(Decimal("0.0001")).normalize(), "f")


def _norm_date(value) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return None


def _norm_text(value) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value)).strip().lower() or None


def as_range(value) -> dict | None:
    """A range value as {min, max, bound}; a bare number is min = max (an old
    scalar reading of a field that is now a range compares equal to it)."""
    if value is None:
        return None
    if isinstance(value, dict):
        lo, hi = _dec(value.get("min")), _dec(value.get("max"))
        if lo is None and hi is None:
            return None
        return {"min": lo, "max": hi, "bound": value.get("bound")}
    d = _dec(value)
    return None if d is None else {"min": d, "max": d, "bound": "exact"}


def _member_fields(key: str) -> list[tuple[str, str]]:
    """(member field, scalar kind) — every member field compared with the same
    normalisation a scalar of that kind gets."""
    out = []
    for name, f in LIST_MEMBER_MODELS[key].model_fields.items():
        args = getattr(f.annotation, "__args__", ()) or (f.annotation,)
        if name == "name":
            out.append((name, "name"))
        elif Decimal in args:
            out.append((name, KIND_NUMBER))
        elif date in args:
            out.append((name, KIND_DATE))
        else:
            out.append((name, KIND_TEXT))
    return out


def _norm_member(key: str, m: dict) -> tuple:
    out = []
    for name, kind in _member_fields(key):
        v = m.get(name)
        if kind == KIND_NUMBER:
            out.append(_norm_number(v))
        elif kind == KIND_DATE:
            out.append(_norm_date(v) if v is not None else None)
        elif kind == "name":
            out.append(normalize_name(v) or None)
        else:
            out.append(_norm_text(v))
    return tuple(out)


def normalize_list(key: str, value) -> str | None:
    """Canonical string for a list: members validated, each member normalised
    field by field like a scalar, then sorted by the member's identity (so a
    reordered list of equal members is the same string). Empty -> None."""
    members, err = validate_list(key, value)
    if err or not members:
        return None
    ident = _LIST_IDENTITY[key]
    canon = sorted((ident(m), _norm_member(key, m)) for m in members)
    return json.dumps([c[1] for c in canon], separators=(",", ":"))


def normalize(spec: FieldSpec, value) -> str | None:
    """Canonical string for comparing two readings. None = absent."""
    if value is None:
        return None
    if spec.kind == KIND_NUMBER:
        return _norm_number(value)
    if spec.kind == KIND_DATE:
        return _norm_date(value)
    if spec.kind == KIND_BOOLEAN:
        return "true" if value is True else "false" if value is False else None
    if spec.kind == KIND_ENUM:
        s = str(value).strip().lower()
        return s if (not spec.enum or s in spec.enum) else None
    if spec.kind == KIND_RANGE:
        r = as_range(value)
        if r is None:
            return None
        return f"{_norm_number(r['min']) or ''}..{_norm_number(r['max']) or ''}"
    if spec.kind == KIND_LIST:
        return normalize_list(spec.key, value)
    if spec.kind == KIND_TEXT:
        return _norm_text(value)
    return str(value)


def lists_agree(key: str, a, b) -> bool:
    """Same members (order-insensitive where order has no meaning), each
    member's fields equal under scalar normalisation."""
    na, nb = normalize_list(key, a), normalize_list(key, b)
    return na is not None and na == nb


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


def _number_in(d: Decimal, nums: list[Decimal]) -> bool:
    # stated directly, per $1,000 (965.50 <-> 96.55) or as a percent of it
    return any(n == d or n == d * 10 or n * 10 == d for n in nums)


def value_in_quote(spec: FieldSpec, value, quote: str | None) -> bool | None:
    """True/False when the check applies; None when it does not (enums and
    booleans are judgments, not strings — their quote must still verify)."""
    if value is None or not quote:
        return None if value is None else False
    if spec.kind in (KIND_ENUM, KIND_BOOLEAN):
        return None
    if spec.kind == KIND_NUMBER:
        d = _dec(value)
        return False if d is None else _number_in(d, numbers_in(quote))
    if spec.kind == KIND_RANGE:
        r = as_range(value)
        if r is None:
            return False
        nums = numbers_in(quote)
        return all(_number_in(v, nums) for v in (r["min"], r["max"]) if v is not None)
    if spec.kind == KIND_DATE:
        return normalize(spec, value) in dates_in(quote)
    if spec.kind == KIND_LIST:
        if not isinstance(value, list) or not value:
            return None
        members = [m for m in value if isinstance(m, dict)]
        if spec.key == "observation_schedule":
            found = dates_in(quote)
            ds = [_norm_date(m.get("observation_date")) for m in members if m.get("observation_date")]
            return all(d in found for d in ds) if ds else None
        q = normalize_name(quote)
        names = [normalize_name(m.get("name")) for m in members if m.get("name")]
        return all(n in q for n in names) if names else None
    return normalize(spec, value) in re.sub(r"\s+", " ", quote).lower()


def spec_map(specs: list[FieldSpec]) -> dict[str, FieldSpec]:
    return {s.key: s for s in specs}


def subset(specs: list[FieldSpec], keys) -> list[FieldSpec]:
    keys = set(keys)
    return [s for s in specs if s.key in keys]
