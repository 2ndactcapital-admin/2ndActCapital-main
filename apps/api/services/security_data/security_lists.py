"""TRACK1 — the platform's named security lists: the reviewed data file, member resolution against the security
master, and the load (list + members + tracking). The operator runs it through scripts/security_lists_load.py.

THE DATA FILE (docs/security_lists/security_lists_v1.json) — ONE reviewed file the operator edits:
    {"version": 1, "lists": [
        {"key": "core_etfs", "name": "Core ETFs", "description": "...", "overlap_note": "...",
         "source": {"kind": "curated"}, "members": [{"ticker": "VTI", "description": "..."}, ...]},
        {"key": "sp500", "name": "S&P 500", "source": {"kind": "index_holdings", "fund_ticker": "SPY",
                                                       "holdings_source": "ssga_spdr"}}, ...]}
A curated member may carry "cusip" instead of / beside its ticker (CUSIP wins: issuer-assigned, unique-indexed).

RESOLUTION: curated → the master by CUSIP, then ticker (securities_global.get_by_identifiers_bulk — an identifier
naming TWO securities is ambiguous and reported, never picked). Index lists → the fund's holdings through Replication
Studio's own pipeline (replica_holdings.choose_source / sec_fund_ids / fetch_holdings, replica_parsers.parse,
replica_holdings.resolve_rows): CUSIP / ISIN / SEDOL first, ticker only when the row has none. A member the master does
not hold is REPORTED as a miss, never created.

NO NETWORK IN A DRY RUN: curated lists resolve from the database alone; an index list reports where it WOULD fetch
from (and, when Replication Studio already holds a live snapshot of the fund, resolves that snapshot's rows instead).
"""

from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field
from datetime import date

from services import securities_global as SG

REPO = pathlib.Path(__file__).resolve().parents[4]
DATA_FILE = REPO / "docs" / "security_lists" / "security_lists_v1.json"
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{1,62}$")
SOURCE_KINDS = ("curated", "index_holdings")


class ListFileError(ValueError):
    pass


@dataclass
class Resolved:
    key: str
    name: str
    description: str | None
    source_kind: str
    source_detail: str
    as_of: date | None
    members: list[dict] = field(default_factory=list)     # {gsid, ticker, description, matched_by, weight}
    misses: list[dict] = field(default_factory=list)      # {ticker / cusip / name, reason}
    fetched: bool = False                                 # True when a network fetch produced the members
    ticker_fallback: int = 0                              # index rows resolved by ticker after their CUSIP missed
    note: str | None = None                               # e.g. "dry run: would fetch …"


def load_definitions(path: pathlib.Path | str = DATA_FILE) -> list[dict]:
    """The file's lists, validated: unique keys matching KEY_RE, a name, a known source kind; curated lists with
    members each carrying a ticker or CUSIP and a one-line description, no duplicate within a list; index lists naming
    their fund ticker. Raises ListFileError naming every problem."""
    try:
        doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ListFileError(f"cannot read the list file: {exc}") from exc
    lists = doc.get("lists") if isinstance(doc, dict) else None
    if not isinstance(lists, list) or not lists:
        raise ListFileError("the file has no 'lists' array")
    problems, seen = [], set()
    for i, d in enumerate(lists):
        k = d.get("key") if isinstance(d, dict) else None
        where = f"list {i} ({k!r})"
        if not isinstance(k, str) or not KEY_RE.match(k):
            problems.append(f"{where}: key must match {KEY_RE.pattern}")
        elif k in seen:
            problems.append(f"{where}: duplicate key")
        seen.add(k)
        if not str(d.get("name") or "").strip():
            problems.append(f"{where}: no name")
        src = d.get("source") or {}
        if src.get("kind") not in SOURCE_KINDS:
            problems.append(f"{where}: source.kind must be one of {SOURCE_KINDS}")
        if src.get("kind") == "index_holdings" and not str(src.get("fund_ticker") or "").strip():
            problems.append(f"{where}: an index list names its fund_ticker")
        if src.get("kind") == "curated":
            mem = d.get("members")
            if not isinstance(mem, list) or not mem:
                problems.append(f"{where}: a curated list needs members")
                continue
            dup = set()
            for j, m in enumerate(mem):
                t = str((m or {}).get("ticker") or "").strip().upper()
                c = str((m or {}).get("cusip") or "").strip().upper()
                if not t and not c:
                    problems.append(f"{where} member {j}: a ticker or a CUSIP")
                if not str((m or {}).get("description") or "").strip():
                    problems.append(f"{where} member {j} ({t or c}): a one-line description")
                ident = c or t
                if ident in dup:
                    problems.append(f"{where}: {ident} listed twice")
                dup.add(ident)
    if problems:
        raise ListFileError("; ".join(problems))
    return lists


