"""Long-history splice (mkt02c): older history from a second provider,
written into the SAME series, before the series' own first observation.

Built for fred.sp500 — FRED keeps only about ten years of S&P 500, Yahoo's
^GSPC goes back decades. Consumers keep seeing ONE series; provenance lives
per row in market_data.indicator_observations.source_provider (NULL = the
series' own source_provider, 'yahoo' = spliced in here).

RULES
──────────────────────────────────────────────────────────────────────────────
  * Boundary F0 = min(obs_date) over the series' ACTIVE rows whose
    source_provider IS NULL. Spliced rows never move it, so re-runs are stable.
  * The fetch goes through the existing Yahoo adapter with a synthetic series
    row — same Decimal-only parsing, 4 dp quantization, raw close and
    incomplete-bar exclusion as every other Yahoo series.
  * OVERLAP GATE before any write: on dates >= F0 present in both the fetch
    and the series' own rows, at least 99.0% must differ by <= 0.02 (FRED
    stores 2 dp, Yahoo 4) and none by more than 1.00, over at least
    MIN_OVERLAP_DAYS days (zero overlap would otherwise pass vacuously). A
    failure writes nothing. This is what stops a different index, or a
    differently-scaled series, from being spliced on.
  * Writes only dates strictly before F0. A date with no active row is
    INSERTed with source_provider 'yahoo'. An active 'yahoo' row whose value
    changed gets the Rule 3 revision (close it, insert the new value, still
    'yahoo'). A row from the series' own source (NULL) or any other provider
    is NEVER revised or closed here.
  * One transaction inside platform_scope(): observations, a notes sentence
    (once), one indicator_ingest_runs row (run_trigger 'manual').
    ingest_status is never changed.

WHY THE NIGHTLY DOES NOT ERASE IT: ingest.plan_writes only walks the points a
fetch returns; a date absent from a fetch is never read, closed or deleted
(verified in mkt02c Task 1b and proven in verify_mkt02c's nightly-survival
check).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from services.database import platform_scope
from services.market_data import ingest
from services.market_data.adapters import scrub_error

SPLICE_PROVIDER = "yahoo"
OVERLAP_TOLERANCE = Decimal("0.02")
OVERLAP_MAX_DIFF = Decimal("1.00")
OVERLAP_MIN_PERCENT = Decimal("99.0")
MIN_OVERLAP_DAYS = 20
WORST_DAYS_REPORTED = 10
DESIGN_DOC = "docs/MARKET_DATA_DESIGN_V1.md"


class SpliceConfigError(Exception):
    """The series is missing or has no own-source rows (exit 2)."""


def notes_sentence(f0: date, symbol: str) -> str:
    return f"History before {f0.isoformat()} spliced from Yahoo {symbol}; see {DESIGN_DOC}"


@dataclass
class GateResult:
    compared: int = 0
    within: int = 0
    worst: Decimal | None = None
    # (obs_date, own value, fetched value, |difference|), worst first, > 0 only
    worst_days: list[tuple[date, Decimal, Decimal, Decimal]] = field(default_factory=list)
    passed: bool = False
    reason: str | None = None

    @property
    def percent_within(self) -> Decimal | None:
        if not self.compared:
            return None
        return (Decimal(self.within) * 100 / Decimal(self.compared)).quantize(Decimal("0.01"))


@dataclass
class SpliceResult:
    series_key: str
    symbol: str
    dry_run: bool
    status: str = "ok"  # ok | gate_failed | fetch_failed
    f0: date | None = None
    fetched: int = 0
    rejected: int = 0
    candidates: int = 0           # fetched points dated strictly before F0
    gate: GateResult = field(default_factory=GateResult)
    inserted: int = 0
    revised: int = 0
    unchanged: int = 0
    untouchable: int = 0          # pre-F0 dates whose active row is NOT 'yahoo' — never written
    lost_races: int = 0
    earliest: date | None = None
    notes_updated: bool = False
    written: bool = False
    error: str | None = None


def run_gate(own: dict[date, Decimal], fetched: list[tuple[date, Decimal]], f0: date) -> GateResult:
    """Compare fetched vs own values on every date >= F0 both carry."""
    g = GateResult()
    diffs: list[tuple[date, Decimal, Decimal, Decimal]] = []
    for d, v in fetched:
        if d < f0 or d not in own:
            continue
        diff = abs(v - own[d])
        g.compared += 1
        if diff <= OVERLAP_TOLERANCE:
            g.within += 1
        diffs.append((d, own[d], v, diff))
    diffs.sort(key=lambda t: (-t[3], t[0]))
    g.worst = diffs[0][3] if diffs else None
    g.worst_days = [t for t in diffs if t[3] > 0][:WORST_DAYS_REPORTED]
    if g.compared < MIN_OVERLAP_DAYS:
        g.reason = (f"only {g.compared} overlapping day(s) on or after {f0}; at least "
                    f"{MIN_OVERLAP_DAYS} are needed to prove both sources are the same index")
    elif g.worst is not None and g.worst > OVERLAP_MAX_DIFF:
        g.reason = f"an overlapping day differs by {g.worst} (> {OVERLAP_MAX_DIFF})"
    elif g.percent_within is not None and Decimal(g.within) * 100 < OVERLAP_MIN_PERCENT * g.compared:
        g.reason = (f"{g.percent_within}% of overlapping days are within {OVERLAP_TOLERANCE} "
                    f"(< {OVERLAP_MIN_PERCENT}%)")
    g.passed = g.reason is None
    return g


async def _active_rows(conn, series_id) -> dict[date, tuple]:
    """{obs_date: (id, value, source_provider)} for the series' active rows."""
    rows = await conn.fetch(
        """SELECT id, obs_date, value, source_provider
             FROM market_data.indicator_observations
            WHERE series_id = $1 AND valid_to IS NULL AND system_to IS NULL""",
        series_id)
    return {r["obs_date"]: (r["id"], r["value"], r["source_provider"]) for r in rows}


