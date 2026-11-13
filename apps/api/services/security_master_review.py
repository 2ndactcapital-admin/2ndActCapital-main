"""REVIEWQ1 — the Security master Review queue's choices: Use incoming · Use existing · Keep both, one Run, bulk by a
server-side filter, Known discrepancies (seen again, move back). See docs/REVIEWQ1_NOTES.md.

Every function runs on a connection already inside ``platform_scope`` (app.is_super_admin LOCAL) and is called only
after ``require_super_admin`` (routers.security_master). The RLS policies on every table touched are the real gate.

DECISION = valid-axis restatement of the review row (close + insert: status, who, when, note, what was applied), the
same shape the legacy resolve / dismiss route uses. ONE audit_log row per item (platform org).

    use_incoming  → status used_incoming  the security takes the incoming value as a NEW version (old kept as history)
    use_existing  → status kept_existing  nothing on the security changes                         (Known discrepancy)
    keep_both     → status kept_both      the incoming value added as an ALTERNATE identifier      (Known discrepancy)

A refused choice raises ``ReviewRefused`` naming the reason; the item stays open and nothing is written for it.
"""

from __future__ import annotations

import json
import re

from services import security_master_symbols as SYM

CHOICES = ("use_incoming", "use_existing", "keep_both")
CHOICE_LABELS = {"use_incoming": "Use incoming", "use_existing": "Use existing", "keep_both": "Keep both"}
STATUS_OF = {"use_incoming": "used_incoming", "use_existing": "kept_existing", "keep_both": "kept_both"}
KNOWN_STATUSES = ("kept_existing", "kept_both")
STATUS_LABELS = {"open": "Open", "resolved": "Resolved", "dismissed": "Dismissed", "used_incoming": "Used incoming",
                 "kept_existing": "Kept existing", "kept_both": "Kept both"}
ALT_FIELDS = ("isin", "cusip", "figi")
SWAP_FIELDS = ("isin", "cusip", "figi", "ticker", "sec_class_id", "sec_series_id")
BULK_CAP = 2000
NOTE_MAX = 2000
LIST_LIMIT_MAX = 1000
FILTERS = ("kind", "field", "provider", "q", "home_vs_us", "global_security_id")
LIVE = "valid_to IS NULL AND system_to IS NULL"
_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


class ReviewError(ValueError):
    """A bad request — 422."""


class ReviewRefused(ReviewError):
    """The choice cannot be applied (a collision, a stale item, a count that moved) — 409; nothing written."""


# ── what a row offers ──────────────────────────────────────────────────────
def _held_by(existing: str | None) -> bool:
    return (existing or "").startswith("held by")


def choices_for(row) -> tuple[list[str], dict]:
    """(choices offered, {choice: why it is not offered}). Server-side: the screen renders only these."""
    kind, field_, existing = row["kind"], row["field"], row["existing_value"]
    notes: dict = {}
    if kind == "identifier_conflict":
        if _held_by(existing):
            why = ("the incoming value is already live on another security — one identifier names one security "
                   "(that is a merge, made on the security)")
            return ["use_existing"], {"use_incoming": why, "keep_both": why}
        out = ["use_incoming", "use_existing"]
        if field_ in ALT_FIELDS:
            out.append("keep_both")
        else:
            notes["keep_both"] = (f"a security carries one {field_} per listing — both cannot be true"
                                  if field_ == "ticker" else f"{field_} names one share class — both cannot be true")
        return out, notes
    if kind == "ticker_cusip_conflict":
        return ["use_incoming", "use_existing"], {
            "keep_both": "one ticker on one exchange names one security — both cannot be true"}
    if kind == "type_mismatch":
        return ["use_incoming", "use_existing"], {"keep_both": "a security has one type"}
    if kind == "list_match":
        return ["keep_both", "use_existing"], {
            "use_incoming": "the list's identifier is added beside the existing one (Keep both) — never replaces it"}
    if kind == "add_to_master":
        return ["use_incoming", "use_existing"], {"keep_both": "nothing in the master to keep it beside"}
    why = {"ticker_conflict": "the incoming record matched no security — nothing to apply it to (a new listing is "
                              "added on the security screen)",
           "ambiguous_match": "more than one security matches — nothing single to apply it to",
           "exchange_mismatch": "the exchange is a per-source attribute ranked at read time — a decision cannot "
                                "outrank a source",
           "ticker_moved": "informational — the move has already been applied",
           "13f_deleted": "informational — the 13(f) list no longer carries it",
           "13f_conflict": "the 13(f) CUSIP is reviewed on the security"}.get(kind, "not applicable to this kind")
    return ["use_existing"], {"use_incoming": why, "keep_both": why}


