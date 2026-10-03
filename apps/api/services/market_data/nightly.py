"""Nightly refresh orchestrator (mkt02).

One run = one batch (one uuid4 ``batch_id``):
  * every selected series is fetched IN FULL and written through
    ingest.ingest_series — the same diff-and-write path backfill uses, one
    transaction per series inside platform_scope();
  * one indicator_ingest_runs row per series (run_trigger, batch_id, status,
    counts, scrubbed error, started_at, finished_at);
  * series whose source_provider has no adapter in the registry are SKIPPED
    and counted (skipped_no_adapter) — not failures;
  * one series failing never aborts the others;
  * at the end ONE summary row (series_id NULL, same batch_id).

WHY FULL HISTORY, NOT A RECENT WINDOW: annual benchmark revisions (payrolls,
GDP, price indexes) reach back years. One FRED request per series makes the
full pull cheap, and the diff writes only what changed (Rule 3).

OVERLAP: no lock. Two runs over the same series are safe because of the
partial unique index plus ingest.apply_plan's race rules (a close that
matches zero rows skips its insert; an insert that conflicts does nothing).
Render also never runs two instances of one cron service at once.

The summary row has no columns for series counts, so they are written into
its ``error`` text in a fixed ``key=value`` form (SUMMARY_PREFIX …); the
rows_* columns carry the batch's row totals.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from services.database import platform_scope
from services.market_data import ingest
from services.market_data.adapters import scrub_error

SUMMARY_PREFIX = "batch summary:"


@dataclass
class SeriesResult:
    series_key: str
    source_provider: str
    status: str  # success | partial | failed
    inserted: int = 0
    revised: int = 0
    unchanged: int = 0
    lost_races: int = 0
    error: str | None = None


@dataclass
class NightlyResult:
    batch_id: uuid.UUID
    trigger: str
    status: str = "success"
    attempted: int = 0
    succeeded: int = 0
    partial: int = 0
    failed: int = 0
    skipped_no_adapter: int = 0
    rows_inserted: int = 0
    rows_revised: int = 0
    rows_unchanged: int = 0
    failed_series: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)  # (series_key, scrubbed error)
    skipped_series: list[str] = field(default_factory=list)
    series: list[SeriesResult] = field(default_factory=list)

    def summary_text(self) -> str:
        text = (f"{SUMMARY_PREFIX} attempted={self.attempted} succeeded={self.succeeded} "
                f"partial={self.partial} failed={self.failed} "
                f"skipped_no_adapter={self.skipped_no_adapter}")
        if self.failed_series:
            text += "; failed: " + ", ".join(self.failed_series)
        return text


def batch_status(attempted: int, failed: int) -> str:
    """success if nothing failed; failed if EVERY attempted series failed;
    otherwise partial. Skipped series are not attempted, so they never make
    a batch partial."""
    if failed == 0:
        return "success"
    if failed == attempted:
        return "failed"
    return "partial"


async def select_series(conn, series_selection: list[str] | None = None) -> list:
    """The nightly's series: every 'active' row, by sort_order — or, when a
    selection of series_keys is injected (tests), exactly those rows."""
    base = """SELECT id, series_key, source_provider, source_code, frequency, license_class,
                     ingest_status, sort_order, last_observation_date
                FROM market_data.indicator_series"""
    if series_selection is None:
        return await conn.fetch(base + " WHERE ingest_status = 'active' ORDER BY sort_order, series_key")
    return await conn.fetch(base + " WHERE series_key = ANY($1::text[]) ORDER BY sort_order, series_key",
                            list(series_selection))


async def run_nightly(conn, registry: Mapping[str, Any], series_selection: list[str] | None = None,
                      trigger: str = "nightly") -> NightlyResult:
    """Refresh every selected series; return the batch totals.

    ``registry`` maps source_provider → adapter (adapters.build_registry() for
    the real one). ``series_selection`` (series_keys) replaces the default
    "every active series" selection. Raises only if the batch itself cannot
    proceed (e.g. the database is unreachable) — per-series problems are
    recorded and counted.
    """
    result = NightlyResult(batch_id=uuid.uuid4(), trigger=trigger)
    started_at = await conn.fetchval("SELECT clock_timestamp()")  # database clock, like every run row
    rows = await select_series(conn, series_selection)

    for row in rows:
        provider = row["source_provider"]
        adapter = registry.get(provider)
        if adapter is None:
            result.skipped_no_adapter += 1
            result.skipped_series.append(row["series_key"])
            continue
        result.attempted += 1
        outcome = await ingest.ingest_series(conn, adapter, row, trigger=trigger,
                                             batch_id=result.batch_id)
        error = scrub_error(outcome.error, adapter) if outcome.error else None
        sr = SeriesResult(row["series_key"], provider, outcome.status,
                          outcome.counts.inserted, outcome.counts.revised,
                          outcome.counts.unchanged, outcome.counts.lost_races, error)
        result.series.append(sr)
        result.rows_inserted += sr.inserted
        result.rows_revised += sr.revised
        result.rows_unchanged += sr.unchanged
        if outcome.status == "failed":
            result.failed += 1
            result.failed_series.append(sr.series_key)
            result.failures.append((sr.series_key, error or "failed with no message"))
        else:
            result.succeeded += 1
            if outcome.status == "partial":
                result.partial += 1

    result.status = batch_status(result.attempted, result.failed)
    async with platform_scope(conn):
        await ingest._record_run(
            conn, None, trigger, result.status,
            counts=ingest.WriteCounts(result.rows_inserted, result.rows_revised, result.rows_unchanged),
            error=scrub_error(result.summary_text()), batch_id=result.batch_id, started_at=started_at)
    return result
