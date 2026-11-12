"""cpsim02 — the simulated custodian's BOOK: validate an upload (D8), write it (D7, replace mode), serve it (D9), export it.

Every function takes a connection already inside a transaction AS ``cpsim_app`` (cpsim.db.tx / as_simulator). Rows arrive
as plain dicts (the admin router parses CSV / JSON and hands them here — the package never parses a file format it did
not define, and imports nothing from services/ but the two shared helpers).

Validation (D8) — a row is ACCEPTED or REJECTED with its reason, never guessed:
  * ids: given → ``^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$``, unique in the file and among the counterparty's live rows that
    this upload does not replace; blank → a stable generated id ``sim-<16 hex>`` (same content → same id);
  * a security row names ONE security in ``portfolio.securities_global`` through ``…_identifiers`` (CUSIP, ISIN, ticker
    — the order the client prefers, contract T6): any identifier that matches nothing → "unknown", more than one live
    security → "ambiguous", two identifiers naming different securities → "identifiers disagree";
  * a cash row (``row_type = cash``) is the documented exception: no identifier, no security, a market value;
  * ``market_value`` blank → quantity × the last PLATFORM price (``org_id IS NULL``) on or before the as-of date, else
    "no price";
  * a transaction ``type`` is one the client maps (its 7 + cpsim04's 7 cash types); each transacted security must have a position in the same account
    (the client records a transaction only against a position — contract §3.4);
  * dates ISO ``YYYY-MM-DD``; numbers decimal strings or JSON numbers; currency three letters.

The book is VALID-axis (Rule 3): replace mode closes the live rows of every account (households file: every household)
the file names and inserts the accepted rows; the upload log row is append-only (a trigger refuses UPDATE / DELETE).

ALTREAD1 — the book is SERVED in Altruist's PUBLISHED shapes (developer.altruist.com, read 2026-11-11): documented field
names, ``YYYY/MM/DD`` dates, accounts with ``account_balance`` (the account's latest positions' market value on or
before the clock — derived; the book stores no balance), ``status`` ACTIVE, ``rep_code`` null, a stable synthetic
``account_number``; positions with ``long_short_indicator``; transactions with ``transaction_type`` (upper-cased),
``transaction_date``, the three fee fields; and the account's TAX LOTS (``cost_basis``): uploaded lots (kind ``lots``,
``cpsim.book_lots``) where the account has any, else ONE lot per security position derived from it (quantity, market
value, cost basis → both bases, opened on the account's earliest buy of that security in the book, else the
position's as-of date). Uploads still take ISO dates (the template).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

KINDS = ("households", "accounts", "positions", "transactions", "lots")
#: the client's 7 + cpsim04 D1 / D2: the cash types the client now maps (transfer / ACH / wire legs, ACH returns)
TXN_TYPES = ("buy", "sell", "dividend", "interest", "fee", "fee_expense", "adjustment",
             "transfer_in", "transfer_out", "ach_in", "ach_out", "wire_in", "wire_out", "ach_return")
ROW_TYPES = ("security", "cash")
ID_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUM_RX = re.compile(r"^-?\d+(\.\d+)?$")
CCY_RX = re.compile(r"^[A-Za-z]{3}$")
IDENT_ORDER = ("cusip", "isin", "ticker")
DEFAULT_CCY = "USD"
MAX_ROWS = 5000
MONEY = Decimal("0.01")

#: the import / export columns per kind — the template, the CSV export and the round trip all use exactly these
COLUMNS = {
    "households": ("household_id", "name"),
    "accounts": ("account_id", "household_id", "name", "account_type"),
    "positions": ("position_id", "account_id", "as_of", "row_type", "cusip", "isin", "ticker", "name", "asset_type",
                  "quantity", "market_value", "cost_basis", "currency_code"),
    "transactions": ("transaction_id", "account_id", "type", "trade_date", "settle_date", "row_type", "cusip", "isin",
                     "ticker", "name", "quantity", "price", "gross_amount", "fees", "taxes", "net_amount",
                     "currency_code"),
    # ALTREAD1 — one row per TAX LOT (served at /v2/accounts/{id}/cost-basis/)
    "lots": ("lot_id", "account_id", "as_of", "cusip", "isin", "ticker", "name", "quantity", "market_value",
             "cost_basis_unadjusted", "cost_basis_adjusted", "open_date", "currency_code"),
}
REQUIRED = {
    "households": ("name",),
    "accounts": ("household_id", "name"),
    "positions": ("account_id", "as_of"),
    "transactions": ("account_id", "type", "trade_date"),
    "lots": ("account_id", "as_of", "quantity", "cost_basis_adjusted", "open_date"),
}
ID_COLUMN = {"households": "household_id", "accounts": "account_id", "positions": "position_id",
             "transactions": "transaction_id", "lots": "lot_id"}
TABLE = {"households": "cpsim.book_households", "accounts": "cpsim.book_accounts", "positions": "cpsim.book_positions",
         "transactions": "cpsim.book_transactions", "lots": "cpsim.book_lots"}
#: the reasons a row can be refused (the admin page's rejection table groups by these keys)
REASONS = {
    "missing": "a required column is blank",
    "bad_id": "the id is not a valid identifier",
    "duplicate_id": "the id appears twice in this file",
    "id_in_use": "the id is already used by a live row this upload does not replace",
    "unknown_household": "no such household in this counterparty's book",
    "unknown_account": "no such account in this counterparty's book",
    "bad_date": "a date is not ISO YYYY-MM-DD",
    "bad_number": "a number is not a decimal",
    "bad_currency": "the currency is not three letters",
    "bad_row_type": "row_type must be security or cash",
    "no_identifier": "a security row names no CUSIP, ISIN or ticker",
    "cash_with_identifier": "a cash row must not name a security",
    "unknown_identifier": "the identifier matches no security in the security master",
    "ambiguous_identifier": "the identifier matches more than one security",
    "identifiers_disagree": "the identifiers name different securities",
    "no_price": "no market value and no platform price on or before the as-of date",
    "no_quantity": "a security position needs a quantity",
    "bad_type": "the transaction type is not one the client maps",
    "no_position": "no position for this security in the same account",
    "bad_dates": "settle_date is before trade_date",
    "unknown_column": "the row carries a column the template does not define",
    "bad_quantity": "a lot's quantity must be greater than zero",
    "lot_dates": "a lot's open_date is after its as-of date",
}


class BookError(Exception):
    status = 400


# ── small parsers (strict on purpose: the simulator accepts only what the contract says) ────────────────────────────
def _txt(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(v) -> Decimal | None:
    """A JSON number or a decimal string; None when blank. Raises ValueError otherwise (never float artefacts)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise ValueError("boolean is not a number")
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    s = str(v).strip()
    if not NUM_RX.match(s):
        raise ValueError(f"{s!r} is not a decimal")
    try:
        return Decimal(s)
    except InvalidOperation as exc:  # pragma: no cover — the regex already rules this out
        raise ValueError(str(exc)) from exc