# ── the filter (one predicate for the list, the count and the bulk apply) ──
def check_filters(filters: dict | None) -> dict:
    from services import security_master_load as M

    f = {k: v for k, v in (filters or {}).items() if v not in (None, "")}
    for k in f:
        if k not in FILTERS:
            raise ReviewError(f"unknown filter (one of {', '.join(FILTERS)})")
    if "kind" in f and f["kind"] not in M.REVIEW_KINDS:
        raise ReviewError("kind: not one of the review kinds")
    for k in ("field", "provider"):
        if k in f and not (0 < len(str(f[k])) <= 63 and re.fullmatch(r"[a-z0-9_]+", str(f[k]))):
            raise ReviewError(f"{k}: 1 to 63 lower-case letters, digits or _")
    if "q" in f:
        q = str(f["q"]).strip()
        if not (2 <= len(q) <= 100 and all(ord(c) >= 32 for c in q)):
            raise ReviewError("q: 2 to 100 characters, no control characters")
        f["q"] = q
    if "home_vs_us" in f:
        if str(f["home_vs_us"]) not in ("true", "false"):
            raise ReviewError("home_vs_us: true or false")
        f["home_vs_us"] = str(f["home_vs_us"])
        if f["home_vs_us"] == "false":
            f.pop("home_vs_us")
    if "global_security_id" in f and not _UUID.match(str(f["global_security_id"])):
        raise ReviewError("global_security_id: a uuid")
    return f


HOME_VS_US_SQL = ("(r.field = 'isin' AND r.incoming_value ~ '^[A-Z]{2}' AND left(r.incoming_value, 2) <> 'US' "
                  "AND r.existing_value ~ '^US[A-Z0-9]{10}(, US[A-Z0-9]{10})*$')")


def _where(filters: dict, statuses: tuple, args: list) -> str:
    w = ["r.valid_to IS NULL AND r.system_to IS NULL"]
    args.append(list(statuses))
    w.append(f"r.status = ANY(${len(args)}::text[])")
    for k, col in (("kind", "r.kind"), ("field", "r.field"), ("provider", "r.provider_key")):
        if filters.get(k):
            args.append(filters[k])
            w.append(f"{col} = ${len(args)}")
    if filters.get("global_security_id"):
        args.append(str(filters["global_security_id"]))
        w.append(f"r.global_security_id = ${len(args)}::uuid")
    if filters.get("q"):
        args.append(f"%{filters['q'].upper()}%")
        n = len(args)
        w.append(f"(upper(r.subject_key) LIKE ${n} OR upper(r.incoming_value) LIKE ${n} OR "
                 f"upper(COALESCE(r.existing_value, '')) LIKE ${n} OR upper(COALESCE(s.name, '')) LIKE ${n})")
    if filters.get("home_vs_us") == "true":
        w.append(HOME_VS_US_SQL)
    return " AND ".join(w)


def status_set(status: str) -> tuple:
    if status == "known":
        return KNOWN_STATUSES
    if status not in STATUS_LABELS:
        raise ReviewError(f"status must be one of known, {', '.join(STATUS_LABELS)}")
    return (status,)


_SELECT = ("SELECT r.id, r.kind, r.subject_key, r.field, r.incoming_value, r.existing_value, r.provider_key, "
           "r.global_security_id, s.name AS security_name, r.detail, r.status, r.decided_by, r.decided_at, "
           "r.decision_note, r.created_at, u.email AS decided_by_email, sg.seen_again, sg.last_seen "
           "FROM portfolio.security_master_review r "
           "LEFT JOIN portfolio.securities_global s ON s.id = r.global_security_id "
           "LEFT JOIN users u ON u.id = r.decided_by "
           "LEFT JOIN LATERAL (SELECT count(*) AS seen_again, max(x.seen_at) AS last_seen FROM "
           "portfolio.security_master_review_sightings x WHERE x.kind = r.kind AND x.subject_key = r.subject_key AND "
           "x.field = r.field AND x.incoming_value = r.incoming_value AND x.provider_key = r.provider_key AND "
           "x.global_security_id IS NOT DISTINCT FROM r.global_security_id AND r.decided_at IS NOT NULL AND "
           "x.seen_at > r.decided_at) sg ON true ")


