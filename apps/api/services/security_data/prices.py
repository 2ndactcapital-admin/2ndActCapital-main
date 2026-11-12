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
                                 days + full history for NEW members, within budget and a deadline. PRICES2: one
                                 SLICE of the day's refresh (portfolio.price_refresh_days); prices_tick continues it
                                 every scheduler tick until every covered security is refreshed for the day or the
                                 daily budget is spent; priority + oldest-first (refresh_order). A source failure
                                 RAISES in the job step → the engine holds the run and alerts a Super Admin; the tick
                                 records it on the day row and never raises.

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
#: PRICES2 — the day's refresh resumes across scheduler ticks (portfolio.price_refresh_days, one row per scope + day)
DAY_SCOPE = "platform"      # the scheduled job's day rows (a verify uses its own scope)
DAY_LEASE_S = 600           # a slice's lease; a holder that died is picked up once it expires
SLICE_CHUNK = 100           # securities per planning chunk inside a slice (split into gap fill / new history)
FAILED_SLICES_STOP = 3      # consecutive failed slices → the day stops 'stopped_error' (no retry every 5 min forever)
STALE_DAY_ROWS = 3          # a 'running' day row older than this many days is closed 'carried_over' by the tick
#: refresh priority. The org uses first — held positions, then models / replicas, then the benchmarks, then any other
#: org use — refreshed EVERY day before anything else; then the tracked lists in this order; anything else last.
ORG_SOURCE_TIERS = {"position": 0, "model": 1, "replica": 1, "replica_constituent": 1, "benchmark": 2,
                    "option_underlying": 3, "note": 3, "substitution": 3, "backtest": 3}
LIST_PRIORITY = ("core_etfs", "fixed_income", "commodities", "key_benchmarks", "sp500", "russell2000")
LIST_TIER_BASE = 10         # list i → tier 10 + i; a tracked security in no ranked list → OTHER_TIER
OTHER_TIER = 99
DAY_STATUS_LABELS = {"running": "Running", "complete": "Complete — every covered security refreshed",
                     "stopped_budget": "Stopped — the daily call budget is spent (the rest goes first tomorrow)",
                     "stopped_error": "Stopped — the source failed", "carried_over": "Carried over to the next day"}
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
    tier: int = 99              # PRICES2 refresh priority (assign_tiers; lower = sooner)


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
                 on_batch=None, judge_coverage: bool = True, clock=time.monotonic) -> IngestOutcome:
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
        if deadline is not None and clock() >= deadline:
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


# ── PRICES2: priority + oldest-first order (pure) ───────────────────────────
def tier_of(sources, lists=(), list_priority=LIST_PRIORITY) -> int:
    """Pure. The refresh tier of one covered security: the best (lowest) of its org uses (ORG_SOURCE_TIERS) and the
    ranked lists it is tracked through (LIST_TIER_BASE + the list's place in ``list_priority``); else OTHER_TIER.
    'requested' / 'tracked' alone rank nothing."""
    t = [ORG_SOURCE_TIERS[s_] for s_ in (sources or ()) if s_ in ORG_SOURCE_TIERS]
    t += [LIST_TIER_BASE + list_priority.index(k) for k in (lists or ()) if k in list_priority]
    return min(t) if t else OTHER_TIER


def refresh_order(items: list[Item], last_refreshed: dict, latest_close: dict) -> list[Item]:
    """Pure. The order a day's refresh asks for securities:
      1. the org uses (tier < LIST_TIER_BASE) before everything tracked — refreshed every day first;
      2. oldest last refresh first (never refreshed = oldest) — what was not reached yesterday goes first today;
      3. tier (core ETFs → fixed income → commodities → key benchmarks → S&P 500 → Russell 2000 → other);
      4. the stalest stored close first (TRACK1), never-priced first; 5. gsid."""
    return sorted(items, key=lambda it: (0 if it.tier < LIST_TIER_BASE else 1, last_refreshed.get(it.gsid) or date.min,
                                         it.tier, latest_close.get(it.gsid) or date.min, it.gsid))


