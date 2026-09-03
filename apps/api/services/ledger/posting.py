"""Posting engine.

build_entry — resolves the posting template, expands template lines into a
  draft journal entry (posted_at IS NULL) with balanced journal_lines.
post — delegates to fn_post_journal_entry (DB validates balance, sets posted_at).
reverse — delegates to fn_reverse_journal_entry (DB creates mirrored entry).

Money: Decimal throughout — no floats.
Immutability: never DELETE entries or lines; reversal only.
Tenancy: org_id resolved from the VEHICLE's own row; never accepted from caller.
         fn_validate_line_org trigger enforces account org matches entry org.

Schema facts (from snapshot):
  journal_entries: id, org_id, vehicle_id, vehicle_kind, entry_date,
    ledger_basis, transaction_type_code, memo, source_event_id,
    reverses_entry_id, reversal_reason, posted_at, posted_by, created_at,
    created_by  (no amount, no dims, no template_id)
  journal_lines: id, entry_id, line_no, account_id, debit, credit,
    currency_code, dim_member_series_id, dim_investment_id, dim_tax_lot_id, memo
    (no org_id — derives from parent entry via trigger)
    debit and credit are NOT NULL DEFAULT 0; pass 0 on the unused side.
  posting_template_lines: id, template_id, line_no, account_code (text),
    side ('D'/'C'), amount_source, dimension_source


WHAT vehicle_id POINTS AT — fee43
──────────────────────────────────────────────────────────────────────────────
``journal_entries.vehicle_id`` has no foreign key and, until fee43, pointed at
an ``spvs.id`` by convention only. ``vehicle_kind`` makes the convention
explicit and adds a second target:

    'SPV'          vehicle_id -> spvs.id                     (unchanged)
    'LEDGER_BOOK'  vehicle_id -> ledger_books.id             (new)

``VEHICLE_KIND_SPV`` is the default everywhere in this module, so every caller
that predates fee43 keeps producing exactly the rows it produced before.

That default is NOT a column DEFAULT, deliberately. fee43 found that Part 1
added ``vehicle_kind`` as NOT NULL with no default, which broke BOTH writers
that never mentioned it — this module's ``build_entry`` and the database's own
``fn_reverse_journal_entry``. A column-level ``DEFAULT 'SPV'`` would have healed
both silently, and would go on silently mis-labelling the first ledger-book
writer that forgets the argument. The writers name the kind instead.
"""

from decimal import Decimal
from typing import Any, Optional

#: ``journal_entries_vehicle_kind_check``, verbatim.
VEHICLE_KIND_SPV = "SPV"
VEHICLE_KIND_LEDGER_BOOK = "LEDGER_BOOK"
VEHICLE_KINDS = (VEHICLE_KIND_SPV, VEHICLE_KIND_LEDGER_BOOK)

#: Where each kind's org_id is read from. ``ledger_books`` is bi-temporal on the
#: system axis (``ledger_books_code_uq`` is partial on ``system_to IS NULL``), so
#: a superseded book is not a valid posting target.
_ORG_SOURCE = {
    VEHICLE_KIND_SPV: "SELECT org_id FROM spvs WHERE id = $1::uuid",
    VEHICLE_KIND_LEDGER_BOOK: (
        "SELECT org_id FROM ledger_books "
        "WHERE id = $1::uuid AND system_to IS NULL"
    ),
}


async def _resolve_org(conn, vehicle_id: str, vehicle_kind: str = VEHICLE_KIND_SPV) -> str:
    """Resolve org_id from the vehicle's own row.  Raises if vehicle unknown."""
    if vehicle_kind not in VEHICLE_KINDS:
        raise ValueError(
            f"vehicle_kind {vehicle_kind!r} is not one of {VEHICLE_KINDS} — the "
            f"deployed journal_entries_vehicle_kind_check would refuse it"
        )
    row = await conn.fetchrow(_ORG_SOURCE[vehicle_kind], vehicle_id)
    if not row:
        raise LookupError(
            f"Vehicle {vehicle_id!r} not found as a live {vehicle_kind}"
        )
    return str(row["org_id"])