def _row_out(r, *, can_write: bool) -> dict:
    from services import security_master_load as M

    d = dict(r)
    d["detail"] = json.loads(d["detail"]) if isinstance(d["detail"], str) else (d["detail"] or {})
    d["kind_label"] = M.REVIEW_LABELS.get(d["kind"], d["kind"])
    d["status_label"] = STATUS_LABELS.get(d["status"], d["status"])
    d["seen_again"] = int(d.get("seen_again") or 0)
    offered, notes = choices_for(d)
    d["choices"] = offered if (can_write and d["status"] == "open") else []
    d["choice_notes"] = notes if d["status"] == "open" else {}
    d["actions"] = (["move_back"] if (can_write and d["status"] in KNOWN_STATUSES) else [])
    return d


async def list_items(conn, *, status: str = "open", filters: dict | None = None, limit: int = 200, offset: int = 0,
                     can_write: bool = False) -> dict:
    f = check_filters(filters)
    args: list = []
    where = _where(f, status_set(status), args)
    total = await conn.fetchval(
        "SELECT count(*) FROM portfolio.security_master_review r LEFT JOIN portfolio.securities_global s ON "
        f"s.id = r.global_security_id WHERE {where}", *args)
    lim = max(1, min(int(limit), LIST_LIMIT_MAX))
    off = max(0, int(offset))
    order = "r.decided_at DESC, r.id" if status != "open" else "r.created_at DESC, r.id"
    rows = await conn.fetch(f"{_SELECT} WHERE {where} ORDER BY {order} LIMIT {lim} OFFSET {off}", *args)
    return {"rows": [_row_out(r, can_write=can_write) for r in rows], "total": int(total), "status": status,
            "filters": f, "limit": lim, "offset": off}


async def facets(conn, status: str = "open") -> dict:
    """The filter vocabularies — the values the queue actually holds (never a client list)."""
    rows = await conn.fetch(
        "SELECT kind, field, provider_key, count(*) AS n FROM portfolio.security_master_review r WHERE "
        f"r.valid_to IS NULL AND r.system_to IS NULL AND r.status = ANY($1::text[]) GROUP BY 1, 2, 3",
        list(status_set(status)))
    agg = {"kind": {}, "field": {}, "provider": {}}
    for r in rows:
        for k, v in (("kind", r["kind"]), ("field", r["field"]), ("provider", r["provider_key"])):
            if v:
                agg[k][v] = agg[k].get(v, 0) + int(r["n"])
    args: list = []
    hv = await conn.fetchval(f"SELECT count(*) FROM portfolio.security_master_review r LEFT JOIN "
                             f"portfolio.securities_global s ON s.id = r.global_security_id WHERE "
                             f"{_where({'home_vs_us': 'true'}, status_set(status), args)}", *args)
    return {k: [{"key": x, "count": n} for x, n in sorted(v.items(), key=lambda kv: (-kv[1], kv[0]))]
            for k, v in agg.items()} | {"home_vs_us": int(hv)}


# ── helpers on the security ────────────────────────────────────────────────
async def _live_ids(conn, gsid, id_type: str) -> list:
    return list(await conn.fetch(
        "SELECT id, id_value, is_primary, is_alternate FROM portfolio.securities_global_identifiers WHERE "
        f"global_security_id = $1::uuid AND id_type = $2 AND {LIVE} ORDER BY is_primary DESC, id_value",
        str(gsid), id_type))


async def _value_owners(conn, id_type: str, value: str, gsid) -> list:
    """Other LIVE securities (canonical, not merged) holding this identifier value."""
    return list(await conn.fetch(
        "SELECT DISTINCT COALESCE(s.canonical_id, s.id) AS gsid, s.name FROM portfolio.securities_global_identifiers i "
        "JOIN portfolio.securities_global s ON s.id = i.global_security_id AND s.valid_to IS NULL AND s.system_to IS "
        "NULL AND s.merged_into_id IS NULL WHERE i.id_type = $1 AND i.id_value = $2 AND i.valid_to IS NULL AND "
        "i.system_to IS NULL "
        "AND COALESCE(s.canonical_id, s.id) IS DISTINCT FROM $3::uuid", id_type, value, str(gsid) if gsid else None))


async def _exchanges(conn, gsid) -> set:
    return {r["value"] for r in await conn.fetch(
        "SELECT value FROM portfolio.security_reference_attributes WHERE global_security_id = $1::uuid AND field = "
        f"'exchange' AND {LIVE}", str(gsid))}