async def assign_tiers(conn, items: list[Item], *, list_priority=LIST_PRIORITY) -> None:
    """Set ``item.tier`` from its sources and the live lists it is tracked through (list members + the tracked row's
    own list_key). Platform scope, own transaction. Tables not deployed → sources only."""
    gsids = [it.gsid for it in items]
    lists: dict[str, set] = {}
    if gsids:
        async with conn.transaction():
            await _platform(conn)
            have = await conn.fetchval("SELECT to_regclass('portfolio.security_list_members') IS NOT NULL AND "
                                       "to_regclass('portfolio.tracked_securities') IS NOT NULL")
            if have:
                for r in await conn.fetch(
                        "SELECT t.global_security_id::text AS g, m.list_key FROM portfolio.tracked_securities t JOIN "
                        "portfolio.security_list_members m ON m.global_security_id = t.global_security_id AND "
                        "m.valid_to IS NULL AND m.system_to IS NULL WHERE t.status = 'tracked' AND t.valid_to IS NULL "
                        "AND t.system_to IS NULL AND t.global_security_id = ANY($1::uuid[]) UNION SELECT "
                        "global_security_id::text, list_key FROM portfolio.tracked_securities WHERE status = 'tracked' "
                        "AND list_key IS NOT NULL AND valid_to IS NULL AND system_to IS NULL AND global_security_id = "
                        "ANY($1::uuid[])", gsids):
                    lists.setdefault(r["g"], set()).add(r["list_key"])
    for it in items:
        it.tier = tier_of(it.sources, lists.get(it.gsid, ()), list_priority)


async def refresh_state(conn, gsids: list[str]) -> dict:
    """{gsid: last_refreshed_on} (portfolio.price_refresh_state; {} where it is not deployed)."""
    if not gsids:
        return {}
    async with conn.transaction():
        await _platform(conn)
        if not await conn.fetchval("SELECT to_regclass('portfolio.price_refresh_state') IS NOT NULL"):
            return {}
        rows = await conn.fetch("SELECT global_security_id::text AS g, last_refreshed_on AS d FROM "
                                "portfolio.price_refresh_state WHERE global_security_id = ANY($1::uuid[])", gsids)
    return {r["g"]: r["d"] for r in rows}


async def mark_refreshed(pool, gsids: list[str], *, day: date, outcome: str, provider_key: str | None,
                         day_id=None) -> int:
    """Record ``day`` as the last refresh of each security (one statement). Never moves a later day backwards."""
    if not gsids:
        return 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _platform(conn)
            if not await conn.fetchval("SELECT to_regclass('portfolio.price_refresh_state') IS NOT NULL"):
                return 0
            await conn.execute(
                "INSERT INTO portfolio.price_refresh_state (global_security_id, last_refreshed_on, last_outcome, "
                "last_provider, last_day_id, updated_at) SELECT g, $2, $3, $4, $5::uuid, now() FROM "
                "unnest($1::uuid[]) AS g ON CONFLICT (global_security_id) DO UPDATE SET last_refreshed_on = "
                "EXCLUDED.last_refreshed_on, last_outcome = EXCLUDED.last_outcome, last_provider = "
                "EXCLUDED.last_provider, last_day_id = EXCLUDED.last_day_id, updated_at = now() WHERE "
                "portfolio.price_refresh_state.last_refreshed_on <= EXCLUDED.last_refreshed_on",
                sorted(set(gsids)), day, outcome, provider_key, str(day_id) if day_id else None)
    return len(set(gsids))


def _merge(into: IngestOutcome, add: IngestOutcome) -> None:
    for k, v in add.__dict__.items():
        if k == "provider":
            continue
        cur = getattr(into, k)
        if isinstance(v, int):
            setattr(into, k, cur + v)
        elif isinstance(v, list):
            cur.extend(v)
        elif isinstance(v, dict):
            for kk, vv in v.items():
                cur[kk] = cur.get(kk, 0) + vv