def _date(v) -> date | None:
    s = _txt(v)
    if s is None:
        return None
    if not DATE_RX.match(s):
        raise ValueError(f"{s!r} is not YYYY-MM-DD")
    return date.fromisoformat(s)


def fmt(d) -> str | None:
    """A Decimal as a plain decimal string (no exponent); None stays None."""
    if d is None:
        return None
    d = Decimal(d)
    s = format(d.normalize(), "f") if d == d.to_integral() else format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def generated_id(counterparty_id, kind: str, *parts) -> str:
    basis = "|".join([str(counterparty_id), kind, *[str(p) for p in parts]])
    return "sim-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


# ── security master (read-only reference data, via cpsim_app's SELECT grants) ─────────────────────────────────────
async def resolve_security(conn, row: dict) -> tuple[str | None, str | None, dict | None]:
    """(security_id, reason_key, detail) for a security row. Never guesses."""
    given = [(t, _txt(row.get(t))) for t in IDENT_ORDER if _txt(row.get(t))]
    if not given:
        return None, "no_identifier", None
    found: dict[str, str] = {}
    for id_type, value in given:
        ids = [str(r["id"]) for r in await conn.fetch(
            "SELECT DISTINCT s.id FROM portfolio.securities_global_identifiers i JOIN portfolio.securities_global s "
            "ON s.id = i.global_security_id WHERE i.id_type = $1 AND upper(i.id_value) = upper($2) "
            "AND i.valid_to IS NULL AND i.system_to IS NULL AND s.valid_to IS NULL AND s.system_to IS NULL "
            "AND s.merged_into_id IS NULL", id_type, value)]
        if not ids:
            return None, "unknown_identifier", {"id_type": id_type, "value": value}
        if len(ids) > 1:
            return None, "ambiguous_identifier", {"id_type": id_type, "value": value, "matches": len(ids)}
        found[id_type] = ids[0]
    if len(set(found.values())) > 1:
        return None, "identifiers_disagree", {"given": [t for t, _ in given]}
    return next(iter(found.values())), None, None


async def security_row(conn, security_id: str) -> dict:
    r = await conn.fetchrow("SELECT id, name, security_type, currency_code FROM portfolio.securities_global "
                            "WHERE id = $1::uuid", security_id)
    return dict(r) if r else {}


async def platform_price(conn, security_id: str, as_of: date) -> Decimal | None:
    return await conn.fetchval(
        "SELECT price FROM portfolio.securities_global_prices WHERE global_security_id = $1::uuid AND org_id IS NULL "
        "AND price_date <= $2 AND valid_to IS NULL AND system_to IS NULL AND price_type = 'close' "
        "ORDER BY price_date DESC LIMIT 1", security_id, as_of)


# ── validation ────────────────────────────────────────────────────────────────────────────────────────────────────
async def _live_ids(conn, counterparty_id, kind: str) -> dict[str, str]:
    """external id → the account (or household) that owns it, among the live rows."""
    owner = {"households": "external_id", "accounts": "external_id", "positions": "account_external_id",
             "transactions": "account_external_id", "lots": "account_external_id"}[kind]
    return {r["external_id"]: r["owner"] for r in await conn.fetch(
        f"SELECT external_id, {owner} AS owner FROM {TABLE[kind]} WHERE counterparty_id = $1::uuid "
        f"AND valid_to IS NULL", str(counterparty_id))}


async def _live_set(conn, counterparty_id, table: str) -> set[str]:
    return {r["external_id"] for r in await conn.fetch(
        f"SELECT external_id FROM {table} WHERE counterparty_id = $1::uuid AND valid_to IS NULL", str(counterparty_id))}


def _reject(rejected: list, index: int, rid, reason: str, detail=None) -> None:
    rejected.append({"row": index, "id": rid, "reason": reason, "message": REASONS[reason],
                     **({"detail": detail} if detail else {})})


