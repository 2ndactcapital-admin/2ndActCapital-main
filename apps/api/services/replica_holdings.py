"""Replication Studio (rep1) — fund holdings ingestion.

    ticker / CUSIP ─► global security (services.securities_global — the ONE
                      resolver; created there if missing, by the platform)
                  ─► SEC ids (company_tickers_mf.json, cached in-process)
                  ─► holdings source (portfolio.replica_holdings_sources —
                      data; the parser it names is code, services.replica_parsers)
                  ─► fetch ─► R2 (gzipped raw bytes) ─► snapshot + rows
                      (marketdata1: the snapshot records provider_key = the source key — the
                      security-data layer's regulator_filing / issuer_file providers)

PLATFORM DATA. Snapshots, rows and the raw files are public source data,
shared by every org and fetched once (owner_scope 'platform', org_id NULL).
Every write here runs in its OWN transaction with ``app.is_super_admin`` set
LOCAL (:func:`platform_tx`) — never on a connection that then does org work,
because a SET LOCAL lasts until the outer transaction ends.

IDEMPOTENT. The same file bytes (SHA-256) for the same fund and source never
create a second snapshot: checked first, and enforced by
``replica_holdings_snapshots_file_uq`` (ON CONFLICT DO NOTHING) for a race.

NEVER GUESSED. A constituent resolves only through an identifier the file
published, through ``securities_global.get_by_identifiers_bulk`` — CUSIP,
then ISIN, then SEDOL; a TICKER only when the row published no stronger
identifier at all, and only when exactly one surviving security carries it.
A row that published a CUSIP the master does not know is unresolved (it is
not then matched on its ticker). Every unresolved non-cash row is QUEUED as an
'unresolved' ``constituent_of`` edge on the shared relationships table — the
same review queue the note pipeline uses (``services.underlying_resolution``).

VALIDATION, recorded on the snapshot; any failure makes it 'needs_review',
not live:
  * weights (excluding cash) sum to 1 ± 0.5%;
  * every non-cash row carries a weight;
  * row count within ±20% of the previous live snapshot;
  * no duplicate constituent;
  * as_of not older than the previous live snapshot's.

DIFF against the previous live snapshot of the same fund and source: added,
removed, and weight moves of at least REPLICA_DIFF_WEIGHT_THRESHOLD (default
0.0025 = 25 bp); ``turnover`` = ½ Σ|Δw|. ``diff_exceeds_threshold`` when a
constituent was added or removed or turnover ≥ REPLICA_DIFF_TURNOVER_THRESHOLD
(default 0.02) — the daily refresh opens a review task for those.

DRY RUN fetches, parses, resolves, validates and diffs, and writes NOTHING —
no snapshot, no rows, no queued edge, no R2 object, no security created.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool

from services import edgar_fetch, storage
from services import replica_parsers as P
from services import securities_global as SG

T_SOURCES = "portfolio.replica_holdings_sources"
T_SNAP = "portfolio.replica_holdings_snapshots"
T_ROWS = "portfolio.replica_holdings_rows"

SEC_MF_URL = "https://www.sec.gov/files/company_tickers_mf.json"
SEC_SERIES_FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series}&type={form}"
                   "&dateb=&owner=include&count=10&output=atom")

WEIGHT_TOLERANCE = Decimal("0.005")
ROWCOUNT_TOLERANCE = Decimal("0.20")
MF_CACHE_SECONDS = 24 * 3600


def diff_weight_threshold() -> Decimal:
    return Decimal(os.environ.get("REPLICA_DIFF_WEIGHT_THRESHOLD") or "0.0025")


def diff_turnover_threshold() -> Decimal:
    return Decimal(os.environ.get("REPLICA_DIFF_TURNOVER_THRESHOLD") or "0.02")


class ReplicaHoldingsError(RuntimeError):
    """Ingestion could not proceed for a reason the caller can act on."""


class NoHoldingsSource(ReplicaHoldingsError):
    pass


# ── connections ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def platform_tx(pool):
    """One platform write unit: a transaction with app.is_super_admin LOCAL.

    Works on the RLS pool (acquire() already opened a transaction; this nests
    as a savepoint and the LOCAL setting lives until that outer transaction
    ends — the connection is released right after) and on a plain asyncpg
    pool (acquire() opens nothing; this opens the transaction)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            yield conn


# ── HTTP ────────────────────────────────────────────────────────────────────
def make_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=edgar_fetch.REQUEST_TIMEOUT, follow_redirects=True)


