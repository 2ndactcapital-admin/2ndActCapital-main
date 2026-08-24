"""File-based reporting-tool import — Portfolio Phase B, Task 4.

Black Diamond, Addepar, Orion and APX all export holdings as a flat table:
something naming the security, something measuring it, a date. The column
headers differ, the shape does not. This module reads one such export and turns
each row into a tenant asset and a position, idempotently.

It depends on NO external credential. That is deliberate and is the reason it
exists: Phase B's other ingestion path (Altruist) is blocked on partner access
this project does not have, and a portfolio system whose only way to get data in
is blocked has no way to get data in.

FOUR DECISIONS WORTH KNOWING ABOUT
──────────────────────────────────────────────────────────────────────────────

1. **Parsing reuses Chancery, it does not add a second stack.**
   ``chancery_intake.detect_file_type`` classifies by MAGIC BYTES (an extension
   is only a weak tie-breaker), ``extract_xlsx`` is the existing openpyxl path,
   and ``extract_text`` is the existing UTF-8→latin-1 decoder. CSV goes through
   ``extract_text`` plus the standard library's ``csv`` reader, because Chancery
   has no CSV-specific path to reuse and a CSV genuinely is the text path with a
   delimiter — not a new parsing approach.

2. **Idempotency is a pre-insert READ, not an ON CONFLICT.**
   Each row gets a stable ``external_id`` — the file's own row identifier when
   it has one, otherwise a SHA-256 over the row's normalised meaningful fields.
   Before writing anything, the importer asks
   ``portfolio_assets.find_external_reference`` whether that id already maps to
   a position. If it does, the row is skipped. Writing the position first and
   upserting the mapping afterwards would make the MAPPING idempotent while the
   POSITION duplicated — which is the exact bug the assertion "re-uploading the
   identical file does not create duplicate positions" is there to catch.

   The hash covers the row's meaning and NOT the filename, so the same holdings
   re-sent as ``q2.csv`` and ``q2-final.csv`` is one position, not two. It also
   means a CHANGED row (different quantity) is a different id and imports as a
   new position — correct: that is the source restating itself, and precedence's
   within-source recency rule resolves it.

3. **Money never touches float in this module — but openpyxl already did.**
   ``_json_cell`` hands back numeric XLSX cells as Python ``float``. That
   precision is lost before this module is reached and cannot be recovered.
   :func:`_to_decimal` converts via ``Decimal(str(value))``, which yields the
   shortest decimal that round-trips the float — i.e. the number the
   spreadsheet was displaying — rather than ``Decimal(float)``'s full binary
   expansion. Everything crossing into ``create_position`` is a ``Decimal``.

   The same ``_json_cell`` also stringifies DATE cells (it keeps only
   int/float/bool/str/None as-is), so an XLSX date arrives here as
   ``'2026-06-30 00:00:00'`` rather than as a ``datetime``. :func:`_to_date`
   handles that explicitly — it was found by running the XLSX path, not by
   reading openpyxl's documentation, which describes what openpyxl returns and
   not what survives Chancery's serialisation step.

4. **One bad row does not fail the file.**
   A row that cannot be understood is recorded in ``ImportResult.errors`` with
   its 1-based file line number and a reason, and the import continues. A
   quarter-end export with one malformed line is still 400 good positions, and
   an all-or-nothing import means the operator's only option is to hand-edit a
   custodian's file — which is worse than skipping the row and saying so.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from services.chancery_intake import (
    TYPE_TEXT,
    TYPE_XLSX,
    detect_file_type,
    extract_text,
    extract_xlsx,
)
from services.portfolio_assets import (
    PERCENT,
    UNITS,
    VALUE,
    PortfolioError,
    TABLE_ASSET_IDENT,
    TABLE_ASSETS,
    _OrgWrite,
    _current,
    _require_org,
    add_identifier,
    create_asset,
    create_position,
    find_external_reference,
    normalize_identifier_value,
    upsert_external_reference,
)
from services.portfolio_precedence import resolve_holding

#: What an imported position's ``source_system`` is. Vendor-agnostic on purpose
#: — see docs/portfoliob_part1.sql. Guessing Black Diamond vs Orion from column
#: headers would manufacture provenance the file does not carry.
IMPORT_SOURCE_SYSTEM = "reporting_tool_import"

#: ``authority`` for an imported position. A reporting tool AGGREGATES custodial
#: data; it does not hold the assets. `custodial` would overstate what the file
#: is, and `custodial` is what an actual custodian feed should claim.
IMPORT_AUTHORITY = "aggregated"

#: Default when the file names no asset type. `asset_type` has no CHECK
#: constraint (A2 recorded this — it is open text), so this is a convention, not
#: a vocabulary. Deliberately vague: the file did not say, and inventing
#: "equity" from a ticker-shaped string would be a guess recorded as a fact.
DEFAULT_ASSET_TYPE = "unclassified"


class ImportError_(PortfolioError):
    """The FILE could not be imported at all — not a per-row problem."""


# ── Header vocabulary ───────────────────────────────────────────────────────
# Mapped by normalised header text. Ordered longest-first within each field when
# matching, so "ending market value" is not swallowed by "value".

def _norm_header(text: Any) -> str:
    """Lower-case, collapse punctuation and whitespace to single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "external_id": (
        "external id", "position id", "holding id", "row id", "lot id",
        "record id", "unique id",
    ),
    "asset_name": (
        "asset name", "security name", "security description", "investment name",
        "holding name", "description", "security", "asset", "investment",
        "holding", "name",
    ),
    "ticker": ("ticker", "symbol", "ticker symbol"),
    "cusip": ("cusip",),
    "isin": ("isin",),
    "sedol": ("sedol",),
    "quantity": ("quantity", "units", "shares", "share balance", "qty", "balance"),
    "market_value": (
        "ending market value", "market value base", "market value",
        "current value", "value base", "total value", "mkt value", "value",
    ),
    "cost_basis": ("cost basis", "total cost", "book value", "cost"),
    "ownership_pct": ("ownership pct", "ownership percent", "ownership", "pct owned"),
    "as_of_date": (
        "as of date", "as of", "valuation date", "position date", "report date",
        "date",
    ),
    "currency_code": ("currency code", "currency", "ccy"),
    "account": ("account name", "account number", "account", "portfolio", "owner"),
    "asset_type": ("asset type", "security type", "asset class", "type"),
}

