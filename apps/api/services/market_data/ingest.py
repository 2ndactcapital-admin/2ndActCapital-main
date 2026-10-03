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
from datetime import date
from decimal import Decimal, InvalidOperation

from services.database import platform_scope
from services.market_data.fred import (
    FredClient,
    FredError,
    FredNotFound,
    scrub,
)

FRED_MISSING = "."
WRITE_CHUNK = 5000
VALIDATE_STATUSES = ("pending", "invalid_code", "active")

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


def _affected(status: str) -> int:
    return int(status.split()[-1])


async def write_observations(conn, series_id, points: list[tuple[date, Decimal]]) -> WriteCounts:
    """Apply the Rule 3 write rule for one series. Call INSIDE platform_scope(conn).

    Requires an open transaction so a series is written all-or-nothing.
    """
    if not conn.is_in_transaction():
        raise RuntimeError("write_observations must run inside platform_scope(conn)")

    existing = await conn.fetch(
        """SELECT id, obs_date, value
             FROM market_data.indicator_observations
            WHERE series_id = $1 AND valid_to IS NULL AND system_to IS NULL""",
        series_id,
    )
    active = {r["obs_date"]: (r["id"], r["value"]) for r in existing}

    counts = WriteCounts()
    to_insert: list[tuple[date, Decimal]] = []
    to_close: list = []
    for obs_date, value in points:
        current = active.get(obs_date)
        if current is None:
            to_insert.append((obs_date, value))
            counts.inserted += 1
        elif current[1] == value:  # Decimal numeric equality: 1.0 == 1.00
            counts.unchanged += 1
        else:
            to_close.append(current[0])
            to_insert.append((obs_date, value))
            counts.revised += 1

    for i in range(0, len(to_close), WRITE_CHUNK):
        chunk = to_close[i:i + WRITE_CHUNK]
        status = await conn.execute(
            """UPDATE market_data.indicator_observations
                  SET valid_to = now()
                WHERE id = ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL""",
            chunk,
        )
        if _affected(status) != len(chunk):
            # Zero affected rows cannot distinguish "row gone" from "RLS
            # refused" — say so rather than guess.
            raise RuntimeError(
                f"closing {len(chunk)} revised rows affected {_affected(status)}: "
                "a concurrent writer or a missing platform_scope (RLS) — not diagnosed")

    for i in range(0, len(to_insert), WRITE_CHUNK):
        chunk = to_insert[i:i + WRITE_CHUNK]
        status = await conn.execute(
            """INSERT INTO market_data.indicator_observations
                   (series_id, obs_date, value, valid_from)
               SELECT $1::uuid, t.d, t.v, now()
                 FROM unnest($2::date[], $3::numeric[]) AS t(d, v)""",
            series_id, [d for d, _ in chunk], [v for _, v in chunk],
        )
        if _affected(status) != len(chunk):
            raise RuntimeError(
                f"inserting {len(chunk)} observations affected {_affected(status)}")
    return counts