def issuer_user_agent() -> str:
    """Issuer sites are not the SEC: a declared but ordinary agent. Settable
    (REPLICA_HTTP_USER_AGENT) because an issuer may start refusing it."""
    return (os.environ.get("REPLICA_HTTP_USER_AGENT")
            or "Mozilla/5.0 (compatible; HollisworksReplicationStudio/1.0)")


async def _issuer_get(http: httpx.AsyncClient, url: str) -> bytes:
    r = await http.get(url, headers={"User-Agent": issuer_user_agent(), "Accept": "*/*"})
    if r.status_code != 200:
        raise ReplicaHoldingsError(f"holdings download answered HTTP {r.status_code}: {url}")
    if not r.content:
        raise ReplicaHoldingsError(f"holdings download was empty: {url}")
    return r.content


# ── SEC ticker file (cached) ────────────────────────────────────────────────
_MF_CACHE: dict[str, Any] = {"at": 0.0, "map": None}


async def sec_fund_ids(http: httpx.AsyncClient, ticker: str) -> dict | None:
    """{cik, series_id, class_id} for a fund ticker from the SEC's own
    company_tickers_mf.json, or None (a unit investment trust such as SPY has
    no series and is absent). Fetched through the EDGAR client (declared
    User-Agent, rate limit), cached in-process for a day."""
    if _MF_CACHE["map"] is None or time.monotonic() - _MF_CACHE["at"] > MF_CACHE_SECONDS:
        r = await edgar_fetch._get(http, SEC_MF_URL)
        body = r.json()
        fields = body.get("fields") or []
        idx = {f: i for i, f in enumerate(fields)}
        if not {"cik", "seriesId", "classId", "symbol"} <= set(idx):
            raise ReplicaHoldingsError(f"company_tickers_mf.json has unexpected fields {fields}")
        m = {}
        for row in body.get("data") or []:
            sym = str(row[idx["symbol"]] or "").upper()
            if sym:
                m[sym] = {"cik": str(row[idx["cik"]]), "series_id": row[idx["seriesId"]],
                          "class_id": row[idx["classId"]]}
        _MF_CACHE.update(at=time.monotonic(), map=m)
    return _MF_CACHE["map"].get(ticker.upper())


def clear_sec_cache() -> None:
    _MF_CACHE.update(at=0.0, map=None)


# ── EDGAR series feed (N-PORT, prospectus) ──────────────────────────────────
_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.S)


def _tag(block: str, name: str) -> str | None:
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
    return m.group(1).strip() if m else None


async def latest_series_filing(http: httpx.AsyncClient, series_id: str, forms: tuple[str, ...]) -> dict | None:
    """The newest filing of ``forms`` for one fund SERIES, from EDGAR's
    series-filtered atom feed (a trust files for hundreds of series; the
    series id narrows it — confirmed for IVV, S000004310, in Task 1b)."""
    best = None
    for form in forms:
        feed = await edgar_fetch._get(http, SEC_SERIES_FEED.format(series=series_id, form=form))
        text = feed.text
        cik = _tag(text, "cik")
        for block in _ENTRY_RE.findall(text):
            ftype = _tag(block, "filing-type")
            if ftype not in forms and (ftype or "").replace("/A", "") not in forms:
                continue
            acc = _tag(block, "accession-number")
            fdate = _tag(block, "filing-date")
            href = (_tag(block, "filing-href") or "").replace("&amp;", "&")
            if not acc or not fdate:
                continue
            cand = {"form": ftype, "accession": acc, "filing_date": date.fromisoformat(fdate),
                    "index_url": href, "cik": str(int(cik)) if cik and cik.isdigit() else None}
            if best is None or (cand["filing_date"], cand["accession"]) > (best["filing_date"], best["accession"]):
                best = cand
    return best


