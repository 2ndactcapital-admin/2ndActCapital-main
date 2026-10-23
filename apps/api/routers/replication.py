"""REP1 — Replication Studio: registry, holdings and fund terms (/replication).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_replication     GET  /replication/registry                     list + envelope
                       GET  /replication/registry/{id}                 one entry: holdings, diff, terms, sources
  manage_replication   GET  /replication/lookup                        resolve a ticker/CUSIP before adding
                       POST /replication/registry                      add an entry (+ ingest + extract)
                       POST /replication/registry/{id}/holdings/refresh
                       POST /replication/registry/{id}/terms/extract
                       POST /replication/registry/{id}/terms/corrections
                       POST /replication/registry/{id}/terms/changes
  approve_replication  POST /replication/registry/{id}/terms/{version_id}/approve
                       POST /replication/registry/{id}/terms/{version_id}/reject
                       (and never for a maker of that draft — services.replica_terms;
                       rep3: each decision completes the draft's governed human task)

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id) — never a body or path value; request models forbid extras.
Another org's entry is a 404 (RLS + an explicit org predicate).

ADD RUNS SYNCHRONOUSLY, CAPPED. POST /registry creates the entry, then ingests
holdings (cap REPLICA_ADD_INGEST_SECONDS, default 90) and extracts terms (cap
REPLICA_ADD_EXTRACT_SECONDS, default 150), and answers 201 with each step's
outcome. Synchronous because there is no job worker in this stack — the only
background executor is the 5-minute Render cron, which would leave a newly
added fund blank for up to a day — and every step is idempotent, so a step that
hits its cap is simply re-run from the entry page (Refresh holdings / Re-run
extraction) with nothing half-written: each write is one transaction.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import replica_holdings as H
from services import replica_parsers as P
from services import replica_schedule as RS
from services import replica_terms as T
from services.authz import require_permission
from services.database import get_pool
from services.securities_global import SecuritiesGlobalError

router = APIRouter(tags=["replication"])

VIEW = "view_replication"
MANAGE = "manage_replication"
APPROVE = "approve_replication"
HOLDINGS_SORTS = {"row_num": "row_num", "weight": "weight", "name": "raw_name", "ticker": "raw_ticker",
                  "cusip": "raw_cusip", "shares": "shares", "resolved": "resolution_state"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntryCreate(_Body):
    ticker: str | None = Field(default=None, max_length=12)
    cusip: str | None = Field(default=None, max_length=12)
    instrument_class: str = Field(min_length=1, max_length=40)
    holdings_source_key: str | None = Field(default=None, max_length=60)
    fund_name: str | None = Field(default=None, max_length=300)
    issuer: str | None = Field(default=None, max_length=200)


class Correction(_Body):
    version_id: UUID
    field_key: str = Field(min_length=1, max_length=80)
    value: Any = None
    notes: str | None = Field(default=None, max_length=2000)


class Change(_Body):
    field_key: str = Field(min_length=1, max_length=80)
    value: Any = None
    reason: str = Field(min_length=1, max_length=2000)
    version_id: UUID | None = None


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


def _cap(env: str, default: float) -> float:
    try:
        return float(os.environ.get(env) or default)
    except ValueError:
        return default


def _raise(exc: Exception):
    if isinstance(exc, T.ReplicaNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, T.ReplicaConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, T.MakerCheckerViolation):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (T.ReplicaTermsError, H.ReplicaHoldingsError, P.HoldingsParseError, SecuritiesGlobalError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _permissions(principal) -> dict:
    return {
        "can_read": principal.is_super_admin or VIEW in principal.permissions,
        "can_write": principal.is_super_admin or MANAGE in principal.permissions,
        "can_approve": principal.is_super_admin or APPROVE in principal.permissions,
        "is_super_admin": principal.is_super_admin,
        "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE,
    }


def _pairs(labels: dict) -> list[dict]:
    return [{"key": k, "label": v} for k, v in labels.items()]


async def _vocab(conn, perms: dict) -> dict:
    sources = await H.list_sources(conn)
    return {
        "instrument_classes": [{"key": c["key"], "label": c["label"], "enabled": c["enabled"]}
                               for c in await T.instrument_classes(conn)],
        "entry_statuses": _pairs(T.ENTRY_STATUS_LABELS),
        "version_statuses": _pairs(T.VERSION_STATUS_LABELS),
        "origins": _pairs(T.ORIGIN_LABELS),
        "confidence": _pairs(T.CONFIDENCE_LABELS),
        "holdings_sources": [{"key": s["key"], "label": s["issuer"], "active": s["active"]} for s in sources],
        "editable": ["add_security", "refresh_holdings", "extract"] if perms["can_write"] else [],
    }


def _j(v):
    return T._loads(v)


def _num(v):
    return None if v is None else (str(v) if isinstance(v, Decimal) else v)


async def _summaries(conn, entries: list[dict]) -> dict[str, dict]:
    if not entries:
        return {}
    ids = list({str(e["global_security_id"]) for e in entries})
    snaps = await conn.fetch(
        """SELECT DISTINCT ON (s.fund_global_security_id, s.source_key) s.id, s.fund_global_security_id, s.source_key,
                  s.as_of, s.status, s.fetched_at, s.row_count,
                  (SELECT count(*) FROM portfolio.replica_holdings_rows r WHERE r.snapshot_id = s.id
                    AND r.asset_type <> 'cash') AS constituents,
                  (SELECT count(*) FROM portfolio.replica_holdings_rows r WHERE r.snapshot_id = s.id
                    AND r.resolution_state = 'resolved') AS resolved
           FROM portfolio.replica_holdings_snapshots s
           WHERE s.fund_global_security_id = ANY($1::uuid[]) AND s.system_to IS NULL
           ORDER BY s.fund_global_security_id, s.source_key, (s.status = 'live') DESC, s.as_of DESC, s.fetched_at DESC""",
        ids)
    return {(str(s["fund_global_security_id"]), s["source_key"]): dict(s) for s in snaps}


@router.get("/replication/registry")
async def list_registry(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        entries = [dict(r) for r in await conn.fetch(
            "SELECT * FROM portfolio.replica_securities WHERE org_id = $1::uuid AND valid_to IS NULL "
            "AND system_to IS NULL ORDER BY ticker", str(org_id))]
        snaps = await _summaries(conn, entries)
        vers = {}
        if entries:
            for v in await conn.fetch(
                    "SELECT replica_security_id, status, version_no, cardinality(needs_review_fields) AS needs_review "
                    "FROM portfolio.replica_terms_versions WHERE replica_security_id = ANY($1::uuid[]) "
                    "AND status IN ('draft', 'approved') AND system_to IS NULL", [e["id"] for e in entries]):
                vers.setdefault(str(v["replica_security_id"]), {})[v["status"]] = dict(v)
        vocab = await _vocab(conn, perms)
    rows = []
    for e in entries:
        s = snaps.get((str(e["global_security_id"]), e["holdings_source_key"]))
        v = vers.get(str(e["id"]), {})
        rows.append({
            "id": str(e["id"]), "display_name": e["display_name"], "ticker": e["ticker"],
            "instrument_class": e["instrument_class"], "issuer": e["issuer"], "status": e["status"],
            "holdings_source_key": e["holdings_source_key"],
            "latest_as_of": s["as_of"].isoformat() if s else None, "latest_status": s["status"] if s else None,
            "last_refreshed": s["fetched_at"].isoformat() if s else None,
            "constituents_total": int(s["constituents"]) if s else 0,
            "constituents_resolved": int(s["resolved"]) if s else 0,
            "terms": {"approved_version": v["approved"]["version_no"] if "approved" in v else None,
                      "draft_version": v["draft"]["version_no"] if "draft" in v else None,
                      "draft_needs_review": int(v["draft"]["needs_review"]) if "draft" in v else 0},
        })
    return {"rows": rows, "permissions": perms, "vocabularies": vocab}


@router.get("/replication/lookup")
async def lookup(request: Request, ticker: str | None = Query(default=None, max_length=12),
                 cusip: str | None = Query(default=None, max_length=12)):
    """Resolve a ticker / CUSIP for the Add screen's confirm step. No writes."""
    require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    http = H.make_http()
    try:
        fund = await H.resolve_fund(pool, ticker=ticker, cusip=cusip, http=http, create=False)
        sec = fund.sec or {}
        async with pool.acquire() as conn:
            try:
                source = await H.choose_source(conn, ticker=fund.ticker, series_id=sec.get("series_id"))
                source_out = {"key": source["key"], "label": source["issuer"], "parser": source["parser"]}
            except H.NoHoldingsSource as exc:
                source_out = {"key": None, "label": None, "error": str(exc)}
            existing = None
            if fund.global_security_id:
                existing = await conn.fetchval(
                    "SELECT id FROM portfolio.replica_securities WHERE org_id = $1::uuid AND global_security_id = "
                    "$2::uuid AND valid_to IS NULL AND system_to IS NULL", str(org_id), fund.global_security_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    finally:
        await http.aclose()
    return {"ticker": fund.ticker, "cusip": fund.cusip, "name": fund.name, "display_name": T.display_name_for(fund.ticker),
            "disclosure": T.disclosure_for(fund.ticker), "in_security_master": fund.global_security_id is not None,
            "sec": sec, "holdings_source": source_out, "existing_entry_id": str(existing) if existing else None}


async def _ingest_entry(pool, entry: dict, http) -> H.IngestResult:
    async with pool.acquire() as conn:
        sources = {s["key"]: s for s in await H.list_sources(conn)}
    source = sources.get(entry["holdings_source_key"])
    if source is None or not source["active"]:
        raise H.NoHoldingsSource(f"holdings source '{entry['holdings_source_key']}' is not active")
    return await H.ingest(pool, fund_global_security_id=str(entry["global_security_id"]), ticker=entry["ticker"],
                          source=source, series_id=entry.get("sec_series_id"), http=http)


async def _step(coro, cap: float) -> dict:
    try:
        return {"ok": True, "result": await asyncio.wait_for(coro, cap)}
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"stopped at the {int(cap)} s cap — re-run it from the entry page"}
    except Exception as exc:  # noqa: BLE001 — reported as the step's outcome; the entry stays
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}


