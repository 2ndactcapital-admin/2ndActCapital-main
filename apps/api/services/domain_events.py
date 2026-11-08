"""Domain event publishing — the platform's generic publish side.

``workflow_triggers`` has carried ``trigger_type='event'`` + ``event_type``
since the Workflow Manager shipped, and ONE hard-wired publisher existed for
ONE event type (``services/chancery_workflow_bridge.py``, for
``document_confirmed``). A trigger row that says "listen for event_type X"
implies something publishes X; nothing generic ever did. This module is that
missing half.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
──────────────────────────────────────────────────────────────────────────────
:func:`publish_event` records a fact and fans it out to whoever subscribed. It
is NOT a work queue, NOT a retry engine, and NOT a place where any business
rule about a specific event type lives. An emitter decides *whether* an event
happened (see :mod:`services.spv_events`); this module only decides *who hears
about it*. Adding a second, third or tenth event type requires no change here.

APPEND-ONLY, AND INDEPENDENT OF WHETHER ANYONE IS LISTENING
──────────────────────────────────────────────────────────────────────────────
The ``domain_events`` row is written FIRST, before any trigger is even looked
up, and it is written whether or not a single subscriber exists. Coupling the
record to the existence of a subscriber would mean the platform's history of
what happened silently depends on how it happened to be configured that day —
a trigger added tomorrow would leave a permanent hole behind it.

IDEMPOTENCY IS THE DATABASE'S JOB, NOT A READ-THEN-WRITE
──────────────────────────────────────────────────────────────────────────────
The deployed index

    domain_events_source_dedupe_uq UNIQUE (org_id, event_type, source_type, source_id)

is the whole guarantee. The insert is ``ON CONFLICT ... DO NOTHING RETURNING``,
so two concurrent publishes of the same fact cannot both win; the loser learns
it lost because it gets no row back and re-reads the winner's. A
``SELECT``-then-``INSERT`` would race, and would still race under a retry loop.

Note what the index does NOT include: ``occurred_at``. One source row therefore
yields at most ONE event of a given type, permanently. That is the intended
reading of "this transaction was realized" — it is a fact about the row, not a
tick of a clock.

RE-PUBLISHING MUST NOT RE-FIRE WHAT ALREADY FIRED
──────────────────────────────────────────────────────────────────────────────
Dedupe on ``domain_events`` alone is not enough: a second publish of the same
fact would find the same triggers and start a second run of each. So the
fan-out skips any trigger that ALREADY has a ``DELIVERED`` delivery for this
event. Deliberately ``DELIVERED`` and not "any delivery": a ``FAILED`` one
(a definition that had no publishable version at the time) SHOULD get another
chance once that is fixed, and re-publishing is how you take it. There is no
unique index on ``domain_event_deliveries`` to lean on here, so this is an
application-level guard and is documented as such.

ONE BAD TRIGGER MUST NOT SILENCE THE GOOD ONES
──────────────────────────────────────────────────────────────────────────────
Every trigger is resolved, started and recorded independently, each delivery
insert in its own transaction. A trigger whose definition has no current
version FAILS LOUDLY — a ``FAILED`` row naming why — rather than being skipped
into silence, which is how a subscriber that quietly stopped working goes
unnoticed for months. It does not abort the fan-out for the other subscribers.
:func:`publish_event` never raises.

MONEY IN PAYLOADS
──────────────────────────────────────────────────────────────────────────────
``Decimal`` is serialised to a JSON *string*, never a float. A carry
calculation downstream reading ``12345.67`` as a float has already lost.
:func:`decode_payload` is the matching reader.

ORG SCOPE
──────────────────────────────────────────────────────────────────────────────
``org_id`` is a parameter supplied by an already-authenticated caller, never
read from a request body. Trigger matching is filtered to that same org, so an
event can only ever start runs for its own tenant.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from services import workflow_autonomy, workflow_engine

logger = logging.getLogger(__name__)

EVENT_TRIGGER_TYPE = "event"

STATUS_DELIVERED = "DELIVERED"
STATUS_FAILED = "FAILED"


class _PayloadEncoder(json.JSONEncoder):
    """Decimal → exact string. See "MONEY IN PAYLOADS" above."""

    def default(self, o: Any):  # noqa: D102
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def encode_payload(payload: dict | None) -> str:
    return json.dumps(payload or {}, cls=_PayloadEncoder, sort_keys=True, default=str)


def decode_payload(value: Any) -> dict:
    """asyncpg hands ``jsonb`` back as text on these pools (no json codec is
    registered), so a caller reading ``domain_events.payload`` gets a string.
    Decode it here rather than in every reader."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return {}