def filing_folder(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"


# ── fund resolution (security master) ───────────────────────────────────────
@dataclass
class FundIdentity:
    global_security_id: str | None
    ticker: str
    cusip: str | None
    name: str
    sec: dict | None
    created: bool = False
    found_by: str | None = None


async def resolve_fund(pool, *, ticker: str | None, cusip: str | None, http: httpx.AsyncClient,
                       fund_name: str | None = None, create: bool = True) -> FundIdentity:
    """Ticker or CUSIP → the fund's global security (+ its SEC ids).

    Lookup through ``securities_global.get_by_identifier`` — CUSIP first
    (issuer-assigned, unique-indexed), then ticker. Missing and ``create``:
    the platform creates it (``create_security`` type 'fund' +
    ``add_identifier``) — a fund's own ticker from the SEC's ticker file is a
    published fact, not a guess. A CUSIP the master does not know cannot be
    turned into a ticker here, so it is refused with a clear message."""
    ticker = (ticker or "").strip().upper() or None
    cusip = (cusip or "").strip().upper() or None
    if not ticker and not cusip:
        raise ReplicaHoldingsError("give a ticker or a CUSIP")
    async with pool.acquire() as conn:
        hit = None
        found_by = None
        if cusip:
            hit = await SG.get_by_identifier(conn, "cusip", cusip)
            found_by = "cusip" if hit else None
        if hit is None and ticker:
            hit = await SG.get_by_identifier(conn, "ticker", ticker)
            found_by = "ticker" if hit else None
        if hit is not None and ticker is None:
            ticker = await conn.fetchval(
                f"SELECT id_value FROM {SG.TABLE_IDENT} WHERE global_security_id = $1::uuid AND id_type = 'ticker' "
                "AND valid_to IS NULL AND system_to IS NULL ORDER BY is_primary DESC LIMIT 1", hit["id"])
    if ticker is None:
        raise ReplicaHoldingsError(
            f"CUSIP {cusip} is not in the security master and carries no ticker to look up — add the fund by ticker")
    sec = await sec_fund_ids(http, ticker)
    if hit is not None:
        if hit["security_type"] not in ("fund", "other"):
            raise ReplicaHoldingsError(
                f"{ticker} resolves to a {hit['security_type']} ({hit['name']}), not a fund")
        return FundIdentity(hit["id"], ticker, cusip, hit["name"], sec, False, found_by)
    name = (fund_name or "").strip() or ticker
    if not create:
        return FundIdentity(None, ticker, cusip, name, sec, False, None)
    async with platform_tx(pool) as conn:
        sid = await SG.create_security(conn, name=name, security_type="fund", short_name=ticker,
                                       currency_code="USD", is_super_admin=True)
        await SG.add_identifier(conn, global_security_id=sid, id_type="ticker", id_value=ticker,
                                is_primary=True, is_super_admin=True)
        if cusip:
            await SG.add_identifier(conn, global_security_id=sid, id_type="cusip", id_value=cusip,
                                    is_super_admin=True)
    return FundIdentity(sid, ticker, cusip, name, sec, True, None)


# ── sources ─────────────────────────────────────────────────────────────────
async def list_sources(conn, *, active_only: bool = False) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT key, issuer, url_template, parser, format, active, match_tickers, priority, terms_of_use_note, "
        f"confirmation_note, confirmed_on FROM {T_SOURCES} WHERE valid_to IS NULL AND system_to IS NULL "
        f"{'AND active' if active_only else ''} ORDER BY priority, key")
    return [dict(r) for r in rows]


async def choose_source(conn, *, ticker: str, series_id: str | None, override_key: str | None = None) -> dict:
    """The fund's holdings source. An explicit, ACTIVE override wins; else the
    first active issuer source that lists the ticker; else N-PORT when the fund
    has an SEC series; else there is none (a UIT outside every issuer list)."""
    sources = await list_sources(conn, active_only=True)
    by_key = {s["key"]: s for s in sources}
    if override_key:
        if override_key not in by_key:
            raise NoHoldingsSource(f"holdings source '{override_key}' does not exist or is not active")
        return by_key[override_key]
    for s in sources:
        if s["parser"] != "sec_nport" and ticker.upper() in [t.upper() for t in s["match_tickers"] or []]:
            return s
    if series_id:
        for s in sources:
            if s["parser"] == "sec_nport":
                return s
    raise NoHoldingsSource(
        f"no active holdings source covers {ticker}: no issuer parser lists it and it has no SEC series for N-PORT")


# ── fetch ───────────────────────────────────────────────────────────────────
@dataclass
class Fetched:
    data: bytes
    url: str
    accession: str | None = None
    ext: str = "bin"