async def validate(conn, counterparty_id, kind: str, rows: list[dict]) -> dict:
    """The PREVIEW: ``{"kind", "accepted": [normalized rows], "rejected": [{row, id, reason, message, detail}],
    "accounts_named": [...], "counts": {...}}``. Writes nothing."""
    if kind not in KINDS:
        raise BookError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")
    if not isinstance(rows, list):
        raise BookError("rows must be a list")
    if len(rows) > MAX_ROWS:
        raise BookError(f"at most {MAX_ROWS} rows per upload")
    cols = set(COLUMNS[kind])
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    households = await _live_set(conn, counterparty_id, TABLE["households"]) if kind == "accounts" else set()
    accounts = await _live_set(conn, counterparty_id, TABLE["accounts"]) \
        if kind in ("positions", "transactions", "lots") else set()
    # positions this upload can lean on: the live book's (any as-of) — transactions and lots check against it
    held: dict[str, set] = {}
    if kind in ("transactions", "lots"):
        for r in await conn.fetch("SELECT account_external_id, row_type, security_id, lower(name) AS name "
                                  "FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND valid_to IS NULL",
                                  str(counterparty_id)):
            key = str(r["security_id"]) if r["row_type"] == "security" else f"cash:{r['name']}"
            held.setdefault(r["account_external_id"], set()).add(key)

    # which owners the file names — an id already owned by one of them may be re-used (it is being replaced)
    owner_col = "household_id" if kind == "households" else "account_id"
    named = sorted({_txt(r.get(owner_col)) for r in rows if isinstance(r, dict) and _txt(r.get(owner_col))})
    live = await _live_ids(conn, counterparty_id, kind)

    for i, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            _reject(rejected, i, None, "missing", {"column": "(row is not an object)"})
            continue
        extra = sorted(set(raw) - cols)
        if extra:
            _reject(rejected, i, _txt(raw.get(ID_COLUMN[kind])), "unknown_column", {"columns": extra})
            continue
        missing = [c for c in REQUIRED[kind] if not _txt(raw.get(c))]
        rid = _txt(raw.get(ID_COLUMN[kind]))
        if missing:
            _reject(rejected, i, rid, "missing", {"columns": missing})
            continue
        if rid is not None and not ID_RX.match(rid):
            _reject(rejected, i, rid, "bad_id")
            continue
        try:
            out = await _normalize(conn, counterparty_id, kind, raw, households, accounts, held)
        except _Refused as r:
            _reject(rejected, i, rid, r.reason, r.detail)
            continue
        if rid is None:
            rid = out.pop("_generated_id")
        else:
            out.pop("_generated_id", None)
        if rid in seen:
            _reject(rejected, i, rid, "duplicate_id")
            continue
        if rid in live and live[rid] not in named and kind in ("positions", "transactions", "lots"):
            _reject(rejected, i, rid, "id_in_use", {"owner": live[rid]})
            continue
        seen.add(rid)
        out[ID_COLUMN[kind]] = rid
        accepted.append({"row": i, **out})
    # the REPLACE scope: the owners with at least one ACCEPTED row (a fully rejected file never empties the book)
    named = sorted({a["household_id"] if kind == "households" else a["account_id"] for a in accepted})
    return {"kind": kind, "accepted": accepted, "rejected": rejected, "accounts_named": named,
            "counts": {"rows": len(rows), "accepted": len(accepted), "rejected": len(rejected)}}