def select(lists: list[dict], keys: list[str] | None) -> list[dict]:
    if not keys:
        return lists
    by = {d["key"]: d for d in lists}
    unknown = [k for k in keys if k not in by]
    if unknown:
        raise ListFileError(f"no list named {', '.join(unknown)} (the file holds {', '.join(by)})")
    return [by[k] for k in keys]


async def _ambiguous(conn, id_type: str, values: list[str]) -> set[str]:
    """Normalized values that DO exist in the master but name more than one security (absent from the bulk map)."""
    if not values:
        return set()
    rows = await conn.fetch(
        "SELECT i.id_value FROM portfolio.securities_global_identifiers i JOIN portfolio.securities_global s ON "
        "s.id = i.global_security_id AND s.valid_to IS NULL AND s.system_to IS NULL WHERE i.id_type = $1 AND "
        "i.id_value = ANY($2::text[]) AND i.valid_to IS NULL AND i.system_to IS NULL GROUP BY i.id_value "
        "HAVING count(DISTINCT COALESCE(s.canonical_id, s.id)) > 1", id_type, values)
    return {r[0] for r in rows}


async def resolve_curated(conn, d: dict) -> Resolved:
    out = Resolved(d["key"], d["name"], d.get("description"), "curated", "curated (docs/security_lists)", None)
    mem = d["members"]
    cusips = [m["cusip"] for m in mem if m.get("cusip")]
    tickers = [m["ticker"] for m in mem if m.get("ticker")]
    by_c = await SG.get_by_identifiers_bulk(conn, "cusip", cusips) if cusips else {}
    by_t = await SG.get_by_identifiers_bulk(conn, "ticker", tickers) if tickers else {}
    amb = await _ambiguous(conn, "ticker", [SG.normalize_identifier_value("ticker", t) for t in tickers])
    seen = set()
    for m in mem:
        hit, via = None, None
        if m.get("cusip"):
            hit, via = by_c.get(SG.normalize_identifier_value("cusip", m["cusip"])), "cusip"
        if hit is None and m.get("ticker"):
            hit, via = by_t.get(SG.normalize_identifier_value("ticker", m["ticker"])), "ticker"
        label = m.get("ticker") or m.get("cusip")
        if hit is None:
            nt = SG.normalize_identifier_value("ticker", m["ticker"]) if m.get("ticker") else None
            out.misses.append({"ticker": m.get("ticker"), "cusip": m.get("cusip"), "description": m["description"],
                               "reason": "the ticker names more than one security in the master (ambiguous — not "
                                         "picked)" if nt in amb else "not in the security master"})
            continue
        if hit["id"] in seen:
            out.misses.append({"ticker": label, "description": m["description"],
                               "reason": "resolves to a security already in this list"})
            continue
        seen.add(hit["id"])
        out.members.append({"gsid": hit["id"], "ticker": m.get("ticker"), "description": m["description"],
                            "matched_by": via, "weight": None})
    return out


def _miss_of(row) -> dict:
    return {"name": row.name, "ticker": row.ticker, "cusip": row.cusip, "isin": row.isin,
            "reason": "not in the security master"}


async def _members_from_rows(conn, out: Resolved, rows) -> None:
    from services import replica_holdings as RH

    resolved = await RH.resolve_rows(conn, rows)
    # A LIST may fall back to the ticker when the row's strong identifier is unknown to the master (Replication
    # Studio's own rule does not: a replica must never mis-map). Found 2026-11-12: the SPY file's non-US-domiciled
    # constituents (Linde, Accenture, Chubb …) carry G-/N- CUSIPs the master lacks, while their US tickers are there.
    # An ambiguous ticker (two securities) is still absent from the bulk map → still a miss.
    fallback = [r for r in rows if r.asset_type != "cash" and r.row_num not in resolved and r.ticker]
    if fallback:
        by_t = await SG.get_by_identifiers_bulk(conn, "ticker", [r.ticker for r in fallback])
        for r in fallback:
            hit = by_t.get(SG.normalize_identifier_value("ticker", r.ticker))
            if hit:
                resolved[r.row_num] = (hit["id"], "ticker")
                out.ticker_fallback += 1
    seen = set()
    for r in rows:
        if r.asset_type == "cash":
            continue
        hit = resolved.get(r.row_num)
        if hit is None:
            out.misses.append(_miss_of(r))
            continue
        gsid, via = hit
        if gsid in seen:
            continue
        seen.add(gsid)
        out.members.append({"gsid": gsid, "ticker": r.ticker, "description": r.name, "matched_by": via,
                            "weight": r.weight})