@router.post("/replication/registry", status_code=201)
async def add_entry(request: Request, body: EntryCreate):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    http = H.make_http()
    refs: list[T.ActionRef] = []
    try:
        try:
            entry, r = await T.create_entry(pool, org_id=org_id, actor_id=principal.user_id, ticker=body.ticker,
                                            cusip=body.cusip, instrument_class=body.instrument_class, http=http,
                                            holdings_source_key=body.holdings_source_key, fund_name=body.fund_name,
                                            issuer=body.issuer)
            refs += r
        except Exception as exc:  # noqa: BLE001
            _raise(exc)
        holdings = await _step(_ingest_entry(pool, entry, http), _cap("REPLICA_ADD_INGEST_SECONDS", 90))
        if holdings["ok"]:
            holdings["result"] = holdings["result"].as_dict()
            holdings["result"].pop("diff", None)
        terms = await _step(T.build_draft(pool, org_id=org_id, entry=entry, actor_id=principal.user_id, http=http),
                            _cap("REPLICA_ADD_EXTRACT_SECONDS", 150))
        if terms["ok"]:
            version, run, r = terms["result"]
            refs += r
            terms["result"] = {"version_id": str(version["id"]), "version_no": version["version_no"],
                               "extraction_status": run.status, "reason": run.reason,
                               "needs_review": list(version["needs_review_fields"] or [])}
    finally:
        await http.aclose()
    await T.publish_actions(pool, refs)
    return {"entry": _entry_out(entry), "steps": {"holdings": holdings, "terms": terms}}


