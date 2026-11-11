"""cpsim01 — "properly formatted in → mirrored out" (design §2 principle 5, v1 stateless).

``validate(schema, doc)`` is a small, dependency-free JSON Schema subset (the keywords the envelope schemas under
cpsim/schemas/ use: type, required, properties, additionalProperties, minLength, maxLength, pattern, format
date-time, enum). Each error is ``{"path": "$.field", "message": "..."}``.

``mirror(envelope, counterparty, business_date)`` returns the same message back (same message_id, payload untouched)
with ``mirrored: true`` and ``received_at`` stamped from the COUNTERPARTY's clock (its business date + the current
time of day), never the server's calendar date.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import date, datetime, timezone

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent / "schemas"
_SCHEMAS: dict[str, dict] = {}
_now = None   # injectable clock (tests): a zero-argument callable returning an aware datetime


def set_now(fn) -> None:
    global _now
    _now = fn


def now() -> datetime:
    return _now() if _now is not None else datetime.now(timezone.utc)


def load_schema(name: str) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,60}", name or ""):
        raise ValueError(f"bad schema name {name!r}")
    if name not in _SCHEMAS:
        _SCHEMAS[name] = json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return _SCHEMAS[name]


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def _is_type(v, t: str) -> bool:
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "null":
        return v is None
    return isinstance(v, _TYPES[t])


def _date_time_ok(s: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})", s):
        return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate(schema: dict, doc, path: str = "$") -> list[dict]:
    errs: list[dict] = []
    t = schema.get("type")
    if t is not None and not _is_type(doc, t):
        return [{"path": path, "message": f"must be of type {t}"}]
    if "enum" in schema and doc not in schema["enum"]:
        errs.append({"path": path, "message": f"must be one of {schema['enum']}"})
    if isinstance(doc, str):
        if "minLength" in schema and len(doc) < schema["minLength"]:
            errs.append({"path": path, "message": f"must be at least {schema['minLength']} characters"})
        if "maxLength" in schema and len(doc) > schema["maxLength"]:
            errs.append({"path": path, "message": f"must be at most {schema['maxLength']} characters"})
        if "pattern" in schema and not re.search(schema["pattern"], doc):
            errs.append({"path": path, "message": f"must match {schema['pattern']}"})
        if schema.get("format") == "date-time" and not _date_time_ok(doc):
            errs.append({"path": path, "message": "must be an ISO 8601 date-time with a time zone"})
    if isinstance(doc, dict):
        for k in schema.get("required", []):
            if k not in doc:
                errs.append({"path": f"{path}.{k}", "message": "is required"})
        props = schema.get("properties", {})
        for k, v in doc.items():
            if k in props:
                errs.extend(validate(props[k], v, f"{path}.{k}"))
            elif schema.get("additionalProperties") is False:
                errs.append({"path": f"{path}.{k}", "message": "is not allowed"})
    return errs


def received_at(business_date: date) -> str:
    """The counterparty's clock: its business date + the current UTC time of day."""
    n = now().astimezone(timezone.utc)
    return datetime.combine(business_date, n.timetz()).isoformat().replace("+00:00", "Z")


def mirror(envelope: dict, slug: str, business_date: date) -> dict:
    return {"message_type": envelope["message_type"], "message_id": envelope["message_id"],
            "as_of": envelope["as_of"], "payload": envelope["payload"], "mirrored": True,
            "received_at": received_at(business_date), "counterparty": slug, "simulated": True}


def sample_envelope(message_type: str = "test.ping") -> dict:
    """The admin page's "Send test message" body."""
    n = now().astimezone(timezone.utc)
    return {"message_type": message_type, "message_id": "test-" + n.strftime("%Y%m%dT%H%M%S%f"),
            "as_of": n.isoformat().replace("+00:00", "Z"), "payload": {"note": "test message from Admin → Counterparties"}}