async def ticker_collision(conn, gsid, ticker: str, *, exchanges: set | None = None) -> dict | None:
    """Another live security on this ticker whose exchange family is compatible with this security's (or where
    either side states no exchange — unknown is treated as the same listing). None when the ticker is free."""
    mine = exchanges if exchanges is not None else (await _exchanges(conn, gsid) if gsid else set())
    for o in await _value_owners(conn, "ticker", ticker, gsid):
        theirs = await _exchanges(conn, o["gsid"])
        if not mine or not theirs or any(SYM.families_compatible(a, b) for a in mine for b in theirs):
            return {"gsid": str(o["gsid"]), "name": o["name"], "exchanges": sorted(theirs)}
    return None


async def _retire(conn, ids: list) -> None:
    if ids:
        await conn.execute("UPDATE portfolio.securities_global_identifiers SET valid_to = now() WHERE id = "
                           "ANY($1::uuid[]) AND valid_to IS NULL", ids)


async def _insert_identifier(conn, gsid, id_type, value, *, primary: bool, alternate: bool = False):
    return await conn.fetchval(
        "INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, id_value, is_primary, "
        "is_alternate) VALUES ($1::uuid, $2, $3, $4, $5) RETURNING id", str(gsid), id_type, value, primary, alternate)


async def swap_identifier(conn, gsid, id_type: str, value: str) -> dict:
    """The security's live values of ``id_type`` retired (history), ``value`` inserted with their primary flag.
    Refused when the value is live on another security (identifier) / collides on the ticker."""
    if id_type == "ticker":
        hit = await ticker_collision(conn, gsid, value)
        if hit:
            raise ReviewRefused(f"ticker {value} is live on another security ({hit['name']}, {hit['gsid']}) on a "
                                "compatible exchange — never two live securities on one ticker + exchange")
    else:
        owners = await _value_owners(conn, id_type, value, gsid)
        if owners:
            raise ReviewRefused(f"{id_type} {value} is live on another security ({owners[0]['name']}, "
                                f"{owners[0]['gsid']}) — one identifier names one security")
    cur = await _live_ids(conn, gsid, id_type)
    before = [r["id_value"] for r in cur]
    if before == [value]:
        return {"field": id_type, "before": before, "after": before, "changed": False}
    keep = [r for r in cur if r["id_value"] == value]
    retire = [r for r in cur if r["id_value"] != value]
    primary = any(r["is_primary"] for r in cur)
    await _retire(conn, [r["id"] for r in retire])
    new_id = None
    if not keep:
        new_id = await _insert_identifier(conn, gsid, id_type, value, primary=primary)
    out = {"field": id_type, "before": before, "after": [value], "changed": True,
           "retired_identifier_ids": [str(r["id"]) for r in retire],
           "new_identifier_id": str(new_id) if new_id else None}
    # a CUSIP's derived US ISIN moves with it (when the security holds exactly the old derived one and the new is free)
    if id_type == "cusip":
        olds = {SYM.isin_from_cusip(v) for v in before} - {None}
        new_isin = SYM.isin_from_cusip(value)
        isins = await _live_ids(conn, gsid, "isin")
        if new_isin and isins and all(r["id_value"] in olds for r in isins) and not await _value_owners(
                conn, "isin", new_isin, gsid):
            out["derived_isin"] = await swap_identifier(conn, gsid, "isin", new_isin)
    return out


async def add_alternate(conn, gsid, id_type: str, value: str) -> dict:
    if id_type not in ALT_FIELDS:
        raise ReviewRefused(f"{id_type} cannot hold an alternate")
    owners = await _value_owners(conn, id_type, value, gsid)
    if owners:
        raise ReviewRefused(f"{id_type} {value} is live on another security ({owners[0]['name']}, "
                            f"{owners[0]['gsid']}) — one identifier names one security")
    cur = await _live_ids(conn, gsid, id_type)
    if any(r["id_value"] == value for r in cur):
        return {"field": id_type, "before": [r["id_value"] for r in cur], "after": [r["id_value"] for r in cur],
                "changed": False}
    new_id = await _insert_identifier(conn, gsid, id_type, value, primary=False, alternate=True)
    return {"field": id_type, "before": [r["id_value"] for r in cur],
            "after": [r["id_value"] for r in cur] + [value], "alternate_identifier_id": str(new_id), "changed": True}


