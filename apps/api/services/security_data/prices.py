"""PRICES1 — end-of-day prices INTO the layer, through the receptor contract (providers/eod.py). Vendor-neutral: no
vendor is named here; every adapter is an ``EodPriceProvider`` and is asked only through ``fetch_eod``.

    coverage_set(conn)           WHO needs prices (Discovery 1c): every security held in a live position, in a live
                                 model version, a replica registry entry or a live replica snapshot's constituents,
                                 an option package / contract, a note replica, a substitution candidate, a backtest
                                 fund, plus the platform benchmarks. Test orgs are left out unless asked. Never
                                 "all 16k".
    ingest(pool, provider, items, start, end, ...)
                                 fetch → quality checks → store → coverage, batch by batch (streaming: one batch of
                                 symbols in memory at a time).
    backfill(...)                the operator's history load (scripts/prices_backfill.py): --dry-run estimates,
                                 --resume checkpoints, a run row in the job history.
    nightly(pool, ...)           the platform job "Security data — prices": gap fill for the coverage set's last few
                                 days + full history for NEW members, within budget and a deadline, resuming next
                                 night. A source failure RAISES → the engine holds the run and alerts a Super Admin.

QUALITY (never silently stored): a zero / negative close → 'invalid_value' event, not stored (the store's CHECK
refuses it too); a currency other than the security's → 'currency_mismatch' event, nothing stored for it; a spike
(|move| > outlier_multiple × median |move|) → 'outlier' event AND the bar is stored flagged 'outlier'; a run of more
than gap_weekdays weekdays without a bar → 'gap' event; the latest bar older than the eod_price stale limit →
'stale' event. Every event is a platform data_quality_events row (org NULL) for the security.

COVERAGE: has_series when the store holds a live series for the security (any provider) after the run — reason
"<n> closes <first>..<last> from <provider>"; no_public_source when this provider has none AND the store holds none —
reason = the source's own answer ("<source> has no data for ZZZ: …" / "the master holds no ticker for it"); otherwise
the flag is left alone. A transport failure never changes coverage."""

from __future__ import annotations

import json
import math
import pathlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from services.security_data import quality as Q
from services.security_data import registry as R
from services.security_data import settings as S
from services.security_data import writes as W
from services.security_data.providers.eod import EodPriceProvider, EodSourceError

LIVE = "valid_to IS NULL AND system_to IS NULL"
DATA_TYPE = "eod_price"
#: the platform's benchmarks (no benchmark table exists — Discovery 1c): broad US equity, US bonds, world equity
BENCHMARK_TICKERS = ("SPY", "AGG", "ACWI")
DEFAULT_YEARS = 5
GAP_FILL_DAYS = 10          # the nightly re-reads this many calendar days for every covered security
NIGHTLY_MAX_NEW = 200       # new coverage-set members given full history per night (the rest next night)
NIGHTLY_DEADLINE_S = 180.0  # inside the 200 s step deadline (security_master_jobs.STEP_DEADLINE_S)
BATCH = 25                  # securities per batch (one transaction each; streaming)
CONTEXT_POINTS = 40         # stored closes read before a batch, so a spike / gap at its start is judged too

#: verify only: provider key → zero-arg factory of a RECORDED transport. Production leaves it empty and each
#: adapter builds its own real transport.
TRANSPORT_FACTORY: dict = {}


class PricesJobError(RuntimeError):
    pass


@dataclass
class Item:
    gsid: str
    identifiers: dict
    currency: str | None
    sources: list[str] = field(default_factory=list)
    coverage: str = "unknown"


@dataclass
class IngestOutcome:
    provider: str
    calls: int = 0
    securities: int = 0
    found: int = 0
    not_found: int = 0
    no_symbol: int = 0
    inserted: int = 0
    restated: int = 0
    unchanged: int = 0
    events: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    deferred: list = field(default_factory=list)      # gsids not reached (budget / deadline) — next run
    not_found_ids: list = field(default_factory=list)
    done_ids: list = field(default_factory=list)

    def add_event(self, kind: str) -> None:
        self.events[kind] = self.events.get(kind, 0) + 1

    def summary(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k not in ("not_found_ids", "done_ids")} | {
            "deferred": len(self.deferred)}


