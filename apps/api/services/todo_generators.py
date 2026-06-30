"""Todo generators (Sprint 13).

ACTUAL generators:      pending_subscriptions, unsigned_documents
ANTICIPATED generators: upcoming_capital_calls, outstanding_tax_docs

All generators are idempotent via ON CONFLICT DO NOTHING.
"""
from typing import Callable


async def generate_pending_subscriptions(pool, user_id: str, org_id: str) -> int:
    """Todos for soft SPV subscriptions (uncommitted soft position)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ss.id AS sub_id, s.name AS spv_name
            FROM spv_subscriptions ss
            JOIN spvs s ON s.id = ss.spv_id
            JOIN member_investments mi ON mi.id = ss.member_investment_id
            WHERE mi.user_id = $1
              AND ss.org_id = $2
              AND ss.subscription_status = 'soft'
              AND ss.valid_to IS NULL
            """,
            user_id, org_id,
        )
        count = 0
        for row in rows:
            await conn.execute(
                """
                INSERT INTO member_todos
                    (org_id, user_id, kind, category, source,
                     related_type, related_id,
                     title, detail, action_key, action_params, priority, status)
                VALUES ($1, $2, 'actual', 'subscription', 'pending_subscriptions',
                        'spv_subscription', $3,
                        $4, $5, '/spvs', 'Review', 10, 'open')
                ON CONFLICT DO NOTHING
                """,
                org_id, user_id, row["sub_id"],
                f"Complete your subscription for {row['spv_name']}",
                "Your commitment is soft — sign to lock in your position.",
            )
            count += 1
    return count


async def generate_unsigned_documents(pool, user_id: str, org_id: str) -> int:
    """Todos for SPV documents sent to this member but not yet signed."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sd.id, sd.title AS doc_title, s.name AS spv_name
            FROM spv_documents sd
            JOIN spvs s ON s.id = sd.spv_id
            JOIN spv_subscriptions ss ON ss.id = sd.subscription_id
            JOIN member_investments mi ON mi.id = ss.member_investment_id
            WHERE mi.user_id = $1
              AND sd.org_id = $2
              AND sd.status = 'sent'
            """,
            user_id, org_id,
        )
        count = 0
        for row in rows:
            await conn.execute(
                """
                INSERT INTO member_todos
                    (org_id, user_id, kind, category, source,
                     related_type, related_id,
                     title, detail, action_key, action_params, priority, status)
                VALUES ($1, $2, 'actual', 'document', 'unsigned_documents',
                        'spv_document', $3,
                        $4, $5, '/spvs', 'Review documents', 20, 'open')
                ON CONFLICT DO NOTHING
                """,
                org_id, user_id, row["id"],
                f"Sign: {row['doc_title']} — {row['spv_name']}",
                "A document is waiting for your signature.",
            )
            count += 1
    return count


async def generate_upcoming_capital_calls(pool, user_id: str, org_id: str) -> int:
    # TODO: capital_calls table not yet deployed
    return 0


async def generate_outstanding_tax_docs(pool, user_id: str, org_id: str) -> int:
    # TODO: tax_documents table not yet deployed
    return 0


GENERATORS: list[tuple[str, Callable]] = [
    ("actual", generate_pending_subscriptions),
    ("actual", generate_unsigned_documents),
    ("anticipated", generate_upcoming_capital_calls),
    ("anticipated", generate_outstanding_tax_docs),
]


async def regenerate_todos(pool, user_id: str, org_id: str) -> dict:
    """Run all generators idempotently. Returns per-generator insert counts."""
    results: dict[str, int] = {}
    for _kind, gen in GENERATORS:
        try:
            n = await gen(pool, user_id, org_id)
            results[gen.__name__] = n
        except Exception as exc:
            print(f"[todos] {gen.__name__} failed: {exc}")
            results[gen.__name__] = 0
    return results
