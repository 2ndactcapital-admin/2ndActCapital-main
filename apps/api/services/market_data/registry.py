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

SECURITY LINKS (mkt02c)
──────────────────────────────────────────────────────────────────────────────
A seed row may carry ``security_global_id`` + ``security_name``. After the
upsert, the loader sets indicator_series.security_global_id ONLY when the
series has no link yet, the id names a live (valid_to, system_to and
merged_into_id NULL) portfolio.securities_global row of security_type 'index'
whose name is EXACTLY ``security_name``, and no other series already holds
that security (uq_indicator_series_security). Anything else leaves the link
as it is and reports a [FIND]. An existing link is never overwritten, and
portfolio.* is only ever read. The link is not a definition field: it is not
part of the upsert's change test, so ``updated`` counts are unaffected.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

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
LINK_FIELDS = ("security_global_id", "security_name")
LINK_SECURITY_TYPE = "index"


@dataclass
class LoadResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    linked: int = 0
    link_unchanged: int = 0  # already linked to exactly the seed's security
    finds: list[str] = field(default_factory=list)


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
        if any(f in row for f in LINK_FIELDS):
            outcome, find = await link_security(
                conn, row["series_key"], row.get("security_global_id"), row.get("security_name"))
            if outcome == "linked":
                result.linked += 1
            elif outcome == "already_linked":
                result.link_unchanged += 1
            if find:
                result.finds.append(find)
    return result


async def link_security(conn, series_key: str, security_id, security_name) -> tuple[str, str | None]:
    """Link one series to its index security. Call INSIDE platform_scope(conn).

    Returns ``(outcome, find)``: outcome is 'linked', 'already_linked' (the
    series already points at exactly this security — idempotent, no find) or
    'skipped' (with the reason as ``find``). Never overwrites a link, never
    writes to portfolio.*, never raises for a refused link.
    """
    def skipped(reason: str) -> tuple[str, str]:
        return "skipped", f"{series_key}: security link NOT set — {reason}"

    if not security_id or not security_name:
        return skipped("the seed row needs both security_global_id and security_name")
    try:
        sec_id = uuid.UUID(str(security_id))
    except ValueError:
        return skipped(f"security_global_id {str(security_id)[:40]!r} is not a uuid")

    current = await conn.fetchrow(
        "SELECT id, security_global_id FROM market_data.indicator_series WHERE series_key = $1",
        series_key)
    if current is None:
        return skipped("the series row does not exist (or is not readable)")
    if current["security_global_id"] == sec_id:
        return "already_linked", None
    if current["security_global_id"] is not None:
        return skipped(f"already linked to security {current['security_global_id']}; "
                       "an existing link is never overwritten")

    sec = await conn.fetchrow(
        """SELECT name, security_type, valid_to, system_to, merged_into_id
             FROM portfolio.securities_global WHERE id = $1""", sec_id)
    if sec is None:
        return skipped(f"no portfolio.securities_global row has id {sec_id}")
    if sec["name"] != security_name:
        return skipped(f"security {sec_id} is named {sec['name']!r}, the seed says {security_name!r}")
    if sec["security_type"] != LINK_SECURITY_TYPE:
        return skipped(f"security {sec_id} has security_type {sec['security_type']!r}, "
                       f"expected {LINK_SECURITY_TYPE!r}")
    if sec["valid_to"] is not None or sec["system_to"] is not None or sec["merged_into_id"] is not None:
        return skipped(f"security {sec_id} is not live (closed, archived or merged)")

    owner = await conn.fetchval(
        "SELECT series_key FROM market_data.indicator_series WHERE security_global_id = $1 AND id <> $2",
        sec_id, current["id"])
    if owner is not None:
        return skipped(f"security {sec_id} is already linked to series {owner} "
                       "(uq_indicator_series_security allows one series per security)")

    try:
        async with conn.transaction():  # a savepoint: a refused link must not abort the load
            status = await conn.execute(
                """UPDATE market_data.indicator_series
                      SET security_global_id = $2, updated_at = now()
                    WHERE id = $1 AND security_global_id IS NULL""",
                current["id"], sec_id)
    except asyncpg.UniqueViolationError:
        return skipped(f"security {sec_id} was linked to another series concurrently "
                       "(uq_indicator_series_security)")
    if int(status.split()[-1]) != 1:
        return skipped("the UPDATE matched zero rows: a concurrent link OR RLS refusing the write "
                       "(is this inside platform_scope?) — zero rows cannot tell which")
    return "linked", None
