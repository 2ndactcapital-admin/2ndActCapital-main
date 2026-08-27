"""Custody provider-adapter interface — Sprint fee31, the account layer.

This is the substrate the whole fee/billing/profitability module sits on. It
deliberately knows nothing about fee schedules, billing groups or positions.

──────────────────────────────────────────────────────────────────────────────
THE ACCOUNT NUMBER IS THE ONLY REALLY DANGEROUS VALUE HERE
──────────────────────────────────────────────────────────────────────────────
A full custodial account number must never reach the database, a log line, an
exception message, a traceback, or any model-facing text. "Be careful not to
print it" is not a control — a dataclass ``repr`` in a stack trace, a
``logging.exception`` on a failed row, or an f-string in an error message all
leak it without anyone writing the word "print".

So the raw number never travels as a ``str``. It travels as
:class:`AccountNumber`, whose ``__repr__``, ``__str__``, ``__format__`` and
``to_json`` **all return the mask**. Getting the real digits out requires
calling :meth:`AccountNumber.reveal` explicitly, and exactly two call sites in
this package do: the hasher and the masker. Everything else — parse errors,
exception rows, dry-run diffs, API responses — handles the object and therefore
handles the mask, by construction rather than by discipline.

──────────────────────────────────────────────────────────────────────────────
DECIMAL, NEVER FLOAT
──────────────────────────────────────────────────────────────────────────────
Every monetary field is ``Decimal``. :func:`parse_decimal` goes from the source
text straight to ``Decimal`` and never through ``float`` on the way — a single
``float("1234567.89")`` round-trip is enough to move a fee by a cent, and a fee
that is a cent off is a fee that has to be explained to a client.

The deployed columns are ``numeric(20,4)``, so values are quantised to four
decimal places at parse time; a source file carrying more precision is rounded
here, once, visibly, rather than by Postgres silently at insert.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

# numeric(20,4) on account_balances_daily and account_flows, introspected from
# the deployed schema. Quantising here means the rounding is ours and is
# reported, instead of Postgres doing it on the way in where nobody sees it.
MONEY_QUANTUM = Decimal("0.0001")

#: How many trailing characters of an account number survive masking.
MASK_VISIBLE_TAIL = 4

#: org_settings key holding the per-org salt mixed into every account-number
#: hash. Per-org so that the same account number at the same custodian hashes
#: differently for two tenants — otherwise a hash would be a cross-tenant join
#: key, which is precisely the correlation the hash exists to prevent.
SALT_SETTING_KEY = "custody.account_hash_salt"

#: org_settings key holding the custodian profiles (see registry.py).
PROFILES_SETTING_KEY = "custody.profiles"

#: Written into ``accounts.registration_type`` / ``tax_status`` when the source
#: file does not carry them. Both columns are NOT NULL with no default and no
#: CHECK in the deployed schema, so *something* has to be supplied; a loud
#: sentinel is better than a plausible-looking guess like 'individual', which
#: would be indistinguishable from a real answer once it is in the table.
UNKNOWN = "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class CustodyError(Exception):
    """Base for everything this package raises."""


class ColumnMappingError(CustodyError):
    """The column map does not describe the file it was pointed at.

    A whole-file failure, not a row failure: if the mapping names a column the
    file does not have, every row would fail the same way and the operator
    needs to fix the mapping, not review 4,000 identical exceptions.
    """


@dataclass(frozen=True)
class RowError:
    """One row that could not be turned into a record.

    Carries ``raw`` already masked. Constructed only via
    :meth:`CustodyAdapter.row_error` so that masking is not an optional step a
    future call site can forget.
    """

    source_row: int
    record_kind: str
    reason_code: str
    reason: str
    raw: dict[str, Any]


# ═══════════════════════════════════════════════════════════════════════════
# The account number
# ═══════════════════════════════════════════════════════════════════════════


class AccountNumber:
    """A custodial account number that will not serialise itself in the clear.

    ``repr``/``str``/``format``/``to_json`` return the MASK. The real value is
    reachable only through :meth:`reveal`, which this package calls in exactly
    two places (:meth:`masked` and :meth:`hashed`).

    Not a ``dataclass``: a dataclass would synthesise a ``__repr__`` containing
    the field, which is the leak.
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: str):
        cleaned = (raw or "").strip()
        if not cleaned:
            raise ValueError("account number is empty")
        self._raw = cleaned

    # ── The two sanctioned readers ────────────────────────────────────────
    def reveal(self) -> str:
        """The real digits. Two call sites in this package; add none casually."""
        return self._raw

    @property
    def masked(self) -> str:
        """``****1234`` — what is stored in ``accounts.account_number_masked``.

        Non-alphanumeric separators are stripped first so that ``12-345-6789``
        and ``123456789`` mask identically; otherwise the mask would leak the
        custodian's formatting convention and, for short numbers, most of the
        number.
        """
        digits = re.sub(r"[^A-Za-z0-9]", "", self.reveal())
        tail = digits[-MASK_VISIBLE_TAIL:]
        return "*" * max(len(digits) - len(tail), MASK_VISIBLE_TAIL) + tail

    def hashed(self, salt: str) -> str:
        """``sha256(account_number + org salt)`` as hex.

        HMAC rather than a bare concatenated digest: with plain
        ``sha256(number + salt)`` an attacker holding the salt can extend it,
        and more practically the concatenation is ambiguous — number "12" with
        salt "34" and number "1" with salt "234" produce the same input string,
        so two different accounts could collide onto one hash and one of them
        would silently vanish behind the unique index. HMAC's length-prefixed
        block structure removes both problems at no cost.

        The value is normalised the same way the mask is (case-folded,
        separators stripped) so ``ABC-123`` and ``abc123`` from two different
        exports of the same account resolve to the same account row instead of
        creating a duplicate.
        """
        if not salt:
            raise CustodyError(
                "refusing to hash an account number with an empty salt — an "
                "unsalted digest of a short account number is trivially "
                "reversible by brute force"
            )
        normalised = re.sub(r"[^A-Za-z0-9]", "", self.reveal()).upper()
        return hmac.new(
            salt.encode("utf-8"), normalised.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    # ── Everything else shows the mask ────────────────────────────────────
    def __repr__(self) -> str:
        return f"AccountNumber({self.masked!r})"

    def __str__(self) -> str:
        return self.masked

    def __format__(self, spec: str) -> str:
        return format(self.masked, spec)

    def to_json(self) -> str:
        return self.masked

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AccountNumber):
            return NotImplemented
        return self._raw == other._raw

    def __hash__(self) -> int:
        # Hashing the raw value is fine — Python's hash is in-process only and
        # never serialised. Not deriving it from the mask matters: two distinct
        # accounts sharing a last-4 must not compare or bucket as one.
        return hash(self._raw)