# ── the coverage set (Discovery 1c) ─────────────────────────────────────────
COVERAGE_SQL = f"""
WITH pos AS (
  SELECT DISTINCT a.global_security_id AS g, 'position' AS src FROM portfolio.positions p
    JOIN portfolio.assets a ON a.id = p.asset_id AND a.valid_to IS NULL AND a.system_to IS NULL
    JOIN public.organizations o ON o.id = p.org_id
   WHERE p.{LIVE.replace(' AND ', ' AND p.')} AND a.global_security_id IS NOT NULL AND ($1 OR NOT o.is_test)
), mdl AS (
  SELECT DISTINCT mvp.global_security_id, 'model' FROM portfolio.model_version_positions mvp
    JOIN public.organizations o ON o.id = mvp.org_id
   WHERE mvp.valid_to IS NULL AND mvp.system_to IS NULL AND mvp.global_security_id IS NOT NULL AND ($1 OR NOT o.is_test)
), rep AS (
  SELECT DISTINCT r.global_security_id, 'replica' FROM portfolio.replica_securities r
    JOIN public.organizations o ON o.id = r.org_id
   WHERE r.status <> 'retired' AND r.valid_to IS NULL AND r.system_to IS NULL AND r.global_security_id IS NOT NULL
     AND ($1 OR NOT o.is_test)
), con AS (
  SELECT DISTINCT h.constituent_global_security_id, 'replica_constituent' FROM portfolio.replica_holdings_rows h
    JOIN portfolio.replica_holdings_snapshots s ON s.id = h.snapshot_id AND s.status = 'live' AND s.system_to IS NULL
   WHERE h.constituent_global_security_id IS NOT NULL
     AND EXISTS (SELECT 1 FROM portfolio.replica_securities r JOIN public.organizations o ON o.id = r.org_id
                  WHERE r.global_security_id = s.fund_global_security_id AND r.status <> 'retired'
                    AND r.valid_to IS NULL AND r.system_to IS NULL AND ($1 OR NOT o.is_test))
), opt AS (
  SELECT DISTINCT x.g, 'option_underlying' FROM (
    SELECT underlying_global_security_id AS g, org_id FROM portfolio.option_packages WHERE {LIVE}
    UNION ALL SELECT underlying_global_security_id, org_id FROM portfolio.option_contracts WHERE {LIVE}
    UNION ALL SELECT global_security_id, org_id FROM portfolio.option_contracts WHERE {LIVE}) x
    JOIN public.organizations o ON o.id = x.org_id WHERE x.g IS NOT NULL AND ($1 OR NOT o.is_test)
), bench AS (
  SELECT DISTINCT i.global_security_id, 'benchmark' FROM portfolio.securities_global_identifiers i
   WHERE i.id_type = 'ticker' AND i.id_value = ANY($2::text[]) AND i.{LIVE.replace(' AND ', ' AND i.')}
)
SELECT g::text AS gsid, array_agg(DISTINCT src ORDER BY src) AS sources FROM (
  SELECT * FROM pos UNION ALL SELECT * FROM mdl UNION ALL SELECT * FROM rep UNION ALL SELECT * FROM con
  UNION ALL SELECT * FROM opt UNION ALL SELECT * FROM bench) u(g, src)
 GROUP BY g ORDER BY g
"""
#: tables some environments may not have yet (rep11 note replicas, rep9 substitutions, rep7 backtests, track1 tracked
#: securities): read when present, each with its own org's is_test rule
OPTIONAL_SOURCES = (
    ("public.note_replicas", "note", "SELECT x.g::text FROM (SELECT global_security_id AS g, org_id FROM "
     "public.note_replicas WHERE valid_to IS NULL AND system_to IS NULL UNION ALL SELECT underlying_global_security_id, "
     "org_id FROM public.note_replicas WHERE valid_to IS NULL AND system_to IS NULL) x JOIN "
     "public.organizations o ON o.id = x.org_id WHERE x.g IS NOT NULL AND ($1 OR NOT o.is_test)"),
    ("public.substitution_set_candidates", "substitution", "SELECT c.replacement_security_id::text FROM "
     "public.substitution_set_candidates c JOIN public.organizations o ON o.id = c.org_id WHERE "
     "c.replacement_security_id IS NOT NULL AND c.valid_to IS NULL AND c.system_to IS NULL AND ($1 OR NOT o.is_test)"),
    # TRACK1 — a Super Admin tracked it on purpose (portfolio.tracked_securities). Platform-wide, not org data: the
    # test-org rule does not apply ($1 is read only to keep the one call shape).
    ("portfolio.tracked_securities", "tracked", "SELECT global_security_id::text FROM portfolio.tracked_securities "
     "WHERE status = 'tracked' AND valid_to IS NULL AND system_to IS NULL AND ($1 OR TRUE)"),
    ("public.backtests", "backtest", "SELECT b.fund_security_id::text FROM public.backtests b JOIN "
     "public.organizations o ON o.id = b.org_id WHERE b.fund_security_id IS NOT NULL AND b.valid_to IS NULL AND "
     "b.system_to IS NULL AND ($1 OR NOT o.is_test)"),
)


