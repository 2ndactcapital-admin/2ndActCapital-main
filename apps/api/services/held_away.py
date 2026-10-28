"""Held-away accounts (rep7) — read-only positions, lots and transactions for
accounts held elsewhere, every row with its source and as-of.

THE RULE — rep7b: a held-away account is traded only as its AUTHORITY allows
──────────────────────────────────────────────────────────────────────────────
By default (authority read-only) no rebalance, transition, harvest, custodian
action or execution instruction ever targets one. An approved authority change
(services/authority.py) opens advise (one-off trades the member executes) or
managed (at another custodian, manual DVP settlement). Each trade path calls
:func:`refusal` once, at its entry, and raises its own conflict (409) with the
plain-words reason:
    rebalance / every proposal trigger   rebalance_proposals._generate
    transition (join / change model)     account_models.propose_change, rebalance_proposals.transition_preview
    approved proposal → instructions     rebalance_proposals.execute
    harvest                              harvest.scan_account
    instruction creation                 execution.create_instructions (and custody.trading.route_trade_list)

SOURCES — best first (Joe): a direct custodian feed, an aggregator (both
"not connected": services/held_away_adapters.py is the interface, no client),
a statement filed in Chancery and extracted by the 'statement_positions'
ensemble (never Claude), and — always available — manual entry or a file.

IMPORTS — one row per load (held_away_imports). A load is a DRAFT: nothing
changes until a second person (approve_held_away, never the person who
entered it) approves the human task. An approved import REPLACES the
account's live positions as of its date (the earlier rows are closed on the
valid axis — every earlier as-of version is kept), closes the earlier open
lots, and adds its lots and transactions; each row it writes is recorded in
held_away_records with its source, import and as-of.

WHERE THE DATA GOES — portfolio.positions / tax_lots / transactions, so
rep5 / rep6 read it with no second path: household wash-sale control (the
household's accounts include held-away ones — an IRA held elsewhere too), the
gain budget (rebalance_proposals.realized_ytd adds gains realized in
held-away accounts of the same tax subject) and the loss bank (source
'held_away'). A declared outside trade (rep6) that a held-away transaction
matches — same subject, security, side, trade date and (when declared)
quantity — is recorded on the transaction's provenance row and then counts
ONCE: the held-away row in the window and the bank, the declaration no more.

STALENESS — held-away data older than its stale_after_days (the source's,
else the org setting) is visible: a proposal whose wash-sale or gain-budget
result depends on such an account shows the date and cannot be approved until
an adviser completes the 'confirm_held_away_data' task. Exactly
stale_after_days old is NOT stale; one day more is. A confirmation records
who and which as-of, and expires when newer data arrives or after
stale_after_days.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from services import human_tasks as HT
from services import held_away_adapters as AD

log = logging.getLogger(__name__)

VIEW, MANAGE, APPROVE = "view_held_away", "manage_held_away", "approve_held_away"
T_SRC = "public.held_away_sources"
T_IMP = "public.held_away_imports"
T_REC = "public.held_away_records"
T_CONF = "public.stale_confirmations"
TASK_IMPORT = "held_away_import_approval"
TASK_CONFIRM = "confirm_held_away_data"
SUBJECT_IMPORT = "held_away_import"
SUBJECT_CONFIRM = "stale_confirmation"
EVENT_SOURCE = "held_away"
STALE_SETTING = "replication.held_away.stale_after_days"
DEFAULT_STALE_DAYS = 30
STATEMENT_TASK_KEY = "statement_positions"
NO_MODELS = ("no extraction models configured for statements ('statement_positions') — enter the positions "
             "manually from the document")
DOCUMENT_RECORD_TYPE = "held_away_account"
LONG_TERM_DAYS = 365

SOURCE_KIND_LABELS = {"custodian_feed": "Direct custodian feed", "aggregator": "Aggregator",
                      "statement": "Statement filed in Chancery", "file": "File upload (xlsx / xls / csv)",
                      "manual": "Entered by an adviser"}
SOURCE_ORDER = ("custodian_feed", "aggregator", "statement", "file", "manual")
SOURCE_STATUS_LABELS = {"active": "Active", "not_connected": "Not connected", "retired": "Retired"}
IMPORT_STATUS_LABELS = {"draft": "Draft — awaiting a second person's approval", "approved": "Approved",
                        "rejected": "Rejected"}
REJECT_REASONS = {
    "no_identifier": "Row has no identifier (ticker, CUSIP, ISIN or SEDOL)",
    "unresolved": "Identifier not in the security master",
    "identifier_conflict": "Identifiers point at different securities",
    "quantity_missing": "Quantity missing",
    "not_number": "A number field is not a number",
    "negative": "A negative quantity, value or cost",
    "date_invalid": "A date is not a date",
    "after_as_of": "Dated after the statement date",
    "side_invalid": "Side is not buy or sell",
    "term_invalid": "Term is not short or long",
    "formula_no_value": "Formula with no saved value",
    "error_cell": "Spreadsheet error value",
    "models_disagree": "The two extraction models disagree on this row",
    "file_refused": "The file itself was refused",
}
CONFIRM_STATUS_LABELS = {"pending": "Awaiting an adviser's confirmation", "confirmed": "Confirmed",
                         "expired": "Expired", "cancelled": "Cancelled"}
AUTHORITY = {"manual": "manual", "file": "stated", "statement": "stated", "aggregator": "aggregated",
             "custodian_feed": "custodial"}
SOURCE_SYSTEM = {"manual": "manual", "file": "manual", "statement": "chancery", "aggregator": "manual",
                 "custodian_feed": "manual"}
LOT_SOURCE_SYSTEM = "held_away"

# the positions-and-lots file vocabulary — rep3's mapping machinery with its own fields
FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ticker": ("Ticker", ("ticker", "symbol", "ticker symbol", "trading symbol")),
    "cusip": ("CUSIP", ("cusip", "cusip number")),
    "isin": ("ISIN", ("isin", "isin code")),
    "sedol": ("SEDOL", ("sedol",)),
    "name": ("Name", ("name", "security name", "security", "description", "security description")),
    "quantity": ("Quantity", ("quantity", "qty", "shares", "units", "share quantity", "position quantity")),
    "market_value": ("Market Value", ("market value", "value", "mv", "current value", "market val")),
    "acquired_date": ("Acquired", ("acquired", "acquired date", "acquisition date", "date acquired", "open date",
                                   "purchase date")),
    "cost": ("Cost", ("cost", "cost basis", "total cost", "basis", "adjusted cost")),
    "term": ("Term", ("term", "holding period")),
    "side": ("Side", ("side", "action", "transaction type", "buy sell")),
    "trade_date": ("Trade Date", ("trade date", "transaction date", "date")),
    "price": ("Price", ("price", "trade price", "execution price")),
    "realized_gain_loss": ("Realized Gain/Loss", ("realized gain loss", "realized", "realized gain",
                                                  "gain loss", "realized g l")),
}
IDENTIFIERS = ("cusip", "isin", "sedol", "ticker")


class HeldAwayError(Exception):
    """A request the rules refuse (422)."""


class HeldAwayNotFound(HeldAwayError, LookupError):
    pass


class HeldAwayConflict(HeldAwayError):
    """The state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    pass


# ── The trade-path guard ────────────────────────────────────────────────────
# rep7b — re-keyed on AUTHORITY (services/authority.py): the same one call per
# path, now answering "does this account's effective authority × custody allow
# this path?". Held-away accounts default to read-only (rep7's rule, exactly);
# an approved change to advise or managed opens the paths the matrix lists.
def refusal(account: dict | None, action: str, *, path: str | None = None, executor: str | None = None) -> str | None:
    """The plain-words reason a trade path refuses this account, or None."""
    from services import authority as AU

    return AU.refusal(account, action, path=path, executor=executor)


def refuse(account: dict | None, action: str, exc_cls, *, path: str | None = None, executor: str | None = None) -> None:
    why = refusal(account, action, path=path, executor=executor)
    if why:
        raise exc_cls(why)


# ── Small helpers ───────────────────────────────────────────────────────────
def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else o.isoformat()
                      if isinstance(o, (date, datetime)) else str(o))


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise HeldAwayNotFound(f"{what} not found") from None


def _num(v):
    return None if v is None else str(v)


def _iso(v):
    return v.isoformat() if v else None


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


async def _withdraw_task(pool, task_id, reason: str) -> None:
    """Close an open task with no actor (the platform opened it; its subject
    went away) — rep5's pattern."""
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        log.info("task %s was not open to withdraw", task_id)


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"held_away.{action}", EVENT_SOURCE, str(resource_id),
        _dumps(payload))


async def _publish(pool, org_id, action: str, resource_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    try:
        await publish_event(pool, str(org_id), f"held_away.{action}", EVENT_SOURCE, str(resource_id),
                            json.loads(_dumps(payload)), created_by=str(actor_id) if actor_id else None)
    except Exception:  # noqa: BLE001 — the event is a notification; the write stands
        log.exception("held_away.%s event not published", action)


async def _setting_days(conn, org_id) -> int:
    from services import rebalance_proposals as RP

    return await RP._setting(conn, org_id, STALE_SETTING, DEFAULT_STALE_DAYS, lo=1, hi=3650, integer=True)


async def load_account(conn, org_id, account_id) -> dict:
    from services import account_models as AM

    try:
        return await AM.load_account(conn, org_id, account_id)
    except AM.AccountModelNotFound as exc:
        raise HeldAwayNotFound(str(exc)) from None


def _need_held_away(account: dict) -> None:
    if not account.get("is_held_away"):
        raise HeldAwayConflict(f"{account['account_number_masked']} is not a held-away account — its positions come "
                               "from its custodian; held-away sources are for accounts held elsewhere")


# ── Sources ─────────────────────────────────────────────────────────────────
async def list_sources(conn, org_id, account_id, *, include_retired: bool = False) -> list[dict]:
    sql = (f"SELECT * FROM {T_SRC} WHERE org_id = $1::uuid AND account_id = $2::uuid AND valid_to IS NULL "
           "AND system_to IS NULL")
    if not include_retired:
        sql += " AND status <> 'retired'"
    rows = [dict(r) for r in await conn.fetch(sql + " ORDER BY created_at", str(org_id), _uuid(account_id, "account"))]
    rows.sort(key=lambda r: SOURCE_ORDER.index(r["kind"]))
    return rows


async def _source(conn, org_id, source_id, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_SRC} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), _uuid(source_id, "source"), str(org_id))
    if row is None:
        raise HeldAwayNotFound("source not found")
    return dict(row)


