"""Per-class and investment-level roll-up math (Sprint 23).

"Class" is the economic subdivision of one investment: several `spvs` rows
sharing a `deal_id`, each with its own carry / mgmt fee / close date.
`deals` is the Investment parent.

The Committed / Called / Distributed / Fees / Net computation built for the
SPV Ledger in Sprint 14 lives here now so the per-SPV ledger endpoint and the
investment roll-up share one implementation — the roll-up is the same math,
just summed across every class of a deal.

Money is Decimal end to end.  Callers that must emit float (the legacy
LedgerSummary schema) convert at their own boundary.
"""
from decimal import Decimal
from uuid import UUID

ZERO = Decimal("0")

# Money fields that make up a class row and roll up additively to the
# investment level.  `net` is derived, not summed — see _net().
MONEY_FIELDS = (
    "total_committed",
    "total_funded",
    "total_called",
    "total_distributed",
    "total_fees",
    "total_recallable",
)

# Committed / funded come from the live subscription rows — same source and
# same filter as GET /spvs/{id}/captable.
_SUBSCRIPTION_TOTALS = """
    SELECT
      COALESCE(SUM(sub.commitment_amount), 0) AS total_committed,
      COALESCE(SUM(sub.funded_amount), 0)     AS total_funded
    FROM spv_subscriptions sub
    WHERE sub.spv_id = s.id AND sub.org_id = s.org_id AND sub.valid_to IS NULL
"""

# Called / distributed / fees / recallable come from posted transactions,
# classified by transaction_types attributes where available and falling back
# to the legacy txn_type string for rows created before Sprint 22.
# This block is the Sprint 14 ledger summary verbatim.
_TRANSACTION_TOTALS = """
    SELECT
      COALESCE(SUM(CASE
        WHEN tt.affects_paid_in > 0 THEN t.amount
        WHEN t.transaction_type_id IS NULL AND t.txn_type = 'capital_call' THEN t.amount
        ELSE 0
      END), 0) AS total_called,
      COALESCE(SUM(CASE
        WHEN tt.affects_nav < 0 AND COALESCE(tt.is_recallable, false) = false THEN t.amount
        WHEN t.transaction_type_id IS NULL AND t.txn_type = 'distribution' THEN t.amount
        ELSE 0
      END), 0) AS total_distributed,
      COALESCE(SUM(CASE
        WHEN tt.category = 'fee' THEN t.amount
        WHEN t.transaction_type_id IS NULL AND t.txn_type IN ('fee', 'return_of_capital') THEN t.amount
        ELSE 0
      END), 0) AS total_fees,
      COALESCE(SUM(CASE
        WHEN COALESCE(tt.is_recallable, false) = true THEN t.amount
        ELSE 0
      END), 0) AS total_recallable
    FROM spv_transactions t
    LEFT JOIN transaction_types tt ON tt.id = t.transaction_type_id
    WHERE t.spv_id = s.id AND t.org_id = s.org_id AND t.status = 'posted'
"""

_CLASS_ROLLUP_SQL = f"""
    SELECT
      s.id            AS spv_id,
      s.deal_id,
      s.name          AS spv_name,
      s.class_label,
      s.spv_status    AS status,
      s.carry_pct,
      s.mgmt_fee_pct,
      s.close_date,
      s.target_raise,
      subs.total_committed,
      subs.total_funded,
      txns.total_called,
      txns.total_distributed,
      txns.total_fees,
      txns.total_recallable
    FROM spvs s
    LEFT JOIN LATERAL ({_SUBSCRIPTION_TOTALS}) subs ON TRUE
    LEFT JOIN LATERAL ({_TRANSACTION_TOTALS}) txns ON TRUE
    WHERE {{where}}
    ORDER BY s.class_label ASC NULLS FIRST, s.created_at ASC
"""


def _dec(v) -> Decimal:
    """Coerce a numeric column (or None) to Decimal without going via float."""
    if v is None:
        return ZERO
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _net(totals: dict) -> Decimal:
    """Net = called - distributed - fees (Sprint 14 definition)."""
    return totals["total_called"] - totals["total_distributed"] - totals["total_fees"]


def _class_row(row) -> dict:
    out = {
        "spv_id": row["spv_id"],
        "deal_id": row["deal_id"],
        "spv_name": row["spv_name"],
        "class_label": row["class_label"],
        "status": row["status"],
        "carry_pct": _dec(row["carry_pct"]) if row["carry_pct"] is not None else None,
        "mgmt_fee_pct": (
            _dec(row["mgmt_fee_pct"]) if row["mgmt_fee_pct"] is not None else None
        ),
        "close_date": row["close_date"],
        "target_raise": (
            _dec(row["target_raise"]) if row["target_raise"] is not None else None
        ),
    }
    for field in MONEY_FIELDS:
        out[field] = _dec(row[field])
    out["net"] = _net(out)
    return out


async def class_rollups(conn, org_id, *, deal_id=None, spv_id=None) -> list[dict]:
    """Per-class (per-SPV) roll-up rows, scoped to one deal or one SPV.

    org_id is always supplied by the caller from the authenticated request —
    never from a request body.
    """
    if (deal_id is None) == (spv_id is None):
        raise ValueError("class_rollups requires exactly one of deal_id / spv_id")

    if deal_id is not None:
        where = "s.deal_id = $1 AND s.org_id = $2"
        key = deal_id
    else:
        where = "s.id = $1 AND s.org_id = $2"
        key = spv_id

    rows = await conn.fetch(
        _CLASS_ROLLUP_SQL.format(where=where),
        key if not isinstance(key, str) else UUID(key),
        org_id if not isinstance(org_id, str) else UUID(org_id),
    )
    return [_class_row(r) for r in rows]


async def spv_totals(conn, org_id, spv_id) -> dict:
    """Totals for a single SPV — the per-class view used by the SPV ledger."""
    rows = await class_rollups(conn, org_id, spv_id=spv_id)
    if not rows:
        return {**{f: ZERO for f in MONEY_FIELDS}, "net": ZERO}
    return rows[0]


async def deal_rollup(conn, org_id, deal_id) -> dict:
    """Investment-level roll-up: totals across every class, plus each class row.

    Both views are returned — the aggregate alone loses the per-class detail
    that differing carry / fee / close dates make meaningful.
    """
    classes = await class_rollups(conn, org_id, deal_id=deal_id)

    totals = {field: sum((c[field] for c in classes), ZERO) for field in MONEY_FIELDS}
    totals["net"] = _net(totals)

    return {
        "deal_id": deal_id,
        "class_count": len(classes),
        "totals": totals,
        "classes": classes,
    }
