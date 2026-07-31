"""Chancery Phase 7 — event-trigger firing bridge.

The FIRST real firing of ``workflow_triggers.event_type`` anywhere in the
platform, narrowly scoped to exactly ONE event type: ``'document_confirmed'``.
Given a document that has just been successfully confirmed, this looks up the
org's ACTIVE event triggers and starts a ``workflow_run`` for each match.

Governance boundary (do not weaken):
  * This module automates *which runs start*. It NEVER changes what happens
    *within* a run. Every started run is driven by the real
    ``services.workflow_engine.start_workflow_run``, so each step still honours
    its own autonomy tier exactly as already built — a Tier-1 User Task still
    pauses for maker-checker approval, Tier-2 still confirm-and-log, only Tier-3
    executes freely. Auto-*starting* a run does not auto-*advance* it past any
    governed step.
  * ``org_id`` is passed in from the already-authenticated confirm context; it
    is never taken from a request body.

Failure policy:
  * No matching trigger → do nothing, log at info level, return cleanly. A
    document with no configured routine is the common case, not an error.
  * A trigger whose definition has no current version, or whose run fails to
    start, is logged and skipped — one bad trigger must never break the
    already-successful confirm, nor prevent other triggers from firing. (The
    engine itself records a failed start as a 'held' run + alert.)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from services import workflow_engine

logger = logging.getLogger(__name__)

# The one event this phase wires. Narrow by design — this is NOT a
# general-purpose autonomous-trigger dispatcher.
EVENT_DOCUMENT_CONFIRMED = "document_confirmed"


def _as_dict(value: Any) -> dict:
    """asyncpg hands jsonb back as a text string (no json codec registered on
    these pools); decode ``mapped_fields`` to a real dict so it nests properly
    in the run context rather than becoming a double-encoded string."""
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    if isinstance(value, dict):
        return value
    return {}


async def fire_document_confirmed_triggers(
    pool, org_id, document_id, *, started_by
) -> dict:
    """Start a workflow run for every active ``document_confirmed`` event trigger
    configured for ``org_id``.

    Called AFTER a document's status is successfully set to 'confirmed'. Returns
    a summary ``{"matched_triggers": int, "started_runs": [...], "skipped": [...]}``
    for logging/verification. Never raises — a bridge failure must not roll back
    an already-successful confirm.
    """
    started_runs: list[dict] = []
    skipped: list[dict] = []

    async with pool.acquire() as conn:
        # Real document context to hand the run. mapped_fields come from the
        # latest confirmed template extraction.
        doc = await conn.fetchrow(
            """
            SELECT id, entity_id, doc_family
            FROM documents
            WHERE id = $1 AND org_id = $2
            """,
            document_id, org_id,
        )
        if doc is None:
            # The caller only fires this after a successful confirm, so a missing
            # row here is anomalous — log and no-op rather than raise.
            logger.warning(
                "chancery_workflow_bridge: document %s not found for org %s; "
                "no workflow trigger fired",
                document_id, org_id,
            )
            return {"matched_triggers": 0, "started_runs": [], "skipped": []}

        # Phase-5 entity linkage is many-to-many in ``document_entity_links``
        # (NOT documents.entity_id, which is a separate legacy single field).
        # Take the earliest link as the primary entity for the run context, and
        # also hand the run the full linked-entity list.
        link_rows = await conn.fetch(
            """
            SELECT entity_id
            FROM document_entity_links
            WHERE document_id = $1 AND org_id = $2
            ORDER BY created_at, entity_id
            """,
            document_id, org_id,
        )
        linked_entity_ids = [str(r["entity_id"]) for r in link_rows]
        # Prefer a Phase-5 link; fall back to documents.entity_id if one is set
        # but no link row exists.
        primary_entity_id = (
            linked_entity_ids[0]
            if linked_entity_ids
            else (str(doc["entity_id"]) if doc["entity_id"] else None)
        )

        extraction = await conn.fetchrow(
            """
            SELECT mapped_fields
            FROM document_template_extractions
            WHERE document_id = $1 AND org_id = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            document_id, org_id,
        )
        mapped_fields = _as_dict(extraction["mapped_fields"]) if extraction else {}

        # workflow_triggers has NO category/doc_family column, so scoping is by
        # (org, event trigger, event_type, active) only — do NOT invent a column.
        triggers = await conn.fetch(
            """
            SELECT id, workflow_definition_id
            FROM workflow_triggers
            WHERE org_id = $1
              AND trigger_type = 'event'
              AND event_type = $2
              AND is_active = true
            """,
            org_id, EVENT_DOCUMENT_CONFIRMED,
        )

        if not triggers:
            logger.info(
                "chancery_workflow_bridge: no matching workflow trigger for "
                "org %s on %s (document %s) — graceful no-op",
                org_id, EVENT_DOCUMENT_CONFIRMED, document_id,
            )
            return {"matched_triggers": 0, "started_runs": [], "skipped": []}

        # Resolve the current version for each matched definition up front; a
        # trigger pointing at a definition with no current version is skipped.
        resolved: list[tuple[Any, Any]] = []  # (trigger_id, version_id)
        for trig in triggers:
            version_id = await conn.fetchval(
                """
                SELECT id FROM workflow_versions
                WHERE workflow_definition_id = $1 AND org_id = $2
                  AND is_current = true
                """,
                trig["workflow_definition_id"], org_id,
            )
            if version_id is None:
                logger.warning(
                    "chancery_workflow_bridge: trigger %s -> definition %s has no "
                    "current version; skipping",
                    trig["id"], trig["workflow_definition_id"],
                )
                skipped.append({
                    "trigger_id": str(trig["id"]),
                    "reason": "no_current_version",
                })
                continue
            resolved.append((trig["id"], version_id))

    # Start each run OUTSIDE the connection above — start_workflow_run acquires
    # and manages its own connection/transactions from the pool.
    for trigger_id, version_id in resolved:
        context = {
            "event_type": EVENT_DOCUMENT_CONFIRMED,
            "trigger_id": str(trigger_id),
            "document_id": str(document_id),
            "entity_id": primary_entity_id,
            "entity_ids": linked_entity_ids,
            "doc_family": doc["doc_family"],
            "mapped_fields": mapped_fields,
        }
        try:
            result = await workflow_engine.start_workflow_run(
                pool, version_id, org_id, context, started_by
            )
            logger.info(
                "chancery_workflow_bridge: trigger %s started run %s (status=%s, "
                "paused_at=%s) for document %s",
                trigger_id, result.get("run_id"), result.get("status"),
                result.get("paused_at"), document_id,
            )
            started_runs.append({
                "trigger_id": str(trigger_id),
                "workflow_version_id": str(version_id),
                "run_id": str(result.get("run_id")),
                "status": result.get("status"),
                "paused_at": result.get("paused_at"),
            })
        except Exception as exc:  # noqa: BLE001 — one bad run must not break confirm
            logger.exception(
                "chancery_workflow_bridge: failed to start run for trigger %s "
                "(document %s): %s",
                trigger_id, document_id, exc,
            )
            skipped.append({
                "trigger_id": str(trigger_id),
                "reason": f"start_failed: {type(exc).__name__}",
            })

    return {
        "matched_triggers": len(triggers),
        "started_runs": started_runs,
        "skipped": skipped,
    }