async def _archive_source(conn, row: dict) -> None:
    """System axis: a copy of the current state, closed; the live row keeps its id."""
    await conn.execute(
        f"""INSERT INTO {T_SRC} (org_id, account_id, kind, provider, status, last_as_of, stale_after_days, notes,
                created_by, archived_from_id, created_at, valid_from, valid_to, system_from, system_to)
            SELECT org_id, account_id, kind, provider, status, last_as_of, stale_after_days, notes, created_by, id,
                   created_at, valid_from, valid_to, system_from, now()
            FROM {T_SRC} WHERE id = $1""", row["id"])


async def _update_source(conn, row: dict, fields: dict) -> dict:
    await _archive_source(conn, row)
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    new = await conn.fetchrow(f"UPDATE {T_SRC} SET {sets}, system_from = now() WHERE id = $1 RETURNING *",
                              row["id"], *fields.values())
    return dict(new)


async def add_source(pool, *, org_id, actor_id, account_id, kind: str, provider: str | None = None,
                     stale_after_days: int | None = None, notes: str | None = None) -> dict:
    if kind not in SOURCE_KIND_LABELS:
        raise HeldAwayError(f"kind is one of {list(SOURCE_KIND_LABELS)}")
    status = "active"
    if kind in ("aggregator", "custodian_feed"):
        p = AD.provider(provider or "")
        if p is None or p.kind != kind:
            raise HeldAwayError(f"choose a known {SOURCE_KIND_LABELS[kind].lower()} provider: "
                                f"{[x['key'] for x in AD.providers() if x['kind'] == kind]}")
        provider, status = p.key, AD.NOT_CONNECTED
    elif provider:
        raise HeldAwayError(f"a {kind} source has no provider")
    if stale_after_days is not None and not (1 <= int(stale_after_days) <= 3650):
        raise HeldAwayError("stale_after_days is between 1 and 3650 (empty = the organisation's default)")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await load_account(conn, org_id, account_id)
        _need_held_away(account)
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""INSERT INTO {T_SRC} (org_id, account_id, kind, provider, status, stale_after_days, notes, created_by)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::uuid) RETURNING *""",
                str(org_id), str(account["id"]), kind, provider, status, stale_after_days,
                (notes or "").strip() or None, str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "source_added", row["id"],
                         {"account_id": str(account["id"]), "kind": kind, "provider": provider, "status": status})
    return dict(row)


async def update_source(pool, *, org_id, actor_id, source_id, changes: dict) -> dict:
    allowed = {"stale_after_days", "notes", "status"}
    bad = set(changes) - allowed
    if bad:
        raise HeldAwayError(f"only {sorted(allowed)} change on a source")
    if "status" in changes and changes["status"] != "retired":
        raise HeldAwayError("a source is retired, never re-activated (add a new one)")
    sad = changes.get("stale_after_days")
    if sad is not None and not (1 <= int(sad) <= 3650):
        raise HeldAwayError("stale_after_days is between 1 and 3650 (empty = the organisation's default)")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await _source(conn, org_id, source_id, lock=True)
            if row["status"] == "retired":
                raise HeldAwayConflict("this source is retired")
            new = await _update_source(conn, row, changes)
            await _audit(conn, org_id, actor["id"], "source_changed", row["id"], {"changes": changes})
    return new


async def source_for_kind(conn, org_id, account: dict, kind: str) -> dict:
    """The account's live source of this kind — created on first use for the
    person-driven kinds (manual / file / statement)."""
    row = await conn.fetchrow(f"SELECT * FROM {T_SRC} WHERE org_id = $1::uuid AND account_id = $2::uuid AND kind = $3 "
                              "AND provider IS NULL AND status <> 'retired' AND valid_to IS NULL AND system_to IS NULL",
                              str(org_id), str(account["id"]), kind)
    if row:
        return dict(row)
    row = await conn.fetchrow(
        f"""INSERT INTO {T_SRC} (org_id, account_id, kind, status) VALUES ($1::uuid, $2::uuid, $3, 'active')
            ON CONFLICT DO NOTHING RETURNING *""", str(org_id), str(account["id"]), kind)
    if row is None:
        row = await conn.fetchrow(f"SELECT * FROM {T_SRC} WHERE org_id = $1::uuid AND account_id = $2::uuid AND "
                                  "kind = $3 AND provider IS NULL AND status <> 'retired' AND valid_to IS NULL "
                                  "AND system_to IS NULL", str(org_id), str(account["id"]), kind)
    return dict(row)


# ── Row validation (shared by manual, file and statement) ──────────────────
def _parse_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    return date.fromisoformat(s)


def _norm_side(v) -> str | None:
    s = (str(v or "")).strip().lower()
    if not s:
        return None
    if s in ("buy", "b", "bought", "purchase"):
        return "buy"
    if s in ("sell", "s", "sold", "sale"):
        return "sell"
    raise ValueError("side")


def term_of(acquired: date | None, on: date) -> str | None:
    """Long-term when held more than one year (IRC §1222), else short."""
    if acquired is None:
        return None
    return "long" if (on - acquired).days > LONG_TERM_DAYS else "short"


async def resolve_identifiers(conn, rows: list[dict]) -> None:
    """Each row's security from its identifiers — the security master's own
    bulk lookup (rep3's rule order: CUSIP / ISIN first and never re-tried on the
    ticker; else SEDOL, else ticker; an ambiguous identifier is no match).
    Sets ``security_id`` or ``reject``."""
    from services import securities_global as SG

    looks: dict[str, dict] = {}
    for t in IDENTIFIERS:
        vals = [r[t] for r in rows if r.get(t)]
        looks[t] = await SG.get_by_identifiers_bulk(conn, t, vals) if vals else {}
    for r in rows:
        if r.get("reject"):
            continue
        hit = {}
        strong = [t for t in ("cusip", "isin") if r.get(t)]
        order = strong or [t for t in ("sedol", "ticker") if r.get(t)][:1]
        if not order:
            r["reject"] = ("no_identifier", REJECT_REASONS["no_identifier"])
            continue
        for t in order:
            got = looks[t].get(SG.normalize_identifier_value(t, r[t]))
            if got:
                hit[t] = str(got.get("matched_global_security_id") or got["id"])
        ids = set(hit.values())
        if len(ids) > 1:
            r["reject"] = ("identifier_conflict", f"{', '.join(f'{k} → {v}' for k, v in hit.items())}")
        elif not ids:
            r["reject"] = ("unresolved", f"{order[0].upper()} {r[order[0]]} is not in the security master")
        else:
            r["security_id"] = ids.pop()


def validate_row(raw: dict, as_of: date) -> dict:
    """One input row (keys from :data:`FIELDS`) → a normalised row with kind
    position | lot | transaction, or a ``reject`` (code, text). Pure."""
    from services.model_upload import cell_text, to_decimal

    r: dict = {"row": raw.get("row"), "raw": {k: cell_text(v) for k, v in raw.items() if k != "row"}}
    for t in IDENTIFIERS:
        v = cell_text(raw.get(t))
        r[t] = v.strip().upper() if v else None
    r["name"] = cell_text(raw.get("name"))
    try:
        for k in ("quantity", "market_value", "cost", "price", "realized_gain_loss"):
            r[k] = to_decimal(raw.get(k))
    except ValueError as exc:
        r["reject"] = ("not_number", str(exc))
        return r
    try:
        r["acquired_date"] = _parse_date(raw.get("acquired_date"))
        r["trade_date"] = _parse_date(raw.get("trade_date"))
    except (ValueError, TypeError):
        r["reject"] = ("date_invalid", REJECT_REASONS["date_invalid"])
        return r
    try:
        r["side"] = _norm_side(raw.get("side"))
    except ValueError:
        r["reject"] = ("side_invalid", f"{raw.get('side')!r} is not buy or sell")
        return r
    term = (cell_text(raw.get("term")) or "").strip().lower() or None
    if term in ("st", "short term", "short-term"):
        term = "short"
    if term in ("lt", "long term", "long-term"):
        term = "long"
    if term not in (None, "short", "long"):
        r["reject"] = ("term_invalid", f"{term!r} is not short or long")
        return r
    r["term"] = term
    if r["quantity"] is None:
        r["reject"] = ("quantity_missing", REJECT_REASONS["quantity_missing"])
        return r
    for k in ("quantity", "market_value", "cost", "price"):
        if r[k] is not None and r[k] < 0:
            r["reject"] = ("negative", f"{FIELDS[k][0]} is negative")
            return r
    for k in ("acquired_date", "trade_date"):
        if r[k] is not None and r[k] > as_of:
            r["reject"] = ("after_as_of", f"{FIELDS[k][0]} {r[k].isoformat()} is after the as-of {as_of.isoformat()}")
            return r
    if r["side"] or r["trade_date"]:
        if not (r["side"] and r["trade_date"]):
            r["reject"] = ("side_invalid", "a transaction row needs both a side and a trade date")
            return r
        r["kind"] = "transaction"
        if r["side"] == "sell" and r["realized_gain_loss"] is not None and r["term"] is None:
            r["term"] = term_of(r["acquired_date"], r["trade_date"])
    elif r["acquired_date"] is not None or r["cost"] is not None:
        r["kind"] = "lot"
        r["term"] = r["term"] or term_of(r["acquired_date"], as_of)
    else:
        r["kind"] = "position"
    return r


def build_payload(rows: list[dict]) -> dict:
    """Accepted rows → positions (summed per security), lots and transactions.
    A lot row also counts toward its security's position."""
    positions: dict[str, dict] = {}
    lots, txns = [], []
    for r in rows:
        if r.get("reject"):
            continue
        sid = r["security_id"]
        if r["kind"] in ("position", "lot"):
            p = positions.setdefault(sid, {"security_id": sid, "label": r.get("name") or r.get("ticker") or sid,
                                           "quantity": Decimal(0), "market_value": Decimal(0), "value_known": True,
                                           "rows": []})
            p["quantity"] += r["quantity"]
            if r["market_value"] is None:
                p["value_known"] = False
            else:
                p["market_value"] += r["market_value"]
            p["rows"].append(r["row"])
        if r["kind"] == "lot":
            lots.append({"security_id": sid, "quantity": str(r["quantity"]), "acquired_date": _iso(r["acquired_date"]),
                         "cost": _num(r["cost"]), "term": r["term"], "row": r["row"]})
        if r["kind"] == "transaction":
            txns.append({"security_id": sid, "side": r["side"], "trade_date": _iso(r["trade_date"]),
                         "quantity": str(r["quantity"]), "price": _num(r["price"]),
                         "realized_gain_loss": _num(r["realized_gain_loss"]), "term": r["term"],
                         "acquired_date": _iso(r["acquired_date"]), "cost": _num(r["cost"]), "row": r["row"]})
    pos = [{"security_id": p["security_id"], "label": p["label"], "quantity": str(p["quantity"]),
            "market_value": str(p["market_value"]) if p["value_known"] else None, "rows": p["rows"]}
           for p in positions.values()]
    return {"positions": pos, "lots": lots, "transactions": txns}