# Longest alias first so a longer, more specific header wins over a substring of
# it. Without this, "market value" and "ending market value" both match a field
# and which one wins depends on dict order — a real source of quiet mis-mapping.
_ALIAS_LOOKUP: list[tuple[str, str]] = sorted(
    ((alias, field_name)
     for field_name, aliases in _HEADER_ALIASES.items()
     for alias in aliases),
    key=lambda pair: -len(pair[0]),
)

#: Identifier columns, in the order they are preferred for asset matching.
#: CUSIP/ISIN before ticker: a ticker is reused across exchanges and over time
#: after a delisting, so it is the weakest of the three as an identity claim.
_IDENTIFIER_FIELDS: tuple[tuple[str, str], ...] = (
    ("cusip", "cusip"),
    ("isin", "isin"),
    ("sedol", "sedol"),
    ("ticker", "ticker"),
)


def map_headers(header_row: Sequence[Any]) -> dict[str, int]:
    """Map our field names onto column indices. Unrecognised columns are ignored.

    Exact normalised match first across the whole row, then substring matching
    for the leftovers. Exact-first matters: a file with both ``Value`` and
    ``Market Value`` columns must bind ``market_value`` to the one whose header
    IS "market value", not to whichever happened to be scanned first.
    """
    normalised = [_norm_header(h) for h in header_row]
    mapping: dict[str, int] = {}
    taken: set[int] = set()

    for alias, field_name in _ALIAS_LOOKUP:
        if field_name in mapping:
            continue
        for idx, header in enumerate(normalised):
            if idx in taken or not header:
                continue
            if header == alias:
                mapping[field_name] = idx
                taken.add(idx)
                break

    for alias, field_name in _ALIAS_LOOKUP:
        if field_name in mapping:
            continue
        for idx, header in enumerate(normalised):
            if idx in taken or not header:
                continue
            if alias in header:
                mapping[field_name] = idx
                taken.add(idx)
                break
    return mapping