async def _platform(conn):
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def coverage_set(conn, *, include_test_orgs: bool = False, only_gsids: list[str] | None = None,
                       benchmarks=BENCHMARK_TICKERS) -> list[Item]:
    """The securities the platform needs prices for, with WHY (sources) and their identifiers. Platform scope inside
    its own transaction. ``only_gsids`` narrows it (a verify's fixtures; the operator's --gsid)."""
    async with conn.transaction():
        await _platform(conn)
        by: dict[str, set] = {}
        for r in await conn.fetch(COVERAGE_SQL, include_test_orgs, list(benchmarks)):
            by.setdefault(r["gsid"], set()).update(r["sources"])
        have = {r["t"] for r in await conn.fetch(
            "SELECT n.nspname || '.' || c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname || '.' || c.relname = ANY($1::text[])", [t for t, _, _ in OPTIONAL_SOURCES])}
        for t, src, sql in OPTIONAL_SOURCES:
            if t in have:
                for r in await conn.fetch(sql, include_test_orgs):
                    by.setdefault(r[0], set()).add(src)
        if only_gsids is not None:
            want = {str(g) for g in only_gsids}
            by = {g: s | {"requested"} for g, s in by.items() if g in want} | {
                g: {"requested"} for g in want if g not in by}
        if not by:
            return []
        rows = await conn.fetch(
            "SELECT s.id::text AS gsid, s.currency_code, s.price_coverage, i.id_type, i.id_value, i.is_primary FROM "
            "portfolio.securities_global s LEFT JOIN portfolio.securities_global_identifiers i ON "
            "i.global_security_id = s.id AND i.id_type IN ('ticker', 'figi', 'cusip') AND i.valid_to IS NULL AND "
            "i.system_to IS NULL WHERE s.id = ANY($1::uuid[]) AND s.valid_to IS NULL AND s.system_to IS NULL AND "
            "s.merged_into_id IS NULL ORDER BY s.id, i.is_primary DESC NULLS LAST, i.id_value", list(by))
    items: dict[str, Item] = {}
    for r in rows:
        it = items.setdefault(r["gsid"], Item(r["gsid"], {}, r["currency_code"], sorted(by[r["gsid"]]),
                                              r["price_coverage"]))
        if r["id_type"] and r["id_type"] not in it.identifiers:
            it.identifiers[r["id_type"]] = r["id_value"]
    return [items[g] for g in sorted(items)]