def rejects_of(rows: list[dict]) -> list[dict]:
    return [{"row": r.get("row"), "code": r["reject"][0], "reason": REJECT_REASONS.get(r["reject"][0], r["reject"][0]),
             "detail": r["reject"][1], "values": r.get("raw") or {}} for r in rows if r.get("reject")]


async def validate_rows(conn, raws: list[dict], as_of: date) -> tuple[dict, list[dict], int]:
    rows = [validate_row(x, as_of) for x in raws]
    await resolve_identifiers(conn, rows)
    return build_payload(rows), rejects_of(rows), len(rows)


# ── File (rep3's parser + mapping machinery) ────────────────────────────────
def read_file(data: bytes, filename: str, *, mapping: dict | None = None, sheet: str | None = None,
              header_row: int | None = None) -> tuple[list[dict], dict, list[dict]]:
    """xlsx / xls / csv → (input rows, mapping by header, row-level messages)
    through services.model_upload: the same precheck (type, macro, size, rows,
    archive), reader, header detection and mapping (with this file's own field
    vocabulary). Raises model_upload.UploadRefused for a refused file."""
    from services import model_upload as U

    U.precheck(data, filename)
    wb = U.read_workbook(data, filename)
    sh = sheet or wb.sheets[0]
    if sh not in wb.grids:
        raise U.UploadRefused("no_header", f"no sheet named {sh!r}")
    grid = wb.grids[sh]
    hdr = header_row or U.detect_header(grid, None, fields=FIELDS, required=("quantity",))
    if not hdr:
        raise U.UploadRefused("no_header", "no header row naming an identifier (ticker, CUSIP, ISIN or SEDOL) and a "
                                           "quantity was found in the first 30 rows")
    headers = U.headers_of(grid, hdr)
    cols = U.mapping_from_request(headers, mapping, None, fields=FIELDS)
    if "quantity" not in cols.values() or not set(cols.values()) & set(IDENTIFIERS):
        raise U.UploadRefused("mapping", "map an identifier column and a quantity column")
    out, msgs = [], []
    for i, line in enumerate(grid[hdr:], start=hdr + 1):
        if not any(U.cell_text(c) for c in line):
            continue
        rec = {"row": i}
        bad = None
        for ci, fld in cols.items():
            if (sh, i, ci) in wb.formula_gaps:
                bad = ("formula_no_value", f"column {headers[ci]!r} is a formula with no saved value")
            if (sh, i, ci) in wb.error_cells:
                bad = ("error_cell", f"column {headers[ci]!r} holds a spreadsheet error")
            rec[fld] = line[ci] if ci < len(line) else None
        first = (U.cell_text(rec.get("name")) or U.cell_text(rec.get("ticker")) or "").lower()
        if first.startswith("total"):
            continue
        if bad:
            rec["_reject"] = bad
        out.append(rec)
    return out, U.mapping_by_header(headers, cols), msgs


# ── Imports ─────────────────────────────────────────────────────────────────
async def _import(conn, org_id, import_id, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_IMP} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), _uuid(import_id, "import"), str(org_id))
    if row is None:
        raise HeldAwayNotFound("import not found")
    return dict(row)