# ── Cell parsing ────────────────────────────────────────────────────────────

_CURRENCY_STRIP = re.compile(r"[\s,$£€¥]")
_DATE_FORMATS = (
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%b-%Y", "%d %b %Y",
    "%b %d, %Y", "%Y/%m/%d", "%m/%d/%y",
)


def _to_decimal(value: Any, field_name: str) -> Decimal | None:
    """A spreadsheet cell to ``Decimal``, or ``None`` when the cell is empty.

    Handles the three things exports actually do to numbers: currency symbols
    and thousands separators, accounting-negative parentheses, and trailing
    percent signs. Raises ``ValueError`` on anything else — a cell that is not a
    number is a malformed ROW, and guessing at it is how a text note in a
    quantity column becomes a position.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} is a boolean, not a number")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # See module docstring point 3: the precision is already gone; str()
        # recovers the shortest decimal that round-trips, which is the number
        # the spreadsheet was showing.
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.rstrip("%")
    text = _CURRENCY_STRIP.sub("", text)
    if not text or text in {"-", "--", "n/a", "N/A"}:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} {value!r} is not a number") from exc
    return -parsed if negative else parsed


def _to_date(value: Any, field_name: str) -> date | None:
    """A spreadsheet cell to ``date``, or ``None`` when empty.

    MEASURED, not assumed: openpyxl does hand back real ``datetime`` objects
    for date-formatted cells, but Chancery's ``_json_cell`` — which the XLSX
    path runs every cell through, to keep the extraction JSON-serialisable —
    stringifies anything that is not int/float/bool/str. So a date cell reaches
    this function as ``'2026-06-30 00:00:00'``, NOT as a ``datetime``. The
    ``fromisoformat`` attempt below is the XLSX path's real entry point; the
    ``datetime``/``date`` branches cover a direct caller, and ``_DATE_FORMATS``
    covers CSV, where the date is whatever the exporter chose to print.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"{field_name} {value!r} is not a recognised date "
        f"(tried ISO-8601 and {', '.join(_DATE_FORMATS)})"
    )


# ── File → rows ─────────────────────────────────────────────────────────────


def parse_tabular(file_bytes: bytes, filename: str | None = None) -> list[list[Any]]:
    """Return the file's rows, header included, as a list of cell lists.

    Dispatches on ``chancery_intake.detect_file_type`` — content, not extension.
    A CSV renamed ``.xlsx`` still parses as a CSV, which is the behaviour the
    magic-byte dispatcher exists to give and the reason this does not switch on
    the filename.
    """
    if not file_bytes:
        raise ImportError_("the uploaded file is empty")

    file_type = detect_file_type(file_bytes, filename)
    if file_type == TYPE_XLSX:
        sheets = extract_xlsx(file_bytes)["extracted_tables"]
        if not sheets:
            raise ImportError_("the workbook contains no sheets with any rows")
        # First non-empty sheet. Multi-sheet exports put the holdings first and
        # disclaimers after; picking "the sheet with the most rows" would import
        # a disclaimer tab from a file whose holdings tab happens to be short.
        for sheet in sheets:
            if sheet["rows"]:
                return [list(r) for r in sheet["rows"]]
        raise ImportError_("the workbook contains no sheets with any rows")

    if file_type == TYPE_TEXT:
        text = extract_text(file_bytes)["extracted_text"]
        if not text.strip():
            raise ImportError_("the uploaded file decoded to no text")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel  # a single-column file sniffs as nothing
        return [row for row in csv.reader(io.StringIO(text), dialect) if row]

    raise ImportError_(
        f"unsupported file type {file_type!r} — this endpoint accepts a CSV or "
        f"an XLSX holdings export. The type is detected from the file's own "
        f"bytes, so a mislabelled extension will not change it."
    )


