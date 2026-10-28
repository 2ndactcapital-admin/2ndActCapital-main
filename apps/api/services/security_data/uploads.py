"""csv_upload — prices, corporate actions and index levels from a file.

    stage()   file → rep3's machinery (services.model_upload: precheck, reader,
              header detection, mapping with THIS file type's own field
              vocabulary) → row validation with a reason per rejected row →
              securities resolved through the master (held_away.resolve_identifiers,
              rep3's rule order) → ONE data_uploads row, status 'pending_approval',
              the accepted rows staged on it, the uploader's licence recorded, an
              approval to-do for the org's other admins. NOTHING enters a store.
    decide()  maker-checker: the uploader can never decide their own file
              (and the CHECK says so too). Approve → the staged rows are written
              to the canonical stores (provider 'csv_upload', the org's own rows,
              upload_id, the file's licence) in the SAME transaction as the
              decision. Reject → nothing is written; a note is required.

Fund holdings are NOT accepted from a file this sprint (FIND): rep1's holdings
store is platform-only (replica_holdings_snapshots_platform_chk), so an org's
file would publish to every org.

The upload row is SYSTEM-axis: its id is stable (store rows carry upload_id);
a decision archives the pending row and inserts the decided one, same id."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal

from services.security_data import writes as W
from services.security_data.types import MEMBER_DISPLAY, RETENTION, SecurityDataError, SecurityDataNotFound

T_UP = "portfolio.data_uploads"
UPLOAD_TYPES = ("eod_price", "corporate_action", "index_level")
APPROVE_PERMISSION = "manage_org_settings"
TODO_SOURCE = "security_data_upload"
ID_FIELDS = {
    "ticker": ("Ticker", ("ticker", "symbol", "ticker symbol", "trading symbol")),
    "cusip": ("CUSIP", ("cusip", "cusip number")),
    "isin": ("ISIN", ("isin", "isin code")),
    "sedol": ("SEDOL", ("sedol",)),
}
FIELDS = {
    "eod_price": {**ID_FIELDS,
                  "date": ("Date", ("date", "price date", "as of", "as of date", "close date", "trade date")),
                  "price": ("Close", ("close", "price", "closing price", "close price", "last", "adj close")),
                  "currency": ("Currency", ("currency", "ccy", "currency code"))},
    "corporate_action": {**ID_FIELDS,
                         "action_type": ("Action", ("action", "action type", "type", "event", "event type")),
                         "ex_date": ("Ex date", ("ex date", "ex dividend date", "exdate", "effective date")),
                         "record_date": ("Record date", ("record date",)),
                         "pay_date": ("Pay date", ("pay date", "payment date", "payable date")),
                         "amount": ("Amount per share", ("amount", "dividend", "amount per share", "cash amount",
                                                         "rate")),
                         "ratio_from": ("Ratio from", ("ratio from", "old shares", "from")),
                         "ratio_to": ("Ratio to", ("ratio to", "new shares", "to")),
                         "currency": ("Currency", ("currency", "ccy"))},
    # the index identifier is mapped to the field key 'ticker' so rep3's header detection (which needs an
    # identifier column) applies unchanged; it is stored as index_key, never resolved as a security
    "index_level": {"ticker": ("Index", ("index", "index key", "series", "series code", "ticker", "symbol")),
                    "date": ("Date", ("date", "as of", "observation date", "level date")),
                    "value": ("Level", ("level", "value", "close", "rate", "yield"))},
}
REQUIRED = {"eod_price": ("date", "price"), "corporate_action": ("action_type", "ex_date"),
            "index_level": ("date", "value")}
ACTION_TYPES = {"dividend": "cash_dividend", "cash dividend": "cash_dividend", "cash_dividend": "cash_dividend",
                "stock dividend": "stock_dividend", "stock_dividend": "stock_dividend", "split": "split",
                "stock split": "split", "reverse split": "reverse_split", "reverse_split": "reverse_split"}
REJECT_REASONS = {
    "no_identifier": "Row has no identifier (ticker, CUSIP, ISIN or SEDOL)",
    "unresolved": "Identifier not in the security master",
    "identifier_conflict": "Identifiers point at different securities",
    "bad_date": "Date missing or not a date",
    "future_date": "Date is in the future",
    "bad_number": "Value missing or not a number",
    "not_positive": "Value must be greater than zero",
    "bad_action": "Action is not a dividend, stock dividend, split or reverse split",
    "split_ratio": "A split needs a ratio from and to, both greater than zero",
    "dividend_amount": "A dividend needs an amount per share greater than zero",
    "bad_index_key": "Index key missing or has characters other than letters, digits and . _ ^ : -",
    "duplicate": "Same item and date listed twice in the file",
    "formula_no_value": "Formula with no saved value",
    "error_cell": "Spreadsheet error value",
}


class UploadMakerChecker(PermissionError):
    pass


def check_licence(lic: dict | None) -> dict:
    """The uploader states the file's rights; nothing is assumed."""
    if not isinstance(lic, dict):
        raise SecurityDataError("the file's licence is required: internal_use, member_display, redistribution, "
                                "retention_after_cancel (and attribution text, if any)")
    out = {"internal_use": lic.get("internal_use"), "member_display": lic.get("member_display"),
           "redistribution": lic.get("redistribution"), "retention_after_cancel": lic.get("retention_after_cancel"),
           "attribution": (lic.get("attribution") or "").strip() or None,
           "terms_note": (lic.get("terms_note") or "").strip() or None}
    if not isinstance(out["internal_use"], bool) or not isinstance(out["redistribution"], bool):
        raise SecurityDataError("licence.internal_use and licence.redistribution must be true or false")
    if out["member_display"] not in MEMBER_DISPLAY or out["member_display"] == "own_holdings_only":
        raise SecurityDataError("licence.member_display must be 'allowed' or 'not_allowed' for a file")
    if out["retention_after_cancel"] not in RETENTION:
        raise SecurityDataError(f"licence.retention_after_cancel must be one of {', '.join(RETENTION)}")
    if out["member_display"] == "allowed" and not out["internal_use"]:
        raise SecurityDataError("a file that may be shown to members must also allow internal use")
    if out["redistribution"] and out["member_display"] != "allowed":
        raise SecurityDataError("redistribution needs member display allowed")
    return out