async def fetch_holdings(http: httpx.AsyncClient, source: dict, *, ticker: str, series_id: str | None) -> Fetched:
    if source["parser"] == "sec_nport":
        if not series_id:
            raise NoHoldingsSource(f"{ticker} has no SEC series id — N-PORT cannot be located")
        filing = await latest_series_filing(http, series_id, ("NPORT-P",))
        if filing is None or not filing.get("cik"):
            raise ReplicaHoldingsError(f"no NPORT-P filing found for series {series_id}")
        url = f"{filing_folder(filing['cik'], filing['accession'])}/primary_doc.xml"
        r = await edgar_fetch._get(http, url)
        return Fetched(r.content, url, filing["accession"], "xml")
    template = source.get("url_template") or ""
    if "{issuer_product_id}" in template:
        raise NoHoldingsSource(f"source '{source['key']}' needs a per-fund issuer product id this sprint does not hold")
    url = template.format(ticker=ticker.upper(), ticker_lower=ticker.lower())
    return Fetched(await _issuer_get(http, url), url, None, source["format"])


# ── resolution of rows ──────────────────────────────────────────────────────
def queue_text(row: P.HoldingRow) -> str:
    """The verbatim string an unresolved constituent is queued under. Built
    from what the file published, so the same constituent in tomorrow's file
    maps to the SAME edge (uq_sec_global_rel_edge), never a duplicate."""
    parts = [row.name or "(unnamed)"]
    for label, v in (("CUSIP", row.cusip), ("ISIN", row.isin), ("SEDOL", row.sedol), ("TICKER", row.ticker)):
        if v:
            parts.append(f"{label} {v.upper()}")
    return " | ".join(parts)


async def resolve_rows(conn, rows: list[P.HoldingRow]) -> dict[int, tuple[str, str]]:
    """{row_num: (global_security_id, via)} for every row that resolves."""
    lookups = {}
    for id_type, attr in (("cusip", "cusip"), ("isin", "isin"), ("sedol", "sedol"), ("ticker", "ticker")):
        vals = [getattr(r, attr) for r in rows if r.asset_type != "cash" and getattr(r, attr)]
        lookups[id_type] = await SG.get_by_identifiers_bulk(conn, id_type, vals) if vals else {}
    out: dict[int, tuple[str, str]] = {}
    for r in rows:
        if r.asset_type == "cash":
            continue
        strong = [(t, getattr(r, t)) for t in ("cusip", "isin", "sedol") if getattr(r, t)]
        for id_type, v in strong:
            hit = lookups[id_type].get(SG.normalize_identifier_value(id_type, v))
            if hit:
                out[r.row_num] = (hit["id"], id_type)
                break
        if r.row_num not in out and not strong and r.ticker:
            hit = lookups["ticker"].get(SG.normalize_identifier_value("ticker", r.ticker))
            if hit:
                out[r.row_num] = (hit["id"], "ticker")
    return out


# ── validation + diff ───────────────────────────────────────────────────────
def validate(parsed: P.ParsedHoldings, previous: dict | None) -> dict:
    non_cash = [r for r in parsed.rows if r.asset_type != "cash"]
    total = sum((r.weight or Decimal(0)) for r in parsed.rows)
    ex_cash = sum((r.weight or Decimal(0)) for r in non_cash)
    checks = {}
    checks["weights_sum"] = {"ok": abs(ex_cash - 1) <= WEIGHT_TOLERANCE,
                             "detail": f"non-cash weights sum to {ex_cash:.6f} (1 ± {WEIGHT_TOLERANCE})"}
    missing = [r.row_num for r in non_cash if r.weight is None]
    checks["weights_present"] = {"ok": not missing, "detail": f"{len(missing)} non-cash row(s) without a weight"}
    keys = [r.identity() for r in non_cash if r.identity()]
    dups = sorted({k for k in keys if keys.count(k) > 1})
    checks["no_duplicates"] = {"ok": not dups, "detail": f"duplicate constituents: {dups[:10]}" if dups else "none"}
    if previous:
        prev_n = int(previous["row_count"])
        lo, hi = prev_n * (1 - ROWCOUNT_TOLERANCE), prev_n * (1 + ROWCOUNT_TOLERANCE)
        n = len(parsed.rows)
        checks["row_count"] = {"ok": lo <= n <= hi, "detail": f"{n} rows vs previous {prev_n} (±20%)"}
        checks["as_of_order"] = {"ok": parsed.as_of >= previous["as_of"],
                                 "detail": f"as_of {parsed.as_of} vs previous {previous['as_of']}"}
    else:
        checks["row_count"] = {"ok": True, "detail": "first snapshot — nothing to compare"}
        checks["as_of_order"] = {"ok": True, "detail": "first snapshot — nothing to compare"}
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks,
            "total_weight": str(total), "total_weight_ex_cash": str(ex_cash)}