def row_external_id(
    mapping: dict[str, int], cells: Sequence[Any], explicit: str | None
) -> str:
    """The idempotency key for one row.

    An explicit id from the file wins, prefixed so it cannot collide with a
    hash. Otherwise: SHA-256 over the row's MEANINGFUL, normalised fields —
    identifiers, name, date, and the measures — not the raw line. Hashing the
    raw line would make a re-export that merely re-ordered or re-formatted
    columns look like an entirely new set of holdings.
    """
    if explicit:
        return f"row:{explicit}"
    parts: list[str] = []
    for field_name in (
        "asset_name", "cusip", "isin", "sedol", "ticker", "account",
        "as_of_date", "quantity", "market_value", "ownership_pct",
    ):
        idx = mapping.get(field_name)
        raw = cells[idx] if idx is not None and idx < len(cells) else None
        parts.append(f"{field_name}={_norm_header(raw) if raw is not None else ''}")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass
class RowError:
    """One skipped row, and why. Line numbers are 1-based INCLUDING the header,
    so they match what the operator sees when they open the file."""

    line: int
    reason: str
    raw: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    """What an import did. Every count is a real row count, not an estimate."""

    source_system: str = IMPORT_SOURCE_SYSTEM
    total_rows: int = 0
    imported: int = 0
    skipped_duplicate: int = 0
    assets_created: int = 0
    assets_matched: int = 0
    positions: list[str] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    header_mapping: dict[str, int] = field(default_factory=dict)
    resolved_holdings: int = 0

    @property
    def ok(self) -> bool:
        """True when at least one row imported. NOT "no errors" — a file with
        one bad line among four hundred good ones succeeded."""
        return self.imported > 0 or self.skipped_duplicate > 0


# ── Asset matching ──────────────────────────────────────────────────────────


async def _match_asset(
    conn, org_id: str, identifiers: list[tuple[str, str]], name: str | None
) -> str | None:
    """Find an existing tenant asset by identifier, then by exact name.

    Identifier match uses the SAME normalisation the write path uses
    (``normalize_identifier_value``), because an asymmetric lookup silently
    misses — A1 recorded that as the reason the helper is shared rather than
    re-implemented per call site.

    Name matching is EXACT (case-insensitively trimmed) and is the last resort.
    Fuzzy name matching across a portfolio is how "Apple Inc" and "Apple Inc."
    become one asset and how "Blackstone Real Estate Income Trust" and
    "Blackstone Real Estate Partners" become one asset too — and only the second
    of those is a disaster, which is exactly why it is not worth the first.
    """
    async with _OrgWrite(conn, org_id) as c:
        for id_type, raw_value in identifiers:
            try:
                value = normalize_identifier_value(id_type, raw_value)
            except PortfolioError:
                continue
            found = await c.fetchval(
                f"""
                SELECT ai.asset_id::text
                FROM {TABLE_ASSET_IDENT} ai
                JOIN {TABLE_ASSETS} a ON a.id = ai.asset_id
                WHERE ai.org_id = $1::uuid AND ai.id_type = $2
                  AND ai.id_value = $3 AND {_current('ai')} AND {_current('a')}
                LIMIT 1
                """,
                org_id, id_type, value,
            )
            if found:
                return found

        if name and name.strip():
            return await c.fetchval(
                f"""
                SELECT a.id::text FROM {TABLE_ASSETS} a
                WHERE a.org_id = $1::uuid AND lower(btrim(a.name)) = lower($2)
                  AND {_current('a')}
                ORDER BY a.system_from
                LIMIT 1
                """,
                org_id, name.strip(),
            )
    return None


# ── The import ──────────────────────────────────────────────────────────────