async def latest_dates(conn, provider_key: str, gsids: list[str]) -> dict[str, date]:
    """{gsid: latest live close date from this provider} (platform rows)."""
    if not gsids:
        return {}
    async with conn.transaction():
        await _platform(conn)
        rows = await conn.fetch(
            "SELECT global_security_id::text AS g, max(price_date) AS d FROM portfolio.securities_global_prices WHERE "
            "global_security_id = ANY($1::uuid[]) AND provider_key = $2 AND org_id IS NULL AND price_type = 'close' "
            f"AND {LIVE} GROUP BY 1", gsids, provider_key)
    return {r["g"]: r["d"] for r in rows}


# ── quality on one fetched series ───────────────────────────────────────────
def judge(bars, *, item_currency: str | None, series_currency: str | None, context: list, today: date,
          stale_limit: int | None, gap_weekdays: int, outlier_multiple: Decimal, outlier_min_moves: int):
    """Pure. (bars to store, {day: flags}, [(kind, detail)]) — the quality rules on one source's series."""
    events: list[tuple[str, dict]] = []
    cur = (series_currency or "").upper() or None
    if item_currency and cur and item_currency.upper() != cur:
        return [], {}, [("currency_mismatch", {"security_currency": item_currency, "source_currency": cur,
                                               "bars": len(bars)})]
    bad = [b for b in bars if b.close is None or b.close <= 0]
    if bad:
        events.append(("invalid_value", {"days": [b.day.isoformat() for b in bad][:50], "count": len(bad),
                                         "values": [format(b.close, "f") for b in bad][:50]}))
    keep = [b for b in bars if b.close is not None and b.close > 0]
    flags: dict = {}
    series = sorted({d_: v for d_, v in list(context) + [(b.day, b.close) for b in keep]}.items())
    new_days = {b.day for b in keep}
    for o in Q.outliers(series, outlier_multiple, outlier_min_moves):
        d_ = date.fromisoformat(o["date"])
        if d_ in new_days:
            flags.setdefault(d_, []).append("outlier")
            events.append(("outlier", o))
    for g in Q.gaps(series, gap_weekdays):
        if date.fromisoformat(g["before"]) in new_days:
            events.append(("gap", g))
    if keep:
        latest = max(new_days)
        age = (today - latest).days
        if S.is_stale(age, stale_limit):
            events.append(("stale", {"latest": latest.isoformat(), "age_days": age, "limit": stale_limit}))
    return keep, flags, events


# ── one ingest pass ─────────────────────────────────────────────────────────
@dataclass
class Limits:
    stale: int | None
    gap: int
    mult: Decimal
    min_moves: int


async def limits(conn) -> Limits:
    gap, _ = await S.setting(conn, None, "security_data.gap_weekdays")
    mult, _ = await S.setting(conn, None, "security_data.outlier_multiple")
    mm, _ = await S.setting(conn, None, "security_data.outlier_min_moves")
    return Limits(S.STALE_DAYS_DEFAULT["eod_price"], int(gap), mult, int(mm))