async def _create_import(pool, *, org_id, actor, account: dict, kind: str, as_of: date, raws: list[dict],
                         document_id=None, file_name=None, file_sha256=None, mapping=None, extraction=None,
                         notes=None, extra_rejects: list[dict] | None = None) -> dict:
    """Validate, write the DRAFT import and open its approval task (maker =
    the person who entered it)."""
    async with pool.acquire() as conn:
        pre = [x for x in raws if x.get("_reject")]
        clean = [x for x in raws if not x.get("_reject")]
        payload, rejects, n = await validate_rows(conn, clean, as_of)
        rejects = [{"row": x.get("row"), "code": x["_reject"][0], "reason": REJECT_REASONS[x["_reject"][0]],
                    "detail": x["_reject"][1], "values": {}} for x in pre] + rejects + list(extra_rejects or [])
        n += len(pre) + len(extra_rejects or [])
        accepted = n - len(rejects)
        src = await source_for_kind(conn, org_id, account, kind)
    iid = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_IMPORT, subject_type=SUBJECT_IMPORT, subject_id=iid, maker_id=actor["id"],
        eligible_permission=APPROVE,
        title=f"Held-away {SOURCE_KIND_LABELS[kind].lower()}: {account['account_number_masked']} as of "
              f"{as_of.isoformat()}",
        payload={"account": account["account_number_masked"], "kind": kind, "as_of": as_of.isoformat(),
                 "rows_read": n, "rows_accepted": accepted, "rows_rejected": len(rejects)},
        action_path=f"/portfolio/accounts/{account['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_IMP} (id, org_id, account_id, source_id, kind, as_of, document_id, file_name,
                            file_sha256, mapping, rows_read, rows_accepted, rows_rejected, rejects, payload, extraction,
                            status, entered_by, approval_task_id, notes)
                        VALUES ($1, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7::uuid, $8, $9, $10::jsonb, $11, $12, $13,
                                $14::jsonb, $15::jsonb, $16::jsonb, 'draft', $17::uuid, $18::uuid, $19) RETURNING *""",
                    iid, str(org_id), str(account["id"]), str(src["id"]), kind, as_of,
                    str(document_id) if document_id else None, file_name, file_sha256, _dumps(mapping or {}), n,
                    accepted, len(rejects), _dumps(rejects), _dumps(payload), _dumps(extraction or {}),
                    str(actor["id"]), task["id"], (notes or "").strip() or None)
                await _audit(conn, org_id, actor["id"], "import_loaded", iid,
                             {"account_id": str(account["id"]), "kind": kind, "as_of": as_of.isoformat(),
                              "rows_read": n, "rows_accepted": accepted, "rows_rejected": len(rejects),
                              "task_id": task["id"]})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the held-away import could not be recorded")
        raise
    await _publish(pool, org_id, "import_loaded", iid, {"account_id": str(account["id"]), "task_id": task["id"]},
                   actor["id"])
    return {**dict(row), "task": task}


async def create_manual_import(pool, *, org_id, actor_id, account_id, as_of: date, rows: list[dict],
                               document_id=None, notes: str | None = None) -> dict:
    """An adviser keys positions / lots / transactions (as-of = the statement
    date, an optional Chancery document)."""
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await load_account(conn, org_id, account_id)
        _need_held_away(account)
        if document_id:
            await _check_document(conn, org_id, document_id)
    raws = [{**x, "row": i} for i, x in enumerate(rows, start=1)]
    return await _create_import(pool, org_id=org_id, actor=actor, account=account, kind="manual", as_of=as_of,
                                raws=raws, document_id=document_id, notes=notes)


async def create_file_import(pool, *, org_id, actor_id, account_id, as_of: date, data: bytes, filename: str,
                             mapping: dict | None = None, sheet: str | None = None, header_row: int | None = None,
                             notes: str | None = None) -> dict:
    from services import model_upload as U

    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await load_account(conn, org_id, account_id)
        _need_held_away(account)
    sha = hashlib.sha256(data).hexdigest()
    try:
        raws, used, _ = read_file(data, filename, mapping=mapping, sheet=sheet, header_row=header_row)
    except U.UploadRefused as exc:
        # the load is still logged: a draft with nothing accepted and the reason — it can only be rejected
        return await _create_import(pool, org_id=org_id, actor=actor, account=account, kind="file", as_of=as_of,
                                    raws=[], file_name=filename[:300], file_sha256=sha, mapping=mapping or {},
                                    extraction={"refused": exc.code},
                                    extra_rejects=[{"row": None, "code": "file_refused",
                                                    "reason": REJECT_REASONS["file_refused"],
                                                    "detail": f"{exc.code}: {exc.text}", "values": {}}],
                                    notes=notes)
    return await _create_import(pool, org_id=org_id, actor=actor, account=account, kind="file", as_of=as_of,
                                raws=raws, file_name=filename[:300], file_sha256=sha, mapping=used, notes=notes)


# ── Statements (Chancery + the 'statement_positions' ensemble, never Claude) ─
async def _check_document(conn, org_id, document_id) -> dict:
    row = await conn.fetchrow("SELECT id, original_filename, status FROM public.documents WHERE id = $1::uuid "
                              "AND org_id = $2::uuid", _uuid(document_id, "document"), str(org_id))
    if row is None:
        raise HeldAwayNotFound("document not found")
    return dict(row)


async def link_statement(pool, *, org_id, actor_id, account_id, document_id) -> dict:
    """File a Chancery document against a held-away account."""
    from services.document_linkage import link_document_to_record

    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await load_account(conn, org_id, account_id)
        _need_held_away(account)
        await _check_document(conn, org_id, document_id)
        async with conn.transaction():
            out = await link_document_to_record(conn, str(org_id), str(document_id), DOCUMENT_RECORD_TYPE,
                                                str(account["id"]), created_by=str(actor["id"]))
            await source_for_kind(conn, org_id, account, "statement")
    return out


async def statement_documents(conn, org_id, account_id) -> list[dict]:
    rows = await conn.fetch(
        """SELECT d.id, d.original_filename, d.status, d.created_at FROM public.document_record_links l
           JOIN public.documents d ON d.id = l.document_id AND d.org_id = l.org_id
           WHERE l.org_id = $1::uuid AND l.record_type = $2 AND l.record_id = $3::uuid ORDER BY d.created_at DESC""",
        str(org_id), DOCUMENT_RECORD_TYPE, _uuid(account_id, "account"))
    return [{"id": str(r["id"]), "file_name": r["original_filename"], "status": r["status"],
             "created_at": _iso(r["created_at"])} for r in rows]


async def statement_ensemble(conn) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM public.ai_ensemble_configs WHERE task_key = $1 AND is_active "
                              "ORDER BY activated_at DESC NULLS LAST LIMIT 1", STATEMENT_TASK_KEY)
    return dict(row) if row else None


async def extraction_status(conn) -> dict:
    from services.replica_terms import is_claude

    ens = await statement_ensemble(conn)
    if ens is None:
        return {"available": False, "message": NO_MODELS, "task_key": STATEMENT_TASK_KEY}
    if is_claude(ens.get("model_1"), ens.get("model_2"), ens.get("system_one_model")):
        return {"available": False, "task_key": STATEMENT_TASK_KEY,
                "message": "the configured statement ensemble names a Claude model — statements are never read by "
                           "Claude; enter the positions manually from the document"}
    return {"available": True, "task_key": STATEMENT_TASK_KEY, "ensemble_config_id": str(ens["id"]),
            "models": [ens["model_1"], ens["model_2"]], "message": None}


STATEMENT_INSTRUCTIONS = """You read ONE brokerage or custodian account statement and list every position, every \
open tax lot and every trade it reports, as ONE JSON object: {"rows": [ {"ticker", "cusip", "isin", "name", \
"quantity", "market_value", "acquired_date", "cost", "term", "side", "trade_date", "price", \
"realized_gain_loss"} ]}. Use null for anything the statement does not state. Numbers as plain decimals \
(no currency signs, no thousands separators); dates as YYYY-MM-DD. Answer only from the text. JSON only."""


async def _proxy_reader(model: str, text: str, as_of: date) -> list[dict]:
    """One model's reading of the statement through the LiteLLM proxy (the
    ensemble path; fallbacks disabled). A Claude answer is discarded."""
    from services.note_extraction import proxy
    from services.replica_terms import is_claude

    resp = await proxy.chat({"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                             "messages": [{"role": "system", "content": STATEMENT_INSTRUCTIONS},
                                          {"role": "user", "content": f"Statement as of {as_of.isoformat()}:\n\n"
                                                                      + text[:120_000]}]})
    if resp.status != 200 or not resp.body:
        raise HeldAwayConflict(f"the statement reader {model} failed ({resp.status}: {resp.error or 'no body'})")
    if is_claude(resp.body.get("model")):
        raise HeldAwayConflict(f"a Claude model answered ({resp.body.get('model')}) — discarded; statements are "
                               "never read by Claude")
    content = ((resp.body.get("choices") or [{}])[0].get("message") or {}).get("content") or "{}"
    rows = (json.loads(content) or {}).get("rows") or []
    return [r for r in rows if isinstance(r, dict)]


#: The seam a verify script replaces (no network): ``await STATEMENT_READER(model, text, as_of) -> [row, ...]``
STATEMENT_READER = _proxy_reader


def _row_key(r: dict) -> tuple:
    from services.model_upload import cell_text, to_decimal

    ident = next(((t, (cell_text(r.get(t)) or "").upper()) for t in ("cusip", "isin", "ticker") if cell_text(r.get(t))),
                 None)
    try:
        q = to_decimal(r.get("quantity"))
    except ValueError:
        q = r.get("quantity")
    return (ident, q, cell_text(r.get("side")), cell_text(r.get("trade_date")), cell_text(r.get("acquired_date")))


async def create_statement_import(pool, *, org_id, actor_id, account_id, document_id, as_of: date,
                                  notes: str | None = None) -> dict:
    """Extract a linked statement through the ensemble → a DRAFT import. A row
    is accepted only when BOTH readers report it identically (the ensemble
    rule); a row only one reader saw is a reject ('models_disagree')."""
    from services.replica_terms import is_claude

    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await load_account(conn, org_id, account_id)
        _need_held_away(account)
        await _check_document(conn, org_id, document_id)
        linked = {d["id"] for d in await statement_documents(conn, org_id, account["id"])}
        if str(document_id) not in linked:
            raise HeldAwayConflict("link the document to this account first")
        ens = await statement_ensemble(conn)
        if ens is None:
            raise HeldAwayConflict(NO_MODELS)
        models = [ens["model_1"], ens["model_2"]]
        if is_claude(*models, ens.get("system_one_model")):
            raise HeldAwayConflict("the statement ensemble names a Claude model — statements are never read by Claude")
        text = await conn.fetchval("SELECT extracted_text FROM public.document_extractions WHERE document_id = $1::uuid "
                                   "AND org_id = $2::uuid AND extracted_text IS NOT NULL ORDER BY created_at DESC "
                                   "LIMIT 1", str(document_id), str(org_id))
    if not (text or "").strip():
        raise HeldAwayConflict("the document has no extracted text yet — Chancery has not read it; enter the "
                               "positions manually from the document")
    readings = [await STATEMENT_READER(m, text, as_of) for m in models]
    a = {_row_key(r): r for r in readings[0]}
    b = {_row_key(r): r for r in readings[1]}
    agreed = [a[k] for k in a if k in b]
    disagreed = [{"row": None, "code": "models_disagree", "reason": REJECT_REASONS["models_disagree"],
                  "detail": f"read by {models[0] if k in a else models[1]} only",
                  "values": {kk: (str(vv) if vv is not None else None) for kk, vv in (a.get(k) or b.get(k)).items()}}
                 for k in list(a.keys() ^ b.keys())]
    raws = [{**r, "row": i} for i, r in enumerate(agreed, start=1)]
    extraction = {"task_key": STATEMENT_TASK_KEY, "ensemble_config_id": str(ens["id"]), "models": models,
                  "agreed": len(agreed), "disagreed": len(disagreed), "label": "extracted — review before approving"}
    return await _create_import(pool, org_id=org_id, actor=actor, account=account, kind="statement", as_of=as_of,
                                raws=raws, document_id=document_id, extraction=extraction, notes=notes,
                                extra_rejects=disagreed)


# ── Deciding an import (maker-checker) ──────────────────────────────────────
async def decide_import(pool, *, org_id, actor_id, import_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise HeldAwayError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise HeldAwayError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        imp = await _import(conn, org_id, import_id)
    if imp["status"] != "draft":
        raise HeldAwayConflict(f"this import is {imp['status']}, not a draft")
    if str(imp["entered_by"]) == str(actor["id"]):
        raise MakerCheckerViolation("maker-checker: you entered this import, so a different holder of "
                                    f"{APPROVE} must approve or reject it")
    if outcome == "approve" and (imp["extraction"] or {}) and _loads(imp["extraction"]).get("refused"):
        raise HeldAwayConflict("the file was refused — reject this import and load a corrected file")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=imp["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise HeldAwayConflict(str(exc)) from exc
    res = done.get("subject_result")
    if res is None:
        async with pool.acquire() as conn:
            res = await _import(conn, org_id, import_id)
    return res


async def _asset_for(conn, org_id, security_id: str) -> str:
    aid = await conn.fetchval("SELECT id FROM portfolio.assets WHERE org_id = $1::uuid AND global_security_id = $2::uuid "
                              "AND valid_to IS NULL AND system_to IS NULL ORDER BY valid_from LIMIT 1",
                              str(org_id), security_id)
    if aid:
        return str(aid)
    g = await conn.fetchrow("SELECT name, security_type FROM portfolio.securities_global WHERE id = $1::uuid "
                            "AND system_to IS NULL ORDER BY valid_to NULLS FIRST LIMIT 1", security_id)
    return str(await conn.fetchval(
        "INSERT INTO portfolio.assets (org_id, global_security_id, name, asset_type) VALUES ($1::uuid, $2::uuid, $3, $4) "
        "RETURNING id", str(org_id), security_id, g["name"] if g else security_id,
        ((g["security_type"] if g else None) or "equity").lower()))


async def _decide_row(conn, imp: dict, status: str, checker, now, note) -> dict:
    """System axis: archive a copy of the draft, then decide the live row (its
    id is kept — provenance rows point at it)."""
    await conn.execute(
        f"""INSERT INTO {T_IMP} (org_id, account_id, source_id, kind, as_of, document_id, file_name, file_sha256,
                mapping, rows_read, rows_accepted, rows_rejected, rejects, payload, extraction, status, entered_by,
                approved_by, approval_task_id, decided_at, decision_note, notes, archived_from_id, created_at,
                valid_from, valid_to, system_from, system_to)
            SELECT org_id, account_id, source_id, kind, as_of, document_id, file_name, file_sha256, mapping, rows_read,
                   rows_accepted, rows_rejected, rejects, payload, extraction, status, entered_by, approved_by,
                   approval_task_id, decided_at, decision_note, notes, id, created_at, valid_from, valid_to,
                   system_from, now()
            FROM {T_IMP} WHERE id = $1""", imp["id"])
    return dict(await conn.fetchrow(
        f"UPDATE {T_IMP} SET status = $2, approved_by = $3, decided_at = $4, decision_note = $5, system_from = now() "
        "WHERE id = $1 RETURNING *", imp["id"], status, checker, now, note))


async def _record(conn, org_id, imp: dict, record_type: str, record_id, matched=None) -> None:
    await conn.execute(
        f"""INSERT INTO {T_REC} (org_id, account_id, source_id, import_id, record_type, record_id, as_of,
                matched_declared_id) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6::uuid, $7, $8::uuid)""",
        str(org_id), str(imp["account_id"]), str(imp["source_id"]), str(imp["id"]), record_type, str(record_id),
        imp["as_of"], str(matched) if matched else None)


async def match_declared(conn, org_id, account: dict, txn: dict) -> str | None:
    """A live declared outside trade this held-away transaction IS (same tax
    subject, security, side, trade date and — when declared — quantity), not
    yet matched by another held-away transaction."""
    from services import tax_profiles as TP

    st, sid = TP.subject_of(account)
    row = await conn.fetchval(
        f"""SELECT d.id FROM public.declared_outside_trades d
            WHERE d.org_id = $1::uuid AND d.subject_type = $2 AND d.subject_id = $3::uuid
              AND d.global_security_id = $4::uuid AND d.side = $5 AND d.trade_date = $6
              AND (d.quantity IS NULL OR d.quantity = $7) AND d.valid_to IS NULL AND d.system_to IS NULL
              AND NOT EXISTS (SELECT 1 FROM {T_REC} h WHERE h.matched_declared_id = d.id AND h.valid_to IS NULL
                              AND h.system_to IS NULL)
            ORDER BY d.created_at LIMIT 1""",
        str(org_id), st, sid, txn["security_id"], txn["side"], date.fromisoformat(txn["trade_date"]),
        Decimal(txn["quantity"]))
    return str(row) if row else None


async def _apply_import(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    """Apply a decided import task. Idempotent. Approve: replace the account's
    live positions as of the import's date (earlier rows closed, kept), close
    the earlier open lots, add the lots and transactions, record provenance,
    match declared trades, move the source's last as-of and expire the
    account's confirmations of older data."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            imp = await conn.fetchrow(f"SELECT * FROM {T_IMP} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if imp is None or imp["status"] != "draft":
                return dict(imp) if imp else None
            imp = dict(imp)
            checker = uuid.UUID(str(actor_id))
            if checker == imp["entered_by"]:
                raise MakerCheckerViolation("maker-checker: the person who entered an import never approves it")
            now = datetime.now(timezone.utc)
            if outcome != "approve":
                new = await _decide_row(conn, imp, "rejected", checker, now, note)
                await _audit(conn, org_id, checker, "import_rejected", imp["id"], {"note": note})
                return new
            account = await load_account(conn, org_id, imp["account_id"])
            _need_held_away(account)
            newer = await conn.fetchval(
                "SELECT max(as_of_date) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid "
                "AND valid_to IS NULL AND system_to IS NULL", str(org_id), str(account["id"]))
            if newer is not None and newer > imp["as_of"]:
                raise HeldAwayConflict(f"the account already holds data as of {newer.isoformat()}, newer than this "
                                       f"import's {imp['as_of'].isoformat()} — reject it")
            payload = _loads(imp["payload"]) or {}
            src = await _source(conn, org_id, imp["source_id"])
            authority, system = AUTHORITY[imp["kind"]], SOURCE_SYSTEM[imp["kind"]]
            why = f"replaced by held-away import {imp['id']} as of {imp['as_of'].isoformat()}"
            # 1. the earlier live positions and open lots → closed (kept); their provenance too
            old_pos = [r["id"] for r in await conn.fetch(
                "SELECT id FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid "
                "AND valid_to IS NULL AND system_to IS NULL", str(org_id), str(account["id"]))]
            if old_pos:
                old_lots = [r["id"] for r in await conn.fetch(
                    "SELECT id FROM portfolio.tax_lots WHERE position_id = ANY($1::uuid[]) AND status = 'open' "
                    "AND valid_to IS NULL AND system_to IS NULL", old_pos)]
                await conn.execute("UPDATE portfolio.positions SET valid_to = now() WHERE id = ANY($1::uuid[])", old_pos)
                if old_lots:
                    await conn.execute("UPDATE portfolio.tax_lots SET valid_to = now() WHERE id = ANY($1::uuid[])",
                                       old_lots)
                await conn.execute(
                    f"UPDATE {T_REC} SET valid_to = now(), superseded_reason = $2 WHERE record_id = ANY($1::uuid[]) "
                    "AND record_type IN ('position', 'tax_lot') AND valid_to IS NULL AND system_to IS NULL",
                    old_pos + old_lots, why)
            # 2. positions as of the import's date
            pos_by_sec: dict[str, str] = {}
            lots_by_sec: dict[str, list] = {}
            for x in payload.get("lots", []):
                lots_by_sec.setdefault(x["security_id"], []).append(x)
            for p in payload.get("positions", []):
                ls = lots_by_sec.get(p["security_id"], [])
                cost = (sum((Decimal(x["cost"]) for x in ls), Decimal(0))
                        if ls and all(x["cost"] is not None for x in ls)
                        and sum((Decimal(x["quantity"]) for x in ls), Decimal(0)) == Decimal(p["quantity"]) else None)
                pid = await conn.fetchval(
                    """INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, quantity,
                           cost_basis, market_value, authority, source_system, account_id)
                       VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10::uuid) RETURNING id""",
                    str(org_id), str(account["primary_entity_id"]), await _asset_for(conn, org_id, p["security_id"]),
                    imp["as_of"], Decimal(p["quantity"]), cost, _dec(p["market_value"]), authority, system,
                    str(account["id"]))
                pos_by_sec[p["security_id"]] = str(pid)
                await _record(conn, org_id, imp, "position", pid)
            # 3. open lots
            for x in payload.get("lots", []):
                cost = _dec(x["cost"])
                lid = await conn.fetchval(
                    """INSERT INTO portfolio.tax_lots (org_id, position_id, acquired_date, quantity, original_cost,
                           adjusted_cost, term, status, source_system)
                       VALUES ($1::uuid, $2::uuid, $3, $4, $5, $5, $6, 'open', $7) RETURNING id""",
                    str(org_id), pos_by_sec[x["security_id"]],
                    date.fromisoformat(x["acquired_date"]) if x["acquired_date"] else None, Decimal(x["quantity"]),
                    cost, x["term"], LOT_SOURCE_SYSTEM)
                await _record(conn, org_id, imp, "tax_lot", lid)
            # 4. transactions (a sale with a realized result closes a lot of its own); one already recorded is skipped
            skipped, matched = 0, 0
            for t in payload.get("transactions", []):
                td = date.fromisoformat(t["trade_date"])
                dup = await conn.fetchval(
                    f"""SELECT x.id FROM portfolio.transactions x JOIN portfolio.positions p ON p.id = x.position_id
                        JOIN portfolio.assets a ON a.id = p.asset_id
                        JOIN {T_REC} h ON h.record_type = 'transaction' AND h.record_id = x.id AND h.system_to IS NULL
                        WHERE p.account_id = $1::uuid AND a.global_security_id = $2::uuid AND x.transaction_type_code = $3
                          AND x.trade_date = $4 AND x.quantity = $5 AND x.valid_to IS NULL AND x.system_to IS NULL
                        LIMIT 1""", str(account["id"]), t["security_id"], t["side"], td, Decimal(t["quantity"]))
                if dup:
                    skipped += 1
                    continue
                pid = pos_by_sec.get(t["security_id"])
                if pid is None:
                    # sold out before the as-of: a zero position on the date carries the trade
                    pid = str(await conn.fetchval(
                        """INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, quantity,
                               market_value, authority, source_system, account_id)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 0, 0, $5, $6, $7::uuid) RETURNING id""",
                        str(org_id), str(account["primary_entity_id"]),
                        await _asset_for(conn, org_id, t["security_id"]), imp["as_of"], authority, system,
                        str(account["id"])))
                    pos_by_sec[t["security_id"]] = pid
                    await _record(conn, org_id, imp, "position", pid)
                q, px = Decimal(t["quantity"]), _dec(t["price"])
                xid = await conn.fetchval(
                    """INSERT INTO portfolio.transactions (org_id, position_id, transaction_type_code, trade_date,
                           quantity, price, gross_amount, authority, source_system)
                       VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
                    str(org_id), pid, t["side"], td, q, px, None if px is None else q * px, authority, system)
                m = await match_declared(conn, org_id, account, t)
                matched += 1 if m else 0
                await _record(conn, org_id, imp, "transaction", xid, matched=m)
                if t["side"] == "sell" and t["realized_gain_loss"] is not None:
                    lid = await conn.fetchval(
                        """INSERT INTO portfolio.tax_lots (org_id, position_id, closing_transaction_id, acquired_date,
                               quantity, original_cost, adjusted_cost, term, realized_gain_loss, status, source_system)
                           VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $6, $7, $8, 'closed', $9) RETURNING id""",
                        str(org_id), pid, str(xid),
                        date.fromisoformat(t["acquired_date"]) if t.get("acquired_date") else None, q,
                        _dec(t.get("cost")), t["term"], Decimal(t["realized_gain_loss"]), LOT_SOURCE_SYSTEM)
                    await _record(conn, org_id, imp, "tax_lot", lid)
            # 5. the source's last as-of; the account's confirmations of older data expire
            if src["last_as_of"] is None or src["last_as_of"] < imp["as_of"]:
                await _update_source(conn, src, {"last_as_of": imp["as_of"]})
            await _expire_confirmations(conn, org_id, account_id=account["id"], as_of=imp["as_of"],
                                        reason=f"newer held-away data arrived (as of {imp['as_of'].isoformat()})")
            new = await _decide_row(conn, imp, "approved", checker, now, note)
            await _audit(conn, org_id, checker, "import_approved", imp["id"],
                         {"positions": len(payload.get("positions", [])), "lots": len(payload.get("lots", [])),
                          "transactions": len(payload.get("transactions", [])) - skipped,
                          "already_recorded": skipped, "declared_matched": matched, "closed_positions": len(old_pos)})
    await _publish(pool, org_id, "import_approved", imp["id"], {"account_id": str(imp["account_id"])}, actor_id)
    return {**new, "already_recorded": skipped, "declared_matched": matched}


async def _on_import_task(pool, task, outcome, actor_id, note):
    return await _apply_import(pool, task["org_id"], task, outcome, actor_id, note)


async def sync_import(pool, *, org_id, import_id) -> None:
    async with pool.acquire() as conn:
        imp = await _import(conn, org_id, import_id)
        if imp["status"] != "draft" or not imp["approval_task_id"]:
            return
        task = await HT.get_task(conn, org_id, imp["approval_task_id"])
    if task["status"] == "completed" and task["outcome"] in ("approve", "reject") and task["completed_by"]:
        await _apply_import(pool, org_id, task, task["outcome"], task["completed_by"], task["note"])


async def _import_precheck(pool, task, outcome, actor_id):
    """Refused BEFORE the task commits (the import stays a draft with its task
    open): approving a refused file, or data older than the account's."""
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        imp = await conn.fetchrow(f"SELECT * FROM {T_IMP} WHERE approval_task_id = $1::uuid AND system_to IS NULL",
                                  task["id"])
        if imp is None or imp["status"] != "draft":
            return
        if (_loads(imp["extraction"]) or {}).get("refused"):
            raise HT.HumanTaskConflict("the file was refused — reject this import and load a corrected file")
        newer = await conn.fetchval(
            "SELECT max(as_of_date) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid "
            "AND valid_to IS NULL AND system_to IS NULL", imp["org_id"], imp["account_id"])
    if newer is not None and newer > imp["as_of"]:
        raise HT.HumanTaskConflict(f"the account already holds data as of {newer.isoformat()}, newer than this "
                                   f"import's {imp['as_of'].isoformat()} — reject it")


