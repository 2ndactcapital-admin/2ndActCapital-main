"""CSV custody adapter — mapping-driven, not schema-driven.

WHY THERE IS NO "SCHWAB COLUMNS" CONSTANT IN THIS FILE
──────────────────────────────────────────────────────────────────────────────
Every custodian exports the same three concepts under different headers, and
the same custodian changes its own headers between report versions. An adapter
that hardcoded one custodian's header names would need a code change and a
deploy for a column rename, and a second custodian would arrive as a second
copy of this file. So this adapter is told the column names at construction —
they come from the org's profile in ``org_settings`` (registry.py) or from what
the operator chose in the import UI's mapping step — and it holds none itself.

ONE FILE, THREE VIEWS
──────────────────────────────────────────────────────────────────────────────
A custodial export is usually one wide CSV: account identity, a balance, and
sometimes a flow, all on one line. ``fetch_accounts`` / ``fetch_balances`` /
``fetch_flows`` are three *views* over the same parsed rows, each requiring only
the columns its own record needs. A file with no ``amount`` column yields zero
flows rather than 4,000 errors — the absence of a concept is not a parse
failure. That is checked once per file, in ``_mapping_for``, not once per row.

Accounts are deduplicated across rows by account number: thirty daily balance
rows for one account describe ONE account, and emitting thirty AccountRecords
would make the dry-run diff report thirty new accounts.
"""

from __future__ import annotations

import codecs
import csv
import io
from datetime import date
from typing import Any, Iterator

from services.custody.base import (
    UNKNOWN,
    AccountNumber,
    AccountRecord,
    BalanceRecord,
    ColumnMappingError,
    CustodyAdapter,
    FlowRecord,
    ParseOutcome,
    parse_bool,
    parse_date,
    parse_decimal,
    parse_text,
)
from services.custody.registry import register_adapter

#: Fields a record kind cannot be built without. Everything else is optional
#: and falls back to the record dataclass's default.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "account": ("account_number",),
    "balance": ("account_number", "as_of_date", "total_market_value"),
    "flow": ("account_number", "flow_date", "amount"),
}