async def ingest(pool, provider: EodPriceProvider, items: list[Item], start: date, end: date, *, today: date,
                 run_id, budget_left: int | None = None, deadline: float | None = None, batch: int = BATCH,
                 on_batch=None, judge_coverage: bool = True) -> IngestOutcome:
    """Fetch [start, end] for ``items`` through ``provider``, batch by batch; each batch's quality events, writes and
    coverage in ONE platform transaction. Stops cleanly at the budget / deadline (the rest → ``deferred``). An
    EodSourceError stops this provider (recorded in ``errors``); what was stored before it stays. ``on_batch(done
    gsids)`` runs after each committed batch (the backfill's checkpoint)."""
    out = IngestOutcome(provider.key)
    key = provider.key
    async with pool.acquire() as conn:
        prow = (await R.providers(conn)).get(key) or {}
        lim = await limits(conn)
    lic = R.provider_licence(prow) if prow else provider.spec.licence.as_dict()
    per = max(1, int(provider.spec.max_symbols_per_call))
    i = 0
    while i < len(items):
        chunk = items[i:i + batch]
        if deadline is not None and time.monotonic() >= deadline:
            out.deferred += [it.gsid for it in items[i:]]
            break
        sym_of = {it.gsid: provider.symbol_for(it.identifiers) for it in chunk}
        askable = [it for it in chunk if sym_of[it.gsid]]
        need_calls = math.ceil(len({sym_of[it.gsid] for it in askable}) / per)
        if budget_left is not None and out.calls + need_calls > budget_left:
            can = max(0, (budget_left - out.calls) * per)
            if can == 0:
                out.deferred += [it.gsid for it in items[i:]]
                break
            chunk = chunk[:can]
            askable = [it for it in askable if it in chunk]
        series: dict = {}
        before = provider.calls
        try:
            syms = sorted({sym_of[it.gsid] for it in askable})
            for j in range(0, len(syms), per):
                series.update(await provider.fetch_eod(syms[j:j + per], start, end))
        except EodSourceError as exc:
            out.calls += provider.calls - before
            out.errors.append(provider.scrub(exc))
            out.deferred += [it.gsid for it in items[i:]]
            break
        except Exception as exc:  # noqa: BLE001 — a transport failure stops this provider, never the whole job
            out.calls += provider.calls - before
            out.errors.append(f"{key}: {type(exc).__name__}: {provider.scrub(exc)}")
            out.deferred += [it.gsid for it in items[i:]]
            break
        out.calls += provider.calls - before
        async with pool.acquire() as conn:
            ctx_rows = await _context(conn, key, [it.gsid for it in askable], start)
            async with conn.transaction():
                await _platform(conn)
                for it in chunk:
                    out.securities += 1
                    sym = sym_of[it.gsid]
                    if not sym:
                        out.no_symbol += 1
                        out.not_found_ids.append(it.gsid)
                        if judge_coverage:
                            await _coverage(conn, it, key, run_id, out,
                                            f"{key} asks by {provider.spec.keyed_by} and the master holds none for it")
                        continue
                    s = series.get(sym)
                    if s is None or not s.found:
                        out.not_found += 1
                        out.not_found_ids.append(it.gsid)
                        if judge_coverage:
                            await _coverage(conn, it, key, run_id, out,
                                            (s.reason if s else None) or f"{key} returned nothing for {sym}")
                        continue
                    out.found += 1
                    keep, flags, events = judge(s.bars, item_currency=it.currency, series_currency=s.currency,
                                                context=ctx_rows.get(it.gsid, []), today=today, stale_limit=lim.stale,
                                                gap_weekdays=lim.gap, outlier_multiple=lim.mult,
                                                outlier_min_moves=lim.min_moves)
                    for kind, detail in events:
                        await W.record_quality_event(conn, org_id=None, data_type=DATA_TYPE, subject_key=it.gsid,
                                                     gsid=it.gsid, event_date=today, kind=kind, provider_key=key,
                                                     detail={**detail, "symbol": sym}, run_id=run_id)
                        out.add_event(kind)
                    got = await W.upsert_eod_bars(conn, it.gsid, keep, provider_key=key, licence=lic,
                                                  currency=s.currency, flags=flags)
                    for k_, v_ in got.items():
                        setattr(out, k_, getattr(out, k_) + v_)
                    if judge_coverage:
                        await _coverage(conn, it, key, run_id, out, None)
                    out.done_ids.append(it.gsid)
        if on_batch is not None:
            await on_batch([it.gsid for it in chunk])
        i += len(chunk)
    return out


