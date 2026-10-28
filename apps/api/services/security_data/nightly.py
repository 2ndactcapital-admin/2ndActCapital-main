"""The nightly security-data run — separate from the indicator nightly
(services.market_data.nightly, hollisworks-market-data-nightly), which it
never calls or changes.

    1. REFRESH: for each data type, the platform rank's providers that fetch
       over the network, in rank order, each within its daily call budget
       (provider_call_log). A provider whose budget is exhausted records a
       'budget_exhausted' quality event per item it could not fetch and the
       walk SKIPS to the next fetching provider in the rank for those items.
       One provider failing never stops the others.
    2. QUALITY (platform series): stale / gap / outlier over the stored
       index levels and platform prices in the look-back window.
    3. QUALITY (org data; test orgs skipped unless include_test_orgs): every
       security a trade of an OPEN proposal prices, read through the layer
       (internal) — stale and disagreeing items become quality events and a
       to-do for the org's approvers.
    4. One security_data_runs summary row.

Network only through the providers' injected transports (tests pass
recorded fixtures); the registry is an ARGUMENT."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from services.security_data import layer as L
from services.security_data import quality as Q
from services.security_data import registry as R
from services.security_data import settings as S
from services.security_data import writes as W
from services.security_data.providers import fred as FRED

TODO_SOURCE = "security_data_quality"
OPEN_STATUSES = ("draft", "pending_approval", "approved")


@dataclass
class RunResult:
    run_id: uuid.UUID
    trigger: str
    status: str = "success"
    fetched: dict = field(default_factory=dict)
    calls: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    budget_exhausted: dict = field(default_factory=dict)
    quality_events: int = 0
    todos: int = 0
    orgs_checked: int = 0
    orgs_skipped_test: int = 0

    def summary(self) -> dict:
        return {"fetched": self.fetched, "calls": self.calls, "errors": self.errors[:50],
                "budget_exhausted": self.budget_exhausted, "quality_events": self.quality_events,
                "todos": self.todos, "orgs_checked": self.orgs_checked, "orgs_skipped_test": self.orgs_skipped_test}


def coverage(data_type: str, provider_key: str) -> list[str]:
    """What a fetching provider refreshes. Index levels: the Treasury curve + fed funds."""
    if data_type == "index_level":
        return list(FRED.TREASURY_CURVE)
    return []


async def refresh(pool, providers: dict, today: date, run: RunResult, *, ranks: dict | None = None,
                  coverage_fn=None) -> None:
    """``ranks`` / ``coverage_fn`` override the platform rank and what each provider covers (tests: fixture
    providers and fixture series only — the real rank and the real Treasury curve are never touched)."""
    cover = coverage_fn or coverage
    async with pool.acquire() as conn:
        rows = await R.providers(conn)
        if ranks is None:
            ranks = {t: await R.platform_rank(conn, t) for t in ("eod_price", "corporate_action", "index_level",
                                                                  "option_quote", "fx")}
    for data_type, keys in ranks.items():
        fetchers = [k for k in keys if k in providers and data_type in getattr(providers[k], "fetches", ())
                    and rows.get(k, {}).get("status") == "active"]
        todo = None
        for k in fetchers:
            want = cover(data_type, k) if todo is None else todo
            if not want:
                break
            async with pool.acquire() as conn:
                left = await W.budget_left(conn, rows[k], today)
            if left == 0:
                out_skipped, calls, written, errors = list(want), 0, 0, []
            else:
                res = await providers[k].fetch(pool, data_type, want, today=today, budget_left=left)
                out_skipped, calls, written, errors = list(res.skipped if res.budget_exhausted else []), \
                    res.calls, res.written, res.errors
            if calls:
                await W.record_calls(pool, k, calls, data_type=data_type, ok=not errors,
                                     error="; ".join(errors)[:2000] or None, run_id=run.run_id, day=today)
            run.calls[k] = run.calls.get(k, 0) + calls
            run.fetched[f"{data_type}:{k}"] = written
            run.errors += errors
            if out_skipped:
                run.budget_exhausted[k] = len(out_skipped)
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                        for subj in out_skipped:
                            await W.record_quality_event(conn, org_id=None, data_type=data_type, subject_key=subj,
                                                         gsid=None, event_date=today, kind="budget_exhausted",
                                                         provider_key=k, run_id=run.run_id,
                                                         detail={"budget": rows[k]["daily_call_budget"]})
                            run.quality_events += 1
            todo = out_skipped          # only what this provider could not fetch goes on to the next one
            if not todo:
                break


async def platform_quality(pool, today: date, run: RunResult, *, only_keys: list[str] | None = None) -> None:
    window, _ = await _plat(pool, "security_data.quality_window_days")
    gap_days, _ = await _plat(pool, "security_data.gap_weekdays")
    mult, _ = await _plat(pool, "security_data.outlier_multiple")
    min_moves, _ = await _plat(pool, "security_data.outlier_min_moves")
    start = today - timedelta(days=int(window))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            for table, key_col, date_col, val_col, dtype in (
                    ("portfolio.index_levels", "index_key", "level_date", "value", "index_level"),
                    ("portfolio.securities_global_prices", "global_security_id::text", "price_date", "price",
                     "eod_price")):
                rows = await conn.fetch(
                    f"SELECT {key_col} AS k, provider_key, {date_col} AS d, {val_col} AS v FROM {table} WHERE org_id "
                    f"IS NULL AND {date_col} >= $1 AND {date_col} <= $2 AND valid_to IS NULL AND system_to IS NULL "
                    f"AND ($3::text[] IS NULL OR {key_col} = ANY($3::text[])) ORDER BY 1, 2, 3", start, today, only_keys)
                series: dict = {}
                for r in rows:
                    series.setdefault((r["k"], r["provider_key"] or ""), []).append((r["d"], r["v"]))
                limit = S.STALE_DAYS_DEFAULT[dtype]
                for (k, prov), pts in series.items():
                    gsid = k if dtype == "eod_price" else None
                    age = (today - pts[-1][0]).days
                    events = []
                    if S.is_stale(age, limit):
                        events.append(("stale", {"latest": pts[-1][0].isoformat(), "age_days": age, "limit": limit}))
                    for g in Q.gaps(pts, int(gap_days)):
                        events.append(("gap", g))
                    for o in Q.outliers(pts, mult, int(min_moves)):
                        events.append(("outlier", o))
                    for kind, detail in events:
                        await W.record_quality_event(conn, org_id=None, data_type=dtype, subject_key=k, gsid=gsid,
                                                     event_date=today, kind=kind, provider_key=prov, detail=detail,
                                                     run_id=run.run_id)
                        run.quality_events += 1


async def _plat(pool, key):
    async with pool.acquire() as conn:
        return await S.setting(conn, None, key)


async def open_proposal_securities(conn, org_id) -> dict[str, list[str]]:
    """{global_security_id: [open proposal ids]} — every security a live trade line of an open proposal
    (draft / pending approval / approved) prices. Org context."""
    feed = await conn.fetch(
        "SELECT DISTINCT t.global_security_id::text AS gsid, p.id AS proposal_id FROM "
        "public.rebalance_proposal_trades t JOIN public.rebalance_proposals p ON p.id = t.proposal_id "
        "WHERE p.org_id = $1::uuid AND p.system_to IS NULL AND p.status = ANY($2::text[]) "
        "AND t.system_to IS NULL AND t.valid_to IS NULL AND t.global_security_id IS NOT NULL", str(org_id),
        list(OPEN_STATUSES))
    out: dict[str, list[str]] = {}
    for f in feed:
        out.setdefault(f["gsid"], []).append(str(f["proposal_id"]))
    return out


async def org_quality(pool, today: date, run: RunResult, *, include_test_orgs: bool = False,
                      only_org_ids: list[str] | None = None, providers: dict | None = None, feed_fn=None) -> None:
    """``feed_fn(conn, org_id)`` overrides :func:`open_proposal_securities` (tests)."""
    from services import replica_terms as T
    from services.database import reset_rls_context, set_rls_context

    feed_fn = feed_fn or open_proposal_securities
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            if only_org_ids is not None:
                rows = await conn.fetch("SELECT id AS org_id, is_test FROM public.organizations WHERE id = "
                                        "ANY($1::uuid[])", only_org_ids)
            else:
                rows = await conn.fetch(
                    "SELECT DISTINCT p.org_id, o.is_test FROM public.rebalance_proposals p JOIN public.organizations o "
                    "ON o.id = p.org_id WHERE p.system_to IS NULL AND p.status = ANY($1::text[])", list(OPEN_STATUSES))
    for r in rows:
        if r["is_test"] and not include_test_orgs:
            run.orgs_skipped_test += 1
            continue
        org_id = str(r["org_id"])
        run.orgs_checked += 1
        tokens = set_rls_context(org_id, False)
        try:
            async with pool.acquire() as conn:
                by_sec = await feed_fn(conn, org_id)
                if not by_sec:
                    continue
                got = await L.get_bulk(conn, org_id, "eod_price", list(by_sec), as_of=today, providers=providers)
                approvers = await T._approvers(conn, org_id, "manage_org_settings")
                async with conn.transaction():
                    for gsid, res in got.items():
                        kind, detail = None, None
                        stale = [t for t in res.tried if str(t.get("why", "")).startswith("stale")]
                        if not res.found and stale:
                            kind, detail = "stale", {"tried": res.tried}
                        elif res.found and "disagreement" in res.quality:
                            kind, detail = "disagreement", {"winner": res.provider_key,
                                                            "value": format(res.value, "f"),
                                                            "others": res.detail.get("disagreement")}
                        if kind is None:
                            continue
                        detail["proposals"] = by_sec[gsid]
                        ev = await W.record_quality_event(conn, org_id=org_id, data_type="eod_price",
                                                          subject_key=gsid, gsid=gsid, event_date=today, kind=kind,
                                                          provider_key=res.provider_key or "", detail=detail,
                                                          run_id=run.run_id)
                        run.quality_events += 1
                        name = await conn.fetchval("SELECT name FROM portfolio.securities_global WHERE id = $1::uuid",
                                                   gsid) or gsid
                        ids = await T.open_todos(
                            conn, org_id=org_id, recipients=approvers, source=TODO_SOURCE,
                            related_type="data_quality_event", related_id=ev,
                            title=f"Price check: {name} ({'stale' if kind == 'stale' else 'sources disagree'})",
                            detail=f"{len(by_sec[gsid])} open proposal(s) price this security — "
                                   + (res.missing if kind == "stale" else res.label),
                            action_key="/admin/security-data/quality")
                        run.todos += len(ids)
        finally:
            reset_rls_context(tokens)


async def run_nightly(pool, *, providers: dict | None = None, today: date | None = None, trigger: str = "scheduled",
                      include_test_orgs: bool = False, only_org_ids: list[str] | None = None,
                      steps: tuple[str, ...] = ("refresh", "platform_quality", "org_quality"),
                      ranks: dict | None = None, coverage_fn=None, only_keys: list[str] | None = None,
                      feed_fn=None) -> RunResult:
    providers = L.code_registry(providers)
    today = today or datetime.now(timezone.utc).date()
    run = RunResult(uuid.uuid4(), trigger)
    started = datetime.now(timezone.utc)
    for name, fn in (("refresh", lambda: refresh(pool, providers, today, run, ranks=ranks, coverage_fn=coverage_fn)),
                     ("platform_quality", lambda: platform_quality(pool, today, run, only_keys=only_keys)),
                     ("org_quality", lambda: org_quality(pool, today, run, include_test_orgs=include_test_orgs,
                                                         only_org_ids=only_org_ids, providers=providers,
                                                         feed_fn=feed_fn))):
        if name not in steps:
            continue
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001 — one step failing never stops the next
            from services.market_data.adapters import scrub_error
            run.errors.append(f"{name}: {scrub_error(exc)}")
            run.status = "partial"
    if run.errors and run.status == "success":
        run.status = "partial"
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await conn.execute("INSERT INTO portfolio.security_data_runs (id, run_trigger, status, started_at, "
                               "finished_at, summary) VALUES ($1::uuid, $2, $3, $4, now(), $5::jsonb)",
                               run.run_id, trigger, run.status, started, json.dumps(run.summary(), default=str))
    return run
