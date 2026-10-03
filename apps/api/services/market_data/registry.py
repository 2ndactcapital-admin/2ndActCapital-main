"""Registry loader — docs/market_data/market_indicator_registry_v1.json →
market_data.indicator_series.

Registry rows are CONFIGURATION, edited in place with ``updated_at`` (not
bi-temporal). The loader owns the DEFINITION fields only. The OPERATIONAL
fields — ingest_status, units, seasonal_adjustment, last_validated_at,
last_observation_date, last_error — belong to the ingest code and are never
written here on an existing row. A new row takes ingest_status from the seed.

Never deletes: a row absent from the seed stays.

ONE DELIBERATE EXCEPTION — frequency on a validated FRED row
──────────────────────────────────────────────────────────────────────────────
``validate`` treats FRED as authoritative for frequency and corrects the row.
If the loader then wrote the seed's frequency back on every ``load``, the two
would fight: each ``all`` run would flip the value, re-print the same [FIND],
and count the row as "updated" forever. So once a FRED row has been validated
(``last_validated_at IS NOT NULL``), the loader keeps the row's frequency.
Fix the seed file to match and the row reads as unchanged again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SEED_PATH = REPO_ROOT / "docs" / "market_data" / "market_indicator_registry_v1.json"

DEFINITION_FIELDS = (
    "name", "category", "region", "frequency", "best_view", "default_transform",
    "cost_tier", "cost_note", "license_class", "source_provider", "source_code",
    "source_url", "notes", "sort_order",
)
OPERATIONAL_FIELDS = (
    "ingest_status", "units", "seasonal_adjustment", "last_validated_at",
    "last_observation_date", "last_error",
)
REQUIRED_SEED_FIELDS = ("series_key", *DEFINITION_FIELDS, "ingest_status")


@dataclass
class LoadResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def load_seed_file(path: Path | str = SEED_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    series = doc["series"] if isinstance(doc, dict) else doc
    for row in series:
        missing = [f for f in REQUIRED_SEED_FIELDS if f not in row]
        if missing:
            raise ValueError(f"seed row {row.get('series_key')!r} is missing {missing}")
    return series


# The frequency carve-out lives in ONE expression, used both as the SET value
# and in the change test, so "updated" and "unchanged" can never disagree.
_FREQUENCY_EXPR = (
    "CASE WHEN s.source_provider = 'fred' AND s.last_validated_at IS NOT NULL "
    "THEN s.frequency ELSE EXCLUDED.frequency END"
)


def _set_clause() -> str:
    parts = []
    for f in DEFINITION_FIELDS:
        parts.append(f"{f} = {_FREQUENCY_EXPR}" if f == "frequency" else f"{f} = EXCLUDED.{f}")
    parts.append("updated_at = now()")
    return ",\n                   ".join(parts)


def _changed_clause() -> str:
    lhs = ", ".join(f"s.{f}" for f in DEFINITION_FIELDS)
    rhs = ", ".join(_FREQUENCY_EXPR if f == "frequency" else f"EXCLUDED.{f}"
                    for f in DEFINITION_FIELDS)
    return f"({lhs}) IS DISTINCT FROM ({rhs})"


_COLUMNS = ("series_key", *DEFINITION_FIELDS, "ingest_status")
_UPSERT_SQL = f"""
    INSERT INTO market_data.indicator_series AS s ({", ".join(_COLUMNS)})
    VALUES ({", ".join(f"${i}" for i in range(1, len(_COLUMNS) + 1))})
    ON CONFLICT (series_key) DO UPDATE
               SET {_set_clause()}
             WHERE {_changed_clause()}
    RETURNING (xmax = 0) AS inserted
"""


async def load_registry(conn, series: list[dict]) -> LoadResult:
    """Upsert every seed row on series_key. Call INSIDE platform_scope(conn).

    A row whose definition fields already match is not touched at all (its
    updated_at does not move) and counts as unchanged.
    """
    result = LoadResult()
    for row in series:
        values = [row["series_key"], *(row[f] for f in DEFINITION_FIELDS), row["ingest_status"]]
        rec = await conn.fetchrow(_UPSERT_SQL, *values)
        if rec is None:
            result.unchanged += 1
        elif rec["inserted"]:
            result.inserted += 1
        else:
            result.updated += 1
    return result