async def _context(conn, key, gsids, start) -> dict:
    """{gsid: [(date, close)]} — up to CONTEXT_POINTS stored closes before ``start`` (this provider)."""
    if not gsids:
        return {}
    async with conn.transaction():
        await _platform(conn)
        rows = await conn.fetch(
            "SELECT g, d, v FROM (SELECT global_security_id::text AS g, price_date AS d, price AS v, row_number() OVER "
            "(PARTITION BY global_security_id ORDER BY price_date DESC) AS rn FROM portfolio.securities_global_prices "
            "WHERE global_security_id = ANY($1::uuid[]) AND provider_key = $2 AND org_id IS NULL AND price_type = "
            f"'close' AND price_date < $3 AND {LIVE}) t WHERE rn <= $4", gsids, key, start, CONTEXT_POINTS)
    out: dict = {}
    for r in rows:
        out.setdefault(r["g"], []).append((r["d"], r["v"]))
    return out


async def _coverage(conn, it: Item, key: str, run_id, out: IngestOutcome, not_found_reason: str | None) -> None:
    """has_series when the store holds a live series (any provider); no_public_source when this source has none
    and the store holds none; the reason recorded either way."""
    st = await conn.fetchrow(
        "SELECT provider_key, count(*) AS n, min(price_date) AS a, max(price_date) AS b FROM "
        "portfolio.securities_global_prices WHERE global_security_id = $1::uuid AND org_id IS NULL AND price_type = "
        f"'close' AND {LIVE} GROUP BY provider_key ORDER BY count(*) DESC, provider_key LIMIT 1", it.gsid)
    if st is not None and int(st["n"]) > 0:
        res = await W.set_price_coverage(conn, it.gsid, status="has_series", provider_key=st["provider_key"],
                                         reason=f"{st['n']} daily closes {st['a'].isoformat()}..{st['b'].isoformat()} "
                                                f"from {st['provider_key']}", first_date=st["a"], last_date=st["b"],
                                         points=int(st["n"]), run_id=run_id)
        out.coverage["has_series"] = out.coverage.get("has_series", 0) + 1
    elif not_found_reason:
        res = await W.set_price_coverage(conn, it.gsid, status="no_public_source", provider_key=key,
                                         reason=not_found_reason, run_id=run_id)
        out.coverage["no_public_source"] = out.coverage.get("no_public_source", 0) + 1
    else:
        return
    if res == "changed":
        out.coverage["flag_changed"] = out.coverage.get("flag_changed", 0) + 1


# ── adapters with their transport ───────────────────────────────────────────
def fetcher(key: str, *, providers: dict | None = None) -> EodPriceProvider:
    """The adapter for ``key`` with a transport: the verify's recorded one (TRANSPORT_FACTORY) or the adapter's own
    real one. Refuses a key that is not an EOD adapter."""
    from services.security_data import layer as L

    mod = L.code_registry(providers).get(key)
    if not isinstance(mod, EodPriceProvider):
        raise PricesJobError(f"{key} is not an end-of-day price adapter")
    cls = type(mod)
    kw = {"environ": mod.environ}
    if key in TRANSPORT_FACTORY:
        kw.update(transport=TRANSPORT_FACTORY[key](), min_interval=0.0)
    else:
        kw["transport"] = mod.real_transport()
    if hasattr(mod, "base_url"):         # the template carries its own key / url
        return cls(mod.key, base_url=mod.base_url, licence=mod.spec.licence, **kw)
    return cls(**kw)


async def eod_rank(conn) -> list[str]:
    """The platform rank's EOD adapters that may FETCH: active registry row + an EodPriceProvider module."""
    from services.security_data import layer as L

    rows = await R.providers(conn)
    code = L.code_registry()
    return [k for k in await R.platform_rank(conn, DATA_TYPE)
            if isinstance(code.get(k), EodPriceProvider) and (rows.get(k) or {}).get("status") == "active"]


async def record_run(pool, *, job: str, trigger: str, status: str, started, summary: dict, run_id=None) -> uuid.UUID:
    rid = run_id or uuid.uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _platform(conn)
            await conn.execute("INSERT INTO portfolio.security_data_runs (id, run_trigger, status, started_at, "
                               "finished_at, summary) VALUES ($1::uuid, $2, $3, $4, now(), $5::jsonb)", rid, trigger,
                               status, started, json.dumps({"job": job, **summary}, default=str))
    return rid


