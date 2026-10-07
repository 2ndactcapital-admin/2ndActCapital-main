"""Shared pieces of the per-user market data endpoints (mkt03b): personal key
dates ("My dates") and saved views.

PER-USER PRIVACY IS ENFORCED HERE, NOT IN RLS. The platform's RLS session
settings are ``app.current_org_id`` and ``app.is_super_admin`` (plus
``app.current_auth0_sub``, used only by the ``users`` bootstrap policy). No
policy can say "only the caller's own rows", so ``market_data.user_key_dates``
and ``market_data.saved_views`` carry an ORG-isolation policy as the backstop,
and EVERY query in ``key_dates.py`` and ``saved_views.py`` filters by BOTH
org_id and user_id taken from the verified session — the same pattern as
``member_todos`` and ``user_notification_preferences``. See
docs/MARKET_DATA_DESIGN_V1.md, decision 20.

These modules run on the REQUEST connection with the caller's RLS context and
must never import platform_scope (verify_mkt03b checks that statically).
"""
from __future__ import annotations

import unicodedata
import uuid
from datetime import date

from pydantic import ValidationError

from services.market_data.access import MARKET_DATA_READ_PERMISSION, MarketDataReader
from services.market_data.read_service import MarketDataRequestError, _err

NOT_FOUND = "Not found."
MSG_CONTROL = "Names cannot contain control characters."

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def personal_envelope(reader: MarketDataReader) -> dict:
    """The permissions envelope for the personal endpoints. ``can_write`` means
    "may manage your OWN rows" — the same single session gate as the read API
    decides it, so no permission name is published for either side."""
    return {
        "can_read": True,
        "can_write": True,
        "is_super_admin": reader.is_super_admin,
        "read_permission": MARKET_DATA_READ_PERMISSION,
        "write_permission": None,
    }


def refuse(message: str, field: str | None, kind: str, *, status: int = 422, **extra) -> MarketDataRequestError:
    errors = [_err(field, kind, message)] if field is not None else None
    return MarketDataRequestError(message, errors=errors, status=status, **extra)


def not_found() -> MarketDataRequestError:
    """One answer for missing, another user's, another org's and a platform
    preset — the caller learns nothing about which."""
    return MarketDataRequestError(NOT_FOUND, status=404)


def parse_row_id(text: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(text))
    except (ValueError, TypeError, AttributeError):
        raise not_found() from None


def clean_name(value, *, max_len: int, empty_msg: str, long_msg: str) -> str:
    """Trim, then: empty → ``empty_msg``; too long → ``long_msg``; any control
    character → MSG_CONTROL. Python's strip() removes every Unicode space at the
    ends, so the result always satisfies the table's ``name = btrim(name)``."""
    if not isinstance(value, str):
        raise refuse(empty_msg, "name", "missing")
    name = value.strip()
    if not name:
        raise refuse(empty_msg, "name", "empty")
    if len(name) > max_len:
        raise refuse(long_msg, "name", "too_long")
    if any(unicodedata.category(ch) == "Cc" for ch in name):
        raise refuse(MSG_CONTROL, "name", "control_character")
    return name


def month_label(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.year}"


def sanitise(exc: ValidationError, prefix: list) -> list[dict]:
    """Like read_service._sanitise, for a nested object: never names an
    undeclared field, and never repeats a discriminator value the caller sent
    (pydantic's union_tag_invalid message quotes it)."""
    out: list[dict] = []
    extra_seen: set = set()
    for e in exc.errors(include_url=False, include_context=False, include_input=False):
        if e["type"] == "extra_forbidden":
            # Point at the object that holds the unknown field, never the field.
            parent = tuple(x if isinstance(x, int) else str(x) for x in e["loc"][:-1])
            if parent not in extra_seen:
                out.append({"loc": ["body", *prefix, *parent], "type": "extra_forbidden",
                             "msg": "This object contains a field that is not accepted"})
                extra_seen.add(parent)
            continue
        loc = ["body", *prefix, *[x if isinstance(x, int) else str(x) for x in e["loc"]]]
        msg = e["msg"]
        if e["type"] in ("union_tag_invalid", "union_tag_not_found"):
            msg = "type must be one of: date, relative"
        out.append({"loc": loc, "type": e["type"], "msg": msg})
    return out