HT.register_kind(TASK_IMPORT, label="Held-away import approval", outcomes=("approve", "reject"),
                 maker_checker=True, on_complete=_on_import_task)
HT.register_precheck(TASK_IMPORT, _import_precheck)


# ── Reading ─────────────────────────────────────────────────────────────────
async def list_imports(conn, org_id, account_id, limit: int = 100) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_IMP} WHERE org_id = $1::uuid AND account_id = $2::uuid AND system_to IS NULL "
        "ORDER BY created_at DESC LIMIT $3", str(org_id), _uuid(account_id, "account"), limit)]


async def get_import(conn, org_id, import_id) -> dict:
    return await _import(conn, org_id, import_id)


async def positions_of(conn, org_id, account_id, *, as_of: date | None = None) -> dict:
    """The account's held-away positions and lots with source and as-of —
    the live version, or the version as of a past date."""
    aid = _uuid(account_id, "account")
    if as_of is None:
        flt, args = "p.valid_to IS NULL", [str(org_id), aid]
    else:
        flt, args = ("p.as_of_date = (SELECT max(as_of_date) FROM portfolio.positions WHERE account_id = $2::uuid "
                     "AND org_id = $1::uuid AND system_to IS NULL AND as_of_date <= $3)"), [str(org_id), aid, as_of]
    rows = await conn.fetch(
        f"""SELECT p.id, p.as_of_date, p.quantity, p.market_value, p.cost_basis, p.authority, p.source_system,
                   p.valid_to, a.global_security_id, COALESCE(g.name, a.name) AS name,
                   (SELECT id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = g.id
                     AND t.id_type = 'ticker' AND t.system_to IS NULL AND t.valid_to IS NULL LIMIT 1) AS ticker,
                   h.import_id, h.source_id, s.kind AS source_kind, s.provider
            FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id
            LEFT JOIN portfolio.securities_global g ON g.id = a.global_security_id AND g.system_to IS NULL
              AND g.valid_to IS NULL
            LEFT JOIN {T_REC} h ON h.record_type = 'position' AND h.record_id = p.id AND h.system_to IS NULL
            LEFT JOIN {T_SRC} s ON s.id = h.source_id
            WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid AND p.system_to IS NULL AND {flt}
            ORDER BY name""", *args)
    pos = []
    for r in rows:
        lots = await conn.fetch(
            f"""SELECT tl.id, tl.acquired_date, tl.quantity, tl.original_cost, tl.adjusted_cost, tl.term, tl.status,
                       tl.realized_gain_loss, h.import_id FROM portfolio.tax_lots tl
                LEFT JOIN {T_REC} h ON h.record_type = 'tax_lot' AND h.record_id = tl.id AND h.system_to IS NULL
                WHERE tl.position_id = $1 AND tl.system_to IS NULL ORDER BY tl.acquired_date NULLS LAST""", r["id"])
        pos.append({"id": str(r["id"]), "security_id": str(r["global_security_id"]) if r["global_security_id"] else None,
                    "label": f"{r['name']} ({r['ticker']})" if r["ticker"] else r["name"],
                    "quantity": _num(r["quantity"]), "market_value": _num(r["market_value"]),
                    "cost_basis": _num(r["cost_basis"]), "as_of": _iso(r["as_of_date"]),
                    "source": r["source_kind"] or r["source_system"], "provider": r["provider"],
                    "import_id": str(r["import_id"]) if r["import_id"] else None, "current": r["valid_to"] is None,
                    "lots": [{"id": str(x["id"]), "acquired_date": _iso(x["acquired_date"]),
                              "quantity": _num(x["quantity"]),
                              "cost": _num(x["adjusted_cost"] if x["adjusted_cost"] is not None else x["original_cost"]),
                              "term": x["term"], "status": x["status"], "realized_gain_loss": _num(x["realized_gain_loss"]),
                              "import_id": str(x["import_id"]) if x["import_id"] else None} for x in lots]})
    versions = [{"as_of": _iso(r["as_of_date"]), "positions": r["n"], "current": r["live"]} for r in await conn.fetch(
        "SELECT as_of_date, count(*) AS n, bool_or(valid_to IS NULL) AS live FROM portfolio.positions "
        "WHERE org_id = $1::uuid AND account_id = $2::uuid AND system_to IS NULL GROUP BY as_of_date "
        "ORDER BY as_of_date DESC", str(org_id), aid)]
    return {"positions": pos, "versions": versions}