async def _budget(pool, key: str, today: date) -> int | None:
    async with pool.acquire() as conn:
        row = (await R.providers(conn)).get(key)
        return None if row is None else await W.budget_left(conn, row, today)


# ── backfill (the operator's load) ──────────────────────────────────────────
class Checkpoint:
    """A JSON file: {key, provider, start, end, done: [gsid]}. Written atomically after every committed batch, so a
    killed run resumes at the next batch. A key mismatch (another provider / range) starts fresh."""

    def __init__(self, path: pathlib.Path, key: str):
        self.path, self.key, self.done = pathlib.Path(path), key, set()

    def load(self) -> set:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        self.done = set(doc.get("done") or []) if doc.get("key") == self.key else set()
        return self.done

    async def mark(self, gsids) -> None:
        self.done |= set(gsids)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": self.key, "done": sorted(self.done)}), encoding="utf-8")
        tmp.replace(self.path)


def estimate(provider: EodPriceProvider, n_items: int, budget: int | None) -> dict:
    per = max(1, provider.spec.max_symbols_per_call)
    calls = math.ceil(n_items / per)
    secs = calls * (provider.spec.min_interval_s + 0.4)
    return {"securities": n_items, "estimated_calls": calls, "estimated_seconds": round(secs),
            "estimated_minutes": round(secs / 60, 1), "daily_budget": budget,
            "days_at_budget": (math.ceil(calls / budget) if budget else None) if budget != 0 else None}


async def backfill(pool, provider: EodPriceProvider, items: list[Item], *, years: int = DEFAULT_YEARS,
                   today: date | None = None, dry_run: bool = False, checkpoint: Checkpoint | None = None,
                   batch: int = BATCH, progress=None, trigger: str = "manual", respect_budget: bool = True) -> dict:
    """The history load for ``items`` (the coverage set). Returns the summary (also the run row's summary)."""
    today = today or datetime.now(timezone.utc).date()
    start = date(today.year - years, today.month, min(today.day, 28))
    budget = await _budget(pool, provider.key, today) if respect_budget else None
    todo = list(items)
    skipped_done = 0
    if checkpoint is not None:
        done = checkpoint.load()
        skipped_done = sum(1 for it in todo if it.gsid in done)
        todo = [it for it in todo if it.gsid not in done]
    plan = {"provider": provider.key, "years": years, "start": start.isoformat(), "end": today.isoformat(),
            "coverage_set": len(items), "already_done": skipped_done, "to_fetch": len(todo),
            **estimate(provider, len(todo), budget)}
    if dry_run:
        return {"dry_run": True, **plan}
    run_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    seen = {"n": 0}

    async def on_batch(gsids):
        seen["n"] += len(gsids)
        if checkpoint is not None:
            await checkpoint.mark(gsids)
        if progress:
            progress(f"[{provider.key}] {skipped_done + seen['n']}/{len(items)} securities")

    out = await ingest(pool, provider, todo, start, today, today=today, run_id=run_id,
                       budget_left=budget, batch=batch, on_batch=on_batch)
    if out.calls:
        await W.record_calls(pool, provider.key, out.calls, data_type=DATA_TYPE, ok=not out.errors,
                             error="; ".join(out.errors)[:2000] or None, run_id=run_id, day=today)
    status = "failed" if out.errors and not out.found else ("partial" if out.errors or out.deferred else "success")
    summary = {**plan, **out.summary(), "run_id": str(run_id), "tracked_progress": await _tracked_progress(pool)}
    await record_run(pool, job="prices_backfill", trigger=trigger, status=status, started=started, summary=summary,
                     run_id=run_id)
    return {"dry_run": False, "status": status, **summary}