async def resolve_index(conn, d: dict, *, http=None, dry_run: bool) -> Resolved:
    """An index list from its fund's holdings. Dry run: no network — the live Replication Studio snapshot when one
    exists, else only where the real run would fetch from."""
    from services import replica_holdings as RH
    from services import replica_parsers as RP

    src = d["source"]
    fund = src["fund_ticker"].strip().upper()
    want = src.get("holdings_source")
    out = Resolved(d["key"], d["name"], d.get("description"), "index_holdings", f"{fund} holdings", None)
    fund_row = await SG.get_by_identifier(conn, "ticker", fund)
    if fund_row is None:
        out.misses.append({"ticker": fund, "reason": "the fund that defines this list is not in the security master"})
        return out
    snap = await RH.latest_snapshot(conn, fund_row["id"], want)
    if dry_run:
        if snap is not None:
            rows = [RP.HoldingRow(row_num=r["row_num"], name=r.get("raw_name"), ticker=r.get("raw_ticker"),
                                  cusip=r.get("raw_cusip"), isin=r.get("raw_isin"), sedol=r.get("raw_sedol"),
                                  weight=r.get("weight"), shares=r.get("shares"), market_value=r.get("market_value"),
                                  currency=r.get("currency"), asset_type=r.get("asset_type") or "equity")
                    for r in await RH.snapshot_rows(conn, snap["id"])]
            out.as_of = snap["as_of"]
            out.source_detail = f"{fund} holdings via {snap['source_key']} (Replication Studio snapshot)"
            await _members_from_rows(conn, out, rows)
        else:
            out.source_detail = f"{fund} holdings via {want or 'the first active holdings source'}"
            out.note = (f"dry run: would fetch {fund}'s holdings from {want or 'its holdings source'} — network, the real "
                        "run only; members and misses are known after it")
        return out
    sec = await RH.sec_fund_ids(http, fund) if (want == "sec_nport" or want is None) else None
    source = await RH.choose_source(conn, ticker=fund, series_id=(sec or {}).get("series_id"), override_key=want)
    fetched = await RH.fetch_holdings(http, source, ticker=fund, series_id=(sec or {}).get("series_id"))
    parsed = RP.parse(source["parser"], fetched.data)
    if parsed.fund_ticker and parsed.fund_ticker.upper() != fund:
        raise ListFileError(f"{d['key']}: the holdings file is for {parsed.fund_ticker}, not {fund}")
    out.as_of = parsed.as_of
    out.fetched = True
    out.source_detail = f"{fund} holdings via {source['key']} ({fetched.url})"[:500]
    await _members_from_rows(conn, out, parsed.rows)
    return out


async def resolve(conn, d: dict, *, http=None, dry_run: bool) -> Resolved:
    if d["source"]["kind"] == "curated":
        return await resolve_curated(conn, d)
    return await resolve_index(conn, d, http=http, dry_run=dry_run)


async def write(conn, r: Resolved, *, actor_id=None, run_id=None, track: bool = True) -> dict:
    """Store the list + members and (unless ``track`` is False) track every member, naming the list. Caller holds
    platform scope; one transaction."""
    from services.security_data import tracking as TR

    async with conn.transaction():
        stored = await TR.upsert_list(conn, list_key=r.key, name=r.name, description=r.description,
                                      source_kind=r.source_kind, source_detail=r.source_detail, as_of=r.as_of,
                                      members=r.members, misses=r.misses, actor_id=actor_id, run_id=run_id)
        tracked = None
        if track and r.members:
            tracked = await TR.track_list(conn, r.key, actor_id=actor_id,
                                          note=f"named list {r.name} (security_lists_load.py)")
    return {"stored": stored, "tracked": tracked}


# ── REVIEWQ1 — list misses resolved through OpenFIGI into review items ──────
def miss_identifier(m: dict) -> tuple[str, str] | None:
    """The identifier a miss is mapped by: a non-US ISIN (the home-market line) → isin; a US ISIN with its CUSIP (a
    CUSIP change: Navient, CBIZ …) → cusip; else whichever it has."""
    isin = (m.get("isin") or "").strip().upper() or None
    cusip = (m.get("cusip") or "").strip().upper() or None
    if isin and not isin.startswith("US"):
        return "isin", isin
    if cusip:
        return "cusip", cusip
    if isin:
        return "isin", isin
    return None