def _split_ticker_ref(incoming: str) -> tuple[str, str | None]:
    t, _, ref = (incoming or "").rpartition("@")
    if not t:
        return incoming, None
    return t, (ref if ref and ref != "?" else None)


# ── the three actions ──────────────────────────────────────────────────────
async def _apply_use_incoming(conn, cur) -> dict:
    kind, gsid, field_, inc = cur["kind"], cur["global_security_id"], cur["field"], cur["incoming_value"]
    if kind == "identifier_conflict":
        if field_ not in SWAP_FIELDS or gsid is None:
            raise ReviewRefused(f"Use incoming is not applicable to {field_ or 'this item'}")
        return await swap_identifier(conn, gsid, field_, inc)
    if kind == "ticker_cusip_conflict":
        ticker, ref = _split_ticker_ref(inc)
        if gsid is None:
            raise ReviewRefused("the item names no security")
        out: dict = {"steps": []}
        mine = [r["id_value"] for r in await _live_ids(conn, gsid, "ticker")]
        if ticker not in mine:
            hit = await ticker_collision(conn, gsid, ticker)
            if hit:
                raise ReviewRefused(f"ticker {ticker} is live on another security ({hit['name']}, {hit['gsid']}) on a "
                                    "compatible exchange — never two live securities on one ticker + exchange")
        if ref is None:
            raise ReviewRefused("the source named no CUSIP / FIGI for the ticker — nothing to apply")
        id_type = "figi" if ref.upper().startswith("BBG") else "cusip"
        out["steps"].append(await swap_identifier(conn, gsid, id_type, ref))
        if ticker not in mine:
            nid = await _insert_identifier(conn, gsid, "ticker", ticker, primary=not mine)
            out["steps"].append({"field": "ticker", "before": mine, "after": mine + [ticker], "changed": True,
                                 "new_identifier_id": str(nid)})
        return out
    if kind == "type_mismatch":
        from services import securities_global as SG

        if inc not in SG.SECURITY_TYPES:
            raise ReviewRefused(f"{inc!r} is not a security type the master holds")
        before = await conn.fetchval("SELECT security_type FROM portfolio.securities_global WHERE id = $1::uuid",
                                     str(gsid))
        if before == inc:
            return {"field": "security_type", "before": before, "after": inc, "changed": False}
        r = await SG.update_security(conn, global_security_id=str(gsid), changes={"security_type": inc},
                                     is_super_admin=True)
        return {"field": "security_type", "before": before, "after": inc, "changed": True,
                "archived_version_id": r["archived_version_id"]}
    if kind == "add_to_master":
        return await _create_from_openfigi(conn, cur)
    raise ReviewRefused(f"Use incoming is not offered for {kind}")


async def _create_from_openfigi(conn, cur) -> dict:
    """add_to_master → the security created through the matcher (the same create path every source uses), from
    OpenFIGI's ticker / name / type and the list's identifier. Any conflict → refused (rolled back by the caller)."""
    from datetime import date

    from services import security_master_load as M
    from services.security_data.providers.reference_common import SourceRecord

    d = cur["detail"] if isinstance(cur["detail"], dict) else json.loads(cur["detail"] or "{}")
    ticker = SYM.normalize_symbol(d.get("ticker"))
    if not ticker:
        raise ReviewRefused("OpenFIGI named no ticker — nothing to create the security from")
    hit = await ticker_collision(conn, None, ticker, exchanges={"US"})
    if hit:
        raise ReviewRefused(f"ticker {ticker} is live on another security ({hit['name']}, {hit['gsid']}) — map the "
                            "list member to it instead (never two live securities on one ticker + exchange)")
    ids = {cur["field"]: cur["incoming_value"]} if cur["field"] in ("isin", "cusip", "figi") else {}
    if d.get("figi"):
        ids.setdefault("figi", d["figi"])
    for t, v in ids.items():
        owners = await _value_owners(conn, t, v, None)
        if owners:
            raise ReviewRefused(f"{t} {v} is already live on {owners[0]['name']} ({owners[0]['gsid']})")
    rec = SourceRecord("openfigi", ticker=ticker, name=d.get("name") or ticker, exchange="US",
                       security_type=d.get("security_type") or "equity", cusip=ids.get("cusip"), figi=ids.get("figi"),
                       isin=ids.get("isin"), as_of=date.today())
    res = await M.Matcher("openfigi", as_of=date.today(), create_new=True).process_conn(conn, [rec])
    outcome = res.by_record.get(rec.subject)
    if not outcome or outcome[0] != "new" or res.review_items:
        raise ReviewRefused(f"the master did not take it cleanly ({outcome[0] if outcome else 'no outcome'}: "
                            f"{outcome[2] if outcome else ''}) — nothing created")
    return {"created_security_id": str(outcome[1]), "ticker": ticker, "identifiers": ids, "changed": True}


