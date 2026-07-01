"""SPV Transaction Allocation Service.

Handles computing, persisting, and posting allocations for SPV transactions.

Key invariant: SUM(allocated_amount) == transaction.amount EXACTLY, enforced
via the largest-remainder method with Decimal arithmetic (precision=28).
"""

from decimal import Decimal, ROUND_FLOOR, getcontext
from typing import Any

import asyncpg

from services.audit import write_audit_log

# Set Decimal context precision high enough for all money operations.
getcontext().prec = 28

_CENT = Decimal("0.01")


def _floor_cents(value: Decimal) -> Decimal:
    """Floor a Decimal to 2 decimal places (nearest cent, toward zero)."""
    return value.quantize(_CENT, rounding=ROUND_FLOOR)


async def compute_allocations(pool: asyncpg.Pool, transaction_id: str) -> list[dict]:
    """Compute (but do NOT persist) allocations for a transaction.

    Returns a list of dicts:
        {
            "subscription_id": str,
            "allocated_amount": str,   # stringified Decimal, e.g. "1234.56"
            "ownership_pct":   str,    # stringified Decimal
            "entity_id":       str,
        }

    Raises ValueError if:
    - The transaction is not found.
    - No eligible active subscriptions exist for the SPV.
    - Total weight across all subscribers is zero.
    """
    async with pool.acquire() as conn:
        # ------------------------------------------------------------------
        # Load transaction
        # ------------------------------------------------------------------
        txn_row = await conn.fetchrow(
            """
            SELECT id, org_id, spv_id, amount, allocation_basis
            FROM spv_transactions
            WHERE id = $1
            """,
            transaction_id,
        )
        if txn_row is None:
            raise ValueError(f"Transaction not found: {transaction_id}")

        org_id = txn_row["org_id"]
        spv_id = txn_row["spv_id"]
        txn_amount = Decimal(str(txn_row["amount"]))
        allocation_basis: str = txn_row["allocation_basis"]

        # ------------------------------------------------------------------
        # Load active subscriptions
        # ------------------------------------------------------------------
        sub_rows = await conn.fetch(
            """
            SELECT id, entity_id, commitment_amount, funded_amount, ownership_pct
            FROM spv_subscriptions
            WHERE spv_id = $1
              AND org_id = $2
              AND valid_to IS NULL
              AND subscription_status IN ('committed', 'funded')
            """,
            spv_id,
            org_id,
        )

        if not sub_rows:
            raise ValueError(
                f"No active subscriptions found for SPV {spv_id} "
                f"(transaction {transaction_id})"
            )

        # ------------------------------------------------------------------
        # Build weights per subscriber
        # ------------------------------------------------------------------
        subs: list[dict[str, Any]] = []

        if allocation_basis == "ownership_pct":
            for row in sub_rows:
                pct = row["ownership_pct"]
                if pct is None:
                    continue  # skip subs without a post-close ownership pct
                subs.append(
                    {
                        "subscription_id": str(row["id"]),
                        "entity_id": str(row["entity_id"]),
                        "weight": Decimal(str(pct)),
                        "ownership_pct": Decimal(str(pct)),
                    }
                )
            total_weight = sum(s["weight"] for s in subs)

        elif allocation_basis == "committed":
            for row in sub_rows:
                amt = row["commitment_amount"] or 0
                subs.append(
                    {
                        "subscription_id": str(row["id"]),
                        "entity_id": str(row["entity_id"]),
                        "weight": Decimal(str(amt)),
                        "ownership_pct": Decimal(str(row["ownership_pct"] or 0)),
                    }
                )
            total_weight = sum(s["weight"] for s in subs)

        elif allocation_basis == "funded":
            for row in sub_rows:
                amt = row["funded_amount"] or 0
                subs.append(
                    {
                        "subscription_id": str(row["id"]),
                        "entity_id": str(row["entity_id"]),
                        "weight": Decimal(str(amt)),
                        "ownership_pct": Decimal(str(row["ownership_pct"] or 0)),
                    }
                )
            total_weight = sum(s["weight"] for s in subs)

        else:
            raise ValueError(f"Unknown allocation_basis: {allocation_basis}")

        if not subs:
            raise ValueError(
                f"No subscribers with valid weight for allocation_basis="
                f"'{allocation_basis}' on transaction {transaction_id}"
            )

        if total_weight == 0:
            raise ValueError(
                f"Total weight is zero for allocation_basis='{allocation_basis}' "
                f"on transaction {transaction_id}; cannot allocate"
            )

        # ------------------------------------------------------------------
        # Largest-remainder method
        # ------------------------------------------------------------------
        # Step 1: floor allocation for each subscriber.
        floors: list[Decimal] = []
        remainders: list[Decimal] = []
        for s in subs:
            exact = txn_amount * s["weight"] / total_weight
            floor_val = _floor_cents(exact)
            floors.append(floor_val)
            remainders.append(exact - floor_val)

        # Step 2: distribute residual pennies to subscribers with the
        #         largest fractional remainders.
        sum_floors = sum(floors)
        residual_cents = round((txn_amount - sum_floors) / _CENT)
        # residual_cents should be a non-negative integer (floors <= exact).
        residual_cents = int(residual_cents)

        # Sort indices by descending remainder; ties broken by subscription_id
        # for determinism.
        order = sorted(
            range(len(subs)),
            key=lambda i: (remainders[i], subs[i]["subscription_id"]),
            reverse=True,
        )

        allocated: list[Decimal] = list(floors)
        for i in range(residual_cents):
            allocated[order[i]] += _CENT

        # Sanity check (should always pass given correct arithmetic).
        assert sum(allocated) == txn_amount, (
            f"Allocation sum {sum(allocated)} != transaction amount {txn_amount}"
        )

        # ------------------------------------------------------------------
        # Build result
        # ------------------------------------------------------------------
        result: list[dict] = []
        for idx, s in enumerate(subs):
            effective_pct = (s["weight"] / total_weight * Decimal("100")).quantize(
                Decimal("0.000001")
            )
            result.append(
                {
                    "subscription_id": s["subscription_id"],
                    "allocated_amount": str(allocated[idx]),
                    "ownership_pct": str(effective_pct),
                    "entity_id": s["entity_id"],
                }
            )

        return result