class _Refused(Exception):
    def __init__(self, reason: str, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _n(raw: dict, col: str):
    try:
        return _num(raw.get(col))
    except ValueError as exc:
        raise _Refused("bad_number", {"column": col, "error": str(exc)[:120]}) from exc


def _d(raw: dict, col: str):
    try:
        return _date(raw.get(col))
    except ValueError as exc:
        raise _Refused("bad_date", {"column": col}) from exc


def _ccy(raw: dict, fallback: str | None) -> str:
    c = _txt(raw.get("currency_code"))
    if c is None:
        return (fallback or DEFAULT_CCY).upper()
    if not CCY_RX.match(c):
        raise _Refused("bad_currency", {"value": c[:10]})
    return c.upper()


async def _normalize(conn, counterparty_id, kind, raw, households, accounts, held) -> dict:
    if kind == "households":
        name = _txt(raw.get("name"))
        return {"name": name[:200], "_generated_id": generated_id(counterparty_id, kind, name)}
    if kind == "accounts":
        hh = _txt(raw.get("household_id"))
        if hh not in households:
            raise _Refused("unknown_household", {"household_id": hh})
        name = _txt(raw.get("name"))
        return {"household_id": hh, "name": name[:200], "account_type": (_txt(raw.get("account_type")) or None),
                "_generated_id": generated_id(counterparty_id, kind, hh, name)}
    acct = _txt(raw.get("account_id"))
    if acct not in accounts:
        raise _Refused("unknown_account", {"account_id": acct})
    row_type = "security" if kind == "lots" else (_txt(raw.get("row_type")) or "security").lower()
    if row_type not in ROW_TYPES:
        raise _Refused("bad_row_type")
    idents = {t: _txt(raw.get(t)) for t in IDENT_ORDER}
    sec_id, sec = None, {}
    if row_type == "cash":
        if any(idents.values()):
            raise _Refused("cash_with_identifier")
    else:
        sec_id, why, detail = await resolve_security(conn, raw)
        if why:
            raise _Refused(why, detail)
        sec = await security_row(conn, sec_id)
    if kind == "positions":
        as_of = _d(raw, "as_of")
        qty, mv, cost = _n(raw, "quantity"), _n(raw, "market_value"), _n(raw, "cost_basis")
        price_source = "uploaded"
        if row_type == "cash":
            if mv is None:
                raise _Refused("missing", {"columns": ["market_value"]})
            price_source = "cash"
        else:
            if qty is None:
                raise _Refused("no_quantity")
            if mv is None:
                px = await platform_price(conn, sec_id, as_of)
                if px is None:
                    raise _Refused("no_price", {"as_of": as_of.isoformat()})
                mv = (qty * Decimal(px)).quantize(MONEY, rounding=ROUND_HALF_UP)
                price_source = "platform_price"
        name = _txt(raw.get("name")) or (sec.get("name") if sec else "Cash")
        ccy = _ccy(raw, sec.get("currency_code") if sec else None)
        key = sec_id if sec_id else f"cash:{ccy}:{name.lower()}"
        return {"account_id": acct, "as_of": as_of, "row_type": row_type, "security_id": sec_id,
                "cusip": idents["cusip"], "isin": idents["isin"], "ticker": idents["ticker"], "name": name[:200],
                "asset_type": _txt(raw.get("asset_type")) or (sec.get("security_type") if sec else "cash"),
                "quantity": qty if row_type == "security" else None, "market_value": mv, "cost_basis": cost,
                "currency_code": ccy, "price_source": price_source,
                "_generated_id": generated_id(counterparty_id, kind, acct, as_of.isoformat(), key)}
    if kind == "lots":
        as_of, opened = _d(raw, "as_of"), _d(raw, "open_date")
        qty, mv = _n(raw, "quantity"), _n(raw, "market_value")
        adj, unadj = _n(raw, "cost_basis_adjusted"), _n(raw, "cost_basis_unadjusted")
        if qty is None or qty <= 0:
            raise _Refused("bad_quantity")
        if opened > as_of:
            raise _Refused("lot_dates")
        if sec_id not in held.get(acct, set()):
            raise _Refused("no_position", {"account_id": acct})
        if mv is None:
            px = await platform_price(conn, sec_id, as_of)
            mv = None if px is None else (qty * Decimal(px)).quantize(MONEY, rounding=ROUND_HALF_UP)
        name = _txt(raw.get("name")) or sec.get("name") or ""
        return {"account_id": acct, "as_of": as_of, "security_id": sec_id, "cusip": idents["cusip"],
                "isin": idents["isin"], "ticker": idents["ticker"], "name": name[:200], "quantity": qty,
                "market_value": mv, "cost_basis_unadjusted": unadj if unadj is not None else adj,
                "cost_basis_adjusted": adj, "open_date": opened,
                "currency_code": _ccy(raw, sec.get("currency_code") if sec else None),
                "_generated_id": generated_id(counterparty_id, kind, acct, as_of.isoformat(), sec_id,
                                              opened.isoformat(), fmt(qty), fmt(adj))}
    # transactions
    typ = (_txt(raw.get("type")) or "").lower()
    if typ not in TXN_TYPES:
        raise _Refused("bad_type", {"type": (_txt(raw.get("type")) or "")[:40]})
    trade, settle = _d(raw, "trade_date"), _d(raw, "settle_date")
    if settle is not None and settle < trade:
        raise _Refused("bad_dates")
    name = _txt(raw.get("name")) or (sec.get("name") if sec else "Cash")
    key = sec_id if sec_id else f"cash:{name.lower()}"
    if key not in held.get(acct, set()):
        raise _Refused("no_position", {"account_id": acct})
    nums = {c: _n(raw, c) for c in ("quantity", "price", "gross_amount", "fees", "taxes", "net_amount")}
    ccy = _ccy(raw, sec.get("currency_code") if sec else None)
    return {"account_id": acct, "type": typ, "trade_date": trade, "settle_date": settle, "row_type": row_type,
            "security_id": sec_id, "cusip": idents["cusip"], "isin": idents["isin"], "ticker": idents["ticker"],
            "name": name[:200], **nums, "currency_code": ccy,
            "_generated_id": generated_id(counterparty_id, kind, acct, typ, trade.isoformat(), key,
                                          *[fmt(nums[c]) for c in ("quantity", "price", "gross_amount")])}


# ── write (ACCEPT): one transaction, replace mode, append-only log ─────────────────────────────────────────────────
async def accept(conn, counterparty_id, kind: str, rows: list[dict], *, file_name: str, sha256: str, fmt_: str,
                 actor) -> dict:
    """Re-validates (the preview is never trusted), logs the upload, closes the live rows of every named owner and
    inserts the accepted rows. Returns ``{"upload_id", "counts", "rejected", "accounts_affected"}``."""
    v = await validate(conn, counterparty_id, kind, rows)
    cid = str(counterparty_id)
    upload_id = str(await conn.fetchval(
        "INSERT INTO cpsim.book_uploads (counterparty_id, uploaded_by, file_name, sha256, kind, format, mode, "
        "rows_accepted, rows_rejected, rejections, accounts_named) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, "
        "'replace', $7, $8, $9::jsonb, $10::text[]) RETURNING id", cid, str(actor) if actor else None,
        file_name[:255], sha256, kind, fmt_, v["counts"]["accepted"], v["counts"]["rejected"],
        json.dumps(v["rejected"], default=str), v["accounts_named"]))
    named = v["accounts_named"]
    if kind in ("households", "accounts"):
        await conn.execute(f"UPDATE {TABLE[kind]} SET valid_to = now() WHERE counterparty_id = $1::uuid "
                           f"AND external_id = ANY($2::text[]) AND valid_to IS NULL", cid, named)
    else:
        await conn.execute(f"UPDATE {TABLE[kind]} SET valid_to = now() WHERE counterparty_id = $1::uuid "
                           f"AND account_external_id = ANY($2::text[]) AND valid_to IS NULL", cid, named)
    for r in v["accepted"]:
        if kind == "households":
            await conn.execute("INSERT INTO cpsim.book_households (counterparty_id, external_id, name, upload_id) "
                               "VALUES ($1::uuid, $2, $3, $4::uuid)", cid, r["household_id"], r["name"], upload_id)
        elif kind == "accounts":
            await conn.execute("INSERT INTO cpsim.book_accounts (counterparty_id, external_id, household_external_id, "
                               "name, account_type, upload_id) VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid)", cid,
                               r["account_id"], r["household_id"], r["name"], r["account_type"], upload_id)
        elif kind == "lots":
            await conn.execute(
                "INSERT INTO cpsim.book_lots (counterparty_id, account_external_id, external_id, as_of, security_id, "
                "cusip, isin, ticker, name, quantity, market_value, cost_basis_unadjusted, cost_basis_adjusted, "
                "open_date, currency_code, upload_id) VALUES ($1::uuid, $2, $3, $4, $5::uuid, $6, $7, $8, $9, "
                "$10::numeric, $11::numeric, $12::numeric, $13::numeric, $14, $15, $16::uuid)", cid, r["account_id"],
                r["lot_id"], r["as_of"], r["security_id"], r["cusip"], r["isin"], r["ticker"], r["name"],
                _s(r["quantity"]), _s(r["market_value"]), _s(r["cost_basis_unadjusted"]), _s(r["cost_basis_adjusted"]),
                r["open_date"], r["currency_code"], upload_id)
        elif kind == "positions":
            await conn.execute(
                "INSERT INTO cpsim.book_positions (counterparty_id, account_external_id, external_id, as_of, row_type, "
                "security_id, cusip, isin, ticker, name, asset_type, quantity, market_value, cost_basis, "
                "currency_code, price_source, upload_id) VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, $7, $8, $9, $10, "
                "$11, $12::numeric, $13::numeric, $14::numeric, $15, $16, $17::uuid)", cid, r["account_id"],
                r["position_id"], r["as_of"], r["row_type"], r["security_id"], r["cusip"], r["isin"], r["ticker"],
                r["name"], r["asset_type"], _s(r["quantity"]), _s(r["market_value"]), _s(r["cost_basis"]),
                r["currency_code"], r["price_source"], upload_id)
        else:
            await conn.execute(
                "INSERT INTO cpsim.book_transactions (counterparty_id, account_external_id, external_id, type, "
                "trade_date, settle_date, row_type, security_id, cusip, isin, ticker, name, quantity, price, "
                "gross_amount, fees, taxes, net_amount, currency_code, upload_id) VALUES ($1::uuid, $2, $3, $4, $5, "
                "$6, $7, $8::uuid, $9, $10, $11, $12, $13::numeric, $14::numeric, $15::numeric, $16::numeric, "
                "$17::numeric, $18::numeric, $19, $20::uuid)", cid, r["account_id"], r["transaction_id"], r["type"],
                r["trade_date"], r["settle_date"], r["row_type"], r["security_id"], r["cusip"], r["isin"],
                r["ticker"], r["name"], _s(r["quantity"]), _s(r["price"]), _s(r["gross_amount"]), _s(r["fees"]),
                _s(r["taxes"]), _s(r["net_amount"]), r["currency_code"], upload_id)
    affected = named if kind in ("positions", "transactions", "lots") else []
    return {"upload_id": upload_id, "kind": kind, "counts": v["counts"], "rejected": v["rejected"],
            "accounts_affected": affected}


def _s(d) -> str | None:
    return None if d is None else format(Decimal(d), "f")


async def uploads(conn, counterparty_id, limit: int = 50) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        "SELECT id, uploaded_by, uploaded_at, file_name, sha256, kind, format, mode, rows_accepted, rows_rejected, "
        "rejections, accounts_named FROM cpsim.book_uploads WHERE counterparty_id = $1::uuid "
        "ORDER BY uploaded_at DESC LIMIT $2", str(counterparty_id), int(limit))]


