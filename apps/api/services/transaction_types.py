"""Transaction type reference service (Sprint 19).

transaction_types is a global reference table — rows are not org-scoped.
The org_id parameter is accepted for API compatibility but is NOT used in
the WHERE clause.
"""

from typing import Optional


async def get_types(
    pool_or_conn,
    org_id: str,
    category: Optional[str] = None,
    security_type: Optional[str] = None,
) -> list[dict]:
    """Return active transaction types filtered by category and/or security_type.

    applies_to_security_types containment: if the column is non-empty, only
    include the type when security_type is in the list.  A null/empty list
    means the type applies to all security types.
    """
    conditions = ["is_active = true"]
    params: list = []

    if category:
        params.append(category)
        conditions.append(f"category = ${len(params)}")

    query = (
        "SELECT id, code, label, category, direction, "
        "affects_paid_in, affects_unfunded, affects_nav, is_recallable, "
        "performance_impact, applies_to_security_types, amount_basis, "
        "display_order, notes, created_at "
        "FROM transaction_types "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY display_order ASC NULLS LAST, label ASC"
    )

    has_acquire = hasattr(pool_or_conn, "acquire")
    if has_acquire:
        async with pool_or_conn.acquire() as conn:
            rows = await conn.fetch(query, *params)
    else:
        rows = await pool_or_conn.fetch(query, *params)

    result = []
    for row in rows:
        d = dict(row)
        applies_to = d.get("applies_to_security_types") or []
        if security_type and applies_to:
            if security_type not in applies_to:
                continue
        d["applies_to_security_types"] = applies_to
        result.append(d)

    return result