async def import_positions_file(
    conn,
    *,
    org_id: str,
    file_bytes: bytes,
    filename: str | None = None,
    owner_entity_id: str,
    as_of_date: date | None = None,
    source_system: str = IMPORT_SOURCE_SYSTEM,
    resolve_precedence_after: bool = True,
) -> ImportResult:
    """Import a reporting-tool holdings export into assets + positions.

    ``org_id`` comes from the caller's JWT claims and never from the request
    body or the file — a file that could name its own tenant would be a
    cross-tenant write primitive uploadable by anyone.

    ``owner_entity_id`` is supplied by the caller, not read from the file's
    account column. Mapping an account NAME in a spreadsheet onto an entity id
    is an entity-resolution problem with its own review queue (Chancery Phase 5
    built one), and doing it implicitly here would attach positions to whichever
    entity happened to match a string. The account column IS read — it goes into
    the row hash, so two accounts' holdings in one file stay distinct rows —
    but it does not select the owner.

    ``as_of_date`` overrides the file's date column, and is REQUIRED when the
    file has no usable date. A defaulted "today" would date a Q2 export to
    whenever it happened to be uploaded.

    When ``resolve_precedence_after`` is set (the default), every holding key
    this import touched is resolved through
    ``portfolio_precedence.resolve_holding`` afterwards. That is what makes an
    import interact correctly with data that was already there — including the
    manual entry it is supposed to supersede, which the importer would otherwise
    never see because it did not write it.
    """
    org_id = _require_org(org_id)
    if not owner_entity_id:
        raise ImportError_("owner_entity_id is required")
    if as_of_date is not None and not isinstance(as_of_date, date):
        raise ImportError_(
            f"as_of_date must be a datetime.date — got {type(as_of_date).__name__}"
        )

    rows = parse_tabular(file_bytes, filename)
    if len(rows) < 2:
        raise ImportError_(
            "the file has no data rows — a header alone is not an import"
        )

    header, data_rows = rows[0], rows[1:]
    mapping = map_headers(header)
    if "asset_name" not in mapping and not any(
        f in mapping for f, _ in _IDENTIFIER_FIELDS
    ):
        raise ImportError_(
            f"no column identifies the security. Expected one of a name column "
            f"({', '.join(_HEADER_ALIASES['asset_name'])}) or an identifier "
            f"column (cusip / isin / sedol / ticker). Headers seen: "
            f"{[str(h) for h in header]}"
        )
    if "quantity" not in mapping and "market_value" not in mapping \
            and "ownership_pct" not in mapping:
        raise ImportError_(
            "no column measures the holding. Expected a quantity, a market "
            f"value or an ownership percentage. Headers seen: "
            f"{[str(h) for h in header]}"
        )

    result = ImportResult(source_system=source_system, header_mapping=dict(mapping))
    touched: set[tuple[str, date]] = set()

    def cell(cells: Sequence[Any], field_name: str) -> Any:
        idx = mapping.get(field_name)
        if idx is None or idx >= len(cells):
            return None
        value = cells[idx]
        return value.strip() if isinstance(value, str) else value

    for offset, cells in enumerate(data_rows):
        line = offset + 2  # 1-based, header is line 1
        raw = [("" if c is None else str(c)) for c in cells]
        if all(not c.strip() for c in raw):
            continue  # a blank spacer line is not a malformed row
        result.total_rows += 1

        try:
            position_id = await _import_row(
                conn,
                org_id=org_id,
                cells=cells,
                cell=cell,
                mapping=mapping,
                owner_entity_id=str(owner_entity_id),
                file_as_of=as_of_date,
                source_system=source_system,
                result=result,
                touched=touched,
            )
        except (PortfolioError, ValueError) as exc:
            result.errors.append(RowError(line=line, reason=str(exc), raw=raw))
            continue
        except Exception as exc:  # noqa: BLE001
            # An unexpected failure is still ONE row's failure. Letting it out
            # would abandon every row after it, which is the all-or-nothing
            # behaviour this importer exists not to have — and the row is
            # recorded with its type so the surprise is visible, not swallowed.
            result.errors.append(
                RowError(line=line, reason=f"{type(exc).__name__}: {exc}", raw=raw)
            )
            continue

        if position_id is not None:
            result.positions.append(position_id)

    if resolve_precedence_after and touched:
        for asset_id, holding_date in sorted(touched):
            outcome = await resolve_holding(
                conn, org_id,
                owner_entity_id=str(owner_entity_id),
                asset_id=asset_id,
                as_of_date=holding_date,
            )
            if outcome is not None:
                result.resolved_holdings += 1

    return result