async def splice_history(conn, adapter, series_key: str, symbol: str, *,
                         dry_run: bool = False) -> SpliceResult:
    """Splice ``symbol``'s history (via ``adapter``, a YahooAdapter or a fake)
    into ``series_key`` before its first own-source observation.

    Raises SpliceConfigError for a missing series / no own-source rows. A
    fetch failure or a failed gate is returned as a status, never raised, and
    in both cases nothing is written.
    """
    res = SpliceResult(series_key, symbol, dry_run)
    series = await conn.fetchrow(
        "SELECT id, notes FROM market_data.indicator_series WHERE series_key = $1", series_key)
    if series is None:
        raise SpliceConfigError(f"series {series_key} is not in the registry")
    f0 = await conn.fetchval(
        """SELECT min(obs_date) FROM market_data.indicator_observations
            WHERE series_id = $1 AND valid_to IS NULL AND system_to IS NULL
              AND source_provider IS NULL""", series["id"])
    if f0 is None:
        raise SpliceConfigError(f"series {series_key} has no active rows from its own source — "
                                "backfill it before splicing older history onto it")
    res.f0 = f0

    started_at = await conn.fetchval("SELECT clock_timestamp()")
    synthetic = {"id": None, "series_key": f"splice:{symbol}", "source_provider": SPLICE_PROVIDER,
                 "source_code": symbol, "frequency": "daily", "license_class": "third_party_licensed",
                 "ingest_status": "active", "sort_order": 0, "last_observation_date": None}
    try:
        fetched = await adapter.fetch_series(synthetic)
    except Exception as exc:  # noqa: BLE001 — reported, nothing written
        res.status = "fetch_failed"
        res.error = scrub_error(f"{type(exc).__name__}: {exc}", adapter)
        return res
    points = list(fetched.points)
    res.fetched = len(points)
    res.rejected = len(fetched.rejected)

    active = await _active_rows(conn, series["id"])
    own = {d: v for d, (_, v, p) in active.items() if p is None}
    res.gate = run_gate(own, points, f0)
    if not res.gate.passed:
        res.status = "gate_failed"
        return res

    inserts: list[tuple[date, Decimal]] = []
    revisions: list[tuple[object, date, Decimal]] = []
    for d, v in points:
        if d >= f0:
            continue
        res.candidates += 1
        cur = active.get(d)
        if cur is None:
            inserts.append((d, v))
        elif cur[2] != SPLICE_PROVIDER:
            res.untouchable += 1
        elif cur[1] == v:
            res.unchanged += 1
        else:
            revisions.append((cur[0], d, v))

    existing_min = min(active) if active else None
    if dry_run:
        res.inserted, res.revised = len(inserts), len(revisions)
        known = [d for d in (existing_min, min((d for d, _ in inserts), default=None)) if d is not None]
        res.earliest = min(known) if known else None
        return res

    if any(d >= f0 for d, _ in inserts) or any(d >= f0 for _, d, _ in revisions):
        raise RuntimeError("splice plan contains a date on or after F0 — refusing to write")

    async with platform_scope(conn):
        counts = await _apply(conn, series["id"], inserts, revisions)
        res.inserted, res.revised, res.lost_races = counts.inserted, counts.revised, counts.lost_races
        res.unchanged += counts.lost_races
        sentence = notes_sentence(f0, symbol)
        notes = series["notes"] or ""
        if sentence not in notes:
            new_notes = f"{notes} | {sentence}" if notes else sentence
            await ingest._update_series(conn, series["id"], "notes = $2", new_notes)
            res.notes_updated = True
        await ingest._record_run(
            conn, series["id"], "manual", "success",
            counts=ingest.WriteCounts(res.inserted, res.revised, res.unchanged), started_at=started_at)
        res.earliest = await conn.fetchval(
            """SELECT min(obs_date) FROM market_data.indicator_observations
                WHERE series_id = $1 AND valid_to IS NULL AND system_to IS NULL""", series["id"])
    res.written = True
    return res