def _entry_out(e: dict) -> dict:
    return {"id": str(e["id"]), "display_name": e["display_name"], "ticker": e["ticker"], "cusip": e["cusip"],
            "fund_name": e["fund_name"], "issuer": e["issuer"], "instrument_class": e["instrument_class"],
            "status": e["status"], "holdings_source_key": e["holdings_source_key"],
            "global_security_id": str(e["global_security_id"]), "sec_cik": e["sec_cik"],
            "sec_series_id": e["sec_series_id"], "sec_class_id": e["sec_class_id"],
            "disclosure": T.disclosure_for(e["ticker"])}


def _snap_out(s: dict | None) -> dict | None:
    if s is None:
        return None
    return {"id": str(s["id"]), "as_of": s["as_of"].isoformat(), "status": s["status"], "source_key": s["source_key"],
            "source_url": s["source_url"], "accession_number": s["accession_number"], "file_sha256": s["file_sha256"],
            "row_count": s["row_count"], "total_weight": _num(s["total_weight"]),
            "total_weight_ex_cash": _num(s["total_weight_ex_cash"]), "validation": _j(s["validation"]),
            "diff": _j(s["diff"]), "diff_exceeds_threshold": s["diff_exceeds_threshold"],
            "fetched_at": s["fetched_at"].isoformat(),
            "previous_snapshot_id": str(s["previous_snapshot_id"]) if s["previous_snapshot_id"] else None}


