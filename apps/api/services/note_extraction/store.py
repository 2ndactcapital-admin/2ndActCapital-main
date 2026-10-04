"""Writes for the extraction engine: runs, readings, staging.

Every write runs inside services.database.platform_scope (SET LOCAL
app.is_super_admin inside its own transaction — never a session SET under the
pooler). Readings are append-only (a trigger refuses UPDATE). Nothing here
touches portfolio.securities_global or portfolio.securities_global_note_terms.

A provider call is stored as one '__call__' reading (value NULL) carrying the
call's tokens, cost, latency and provider-reported model; the per-field
readings of that call share its call_id and carry no cost of their own, so
SUM(cost_usd) over a run is the run's real spend, counted once per call.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from services.database import platform_scope
from services.note_extraction.sanitize import strip_nul, strip_nul_deep

CALL_FIELD = "__call__"

READING_COLUMNS = (
    "id", "reference_filing_id", "run_id", "field_key", "value", "value_normalized", "source",
    "origin", "status", "error", "deployment_name", "provider_model", "proxy_model_id",
    "ensemble_config_id", "prompt_version", "prompt_prefix_hash", "reasoning_effort",
    "source_quote", "quote_verified", "value_in_quote", "raw_char_start", "raw_char_end",
    "probability", "input_tokens", "output_tokens", "cached_tokens", "cost_usd", "latency_ms",
    "call_id", "metadata", "created_by",
)


@dataclass
class ReadingRow:
    reference_filing_id: str
    field_key: str
    source: str
    value: object = None
    value_normalized: str | None = None
    run_id: str | None = None
    origin: str = "cascade"
    status: str = "ok"
    error: str | None = None
    deployment_name: str | None = None
    provider_model: str | None = None
    proxy_model_id: str | None = None
    ensemble_config_id: str | None = None
    prompt_version: str | None = None
    prompt_prefix_hash: str | None = None
    reasoning_effort: str | None = None
    source_quote: str | None = None
    quote_verified: bool | None = None
    value_in_quote: bool | None = None
    raw_char_start: int | None = None
    raw_char_end: int | None = None
    probability: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    call_id: str | None = None
    metadata: dict = field(default_factory=dict)
    created_by: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def as_tuple(self):
        return (
            self.id, self.reference_filing_id, self.run_id, self.field_key,
            None if self.value is None else json.dumps(strip_nul_deep(self.value), default=str),
            strip_nul(self.value_normalized), self.source, self.origin, self.status,
            strip_nul((self.error or None) and self.error[:2000]),
            self.deployment_name, strip_nul(self.provider_model), strip_nul(self.proxy_model_id),
            self.ensemble_config_id, self.prompt_version,
            self.prompt_prefix_hash, self.reasoning_effort,
            strip_nul((self.source_quote or None) and self.source_quote[:2000]),
            self.quote_verified, self.value_in_quote, self.raw_char_start, self.raw_char_end,
            None if self.probability is None else Decimal(str(round(self.probability, 6))),
            self.input_tokens, self.output_tokens, self.cached_tokens,
            None if self.cost_usd is None else Decimal(str(round(self.cost_usd, 8))),
            self.latency_ms, self.call_id,
            json.dumps(strip_nul_deep(self.metadata), default=str), self.created_by,
        )


_INSERT_READING = (
    f"INSERT INTO portfolio.note_term_readings ({', '.join(READING_COLUMNS)}) VALUES ("
    + ", ".join(
        f"${i}::jsonb" if c in ("value", "metadata") else f"${i}"
        for i, c in enumerate(READING_COLUMNS, start=1)
    )
    + ")"
)


async def insert_readings(conn, rows: list[ReadingRow]) -> int:
    if not rows:
        return 0
    async with platform_scope(conn):
        await conn.executemany(_INSERT_READING, [r.as_tuple() for r in rows])
    return len(rows)


async def create_run(conn, *, run_kind: str, spend_cap_usd: float, config: dict,
                     ensemble_config_id=None, created_by=None, notes_planned: int = 0,
                     status: str = "running") -> str:
    async with platform_scope(conn):
        return str(await conn.fetchval(
            """INSERT INTO portfolio.note_extraction_runs
                 (run_kind, status, ensemble_config_id, config, spend_cap_usd, notes_planned, created_by)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7) RETURNING id""",
            run_kind, status, ensemble_config_id, json.dumps(config, default=str),
            Decimal(str(spend_cap_usd)), notes_planned, created_by,
        ))


async def recorded_spend(conn, run_id) -> float:
    v = await conn.fetchval(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM portfolio.note_term_readings WHERE run_id = $1", run_id)
    return float(v or 0)


async def finish_run(conn, run_id, *, status: str, notes_done: int, stop_reason: str | None = None,
                     report: dict | None = None) -> None:
    async with platform_scope(conn):
        spent = await recorded_spend(conn, run_id)
        await conn.execute(
            """UPDATE portfolio.note_extraction_runs
                  SET status = $2, notes_done = $3, stop_reason = $4, report = $5::jsonb,
                      spent_usd = $6, finished_at = now()
                WHERE id = $1""",
            run_id, status, notes_done, strip_nul(stop_reason),
            json.dumps(strip_nul_deep(report), default=str) if report is not None else None,
            Decimal(str(round(spent, 8))),
        )


async def update_run_progress(conn, run_id, *, notes_done: int) -> None:
    async with platform_scope(conn):
        spent = await recorded_spend(conn, run_id)
        await conn.execute(
            "UPDATE portfolio.note_extraction_runs SET notes_done = $2, spent_usd = $3 WHERE id = $1",
            run_id, notes_done, Decimal(str(round(spent, 8))),
        )


@dataclass
class StagedField:
    field_key: str
    resolved_value: object
    resolution: str
    is_critical: bool
    needs_review: bool
    winning_reading_id: str | None = None
    source_quote: str | None = None
    raw_char_start: int | None = None
    raw_char_end: int | None = None
    probability: float | None = None


async def insert_staging(conn, *, run_id, reference_filing_id, status: str, status_reason: str | None,
                         ensemble_config_id, full_tokens_est: int | None, trimmed_tokens_est: int | None,
                         disagreement_count: int, jev_called: bool, escalated: bool,
                         fuller_text_retry: bool, skip_second_reader_safe: bool | None,
                         cost_usd: float, unmatched_participants: list, detail: dict,
                         fields: list[StagedField]) -> str:
    async with platform_scope(conn):
        staging_id = await conn.fetchval(
            """INSERT INTO portfolio.note_extraction_staging
                 (run_id, reference_filing_id, status, status_reason, ensemble_config_id,
                  full_tokens_est, trimmed_tokens_est, disagreement_count, jev_called, escalated,
                  fuller_text_retry, skip_second_reader_safe, cost_usd, unmatched_participants, detail)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,$15::jsonb)
               RETURNING id""",
            run_id, reference_filing_id, status, strip_nul(status_reason), ensemble_config_id,
            full_tokens_est, trimmed_tokens_est, disagreement_count, jev_called, escalated,
            fuller_text_retry, skip_second_reader_safe, Decimal(str(round(cost_usd, 8))),
            json.dumps(strip_nul_deep(unmatched_participants), default=str),
            json.dumps(strip_nul_deep(detail), default=str),
        )
        await conn.executemany(
            """INSERT INTO portfolio.note_extraction_staged_fields
                 (staging_id, field_key, resolved_value, resolution, is_critical, needs_review,
                  winning_reading_id, source_quote, raw_char_start, raw_char_end, probability)
               VALUES ($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9,$10,$11)""",
            [(staging_id, f.field_key,
              None if f.resolved_value is None else json.dumps(strip_nul_deep(f.resolved_value), default=str),
              f.resolution, f.is_critical, f.needs_review, f.winning_reading_id,
              strip_nul((f.source_quote or None) and f.source_quote[:2000]), f.raw_char_start, f.raw_char_end,
              None if f.probability is None else Decimal(str(round(f.probability, 6))))
             for f in fields],
        )
    return str(staging_id)
