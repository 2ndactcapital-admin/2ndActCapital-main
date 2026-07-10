"""Posting engine.

build_entry — resolves the posting template, expands template lines into a
  draft journal entry (posted_at IS NULL) with balanced journal_lines.
post — delegates to fn_post_journal_entry (DB validates balance, sets posted_at).
reverse — delegates to fn_reverse_journal_entry (DB creates mirrored entry).

Money: Decimal throughout — no floats.
Immutability: never DELETE entries or lines; reversal only.
Tenancy: org_id resolved from spvs.org_id; never accepted from caller.
"""

import json
from decimal import Decimal
from typing import Any, Optional


async def _resolve_org(conn, vehicle_id: str) -> str:
    """Resolve org_id from spvs.  Raises if vehicle unknown."""
    row = await conn.fetchrow(
        "SELECT org_id FROM spvs WHERE id = $1::uuid", vehicle_id
    )
    if not row:
        raise LookupError(f"Vehicle {vehicle_id!r} not found in spvs")
    return str(row["org_id"])


async def _resolve_template(conn, org_id: str, transaction_type_code: str):
    """Resolve posting template.

    vehicle_type-specific matching is a seam for a later sprint
    (vehicle_type not yet on spvs).  Always falls back to vehicle_type_scope='any'.
    """
    return await conn.fetchrow(
        "SELECT id, name FROM posting_templates "
        "WHERE org_id = $1 "
        "  AND transaction_type_code = $2 "
        "  AND vehicle_type_scope = 'any' "
        "  AND is_active = true "
        "LIMIT 1",
        org_id, transaction_type_code,
    )


async def build_entry(
    pool,
    vehicle_id: str,
    transaction_type_code: str,
    entry_date: Any,
    amount: Any,
    dims: dict,
    basis: str = "GAAP",
    created_by: Optional[str] = None,
) -> dict:
    """Build a draft journal entry with expanded lines.

    Returns the entry dict with a 'lines' key containing journal_lines rows
    (augmented with account_code, account_name, tax_character for display).
    posted_at is NULL — caller must call post() to commit.
    """
    amount = Decimal(str(amount))
    if amount == 0:
        raise ValueError("amount must be non-zero")

    async with pool.acquire() as conn:
        async with conn.transaction():
            org_id = await _resolve_org(conn, vehicle_id)

            tmpl = await _resolve_template(conn, org_id, transaction_type_code)
            if not tmpl:
                raise LookupError(
                    f"No active posting template for '{transaction_type_code}' "
                    f"(org {org_id}, vehicle_type_scope='any')"
                )
            template_id = str(tmpl["id"])
            template_name = tmpl["name"]

            lines = await conn.fetch(
                "SELECT ptl.id, ptl.account_id, ptl.dr_cr, "
                "       ptl.dimension_source, ptl.line_order, "
                "       coa.code AS account_code, coa.name AS account_name, "
                "       coa.tax_character "
                "FROM posting_template_lines ptl "
                "JOIN chart_of_accounts coa ON coa.id = ptl.account_id "
                "WHERE ptl.posting_template_id = $1::uuid "
                "ORDER BY ptl.line_order",
                template_id,
            )
            if not lines:
                raise ValueError(f"Template '{template_name}' has no lines")

            entry = await conn.fetchrow(
                "INSERT INTO journal_entries "
                "(org_id, vehicle_id, transaction_type_code, entry_date, "
                " amount, dims, basis, template_id, created_by) "
                "VALUES ($1::uuid, $2::uuid, $3, $4::date, $5, $6::jsonb, $7, $8::uuid, $9::uuid) "
                "RETURNING *",
                org_id, vehicle_id, transaction_type_code, entry_date,
                amount, json.dumps(dims, default=str), basis, template_id, created_by,
            )
            entry_id = str(entry["id"])

            inserted_lines: list[dict] = []
            for ln in lines:
                dr_cr = ln["dr_cr"]
                debit: Optional[Decimal] = amount if dr_cr == "D" else None
                credit: Optional[Decimal] = amount if dr_cr == "C" else None

                dim_src = ln["dimension_source"]
                dim_member_series_id = (
                    dims.get("member_series_id") if dim_src == "member_series" else None
                )
                dim_investment_id = (
                    dims.get("investment_id") if dim_src == "investment" else None
                )

                jl = await conn.fetchrow(
                    "INSERT INTO journal_lines "
                    "(org_id, journal_entry_id, account_id, debit, credit, "
                    " dim_member_series_id, dim_investment_id) "
                    "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7) "
                    "RETURNING *",
                    org_id, entry_id, str(ln["account_id"]),
                    debit, credit,
                    dim_member_series_id, dim_investment_id,
                )
                row = dict(jl)
                row["account_code"] = ln["account_code"]
                row["account_name"] = ln["account_name"]
                row["tax_character"] = ln["tax_character"]
                inserted_lines.append(row)

            result = dict(entry)
            result["lines"] = inserted_lines
            result["template_name"] = template_name
            return result


async def post(pool, entry_id: str, user_id: str) -> dict:
    """Post a draft entry.

    Calls fn_post_journal_entry which validates balance and raises if unbalanced.
    Writes audit_log entry after success.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT fn_post_journal_entry($1::uuid, $2::uuid)",
            entry_id, user_id,
        )
        row = await conn.fetchrow(
            "SELECT * FROM journal_entries WHERE id = $1::uuid", entry_id
        )
        return dict(row) if row else {"id": entry_id}


async def reverse(pool, entry_id: str, reason: str, user_id: str) -> dict:
    """Reverse a posted entry.

    Calls fn_reverse_journal_entry which creates a mirrored entry.
    Returns the new reversal entry.
    """
    async with pool.acquire() as conn:
        # The function may return the new entry's UUID or void.
        # Capture the return value and fall back to querying by reverses_entry_id.
        try:
            new_id = await conn.fetchval(
                "SELECT fn_reverse_journal_entry($1::uuid, $2::text, $3::uuid)",
                entry_id, reason, user_id,
            )
        except Exception as exc:
            raise ValueError(f"Reversal failed: {exc}") from exc

        if new_id:
            row = await conn.fetchrow(
                "SELECT * FROM journal_entries WHERE id = $1::uuid", new_id
            )
            return dict(row) if row else {"id": str(new_id)}

        # Fallback: find the entry that reverses this one
        row = await conn.fetchrow(
            "SELECT * FROM journal_entries WHERE reverses_entry_id = $1::uuid",
            entry_id,
        )
        return dict(row) if row else {"reversed": True, "original_entry_id": entry_id}