async def _apply(conn, series_id, inserts, revisions) -> ingest.WriteCounts:
    """Close revised 'yahoo' rows, then insert with source_provider 'yahoo'.
    Same race rules as ingest.apply_plan (see its docstring): the close
    repeats the active predicate AND source_provider = 'yahoo', so it can
    never close an own-source row; the insert does nothing on conflict."""
    await ingest._require_platform_scope(conn)
    counts = ingest.WriteCounts()
    closed: set = set()
    ids = sorted({rid for rid, _, _ in revisions}, key=str)
    if ids:
        rows = await conn.fetch(
            """WITH target AS (
                   SELECT id FROM market_data.indicator_observations
                    WHERE id = ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL
                      AND source_provider = $2
                    ORDER BY id
                      FOR UPDATE)
               UPDATE market_data.indicator_observations o
                  SET valid_to = now()
                 FROM target
                WHERE o.id = target.id AND o.valid_to IS NULL AND o.system_to IS NULL
            RETURNING o.id""",
            ids, SPLICE_PROVIDER)
        closed = {r["id"] for r in rows}

    to_insert = list(inserts)
    revised_dates: set[date] = set()
    for rid, d, v in revisions:
        if rid in closed:
            to_insert.append((d, v))
            revised_dates.add(d)
        else:
            counts.lost_races += 1

    for i in range(0, len(to_insert), ingest.WRITE_CHUNK):
        chunk = to_insert[i:i + ingest.WRITE_CHUNK]
        rows = await conn.fetch(
            """INSERT INTO market_data.indicator_observations
                   (series_id, obs_date, value, valid_from, source_provider)
               SELECT $1::uuid, t.d, t.v, now(), $4
                 FROM unnest($2::date[], $3::numeric[]) AS t(d, v)
               ON CONFLICT (series_id, obs_date) WHERE valid_to IS NULL AND system_to IS NULL
               DO NOTHING
            RETURNING obs_date""",
            series_id, [d for d, _ in chunk], [v for _, v in chunk], SPLICE_PROVIDER)
        written = {r["obs_date"] for r in rows}
        for d, _ in chunk:
            if d not in written:
                counts.lost_races += 1
            elif d in revised_dates:
                counts.revised += 1
            else:
                counts.inserted += 1
    return counts


def format_report(res: SpliceResult) -> list[str]:
    """Operator output. Never a URL or a secret."""
    g = res.gate
    pct = g.percent_within
    lines = [
        f"series {res.series_key} ← Yahoo {res.symbol}; own-source boundary F0 = {res.f0}",
        f"points fetched: {res.fetched}" + (f" (rejected {res.rejected})" if res.rejected else ""),
        f"candidate pre-F0 points: {res.candidates}",
        f"overlap days compared: {g.compared}",
        f"within {OVERLAP_TOLERANCE}: {pct if pct is not None else '-'}%",
        f"worst difference: {g.worst if g.worst is not None else '-'}",
        f"rows inserted {res.inserted}, revised {res.revised}, unchanged {res.unchanged}"
        + (f", never-touched non-Yahoo rows before F0 {res.untouchable}" if res.untouchable else "")
        + (f" (lost races {res.lost_races})" if res.lost_races else ""),
        f"earliest date now present: {res.earliest or '-'}",
    ]
    if res.notes_updated:
        lines.append("notes: provenance sentence appended")
    return lines