def _date(v) -> date | None:
    from services import model_upload as U
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = U.cell_text(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _num(v) -> Decimal | None:
    from services import model_upload as U
    try:
        return U.to_decimal(v)
    except ValueError:
        return None


def read_rows(data: bytes, filename: str, data_type: str, *, mapping: dict | None = None):
    """(rows with '_reject' set where the cell itself is bad, mapping by header) — held_away.read_file's
    shape over this type's vocabulary."""
    from services import model_upload as U

    fields = FIELDS[data_type]
    U.precheck(data, filename)
    wb = U.read_workbook(data, filename)
    sh = wb.sheets[0]
    grid = wb.grids[sh]
    # the caller's explicit mapping counts as a saved mapping for header detection (a non-synonym header)
    hdr = U.detect_header(grid, mapping, fields=fields, required=REQUIRED[data_type])
    if not hdr:
        need = ", ".join(fields[f][0] for f in REQUIRED[data_type])
        raise U.UploadRefused("no_header", f"no header row naming an identifier and {need} was found in the first "
                                           "30 rows")
    headers = U.headers_of(grid, hdr)
    cols = U.mapping_from_request(headers, mapping, None, fields=fields)
    if not set(REQUIRED[data_type]) <= set(cols.values()):
        raise U.UploadRefused("mapping", "map " + ", ".join(fields[f][0] for f in REQUIRED[data_type]))
    out = []
    for i, line in enumerate(grid[hdr:], start=hdr + 1):
        if not any(U.cell_text(c) for c in line):
            continue
        rec = {"row": i}
        for ci, fld in cols.items():
            if (sh, i, ci) in wb.formula_gaps:
                rec["_reject"] = ("formula_no_value", f"column {headers[ci]!r}")
            if (sh, i, ci) in wb.error_cells:
                rec["_reject"] = ("error_cell", f"column {headers[ci]!r}")
            rec[fld] = line[ci] if ci < len(line) else None
        if (U.cell_text(rec.get("ticker")) or "").lower().startswith("total"):
            continue
        for t in ID_FIELDS:
            if t in rec:
                rec[t] = U.cell_text(rec[t])
        out.append(rec)
    return out, U.mapping_by_header(headers, cols)


def validate_row(r: dict, data_type: str, today: date) -> None:
    """Sets r['reject'] = (code, detail) or the typed fields."""
    import re
    if r.get("_reject"):
        r["reject"] = r["_reject"]
        return
    if data_type in ("eod_price", "index_level"):
        dt = _date(r.get("date"))
        v = _num(r.get("price" if data_type == "eod_price" else "value"))
        if dt is None:
            r["reject"] = ("bad_date", str(r.get("date")))
        elif dt > today:
            r["reject"] = ("future_date", dt.isoformat())
        elif v is None:
            r["reject"] = ("bad_number", str(r.get("price" if data_type == "eod_price" else "value")))
        elif data_type == "eod_price" and v <= 0:
            r["reject"] = ("not_positive", format(v, "f"))
        else:
            r["d"], r["v"] = dt, v
        if data_type == "index_level" and "reject" not in r:
            key = (r.get("ticker") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_.^:-]{1,60}", key):
                r["reject"] = ("bad_index_key", key)
            else:
                r["index_key"] = key
        return
    act = ACTION_TYPES.get((str(r.get("action_type") or "")).strip().lower())
    ex = _date(r.get("ex_date"))
    if act is None:
        r["reject"] = ("bad_action", str(r.get("action_type")))
    elif ex is None:
        r["reject"] = ("bad_date", str(r.get("ex_date")))
    elif act in ("split", "reverse_split", "stock_dividend") and not (
            (_num(r.get("ratio_from")) or 0) > 0 and (_num(r.get("ratio_to")) or 0) > 0):
        r["reject"] = ("split_ratio", f"{r.get('ratio_from')} : {r.get('ratio_to')}")
    elif act == "cash_dividend" and not (_num(r.get("amount")) or 0) > 0:
        r["reject"] = ("dividend_amount", str(r.get("amount")))
    else:
        r["d"], r["action"] = ex, act
        r["record_date"], r["pay_date"] = _date(r.get("record_date")), _date(r.get("pay_date"))
        terms = {"currency": (r.get("currency") or "USD")}
        if act == "cash_dividend":
            terms["amount_per_share"] = format(_num(r["amount"]), "f")
        else:
            terms.update(ratio_from=format(_num(r["ratio_from"]), "f"), ratio_to=format(_num(r["ratio_to"]), "f"))
        r["terms"] = terms


async def stage(pool, *, org_id, actor_id, data_type: str, data: bytes, filename: str, licence: dict,
                mapping: dict | None = None, today: date | None = None) -> dict:
    from services import held_away as HA
    from services import replica_terms as T

    if data_type not in UPLOAD_TYPES:
        raise SecurityDataError(f"a file can carry {', '.join(UPLOAD_TYPES)} — not {data_type!r}"
                                + (" (fund holdings from a file are not accepted yet: rep1's holdings store is "
                                   "platform-wide)" if data_type == "fund_holdings" else ""))
    lic = check_licence(licence)
    today = today or date.today()
    rows, by_header = read_rows(data, filename, data_type, mapping=mapping)
    for r in rows:
        validate_row(r, data_type, today)
    async with pool.acquire() as conn:
        if data_type != "index_level":
            await HA.resolve_identifiers(conn, [r for r in rows if not r.get("reject")])
        seen = set()
        for r in rows:
            if r.get("reject"):
                continue
            k = (r.get("security_id") or r.get("index_key"), r["d"].isoformat(), r.get("action"))
            if k in seen:
                r["reject"] = ("duplicate", f"{k[0]} {k[1]}")
            seen.add(k)
        rejects = [{"row": r["row"], "code": r["reject"][0], "reason": REJECT_REASONS.get(r["reject"][0],
                                                                                        r["reject"][0]),
                    "detail": r["reject"][1]} for r in rows if r.get("reject")]
        staged = [_stage_row(r, data_type) for r in rows if not r.get("reject")]
        sha = hashlib.sha256(data).hexdigest()
        async with conn.transaction():
            if await conn.fetchval(f"SELECT 1 FROM {T_UP} WHERE org_id = $1::uuid AND data_type = $2 AND "
                                   "file_sha256 = $3 AND system_to IS NULL AND status <> 'rejected'", str(org_id),
                                   data_type, sha):
                raise SecurityDataError("this exact file was already uploaded for this data type")
            uid = await conn.fetchval(
                f"INSERT INTO {T_UP} (org_id, data_type, file_name, file_sha256, mapping, licence, row_count, "
                "accepted_count, rejects, staged_rows, uploaded_by) VALUES ($1::uuid, $2, $3, $4, $5::jsonb, "
                "$6::jsonb, $7, $8, $9::jsonb, $10::jsonb, $11::uuid) RETURNING id", str(org_id), data_type,
                filename, sha, json.dumps(by_header), json.dumps(lic), len(rows), len(staged), json.dumps(rejects),
                json.dumps(staged), str(actor_id))
            approvers = [u for u in await T._approvers(conn, org_id, APPROVE_PERMISSION) if u != str(actor_id)]
            await T.open_todos(conn, org_id=org_id, recipients=approvers, source=TODO_SOURCE,
                               related_type="data_upload", related_id=uid,
                               title=f"Approve data file: {filename}",
                               detail=f"{len(staged)} {data_type} rows accepted, {len(rejects)} rejected — counts only "
                                      "once a second person approves",
                               action_key="/admin/security-data/uploads")
    return await get_upload(pool, org_id, uid)


def _stage_row(r: dict, data_type: str) -> dict:
    base = {"row": r["row"], "date": r["d"].isoformat()}
    if data_type == "eod_price":
        return {**base, "global_security_id": r["security_id"], "price": format(r["v"], "f"),
                "currency": (r.get("currency") or None)}
    if data_type == "index_level":
        return {**base, "index_key": r["index_key"], "value": format(r["v"], "f")}
    return {**base, "global_security_id": r["security_id"], "action_type": r["action"],
            "record_date": r["record_date"].isoformat() if r["record_date"] else None,
            "pay_date": r["pay_date"].isoformat() if r["pay_date"] else None, "terms": r["terms"]}


async def get_upload(pool_or_conn, org_id, upload_id) -> dict:
    async def q(conn):
        row = await conn.fetchrow(f"SELECT * FROM {T_UP} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                  "system_to IS NULL", str(upload_id), str(org_id))
        if row is None:
            raise SecurityDataNotFound("upload not found")
        out = dict(row)
        for k in ("mapping", "licence", "rejects", "staged_rows"):
            out[k] = json.loads(out[k]) if isinstance(out[k], str) else out[k]
        return out
    if hasattr(pool_or_conn, "acquire"):
        async with pool_or_conn.acquire() as conn:
            return await q(conn)
    return await q(pool_or_conn)


async def list_uploads(conn, org_id, limit: int = 100) -> list[dict]:
    rows = await conn.fetch(f"SELECT id, data_type, file_name, row_count, accepted_count, status, uploaded_by, "
                            f"decided_by, decided_at, decision_note, licence, created_at, rejects FROM {T_UP} WHERE "
                            "org_id = $1::uuid AND system_to IS NULL ORDER BY created_at DESC LIMIT $2",
                            str(org_id), limit)
    out = []
    for r in rows:
        x = dict(r)
        for k in ("licence", "rejects"):
            x[k] = json.loads(x[k]) if isinstance(x[k], str) else x[k]
        out.append(x)
    return out


async def decide(pool, *, org_id, actor_id, upload_id, approve: bool, note: str | None = None) -> dict:
    from services import replica_terms as T

    note = (note or "").strip() or None
    if not approve and not note:
        raise SecurityDataError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_UP} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                      "system_to IS NULL FOR UPDATE", str(upload_id), str(org_id))
            if row is None:
                raise SecurityDataNotFound("upload not found")
            if row["status"] != "pending_approval":
                raise SecurityDataError(f"the upload is already {row['status']}")
            if str(row["uploaded_by"]) == str(actor_id):
                raise UploadMakerChecker("maker-checker: the person who uploaded a file cannot approve or reject it "
                                         "— another admin must")
            lic = json.loads(row["licence"]) if isinstance(row["licence"], str) else row["licence"]
            staged = json.loads(row["staged_rows"]) if isinstance(row["staged_rows"], str) else row["staged_rows"]
            written = 0
            if approve:
                written = await _write_staged(conn, org_id, row["data_type"], staged, row["id"], lic)
            await conn.execute(f"UPDATE {T_UP} SET system_to = now() WHERE id = $1::uuid AND system_to IS NULL",
                               row["id"])
            cols = [c for c in row.keys() if c not in ("system_from", "system_to", "status", "decided_by",
                                                       "decided_at", "decision_note")]
            await conn.execute(
                f"INSERT INTO {T_UP} ({', '.join(cols)}, status, decided_by, decided_at, decision_note) "
                f"SELECT {', '.join(cols)}, $2, $3::uuid, now(), $4 FROM {T_UP} WHERE id = $1::uuid "
                "AND system_to IS NOT NULL ORDER BY system_to DESC LIMIT 1",
                row["id"], "approved" if approve else "rejected", str(actor_id), note)
            await T.close_todos(conn, org_id=org_id, source=TODO_SOURCE, related_id=row["id"])
    out = await get_upload(pool, org_id, upload_id)
    out["written"] = written
    return out