async def transactions_of(conn, org_id, account_id, limit: int = 200) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT x.id, x.transaction_type_code, x.trade_date, x.quantity, x.price, a.global_security_id,
                   COALESCE(g.name, a.name) AS name, h.as_of, h.matched_declared_id, s.kind AS source_kind
            FROM portfolio.transactions x JOIN portfolio.positions p ON p.id = x.position_id
            JOIN portfolio.assets a ON a.id = p.asset_id
            LEFT JOIN portfolio.securities_global g ON g.id = a.global_security_id AND g.system_to IS NULL
              AND g.valid_to IS NULL
            LEFT JOIN {T_REC} h ON h.record_type = 'transaction' AND h.record_id = x.id AND h.system_to IS NULL
            LEFT JOIN {T_SRC} s ON s.id = h.source_id
            WHERE x.org_id = $1::uuid AND p.account_id = $2::uuid AND x.valid_to IS NULL AND x.system_to IS NULL
            ORDER BY x.trade_date DESC LIMIT $3""", str(org_id), _uuid(account_id, "account"), limit)
    return [{"id": str(r["id"]), "side": r["transaction_type_code"], "trade_date": _iso(r["trade_date"]),
             "quantity": _num(r["quantity"]), "price": _num(r["price"]), "label": r["name"],
             "security_id": str(r["global_security_id"]) if r["global_security_id"] else None,
             "as_of": _iso(r["as_of"]), "source": r["source_kind"],
             "matched_declared_id": str(r["matched_declared_id"]) if r["matched_declared_id"] else None}
            for r in rows]


def source_out(r: dict, *, today: date, default_days: int) -> dict:
    days = r["stale_after_days"] or default_days
    last = r["last_as_of"]
    stale = r["kind"] not in ("aggregator", "custodian_feed") and is_stale(last, today, days)
    return {"id": str(r["id"]), "kind": r["kind"], "provider": r["provider"], "status": r["status"],
            "last_as_of": _iso(last), "stale_after_days": r["stale_after_days"], "effective_stale_after_days": days,
            "stale": stale, "notes": r["notes"], "created_at": _iso(r["created_at"])}


def import_out(r: dict) -> dict:
    return {"id": str(r["id"]), "account_id": str(r["account_id"]), "source_id": str(r["source_id"]),
            "kind": r["kind"], "as_of": _iso(r["as_of"]),
            "document_id": str(r["document_id"]) if r["document_id"] else None, "file_name": r["file_name"],
            "mapping": _loads(r["mapping"]) or {}, "rows_read": r["rows_read"], "rows_accepted": r["rows_accepted"],
            "rows_rejected": r["rows_rejected"], "rejects": _loads(r["rejects"]) or [],
            "payload": _loads(r["payload"]) or {}, "extraction": _loads(r["extraction"]) or {}, "status": r["status"],
            "entered_by": str(r["entered_by"]), "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"], "notes": r["notes"],
            "created_at": _iso(r["created_at"])}


def import_actions(r: dict, *, can_approve: bool, user_id) -> list[str]:
    if r["status"] != "draft" or not can_approve or str(r["entered_by"]) == str(user_id):
        return []
    if (_loads(r["extraction"]) or {}).get("refused"):
        return ["reject"]
    return ["approve", "reject"]


# ── Staleness ───────────────────────────────────────────────────────────────
def is_stale(as_of: date | None, today: date, days: int) -> bool:
    """Exactly ``days`` old is NOT stale; ``days + 1`` is. No data is stale."""
    return as_of is None or (today - as_of).days > int(days)


async def account_as_of(conn, org_id, account_id) -> date | None:
    """The held-away account's data date: its live positions' as-of."""
    return await conn.fetchval("SELECT max(as_of_date) FROM portfolio.positions WHERE org_id = $1::uuid "
                               "AND account_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL",
                               str(org_id), str(account_id))


async def stale_days_for(conn, org_id, account_id) -> tuple[int, str]:
    v = await conn.fetchval(f"SELECT min(stale_after_days) FROM {T_SRC} WHERE org_id = $1::uuid AND account_id = "
                            "$2::uuid AND status <> 'retired' AND valid_to IS NULL AND system_to IS NULL",
                            str(org_id), str(account_id))
    if v:
        return int(v), "source"
    return await _setting_days(conn, org_id), "organisation"


async def dependencies(conn, org_id, account: dict) -> list[dict]:
    """The held-away accounts a proposal for ``account`` depends on: every
    held-away account of its household (wash-sale control) and of its tax
    subject (the gain budget) — and, rep7b, the account itself when it is
    held away (its data comes from outside the integrated feed)."""
    from services import rebalance_proposals as RP

    accts = {str(a["id"]): a for a in await RP.household_accounts(conn, org_id, account)}
    for a in await subject_accounts(conn, org_id, account):
        accts.setdefault(str(a["id"]), a)
    # rep7b — a held-away account that is itself proposed for (advise, or managed at another custodian) depends on
    # its OWN held-away data too; before rep7b no held-away account could have a proposal, so nothing changes for one
    # that existed.
    ids = [k for k in accts if k != str(account["id"]) or account.get("is_held_away")]
    if account.get("is_held_away") and str(account["id"]) not in ids:
        ids.append(str(account["id"]))
    if not ids:
        return []
    rows = await conn.fetch("SELECT id, account_number_masked, registration_type, tax_status FROM public.accounts "
                            "WHERE id = ANY($1::uuid[]) AND org_id = $2::uuid AND is_held_away AND system_to IS NULL "
                            "AND valid_to IS NULL ORDER BY account_number_masked", ids, str(org_id))
    return [dict(r) for r in rows]


async def subject_accounts(conn, org_id, account: dict) -> list[dict]:
    """Every account of the same tax subject (rep6: the household, else the
    primary entity)."""
    if account.get("household_id"):
        sql, arg = "household_id = $2::uuid", str(account["household_id"])
    else:
        sql, arg = "household_id IS NULL AND primary_entity_id = $2::uuid", str(account["primary_entity_id"])
    return [dict(r) for r in await conn.fetch(
        f"SELECT id, account_number_masked, registration_type, tax_status, is_held_away FROM public.accounts "
        f"WHERE org_id = $1::uuid AND {sql} AND system_to IS NULL AND valid_to IS NULL", str(org_id), arg)]


async def _live_confirmation(conn, org_id, account_id, *, proposal_id=None, purpose: str = "proposal") -> dict | None:
    row = await conn.fetchrow(
        f"SELECT * FROM {T_CONF} WHERE org_id = $1::uuid AND account_id = $2::uuid AND purpose = $3 "
        "AND proposal_id IS NOT DISTINCT FROM $4::uuid AND status IN ('pending', 'confirmed') AND valid_to IS NULL "
        "AND system_to IS NULL", str(org_id), str(account_id), purpose, str(proposal_id) if proposal_id else None)
    return dict(row) if row else None


def confirmation_holds(c: dict | None, as_of: date | None, today: date) -> bool:
    return bool(c and c["status"] == "confirmed" and as_of is not None and c["as_of_confirmed"] == as_of
                and c["expires_on"] is not None and c["expires_on"] >= today)


async def staleness(conn, org_id, account: dict, today: date, *, proposal_id=None) -> list[dict]:
    """Every held-away dependency with its as-of, threshold and confirmation;
    ``blocking`` = stale and not confirmed for this proposal."""
    out = []
    for d in await dependencies(conn, org_id, account):
        as_of = await account_as_of(conn, org_id, d["id"])
        days, origin = await stale_days_for(conn, org_id, d["id"])
        stale = is_stale(as_of, today, days)
        c = await _live_confirmation(conn, org_id, d["id"], proposal_id=proposal_id) if proposal_id else None
        ok = confirmation_holds(c, as_of, today)
        out.append({"account_id": str(d["id"]), "account": d["account_number_masked"], "as_of": _iso(as_of),
                    "age_days": None if as_of is None else (today - as_of).days, "stale_after_days": days,
                    "threshold_from": origin, "stale": stale, "confirmed": ok, "blocking": stale and not ok,
                    "confirmation": confirmation_out(c) if c else None,
                    "message": (None if not stale else
                                f"held-away account {d['account_number_masked']}: data as of "
                                f"{as_of.isoformat() if as_of else 'never'} is older than {days} day(s)"
                                + (" — confirmed" if ok else " — an adviser must confirm it before approval"))})
    return out


def confirmation_out(c: dict) -> dict:
    return {"id": str(c["id"]), "account_id": str(c["account_id"]), "as_of_confirmed": _iso(c["as_of_confirmed"]),
            "purpose": c["purpose"], "proposal_id": str(c["proposal_id"]) if c["proposal_id"] else None,
            "status": c["status"], "task_id": str(c["task_id"]) if c["task_id"] else None,
            "confirmed_by": str(c["confirmed_by"]) if c["confirmed_by"] else None,
            "confirmed_at": _iso(c["confirmed_at"]), "expires_on": _iso(c["expires_on"]),
            "closed_reason": c["closed_reason"], "note": c["note"]}


async def _update_conf(conn, row: dict, fields: dict) -> dict:
    await conn.execute(
        f"""INSERT INTO {T_CONF} (org_id, account_id, as_of_confirmed, purpose, proposal_id, status, requested_by,
                task_id, confirmed_by, confirmed_at, expires_on, closed_reason, note, archived_from_id, created_at,
                valid_from, valid_to, system_from, system_to)
            SELECT org_id, account_id, as_of_confirmed, purpose, proposal_id, status, requested_by, task_id, confirmed_by,
                   confirmed_at, expires_on, closed_reason, note, id, created_at, valid_from, valid_to, system_from, now()
            FROM {T_CONF} WHERE id = $1""", row["id"])
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    return dict(await conn.fetchrow(f"UPDATE {T_CONF} SET {sets}, system_from = now() WHERE id = $1 RETURNING *",
                                    row["id"], *fields.values()))


async def request_confirmation(pool, *, org_id, account_id, proposal_id=None, actor_id=None,
                               purpose: str = "proposal") -> dict:
    """Open (or return) the 'confirm_held_away_data' task for one held-away
    account — by a person, or by the platform for a proposal it generated."""
    async with pool.acquire() as conn:
        if actor_id:
            await HT.assert_human(conn, actor_id, org_id)
        account = await load_account(conn, org_id, account_id)
        _need_held_away(account)
        cur = await _live_confirmation(conn, org_id, account["id"], proposal_id=proposal_id, purpose=purpose)
        as_of = await account_as_of(conn, org_id, account["id"])
    if cur:
        return cur
    cid = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_CONFIRM, subject_type=SUBJECT_CONFIRM, subject_id=cid, maker_id=None,
        eligible_permission=MANAGE,
        title=f"Confirm held-away data: {account['account_number_masked']} as of "
              f"{as_of.isoformat() if as_of else 'never'}",
        payload={"account": account["account_number_masked"], "as_of": _iso(as_of),
                 "proposal_id": str(proposal_id) if proposal_id else None},
        action_path=f"/portfolio/proposals/{proposal_id}" if proposal_id else f"/portfolio/accounts/{account['id']}",
        actor_kind="system")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_CONF} (id, org_id, account_id, as_of_confirmed, purpose, proposal_id, status,
                            requested_by, task_id)
                        VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6::uuid, 'pending', $7::uuid, $8::uuid) RETURNING *""",
                    cid, str(org_id), str(account["id"]), as_of or date(1900, 1, 1), purpose,
                    str(proposal_id) if proposal_id else None, str(actor_id) if actor_id else None, task["id"])
                await _audit(conn, org_id, actor_id, "confirmation_requested", cid,
                             {"account_id": str(account["id"]), "as_of": _iso(as_of), "task_id": task["id"],
                              "proposal_id": str(proposal_id) if proposal_id else None})
    except Exception:
        await _withdraw_task(pool, task["id"], "the confirmation could not be recorded")
        raise
    return dict(row)