async def counts(conn, counterparty_id) -> dict:
    out = {}
    for k, t in TABLE.items():
        out[k] = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE counterparty_id = $1::uuid AND valid_to IS NULL",
                                     str(counterparty_id))
    return out


# ── serve (D9 → ALTREAD1): Altruist's PUBLISHED shapes — documented field names, explicit ids, YYYY/MM/DD dates ─────
def doc_date(d) -> str | None:
    """A date in the documented response form ``YYYY/MM/DD`` (json-code-examples)."""
    return d.strftime("%Y/%m/%d") if d else None


def account_number(counterparty_id, external_id: str) -> str:
    """A stable synthetic 9-digit account number (the book stores none; the client keeps only its last four)."""
    h = hashlib.sha256(f"{counterparty_id}|acct|{external_id}".encode("utf-8")).hexdigest()
    return str(int(h[:12], 16) % 900_000_000 + 100_000_000)


def security_type(row_type: str | None, asset_type: str | None) -> str:
    """The documented ``security_type`` (upper case): CASH for a cash row, else the book's own type upper-cased."""
    if row_type == "cash":
        return "CASH"
    return (asset_type or "OTHER").strip().upper().replace(" ", "_") or "OTHER"


async def _idents(conn, security_ids: list) -> dict[str, dict]:
    """CUSIP + ticker per security from the security master — ONE query for a whole response (a row uploaded with only
    an ISIN is still served with the identifiers the documented shape carries)."""
    ids = sorted({str(x) for x in security_ids if x})
    if not ids:
        return {}
    out: dict[str, dict] = {}
    for r in await conn.fetch(
            "SELECT global_security_id::text AS id, id_type, id_value FROM portfolio.securities_global_identifiers "
            "WHERE global_security_id = ANY($1::uuid[]) AND id_type IN ('cusip', 'ticker') AND valid_to IS NULL "
            "AND system_to IS NULL ORDER BY is_primary DESC NULLS LAST, id_value", ids):
        out.setdefault(r["id"], {}).setdefault(r["id_type"], r["id_value"])
    for r in await conn.fetch("SELECT id::text AS id, security_type FROM portfolio.securities_global WHERE id = "
                              "ANY($1::uuid[])", ids):
        out.setdefault(r["id"], {})["security_type"] = r["security_type"]
    return out


