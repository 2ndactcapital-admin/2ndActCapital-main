"""SYSTEM-axis archive (CLAUDE.md Rule 3, the "system-time archival" axis) — ONE shared helper.

A table whose live row's id is referenced (events, approval tasks, sod_decisions, lots …) keeps that id stable: before
every change, a COPY of the live row is inserted with a fresh id, ``archived_from_id`` = the live id and
``system_to`` = now(); the caller then updates the live row (same id) with ``system_from`` = now().

CASH07 extracted this from the pattern seventeen services carried as their own copy (services/execution.py's
``_archive`` and its siblings). New code calls :func:`archive_live_row`; the existing copies are unchanged (moving them
is a follow-up, recorded in docs/cash/CASH07_NOTES.md). The table name is a trusted constant of the caller, never input.
"""

from __future__ import annotations

import re

_TABLE_RX = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


async def archive_live_row(conn, table: str, row_id) -> str | None:
    """Copy the live row ``row_id`` of ``table`` (schema-qualified) to an archived state; returns the copy's id, or None
    when no live row exists (the caller decides how to refuse). Runs on the caller's connection / transaction."""
    if not _TABLE_RX.match(table):
        raise ValueError(f"archive_live_row: not a schema-qualified table name: {table!r}")
    got = await conn.fetchval(
        f"""INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {table} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(row_id))
    return str(got) if got is not None else None