async def resolve_misses(conn, list_keys: list[str] | None, *, transport, key: str | None = None,
                         dry_run: bool = True, run_id=None, throttle: bool = True, budget=None) -> dict:
    """Every stored miss of the named lists (all lists when None) with an ISIN / CUSIP → OpenFIGI (the existing
    provider, US composite) → a REVIEW ITEM, never a merge:
        the FIGI (else the ticker) names ONE master security → kind list_match (Keep both = the miss's identifier added
                                                                  as an alternate; Use existing = rejected)
        OpenFIGI answers, nothing in the master              → kind add_to_master (OpenFIGI's ticker / name / type)
        OpenFIGI has no answer                               → reported only (nothing to add)
    Dry run: the OpenFIGI look-ups run (read-only) and NOTHING is written (no review item, no mapping row). The caller
    holds platform scope. Repeat suppression is insert_review's (the same open item → nothing; a Known one → seen
    again)."""
    from services import security_master_load as M
    from services import security_master_symbols as SYM
    from services.security_data import tracking as TR
    from services.security_data.providers import openfigi as OF

    lists = [x for x in await TR.list_rows(conn) if list_keys is None or x["list_key"] in list_keys]
    work: dict[tuple, dict] = {}
    for lst in lists:
        for m in lst.get("misses") or []:
            ident = miss_identifier(m)
            if ident is None:
                continue
            work.setdefault(ident, {"miss": m, "lists": []})["lists"].append(lst["list_key"])
    without = sum(1 for lst in lists for m in (lst.get("misses") or []) if miss_identifier(m) is None)
    idents = list(work)
    jobs = [OF.isin_job(v) if t == "isin" else OF.cusip_job(v) for t, v in idents]
    answers, requests = await OF.map_jobs(transport, jobs, key=key, budget=budget, throttle=throttle) if jobs else ([], 0)
    by_job = {(a.job["idType"], a.job["idValue"]): a for a in answers}
    hits = {i: by_job.get(("ID_ISIN" if i[0] == "isin" else "ID_CUSIP", i[1])) for i in idents}
    figis = [a.hit.get("compositeFIGI") or a.hit.get("figi") for a in hits.values() if a and a.hit
             and (a.hit.get("compositeFIGI") or a.hit.get("figi"))]
    tickers = [SYM.normalize_symbol(a.hit.get("ticker")) for a in hits.values() if a and a.hit and a.hit.get("ticker")]
    by_f = await SG.get_by_identifiers_bulk(conn, "figi", figis) if figis else {}
    by_t = await SG.get_by_identifiers_bulk(conn, "ticker", [t for t in tickers if t]) if tickers else {}
    out = {"lists": [x["list_key"] for x in lists], "misses_with_identifier": len(idents),
           "misses_without_identifier": without, "openfigi_jobs": len(jobs), "openfigi_requests": requests,
           "list_match": 0, "add_to_master": 0, "no_answer": 0, "not_reached": 0, "items_created": 0,
           "already_open_or_known": 0, "dry_run": dry_run, "rows": []}
    for (t, v), a in hits.items():
        w = work[(t, v)]
        m = w["miss"]
        row = {"id_type": t, "value": v, "name": m.get("name") or m.get("description"), "lists": w["lists"]}
        if a is None:
            out["not_reached"] += 1
            row["outcome"] = "not reached (budget / request limit) — next run"
            out["rows"].append(row)
            continue
        if not a.hit:
            out["no_answer"] += 1
            row["outcome"] = f"OpenFIGI: {a.warning or 'no answer'}"
            out["rows"].append(row)
            continue
        figi = a.hit.get("compositeFIGI") or a.hit.get("figi")
        ticker = SYM.normalize_symbol(a.hit.get("ticker"))
        sec, via = (by_f.get(SG.normalize_identifier_value("figi", figi)) if figi else None), "figi"
        if sec is None and ticker:
            sec, via = by_t.get(SG.normalize_identifier_value("ticker", ticker)), "ticker"
        detail = {"lists": w["lists"], "figi": figi, "ticker": ticker, "name": (a.hit.get("name") or "").strip() or None,
                  "security_type": SYM.type_from_figi(a.hit), "miss_name": row["name"],
                  "exch_code": a.hit.get("exchCode")}
        if sec is not None:
            have = [r["id_value"] for r in await conn.fetch(
                "SELECT id_value FROM portfolio.securities_global_identifiers WHERE global_security_id = $1::uuid AND "
                "id_type = $2 AND valid_to IS NULL AND system_to IS NULL ORDER BY id_value", sec["id"], t)]
            kind, gsid = "list_match", sec["id"]
            detail["matched_by"] = via
            existing = ", ".join(have) or "none"
            row.update(outcome="list_match", security_id=sec["id"], security_name=sec["name"], matched_by=via)
        else:
            kind, gsid, existing = "add_to_master", None, None
            row.update(outcome="add_to_master", ticker=ticker, openfigi_name=detail["name"])
        out[kind] += 1
        if not dry_run:
            rid = await M.insert_review(conn, kind=kind, subject_key=f"{t}:{v}", provider_key="openfigi", field=t,
                                        incoming_value=v, existing_value=existing, global_security_id=gsid,
                                        detail=detail, run_id=run_id)
            out["items_created" if rid else "already_open_or_known"] += 1
            row["review_id"] = str(rid) if rid else None
        out["rows"].append(row)
    if not dry_run and answers:
        await OF.record(conn, answers, run_id=run_id)
    return out