@router.get("/replication/registry/{entry_id}")
async def get_entry(request: Request, entry_id: str, snapshot_id: str | None = Query(default=None),
                    page: int = Query(default=1, ge=1), page_size: int = Query(default=100, ge=1, le=500),
                    sort: str = Query(default="weight"), direction: str = Query(default="desc", alias="dir")):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    col = HOLDINGS_SORTS.get(sort)
    if col is None or direction not in ("asc", "desc"):
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(HOLDINGS_SORTS)}, dir asc|desc")
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            entry = await T.load_entry(conn, org_id, entry_id)
        except Exception as exc:  # noqa: BLE001
            _raise(exc)
        fund_id = str(entry["global_security_id"])
        snaps = [dict(r) for r in await conn.fetch(
            "SELECT * FROM portfolio.replica_holdings_snapshots WHERE fund_global_security_id = $1::uuid "
            "AND source_key = $2 AND system_to IS NULL ORDER BY as_of DESC, fetched_at DESC", fund_id,
            entry["holdings_source_key"])]
        current = None
        if snapshot_id:
            current = next((s for s in snaps if str(s["id"]) == snapshot_id), None)
            if current is None:
                raise HTTPException(status_code=404, detail="snapshot not found for this entry")
        else:
            current = next((s for s in snaps if s["status"] == "live"), snaps[0] if snaps else None)
        holdings: dict[str, Any] = {"rows": [], "total": 0, "page": page, "page_size": page_size}
        if current is not None:
            holdings["total"] = int(await conn.fetchval(
                "SELECT count(*) FROM portfolio.replica_holdings_rows WHERE snapshot_id = $1", current["id"]))
            rows = await conn.fetch(
                f"""SELECT r.row_num, r.raw_name, r.raw_ticker, r.raw_cusip, r.raw_isin, r.raw_sedol, r.asset_type,
                           r.weight, r.shares, r.market_value, r.currency, r.resolution_state, r.resolved_via,
                           r.constituent_global_security_id, rel.link_state AS queue_state
                    FROM portfolio.replica_holdings_rows r
                    LEFT JOIN portfolio.securities_global_relationships rel ON rel.id = r.relationship_id
                    WHERE r.snapshot_id = $1
                    ORDER BY r.{col} {direction} NULLS LAST, r.row_num LIMIT $2 OFFSET $3""",
                current["id"], page_size, (page - 1) * page_size)
            holdings["rows"] = [{
                "row_num": r["row_num"], "name": r["raw_name"], "ticker": r["raw_ticker"], "cusip": r["raw_cusip"],
                "isin": r["raw_isin"], "sedol": r["raw_sedol"], "asset_type": r["asset_type"],
                "weight": _num(r["weight"]), "shares": _num(r["shares"]), "market_value": _num(r["market_value"]),
                "currency": r["currency"], "resolution_state": r["resolution_state"],
                "resolved_via": r["resolved_via"], "queue_state": r["queue_state"],
                "resolved": r["resolution_state"] == "resolved"} for r in rows]
        versions = await T.versions_of(conn, entry["id"])
        draft = await T.live_draft(conn, entry["id"])
        approved = await T.live_approved(conn, entry["id"])
        registry = await T.load_registry(conn)
        shown = draft or approved
        values = _j(shown["term_values"]) if shown else {}
        reading_ids = [v.get("reading_id") for v in values.values() if v.get("reading_id")]
        for v in values.values():
            reading_ids += [a["reading_id"] for a in v.get("alternatives") or []]
        readings = await T.readings_for(conn, reading_ids)
        docs = [dict(r) for r in await conn.fetch(
            "SELECT id, doc_kind, form_type, accession_number, filing_date, source_url, sha256, byte_size, fetched_at "
            "FROM portfolio.replica_source_documents WHERE fund_global_security_id = $1::uuid AND system_to IS NULL "
            "ORDER BY fetched_at DESC", fund_id)]
        actions = await T.actions_of(conn, entry["id"])
        vocab = await _vocab(conn, perms)
    is_maker = bool(draft) and str(principal.user_id) in {str(m) for m in draft["maker_ids"] or []}
    fields = []
    for reg in registry:
        v = values.get(reg["field_key"]) or {}
        rd = readings.get(v.get("reading_id") or "")
        fields.append({
            "field_key": reg["field_key"], "label": reg["display_label"], "section": reg["section"],
            "data_type": reg["data_type"], "enum_values": reg["enum_values"], "unit": reg["unit"],
            "is_critical": reg["is_critical"], "disclosure_only": reg["disclosure_only"],
            "value": v.get("value"), "fund_value": v.get("fund_value"), "origin": v.get("origin"),
            "source": v.get("source"), "confidence": v.get("confidence"), "needs_review": bool(v.get("needs_review")),
            "review_reasons": v.get("review_reasons") or [], "reason": v.get("reason"),
            "quote": v.get("quote"), "quote_verified": v.get("quote_verified"),
            "document_url": rd["document_url"] if rd else None, "form_type": rd["form_type"] if rd else None,
            "provider_model": rd["provider_model"] if rd else None, "data_source": v.get("data_source"),
            "alternatives": [{**a, "provider_model": (readings.get(a["reading_id"]) or {}).get("provider_model")}
                             for a in v.get("alternatives") or []],
        })
    can_write = perms["can_write"]
    terms_vocab = {
        "editable": [f["field_key"] for f in fields] if can_write and draft else [],
        "correctable": [f["field_key"] for f in fields] if can_write and draft else [],
        "changeable": [f["field_key"] for f in fields if not f["disclosure_only"]]
        if can_write and (draft or approved) else [],
    }
    return {
        "entry": _entry_out(entry),
        "permissions": {**perms, "can_approve_draft": perms["can_approve"] and bool(draft) and not is_maker,
                        "is_maker_of_draft": is_maker},
        "vocabularies": {**vocab, **terms_vocab},
        "snapshot": _snap_out(current),
        "snapshots": [{"id": str(s["id"]), "as_of": s["as_of"].isoformat(), "status": s["status"],
                       "row_count": s["row_count"], "file_sha256": s["file_sha256"], "source_url": s["source_url"],
                       "fetched_at": s["fetched_at"].isoformat()} for s in snaps],
        "holdings": holdings,
        "terms": {
            "shown_version": ({"id": str(shown["id"]), "version_no": shown["version_no"], "status": shown["status"],
                               "extraction": _j(shown["extraction"]),
                               "needs_review_fields": list(shown["needs_review_fields"] or [])} if shown else None),
            "draft_version_id": str(draft["id"]) if draft else None,
            "approved_version_id": str(approved["id"]) if approved else None,
            "departures": _j(shown["departures"]) if shown else [],
            "fields": fields,
            "versions": [{"id": str(v["id"]), "version_no": v["version_no"], "status": v["status"],
                          "departures": len(_j(v["departures"]) or []),
                          "approved_at": v["approved_at"].isoformat() if v["approved_at"] else None,
                          "rejected_at": v["rejected_at"].isoformat() if v["rejected_at"] else None,
                          "decision_note": v["decision_note"]} for v in versions],
        },
        "sources": {"documents": [{**d, "id": str(d["id"]), "filing_date": d["filing_date"].isoformat()
                                   if d["filing_date"] else None, "fetched_at": d["fetched_at"].isoformat()}
                                  for d in docs]},
        "actions": [{**a, "id": str(a["id"]), "actor_id": str(a["actor_id"]) if a["actor_id"] else None,
                     "terms_version_id": str(a["terms_version_id"]) if a["terms_version_id"] else None,
                     "before_value": _j(a["before_value"]), "after_value": _j(a["after_value"]),
                     "created_at": a["created_at"].isoformat()} for a in actions],
    }


