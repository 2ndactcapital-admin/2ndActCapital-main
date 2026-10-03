"""Validate registry FRED codes against FRED, and backfill observation history.

Every write runs inside ``services.database.platform_scope(conn)`` — the
transaction-local super-admin carve-out the market_data write policies check.
Never a session-level SET (see platform_scope's docstring for why). No org_id
exists anywhere in this feature.

OBSERVATION WRITE RULE (CLAUDE.md Rule 3, valid-axis restatement)
──────────────────────────────────────────────────────────────────────────────
Active row = ``valid_to IS NULL AND system_to IS NULL`` (the predicate of the
partial unique index uq_indicator_obs_point). For each fetched point:

  * no active row for (series, date) → INSERT;
  * active row with a numerically equal value → write nothing;
  * active row with a different value → UPDATE that row SET valid_to = now(),
    then INSERT the new value with valid_from = now(). ``now()`` is the
    transaction start, so the closed row's valid_to equals the new row's
    valid_from exactly — no gap, no overlap.

Values are Decimal end to end: FRED's strings are parsed with Decimal(str);
asyncpg maps numeric ↔ Decimal. A float never appears.

A point that disappears from FRED is left as it is: this sprint has no
deletion semantics for observations, and a silent delete of history would be
the worse failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from services.database import platform_scope
from services.market_data.fred import FredClient

# Statuses `validate` picks up, per provider. FRED keeps mkt01's set. Yahoo
# also picks up 'deferred': its six rows were seeded deferred because no
# adapter existed, and `validate --provider yahoo` is how they become active.
VALIDATE_STATUSES_BY_PROVIDER = {
    "yahoo": ("deferred", "pending", "invalid_code", "active"),
}
# Registry columns a successful validate may write, in a fixed order — the
# adapter says which of them it knows (FRED: all three; Yahoo: units only).
VALIDATE_FIELDS = ("units", "seasonal_adjustment", "frequency")

FRED_MISSING = "."
WRITE_CHUNK = 5000
VALIDATE_STATUSES = ("pending", "invalid_code", "active")
DEFAULT_PROVIDER = "fred"

_NUMERIC_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


# ── Parsing ─────────────────────────────────────────────────────────────────
class RejectedValue(ValueError):
    """A value that is neither a number nor FRED's missing marker."""