async def allocate_transaction(
    pool: asyncpg.Pool,
    transaction_id: str,
    actor_user_id: str,
) -> list[dict]:
    """Compute and persist allocations for a transaction.

    Atomically:
    1. Deletes any existing active allocation rows for the transaction.
    2. Inserts the newly computed allocation rows.
    3. Updates the transaction status to 'allocated'.

    Verifies that SUM(allocated_amount) in the DB equals the transaction amount.

    Returns the persisted allocation rows as a list of dicts.
    """
    allocations = await compute_allocations(pool, transaction_id)

    async with pool.acquire() as conn:
        # Load transaction metadata needed for the DB writes.
        txn_row = await conn.fetchrow(
            """
            SELECT id, org_id, spv_id, amount
            FROM spv_transactions
            WHERE id = $1
            """,
            transaction_id,
        )
        if txn_row is None:
            raise ValueError(f"Transaction not found: {transaction_id}")

        org_id = txn_row["org_id"]
        spv_id = txn_row["spv_id"]
        txn_amount = Decimal(str(txn_row["amount"]))

        async with conn.transaction():
            # Delete existing active allocations (idempotent re-allocation).
            await conn.execute(
                """
                DELETE FROM spv_transaction_allocations
                WHERE transaction_id = $1
                  AND status = 'active'
                """,
                transaction_id,
            )

            # Insert new allocation rows.
            for alloc in allocations:
                await conn.execute(
                    """
                    INSERT INTO spv_transaction_allocations
                        (org_id, transaction_id, spv_id, subscription_id, entity_id,
                         allocated_amount, ownership_pct, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'active')
                    """,
                    org_id,
                    transaction_id,
                    spv_id,
                    alloc["subscription_id"],
                    alloc["entity_id"],
                    Decimal(alloc["allocated_amount"]),
                    Decimal(alloc["ownership_pct"]),
                )

            # Update transaction status.
            await conn.execute(
                """
                UPDATE spv_transactions
                SET status = 'allocated',
                    allocated_at = now(),
                    updated_at = now()
                WHERE id = $1
                """,
                transaction_id,
            )

            # Verify the persisted sum matches the transaction amount exactly.
            sum_row = await conn.fetchrow(
                """
                SELECT SUM(allocated_amount) AS total
                FROM spv_transaction_allocations
                WHERE transaction_id = $1
                  AND status = 'active'
                """,
                transaction_id,
            )
            db_total = Decimal(str(sum_row["total"])) if sum_row["total"] is not None else Decimal("0")
            if db_total != txn_amount:
                raise AssertionError(
                    f"Persisted allocation sum {db_total} != "
                    f"transaction amount {txn_amount} for transaction {transaction_id}"
                )

    # Write audit log outside the DB transaction (uses its own pool connection).
    await write_audit_log(
        org_id=org_id,
        action="allocate",
        table_name="spv_transaction",
        record_id=transaction_id,
        new={"allocation_count": len(allocations)},
        actor=actor_user_id,
    )

    return allocations