async def _resolve_template(conn, org_id: str, transaction_type_code: str):
    """Resolve posting template.

    vehicle_type-specific matching is a seam for a later sprint.
    Always uses vehicle_type_scope='any'.
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


async def build_entry_on_conn(
    conn,
    vehicle_id: str,
    transaction_type_code: str,
    entry_date: Any,
    amount: Any,
    dims: dict,
    ledger_basis: str = "GAAP",
    created_by: Optional[str] = None,
    vehicle_kind: str = VEHICLE_KIND_SPV,
    memo: Optional[str] = None,
    source_event_id: Optional[str] = None,
    reverse_sides: bool = False,
) -> dict:
    """:func:`build_entry`'s body, on a connection the CALLER owns.

    Opens no transaction of its own. That is the whole point: fee43 posts a fee
    run's journal entry inside the same transaction that moves the run to
    POSTED, so a GL failure rolls the status change back with it. A function
    that acquired its own connection could not participate in that atom.

    ``build_entry`` remains the pool-level entry point and is unchanged in
    behaviour — it acquires, opens a transaction, and calls this.

    ``reverse_sides`` swaps every template line's D and C. It exists because
    ``amount`` cannot be negative — ``jl_one_side`` requires both columns >= 0 —
    while a fee run legitimately can be: a REVERSAL run's lines are the original
    run's, sign-flipped. Booking ``abs(amount)`` with the sides swapped is the
    same entry the other way round, which is what a negative fee means.

    ``source_event_id`` records WHAT caused this entry. fee43 puts the fee_run
    or spv_carry_run id there, which is the only link back from a journal entry
    to the run that produced it — no other column relates the two.
    """
    amount = Decimal(str(amount))
    if amount == 0:
        raise ValueError("amount must be non-zero")
    if amount < 0:
        raise ValueError(
            "amount must be positive; pass abs(amount) with reverse_sides=True "
            "to book a negative (jl_one_side refuses a negative debit or credit)"
        )

    org_id = await _resolve_org(conn, vehicle_id, vehicle_kind)

    tmpl = await _resolve_template(conn, org_id, transaction_type_code)
    if not tmpl:
        raise LookupError(
            f"No active posting template for '{transaction_type_code}' "
            f"(org {org_id}, vehicle_type_scope='any')"
        )
    template_id = str(tmpl["id"])
    template_name = tmpl["name"]

    # Template lines store account_code (text); resolve account_id via COA JOIN.
    lines = await conn.fetch(
        "SELECT ptl.line_no, ptl.account_code, ptl.side, ptl.dimension_source, "
        "       coa.id AS account_id, coa.name AS account_name, "
        "       coa.tax_character_code "
        "FROM posting_template_lines ptl "
        "JOIN chart_of_accounts coa "
        "     ON coa.org_id = $1 AND coa.code = ptl.account_code "
        "     AND coa.system_to IS NULL AND coa.is_active = true "
        "WHERE ptl.template_id = $2::uuid "
        "ORDER BY ptl.line_no",
        org_id, template_id,
    )
    if not lines:
        raise ValueError(f"Template '{template_name}' has no lines")

    entry = await conn.fetchrow(
        "INSERT INTO journal_entries "
        "(org_id, vehicle_id, vehicle_kind, transaction_type_code, entry_date, "
        " ledger_basis, memo, created_by, source_event_id) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::date, $6, $7, $8::uuid, $9::uuid) "
        "RETURNING *",
        org_id, vehicle_id, vehicle_kind, transaction_type_code, entry_date,
        ledger_basis,
        # memo records which template produced this entry unless the caller has
        # something more specific to say (fee43 names the run).
        memo if memo is not None else template_name,
        created_by, source_event_id,
    )
    entry_id = str(entry["id"])

    inserted_lines: list[dict] = []
    for ln in lines:
        side = ln["side"]
        if reverse_sides:
            side = "C" if side == "D" else "D"
        debit = amount if side == "D" else Decimal("0")
        credit = amount if side == "C" else Decimal("0")

        dim_src = ln["dimension_source"]
        dim_member_series_id = (
            dims.get("member_series_id") if dim_src == "member_series" else None
        )
        dim_investment_id = (
            dims.get("investment_id") if dim_src == "investment" else None
        )

        jl = await conn.fetchrow(
            "INSERT INTO journal_lines "
            "(entry_id, line_no, account_id, debit, credit, "
            " dim_member_series_id, dim_investment_id) "
            "VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7) "
            "RETURNING *",
            entry_id, ln["line_no"], str(ln["account_id"]),
            debit, credit,
            dim_member_series_id, dim_investment_id,
        )
        row = dict(jl)
        row["account_code"] = ln["account_code"]
        row["account_name"] = ln["account_name"]
        row["tax_character_code"] = ln["tax_character_code"]
        row["side"] = side  # the side ACTUALLY booked, after any reversal
        inserted_lines.append(row)

    result = dict(entry)
    result["lines"] = inserted_lines
    result["template_name"] = template_name
    result["_amount"] = str(amount)  # echoed back for UI convenience
    return result


async def build_entry(
    pool,
    vehicle_id: str,
    transaction_type_code: str,
    entry_date: Any,
    amount: Any,
    dims: dict,
    ledger_basis: str = "GAAP",
    created_by: Optional[str] = None,
    vehicle_kind: str = VEHICLE_KIND_SPV,
) -> dict:
    """Build a draft journal entry with expanded lines.

    Amount drives line-level debit/credit — NOT stored on the entry row itself.
    Returns the entry dict with 'lines' (augmented with account_code, account_name).
    posted_at is NULL — caller must call post() to commit.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await build_entry_on_conn(
                conn, vehicle_id, transaction_type_code, entry_date, amount,
                dims, ledger_basis=ledger_basis, created_by=created_by,
                vehicle_kind=vehicle_kind,
            )


async def post_on_conn(conn, entry_id: str, user_id: str) -> dict:
    """:func:`post`'s body, on a connection the CALLER owns.  See
    :func:`build_entry_on_conn` for why this split exists."""
    await conn.execute(
        "SELECT fn_post_journal_entry($1::uuid, $2::uuid)", entry_id, user_id
    )
    row = await conn.fetchrow(
        "SELECT * FROM journal_entries WHERE id = $1::uuid", entry_id
    )
    return dict(row) if row else {"id": entry_id}


async def post(pool, entry_id: str, user_id: str) -> dict:
    """Post a draft entry.

    Calls fn_post_journal_entry which validates balance and raises if unbalanced.
    """
    async with pool.acquire() as conn:
        return await post_on_conn(conn, entry_id, user_id)


async def reverse(pool, entry_id: str, reason: str, user_id: str) -> dict:
    """Reverse a posted entry.

    Calls fn_reverse_journal_entry which creates a mirrored entry.
    Returns the new reversal entry.
    """
    async with pool.acquire() as conn:
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