async def _on_confirm_task(pool, task, outcome, actor_id, note):
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_CONF} WHERE task_id = $1::uuid AND system_to IS NULL "
                                      "FOR UPDATE", task["id"])
            if row is None or row["status"] != "pending":
                return dict(row) if row else None
            row = dict(row)
            as_of = await account_as_of(conn, org_id, row["account_id"])
            days, _ = await stale_days_for(conn, org_id, row["account_id"])
            today = date.today()
            new = await _update_conf(conn, row, {"status": "confirmed", "confirmed_by": uuid.UUID(str(actor_id)),
                                                 "confirmed_at": datetime.now(timezone.utc),
                                                 "as_of_confirmed": as_of or date(1900, 1, 1),
                                                 "expires_on": today + timedelta(days=days), "note": note,
                                                 "task_id": row["task_id"]})
            await _audit(conn, org_id, actor_id, "data_confirmed", row["id"],
                         {"account_id": str(row["account_id"]), "as_of_confirmed": _iso(as_of),
                          "expires_on": (today + timedelta(days=days)).isoformat(),
                          "proposal_id": str(row["proposal_id"]) if row["proposal_id"] else None})
    await _publish(pool, org_id, "data_confirmed", new["id"], {"account_id": str(new["account_id"])}, actor_id)
    return new