async def households(conn, counterparty_id, name_pattern: str | None = None) -> list[dict]:
    """DOCUMENTED fields: id, name. ``name_pattern`` — the docs do not define its language; the simulator treats it as
    a case-insensitive substring."""
    return [{"id": r["external_id"], "name": r["name"]} for r in await conn.fetch(
        "SELECT external_id, name FROM cpsim.book_households WHERE counterparty_id = $1::uuid AND valid_to IS NULL "
        "AND ($2::text IS NULL OR position(lower($2::text) in lower(name)) > 0) ORDER BY external_id",
        str(counterparty_id), name_pattern or None)]


_ACCOUNTS_SQL = """
SELECT a.external_id, a.name, a.household_external_id, a.account_type, snap.as_of AS balance_as_of,
       (SELECT sum(p.market_value) FROM cpsim.book_positions p WHERE p.counterparty_id = a.counterparty_id
         AND p.account_external_id = a.external_id AND p.valid_to IS NULL AND p.as_of = snap.as_of) AS balance
FROM cpsim.book_accounts a
LEFT JOIN LATERAL (SELECT max(q.as_of) AS as_of FROM cpsim.book_positions q WHERE q.counterparty_id = a.counterparty_id
                   AND q.account_external_id = a.external_id AND q.valid_to IS NULL AND q.as_of <= $3) snap ON true
WHERE a.counterparty_id = $1::uuid AND a.valid_to IS NULL AND ($2::text IS NULL OR a.household_external_id = $2::text)
ORDER BY a.external_id
"""


async def accounts(conn, counterparty_id, household_id: str | None, clock: date | None = None) -> list[dict]:
    """DOCUMENTED fields: id, rep_code, account_number, account_type, account_name, status, date_open, date_closed,
    as_of_date, account_balance. The balance is DERIVED (the latest positions' market value on or before the clock)."""
    clock = clock or date.today()
    rows = await conn.fetch(_ACCOUNTS_SQL, str(counterparty_id), household_id, clock)
    return [{"id": r["external_id"], "rep_code": None, "account_number": account_number(counterparty_id,
                                                                                       r["external_id"]),
             "account_type": (r["account_type"] or "").upper() or None, "account_name": r["name"],
             "status": "ACTIVE", "date_open": None, "date_closed": None,
             "as_of_date": doc_date(r["balance_as_of"] or clock),
             "account_balance": fmt(r["balance"]) if r["balance"] is not None else "0",
             "household_id": r["household_external_id"]} for r in rows]


async def account_exists(conn, counterparty_id, account_id: str) -> bool:
    return bool(await conn.fetchval("SELECT EXISTS (SELECT 1 FROM cpsim.book_accounts WHERE counterparty_id = "
                                    "$1::uuid AND external_id = $2 AND valid_to IS NULL)", str(counterparty_id),
                                    account_id))


def position_out(r, ids: dict | None = None) -> dict:
    """DOCUMENTED fields: id, cusip, symbol, description, long_short_indicator, currency_code, quantity, as_of_date,
    security_type, market_value."""
    sec = (ids or {}).get(str(r["security_id"])) if r["security_id"] else {}
    qty = r["quantity"]
    return {"id": r["external_id"], "cusip": r["cusip"] or (sec or {}).get("cusip"),
            "symbol": r["ticker"] or (sec or {}).get("ticker"), "description": r["name"],
            "long_short_indicator": "S" if (qty is not None and qty < 0) else "L",
            "currency_code": r["currency_code"], "quantity": fmt(qty), "as_of_date": doc_date(r["as_of"]),
            "security_type": security_type(r["row_type"], r["asset_type"]), "market_value": fmt(r["market_value"])}


def transaction_out(r, ids: dict | None = None) -> dict:
    """DOCUMENTED fields: id, cusip, symbol, security_type, quantity, price, amount, interest, currency_code,
    transaction_type, transaction_subtype, transaction_description, cancellation_reversal_flag, transaction_fees,
    broker_fees, other_fees, TEFRA_withheld, state_tax_withheld, as_of_date, transaction_date, settle_date. The book
    keeps ONE fee and ONE tax column: served as transaction_fees / TEFRA_withheld, the other parts "0"."""
    sec = (ids or {}).get(str(r["security_id"])) if r["security_id"] else {}
    amount = r["net_amount"] if r["net_amount"] is not None else r["gross_amount"]
    return {"id": r["external_id"], "cusip": r["cusip"] or (sec or {}).get("cusip"),
            "symbol": r["ticker"] or (sec or {}).get("ticker"),
            "security_type": security_type(r["row_type"], (sec or {}).get("security_type")),
            "quantity": fmt(r["quantity"]), "price": fmt(r["price"]), "amount": fmt(amount), "interest": None,
            "currency_code": r["currency_code"], "transaction_type": (r["type"] or "").upper(),
            "transaction_subtype": None, "transaction_description": r["name"],
            "cancellation_reversal_flag": "N",
            "transaction_fees": fmt(r["fees"]) or "0", "broker_fees": "0", "other_fees": "0",
            "TEFRA_withheld": fmt(r["taxes"]) or "0", "state_tax_withheld": "0",
            "as_of_date": doc_date(r["trade_date"]), "transaction_date": doc_date(r["trade_date"]),
            "settle_date": doc_date(r["settle_date"])}