async def _audit(conn, *, actor_id, action: str, payload: dict) -> None:
    from services import security_master_jobs as J

    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
        "($1::uuid, $2::uuid, $3, 'security_master_review', $4, $5::jsonb)",
        str(J.platform_org()), str(actor_id) if actor_id else None, action, str(payload.get("review_id") or ""),
        json.dumps(payload, default=str))


async def _restate(conn, cur, *, status: str, actor_id, note: str | None, detail: dict):
    await conn.execute("UPDATE portfolio.security_master_review SET valid_to = now() WHERE id = $1 AND valid_to IS NULL",
                       cur["id"])
    decided = status != "open"
    return await conn.fetchval(
        "INSERT INTO portfolio.security_master_review (kind, subject_key, field, incoming_value, existing_value, "
        "provider_key, global_security_id, detail, status, decided_by, decided_at, decision_note, run_id, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::uuid, CASE WHEN $11 THEN now() END, $12, $13, $14) "
        "RETURNING id",
        cur["kind"], cur["subject_key"], cur["field"], cur["incoming_value"], cur["existing_value"],
        cur["provider_key"], cur["global_security_id"], json.dumps(detail, default=str), status,
        str(actor_id) if (decided and actor_id) else None, decided, note if decided else None, cur["run_id"],
        cur["created_at"])


def _note(note: str | None, *, required: bool = False) -> str | None:
    n = (note or "").strip()
    if len(n) > NOTE_MAX:
        raise ReviewError(f"the note is longer than {NOTE_MAX} characters")
    if required and not n:
        raise ReviewError("a note is required")
    return n or None


async def _lock(conn, review_id):
    cur = await conn.fetchrow(f"SELECT * FROM portfolio.security_master_review WHERE id = $1::uuid AND {LIVE} "
                              "FOR UPDATE", str(review_id))
    if cur is None:
        raise ReviewRefused("review item not found (or no longer current — reload)")
    cur = dict(cur)
    cur["detail"] = json.loads(cur["detail"]) if isinstance(cur["detail"], str) else (cur["detail"] or {})
    return cur


async def apply_choice(conn, review_id, choice: str, *, actor_id, note: str | None = None) -> dict:
    """ONE item, in its own savepoint: refusal → nothing written for it. Returns {id, new_id, choice, status,
    applied}."""
    if choice not in CHOICES:
        raise ReviewError(f"choice must be one of {', '.join(CHOICES)}")
    note = _note(note)
    async with conn.transaction():
        cur = await _lock(conn, review_id)
        if cur["status"] != "open":
            raise ReviewRefused(f"already decided ({STATUS_LABELS.get(cur['status'], cur['status'])})")
        offered, notes = choices_for(cur)
        if choice not in offered:
            raise ReviewRefused(f"{CHOICE_LABELS[choice]} is not offered here: {notes.get(choice, 'not applicable')}")
        if choice == "use_incoming":
            applied = await _apply_use_incoming(conn, cur)
        elif choice == "keep_both":
            if cur["global_security_id"] is None:
                raise ReviewRefused("the item names no security to keep the value on")
            applied = await add_alternate(conn, cur["global_security_id"], cur["field"], cur["incoming_value"])
        else:
            applied = {"changed": False}
        status = STATUS_OF[choice]
        detail = {**cur["detail"], "decision": {"choice": choice, "applied": applied}}
        new_id = await _restate(conn, cur, status=status, actor_id=actor_id, note=note, detail=detail)
        await _audit(conn, actor_id=actor_id, action="security_master.review_decided",
                     payload={"review_id": str(cur["id"]), "new_review_id": str(new_id), "kind": cur["kind"],
                              "subject_key": cur["subject_key"], "field": cur["field"], "choice": choice,
                              "before": {"status": "open", "existing_value": cur["existing_value"]},
                              "after": {"status": status, "applied": applied}, "note": note})
    return {"id": str(cur["id"]), "new_id": str(new_id), "choice": choice, "status": status, "applied": applied}