async def post_transaction(
    pool: asyncpg.Pool,
    transaction_id: str,
    actor_user_id: str,
) -> None:
    """Post an allocated transaction, updating subscription funded balances
    for capital calls.

    The transaction must be in status='allocated'; raises ValueError otherwise.

    For 'capital_call' transactions, increments spv_subscriptions.funded_amount
    by each subscriber's allocation amount (direct running-balance update, not
    bi-temporal, per sprint spec).

    For 'distribution' and other types, no subscription columns are updated.
    """
    async with pool.acquire() as conn:
        # Load transaction.
        txn_row = await conn.fetchrow(
            """
            SELECT id, org_id, status, txn_type
            FROM spv_transactions
            WHERE id = $1
            """,
            transaction_id,
        )
        if txn_row is None:
            raise ValueError(f"Transaction not found: {transaction_id}")

        org_id = txn_row["org_id"]
        current_status: str = txn_row["status"]
        txn_type: str = txn_row["txn_type"]

        if current_status != "allocated":
            raise ValueError(
                f"Transaction {transaction_id} must be in status 'allocated' to post; "
                f"current status is '{current_status}'"
            )

        # Load active allocations.
        alloc_rows = await conn.fetch(
            """
            SELECT id, subscription_id, allocated_amount
            FROM spv_transaction_allocations
            WHERE transaction_id = $1
              AND status = 'active'
            """,
            transaction_id,
        )

        async with conn.transaction():
            # Mark transaction as posted.
            await conn.execute(
                """
                UPDATE spv_transactions
                SET status = 'posted',
                    posted_at = now(),
                    updated_at = now()
                WHERE id = $1
                """,
                transaction_id,
            )

            if txn_type == "capital_call":
                for alloc in alloc_rows:
                    sub_id = alloc["subscription_id"]
                    alloc_amount = Decimal(str(alloc["allocated_amount"]))

                    # Increment the subscription's funded_amount (running balance).
                    await conn.execute(
                        """
                        UPDATE spv_subscriptions
                        SET funded_amount = funded_amount + $1
                        WHERE id = $2
                        """,
                        alloc_amount,
                        sub_id,
                    )

                    # Audit each funded_amount increment.
                    await write_audit_log(
                        org_id=org_id,
                        action="capital_call_funded",
                        table_name="spv_subscriptions",
                        record_id=str(sub_id),
                        new={
                            "transaction_id": transaction_id,
                            "allocation_id": str(alloc["id"]),
                            "funded_increment": str(alloc_amount),
                        },
                        actor=actor_user_id,
                    )

            # For 'distribution', 'fee', 'return_of_capital': no subscription
            # columns are updated — outbound flows do not change funded_amount.

    # Top-level post audit log.
    await write_audit_log(
        org_id=org_id,
        action="post",
        table_name="spv_transaction",
        record_id=transaction_id,
        new={"txn_type": txn_type, "allocation_count": len(alloc_rows)},
        actor=actor_user_id,
    )