async def _record_run(conn, series_id, trigger: str, status: str, *,
                      counts: WriteCounts | None = None, error: str | None = None) -> None:
    counts = counts or WriteCounts()
    await conn.execute(
        """INSERT INTO market_data.indicator_ingest_runs
               (series_id, run_trigger, status, rows_inserted, rows_revised,
                rows_unchanged, error, finished_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, now())""",
        series_id, trigger, status, counts.inserted, counts.revised, counts.unchanged, error,
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
    sql = f"""SELECT id, series_key, source_code, frequency, license_class, ingest_status
                FROM market_data.indicator_series
               WHERE {where}"""
    if series_keys:
        args = [*args, list(series_keys)]
        sql += f" AND series_key = ANY(${len(args)}::text[])"
    sql += " ORDER BY sort_order, series_key"
    return await conn.fetch(sql, *args)


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


async def validate(conn, client: FredClient, series_keys: list[str] | None = None) -> list[ValidateOutcome]:
    """Check every FRED registry code against FRED's series metadata.

    One indicator_ingest_runs row per series (run_trigger='validate'). A
    transient failure NEVER marks a series invalid_code — only FredNotFound
    does.
    """
    rows = await _fetch_series(
        conn, "source_provider = 'fred' AND ingest_status = ANY($1::text[])",
        [list(VALIDATE_STATUSES)], series_keys)
    outcomes: list[ValidateOutcome] = []
    for row in rows:
        key, code = row["series_key"], row["source_code"]
        try:
            if not code:
                raise FredNotFound(f"registry row {key} has no source_code")
            meta = await client.get_series(code)
        except FredNotFound as exc:
            msg = client.scrub(exc)
            async with platform_scope(conn):
                await _update_series(conn, row["id"],
                                     "ingest_status = 'invalid_code', last_error = $2", msg)
                await _record_run(conn, row["id"], "validate", "failed", error=msg)
            outcomes.append(ValidateOutcome(key, code, "invalid_code", "invalid_code", error=msg))
            continue
        except Exception as exc:  # noqa: BLE001 — transient, refused, or a bug
            msg = client.scrub(exc) if isinstance(exc, FredError) \
                else client.scrub(f"{type(exc).__name__}: {exc}")
            async with platform_scope(conn):
                await _update_series(conn, row["id"], "last_error = $2", msg)
                await _record_run(conn, row["id"], "validate", "failed", error=msg)
            outcomes.append(ValidateOutcome(key, code, "error", row["ingest_status"], error=msg))
            continue

        finds: list[str] = []
        if meta.frequency != row["frequency"]:
            finds.append(f"{key}: frequency seed={row['frequency']} FRED={meta.frequency} "
                         f"(FRED frequency_short={meta.frequency_short!r}) — updated to FRED's")
        if "copyright" in (meta.notes or "").lower() and row["license_class"] == "public_domain":
            finds.append(f"{key}: FRED notes mention copyright but seed license_class="
                         "public_domain — review; license_class NOT changed")
        async with platform_scope(conn):
            await _update_series(
                conn, row["id"],
                "ingest_status = 'active', units = $2, seasonal_adjustment = $3, "
                "frequency = $4, last_validated_at = now(), last_error = NULL",
                meta.units, meta.seasonal_adjustment, meta.frequency)
            await _record_run(conn, row["id"], "validate", "success")
        outcomes.append(ValidateOutcome(key, code, "active", "active", units=meta.units, finds=finds))
    return outcomes


# ── backfill ────────────────────────────────────────────────────────────────
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


async def backfill(conn, client: FredClient, series_keys: list[str] | None = None) -> list[BackfillOutcome]:
    """Fetch full history for every 'active' series and write it, one
    transaction per series. One series failing never aborts the others."""
    rows = await _fetch_series(conn, "ingest_status = 'active'", [], series_keys)
    outcomes: list[BackfillOutcome] = []
    for row in rows:
        key = row["series_key"]
        try:
            raw = await client.get_observations(row["source_code"])
            parsed = parse_observations(raw)
            status = "partial" if parsed.rejected else "success"
            run_error = None
            if parsed.rejected:
                run_error = client.scrub(
                    f"rejected {len(parsed.rejected)} value(s): " + "; ".join(parsed.rejected[:5]))
            async with platform_scope(conn):
                counts = await write_observations(conn, row["id"], parsed.points)
                stats = await conn.fetchrow(
                    """SELECT count(*) AS n, min(obs_date) AS first, max(obs_date) AS last
                         FROM market_data.indicator_observations
                        WHERE series_id = $1 AND valid_to IS NULL AND system_to IS NULL""",
                    row["id"])
                await _update_series(conn, row["id"],
                                     "last_observation_date = $2, last_error = NULL", stats["last"])
                await _record_run(conn, row["id"], "backfill", status, counts=counts, error=run_error)
            outcomes.append(BackfillOutcome(
                key, status, counts, parsed.skipped, len(parsed.rejected),
                stats["n"], stats["first"], stats["last"], run_error))
        except Exception as exc:  # noqa: BLE001 — recorded per series, never raised
            msg = client.scrub(exc) if isinstance(exc, FredError) \
                else scrub(f"{type(exc).__name__}: {client.scrub(exc)}")
            try:
                async with platform_scope(conn):
                    await _update_series(conn, row["id"], "last_error = $2", msg)
                    await _record_run(conn, row["id"], "backfill", "failed", error=msg)
            except Exception as rec_exc:  # noqa: BLE001
                msg += client.scrub(f" | also failed to record the failure: {type(rec_exc).__name__}")
            outcomes.append(BackfillOutcome(key, "failed", error=msg))
    return outcomes