def compute_diff(rows: list[P.HoldingRow], previous_rows: list[dict] | None) -> tuple[dict, bool]:
    if previous_rows is None:
        return {"first_snapshot": True, "added": [], "removed": [], "moves": [], "turnover": "0"}, False
    threshold = diff_weight_threshold()

    def key_of(d) -> str | None:
        for k in ("raw_cusip", "raw_isin", "raw_sedol", "raw_ticker"):
            if d.get(k):
                return str(d[k]).upper()
        return None

    now = {}
    for r in rows:
        if r.asset_type != "cash" and r.identity():
            now[r.identity()] = {"name": r.name, "ticker": r.ticker, "weight": r.weight or Decimal(0)}
    before = {}
    for d in previous_rows:
        if d["asset_type"] != "cash" and key_of(d):
            before[key_of(d)] = {"name": d["raw_name"], "ticker": d["raw_ticker"], "weight": d["weight"] or Decimal(0)}
    added = [{"key": k, **{**v, "weight": str(v["weight"])}} for k, v in sorted(now.items()) if k not in before]
    removed = [{"key": k, **{**v, "weight": str(v["weight"])}} for k, v in sorted(before.items()) if k not in now]
    moves = []
    turnover = Decimal(0)
    for k in sorted(set(now) | set(before)):
        a = before.get(k, {}).get("weight", Decimal(0))
        b = now.get(k, {}).get("weight", Decimal(0))
        delta = b - a
        turnover += abs(delta)
        if k in now and k in before and abs(delta) >= threshold:
            moves.append({"key": k, "name": now[k]["name"], "ticker": now[k]["ticker"],
                          "before": str(a), "after": str(b), "delta": str(delta)})
    turnover = turnover / 2
    moves.sort(key=lambda m: -abs(Decimal(m["delta"])))
    exceeds = bool(added or removed) or turnover >= diff_turnover_threshold()
    return ({"first_snapshot": False, "added": added, "removed": removed, "moves": moves,
             "turnover": str(turnover), "weight_threshold": str(threshold),
             "turnover_threshold": str(diff_turnover_threshold())}, exceeds)


# ── reads ───────────────────────────────────────────────────────────────────
async def latest_snapshot(conn, fund_id: str, source_key: str | None = None, *, live_only: bool = True) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT * FROM {T_SNAP} WHERE fund_global_security_id = $1::uuid "
        "AND ($2::text IS NULL OR source_key = $2) AND (NOT $3::bool OR status = 'live') "
        "AND valid_to IS NULL AND system_to IS NULL ORDER BY as_of DESC, fetched_at DESC LIMIT 1",
        str(fund_id), source_key, live_only)
    return dict(row) if row else None


async def snapshot_rows(conn, snapshot_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_ROWS} WHERE snapshot_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY row_num", str(snapshot_id))]


def r2_key_for(ticker: str, as_of: date, sha: str, ext: str) -> str:
    return f"replica/holdings/{ticker.upper()}/{as_of.isoformat()}/{sha[:16]}.{ext}.gz"


# ── ingest ──────────────────────────────────────────────────────────────────
@dataclass
class IngestResult:
    fund_global_security_id: str
    ticker: str
    source_key: str
    dry_run: bool
    created: bool = False
    snapshot_id: str | None = None
    status: str | None = None
    as_of: date | None = None
    row_count: int = 0
    resolved: int = 0
    unresolved: int = 0
    queued: int = 0
    validation: dict = field(default_factory=dict)
    diff: dict = field(default_factory=dict)
    diff_exceeds_threshold: bool = False
    sha256: str | None = None
    r2_key: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["as_of"] = self.as_of.isoformat() if self.as_of else None
        return d