async def _write_staged(conn, org_id, data_type: str, staged: list[dict], upload_id, lic: dict) -> int:
    n = 0
    uid = str(upload_id)
    if data_type == "eod_price":
        by: dict[str, list] = {}
        for s in staged:
            by.setdefault(s["global_security_id"], []).append((date.fromisoformat(s["date"]), Decimal(s["price"]),
                                                               s.get("currency")))
        for gsid, pts in by.items():
            n += await W.upsert_prices(None, gsid, [(a, b) for a, b, _ in pts], provider_key="csv_upload",
                                       currency=pts[0][2], org_id=str(org_id), upload_id=uid, licence=lic, conn=conn)
    elif data_type == "index_level":
        by = {}
        for s in staged:
            by.setdefault(s["index_key"], []).append((date.fromisoformat(s["date"]), Decimal(s["value"])))
        for key, pts in by.items():
            n += await W.upsert_index_levels(None, key, pts, provider_key="csv_upload", org_id=str(org_id),
                                             upload_id=uid, licence=lic, conn=conn)
    else:
        for s in staged:
            await W.insert_corporate_action(
                conn, gsid=s["global_security_id"], action_type=s["action_type"], ex_date=date.fromisoformat(s["date"]),
                record_date=date.fromisoformat(s["record_date"]) if s.get("record_date") else None,
                pay_date=date.fromisoformat(s["pay_date"]) if s.get("pay_date") else None, terms=s["terms"],
                provider_key="csv_upload", org_id=str(org_id), upload_id=uid, licence=lic)
            n += 1
    return n


def vocabulary() -> dict:
    return {"upload_types": list(UPLOAD_TYPES),
            "upload_fields": {t: {k: v[0] for k, v in f.items()} for t, f in FIELDS.items()},
            "required_fields": {t: list(v) for t, v in REQUIRED.items()},
            "reject_reasons": REJECT_REASONS}


def _uuid(v) -> str:
    return str(uuid.UUID(str(v)))