def parse_value(raw: str) -> Decimal | None:
    """FRED value string → Decimal. Returns None for the '.' missing marker.

    Raises RejectedValue for anything non-numeric or non-finite. The regex runs
    first because Decimal() alone also accepts 'NaN', 'Infinity' and '1_000'.
    """
    text = (raw or "").strip()
    if text == FRED_MISSING:
        return None
    if not _NUMERIC_RE.match(text):
        raise RejectedValue(f"non-numeric value {text[:40]!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise RejectedValue(f"unparseable value {text[:40]!r}") from exc
    if not value.is_finite():
        raise RejectedValue(f"non-finite value {text[:40]!r}")
    return value


@dataclass
class ParsedObservations:
    points: list[tuple[date, Decimal]] = field(default_factory=list)
    skipped: int = 0
    rejected: list[str] = field(default_factory=list)


def parse_observations(raw: list[tuple[str, str]]) -> ParsedObservations:
    """Raw FRED ``(date, value)`` strings → typed points.

    obs_date is stored exactly as FRED gives it (FRED dates a monthly point on
    the 1st, a quarterly one on the quarter's first day) — never shifted.
    """
    out = ParsedObservations()
    seen: set[date] = set()
    for raw_date, raw_value in raw:
        try:
            obs_date = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            out.rejected.append(f"bad date {str(raw_date)[:20]!r}")
            continue
        try:
            value = parse_value(raw_value)
        except RejectedValue as exc:
            out.rejected.append(f"{obs_date}: {exc}")
            continue
        if value is None:
            out.skipped += 1
            continue
        if obs_date in seen:
            out.rejected.append(f"{obs_date}: duplicate date in one fetch")
            continue
        seen.add(obs_date)
        out.points.append((obs_date, value))
    return out


# ── Writes ──────────────────────────────────────────────────────────────────
@dataclass
class WriteCounts:
    inserted: int = 0
    revised: int = 0
    unchanged: int = 0
    # Points another writer got to first (also counted in ``unchanged``).
    lost_races: int = 0


@dataclass
class WritePlan:
    """What one fetch means against the active rows read just before it."""
    inserts: list[tuple[date, Decimal]] = field(default_factory=list)
    # (active row id, obs_date, new value)
    revisions: list[tuple[object, date, Decimal]] = field(default_factory=list)
    unchanged: int = 0


def _affected(status: str) -> int:
    return int(status.split()[-1])


async def read_active(conn, series_id) -> dict:
    """{obs_date: (row id, value)} for the series' active rows."""
    existing = await conn.fetch(
        """SELECT id, obs_date, value
             FROM market_data.indicator_observations
            WHERE series_id = $1 AND valid_to IS NULL AND system_to IS NULL""",
        series_id,
    )
    return {r["obs_date"]: (r["id"], r["value"]) for r in existing}


def plan_writes(active: dict, points: list[tuple[date, Decimal]]) -> WritePlan:
    plan = WritePlan()
    for obs_date, value in points:
        current = active.get(obs_date)
        if current is None:
            plan.inserts.append((obs_date, value))
        elif current[1] == value:  # Decimal numeric equality: 1.0 == 1.00
            plan.unchanged += 1
        else:
            plan.revisions.append((current[0], obs_date, value))
    return plan


async def _require_platform_scope(conn) -> None:
    """Fail loudly BEFORE writing if the RLS carve-out is not set.

    This is what lets :func:`apply_plan` read "the close matched zero rows" as
    "another run closed it first". Without this check, zero rows could just as
    well be RLS silently refusing the UPDATE (CLAUDE.md: "row not found" is the
    symptom of two different bugs), and the race handling would hide it.
    """
    if not conn.is_in_transaction():
        raise RuntimeError("market data writes must run inside platform_scope(conn)")
    scope = await conn.fetchval("SELECT current_setting('app.is_super_admin', true)")
    if scope != "true":
        raise RuntimeError("market data writes must run inside platform_scope(conn): "
                           "app.is_super_admin is not 'true' in this transaction")


async def apply_plan(conn, series_id, plan: WritePlan) -> WriteCounts:
    """Write a plan. Call INSIDE platform_scope(conn).

    RACE HANDLING (mkt02) — no lock; correctness comes from the partial unique
    index uq_indicator_obs_point plus these two rules:
      * the UPDATE that closes a revised row repeats the active-row predicate
        and returns the ids it really closed. A row it did not close was
        closed by a concurrent run between our read and our write, so the
        point is NOT inserted and counts as unchanged;
      * the INSERT uses ON CONFLICT (series_id, obs_date) WHERE <active> DO
        NOTHING, so a point a concurrent run inserted first is skipped (and
        counted unchanged) instead of aborting the series with a unique
        violation.
    Either way at most one active row per (series, date) can exist.
    """
    await _require_platform_scope(conn)
    counts = WriteCounts(unchanged=plan.unchanged)

    closed: set = set()
    ids = sorted({rid for rid, _, _ in plan.revisions}, key=str)
    for i in range(0, len(ids), WRITE_CHUNK):
        chunk = ids[i:i + WRITE_CHUNK]
        # Lock in id order so two concurrent runs revising the same series
        # queue behind each other instead of deadlocking.
        rows = await conn.fetch(
            """WITH target AS (
                   SELECT id FROM market_data.indicator_observations
                    WHERE id = ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL
                    ORDER BY id
                      FOR UPDATE)
               UPDATE market_data.indicator_observations o
                  SET valid_to = now()
                 FROM target
                WHERE o.id = target.id AND o.valid_to IS NULL AND o.system_to IS NULL
            RETURNING o.id""",
            chunk,
        )
        closed.update(r["id"] for r in rows)

    to_insert: list[tuple[date, Decimal]] = list(plan.inserts)
    revised_dates: set[date] = set()
    for rid, obs_date, value in plan.revisions:
        if rid in closed:
            to_insert.append((obs_date, value))
            revised_dates.add(obs_date)
        else:
            counts.unchanged += 1
            counts.lost_races += 1

    for i in range(0, len(to_insert), WRITE_CHUNK):
        chunk = to_insert[i:i + WRITE_CHUNK]
        rows = await conn.fetch(
            """INSERT INTO market_data.indicator_observations
                   (series_id, obs_date, value, valid_from)
               SELECT $1::uuid, t.d, t.v, now()
                 FROM unnest($2::date[], $3::numeric[]) AS t(d, v)
               ON CONFLICT (series_id, obs_date) WHERE valid_to IS NULL AND system_to IS NULL
               DO NOTHING
            RETURNING obs_date""",
            series_id, [d for d, _ in chunk], [v for _, v in chunk],
        )
        written = {r["obs_date"] for r in rows}
        for obs_date, _ in chunk:
            if obs_date not in written:
                counts.unchanged += 1
                counts.lost_races += 1
            elif obs_date in revised_dates:
                counts.revised += 1
            else:
                counts.inserted += 1
    return counts


async def write_observations(conn, series_id, points: list[tuple[date, Decimal]]) -> WriteCounts:
    """Apply the Rule 3 write rule for one series. Call INSIDE platform_scope(conn).

    Requires an open transaction so a series is written all-or-nothing.
    read → plan → apply; the split exists so the verify can close a row
    between the read and the write and prove the lost-race path.
    """
    await _require_platform_scope(conn)
    active = await read_active(conn, series_id)
    return await apply_plan(conn, series_id, plan_writes(active, points))


async def _record_run(conn, series_id, trigger: str, status: str, *,
                      counts: WriteCounts | None = None, error: str | None = None,
                      batch_id=None, started_at: datetime | None = None) -> None:
    """One indicator_ingest_runs row. ``series_id`` None = a batch summary row.

    ``started_at`` is when the work began (before the fetch); without it the
    column default is the transaction start, which is after the fetch.
    ``finished_at`` is clock_timestamp(), the real time of this write.
    """
    counts = counts or WriteCounts()
    await conn.execute(
        """INSERT INTO market_data.indicator_ingest_runs
               (series_id, run_trigger, status, rows_inserted, rows_revised,
                rows_unchanged, error, started_at, finished_at, batch_id)
           VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, COALESCE($8::timestamptz, now()),
                   clock_timestamp(), $9::uuid)""",
        series_id, trigger, status, counts.inserted, counts.revised, counts.unchanged, error,
        started_at, batch_id,
    )


async def _update_series(conn, series_id, sql_set: str, *args) -> None:
    status = await conn.execute(
        f"UPDATE market_data.indicator_series SET {sql_set}, updated_at = now() WHERE id = $1",
        series_id, *args,
    )
    if _affected(status) != 1:
        raise RuntimeError(
            "registry UPDATE matched zero rows: the row is gone OR RLS refused the "
            "write (is this inside platform_scope?) — zero rows cannot tell which")


async def _fetch_series(conn, where: str, args: list, series_keys: list[str] | None):
    sql = f"""SELECT id, series_key, source_provider, source_code, frequency, license_class,
                     ingest_status, sort_order, last_observation_date
                FROM market_data.indicator_series
               WHERE {where}"""
    if series_keys:
        args = [*args, list(series_keys)]
        sql += f" AND series_key = ANY(${len(args)}::text[])"
    sql += " ORDER BY sort_order, series_key"
    return await conn.fetch(sql, *args)


def _as_adapter(client_or_adapter):
    """Accept mkt01's FredClient (verify_mkt01 and older callers pass one) or
    any registry adapter."""
    if isinstance(client_or_adapter, FredClient):
        from services.market_data.adapters import FredAdapter
        return FredAdapter(client_or_adapter)
    return client_or_adapter


def _error_text(adapter, exc: BaseException) -> str:
    from services.market_data.adapters import scrub_error, ProviderError
    if isinstance(exc, ProviderError):
        return scrub_error(exc, adapter)
    return scrub_error(f"{type(exc).__name__}: {exc}", adapter)


# ── validate ────────────────────────────────────────────────────────────────
@dataclass
class ValidateOutcome:
    series_key: str
    source_code: str | None
    outcome: str  # 'active' | 'invalid_code' | 'error'
    status_after: str
    units: str | None = None
    error: str | None = None
    finds: list[str] = field(default_factory=list)


async def validate(conn, client, series_keys: list[str] | None = None, *,
                   provider: str = DEFAULT_PROVIDER) -> list[ValidateOutcome]:
    """Check every registry code for ``provider`` against its source.

    ``client`` is a provider adapter (services/market_data/adapters.py) or a
    FredClient. One indicator_ingest_runs row per series (run_trigger=
    'validate'). A transient failure NEVER marks a series invalid_code — only
    the source's explicit "not found" does.
    """
    from services.market_data.adapters import scrub_error

    adapter = _as_adapter(client)
    statuses = VALIDATE_STATUSES_BY_PROVIDER.get(provider, VALIDATE_STATUSES)
    rows = await _fetch_series(
        conn, "source_provider = $1 AND ingest_status = ANY($2::text[])",
        [provider, list(statuses)], series_keys)
    outcomes: list[ValidateOutcome] = []
    for row in rows:
        key, code = row["series_key"], row["source_code"]
        if not code:
            outcome, fields, msg, finds = "invalid_code", {}, f"registry row {key} has no source_code", []
        else:
            try:
                result = await adapter.validate_series(row)
                outcome, fields, finds = result.outcome, result.fields, result.finds
                msg = scrub_error(result.error, adapter) if result.error else None
            except Exception as exc:  # noqa: BLE001 — transient, refused, or a bug
                outcome, fields, finds, msg = "error", {}, [], _error_text(adapter, exc)

        if outcome == "invalid_code":
            async with platform_scope(conn):
                await _update_series(conn, row["id"],
                                     "ingest_status = 'invalid_code', last_error = $2", msg)
                await _record_run(conn, row["id"], "validate", "failed", error=msg)
            outcomes.append(ValidateOutcome(key, code, "invalid_code", "invalid_code", error=msg))
            continue
        if outcome != "active":
            msg = msg or "validation failed with no message"
            async with platform_scope(conn):
                await _update_series(conn, row["id"], "last_error = $2", msg)
                await _record_run(conn, row["id"], "validate", "failed", error=msg)
            outcomes.append(ValidateOutcome(key, code, "error", row["ingest_status"], error=msg))
            continue

        sets, args = ["ingest_status = 'active'"], []
        for name in VALIDATE_FIELDS:
            if name in fields:
                args.append(fields[name])
                sets.append(f"{name} = ${len(args) + 1}")
        sets += ["last_validated_at = now()", "last_error = NULL"]
        async with platform_scope(conn):
            await _update_series(conn, row["id"], ", ".join(sets), *args)
            await _record_run(conn, row["id"], "validate", "success")
        outcomes.append(ValidateOutcome(key, code, "active", "active",
                                        units=fields.get("units"), finds=finds))
    return outcomes


# ── per-series ingest (shared by backfill and the nightly) ─────────────────
@dataclass
class BackfillOutcome:
    series_key: str
    status: str  # success | partial | failed
    counts: WriteCounts = field(default_factory=WriteCounts)
    skipped: int = 0
    rejected: int = 0
    active_rows: int = 0
    first_date: date | None = None
    last_date: date | None = None
    error: str | None = None


async def ingest_series(conn, adapter, row, *, trigger: str, batch_id=None) -> BackfillOutcome:
    """Fetch one series' full history and write it — ONE transaction.

    This is the write path both ``backfill`` and the nightly orchestrator use.
    Never raises for a per-series problem: a failure is recorded on the
    series (last_error) and in the run log, and the series' observations and
    ingest_status are left exactly as they were.
    """
    from services.market_data.adapters import scrub_error

    key = row["series_key"]
    # The database clock, not this machine's: finished_at is clock_timestamp()
    # on the server, and a skewed laptop clock must not put start after finish.
    started_at = await conn.fetchval("SELECT clock_timestamp()")
    try:
        fetched = await adapter.fetch_series(row)
        status = "partial" if fetched.rejected else "success"
        run_error = None
        if fetched.rejected:
            run_error = scrub_error(
                f"rejected {len(fetched.rejected)} value(s): " + "; ".join(fetched.rejected[:5]),
                adapter)
        async with platform_scope(conn):
            counts = await write_observations(conn, row["id"], fetched.points)
            stats = await conn.fetchrow(
                """SELECT count(*) AS n, min(obs_date) AS first, max(obs_date) AS last
                     FROM market_data.indicator_observations
                    WHERE series_id = $1 AND valid_to IS NULL AND system_to IS NULL""",
                row["id"])
            await _update_series(conn, row["id"],
                                 "last_observation_date = $2, last_error = NULL", stats["last"])
            await _record_run(conn, row["id"], trigger, status, counts=counts, error=run_error,
                              batch_id=batch_id, started_at=started_at)
        return BackfillOutcome(key, status, counts, fetched.skipped, len(fetched.rejected),
                               stats["n"], stats["first"], stats["last"], run_error)
    except Exception as exc:  # noqa: BLE001 — recorded per series, never raised
        msg = _error_text(adapter, exc)
        try:
            async with platform_scope(conn):
                await _update_series(conn, row["id"], "last_error = $2", msg)
                await _record_run(conn, row["id"], trigger, "failed", error=msg,
                                  batch_id=batch_id, started_at=started_at)
        except Exception as rec_exc:  # noqa: BLE001
            msg += scrub_error(f" | also failed to record the failure: {type(rec_exc).__name__}")
        return BackfillOutcome(key, "failed", error=msg)


async def backfill(conn, client, series_keys: list[str] | None = None, *,
                   provider: str = DEFAULT_PROVIDER) -> list[BackfillOutcome]:
    """Fetch full history for every 'active' series of ``provider`` and write
    it, one transaction per series. One series failing never aborts the others.

    mkt01 selected every active series regardless of provider; that was only
    correct while FRED was the sole active provider. Filtering by provider
    keeps `backfill` (default 'fred') from sending a Yahoo ticker to FRED.
    """
    adapter = _as_adapter(client)
    rows = await _fetch_series(conn, "ingest_status = 'active' AND source_provider = $1",
                               [provider], series_keys)
    return [await ingest_series(conn, adapter, row, trigger="backfill") for row in rows]