async def run(conn, items: list[dict], *, actor_id, note: str | None = None) -> dict:
    """The one Run: every item with a choice, each in its own savepoint; refusals listed with their reason."""
    note = _note(note)
    seen, todo = set(), []
    for it in items:
        rid, ch = str(it.get("id") or ""), it.get("choice")
        if not _UUID.match(rid) or ch not in CHOICES:
            raise ReviewError("every item needs an id and a choice (use_incoming / use_existing / keep_both)")
        if rid in seen:
            raise ReviewError("an item is listed twice")
        seen.add(rid)
        todo.append((rid, ch))
    if len(todo) > BULK_CAP:
        raise ReviewError(f"at most {BULK_CAP} items per Run")
    counts = {c: 0 for c in CHOICES}
    applied, refused = [], []
    for rid, ch in todo:
        try:
            r = await apply_choice(conn, rid, ch, actor_id=actor_id, note=note)
            counts[ch] += 1
            applied.append(r)
        except ReviewRefused as exc:
            refused.append({"id": rid, "choice": ch, "reason": str(exc)})
    return {"requested": {c: sum(1 for _, x in todo if x == c) for c in CHOICES}, "applied_counts": counts,
            "applied": applied, "refused": refused}


async def bulk_ids(conn, filters: dict) -> tuple[list[str], int]:
    f = check_filters(filters)
    if not f:
        raise ReviewError("a bulk choice needs at least one filter")
    args: list = []
    where = _where(f, ("open",), args)
    rows = await conn.fetch("SELECT r.id FROM portfolio.security_master_review r LEFT JOIN portfolio.securities_global s "
                            f"ON s.id = r.global_security_id WHERE {where} ORDER BY r.created_at, r.id", *args)
    return [str(r["id"]) for r in rows], len(rows)


async def estimate(conn, filters: dict, choice: str) -> dict:
    """How many OPEN items the filter selects and how many of them offer ``choice`` (writes nothing)."""
    if choice not in CHOICES:
        raise ReviewError(f"choice must be one of {', '.join(CHOICES)}")
    f = check_filters(filters)
    if not f:
        raise ReviewError("a bulk choice needs at least one filter")
    args: list = []
    where = _where(f, ("open",), args)
    rows = await conn.fetch("SELECT r.kind, r.field, r.existing_value FROM portfolio.security_master_review r LEFT JOIN "
                            f"portfolio.securities_global s ON s.id = r.global_security_id WHERE {where}", *args)
    applicable = sum(1 for r in rows if choice in choices_for(r)[0])
    return {"total": len(rows), "choice": choice, "applicable": applicable, "not_offered": len(rows) - applicable,
            "over_cap": len(rows) > BULK_CAP, "cap": BULK_CAP, "filters": f}


async def bulk(conn, filters: dict, choice: str, *, expected_total: int | None, actor_id, note: str | None = None
               ) -> dict:
    """One choice for every OPEN item the filter selects (re-evaluated here). The count must equal the confirmed
    ``expected_total`` — otherwise nothing is written. Items the choice is not offered for are refused, never forced."""
    if choice not in CHOICES:
        raise ReviewError(f"choice must be one of {', '.join(CHOICES)}")
    ids, total = await bulk_ids(conn, filters)
    if expected_total is None or int(expected_total) != total:
        raise ReviewRefused(f"the filter now selects {total} items, not the {expected_total} confirmed — nothing was "
                            "changed; confirm again")
    if total > BULK_CAP:
        raise ReviewRefused(f"{total} items — above the cap of {BULK_CAP}; narrow the filter")
    if total == 0:
        raise ReviewRefused("the filter selects nothing")
    out = await run(conn, [{"id": i, "choice": choice} for i in ids], actor_id=actor_id, note=note)
    out["total"] = total
    out["filters"] = check_filters(filters)
    return out