async def ingest(pool, *, fund_global_security_id: str | None, ticker: str, source: dict, series_id: str | None,
                 http: httpx.AsyncClient, dry_run: bool = False) -> IngestResult:
    if fund_global_security_id is None and not dry_run:
        raise ReplicaHoldingsError("a fund that is not in the security master can only be dry-run")
    res = IngestResult(str(fund_global_security_id) if fund_global_security_id else "", ticker.upper(),
                       source["key"], dry_run)
    fetched = await fetch_holdings(http, source, ticker=ticker, series_id=series_id)
    sha = hashlib.sha256(fetched.data).hexdigest()
    res.sha256 = sha
    existing = None
    if fund_global_security_id is not None:
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                f"SELECT id, status, as_of, row_count FROM {T_SNAP} WHERE fund_global_security_id = $1::uuid "
                "AND source_key = $2 AND file_sha256 = $3 AND system_to IS NULL", str(fund_global_security_id),
                source["key"], sha)
    if existing is not None:
        res.snapshot_id, res.status, res.as_of = str(existing["id"]), existing["status"], existing["as_of"]
        res.row_count, res.reason = int(existing["row_count"]), "same file bytes as an existing snapshot"
        return res

    parsed = P.parse(source["parser"], fetched.data)
    if parsed.fund_ticker and parsed.fund_ticker.upper() != ticker.upper():
        raise ReplicaHoldingsError(f"the file is for {parsed.fund_ticker}, not {ticker}")
    async with pool.acquire() as conn:
        previous = (await latest_snapshot(conn, fund_global_security_id, source["key"])
                    if fund_global_security_id else None)
        prev_rows = await snapshot_rows(conn, previous["id"]) if previous else None
        resolved = await resolve_rows(conn, parsed.rows)
    validation = validate(parsed, previous)
    diff, exceeds = compute_diff(parsed.rows, prev_rows)
    unresolved_rows = [r for r in parsed.rows if r.asset_type != "cash" and r.row_num not in resolved]
    res.as_of, res.row_count = parsed.as_of, len(parsed.rows)
    res.resolved, res.unresolved = len(resolved), len(unresolved_rows)
    res.validation, res.diff, res.diff_exceeds_threshold = validation, diff, exceeds
    res.status = "live" if validation["ok"] else "needs_review"
    if dry_run:
        res.reason = "dry run — nothing written"
        return res

    key = r2_key_for(ticker, parsed.as_of, sha, fetched.ext)
    await run_in_threadpool(storage.upload_bytes, key, gzip.compress(fetched.data, mtime=0),
                            "application/gzip")
    res.r2_key = key
    async with platform_tx(pool) as conn:
        queued = await SG.add_relationships_bulk(
            conn, from_global_security_id=fund_global_security_id,
            raw_texts=[queue_text(r) for r in unresolved_rows], relationship_type="constituent_of",
            is_super_admin=True) if unresolved_rows else {}
        snap_id = await conn.fetchval(
            f"""INSERT INTO {T_SNAP} (fund_global_security_id, as_of, source_key, source_url, accession_number,
                    file_sha256, r2_key, byte_size, row_count, total_weight, total_weight_ex_cash, status,
                    validation, previous_snapshot_id, diff, diff_exceeds_threshold, provider_key)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::uuid, $15::jsonb, $16,
                        $3)
                ON CONFLICT ON CONSTRAINT replica_holdings_snapshots_file_uq DO NOTHING
                RETURNING id""",
            str(fund_global_security_id), parsed.as_of, source["key"], fetched.url, fetched.accession, sha, key,
            len(fetched.data), len(parsed.rows), Decimal(validation["total_weight"]),
            Decimal(validation["total_weight_ex_cash"]), res.status, _dumps(validation),
            str(previous["id"]) if previous else None, _dumps(diff), exceeds)
        if snap_id is None:          # a concurrent ingest of the same bytes won
            res.reason = "same file bytes as an existing snapshot (concurrent)"
            return res
        records = []
        for r in parsed.rows:
            hit = resolved.get(r.row_num)
            if r.asset_type == "cash":
                state, rel = "not_applicable", None
            elif hit:
                state, rel = "resolved", None
            else:
                state, rel = "unresolved", queued.get(queue_text(r))
            records.append((snap_id, r.row_num, r.name, r.ticker, r.cusip, r.isin, r.sedol, _dumps(r.raw),
                            r.asset_type, hit[0] if hit else None, state, hit[1] if hit else None, rel,
                            r.weight, r.shares, r.market_value, r.currency))
        await conn.executemany(
            f"""INSERT INTO {T_ROWS} (snapshot_id, row_num, raw_name, raw_ticker, raw_cusip, raw_isin, raw_sedol,
                    raw, asset_type, constituent_global_security_id, resolution_state, resolved_via,
                    relationship_id, weight, shares, market_value, currency)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::uuid, $11, $12, $13::uuid, $14, $15, $16, $17)""",
            records)
    res.created, res.snapshot_id, res.queued = True, str(snap_id), len(queued)
    return res


def _dumps(obj) -> str:
    import json

    return json.dumps(obj, default=str, sort_keys=True)
