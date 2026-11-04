"""SECMASTER1 — the source pulls, shared by the nightly jobs (services.security_master_jobs) and the operator's
one-time load (scripts/secmaster_initial_load.py). Every stage:

  * pulls through an INJECTED SourceTransport (RealSourceTransport in production; recorded fixtures in the verify);
  * counts every request against the provider's daily budget and writes it to provider_call_log (CallBudget);
  * parses as it streams and hands the matcher batches of ``batch_size`` (one transaction per batch);
  * stops at its ``deadline`` (seconds) with the rest deferred to the next run — a step stays inside the
    scheduler tick; the one-time load passes no deadline;
  * returns a JSON-safe summary (counts, review item ids, deferred work) that becomes the workflow step's output.

    sync_universe        Nasdaq Trader (nasdaqlisted + otherlisted) → matcher → delisting check
    sync_sec_tickers     SEC company tickers → matcher (CIK + exchange)
    sync_openfigi_new    OpenFIGI for listed securities with no FIGI yet (not asked in the last 30 days)
    sync_13f             the 13(f) list: a NEW quarter is loaded once; pending CUSIPs mapped through OpenFIGI
    sync_attributes      FinanceDatabase sector / industry / country / fund category (never creates)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from services import security_master_load as M
from services import security_master_symbols as SYM
from services.security_data.providers import finance_database as FD
from services.security_data.providers import nasdaq_trader as NT
from services.security_data.providers import openfigi as OF
from services.security_data.providers import sec_13f_list as F13
from services.security_data.providers import sec_tickers as SEC
from services.security_data.providers.reference_common import BudgetExhausted, CallBudget, SourceRecord

LIVE = "valid_to IS NULL AND system_to IS NULL"
DEFAULT_BATCH = 1000


@dataclass
class SyncContext:
    pool: Any
    transport: Any
    run_id: Any = None
    today: date | None = None
    batch_size: int = DEFAULT_BATCH
    deadline_s: float | None = None
    log: Callable[[str], None] = lambda _m: None
    environ: dict | None = None
    checkpoint: Any = None            # scripts/secmaster_initial_load.Checkpoints (resume)
    universe_scope_prefix: str | None = None   # verify only: the delisting check sees fixture tickers only
    throttle: bool = True             # OpenFIGI pacing (the verify's fixture transport needs none)
    _t0: float = field(default_factory=time.monotonic)

    def day(self) -> date:
        return self.today or datetime.now(timezone.utc).date()

    def out_of_time(self) -> bool:
        return self.deadline_s is not None and time.monotonic() - self._t0 > self.deadline_s


async def _batches(ctx: SyncContext, source: str, matcher: M.Matcher, records_aiter, total: M.BatchResult,
                   skipped_batches: list) -> None:
    """Feed the matcher ``batch_size`` records at a time. A checkpointed batch (resume) is skipped."""
    buf: list[SourceRecord] = []
    n = 0

    async def flush():
        nonlocal buf, n
        if not buf:
            return
        if ctx.checkpoint is not None and await ctx.checkpoint.is_done(source, n):
            skipped_batches.append(n)
        else:
            res = await matcher.process(ctx.pool, buf)
            total.merge(res)
            if ctx.checkpoint is not None:
                await ctx.checkpoint.mark(source, n, res.summary())
            ctx.log(f"[{source}] batch {n}: {len(buf)} records — new {res.new}, matched "
                    f"{sum(res.matched.values())}, conflicts {res.conflicts}, unmapped {res.unmapped}"
                    f"{', deferred ' + str(res.deferred) if res.deferred else ''}")
        n += 1
        buf = []

    async for rec in records_aiter:
        buf.append(rec)
        if len(buf) >= ctx.batch_size:
            await flush()
            if ctx.out_of_time():
                raise _OutOfTime()
    await flush()


class _OutOfTime(Exception):
    pass


# ── Nasdaq Trader: the listed universe ──────────────────────────────────────
async def sync_universe(ctx: SyncContext, *, max_new: int | None = None, delisting: bool = True) -> dict:
    budget = await CallBudget(ctx.pool, NT.KEY, run_id=ctx.run_id, today=ctx.day()).load()
    matcher = M.Matcher(NT.KEY, as_of=ctx.day(), run_id=ctx.run_id, max_new=max_new)
    total = M.BatchResult()
    stats_box: dict = {}
    skipped: list = []
    async with ctx.pool.acquire() as conn:
        prev = await M.previous_file(conn, NT.KEY, NT.FILE_KEY)

    async def records():
        async for kind, item in NT.fetch_universe(ctx.transport, as_of=ctx.day(), budget=budget):
            if kind == "record":
                yield item
            else:
                stats_box["stats"] = item

    timed_out = False
    try:
        await _batches(ctx, NT.KEY, matcher, records(), total, skipped)
    except _OutOfTime:
        timed_out = True
    except Exception as exc:
        await budget.flush(ok=False, error=str(exc))
        raise
    await budget.flush()
    stats = stats_box.get("stats")
    out = {"source": NT.KEY, **total.summary(), "calls": budget.used, "timed_out": timed_out,
           "skipped_batches": len(skipped)}
    if stats is None:
        out["delisting"] = {"skipped": "the files were not read to the end"}
        return out
    file_dt = stats.as_of or datetime.now(timezone.utc)
    file_day = file_dt.date() if isinstance(file_dt, datetime) else file_dt
    out.update({"rows": stats.rows, "kept": stats.kept, "excluded": stats.excluded, "file_as_of": file_dt.isoformat()
                if isinstance(file_dt, datetime) else str(file_dt), "sha256": stats.sha256})
    complete = not timed_out and not total.deferred and not skipped
    if delisting and complete:
        ab = await M.mark_absent(ctx.pool, NT.KEY, total.seen, file_as_of=file_day,
                                 previous_as_of=(prev["as_of"].date() if prev else None), run_id=ctx.run_id,
                                 scope_ticker_prefix=ctx.universe_scope_prefix)
        out["delisting"] = ab.summary()
    else:
        out["delisting"] = {"skipped": "not every record of the file was processed this run" if not complete
                            else "not run (one-time load establishes the universe)"}
    await M.record_file(ctx.pool, provider_key=NT.KEY, file_key=NT.FILE_KEY, as_of=file_dt, sha256=stats.sha256,
                        row_count=stats.rows, status="complete" if complete else "partial",
                        summary={k: v for k, v in out.items() if k != "review_item_ids"}, run_id=ctx.run_id)
    return out


# ── SEC company tickers ─────────────────────────────────────────────────────
async def sync_sec_tickers(ctx: SyncContext, *, max_new: int | None = None) -> dict:
    budget = await CallBudget(ctx.pool, SEC.KEY, run_id=ctx.run_id, today=ctx.day()).load()
    try:
        recs, stats = await SEC.fetch(ctx.transport, as_of=ctx.day(), budget=budget)
    except Exception as exc:
        await budget.flush(ok=False, error=str(exc))
        raise
    await budget.flush()
    matcher = M.Matcher(SEC.KEY, as_of=ctx.day(), run_id=ctx.run_id, max_new=max_new)
    total = M.BatchResult()
    skipped: list = []

    async def gen():
        for r in recs:
            yield r

    timed_out = False
    try:
        await _batches(ctx, SEC.KEY, matcher, gen(), total, skipped)
    except _OutOfTime:
        timed_out = True
    complete = not timed_out and not total.deferred and not skipped
    out = {"source": SEC.KEY, **total.summary(), "calls": budget.used, "rows": stats.rows, "kept": stats.kept,
           "excluded": stats.excluded, "sha256": stats.sha256, "timed_out": timed_out}
    await M.record_file(ctx.pool, provider_key=SEC.KEY, file_key=SEC.FILE_KEY, as_of=datetime.now(timezone.utc),
                        sha256=stats.sha256, row_count=stats.rows, status="complete" if complete else "partial",
                        summary={k: v for k, v in out.items() if k != "review_item_ids"}, run_id=ctx.run_id)
    return out


# ── OpenFIGI ────────────────────────────────────────────────────────────────
async def _figi_candidates(conn, limit: int) -> list[str]:
    """Canonical tickers of listed securities that have a ticker and no FIGI yet."""
    rows = await conn.fetch(
        "SELECT DISTINCT t.id_value FROM portfolio.securities_global_identifiers t "
        "JOIN portfolio.security_reference_attributes a ON a.global_security_id = t.global_security_id "
        "AND a.field = 'listing_status' AND a.value = 'listed' AND a.valid_to IS NULL AND a.system_to IS NULL "
        "WHERE t.id_type = 'ticker' AND t.valid_to IS NULL AND t.system_to IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM portfolio.securities_global_identifiers f WHERE f.global_security_id = t.global_security_id "
        "AND f.id_type = 'figi' AND f.valid_to IS NULL AND f.system_to IS NULL) ORDER BY 1 LIMIT $1", int(limit))
    return [r["id_value"] for r in rows]


async def sync_openfigi_new(ctx: SyncContext, *, max_requests: int = 40, only_tickers: list[str] | None = None) -> dict:
    """OpenFIGI for NEW identifiers only, within budget: listed tickers with no FIGI, not asked in 30 days."""
    key = OF.api_key(ctx.environ)
    lim = OF.limits(key)
    budget = await CallBudget(ctx.pool, OF.KEY, run_id=ctx.run_id, today=ctx.day()).load()
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            await M._platform(conn)
            cands = only_tickers if only_tickers is not None else await _figi_candidates(
                conn, lim["jobs"] * max_requests * 3)
            jobs_by_ticker = {t: OF.ticker_job(t) for t in cands}
            sendable = [t for t, j in jobs_by_ticker.items() if j is not None]
            due = await OF.due(conn, "TICKER", [jobs_by_ticker[t]["idValue"] for t in sendable])
    due_set = set(due)
    jobs = [jobs_by_ticker[t] for t in sendable if jobs_by_ticker[t]["idValue"] in due_set]
    jobs = jobs[: lim["jobs"] * max_requests]
    exhausted = False
    try:
        answers, reqs = await OF.map_jobs(ctx.transport, jobs, key=key, budget=budget, max_requests=max_requests,
                                          throttle=ctx.throttle)
    except BudgetExhausted:
        answers, reqs, exhausted = [], 0, True
    except Exception as exc:
        await budget.flush(ok=False, error=str(exc))
        raise
    await budget.flush()
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            await M._platform(conn)
            await OF.record(conn, answers, run_id=ctx.run_id)
    recs = []
    for a in answers:
        r = OF.to_record(a, as_of=ctx.day())
        if r is not None:
            r.attrs = {"exchange": "US"}
            recs.append(r)
    total = M.BatchResult()
    if recs:
        matcher = M.Matcher(OF.KEY, as_of=ctx.day(), run_id=ctx.run_id, create_new=False)
        for i in range(0, len(recs), ctx.batch_size):
            total.merge(await matcher.process(ctx.pool, recs[i:i + ctx.batch_size]))
    return {"source": OF.KEY, "asked": len(answers), "hits": sum(1 for a in answers if a.hit),
            "misses": sum(1 for a in answers if not a.hit), "requests": reqs, "calls": budget.used,
            "not_sendable": len(cands) - len(sendable), "not_due_30d": len(sendable) - len(due),
            "deferred": max(0, len(due) - len(jobs)) + (len(jobs) - len(answers)),
            "budget_exhausted": exhausted, "keyed": bool(key), **{f"match_{k}": v for k, v in
                                                                   total.summary().items() if k != "review_item_ids"},
            "review_item_ids": total.summary()["review_item_ids"]}


# ── the 13(f) list ──────────────────────────────────────────────────────────
WAITING = "not mapped — waiting for OpenFIGI (daily budget; the next run continues)"


async def quarter_loaded(conn, quarter: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM portfolio.security_master_source_files WHERE provider_key = $1 AND file_key = $2 AND "
        "status = 'complete' AND " + LIVE, F13.KEY, quarter))


async def load_13f_rows(ctx: SyncContext, quarter: str, rows: list, stats, *, url: str) -> dict:
    """Insert the quarter's rows (one live row per CUSIP; an existing live row is left alone), map CUSIPs already
    in the master at once, flag DELETED rows that are in the master for review. Returns counts."""
    counts = {"rows": len(rows), "options": 0, "added": 0, "deleted": 0, "mapped_from_master": 0,
              "review_items": []}
    for i in range(0, len(rows), ctx.batch_size):
        chunk = rows[i:i + ctx.batch_size]
        async with ctx.pool.acquire() as conn:
            async with conn.transaction():
                await M._platform(conn)
                cusips = [r.cusip for r in chunk if not r.is_option]
                have = {r["id_value"]: r["gsid"] for r in await conn.fetch(
                    "SELECT i.id_value, COALESCE(s.canonical_id, s.id) AS gsid FROM portfolio.securities_global_"
                    "identifiers i JOIN portfolio.securities_global s ON s.id = i.global_security_id "
                    "WHERE i.id_type = 'cusip' AND i.id_value = ANY($1::text[]) AND i.valid_to IS NULL "
                    "AND i.system_to IS NULL", cusips)} if cusips else {}
                values = []
                for r in chunk:
                    if r.is_option:
                        counts["options"] += 1
                        status, note, gsid = "option_skipped", "option (CALL / PUT) — not loaded as a security", None
                    elif r.cusip in have:
                        status, note, gsid = "mapped", "already in the master by CUSIP", have[r.cusip]
                        counts["mapped_from_master"] += 1
                    elif r.status == "deleted":
                        status, note, gsid = ("not_mapped", "not mapped — deleted from the 13(f) list this quarter",
                                              None)
                    else:
                        status, note, gsid = "pending", WAITING, None
                    counts[r.status] = counts.get(r.status, 0) + (1 if r.status in ("added", "deleted") else 0)
                    values.append((quarter, r.cusip, r.has_listed_options, r.issuer[:200], r.description[:200],
                                   r.status, r.is_option, status, note, gsid))
                await conn.executemany(
                    "INSERT INTO portfolio.sec_13f_list (quarter, cusip, has_listed_options, issuer, description, "
                    "status, is_option, map_status, map_note, global_security_id) VALUES ($1, $2, $3, $4, $5, $6, $7, "
                    "$8, $9, $10::uuid) ON CONFLICT (quarter, cusip) WHERE valid_to IS NULL AND system_to IS NULL "
                    "DO NOTHING", values)
                for r in chunk:
                    if r.status == "deleted" and not r.is_option and r.cusip in have:
                        rid = await M.insert_review(conn, kind="13f_deleted", subject_key=f"cusip:{r.cusip}",
                                                    provider_key=F13.KEY, field="13f_status",
                                                    incoming_value=f"deleted {quarter}", global_security_id=have[r.cusip],
                                                    detail={"issuer": r.issuer, "description": r.description,
                                                            "note": "a DELETED row retires nothing by itself"},
                                                    run_id=ctx.run_id)
                        if rid is not None:
                            counts["review_items"].append(str(rid))
    await M.record_file(ctx.pool, provider_key=F13.KEY, file_key=quarter, as_of=datetime.now(timezone.utc),
                        sha256=stats.sha256, row_count=stats.rows, status="complete",
                        summary={"url": url, **{k: v for k, v in counts.items() if k != "review_items"},
                                 "excluded": stats.excluded}, run_id=ctx.run_id)
    return counts


async def map_13f_pending(ctx: SyncContext, quarter: str, *, max_requests: int = 40, limit: int | None = None) -> dict:
    """Pending (non-option) CUSIPs of ``quarter`` → OpenFIGI (not asked in 30 days) → matcher (provider
    sec_13f_list: may create) → each row restated mapped / not mapped with its reason."""
    key = OF.api_key(ctx.environ)
    lim = OF.limits(key)
    cap = limit if limit is not None else lim["jobs"] * max_requests
    budget = await CallBudget(ctx.pool, OF.KEY, run_id=ctx.run_id, today=ctx.day()).load()
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            await M._platform(conn)
            pend = await conn.fetch(
                "SELECT id, cusip, issuer, description, has_listed_options, status, is_option FROM portfolio.sec_13f_list "
                "WHERE quarter = $1 AND map_status = 'pending' AND " + LIVE + " ORDER BY cusip LIMIT $2", quarter,
                int(cap) * 3)
            due = set(await OF.due(conn, "ID_CUSIP", [r["cusip"] for r in pend]))
            recent = {r["id_value"]: r for r in await conn.fetch(
                "SELECT DISTINCT ON (id_value) id_value, ok, hits, result, warning, mapped_at FROM "
                "portfolio.openfigi_mappings WHERE id_type = 'ID_CUSIP' AND id_value = ANY($1::text[]) AND " + LIVE +
                " ORDER BY id_value, mapped_at DESC", [r["cusip"] for r in pend if r["cusip"] not in due])}
    to_ask = [r for r in pend if r["cusip"] in due][:cap]
    exhausted = False
    try:
        answers, reqs = await OF.map_jobs(ctx.transport, [OF.cusip_job(r["cusip"]) for r in to_ask], key=key,
                                          budget=budget, max_requests=max_requests, throttle=ctx.throttle)
    except BudgetExhausted:
        answers, reqs, exhausted = [], 0, True
    except Exception as exc:
        await budget.flush(ok=False, error=str(exc))
        raise
    await budget.flush()
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            await M._platform(conn)
            await OF.record(conn, answers, run_id=ctx.run_id)
    by_cusip = {a.job["idValue"]: a for a in answers}
    # a CUSIP answered in the last 30 days (by an earlier run) is decided from that answer, without asking again
    for c, r in recent.items():
        if c not in by_cusip:
            hit = r["result"]
            if isinstance(hit, str):
                hit = json.loads(hit)
            by_cusip[c] = OF.Answer(OF.cusip_job(c), hit, r["hits"], r["warning"], r["ok"])
    rows = {r["cusip"]: r for r in pend}
    recs = []
    for c, a in by_cusip.items():
        row = rows.get(c)
        if row is None or a.hit is None:
            continue
        rec = OF.to_record(a, cusip=c, as_of=ctx.day())
        rec.provider_key = F13.KEY
        rec.name = rec.name or row["issuer"]
        rec.attrs = {"exchange": "US"}
        recs.append(rec)
    total = M.BatchResult()
    if recs:
        matcher = M.Matcher(F13.KEY, as_of=ctx.day(), run_id=ctx.run_id)
        for i in range(0, len(recs), ctx.batch_size):
            total.merge(await matcher.process(ctx.pool, recs[i:i + ctx.batch_size]))
    # restate each decided row (valid axis): mapped / not mapped with its reason
    close, inserts = [], []
    reasons: dict[str, int] = {}
    for c, a in by_cusip.items():
        row = rows.get(c)
        if row is None:
            continue
        if a.hit is None:
            st, note, gsid = "not_mapped", f"not mapped — OpenFIGI found no identifier ({a.warning or 'no answer'})", None
        else:
            outcome = total.by_record.get(c)
            if outcome and outcome[0] in ("matched", "new"):
                st, note, gsid = "mapped", f"mapped through OpenFIGI ({outcome[2]})", outcome[1]
            elif outcome and outcome[0] == "conflict":
                st, note, gsid = "not_mapped", "not mapped — conflicts with an existing security (review item)", None
            elif outcome and outcome[0] == "deferred":
                continue
            else:
                st, note, gsid = "not_mapped", f"not mapped — {outcome[2] if outcome else 'no match'}", None
        if st == "not_mapped":
            reasons[note] = reasons.get(note, 0) + 1
        close.append(row["id"])
        inserts.append((quarter, c, row["has_listed_options"], row["issuer"], row["description"], row["status"],
                        row["is_option"], st, note, gsid))
    if close:
        async with ctx.pool.acquire() as conn:
            async with conn.transaction():
                await M._platform(conn)
                await conn.execute("UPDATE portfolio.sec_13f_list SET valid_to = now() WHERE id = ANY($1::uuid[]) "
                                   "AND valid_to IS NULL", close)
                await conn.executemany(
                    "INSERT INTO portfolio.sec_13f_list (quarter, cusip, has_listed_options, issuer, description, "
                    "status, is_option, map_status, map_note, global_security_id) VALUES ($1, $2, $3, $4, $5, $6, "
                    "$7, $8, $9, $10::uuid)", inserts)
    left = len(pend) - len(close)
    return {"quarter": quarter, "asked": len(answers), "requests": reqs, "calls": budget.used,
            "decided": len(close), "mapped": sum(1 for x in inserts if x[7] == "mapped"),
            "not_mapped": sum(1 for x in inserts if x[7] == "not_mapped"), "not_mapped_reasons": reasons,
            "still_pending": max(0, left), "budget_exhausted": exhausted, "keyed": bool(key),
            **{f"match_{k}": v for k, v in total.summary().items() if k != "review_item_ids"},
            "review_item_ids": total.summary()["review_item_ids"]}


async def sync_13f(ctx: SyncContext, *, map_requests: int = 40, load_only: bool = False) -> dict:
    """Daily: is a newer quarter published? If so, load it ONCE. Then map pending CUSIPs within budget."""
    budget = await CallBudget(ctx.pool, F13.KEY, run_id=ctx.run_id, today=ctx.day()).load()
    try:
        latest = await F13.latest_quarter(ctx.transport, budget=budget)
        if latest is None:
            await budget.flush(ok=False, error="no 13(f) list link found on the SEC page")
            raise RuntimeError("the SEC 13(f) page lists no quarterly text file — the page layout may have changed")
        quarter, url = latest
        async with ctx.pool.acquire() as conn:
            loaded = await quarter_loaded(conn, quarter)
        out: dict = {"source": F13.KEY, "quarter": quarter, "url": url, "new_quarter": not loaded}
        if not loaded:
            rows, stats = await F13.fetch_quarter(ctx.transport, url, budget=budget)
            if not rows:
                raise RuntimeError(f"the 13(f) file for {quarter} parsed to zero rows — not recorded")
            loaded_counts = await load_13f_rows(ctx, quarter, rows, stats, url=url)
            out["load"] = {k: v for k, v in loaded_counts.items() if k != "review_items"}
            out["load"]["excluded"] = stats.excluded
            out["review_item_ids"] = loaded_counts["review_items"]
        else:
            out["load"] = {"skipped": f"{quarter} is already loaded"}
    except Exception as exc:
        await budget.flush(ok=False, error=str(exc))
        raise
    await budget.flush()
    out["calls"] = budget.used
    if not load_only:
        out["mapping"] = await map_13f_pending(ctx, quarter, max_requests=map_requests)
        out.setdefault("review_item_ids", [])
        out["review_item_ids"] = out["review_item_ids"] + out["mapping"].pop("review_item_ids", [])
    return out


# ── FinanceDatabase ─────────────────────────────────────────────────────────
async def sync_attributes(ctx: SyncContext, *, kinds=("equities", "etfs"), equity_files=FD.US_EQUITY_FILES,
                          etf_files=FD.US_ETF_FILES) -> dict:
    budget = await CallBudget(ctx.pool, FD.KEY, run_id=ctx.run_id, today=ctx.day()).load()
    matcher = M.Matcher(FD.KEY, as_of=ctx.day(), run_id=ctx.run_id, create_new=False)
    total = M.BatchResult()
    stats_box: dict = {}
    skipped: list = []

    async def records():
        async for kind, item in FD.fetch(ctx.transport, kinds=kinds, equity_files=equity_files, etf_files=etf_files,
                                         as_of=ctx.day(), budget=budget):
            if kind == "record":
                yield item
            else:
                stats_box["stats"] = item

    timed_out = False
    try:
        await _batches(ctx, FD.KEY, matcher, records(), total, skipped)
    except _OutOfTime:
        timed_out = True
    except Exception as exc:
        await budget.flush(ok=False, error=str(exc))
        raise
    await budget.flush()
    st = stats_box.get("stats")
    out = {"source": FD.KEY, **total.summary(), "calls": budget.used, "timed_out": timed_out,
           "rows": st.rows if st else None, "excluded": st.excluded if st else None}
    if st is not None:
        await M.record_file(ctx.pool, provider_key=FD.KEY, file_key="us_equities+etfs",
                            as_of=datetime.now(timezone.utc), sha256=st.sha256, row_count=st.rows,
                            status="partial" if timed_out or skipped else "complete",
                            summary={k: v for k, v in out.items() if k != "review_item_ids"}, run_id=ctx.run_id)
    return out


# ── the job history (portfolio.security_data_runs, insert-only) ──────────────
async def record_run(pool, *, job: str, trigger: str, status: str, started_at: datetime, summary: dict,
                     run_id=None) -> uuid.UUID:
    rid = run_id or uuid.uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await M._platform(conn)
            await conn.execute(
                "INSERT INTO portfolio.security_data_runs (id, run_trigger, status, started_at, finished_at, summary) "
                "VALUES ($1::uuid, $2, $3, $4, now(), $5::jsonb)", rid, trigger, status, started_at,
                json.dumps({"job": job, **summary}, default=str))
    return rid