async def positions(conn, counterparty_id, account_id: str, clock: date) -> list[dict]:
    """The latest snapshot on or before the counterparty's clock date."""
    rows = await conn.fetch(
        "SELECT * FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND account_external_id = $2 "
        "AND valid_to IS NULL AND as_of = (SELECT max(as_of) FROM cpsim.book_positions WHERE counterparty_id = "
        "$1::uuid AND account_external_id = $2 AND valid_to IS NULL AND as_of <= $3) ORDER BY external_id",
        str(counterparty_id), account_id, clock)
    ids = await _idents(conn, [r["security_id"] for r in rows])
    return [position_out(r, ids) for r in rows]


def default_window(clock: date) -> tuple[date, date]:
    """The documented default transactions window: the past month (one calendar month back from the clock)."""
    import calendar

    m = clock.month - 1 - 1
    y, m = clock.year + m // 12, m % 12 + 1
    return date(y, m, min(clock.day, calendar.monthrange(y, m)[1])), clock


async def transactions(conn, counterparty_id, account_id: str, clock: date, date_from: date | None = None,
                       date_to: date | None = None) -> list[dict]:
    """transaction_date in [date_from, date_to] (default: the past month) and never after the clock."""
    lo, hi = default_window(clock)
    lo = date_from or lo
    hi = min(date_to or hi, clock)
    rows = await conn.fetch(
        "SELECT * FROM cpsim.book_transactions WHERE counterparty_id = $1::uuid AND account_external_id = $2 "
        "AND valid_to IS NULL AND trade_date >= $3 AND trade_date <= $4 ORDER BY trade_date, external_id",
        str(counterparty_id), account_id, lo, hi)
    ids = await _idents(conn, [r["security_id"] for r in rows])
    return [transaction_out(r, ids) for r in rows]


def lot_out(r, ids: dict | None = None) -> dict:
    """DOCUMENTED cost-basis fields (one row per TAX LOT): id, cusip, symbol, description, quantity, market_value,
    cost_basis_unadjusted, cost_basis_adjusted, unrealized_gain_loss, as_of_date, open_date."""
    sec = (ids or {}).get(str(r["security_id"])) if r["security_id"] else {}
    mv, adj = r["market_value"], r["cost_basis_adjusted"]
    return {"id": r["external_id"], "cusip": r["cusip"] or (sec or {}).get("cusip"),
            "symbol": r["ticker"] or (sec or {}).get("ticker"), "description": r["name"],
            "quantity": fmt(r["quantity"]), "market_value": fmt(mv),
            "cost_basis_unadjusted": fmt(r["cost_basis_unadjusted"]), "cost_basis_adjusted": fmt(adj),
            "unrealized_gain_loss": fmt(Decimal(mv) - Decimal(adj)) if mv is not None and adj is not None else None,
            "as_of_date": doc_date(r["as_of"]), "open_date": doc_date(r["open_date"])}


async def lots(conn, counterparty_id, account_id: str, clock: date) -> list[dict]:
    """The account's tax lots on the clock date: its UPLOADED lots (latest lot snapshot on or before the clock) when it
    has any; otherwise one lot per security position of the latest position snapshot, derived from it (cost basis →
    both bases; opened on the account's earliest buy of that security, else the position's as-of)."""
    cid = str(counterparty_id)
    up = await conn.fetch(
        "SELECT * FROM cpsim.book_lots WHERE counterparty_id = $1::uuid AND account_external_id = $2 AND valid_to IS "
        "NULL AND as_of = (SELECT max(as_of) FROM cpsim.book_lots WHERE counterparty_id = $1::uuid AND "
        "account_external_id = $2 AND valid_to IS NULL AND as_of <= $3) ORDER BY open_date, external_id",
        cid, account_id, clock)
    if up:
        ids = await _idents(conn, [r["security_id"] for r in up])
        return [lot_out(r, ids) for r in up]
    rows = await conn.fetch(
        "SELECT p.*, (SELECT min(t.trade_date) FROM cpsim.book_transactions t WHERE t.counterparty_id = p.counterparty_id "
        "AND t.account_external_id = p.account_external_id AND t.security_id = p.security_id AND t.type = 'buy' AND "
        "t.valid_to IS NULL AND t.trade_date <= $3) AS first_buy FROM cpsim.book_positions p WHERE p.counterparty_id = "
        "$1::uuid AND p.account_external_id = $2 AND p.valid_to IS NULL AND p.row_type = 'security' AND p.as_of = "
        "(SELECT max(as_of) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND account_external_id = $2 "
        "AND valid_to IS NULL AND as_of <= $3) ORDER BY p.external_id", cid, account_id, clock)
    ids = await _idents(conn, [r["security_id"] for r in rows])
    out = []
    for r in rows:
        if r["quantity"] is None or r["quantity"] <= 0 or r["cost_basis"] is None:
            continue           # no cost basis on the position → no lot to derive (never invented)
        out.append(lot_out({"external_id": f"{r['external_id']}-lot", "security_id": r["security_id"],
                            "cusip": r["cusip"], "ticker": r["ticker"], "name": r["name"], "quantity": r["quantity"],
                            "market_value": r["market_value"], "cost_basis_unadjusted": r["cost_basis"],
                            "cost_basis_adjusted": r["cost_basis"], "as_of": r["as_of"],
                            "open_date": r["first_buy"] or r["as_of"]}, ids))
    return out