class CsvCustodyAdapter(CustodyAdapter):
    """Parse an uploaded delimited file into custody records."""

    adapter_key = "csv"

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
        super().__init__(
            custodian_code=custodian_code,
            source_system=source_system,
            filename=filename,
            column_map=column_map,
            strict_kinds=strict_kinds,
        )
        # Which record kinds the CALLER mapped by hand. A default profile is a
        # guess about a custodian's usual column names and must degrade quietly
        # when a particular export omits an optional column; an operator's own
        # mapping was chosen from a dropdown of this file's real headers, so a
        # column that is not there means something is genuinely wrong and
        # silently ignoring it would drop data they asked for. Same code, two
        # different meanings of "this column is missing".
        self._rows: list[dict[str, str]] = []
        self._headers: list[str] = []
        self._dropped_columns: dict[str, list[str]] = {}
        self._load(file_bytes)

    # ── File → rows ───────────────────────────────────────────────────────
    def _load(self, file_bytes: bytes) -> None:
        """Decode and parse. A whole-file problem raises; a row problem does not.

        The BOM strip is not cosmetic: a UTF-8 BOM makes the FIRST header
        literally ``"﻿Account Number"``, so a mapping that names
        ``Account Number`` misses by one invisible character and the operator
        sees "column not found" while looking straight at the column. Excel
        writes that BOM by default, which is what every custodial export is
        opened and re-saved in.
        """
        if not file_bytes:
            raise ColumnMappingError("the uploaded file is empty")

        text = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = file_bytes.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:  # pragma: no cover — latin-1 decodes any byte string
            raise ColumnMappingError("the uploaded file is not readable text")
        text = text.lstrip(codecs.BOM_UTF8.decode("utf-8"))

        sample = text[:8192]
        try:
            dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            raise ColumnMappingError("the uploaded file has no header row")

        self._headers = [(name or "").strip() for name in reader.fieldnames]
        for row in reader:
            # Header text is normalised on BOTH sides (here and in
            # _mapping_for), so a mapping saved against "Market Value " with a
            # trailing space still resolves.
            self._rows.append(
                {
                    (key or "").strip(): ("" if value is None else str(value).strip())
                    for key, value in row.items()
                    if key is not None
                }
            )

    # ── Introspection, for the UI's mapping step ─────────────────────────
    @property
    def headers(self) -> list[str]:
        return list(self._headers)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def sample_rows(self, limit: int = 5) -> list[dict[str, str]]:
        """Preview rows for the mapping screen, with anything account-number
        shaped masked.

        The preview is rendered before the operator has told us which column
        holds the account number, so there is no column to trust — every value
        is run through the same over-matching heuristic the error path uses.
        The operator maps by column NAME and by the shape of the other columns;
        seeing full account numbers is not required to do that, and the preview
        is the one surface where the raw file would otherwise be echoed back
        verbatim into a browser and a server log.
        """
        from services.custody.base import _looks_like_account_number

        preview: list[dict[str, str]] = []
        for row in self._rows[:limit]:
            safe = {}
            for key, value in row.items():
                if _looks_like_account_number(value):
                    try:
                        safe[key] = AccountNumber(value).masked
                    except ValueError:
                        safe[key] = value
                else:
                    safe[key] = value
            preview.append(safe)
        return preview

    # ── Mapping ───────────────────────────────────────────────────────────
    def _mapping_for(self, kind: str) -> dict[str, str] | None:
        """The usable mapping for one record kind, or None if not present.

        Returns None — meaning "this file does not carry this concept" — when a
        REQUIRED column for the kind is absent. A balances-only export really
        does have no flow date and no amount, and 4,000 identical row errors is
        the wrong way to say so.

        An OPTIONAL field pointed at a column the file lacks is dropped, and
        raises only when this kind's mapping came from the caller (see
        ``strict_kinds``). The platform's default profile deliberately maps more
        columns than any one custodian emits — that is what makes it a useful
        starting point — so treating its misses as errors would make the default
        profile unusable against every real file.
        """
        mapping = {
            field: column
            for field, column in (self.column_map.get(kind) or {}).items()
            if column
        }
        if not mapping:
            return None

        headers = set(self._headers)
        required = REQUIRED_FIELDS[kind]

        required_columns = {mapping[f] for f in required if f in mapping}
        if any(f not in mapping for f in required) or not (
            required_columns <= headers
        ):
            return None

        optional_missing = sorted(
            {
                column
                for field, column in mapping.items()
                if field not in required and column not in headers
            }
        )
        if optional_missing:
            if kind in self.strict_kinds:
                raise ColumnMappingError(
                    f"the {kind} mapping names column(s) {optional_missing} "
                    f"which are not in the file. Available columns: "
                    f"{self._headers}"
                )
            self._dropped_columns[kind] = optional_missing
            mapping = {
                field: column
                for field, column in mapping.items()
                if column in headers
            }
        return mapping

    @property
    def dropped_columns(self) -> dict[str, list[str]]:
        """Optional profile columns this file does not have, per record kind.

        Surfaced rather than swallowed: a default profile quietly dropping
        ``service_model`` is fine, but an operator looking at an import that did
        not populate a field they expected needs somewhere to see why.
        """
        return dict(self._dropped_columns)

    def _numbered(self) -> Iterator[tuple[int, dict[str, str]]]:
        """Rows with their 1-based line number in the file, header excluded.

        ``+2`` so the number an exception reports is the line an operator can
        open the file to: line 1 is the header, so the first data row is 2.
        """
        for index, row in enumerate(self._rows):
            yield index + 2, row

    @staticmethod
    def _value(row: dict[str, str], mapping: dict[str, str], field: str) -> str:
        return row.get(mapping.get(field, ""), "")

    @classmethod
    def _absent(cls, row: dict[str, str], mapping: dict[str, str], kind: str) -> bool:
        """Does this row simply not carry a record of this kind?

        THE DISTINCTION THIS DRAWS IS THE WHOLE REASON ONE FILE CAN HOLD THREE
        KINDS. A custodial export is one wide table: thirty rows of daily
        balances, three of which also happen to describe a cash flow. On the
        other twenty-seven, ``flow_date`` and ``amount`` are empty — that is not
        a malformed flow, it is a day with no flow.

        EVERY kind-specific required column blank → the row has no record of
        this kind, skip it. SOME blank and some not → a genuinely broken row,
        reported as an exception. Collapsing the two produced 28 spurious
        "unparseable_flow" exceptions on a perfectly good 30-day file, and an
        exception list that cries wolf 28 times is one nobody reads.

        ``account_number`` is EXCLUDED from the test, and that exclusion is the
        whole trick. It is required by all three kinds and populated on every
        line of a wide export, so including it makes ``any()`` true on every row
        and the helper never fires. What tells you a row carries a flow is
        ``flow_date``/``amount``, not the account it belongs to. For the account
        kind, which has no other required field, the number itself is the test.
        """
        discriminators = [
            field for field in REQUIRED_FIELDS[kind] if field != "account_number"
        ] or ["account_number"]
        return not any(
            cls._value(row, mapping, field).strip() for field in discriminators
        )

    # ── The interface ─────────────────────────────────────────────────────
    def fetch_accounts(self) -> ParseOutcome:
        outcome = ParseOutcome()
        mapping = self._mapping_for("account")
        if mapping is None:
            return outcome
        number_column = mapping["account_number"]
        seen: set[str] = set()

        for line, row in self._numbered():
            if self._absent(row, mapping, "account"):
                continue
            try:
                number = AccountNumber(self._value(row, mapping, "account_number"))
            except ValueError as exc:
                outcome.errors.append(
                    self.row_error(
                        line, "account", "missing_account_number", str(exc), row,
                        account_number_column=number_column,
                    )
                )
                continue

            # One account per number, however many rows describe it. First
            # occurrence wins; a later row disagreeing about, say, tax status is
            # not an error worth stopping an import over, and the account's
            # identity is the number.
            key = number.reveal()
            if key in seen:
                continue
            seen.add(key)

            try:
                record = AccountRecord(
                    source_row=line,
                    account_number=number,
                    custodian_code=self.custodian_code,
                    primary_entity_ref=parse_text(
                        self._value(row, mapping, "primary_entity_ref")
                    ),
                    household_ref=parse_text(
                        self._value(row, mapping, "household_ref")
                    ),
                    custodian_account_id=parse_text(
                        self._value(row, mapping, "custodian_account_id")
                    ),
                    registration_type=parse_text(
                        self._value(row, mapping, "registration_type")
                    ) or UNKNOWN,
                    tax_status=parse_text(
                        self._value(row, mapping, "tax_status")
                    ) or UNKNOWN,
                    service_model=parse_text(
                        self._value(row, mapping, "service_model")
                    ),
                    advisor_of_record_ref=parse_text(
                        self._value(row, mapping, "advisor_of_record_ref")
                    ),
                    is_billable=parse_bool(
                        self._value(row, mapping, "is_billable") or None,
                        field_name="is_billable", default=True,
                    ),
                    is_discretionary=parse_bool(
                        self._value(row, mapping, "is_discretionary") or None,
                        field_name="is_discretionary", default=True,
                    ),
                    is_held_away=parse_bool(
                        self._value(row, mapping, "is_held_away") or None,
                        field_name="is_held_away", default=False,
                    ),
                    opened_on=self._optional_date(row, mapping, "opened_on"),
                    closed_on=self._optional_date(row, mapping, "closed_on"),
                    base_currency=(
                        parse_text(self._value(row, mapping, "base_currency")) or "USD"
                    ).upper(),
                )
            except ValueError as exc:
                seen.discard(key)   # let a later, well-formed row describe it
                outcome.errors.append(
                    self.row_error(
                        line, "account", "unparseable_account", str(exc), row,
                        account_number_column=number_column,
                    )
                )
                continue
            outcome.records.append(record)
        return outcome

    def fetch_balances(self, as_of: date) -> ParseOutcome:
        """Balances at ``as_of``.

        ``as_of`` is a FILTER when the file carries its own date column and a
        STAMP when it does not. A month of daily balances is one file with a
        date column, so the common call is ``fetch_balances`` per distinct date
        — see ``importer.parse_file``, which asks the adapter for the dates
        present rather than guessing.
        """
        outcome = ParseOutcome()
        mapping = self._mapping_for("balance")
        if mapping is None:
            return outcome
        number_column = mapping["account_number"]
        has_date_column = mapping.get("as_of_date") in self._headers

        for line, row in self._numbered():
            if self._absent(row, mapping, "balance"):
                continue
            try:
                number = AccountNumber(self._value(row, mapping, "account_number"))
                row_date = (
                    parse_date(
                        self._value(row, mapping, "as_of_date"), field_name="as_of_date"
                    )
                    if has_date_column
                    else as_of
                )
                if row_date != as_of:
                    continue
                record = BalanceRecord(
                    source_row=line,
                    account_number=number,
                    as_of_date=row_date,
                    total_market_value=parse_decimal(
                        self._value(row, mapping, "total_market_value"),
                        field_name="total_market_value",
                    ),
                    cash_value=self._optional_money(row, mapping, "cash_value"),
                    margin_balance=self._optional_money(row, mapping, "margin_balance"),
                    accrued_income=self._optional_money(row, mapping, "accrued_income"),
                    source_system=self.source_system,
                    is_billing_source=parse_bool(
                        self._value(row, mapping, "is_billing_source") or None,
                        field_name="is_billing_source", default=False,
                    ),
                    is_final=parse_bool(
                        self._value(row, mapping, "is_final") or None,
                        field_name="is_final", default=False,
                    ),
                )
            except ValueError as exc:
                outcome.errors.append(
                    self.row_error(
                        line, "balance", "unparseable_balance", str(exc), row,
                        account_number_column=number_column,
                    )
                )
                continue
            outcome.records.append(record)
        return outcome

    def fetch_flows(self, from_date: date, to_date: date) -> ParseOutcome:
        outcome = ParseOutcome()
        mapping = self._mapping_for("flow")
        if mapping is None:
            return outcome
        number_column = mapping["account_number"]

        # Occurrence index per (account, date, amount, type). This is what lets
        # two identical $500 deposits on one day both be stored while the same
        # file imported twice stores neither a second time — see the addendum
        # migration's note on why a plain unique index would be wrong.
        occurrences: dict[tuple, int] = {}

        for line, row in self._numbered():
            if self._absent(row, mapping, "flow"):
                continue
            try:
                number = AccountNumber(self._value(row, mapping, "account_number"))
                flow_date = parse_date(
                    self._value(row, mapping, "flow_date"), field_name="flow_date"
                )
                if not (from_date <= flow_date <= to_date):
                    continue
                amount = parse_decimal(
                    self._value(row, mapping, "amount"), field_name="amount"
                )
                flow_type = (
                    parse_text(self._value(row, mapping, "flow_type")) or UNKNOWN
                )
                key = (number.reveal(), flow_date, amount, flow_type)
                occurrence = occurrences.get(key, 0)
                occurrences[key] = occurrence + 1

                record = FlowRecord(
                    source_row=line,
                    account_number=number,
                    flow_date=flow_date,
                    amount=amount,
                    flow_type=flow_type,
                    is_billable_flow=parse_bool(
                        self._value(row, mapping, "is_billable_flow") or None,
                        field_name="is_billable_flow", default=True,
                    ),
                    source_system=self.source_system,
                    occurrence=occurrence,
                )
            except ValueError as exc:
                outcome.errors.append(
                    self.row_error(
                        line, "flow", "unparseable_flow", str(exc), row,
                        account_number_column=number_column,
                    )
                )
                continue
            outcome.records.append(record)
        return outcome

    # ── Dates and money the file may simply not carry ────────────────────
    def balance_dates(self) -> list[date]:
        """Distinct ``as_of_date`` values in the file, ascending.

        Lets the importer iterate the dates the file actually contains instead
        of the caller having to know them, which is what keeps ``fetch_balances``
        honest as a per-date read while a 30-day file still imports in one pass.
        Unparseable dates are ignored here; ``fetch_balances`` reports them as
        row errors so they are surfaced exactly once.
        """
        mapping = self._mapping_for("balance")
        if mapping is None or mapping.get("as_of_date") not in self._headers:
            return []
        seen: set[date] = set()
        for _, row in self._numbered():
            if self._absent(row, mapping, "balance"):
                continue
            try:
                seen.add(
                    parse_date(
                        self._value(row, mapping, "as_of_date"), field_name="as_of_date"
                    )
                )
            except ValueError:
                continue
        return sorted(seen)

    def flow_date_range(self) -> tuple[date, date] | None:
        mapping = self._mapping_for("flow")
        if mapping is None:
            return None
        dates: list[date] = []
        for _, row in self._numbered():
            if self._absent(row, mapping, "flow"):
                continue
            try:
                dates.append(
                    parse_date(
                        self._value(row, mapping, "flow_date"), field_name="flow_date"
                    )
                )
            except ValueError:
                continue
        return (min(dates), max(dates)) if dates else None

    def _optional_date(
        self, row: dict[str, str], mapping: dict[str, str], field: str
    ) -> date | None:
        raw = self._value(row, mapping, field)
        return parse_date(raw, field_name=field) if raw else None

    def _optional_money(
        self, row: dict[str, str], mapping: dict[str, str], field: str
    ):
        from decimal import Decimal

        raw = self._value(row, mapping, field)
        return parse_decimal(raw, field_name=field) if raw else Decimal("0")


register_adapter("csv", CsvCustodyAdapter)