# ── the day row (portfolio.price_refresh_days) ──────────────────────────────
def day_out(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("id", "workflow_run_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("started_at", "heartbeat_at", "finished_at", "lease_until"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    if d.get("price_day") is not None:
        d["price_day"] = d["price_day"].isoformat()
    for k in ("errors", "summary"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["status_label"] = DAY_STATUS_LABELS.get(d.get("status"), d.get("status"))
    d["progress_label"] = f"{int(d.get('refreshed') or 0):,} of {int(d.get('covered') or 0):,} refreshed"
    return d


async def open_day(pool, *, scope: str, day: date, workflow_run_id=None) -> dict:
    """Today's row for ``scope`` (created once — the unique (scope, price_day) index decides, never a read-then-write
    race). A still-running row of an EARLIER day is closed 'carried_over': what it did not reach goes first today."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _platform(conn)
            await conn.execute(
                "UPDATE portfolio.price_refresh_days SET status = 'carried_over', finished_at = now(), lease_until = "
                "NULL, stop_detail = 'a new day started before this one finished' WHERE scope = $1 AND status = "
                "'running' AND price_day < $2", scope, day)
            await conn.execute(
                "INSERT INTO portfolio.price_refresh_days (scope, price_day, workflow_run_id) VALUES ($1, $2, $3::uuid) "
                "ON CONFLICT (scope, price_day) DO NOTHING", scope, day, str(workflow_run_id) if workflow_run_id else None)
            row = await conn.fetchrow("SELECT * FROM portfolio.price_refresh_days WHERE scope = $1 AND price_day = $2",
                                      scope, day)
    return day_out(row)


async def claim_day(pool, day_id) -> dict | None:
    """The slice's lease: only a RUNNING row whose lease is free or expired — two ticks never refresh the same day at
    once."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _platform(conn)
            row = await conn.fetchrow(
                "UPDATE portfolio.price_refresh_days SET lease_until = now() + make_interval(secs => $2::integer), "
                "heartbeat_at = now() WHERE id = $1::uuid AND status = 'running' AND (lease_until IS NULL OR "
                "lease_until < now()) RETURNING *", str(day_id), DAY_LEASE_S)
    return day_out(row)


async def _close_slice(pool, day_id, **cols) -> dict | None:
    sets, args = [], [str(day_id)]
    for k, v in cols.items():
        args.append(json.dumps(v, default=str) if k in ("errors", "summary") else v)
        sets.append(f"{k} = ${len(args)}" + ("::jsonb" if k in ("errors", "summary") else ""))
    sets += ["lease_until = NULL", "heartbeat_at = now()"]
    if cols.get("status") and cols["status"] != "running":
        sets.append("finished_at = now()")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _platform(conn)
            row = await conn.fetchrow(f"UPDATE portfolio.price_refresh_days SET {', '.join(sets)} WHERE id = $1::uuid "
                                      "RETURNING *", *args)
    return day_out(row)


async def day_rows(conn, *, scope: str = DAY_SCOPE, limit: int = 10) -> list[dict]:
    """The latest day rows for ``scope`` (caller holds platform scope or reads under the SELECT-for-all policy)."""
    if not await conn.fetchval("SELECT to_regclass('portfolio.price_refresh_days') IS NOT NULL"):
        return []
    return [day_out(r) for r in await conn.fetch(
        "SELECT * FROM portfolio.price_refresh_days WHERE scope = $1 ORDER BY price_day DESC LIMIT $2", scope, limit)]


# ── the nightly job step ────────────────────────────────────────────────────
async def nightly(pool, *, today: date | None = None, run_id=None, include_test_orgs: bool = False,
                  only_gsids: list[str] | None = None, keys: list[str] | None = None, years: int = DEFAULT_YEARS,
                  gap_days: int = GAP_FILL_DAYS, max_new: int = NIGHTLY_MAX_NEW,
                  deadline_s: float = NIGHTLY_DEADLINE_S, providers: dict | None = None, scope: str | None = None,
                  list_priority=LIST_PRIORITY, raise_errors: bool = True, clock=time.monotonic) -> dict:
    """ONE slice of the day's refresh: gap fill + new members' history, in priority / oldest-first order, rank order
    with fallback (what the first adapter does not find goes to the next), inside ``deadline_s`` and each adapter's
    daily budget.

    DAY MODE (the scheduled job: ``only_gsids`` None, or an explicit ``scope``): the day's row in
    portfolio.price_refresh_days is opened (once) and leased; securities already refreshed for ``today`` are skipped;
    the slice's progress is recorded on the row, which ends 'complete' (every covered security refreshed today),
    'stopped_budget' (the first source's daily budget is spent — the rest goes first tomorrow) or 'stopped_error'
    (FAILED_SLICES_STOP failed slices in a row); otherwise it stays 'running' and the scheduler tick (prices_tick)
    continues it. A day row that another slice holds → nothing is done ('busy').

    ONE-OFF MODE (``only_gsids`` without a ``scope`` — the operator's --gsid, PRICES1 / TRACK1 verifies): no day row;
    every requested security is refreshed (PRICES1's behaviour).

    Raises PricesJobError when any adapter's request failed (after storing and recording what it could) unless
    ``raise_errors`` is False (the tick)."""
    today = today or datetime.now(timezone.utc).date()
    day_mode = scope is not None or only_gsids is None
    scope = scope or DAY_SCOPE
    deadline = clock() + deadline_s
    day = None
    wf_run = run_id
    if day_mode:
        day = await open_day(pool, scope=scope, day=today, workflow_run_id=run_id)
        if day["status"] != "running":
            return {"coverage_set": day["covered"], "providers": {}, "today": today.isoformat(), "run_id": day["id"],
                    "errors": [], "action": "day_finished", "day": day, "deferred": 0}
        claimed = await claim_day(pool, day["id"])
        if claimed is None:
            return {"coverage_set": day["covered"], "providers": {}, "today": today.isoformat(), "run_id": day["id"],
                    "errors": [], "action": "busy", "day": day, "deferred": 0}
        day = claimed
        run_id = uuid.UUID(day["id"])
    run_id = run_id or uuid.uuid4()
    try:
        summary = await _slice(pool, today=today, run_id=run_id, include_test_orgs=include_test_orgs,
                               only_gsids=only_gsids, keys=keys, years=years, gap_days=gap_days, max_new=max_new,
                               deadline=deadline, providers=providers, day=day, list_priority=list_priority,
                               clock=clock)
    except Exception as exc:  # noqa: BLE001 — recorded on the day row, then re-raised (the step holds) or returned
        if day is None:
            raise
        err = f"{type(exc).__name__}: {str(exc)[:500]}"
        fails = int(day["consecutive_failures"]) + 1
        await _close_slice(pool, day["id"], slices=int(day["slices"]) + 1, consecutive_failures=fails,
                           errors=(day.get("errors") or [])[-49:] + [{"at": datetime.now(timezone.utc).isoformat(),
                                                                       "error": err}],
                           status="stopped_error" if fails >= FAILED_SLICES_STOP else "running",
                           stop_detail=err if fails >= FAILED_SLICES_STOP else None)
        if raise_errors:
            raise
        return {"coverage_set": day["covered"], "providers": {}, "today": today.isoformat(), "run_id": day["id"],
                "errors": [err], "action": "failed", "day": None, "deferred": 0}
    summary["tracked_progress"] = await _tracked_progress(pool)
    if day is not None:
        summary["workflow_run_id"] = str(wf_run) if wf_run else None
        summary["day"] = await _record_slice(pool, day, summary, keys_order=summary.pop("_order"), today=today)
    else:
        summary.pop("_order", None)
        summary.pop("_refreshable_ids", None)
    errors = summary["errors"]
    if errors and raise_errors:
        raise PricesJobError("end-of-day prices: " + "; ".join(errors[:5]))
    return summary


async def _slice(pool, *, today, run_id, include_test_orgs, only_gsids, keys, years, gap_days, max_new, deadline,
                 providers, day, list_priority, clock) -> dict:
    async with pool.acquire() as conn:
        items = await coverage_set(conn, include_test_orgs=include_test_orgs, only_gsids=only_gsids)
        order = keys if keys is not None else await eod_rank(conn)
        state = await refresh_state(conn, [it.gsid for it in items])
    summary = {"coverage_set": len(items), "providers": {}, "today": today.isoformat(), "run_id": str(run_id),
               "_order": order}
    errors: list[str] = []
    pending = [it for it in items if it.coverage != "no_public_source"]
    summary["skipped_no_public_source"] = len(items) - len(pending)
    summary["refreshable"] = len(pending)
    summary["_refreshable_ids"] = [it.gsid for it in pending]
    if day is not None:
        done_today = [it for it in pending if state.get(it.gsid) == today]
        summary["already_refreshed_today"] = len(done_today)
        pending = [it for it in pending if state.get(it.gsid) != today]
        # new members' history: at most max_new a DAY (not per slice) — the day row counts what was loaded
        max_new = max(0, max_new - int(day.get("new_loaded") or 0))
    async with pool.acquire() as conn:
        await assign_tiers(conn, pending, list_priority=list_priority)
    deferred_total = 0
    new_loaded = 0
    asked: list[str] = []          # the order this slice asked for securities (the verify reads it)
    for key in order:
        if not pending:
            break
        prov = fetcher(key, providers=providers)
        async with pool.acquire() as conn:
            last = await latest_dates(conn, key, [it.gsid for it in pending])
        ordered = refresh_order(pending, state, last)
        new_all = [it for it in ordered if it.gsid not in last]
        new_ok = {it.gsid for it in new_all[:max_new]}
        new_later = len(new_all) - len(new_ok)
        work = [it for it in ordered if it.gsid in last or it.gsid in new_ok]
        budget = await _budget(pool, key, today)
        res_old, res_new = IngestOutcome(key), IngestOutcome(key)
        gap_start = today - timedelta(days=gap_days)
        hist_start = date(today.year - years, today.month, min(today.day, 28))
        stop = False
        # the order is kept EXACTLY: consecutive runs of the same kind (gap fill / new history — different date
        # ranges) are asked in turn, each in chunks of SLICE_CHUNK; never "all gap fills of a chunk first"
        runs: list[tuple[bool, list]] = []
        for it in work:
            old_ = it.gsid in last
            if not runs or runs[-1][0] != old_ or len(runs[-1][1]) >= SLICE_CHUNK:
                runs.append((old_, []))
            runs[-1][1].append(it)
        for old_, part in runs:
            res, start_ = (res_old, gap_start) if old_ else (res_new, hist_start)
            if stop:
                res.deferred += [it.gsid for it in part]
                continue
            left = None if budget is None else max(0, budget - res_old.calls - res_new.calls)
            asked += [it.gsid for it in part]
            # a multi-symbol source asks a whole batch in ONE request: the batch is at least its per-call size
            r = await ingest(pool, prov, part, start_, today, today=today, run_id=run_id, budget_left=left,
                             deadline=deadline, clock=clock, batch=max(BATCH, int(prov.spec.max_symbols_per_call or 1)))
            _merge(res, r)
            if r.errors or r.deferred:
                stop = True
        calls = res_old.calls + res_new.calls
        if calls:
            await W.record_calls(pool, key, calls, data_type=DATA_TYPE, ok=not (res_old.errors or res_new.errors),
                                 error="; ".join(res_old.errors + res_new.errors)[:2000] or None, run_id=run_id,
                                 day=today)
        await _aclose(prov)
        await mark_refreshed(pool, res_old.done_ids + res_new.done_ids, day=today, outcome="found", provider_key=key,
                             day_id=day["id"] if day else None)
        new_loaded += len(res_new.done_ids)
        deferred_total += len(res_old.deferred) + len(res_new.deferred)
        summary["providers"][key] = {"gap_fill": res_old.summary(), "new_history": res_new.summary(),
                                     "new_deferred_to_next_night": new_later, "calls": calls}
        errors += res_old.errors + res_new.errors
        nf = set(res_old.not_found_ids) | set(res_new.not_found_ids)
        pending = [it for it in pending if it.gsid in nf]      # the next adapter tries only what this one lacked
        max_new = max(0, max_new - len(res_new.done_ids))
    # what EVERY adapter answered "no data" for has been asked today: refreshed (not found), not retried all day
    if pending and order:
        await mark_refreshed(pool, [it.gsid for it in pending], day=today, outcome="not_found", provider_key=order[-1],
                             day_id=day["id"] if day else None)
    summary["errors"] = errors
    summary["deferred"] = deferred_total
    summary["new_loaded"] = new_loaded
    summary["asked"] = asked
    return summary


async def _record_slice(pool, day: dict, summary: dict, *, keys_order: list[str], today: date) -> dict | None:
    """The slice's progress on the day row: covered / refreshed today (READ BACK from the state table, not counted),
    calls, slices, what is left; the status the day is now in."""
    ids = summary.pop("_refreshable_ids", [])
    async with pool.acquire() as conn:
        state = await refresh_state(conn, ids)
    covered = len(ids)
    refreshed = sum(1 for g in ids if state.get(g) == today)
    calls = sum(int(v.get("calls") or 0) for v in summary["providers"].values())
    left = await _budget(pool, keys_order[0], today) if keys_order else None
    errs = summary["errors"]
    fails = int(day["consecutive_failures"]) + 1 if errs else 0
    remaining = max(0, covered - refreshed)
    if remaining == 0:
        status, detail = "complete", None
    elif left == 0:
        status, detail = "stopped_budget", (f"{keys_order[0]}'s daily call budget is spent; {remaining:,} not refreshed "
                                            "today go first tomorrow")
    elif fails >= FAILED_SLICES_STOP:
        status, detail = "stopped_error", "; ".join(errs[:3])[:2000]
    else:
        status, detail = "running", None
    slim = {k: v for k, v in summary.items() if k not in ("asked", "tracked_progress", "day")}
    return await _close_slice(
        pool, day["id"], covered=covered, refreshed=refreshed, calls=int(day["calls"]) + calls,
        slices=int(day["slices"]) + 1, deferred=remaining, new_loaded=int(day["new_loaded"]) + int(summary["new_loaded"]),
        consecutive_failures=fails, status=status, stop_detail=detail, summary=slim,
        errors=((day.get("errors") or []) + [{"at": datetime.now(timezone.utc).isoformat(), "error": e}
                                             for e in errs[:5]])[-50:])


# ── PRICES2: the scheduler tick continues the day ───────────────────────────
TICK_ENV = "PRICES_DAY_TICK"     # '0' turns the continuation step off (the 21:15 job still does its first slice)


def tick_enabled() -> bool:
    import os

    return (os.environ.get(TICK_ENV) or "1").strip().lower() not in ("0", "false", "off", "no")


async def job_trigger_active(conn) -> bool:
    """Is the platform job 'Security data — prices' switched on (its scheduled trigger active)? Platform scope."""
    from services import security_master_jobs as J

    async with conn.transaction():
        await _platform(conn)
        v = await conn.fetchval(
            "SELECT bool_or(t.is_active) FROM workflow_definitions d JOIN workflow_triggers t ON "
            "t.workflow_definition_id = d.id AND t.trigger_type = 'scheduled' WHERE d.org_id = $1::uuid AND d.name = $2",
            str(J.PLATFORM_ORG_ID), J.JOBS["security_data_prices"].name)
    return bool(v)


async def prices_tick(pool, *, now_utc: datetime | None = None, scope: str = DAY_SCOPE, check_trigger: bool = True,
                      nightly_kwargs: dict | None = None, budget_seconds: float | None = None,
                      clock=time.monotonic) -> dict:
    """One scheduler tick's price step: CONTINUE the latest running day row of ``scope`` with one slice. It never
    starts a day (the job's own schedule — weekdays 21:15 New York — does), never runs while the job is switched off,
    and never raises: a failure is recorded on the day row and returned."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if not tick_enabled():
        return {"action": "off", "reason": f"{TICK_ENV} is off"}
    try:
        async with pool.acquire() as conn:
            if check_trigger and not await job_trigger_active(conn):
                return {"action": "job_off"}
            async with conn.transaction():
                await _platform(conn)
                if not await conn.fetchval("SELECT to_regclass('portfolio.price_refresh_days') IS NOT NULL"):
                    return {"action": "not_deployed"}
                await conn.execute(
                    "UPDATE portfolio.price_refresh_days SET status = 'carried_over', finished_at = now(), lease_until = "
                    "NULL, stop_detail = 'not finished within ' || $3::integer || ' days' WHERE scope = $1 AND status = "
                    "'running' AND price_day < $2", scope, now_utc.date() - timedelta(days=STALE_DAY_ROWS),
                    STALE_DAY_ROWS)
                row = await conn.fetchrow("SELECT id, price_day FROM portfolio.price_refresh_days WHERE scope = $1 AND "
                                          "status = 'running' ORDER BY price_day DESC LIMIT 1", scope)
        if row is None:
            return {"action": "idle"}
        kw = dict(nightly_kwargs or {})
        kw.setdefault("deadline_s", min(NIGHTLY_DEADLINE_S, budget_seconds or NIGHTLY_DEADLINE_S))
        summ = await nightly(pool, today=row["price_day"], scope=scope, raise_errors=False, clock=clock, **kw)
        d = summ.get("day") or {}
        return {"action": summ.get("action") or "continued", "day_id": str(row["id"]),
                "price_day": row["price_day"].isoformat(), "status": d.get("status"),
                "progress": d.get("progress_label"), "errors": summ.get("errors") or []}
    except Exception as exc:  # noqa: BLE001 — never raised into the tick
        return {"action": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


# ── PRICES2: the Security master screen's line ──────────────────────────────
async def refresh_status(conn, *, today: date | None = None, scope: str = DAY_SCOPE,
                         include_test_orgs: bool = False, only_gsids: list[str] | None = None) -> dict:
    """"covered N · refreshed today M · oldest price date D": N = the coverage set; M = covered securities whose last
    refresh is ``today`` (or the latest day row's day); D = the oldest LATEST platform close across the covered
    securities that have one (the stalest series), with how many covered hold none. Plus the latest day rows."""
    days = await day_rows(conn, scope=scope, limit=7)
    day = date.fromisoformat(days[0]["price_day"]) if days else (today or datetime.now(timezone.utc).date())
    if today is not None:
        day = today
    items = await coverage_set(conn, include_test_orgs=include_test_orgs, only_gsids=only_gsids)
    ids = [it.gsid for it in items]
    state = await refresh_state(conn, ids)
    async with conn.transaction():
        await _platform(conn)
        rows = await conn.fetch(
            "SELECT global_security_id::text AS g, max(price_date) AS d FROM portfolio.securities_global_prices WHERE "
            f"global_security_id = ANY($1::uuid[]) AND org_id IS NULL AND price_type = 'close' AND {LIVE} GROUP BY 1",
            ids) if ids else []
    latest = {r["g"]: r["d"] for r in rows}
    covered = len(ids)
    refreshed = sum(1 for g in ids if state.get(g) == day)
    oldest = min(latest.values()) if latest else None
    label = (f"covered {covered:,} · refreshed today {refreshed:,} · oldest price date "
             f"{oldest.isoformat() if oldest else '—'}")
    return {"covered": covered, "refreshed_today": refreshed, "day": day.isoformat(),
            "oldest_price_date": oldest.isoformat() if oldest else None, "without_prices": covered - len(latest),
            "label": label, "days": days}


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