# ═══════════════════════════════════════════════════════════════════════════
# Parsing helpers — shared by every adapter, not just the CSV one
# ═══════════════════════════════════════════════════════════════════════════

_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%Y%m%d",
    "%m-%d-%Y",
    "%b %d, %Y",
    "%d-%b-%Y",
)


def parse_decimal(raw: Any, *, field_name: str) -> Decimal:
    """Source text → ``Decimal``, never touching ``float``.

    Handles the shapes custodial exports actually emit: thousands separators,
    currency symbols, and negatives written as ``(1,234.56)``.
    """
    if isinstance(raw, Decimal):
        return raw.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    if isinstance(raw, float):
        # Reached only if a caller hands us a float directly. Route through str
        # so we quantise the shortest repr rather than the binary artefact.
        raw = repr(raw)

    text = str(raw if raw is not None else "").strip()
    if not text:
        raise ValueError(f"{field_name} is empty")

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = re.sub(r"[,\s$€£¥]", "", text)
    if text.endswith("-"):          # trailing-minus convention
        negative, text = True, text[:-1]
    if not text:
        raise ValueError(f"{field_name} is empty")

    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} is not a number: {raw!r}") from exc
    if negative:
        value = -value
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def parse_date(raw: Any, *, field_name: str) -> date:
    """Source text → ``date``, trying the formats custodians actually use.

    ``%m/%d/%Y`` is tried before ``%d/%m/%Y`` because every custodian in scope
    is US-domiciled. The ambiguity is unavoidable in a bare CSV and is called
    out here so that the tie-break is a stated choice rather than an accident
    of dict ordering: ``03/04/2026`` is read as 4 March.
    """
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw

    text = str(raw if raw is not None else "").strip()
    if not text:
        raise ValueError(f"{field_name} is empty")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{field_name} is not a recognised date: {text!r}")