async def move_back(conn, review_id, *, actor_id, note: str) -> dict:
    """A Known item back to the queue (audited). Keep both: its alternate identifier is retired, so the next run raises
    the disagreement again and it stays ONE open item."""
    note = _note(note, required=True)
    async with conn.transaction():
        cur = await _lock(conn, review_id)
        if cur["status"] not in KNOWN_STATUSES:
            raise ReviewRefused("only a Known discrepancy (Use existing / Keep both) moves back to the queue")
        retired = []
        if cur["status"] == "kept_both" and cur["global_security_id"] is not None:
            alt = [r for r in await _live_ids(conn, cur["global_security_id"], cur["field"])
                   if r["id_value"] == cur["incoming_value"] and r["is_alternate"]]
            await _retire(conn, [r["id"] for r in alt])
            retired = [str(r["id"]) for r in alt]
        dup = await conn.fetchval(
            f"SELECT id FROM portfolio.security_master_review WHERE status = 'open' AND {LIVE} AND kind = $1 AND "
            "subject_key = $2 AND field = $3 AND incoming_value = $4", cur["kind"], cur["subject_key"], cur["field"],
            cur["incoming_value"])
        if dup is not None:
            raise ReviewRefused("an open item for the same value is already in the queue — decide that one")
        detail = {**cur["detail"], "moved_back": {"by": str(actor_id) if actor_id else None, "note": note,
                                                  "from_status": cur["status"], "decided_at": cur["decided_at"],
                                                  "decision_note": cur["decision_note"],
                                                  "retired_alternate_ids": retired}}
        detail.pop("decision", None)
        new_id = await _restate(conn, cur, status="open", actor_id=actor_id, note=None, detail=detail)
        await _audit(conn, actor_id=actor_id, action="security_master.review_moved_back",
                     payload={"review_id": str(cur["id"]), "new_review_id": str(new_id), "kind": cur["kind"],
                              "subject_key": cur["subject_key"], "before": {"status": cur["status"]},
                              "after": {"status": "open", "retired_alternate_ids": retired}, "note": note})
    return {"id": str(cur["id"]), "new_id": str(new_id), "status": "open", "retired_alternate_ids": retired}


# ── repeat suppression (called by security_master_load.insert_review) ──────
KNOWN_MATCH_SQL = (
    "SELECT id FROM portfolio.security_master_review WHERE status = ANY($7::text[]) AND valid_to IS NULL AND "
    "system_to IS NULL AND kind = $1 AND subject_key = $2 AND field = $3 AND incoming_value = $4 AND provider_key = $5 "
    "AND global_security_id IS NOT DISTINCT FROM $6::uuid ORDER BY decided_at DESC LIMIT 1")


async def sighting_if_known(conn, *, kind, subject_key, field, incoming_value, provider_key, global_security_id,
                            run_id=None):
    """A Known item with this natural key → one sighting row, returns its review id; else None (insert normally)."""
    gs = str(global_security_id) if global_security_id else None
    rid = await conn.fetchval(KNOWN_MATCH_SQL, kind, subject_key, field or "", incoming_value or "", provider_key, gs,
                              list(KNOWN_STATUSES))
    if rid is None:
        return None
    await conn.execute(
        "INSERT INTO portfolio.security_master_review_sightings (kind, subject_key, field, incoming_value, provider_key, "
        "global_security_id, review_id, run_id) VALUES ($1, $2, $3, $4, $5, $6::uuid, $7, $8::uuid)",
        kind, subject_key, field or "", incoming_value or "", provider_key, gs, rid, str(run_id) if run_id else None)
    return rid


async def alternate_sightings(conn, keys: list[tuple], *, run_id=None) -> int:
    """A record matched through a Keep both alternate: a sighting for each Known item of that natural key (set-based)."""
    if not keys:
        return 0
    cols = list(zip(*keys))
    n = await conn.fetchval(
        "WITH k AS (SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::text[], $6::uuid[]) AS "
        "w(kind, subject_key, field, incoming_value, provider_key, gsid)), ins AS ("
        "INSERT INTO portfolio.security_master_review_sightings (kind, subject_key, field, incoming_value, provider_key, "
        "global_security_id, review_id, run_id) SELECT DISTINCT ON (r.id) r.kind, r.subject_key, r.field, "
        "r.incoming_value, r.provider_key, r.global_security_id, r.id, $7::uuid FROM k JOIN "
        "portfolio.security_master_review r ON r.kind = k.kind AND r.subject_key = k.subject_key AND r.field = k.field "
        "AND r.incoming_value = k.incoming_value AND r.provider_key = k.provider_key AND r.global_security_id = k.gsid "
        "AND r.status = 'kept_both' AND r.valid_to IS NULL AND r.system_to IS NULL RETURNING 1) SELECT count(*) FROM ins",
        list(cols[0]), list(cols[1]), list(cols[2]), list(cols[3]), list(cols[4]), [str(x) for x in cols[5]],
        str(run_id) if run_id else None)
    return int(n or 0)
