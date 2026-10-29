"""Shared shapes for the security-data layer. No vendor names, no network."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

DATA_TYPES = ("eod_price", "corporate_action", "fund_holdings", "index_level", "option_quote", "fx")
PURPOSES = ("internal", "member_display")
PROVIDER_KINDS = ("custodian", "regulator_filing", "issuer_file", "public_api", "csv_upload", "adviser_entry", "vendor")
PROVIDER_STATUSES = ("active", "not_connected", "retired")
MEMBER_DISPLAY = ("allowed", "own_holdings_only", "not_allowed")
RETENTION = ("keep", "delete", "unknown", "not_applicable")
QUALITY_KINDS = ("stale", "gap", "outlier", "disagreement", "budget_exhausted")

DATA_TYPE_LABELS = {"eod_price": "End-of-day price", "corporate_action": "Corporate actions (dividends, splits)",
                    "fund_holdings": "Fund holdings", "index_level": "Index level / rate",
                    "option_quote": "Option quote", "fx": "FX rate"}
PURPOSE_LABELS = {"internal": "Internal use (adviser analysis)", "member_display": "Shown to a member"}
KIND_LABELS = {"custodian": "Custodian valuation", "regulator_filing": "Regulatory filing",
               "issuer_file": "Issuer file", "public_api": "Public data API", "csv_upload": "File upload",
               "adviser_entry": "Adviser entry", "vendor": "Data vendor"}
STATUS_LABELS = {"active": "Active", "not_connected": "Not connected", "retired": "Retired"}
MEMBER_DISPLAY_LABELS = {"allowed": "Allowed", "own_holdings_only": "The member's own holdings only",
                         "not_allowed": "Not allowed"}
RETENTION_LABELS = {"keep": "May keep after cancellation", "delete": "Must delete after cancellation",
                    "unknown": "Unknown — treat as delete", "not_applicable": "Not applicable (own data)"}
QUALITY_LABELS = {"stale": "Stale", "gap": "Gap in the series", "outlier": "Outlier move",
                  "disagreement": "Sources disagree", "budget_exhausted": "Daily call budget exhausted"}

NOT_PRICED = "not priced"


class SecurityDataError(ValueError):
    """A caller error the API maps to 422 (unknown type, purpose, provider)."""


class SecurityDataNotFound(LookupError):
    pass


@dataclass
class Point:
    """One value a provider holds for a subject on a date."""
    value: Any                 # Decimal for prices / levels / quotes; list / dict for actions and holdings
    as_of: date
    provider_key: str
    licence: dict = field(default_factory=dict)   # the value's own licence when it carries one (an upload)
    quality: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class ReadContext:
    """Who is asking and for what. ``account_ids`` scopes custodian
    valuations to those accounts — REQUIRED for a member-display read (the
    member's own accounts: custodian valuations are shown to a member only for
    their own holdings); optional for internal use (None = every account of
    the org)."""
    org_id: str
    purpose: str = "internal"
    account_ids: list[str] | None = None
    max_age_days: int | None = None


@dataclass
class Result:
    data_type: str
    subject: str
    found: bool
    value: Any = None
    provider_key: str | None = None
    provider_name: str | None = None
    as_of: date | None = None
    age_days: int | None = None
    quality: list[str] = field(default_factory=list)
    licence: dict = field(default_factory=dict)
    attribution: str | None = None
    missing: str | None = None
    tried: list[dict] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        """The one line a screen shows on hover: source + as-of, or what is missing."""
        if not self.found:
            return f"{NOT_PRICED} — {self.missing}"
        return f"estimate — {self.provider_name or self.provider_key}, as of {self.as_of.isoformat()}"

    def as_dict(self) -> dict:
        v = self.value
        if isinstance(v, Decimal):
            v = format(v, "f")
        return {"data_type": self.data_type, "subject": self.subject, "found": self.found, "value": v,
                "provider_key": self.provider_key, "provider_name": self.provider_name,
                "as_of": self.as_of.isoformat() if self.as_of else None, "age_days": self.age_days,
                "quality": list(self.quality), "licence": self.licence, "attribution": self.attribution,
                "missing": self.missing, "tried": self.tried, "label": self.label, "detail": self.detail}


def d(v) -> Decimal | None:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))