async def _import_row(
    conn,
    *,
    org_id: str,
    cells: Sequence[Any],
    cell,
    mapping: dict[str, int],
    owner_entity_id: str,
    file_as_of: date | None,
    source_system: str,
    result: ImportResult,
    touched: set[tuple[str, date]],
) -> str | None:
    """Import one row. Returns the new position id, or ``None`` if it was a
    duplicate. Raises ``ValueError``/``PortfolioError`` for a malformed row —
    the caller records it and moves on."""

    name = cell(cells, "asset_name")
    name = str(name).strip() if name not in (None, "") else None

    identifiers: list[tuple[str, str]] = []
    for field_name, id_type in _IDENTIFIER_FIELDS:
        value = cell(cells, field_name)
        if value not in (None, ""):
            identifiers.append((id_type, str(value).strip()))

    if not name and not identifiers:
        raise ValueError(
            "row names no security — it has neither an asset name nor any "
            "identifier (cusip / isin / sedol / ticker)"
        )

    quantity = _to_decimal(cell(cells, "quantity"), "quantity")
    market_value = _to_decimal(cell(cells, "market_value"), "market value")
    ownership_pct = _to_decimal(cell(cells, "ownership_pct"), "ownership pct")
    cost_basis = _to_decimal(cell(cells, "cost_basis"), "cost basis")

    if quantity is None and market_value is None and ownership_pct is None:
        raise ValueError(
            "row measures nothing — quantity, market value and ownership "
            "percentage are all empty"
        )

    as_of = _to_date(cell(cells, "as_of_date"), "as of date") or file_as_of
    if as_of is None:
        raise ValueError(
            "row has no as-of date and none was supplied for the file. A "
            "position dated 'whenever it was uploaded' is not the same fact as "
            "a position dated at quarter end."
        )

    explicit_id = cell(cells, "external_id")
    external_id = row_external_id(
        mapping, cells, str(explicit_id).strip() if explicit_id else None
    )

    existing = await find_external_reference(
        conn, org_id=org_id, source_system=source_system,
        external_id=external_id, record_type="position",
    )
    if existing is not None:
        result.skipped_duplicate += 1
        return None

    asset_id = await _match_asset(conn, org_id, identifiers, name)
    if asset_id is None:
        asset_type = cell(cells, "asset_type")
        currency = cell(cells, "currency_code")
        # The asset's declared basis follows what the file actually measures.
        # An asset created as `units` because that is the schema default, then
        # only ever measured by value, makes every downstream basis check
        # disagree with the data.
        asset_basis = (
            UNITS if quantity is not None
            else PERCENT if ownership_pct is not None
            else VALUE
        )
        asset_id = await create_asset(
            conn,
            org_id=org_id,
            name=name or identifiers[0][1],
            asset_type=(str(asset_type).strip() if asset_type else DEFAULT_ASSET_TYPE),
            ownership_basis=asset_basis,
            currency_code=(str(currency).strip() if currency else None),
        )
        result.assets_created += 1
        for id_type, raw_value in identifiers:
            try:
                await add_identifier(
                    conn, org_id=org_id, asset_id=asset_id,
                    id_type=id_type, id_value=raw_value,
                    is_primary=(id_type == identifiers[0][0]),
                )
            except PortfolioError:
                # A malformed identifier does not invalidate the holding. The
                # asset exists and the position is about to; dropping the whole
                # row over an unusable CUSIP would lose real data to a typo in
                # a column we only use for matching.
                continue
    else:
        result.assets_matched += 1

    # Which measure is AUTHORITATIVE for this row, and therefore which others
    # must be NULL — create_position enforces this and has no database backstop
    # (A2's `_validate_basis`), so the choice is made explicitly here rather
    # than by passing everything and hoping.
    if quantity is not None:
        basis, pct_arg, mv_arg = UNITS, None, market_value
    elif ownership_pct is not None:
        basis, pct_arg, mv_arg = PERCENT, ownership_pct, market_value
    else:
        basis, pct_arg, mv_arg = VALUE, None, market_value

    position_id = await create_position(
        conn,
        org_id=org_id,
        owner_entity_id=owner_entity_id,
        asset_id=asset_id,
        as_of_date=as_of,
        authority=IMPORT_AUTHORITY,
        source_system=source_system,
        ownership_basis=basis,
        quantity=quantity if basis == UNITS else None,
        ownership_pct=pct_arg,
        market_value=mv_arg,
        cost_basis=cost_basis,
    )
    await upsert_external_reference(
        conn,
        org_id=org_id,
        source_system=source_system,
        external_id=external_id,
        record_type="position",
        record_id=position_id,
    )
    result.imported += 1
    touched.add((asset_id, as_of))
    return position_id