def parse_bool(raw: Any, *, field_name: str, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if not text:
        return default
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{field_name} is not a boolean: {raw!r}")


def parse_text(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


# ═══════════════════════════════════════════════════════════════════════════
# The three record types the interface trades in
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AccountRecord:
    """One billable account as the source system describes it.

    ``primary_entity_ref`` / ``household_ref`` are the source's *reference* —
    a uuid, or a display name to be looked up. Resolution to a real
    ``entities.id`` / ``households.id`` happens in the importer against the
    caller's org, never in the adapter: an adapter that resolved ids would need
    a database handle and an org, and would then be the place a cross-tenant
    lookup could be introduced.

    A record whose ``primary_entity_ref`` does not resolve cannot be inserted —
    ``accounts.primary_entity_id`` is NOT NULL with a foreign key — so it
    becomes a batch exception. That is the schema enforcing the sprint's
    "never silently drop" requirement rather than application code promising it.
    """

    source_row: int
    account_number: AccountNumber
    custodian_code: str
    primary_entity_ref: str | None = None
    household_ref: str | None = None
    custodian_account_id: str | None = None
    registration_type: str = UNKNOWN
    tax_status: str = UNKNOWN
    service_model: str | None = None
    advisor_of_record_ref: str | None = None
    is_billable: bool = True
    is_discretionary: bool = True
    is_held_away: bool = False
    opened_on: date | None = None
    closed_on: date | None = None
    base_currency: str = "USD"

    def summary(self) -> dict[str, Any]:
        """Safe-to-serialise view. ``account_number`` comes out masked."""
        return {
            "source_row": self.source_row,
            "account_number_masked": self.account_number.masked,
            "custodian_code": self.custodian_code,
            "primary_entity_ref": self.primary_entity_ref,
            "household_ref": self.household_ref,
            "registration_type": self.registration_type,
            "tax_status": self.tax_status,
            "service_model": self.service_model,
            "is_billable": self.is_billable,
            "is_discretionary": self.is_discretionary,
            "is_held_away": self.is_held_away,
            "opened_on": self.opened_on.isoformat() if self.opened_on else None,
            "closed_on": self.closed_on.isoformat() if self.closed_on else None,
            "base_currency": self.base_currency,
        }


@dataclass(frozen=True)
class BalanceRecord:
    """One account's end-of-day balance.

    Maps onto ``account_balances_daily``, whose PRIMARY KEY is the natural key
    ``(org_id, account_id, as_of_date, source_system)``. That is what makes a
    balance re-import idempotent with no extra machinery: the same file twice
    produces the same four values and conflicts on the PK.
    """

    source_row: int
    account_number: AccountNumber
    as_of_date: date
    total_market_value: Decimal
    cash_value: Decimal = Decimal("0")
    margin_balance: Decimal = Decimal("0")
    accrued_income: Decimal = Decimal("0")
    source_system: str = "CSV"
    source_confidence: str = "CONFIRMED"
    is_billing_source: bool = False
    is_final: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "source_row": self.source_row,
            "account_number_masked": self.account_number.masked,
            "as_of_date": self.as_of_date.isoformat(),
            "total_market_value": str(self.total_market_value),
            "cash_value": str(self.cash_value),
            "margin_balance": str(self.margin_balance),
            "accrued_income": str(self.accrued_income),
            "source_system": self.source_system,
            "source_confidence": self.source_confidence,
            "is_billing_source": self.is_billing_source,
            "is_final": self.is_final,
        }


@dataclass(frozen=True)
class FlowRecord:
    """One contribution / withdrawal / transfer.

    ``account_flows`` has a surrogate id and no natural key in the deployed
    schema, so idempotency is carried by ``source_row_hash`` (added by this
    sprint's addendum migration and computed in importer.flow_row_hash).
    ``occurrence`` is that fingerprint's disambiguator: two genuinely identical
    deposits on one day get occurrence 0 and 1 and both survive, while the same
    file imported twice reproduces the same pair and dedupes.
    """

    source_row: int
    account_number: AccountNumber
    flow_date: date
    amount: Decimal
    flow_type: str
    is_billable_flow: bool = True
    source_system: str = "CSV"
    occurrence: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "source_row": self.source_row,
            "account_number_masked": self.account_number.masked,
            "flow_date": self.flow_date.isoformat(),
            "amount": str(self.amount),
            "flow_type": self.flow_type,
            "is_billable_flow": self.is_billable_flow,
            "source_system": self.source_system,
            "occurrence": self.occurrence,
        }