# ── export ─────────────────────────────────────────────────────────────────────────────────────────────────────────
async def export_rows(conn, counterparty_id, kind: str) -> list[dict]:
    """The live book in the IMPORT columns (a CSV export re-imports to the identical book)."""
    if kind not in KINDS:
        raise BookError(f"unknown kind {kind!r}")
    cid = str(counterparty_id)
    if kind == "households":
        return [{"household_id": r["external_id"], "name": r["name"]} for r in await conn.fetch(
            "SELECT external_id, name FROM cpsim.book_households WHERE counterparty_id = $1::uuid AND valid_to IS NULL "
            "ORDER BY external_id", cid)]
    if kind == "accounts":
        return [{"account_id": r["external_id"], "household_id": r["household_external_id"], "name": r["name"],
                 "account_type": r["account_type"] or ""} for r in await conn.fetch(
            "SELECT * FROM cpsim.book_accounts WHERE counterparty_id = $1::uuid AND valid_to IS NULL "
            "ORDER BY external_id", cid)]
    if kind == "positions":
        out = []
        for r in await conn.fetch("SELECT * FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND valid_to "
                                  "IS NULL ORDER BY account_external_id, as_of, external_id", cid):
            out.append({"position_id": r["external_id"], "account_id": r["account_external_id"],
                        "as_of": r["as_of"].isoformat(), "row_type": r["row_type"], "cusip": r["cusip"] or "",
                        "isin": r["isin"] or "", "ticker": r["ticker"] or "", "name": r["name"],
                        "asset_type": r["asset_type"] or "", "quantity": fmt(r["quantity"]) or "",
                        "market_value": fmt(r["market_value"]) or "", "cost_basis": fmt(r["cost_basis"]) or "",
                        "currency_code": r["currency_code"]})
        return out
    if kind == "lots":
        return [{"lot_id": r["external_id"], "account_id": r["account_external_id"], "as_of": r["as_of"].isoformat(),
                 "cusip": r["cusip"] or "", "isin": r["isin"] or "", "ticker": r["ticker"] or "", "name": r["name"],
                 "quantity": fmt(r["quantity"]) or "", "market_value": fmt(r["market_value"]) or "",
                 "cost_basis_unadjusted": fmt(r["cost_basis_unadjusted"]) or "",
                 "cost_basis_adjusted": fmt(r["cost_basis_adjusted"]) or "", "open_date": r["open_date"].isoformat(),
                 "currency_code": r["currency_code"]} for r in await conn.fetch(
            "SELECT * FROM cpsim.book_lots WHERE counterparty_id = $1::uuid AND valid_to IS NULL "
            "ORDER BY account_external_id, as_of, open_date, external_id", cid)]
    out = []
    for r in await conn.fetch("SELECT * FROM cpsim.book_transactions WHERE counterparty_id = $1::uuid AND valid_to "
                              "IS NULL ORDER BY account_external_id, trade_date, external_id", cid):
        out.append({"transaction_id": r["external_id"], "account_id": r["account_external_id"], "type": r["type"],
                    "trade_date": r["trade_date"].isoformat(),
                    "settle_date": r["settle_date"].isoformat() if r["settle_date"] else "",
                    "row_type": r["row_type"], "cusip": r["cusip"] or "", "isin": r["isin"] or "",
                    "ticker": r["ticker"] or "", "name": r["name"] or "",
                    **{c: fmt(r[c]) or "" for c in ("quantity", "price", "gross_amount", "fees", "taxes",
                                                    "net_amount")},
                    "currency_code": r["currency_code"]})
    return out


async def export_api(conn, counterparty_id, clock: date) -> dict:
    """The book in the API's OWN (documented) response shapes, as served on the counterparty's clock date
    (transactions: the documented default window — the past month)."""
    hh = await households(conn, counterparty_id)
    accts = await accounts(conn, counterparty_id, None, clock)
    return {"as_of": doc_date(clock), "households": hh, "accounts": accts,
            "positions": {a["id"]: await positions(conn, counterparty_id, a["id"], clock) for a in accts},
            "cost_basis": {a["id"]: await lots(conn, counterparty_id, a["id"], clock) for a in accts},
            "transactions": {a["id"]: await transactions(conn, counterparty_id, a["id"], clock) for a in accts}}


def template(kind: str) -> dict:
    """The template for one kind: its columns, which are required, one example row."""
    examples = {
        "households": {"household_id": "hh-001", "name": "Example household"},
        "accounts": {"account_id": "acct-001", "household_id": "hh-001", "name": "Example brokerage",
                     "account_type": "individual"},
        "positions": {"position_id": "", "account_id": "acct-001", "as_of": "2026-11-10", "row_type": "security",
                      "cusip": "", "isin": "", "ticker": "EXMPL", "name": "", "asset_type": "", "quantity": "10",
                      "market_value": "", "cost_basis": "", "currency_code": "USD"},
        "transactions": {"transaction_id": "", "account_id": "acct-001", "type": "buy", "trade_date": "2026-11-09",
                         "settle_date": "2026-11-10", "row_type": "security", "cusip": "", "isin": "",
                         "ticker": "EXMPL", "name": "", "quantity": "10", "price": "100.00", "gross_amount": "1000.00",
                         "fees": "0", "taxes": "0", "net_amount": "1000.00", "currency_code": "USD"},
        "lots": {"lot_id": "", "account_id": "acct-001", "as_of": "2026-11-10", "cusip": "", "isin": "",
                 "ticker": "EXMPL", "name": "", "quantity": "10", "market_value": "",
                 "cost_basis_unadjusted": "950.00", "cost_basis_adjusted": "950.00", "open_date": "2025-03-14",
                 "currency_code": "USD"},
    }
    if kind not in KINDS:
        raise BookError(f"unknown kind {kind!r}")
    return {"kind": kind, "columns": list(COLUMNS[kind]), "required": list(REQUIRED[kind]),
            "id_column": ID_COLUMN[kind], "example": examples[kind], "transaction_types": list(TXN_TYPES),
            "row_types": list(ROW_TYPES), "max_rows": MAX_ROWS}
