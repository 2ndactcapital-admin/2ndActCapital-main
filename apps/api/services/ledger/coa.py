"""Chart of accounts service.

Bi-temporal writes (same pattern as entity_relationships):
  to update, close the prior row with system_to = now(), then insert new row.
  Never UPDATE in place.
"""

from typing import Optional

_COA_FIELDS = (
    "id, org_id, code, name, account_type, normal_balance, "
    "tax_character_code, parent_code, is_capital_account, is_active, "
    "valid_from, valid_to, system_from, system_to"
)


async def list_accounts(
    pool, org_id: str, as_of: Optional[str] = None
) -> list[dict]:
    """Return active COA rows for an org.

    as_of (YYYY-MM-DD): time-travel — return rows current at that system date.
    Omit for live state (system_to IS NULL).
    """
    if as_of:
        query = (
            f"SELECT {_COA_FIELDS} FROM chart_of_accounts "
            "WHERE org_id = $1 "
            "  AND system_from::date <= $2::date "
            "  AND (system_to IS NULL OR system_to::date > $2::date) "
            "ORDER BY code"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, org_id, as_of)
    else:
        query = (
            f"SELECT {_COA_FIELDS} FROM chart_of_accounts "
            "WHERE org_id = $1 AND system_to IS NULL "
            "ORDER BY code"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, org_id)
    return [dict(r) for r in rows]


async def create_account(
    conn, org_id: str, data: dict, created_by: Optional[str] = None
) -> dict:
    row = await conn.fetchrow(
        "INSERT INTO chart_of_accounts "
        "(org_id, code, name, account_type, is_capital_account, tax_character_code, normal_balance) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "RETURNING *",
        org_id,
        data["code"],
        data["name"],
        data["account_type"],
        data.get("is_capital_account", False),
        data.get("tax_character_code"),
        data["normal_balance"],
    )
    return dict(row)


async def update_account(
    conn, org_id: str, account_id: str, data: dict, updated_by: Optional[str] = None
) -> dict:
    """Bi-temporal close-and-insert.  Closes the live row, inserts successor."""
    async with conn.transaction():
        old = await conn.fetchrow(
            "UPDATE chart_of_accounts SET system_to = now() "
            "WHERE id = $1 AND org_id = $2 AND system_to IS NULL "
            "RETURNING *",
            account_id, org_id,
        )
        if not old:
            raise LookupError(f"Account {account_id} not found or already superseded")

        o = dict(old)
        new_row = await conn.fetchrow(
            "INSERT INTO chart_of_accounts "
            "(org_id, code, name, account_type, is_capital_account, tax_character_code, normal_balance) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "RETURNING *",
            org_id,
            data.get("code", o["code"]),
            data.get("name", o["name"]),
            data.get("account_type", o["account_type"]),
            data.get("is_capital_account", o["is_capital_account"]),
            data.get("tax_character_code", o["tax_character_code"]),
            data.get("normal_balance", o["normal_balance"]),
        )
        return dict(new_row)