async def _resolve_current_version(conn, *, definition_id, org_id):
    """The definition's publishable version, via the ONE existing mechanism.

    ``workflow_triggers`` points at a ``workflow_definition_id`` while
    ``workflow_runs`` requires a ``workflow_version_id``. That gap is already
    bridged in exactly one way in this codebase — ``workflow_versions.is_current``
    — by both real starters of a run: ``workflow_scheduler.load_due_candidates``
    (``LEFT JOIN ... AND v.is_current``) and ``chancery_workflow_bridge``. This
    is the same lookup, not a second one.
    """
    return await conn.fetchval(
        """
        SELECT id FROM workflow_versions
        WHERE workflow_definition_id = $1 AND org_id = $2
          AND is_current = true
        """,
        definition_id, org_id,
    )


async def _record_delivery(
    conn, *, org_id, event_id, trigger_id, run_id, status, error_detail
):
    """Write one ``domain_event_deliveries`` row in its OWN transaction.

    Its own transaction because a failure recording one trigger's outcome must
    not poison the connection for the next trigger's — under asyncpg an error
    inside an open transaction aborts every subsequent statement in it.
    """
    async with conn.transaction():
        return await conn.fetchval(
            """
            INSERT INTO domain_event_deliveries
                (org_id, domain_event_id, workflow_trigger_id, workflow_run_id,
                 status, error_detail)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            org_id, event_id, trigger_id, run_id, status, error_detail,
        )


STATUS_STARTING = "STARTING"          # S29B: the claim a run start holds (one per event per workflow version)
STATUS_RATE_LIMITED = "RATE_LIMITED"  # S29B: the trigger already started its hourly limit of runs
TODO_SOURCE_RATE_LIMITED = "workflow_event_rate_limited"


async def _claim_delivery(conn, *, org_id, event_id, trigger_id, version_id):
    """S29B — claim (event, workflow version) BEFORE starting a run: the partial unique index
    domain_event_deliveries_event_version_uq makes a second claim (another trigger on the same version, or a
    concurrent publish of the same event) match nothing. Returns the claim id, or None when already claimed."""
    async with conn.transaction():
        return await conn.fetchval(
            """
            INSERT INTO domain_event_deliveries
                (org_id, domain_event_id, workflow_trigger_id, workflow_version_id, status, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (domain_event_id, workflow_version_id)
               WHERE workflow_version_id IS NOT NULL AND status IN ('STARTING', 'DELIVERED')
            DO NOTHING
            RETURNING id
            """,
            org_id, event_id, trigger_id, version_id, STATUS_STARTING, workflow_autonomy.now(),
        )


async def _finish_delivery(conn, *, delivery_id, run_id, status, error_detail):
    async with conn.transaction():
        await conn.execute(
            "UPDATE domain_event_deliveries SET status = $2, workflow_run_id = $3, error_detail = $4 WHERE id = $1",
            delivery_id, status, run_id, error_detail)


async def _rate_limit_alert(conn, *, org_id, trigger_id, owner, detail: str) -> None:
    from services import workflow_todos

    recipients = set(await workflow_todos._org_admin_recipients(org_id))
    if owner:
        recipients.add(str(owner))
    async with conn.transaction():
        for uid in sorted(recipients):
            await workflow_todos._upsert_todo(
                conn, org_id=org_id, user_id=uid, source=TODO_SOURCE_RATE_LIMITED, related_type="workflow_trigger",
                related_id=trigger_id, title="Event trigger rate-limited", detail=detail[:2000], priority=10,
                action_key="/admin/workflows/triggers")


async def publish_event(
    pool,
    org_id,
    event_type: str,
    source_type: str,
    source_id,
    payload: dict | None = None,
    *,
    created_by=None,
) -> dict:
    """Record a domain event and start a run for every active subscriber.

    Returns a summary::

        {
          "event_id": str,
          "deduped": bool,          # True → this exact event already existed
          "occurred_at": datetime,
          "matched_triggers": int,  # active triggers for (org, event_type)
          "delivered": [ {trigger_id, workflow_run_id, delivery_id}, ... ],
          "failed":    [ {trigger_id, error_detail, delivery_id}, ... ],
          "already_delivered": [trigger_id, ...],   # skipped: fired previously
          "deduped_by_version": [trigger_id, ...],  # S29B: this event already started a run of that version
          "rate_limited": [trigger_id, ...],        # S29B: that version is over the org's event_runs_per_hour
        }

    Never raises. A publish is an observation about something that ALREADY
    happened and was already committed by its emitter; failing the caller
    because a subscriber is misconfigured would roll back a real, correct
    business write to protect a notification.
    """
    delivered: list[dict] = []
    failed: list[dict] = []
    already: list[str] = []
    by_version: list[str] = []
    limited: list[str] = []

    try:
        async with pool.acquire() as conn:
            # ── 1. The append-only record. The unique index IS the idempotency.
            row = await conn.fetchrow(
                """
                INSERT INTO domain_events
                    (org_id, event_type, source_type, source_id, payload, created_by)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                ON CONFLICT (org_id, event_type, source_type, source_id) DO NOTHING
                RETURNING id, occurred_at
                """,
                org_id, event_type, source_type, source_id,
                encode_payload(payload), created_by,
            )
            deduped = row is None
            if deduped:
                # Someone (an earlier call, or a concurrent one that won the
                # race) already recorded this fact. Adopt their row: the
                # fan-out below must key off the SAME event id, or the
                # already-delivered guard would look at the wrong event.
                row = await conn.fetchrow(
                    """
                    SELECT id, occurred_at FROM domain_events
                    WHERE org_id = $1 AND event_type = $2
                      AND source_type = $3 AND source_id = $4
                    """,
                    org_id, event_type, source_type, source_id,
                )
                if row is None:
                    # Only reachable if the row was deleted between the two
                    # statements. Nothing sane to fan out to.
                    logger.error(
                        "publish_event: %s/%s/%s conflicted then vanished for org %s",
                        event_type, source_type, source_id, org_id,
                    )
                    return {
                        "event_id": None, "deduped": True, "occurred_at": None,
                        "matched_triggers": 0, "delivered": [], "failed": [],
                        "already_delivered": [],
                    }

            event_id = row["id"]
            occurred_at = row["occurred_at"]

            # ── 2. Who subscribed. Scoped to this event's own org.
            triggers = await conn.fetch(
                """
                SELECT id, workflow_definition_id, created_by
                FROM workflow_triggers
                WHERE org_id = $1
                  AND trigger_type = $2
                  AND event_type = $3
                  AND is_active = true
                ORDER BY created_at, id
                """,
                org_id, EVENT_TRIGGER_TYPE, event_type,
            )

            # ── 3. Which of them already fired for THIS event.
            fired_rows = await conn.fetch(
                """
                SELECT DISTINCT workflow_trigger_id
                FROM domain_event_deliveries
                WHERE domain_event_id = $1 AND status = $2
                """,
                event_id, STATUS_DELIVERED,
            )
            fired = {r["workflow_trigger_id"] for r in fired_rows}

            # ── 4. Resolve each pending trigger to a runnable version.
            #      Resolution failures are recorded here and now; they are a
            #      real, reportable state, not a skip.
            runnable: list[tuple[Any, Any, Any]] = []  # (trigger_id, version_id, trigger owner)
            for trig in triggers:
                if trig["id"] in fired:
                    already.append(str(trig["id"]))
                    continue
                version_id = await _resolve_current_version(
                    conn,
                    definition_id=trig["workflow_definition_id"],
                    org_id=org_id,
                )
                if version_id is None:
                    detail = (
                        f"workflow_definition {trig['workflow_definition_id']} has no "
                        f"current version (workflow_versions.is_current = true); "
                        f"cannot start a run for trigger {trig['id']}"
                    )
                    logger.warning("publish_event: %s", detail)
                    delivery_id = await _record_delivery(
                        conn, org_id=org_id, event_id=event_id,
                        trigger_id=trig["id"], run_id=None,
                        status=STATUS_FAILED, error_detail=detail,
                    )
                    failed.append({
                        "trigger_id": str(trig["id"]),
                        "error_detail": detail,
                        "delivery_id": str(delivery_id),
                    })
                    continue
                runnable.append((trig["id"], version_id, trig["created_by"]))

            context_base = {
                "event_type": event_type,
                "source_type": source_type,
                "source_id": str(source_id),
                "payload": decode_payload(encode_payload(payload)),
                "occurred_at": occurred_at.isoformat(),
                "domain_event_id": str(event_id),
            }

        # ── 5. Start the runs OUTSIDE the connection above. start_workflow_run
        #      acquires and manages its own connections (and deliberately takes
        #      an INDEPENDENT one so its run row genuinely commits — see
        #      workflow_engine._independent_acquire); holding this one open
        #      across it would nest its work in this transaction.
        for trigger_id, version_id, trigger_created_by in runnable:
            context = dict(context_base, trigger_id=str(trigger_id), trigger_type=EVENT_TRIGGER_TYPE)
            run_id = None
            error_detail = None
            # S29B — rate limit (per trigger, per hour) and de-duplication (one run per event per workflow version)
            async with pool.acquire() as conn:
                over, n_hour, limit = await workflow_autonomy.event_rate_limited(conn, org_id=org_id,
                                                                                 workflow_version_id=version_id)
                if over:
                    detail = (f"workflow version {version_id} already started {n_hour} run(s) from events in the last "
                              f"hour (limit {limit}); event {event_id} ({event_type}) was not run (trigger "
                              f"{trigger_id})")
                    await _record_delivery(conn, org_id=org_id, event_id=event_id, trigger_id=trigger_id,
                                           run_id=None, status=STATUS_RATE_LIMITED, error_detail=detail)
                    await _rate_limit_alert(conn, org_id=org_id, trigger_id=trigger_id, owner=trigger_created_by,
                                            detail=detail)
                    limited.append(str(trigger_id))
                    continue
                claim_id = await _claim_delivery(conn, org_id=org_id, event_id=event_id, trigger_id=trigger_id,
                                                 version_id=version_id)
            if claim_id is None:
                by_version.append(str(trigger_id))
                continue
            try:
                result = await workflow_engine.start_workflow_run(
                    pool, version_id, org_id, context,
                    created_by or trigger_created_by,
                )
                run_id = result.get("run_id")
            except Exception as exc:  # noqa: BLE001 — one bad subscriber only
                logger.exception(
                    "publish_event: trigger %s failed to start a run for event %s",
                    trigger_id, event_id,
                )
                error_detail = f"{type(exc).__name__}: {exc}"
                run_id = getattr(exc, "held_run_id", None)  # a HELD run exists — the delivery names it

            async with pool.acquire() as conn:
                if run_id is None and error_detail is None:
                    # The engine returned without an id. Treat as a failure —
                    # a DELIVERED row naming no run is worse than no row.
                    error_detail = (
                        "start_workflow_run returned no run_id for "
                        f"workflow_version {version_id}"
                    )
                # a held run still has an id (exc.held_run_id) — the delivery names it
                await _finish_delivery(
                    conn, delivery_id=claim_id, run_id=run_id,
                    status=STATUS_FAILED if error_detail else STATUS_DELIVERED,
                    error_detail=error_detail,
                )
                delivery_id = claim_id
            if error_detail:
                failed.append({
                    "trigger_id": str(trigger_id),
                    "error_detail": error_detail,
                    "delivery_id": str(delivery_id),
                })
            else:
                delivered.append({
                    "trigger_id": str(trigger_id),
                    "workflow_run_id": str(run_id),
                    "delivery_id": str(delivery_id),
                })

        logger.info(
            "publish_event: %s %s/%s org=%s event=%s deduped=%s matched=%d "
            "delivered=%d failed=%d already=%d",
            event_type, source_type, source_id, org_id, event_id, deduped,
            len(triggers), len(delivered), len(failed), len(already),
        )
        return {
            "event_id": str(event_id),
            "deduped": deduped,
            "occurred_at": occurred_at,
            "matched_triggers": len(triggers),
            "delivered": delivered,
            "failed": failed,
            "already_delivered": already,
            "deduped_by_version": by_version,
            "rate_limited": limited,
        }

    except Exception as exc:  # noqa: BLE001 — publishing must not break the emitter
        logger.exception(
            "publish_event: unrecoverable failure publishing %s for %s/%s (org %s): %s",
            event_type, source_type, source_id, org_id, exc,
        )
        return {
            "event_id": None, "deduped": False, "occurred_at": None,
            "matched_triggers": 0, "delivered": delivered, "failed": failed,
            "already_delivered": already,
            "error": f"{type(exc).__name__}: {exc}",
        }