@router.post("/replication/registry/{entry_id}/holdings/refresh")
async def refresh_holdings(request: Request, entry_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    http = H.make_http()
    try:
        async with pool.acquire() as conn:
            entry = await T.load_entry(conn, org_id, entry_id)
        res = await _ingest_entry(pool, entry, http)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    finally:
        await http.aclose()
    async with pool.acquire() as conn:
        ref = await T._record_action(conn, org_id=org_id, entry_id=entry["id"], version_id=None,
                                     action="refresh_holdings", actor_id=principal.user_id,
                                     detail={"snapshot_id": res.snapshot_id, "created": res.created,
                                             "status": res.status})
    refs = [ref]
    reason = RS.review_reason(res)
    if reason:
        refs += await RS.open_holdings_review(pool, fund_id=str(entry["global_security_id"]),
                                              snapshot_id=res.snapshot_id, ticker=entry["ticker"], reason=reason,
                                              org_ids=[str(org_id)], actor_id=principal.user_id)
    await T.publish_actions(pool, refs)
    out = res.as_dict()
    out["review_task_opened"] = bool(reason)
    return out


@router.post("/replication/registry/{entry_id}/terms/extract")
async def extract_terms(request: Request, entry_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    http = H.make_http()
    try:
        async with pool.acquire() as conn:
            entry = await T.load_entry(conn, org_id, entry_id)
        version, run, refs = await T.build_draft(pool, org_id=org_id, entry=entry, actor_id=principal.user_id,
                                                 http=http)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    finally:
        await http.aclose()
    await T.publish_actions(pool, refs)
    return {"version_id": str(version["id"]), "version_no": version["version_no"], "extraction_status": run.status,
            "reason": run.reason, "needs_review": list(version["needs_review_fields"] or [])}


def _version_out(v: dict) -> dict:
    return {"id": str(v["id"]), "version_no": v["version_no"], "status": v["status"],
            "term_values": _j(v["term_values"]), "departures": _j(v["departures"]),
            "needs_review_fields": list(v["needs_review_fields"] or []),
            "maker_ids": [str(m) for m in v["maker_ids"] or []],
            "checker_id": str(v["checker_id"]) if v["checker_id"] else None}


@router.post("/replication/registry/{entry_id}/terms/corrections")
async def correct(request: Request, entry_id: str, body: Correction):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        version, refs = await T.correct_field(pool, org_id=org_id, actor_id=principal.user_id, entry_id=entry_id,
                                              version_id=body.version_id, field_key=body.field_key, value=body.value,
                                              notes=body.notes)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    await T.publish_actions(pool, refs)
    return {"version": _version_out(version), "action_id": refs[0].id}


@router.post("/replication/registry/{entry_id}/terms/changes")
async def change(request: Request, entry_id: str, body: Change):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        version, refs = await T.change_field(pool, org_id=org_id, actor_id=principal.user_id, entry_id=entry_id,
                                             field_key=body.field_key, value=body.value, reason=body.reason,
                                             version_id=body.version_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    await T.publish_actions(pool, refs)
    return {"version": _version_out(version), "action_id": refs[0].id}


async def _decide(request: Request, entry_id: str, version_id: str, body: Decision | None, approve: bool):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    try:
        UUID(version_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="terms version not found") from None
    pool = await get_pool()
    try:
        version, refs = await T.decide(pool, org_id=org_id, actor_id=principal.user_id, entry_id=entry_id,
                                       version_id=version_id, approve=approve, note=body.note if body else None)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    # rep3: the decision completes the version's human task; the task callback
    # already published the action's domain event — not published again here.
    return {"version": _version_out(version), "action_id": refs[0].id if refs else None}


@router.post("/replication/registry/{entry_id}/terms/{version_id}/approve")
async def approve(request: Request, entry_id: str, version_id: str, body: Decision | None = None):
    return await _decide(request, entry_id, version_id, body, True)


@router.post("/replication/registry/{entry_id}/terms/{version_id}/reject")
async def reject(request: Request, entry_id: str, version_id: str, body: Decision | None = None):
    return await _decide(request, entry_id, version_id, body, False)