# ═══════════════════════════════════════════════════════════════════════════
# The adapter interface
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ParseOutcome:
    """What one ``fetch_*`` call produced, including what it could not parse.

    Records AND errors, together, always. A ``fetch_*`` that returned only the
    good records would make dropping a row the path of least resistance for
    every caller, which is the failure mode the sprint names explicitly.
    """

    records: list[Any] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)


class CustodyAdapter(ABC):
    """Read accounts, balances and flows from one custodian's export.

    An adapter is constructed per import and is stateless with respect to the
    database: it never opens a connection, never sees an ``org_id``, and never
    resolves an entity. It turns bytes into records. Everything tenant-scoped
    happens in the importer, which is the only place a cross-tenant mistake
    could be made and therefore the only place worth guarding.

    ``custodian_code`` is what the registry keyed on to find this class; it is
    carried onto every AccountRecord so the account's identity — the deployed
    ``(org_id, custodian_code, account_number_hash)`` unique index — is complete.
    """

    #: Registry key. Subclasses set it; the registry reads it back for errors.
    adapter_key: str = ""

    #: THE CONSTRUCTION CONTRACT every adapter must accept, because
    #: ``registry.build_adapter`` passes all of it by keyword and does not know
    #: which class it is building. A future adapter that ignores ``file_bytes``
    #: (a live REST client, say) still has to accept the keyword — otherwise
    #: adding it would mean editing build_adapter, which is the coupling the
    #: registry exists to remove.
    def __init__(
        self,
        *,
        custodian_code: str,
        source_system: str | None = None,
        file_bytes: bytes = b"",
        filename: str | None = None,
        column_map: dict[str, dict[str, str]] | None = None,
        strict_kinds: frozenset[str] = frozenset(),
    ):
        self.custodian_code = custodian_code
        self.source_system = source_system or custodian_code
        self.filename = filename
        self.column_map = column_map or {}
        self.strict_kinds = frozenset(strict_kinds)

    @abstractmethod
    def fetch_accounts(self) -> ParseOutcome:
        """Accounts described by the source. ``records`` are AccountRecord."""

    @abstractmethod
    def fetch_balances(self, as_of: date) -> ParseOutcome:
        """Balances at ``as_of``. ``records`` are BalanceRecord.

        A source that carries its own date column filters to ``as_of``; a
        source that does not stamps every row with it.
        """

    @abstractmethod
    def fetch_flows(self, from_date: date, to_date: date) -> ParseOutcome:
        """Flows in ``[from_date, to_date]`` inclusive. ``records`` are FlowRecord."""

    # ── Shared ────────────────────────────────────────────────────────────
    @staticmethod
    def row_error(
        source_row: int,
        record_kind: str,
        reason_code: str,
        reason: str,
        raw: dict[str, Any],
        *,
        account_number_column: str | None = None,
    ) -> RowError:
        """Build a RowError with the raw row already masked.

        The single constructor for row errors on purpose. ``raw`` is the source
        row verbatim, which for an account file contains the full number in
        whichever column the mapping pointed at — so that column is replaced
        with its mask here, before the value can reach a log, a response body
        or ``account_import_exceptions.raw_row``.

        When the mapping is the thing that failed we may not know which column
        held the number. In that case every value that even looks like an
        account number is masked, because a false positive costs a masked
        string and a false negative costs a leaked account number.
        """
        safe: dict[str, Any] = {}
        for key, value in raw.items():
            text = "" if value is None else str(value)
            if key == account_number_column or (
                account_number_column is None and _looks_like_account_number(text)
            ):
                try:
                    safe[key] = AccountNumber(text).masked
                except ValueError:
                    safe[key] = ""
            else:
                safe[key] = text
        return RowError(
            source_row=source_row,
            record_kind=record_kind,
            reason_code=reason_code,
            reason=reason,
            raw=safe,
        )


def _looks_like_account_number(text: str) -> bool:
    """Conservative-by-being-aggressive: is this plausibly an account number?

    Used only on the mapping-unknown path in :meth:`CustodyAdapter.row_error`.
    Deliberately over-matches — a masked amount in an exception row is a
    cosmetic annoyance, an unmasked account number is the thing this sprint
    exists to prevent.
    """
    stripped = re.sub(r"[^A-Za-z0-9]", "", text)
    if len(stripped) < 6 or len(stripped) > 32:
        return False
    return sum(character.isdigit() for character in stripped) >= 5