async def confirm(pool, *, org_id, actor_id, confirmation_id, note: str | None = None) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {T_CONF} WHERE id = $1::uuid AND org_id = $2::uuid "
                                  "AND system_to IS NULL", _uuid(confirmation_id, "confirmation"), str(org_id))
    if row is None:
        raise HeldAwayNotFound("confirmation not found")
    if row["status"] != "pending" or not row["task_id"]:
        raise HeldAwayConflict(f"this confirmation is {row['status']}")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["task_id"], actor_id=actor_id, outcome="done",
                                      note=note)
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    return done.get("subject_result") or dict(row)


HT.register_kind(TASK_CONFIRM, label="Confirm held-away data", outcomes=("done",), maker_checker=False,
                 on_complete=_on_confirm_task, system_open=True)


async def _expire_confirmations(conn, org_id, *, account_id=None, as_of: date | None = None, today: date | None = None,
                                reason: str | None = None) -> list[str]:
    """Confirmed ones of older data (newer data arrived), and confirmed ones
    past their expiry, become 'expired'."""
    today = today or date.today()
    args, flt = [str(org_id)], ""
    if account_id:
        args.append(str(account_id))
        flt = " AND account_id = $2::uuid"
    out = []
    for r in await conn.fetch(f"SELECT * FROM {T_CONF} WHERE org_id = $1::uuid{flt} AND status = 'confirmed' "
                              "AND valid_to IS NULL AND system_to IS NULL", *args):
        r = dict(r)
        cur = as_of if as_of is not None else await account_as_of(conn, org_id, r["account_id"])
        why = None
        if cur is not None and cur > r["as_of_confirmed"]:
            why = reason or f"newer held-away data arrived (as of {cur.isoformat()})"
        elif r["expires_on"] is not None and r["expires_on"] < today:
            why = f"the confirmation passed its expiry ({r['expires_on'].isoformat()})"
        if why:
            await _update_conf(conn, r, {"status": "expired", "closed_reason": why, "task_id": r["task_id"]})
            out.append(str(r["id"]))
    return out


async def staleness_tick(pool, *, org_id, today: date | None = None) -> dict:
    """The daily staleness check (the caller set the org's RLS context):
    expire confirmations, and open the confirm task for every open proposal
    that depends on stale held-away data. Never trades, never proposes."""
    from services import rebalance_proposals as RP

    today = today or date.today()
    out = {"expired": [], "requested": []}
    async with pool.acquire() as conn:
        async with conn.transaction():
            out["expired"] = await _expire_confirmations(conn, org_id, today=today)
        props = [dict(r) for r in await conn.fetch(
            f"SELECT id, account_id FROM {RP.T_P} WHERE org_id = $1::uuid AND status IN ('draft', 'pending_approval') "
            "AND system_to IS NULL", str(org_id))]
    for p in props:
        try:
            out["requested"] += await ensure_proposal_confirmations(pool, org_id=org_id, proposal_id=p["id"],
                                                                    account_id=p["account_id"], today=today)
        except Exception as exc:  # noqa: BLE001 — one proposal never stops the others
            log.warning("staleness check for proposal %s: %s", p["id"], exc)
    return out


async def ensure_proposal_confirmations(pool, *, org_id, proposal_id, account_id, today: date | None = None) -> list[str]:
    today = today or date.today()
    async with pool.acquire() as conn:
        account = await load_account(conn, org_id, account_id)
        st = await staleness(conn, org_id, account, today, proposal_id=proposal_id)
    out = []
    for s in st:
        if s["blocking"] and not (s["confirmation"] and s["confirmation"]["status"] == "pending"):
            c = await request_confirmation(pool, org_id=org_id, account_id=s["account_id"], proposal_id=proposal_id)
            out.append(str(c["id"]))
    return out


async def proposal_held_away(conn, org_id, p: dict, today: date | None = None) -> dict:
    """The proposal view's held-away block: every dependency's as-of and
    whether approval waits for a confirmation."""
    from services import account_models as AM

    account = await AM.load_account(conn, org_id, p["account_id"])
    st = await staleness(conn, org_id, account, today or date.today(), proposal_id=p["id"])
    return {"dependencies": st, "blocking": any(s["blocking"] for s in st),
            "stale_label": "stale held-away data — confirm before approval" if any(s["blocking"] for s in st) else None}


async def _approval_precheck(pool, task, outcome, actor_id):
    """rep5's approval / review task: refused (task left open) while the
    proposal depends on stale, unconfirmed held-away data."""
    if outcome != "approve":
        return
    from services import rebalance_proposals as RP

    async with pool.acquire() as conn:
        p = await conn.fetchrow(f"SELECT id, account_id, org_id FROM {RP.T_P} WHERE approval_task_id = $1::uuid "
                                "AND system_to IS NULL", task["id"])
        if p is None:
            return
        block = await proposal_held_away(conn, p["org_id"], dict(p))
    if block["blocking"]:
        await ensure_proposal_confirmations(pool, org_id=p["org_id"], proposal_id=p["id"], account_id=p["account_id"])
        msgs = "; ".join(s["message"] for s in block["dependencies"] if s["blocking"])
        raise HT.HumanTaskConflict(f"approval waits for an adviser to confirm stale held-away data ({TASK_CONFIRM}): "
                                   f"{msgs}")


def _register_prechecks() -> None:
    from services import rebalance_proposals as RP

    HT.register_precheck(RP.TASK_APPROVAL, _approval_precheck)
    HT.register_precheck(RP.TASK_REVIEW, _approval_precheck)


# ── Realized elsewhere (the gain budget) ────────────────────────────────────
async def realized_elsewhere(conn, org_id, account: dict, today: date) -> dict:
    """Gains realized this year in held-away accounts of the account's tax
    subject (closed lots, by the closing transaction's trade date — the same
    rule as rep5's realized_ytd). The account itself is excluded."""
    others = [str(a["id"]) for a in await subject_accounts(conn, org_id, account)
              if a["is_held_away"] and str(a["id"]) != str(account["id"])]
    if not others:
        return {"amount": Decimal(0), "lots": 0, "accounts": []}
    r = await conn.fetchrow(
        """SELECT COALESCE(sum(tl.realized_gain_loss), 0) AS amt, count(*) AS n,
                  array_agg(DISTINCT p.account_id) AS accts
           FROM portfolio.tax_lots tl
           JOIN portfolio.positions p ON p.id = tl.position_id
           JOIN portfolio.transactions x ON x.id = tl.closing_transaction_id AND x.system_to IS NULL
           WHERE tl.org_id = $1::uuid AND p.account_id = ANY($2::uuid[]) AND tl.status = 'closed'
             AND tl.valid_to IS NULL AND tl.system_to IS NULL AND tl.realized_gain_loss IS NOT NULL
             AND x.trade_date >= $3 AND x.trade_date <= $4""",
        str(org_id), others, date(today.year, 1, 1), today)
    return {"amount": Decimal(str(r["amt"] or 0)), "lots": int(r["n"] or 0),
            "accounts": [str(x) for x in (r["accts"] or []) if x]}


# ── Account panel ───────────────────────────────────────────────────────────
async def account_panel(conn, org_id, account_id, today: date | None = None) -> dict:
    today = today or date.today()
    account = await load_account(conn, org_id, account_id)
    default_days = await _setting_days(conn, org_id)
    srcs = await list_sources(conn, org_id, account["id"])
    as_of = await account_as_of(conn, org_id, account["id"])
    days, origin = await stale_days_for(conn, org_id, account["id"])
    confs = [confirmation_out(dict(r)) for r in await conn.fetch(
        f"SELECT * FROM {T_CONF} WHERE org_id = $1::uuid AND account_id = $2::uuid AND valid_to IS NULL "
        "AND system_to IS NULL ORDER BY created_at DESC LIMIT 20", str(org_id), str(account["id"]))]
    return {"account": {"id": str(account["id"]), "account_number_masked": account["account_number_masked"],
                        "custodian_code": account["custodian_code"], "registration_type": account["registration_type"],
                        "tax_status": account["tax_status"], "is_held_away": account["is_held_away"],
                        "household_id": str(account["household_id"]) if account["household_id"] else None},
            "as_of": _iso(as_of), "stale_after_days": days, "threshold_from": origin,
            "stale": bool(account["is_held_away"]) and is_stale(as_of, today, days),
            "sources": [source_out(s, today=today, default_days=default_days) for s in srcs],
            "providers": AD.providers(), "statement_extraction": await extraction_status(conn),
            "statements": await statement_documents(conn, org_id, account["id"]),
            "confirmations": confs, **_trade_brief(account)}


def _trade_brief(account: dict) -> dict:
    """rep7b — what the account's authority allows (read-only by default: no trade control)."""
    from services import authority as AU

    paths = AU.account_actions(account)
    e = AU.effective(account)
    note = ("held away — Hollisworks never trades this account; moving the assets comes first"
            if e["authority"] == "read_only" else
            "held away — advise: one-off trades the member or another adviser executes" if e["authority"] == "advise"
            else "held away — managed at another custodian: manual DVP settlement instructions")
    return {"trade_controls": bool(paths), "trade_paths": paths, "authority": AU.authority_out(account),
            "trade_note": note}


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"source_kinds": [{"key": k, "label": SOURCE_KIND_LABELS[k], "rank": i + 1}
                             for i, k in enumerate(SOURCE_ORDER)],
            "source_statuses": pairs(SOURCE_STATUS_LABELS), "import_statuses": pairs(IMPORT_STATUS_LABELS),
            "reject_reasons": pairs(REJECT_REASONS), "confirmation_statuses": pairs(CONFIRM_STATUS_LABELS),
            "file_fields": [{"key": k, "label": v[0]} for k, v in FIELDS.items()],
            "providers": AD.providers()}
