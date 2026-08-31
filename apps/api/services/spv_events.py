"""SPV realization emitter — the first real publisher on top of
:mod:`services.domain_events`.

WHAT MAKES A DISTRIBUTION A "REALIZATION"
──────────────────────────────────────────────────────────────────────────────
Not the code string. ``public.transaction_types`` already carries the
accounting semantics as data, and ``services/portfolio_commitments.py`` states
the house rule plainly: *the flag values are read, never re-derived*. The
deployed catalogue (introspected 2026-08-31) draws the line we need:

    code             category       performance_impact   affects_nav
    dist_gain        distribution   gain                 -1
    dist_roc         distribution   distribution         -1
    dist_recallable  distribution   distribution         -1
    dist_stock       distribution   distribution         -1
    dist_income      distribution   income                0
    sell             transfer       gain                 -1

So the predicate is ``category = 'distribution' AND performance_impact = 'gain'``
— which today matches EXACTLY ``dist_gain`` and nothing else. Both halves are
load-bearing: without ``category`` a public-market ``sell`` (also
``performance_impact='gain'``) would emit an SPV realization.

This confirms the settled reading rather than restating it: a return of capital
carries ``performance_impact = 'distribution'`` because there is no gain in it
to carry against, and ``dist_income`` is ``income``. Matching on the flags
rather than on ``code = 'dist_gain'`` means a new distribution type added to the
catalogue with ``performance_impact='gain'`` is picked up with no code change
here, which is the entire reason those columns exist.

POSTED ONLY
──────────────────────────────────────────────────────────────────────────────
A draft or merely-allocated transaction is a proposal. Same discipline as
fee36's posted-only credit basis and fee37/39's posted-only cost recognition:
nothing downstream may compute carry against money nobody has actually moved.
The status is re-read from the database inside :func:`emit_spv_realization`
rather than trusted from the caller — the check and the fact then cannot drift.

PER-INVESTOR AMOUNTS, NOT JUST THE VEHICLE TOTAL
──────────────────────────────────────────────────────────────────────────────
Carry is owed per investor, so the payload carries the real
``spv_transaction_allocations`` rows. A consumer handed only ``amount`` would
have to re-derive the split, and any drift between its derivation and the
posted allocation is a real mispayment. Amounts are ``Decimal`` throughout and
serialise to exact JSON strings (see :mod:`services.domain_events`).

WHAT THIS DOES NOT DO
──────────────────────────────────────────────────────────────────────────────
It does not calculate carry, and it does not execute anything. It records that
a realization happened and lets whoever subscribed start a run. Every started
run is the ordinary governed engine, so a write-verb step still stops at its
own maker-checker gate exactly as it would if a human had started it.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from services.domain_events import publish_event

logger = logging.getLogger(__name__)

EVENT_SPV_REALIZATION = "spv_realization"
SOURCE_SPV_TRANSACTION = "spv_transaction"

POSTED_STATUS = "posted"
ACTIVE_ALLOCATION_STATUS = "active"

# See "WHAT MAKES A DISTRIBUTION A REALIZATION" above. Both halves required.
REALIZATION_CATEGORY = "distribution"
REALIZATION_PERFORMANCE_IMPACT = "gain"


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


async def load_realization_context(conn, transaction_id):
    """The transaction joined to its type and SPV, or None if it does not exist.

    Kept separate from the emit decision so a caller (or a test) can ask "is
    this a realization?" without publishing anything.
    """
    return await conn.fetchrow(
        """
        SELECT t.id, t.org_id, t.spv_id, t.txn_type, t.txn_date, t.amount,
               t.currency_code, t.amount_basis, t.allocation_basis, t.status,
               t.posted_at, t.description, t.reference, t.transaction_type_id,
               tt.code               AS type_code,
               tt.category           AS type_category,
               tt.performance_impact AS type_performance_impact,
               s.name                AS spv_name,
               s.class_label         AS class_label,
               s.vehicle_type        AS vehicle_type
        FROM spv_transactions t
        LEFT JOIN public.transaction_types tt ON tt.id = t.transaction_type_id
        LEFT JOIN spvs s ON s.id = t.spv_id
        WHERE t.id = $1
        """,
        transaction_id,
    )


def is_realization(row) -> bool:
    """Flag-driven, not code-driven. See the module docstring."""
    if row is None:
        return False
    return (
        row["type_category"] == REALIZATION_CATEGORY
        and row["type_performance_impact"] == REALIZATION_PERFORMANCE_IMPACT
    )


async def build_realization_payload(conn, row) -> dict:
    """The event payload for a realized SPV distribution.

    ``allocations`` is the real posted split, read at the same
    ``status='active'`` that ``spv_allocation.post_transaction`` itself uses —
    the payload must describe the rows that were actually posted, not a
    recomputation of them.
    """
    alloc_rows = await conn.fetch(
        """
        SELECT a.id, a.subscription_id, a.entity_id, a.ownership_pct,
               a.allocated_amount, a.status,
               e.display_name AS entity_display_name
        FROM spv_transaction_allocations a
        LEFT JOIN entities e ON e.id = a.entity_id
        WHERE a.transaction_id = $1
          AND a.status = $2
        ORDER BY a.entity_id, a.id
        """,
        row["id"], ACTIVE_ALLOCATION_STATUS,
    )

    allocations = [
        {
            "allocation_id": str(a["id"]),
            "subscription_id": str(a["subscription_id"]),
            "entity_id": str(a["entity_id"]),
            "entity_display_name": a["entity_display_name"],
            "ownership_pct": _dec(a["ownership_pct"]),
            "allocated_amount": _dec(a["allocated_amount"]),
        }
        for a in alloc_rows
    ]
    allocated_total = sum(
        (a["allocated_amount"] for a in allocations), Decimal("0")
    )

    return {
        "spv_id": str(row["spv_id"]),
        "spv_name": row["spv_name"],
        "spv_transaction_id": str(row["id"]),
        "transaction_type_code": row["type_code"],
        "transaction_type_id": str(row["transaction_type_id"]),
        "txn_type": row["txn_type"],
        "txn_date": row["txn_date"].isoformat() if row["txn_date"] else None,
        "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None,
        "amount": _dec(row["amount"]),
        "currency_code": row["currency_code"],
        "amount_basis": row["amount_basis"],
        "allocation_basis": row["allocation_basis"],
        # class_label is per-vehicle and often NULL (a whole-fund SPV). Carried
        # explicitly rather than omitted so a consumer can tell "no class" from
        # "class not supplied".
        "class_label": row["class_label"],
        "vehicle_type": row["vehicle_type"],
        "allocations": allocations,
        "allocations_total": allocated_total,
        "allocation_count": len(allocations),
    }


async def emit_spv_realization(pool, transaction_id, *, actor_user_id=None) -> dict | None:
    """Publish ``spv_realization`` for a POSTED, gain-type SPV transaction.

    Returns :func:`services.domain_events.publish_event`'s summary, or ``None``
    when this transaction is not a realization (wrong type, or not posted yet).
    ``None`` is the ordinary case, not an error.

    Never raises: it is called after the post has already committed, and a
    subscriber problem must not turn a correct, completed post into a 500.
    """
    try:
        async with pool.acquire() as conn:
            row = await load_realization_context(conn, transaction_id)
            if row is None:
                logger.warning(
                    "spv_events: transaction %s not found; nothing published",
                    transaction_id,
                )
                return None

            if row["status"] != POSTED_STATUS:
                logger.debug(
                    "spv_events: transaction %s is '%s', not '%s' — no event",
                    transaction_id, row["status"], POSTED_STATUS,
                )
                return None

            if not is_realization(row):
                logger.debug(
                    "spv_events: transaction %s type %s (category=%s, "
                    "performance_impact=%s) is not a realization — no event",
                    transaction_id, row["type_code"], row["type_category"],
                    row["type_performance_impact"],
                )
                return None

            payload = await build_realization_payload(conn, row)
            org_id = row["org_id"]

        return await publish_event(
            pool,
            org_id,
            EVENT_SPV_REALIZATION,
            SOURCE_SPV_TRANSACTION,
            row["id"],
            payload,
            created_by=actor_user_id,
        )
    except Exception as exc:  # noqa: BLE001 — must not undo a committed post
        logger.exception(
            "spv_events: failed to emit %s for transaction %s: %s",
            EVENT_SPV_REALIZATION, transaction_id, exc,
        )
        return None
