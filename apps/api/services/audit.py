"""Audit logging.

The exact shape of ``audit_log`` is not known at build time, so this module
introspects the table's columns once (cached) and writes only into columns
that actually exist. A set of common column-name aliases is mapped to each
logical value, making the writer resilient to naming differences.
"""

import json
from typing import Any

import asyncpg

# Cache of {column_name: data_type} for audit_log, populated on first write.
_audit_columns: dict[str, str] | None = None


async def _load_columns(conn: asyncpg.Connection) -> dict[str, str]:
    global _audit_columns
    if _audit_columns is None:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'audit_log'
            """
        )
        _audit_columns = {r["column_name"]: r["data_type"] for r in rows}
    return _audit_columns


def _candidate_values(
    *,
    org_id: Any,
    action: str,
    table_name: str,
    record_id: Any,
    old: dict | None,
    new: dict | None,
    actor: Any,
) -> dict[str, Any]:
    """Map common audit column names to the value each should receive."""
    return {
        # tenant
        "org_id": org_id,
        # operation
        "action": action,
        "operation": action,
        "event_type": action,
        "change_type": action,
        "action_type": action,
        # target table
        "table_name": table_name,
        "entity_table": table_name,
        "target_table": table_name,
        "object_type": table_name,
        "record_type": table_name,
        "resource_type": table_name,
        # target row
        "record_id": record_id,
        "entity_id": record_id,
        "row_id": record_id,
        "target_id": record_id,
        "object_id": record_id,
        "resource_id": record_id,
        # before / after snapshots
        "old_values": old,
        "old_data": old,
        "previous_values": old,
        "before": old,
        "new_values": new,
        "new_data": new,
        "changed_values": new,
        "after": new,
        "payload": new,
        # actor
        "changed_by": actor,
        "created_by": actor,
        "actor_id": actor,
        "user_id": actor,
        "performed_by": actor,
    }


async def write_audit_log(
    conn: asyncpg.Connection | None = None,
    *,
    org_id: Any,
    action: str,
    table_name: str,
    record_id: Any,
    old: dict | None = None,
    new: dict | None = None,
    actor: Any = None,
) -> None:
    """Insert an audit row using a fresh pool connection. Never raises.

    The ``conn`` parameter is accepted for backward compatibility but is not
    used — the function always acquires a clean connection from the pool so
    that a caller whose transaction is in a failed state does not poison the
    audit write, and an audit failure never aborts the caller's transaction.
    """
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as fresh_conn:
            columns = await _load_columns(fresh_conn)
            if not columns:
                return  # No audit_log table present; nothing to do.

            candidates = _candidate_values(
                org_id=org_id,
                action=action,
                table_name=table_name,
                record_id=record_id,
                old=old,
                new=new,
                actor=actor,
            )

            insert_cols: list[str] = []
            values: list[Any] = []
            for col, value in candidates.items():
                if col not in columns or value is None:
                    continue
                data_type = columns[col]
                if data_type in ("json", "jsonb") and not isinstance(value, str):
                    value = json.dumps(value, default=str)
                insert_cols.append(col)
                values.append(value)

            if not insert_cols:
                return

            placeholders = ", ".join(f"${i + 1}" for i in range(len(values)))
            col_list = ", ".join(insert_cols)
            await fresh_conn.execute(
                f"INSERT INTO audit_log ({col_list}) VALUES ({placeholders})",
                *values,
            )
    except Exception as e:
        print(f"Audit log write failed: {e}")