# ── the nightly job step ────────────────────────────────────────────────────
async def nightly(pool, *, today: date | None = None, run_id=None, include_test_orgs: bool = False,
                  only_gsids: list[str] | None = None, keys: list[str] | None = None, years: int = DEFAULT_YEARS,
                  gap_days: int = GAP_FILL_DAYS, max_new: int = NIGHTLY_MAX_NEW,
                  deadline_s: float = NIGHTLY_DEADLINE_S, providers: dict | None = None) -> dict:
    """Gap fill + new members' history, rank order with fallback: what the first adapter does not find goes to the
    next. Raises PricesJobError when any adapter's request failed (after storing what it could)."""
    today = today or datetime.now(timezone.utc).date()
    run_id = run_id or uuid.uuid4()
    deadline = time.monotonic() + deadline_s
    async with pool.acquire() as conn:
        items = await coverage_set(conn, include_test_orgs=include_test_orgs, only_gsids=only_gsids)
        order = keys if keys is not None else await eod_rank(conn)
    summary = {"coverage_set": len(items), "providers": {}, "today": today.isoformat(), "run_id": str(run_id)}
    errors: list[str] = []
    pending = [it for it in items if it.coverage != "no_public_source"]
    summary["skipped_no_public_source"] = len(items) - len(pending)
    for key in order:
        if not pending:
            break
        prov = fetcher(key, providers=providers)
        async with pool.acquire() as conn:
            last = await latest_dates(conn, key, [it.gsid for it in pending])
        # TRACK1: stalest first — with a deadline the old gsid order refreshed the SAME first securities every night
        old = sorted((it for it in pending if it.gsid in last), key=lambda it: (last[it.gsid], it.gsid))
        new = [it for it in pending if it.gsid not in last]
        new_now, new_later = new[:max_new], new[max_new:]
        budget = await _budget(pool, key, today)
        # TRACK1: while new members wait, the gap fill takes at most half of what is left of the deadline, so new
        # history is never starved by a coverage set larger than one night's capacity
        gap_deadline = (time.monotonic() + (deadline - time.monotonic()) / 2) if new_now else deadline
        res_old = await ingest(pool, prov, old, today - timedelta(days=gap_days), today, today=today, run_id=run_id,
                               budget_left=budget, deadline=gap_deadline) if old else IngestOutcome(key)
        left = None if budget is None else max(0, budget - res_old.calls)
        start = date(today.year - years, today.month, min(today.day, 28))
        res_new = await ingest(pool, prov, new_now, start, today, today=today, run_id=run_id, budget_left=left,
                               deadline=deadline) if new_now and not res_old.errors else IngestOutcome(key)
        calls = res_old.calls + res_new.calls
        if calls:
            await W.record_calls(pool, key, calls, data_type=DATA_TYPE, ok=not (res_old.errors or res_new.errors),
                                 error="; ".join(res_old.errors + res_new.errors)[:2000] or None, run_id=run_id,
                                 day=today)
        await _aclose(prov)
        summary["providers"][key] = {"gap_fill": res_old.summary(), "new_history": res_new.summary(),
                                     "new_deferred_to_next_night": len(new_later)}
        errors += res_old.errors + res_new.errors
        nf = set(res_old.not_found_ids) | set(res_new.not_found_ids)
        pending = [it for it in pending if it.gsid in nf]      # the next adapter tries only what this one lacked
    summary["errors"] = errors
    summary["tracked_progress"] = await _tracked_progress(pool)
    if errors:
        raise PricesJobError("end-of-day prices: " + "; ".join(errors[:5]))
    return summary


async def _tracked_progress(pool) -> dict | None:
    """TRACK1 — "1,240 of 2,700 loaded" for the job history (None where the table is not deployed)."""
    from services.security_data import tracking as TR

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _platform(conn)
            if not await conn.fetchval("SELECT to_regclass('portfolio.tracked_securities') IS NOT NULL"):
                return None
            return await TR.progress(conn)


async def _aclose(prov) -> None:
    t = getattr(prov.http, "transport", None)
    if t is not None and hasattr(t, "aclose"):
        try:
            await t.aclose()
        except Exception:  # noqa: BLE001
            pass
