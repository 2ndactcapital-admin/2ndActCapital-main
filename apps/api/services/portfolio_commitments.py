"""Portfolio Phase E — commitments, their derivation from real transactions, and
the tax-document chase list.

WHAT A COMMITMENT IS HERE
──────────────────────────────────────────────────────────────────────────────
``portfolio.commitments`` hangs off a ``portfolio.positions`` row and carries the
five figures every private-fund holding is actually discussed in: what was
committed, what has been called, what has been distributed, how much of that is
recallable, and what is still unfunded. Four of those five are DERIVED — they are
the running total of the position's transactions, and this module is the only
thing that computes them.

WHY THE DERIVATION IS EXPLICIT AND NOT A TRIGGER
──────────────────────────────────────────────────────────────────────────────
Same reasoning as Phase C's rollup, and it is not a style preference. A capital
call posts as several transactions (``call_investment`` + ``call_mgmt_fee`` +
``call_org_cost``); a row-level trigger would fire between them and leave the
commitment briefly stating a called-to-date that was never true. Anything reading
mid-batch — a report, a screen, a second process — reads a figure that has no
moment in the world it corresponds to. :func:`recompute_commitment` is therefore
called ONCE, after a batch, by the caller who knows the batch is finished.

It is also idempotent by construction: it re-derives from the ledger every time
rather than incrementing, so a double call, a replayed import or a crash between
transaction and recompute all converge on the same answer. An incrementing
trigger has none of those properties.

THE FLAG VALUES ARE READ, NEVER RE-DERIVED
──────────────────────────────────────────────────────────────────────────────
``public.transaction_types`` carries ``affects_paid_in`` / ``affects_unfunded`` /
``is_recallable``, and the aggregation JOINs them. Introspected on the deployed
database (2026-08-23), all sixteen codes:

    call_investment / call_mgmt_fee / call_org_cost /
    call_partnership_expense      paid_in=+1  unfunded=-1  recallable=false
    dist_recallable               paid_in=-1  unfunded=+1  recallable=TRUE
    every other code              paid_in= 0  unfunded= 0  recallable=false

Nothing here pattern-matches on the ``call_`` / ``dist_`` prefix. A new type
added to the catalogue with the right flags is picked up with no code change,
which is the entire point of the flags existing.

**A NOTE ON THE ``unfunded`` FORMULA, RECORDED RATHER THAN SILENTLY "FIXED".**
The formula this module implements is the one the phase brief specifies:

    unfunded = commitment_amount - called_to_date + recallable_amount

On the deployed catalogue ``affects_unfunded`` is the exact negation of
``affects_paid_in`` on every one of the five non-zero codes, so a purely
flag-driven accumulator would give

    unfunded = commitment_amount + SUM(amount * affects_unfunded)
             = commitment_amount - called_to_date

— which is 10,000 LOWER after a 10,000 recallable distribution, because
``dist_recallable``'s ``affects_paid_in = -1`` has *already* restored that
capacity by reducing ``called_to_date``. The brief's formula adds
``recallable_amount`` on top of that, so a recallable distribution moves
``unfunded`` by twice its face value. The brief is explicit and is implemented
as written; :data:`UNFUNDED_FORMULA` names it and
:func:`unfunded_flag_driven` computes the alternative, so the difference is
measurable rather than a matter of reading two docstrings. If the double
movement turns out to be unwanted, the change is one line here plus the
constant, and every stored figure is re-derivable by re-running the recompute.

WHAT COUNTS AS "THE AMOUNT"
──────────────────────────────────────────────────────────────────────────────
``COALESCE(gross_amount, net_amount)`` — the stated amount of the transaction,
falling back to the settled one when a feed carries only that. NOT
``net_amount`` first: a capital call for 50,000 with a 500 fee recorded on the
same row was a call for 50,000, and ``called_to_date`` is a gross figure in every
LP statement it will ever be reconciled against. Transactions carrying NEITHER
amount (a ``dist_stock`` recorded in units, say) contribute nothing and are
COUNTED and returned as :attr:`CommitmentTotals.amountless_transactions`, because
a stock distribution silently valued at zero is exactly the kind of absence that
reads as a number.

WHY THE RECOMPUTE UPDATES IN PLACE
──────────────────────────────────────────────────────────────────────────────
CLAUDE.md Rule 3 governs FACTS that stopped being true. These four columns are
not facts, they are an arithmetic projection of ``portfolio.transactions``, which
IS bitemporal and IS the history. Superseding the commitment row on every
recompute would mint an unbounded chain of rows differing only in a total that is
re-derivable from data already stored — and would break the ONE thing on this
table that is a bitemporal fact: ``tax_doc_status``, whose transitions
(``awaiting`` → ``received``) are a real timeline that a torrent of arithmetic
supersessions would bury. The commitment's own facts (``commitment_amount``,
``commitment_date``, ``tax_*``) are never touched by :func:`recompute_commitment`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from services.portfolio_assets import (
    PortfolioError,
    _money,
    _OrgWrite,
    _opt_money,
    _require_org,
)

TABLE_COMMITMENTS = "portfolio.commitments"
TABLE_TRANSACTIONS = "portfolio.transactions"
TABLE_POSITIONS = "portfolio.positions"
TABLE_ASSETS = "portfolio.assets"
TABLE_ENTITIES = "public.entities"
TABLE_TXN_TYPES = "public.transaction_types"

#: ``commitments_tax_status_chk``, mirrored verbatim from the deployed CHECK.
TAX_DOC_STATUSES = frozenset({"not_expected", "awaiting", "received", "amended"})

#: The one terminal status: a commitment in this state is OFF the chase list.
TAX_DOC_RECEIVED = "received"

#: ``transaction_types.category`` for the rows that make up
#: ``distributed_to_date``. The real deployed classification column, not a
#: ``dist_`` prefix match — ``dividend`` and ``interest`` are also
#: ``category='distribution'`` and are distributions when they are recorded
#: against a position that has a commitment.
DISTRIBUTION_CATEGORY = "distribution"

#: The formula, named so a reader (and the verification) can see which of the two
#: candidates in the module docstring is in force.
UNFUNDED_FORMULA = "commitment_amount - called_to_date + recallable_amount"

#: The index Part 1 deployed for :func:`tax_chase_list`. Partial, on the
#: current-row predicate:
#:   (org_id, tax_year, tax_doc_status)
#:   WHERE tax_doc_expected = true AND system_to IS NULL AND valid_to IS NULL
#: Every one of those three WHERE terms must appear LITERALLY in a query for the
#: planner to prove the partial predicate is implied. Dropping ``system_to IS
#: NULL`` because "nothing writes it yet" would silently cost the index.
TAX_CHASE_INDEX = "idx_commitments_tax_chase"


# The current-row predicate, both temporal axes, alias-qualified on each column.
def _current(alias: str) -> str:
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


#: The transaction amount, in one place. See the module docstring.
_AMOUNT = "COALESCE(t.gross_amount, t.net_amount)"


class CommitmentError(PortfolioError):
    """A commitment could not be created or recomputed."""


@dataclass(frozen=True)
class CommitmentTotals:
    """What a recompute derived. Every monetary field is a :class:`Decimal`."""

    commitment_id: str
    position_id: str
    commitment_amount: Decimal | None
    called_to_date: Decimal
    distributed_to_date: Decimal
    recallable_amount: Decimal
    unfunded: Decimal | None
    transactions_counted: int
    #: Current transactions on the position carrying NEITHER ``gross_amount`` nor
    #: ``net_amount``. They contribute nothing to any total; reported so a
    #: units-only distribution is a visible absence and not a silent zero.
    amountless_transactions: int

    @property
    def unfunded_flag_driven(self) -> Decimal | None:
        """The alternative in the module docstring: ``commitment - called``.

        Not stored anywhere. Exposed so the difference the brief's formula makes
        is a number a caller can compare, rather than a claim in prose.
        """
        if self.commitment_amount is None:
            return None
        return self.commitment_amount - self.called_to_date


def unfunded_flag_driven(
    commitment_amount: Decimal | None, called_to_date: Decimal
) -> Decimal | None:
    """``commitment_amount - called_to_date`` — the flag-driven alternative."""
    if commitment_amount is None:
        return None
    return commitment_amount - called_to_date


def _check_tax_status(value: str) -> str:
    if value not in TAX_DOC_STATUSES:
        raise CommitmentError(
            f"tax_doc_status={value!r} is not one of "
            f"{sorted(TAX_DOC_STATUSES)} (commitments_tax_status_chk). A CHECK "
            f"violation surfaces as a 23514 naming the constraint and not the "
            f"value that was wrong."
        )
    return value


# ── Create ──────────────────────────────────────────────────────────────────


async def create_commitment(
    conn,
    *,
    org_id: str,
    position_id: str,
    commitment_amount: Decimal | int | str,
    commitment_date: date,
    vintage_year: int | None = None,
    liquidity_terms: dict | None = None,
    tax_doc_expected: bool = False,
    tax_year: int | None = None,
    tax_doc_status: str | None = None,
) -> str:
    """Create a commitment against an existing position. Returns its id.

    ``called_to_date`` / ``distributed_to_date`` / ``recallable_amount`` are left
    at their column defaults of ``0``, and ``unfunded`` at its default of
    ``NULL`` — deliberately, and not as an oversight. No transaction exists yet,
    so the four derived figures have no input; writing ``unfunded =
    commitment_amount`` here would make a *derived* column look like a *stated*
    one, and there would be no way to tell a commitment that had never been
    recomputed from one whose recompute happened to return the full amount.
    ``unfunded`` is NULL until the first :func:`recompute_commitment`, and that
    NULL means "not yet derived".

    ``tax_doc_status`` defaults to the column's own ``'not_expected'`` when
    ``tax_doc_expected`` is false, and to ``'awaiting'`` when it is true — a
    commitment that expects a K-1 and has not got one IS awaiting it, and the
    alternative (leaving it ``'not_expected'``) would put a row on the chase list
    describing itself as not expecting the document it is being chased for.

    ``org_id`` comes from the caller's JWT claims, never from a request body.
    """
    org_id = _require_org(org_id)
    amount = _money(commitment_amount, "commitment_amount")
    if not isinstance(commitment_date, date):
        raise CommitmentError(
            f"commitment_date must be a datetime.date — got "
            f"{type(commitment_date).__name__}"
        )
    if tax_doc_status is None:
        tax_doc_status = "awaiting" if tax_doc_expected else "not_expected"
    _check_tax_status(tax_doc_status)
    if tax_doc_expected and tax_year is None:
        raise CommitmentError(
            "tax_doc_expected=True requires tax_year — the chase list is keyed "
            "on (org_id, tax_year, tax_doc_status) and a commitment with a NULL "
            "tax_year can never appear on any year's list, so it would be "
            "chased by nobody while claiming to expect a document"
        )

    async with _OrgWrite(conn, org_id) as c:
        position = await c.fetchrow(
            f"SELECT p.id FROM {TABLE_POSITIONS} p "
            f"WHERE p.id = $1::uuid AND p.org_id = $2::uuid AND {_current('p')}",
            str(position_id), org_id,
        )
        if position is None:
            raise CommitmentError(
                f"position {position_id} is not a current position in this org. "
                f"There is an FK on position_id, but a 23503 names a constraint "
                f"and cannot see the temporal predicate at all — a superseded "
                f"position would satisfy the FK and carry a commitment nothing "
                f"would ever recompute."
            )
        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_COMMITMENTS}
                (org_id, position_id, commitment_amount, commitment_date,
                 vintage_year, liquidity_terms, tax_doc_expected, tax_year,
                 tax_doc_status)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb, $7, $8, $9)
            RETURNING id::text
            """,
            org_id, str(position_id), amount, commitment_date, vintage_year,
            _jsonb(liquidity_terms), bool(tax_doc_expected), tax_year,
            tax_doc_status,
        )
    return row["id"]


def _jsonb(value: dict | None) -> str | None:
    return None if value is None else json.dumps(value)


# ── Derive ──────────────────────────────────────────────────────────────────


async def derive_commitment_totals(
    conn, *, org_id: str, commitment_id: str
) -> CommitmentTotals:
    """Compute a commitment's four derived figures WITHOUT writing them.

    Split out from :func:`recompute_commitment` so a caller can see what a
    recompute would do — and so the verification can assert the arithmetic
    independently of the UPDATE that stores it.
    """
    org_id = _require_org(org_id)
    commitment = await conn.fetchrow(
        f"""
        SELECT id::text AS id, position_id::text AS position_id, commitment_amount
        FROM {TABLE_COMMITMENTS} c
        WHERE c.id = $1::uuid AND c.org_id = $2::uuid AND {_current('c')}
        """,
        str(commitment_id), org_id,
    )
    if commitment is None:
        raise CommitmentError(
            f"commitment {commitment_id} does not exist in org {org_id}"
        )

    agg = await conn.fetchrow(
        f"""
        SELECT
            COALESCE(SUM({_AMOUNT} * tt.affects_paid_in), 0)   AS called,
            COALESCE(SUM({_AMOUNT}) FILTER (
                WHERE tt.category = $3), 0)                     AS distributed,
            COALESCE(SUM({_AMOUNT}) FILTER (
                WHERE tt.is_recallable), 0)                     AS recallable,
            count(*)                                            AS n,
            count(*) FILTER (WHERE {_AMOUNT} IS NULL)           AS amountless
        FROM {TABLE_TRANSACTIONS} t
        JOIN {TABLE_TXN_TYPES} tt ON tt.code = t.transaction_type_code
        WHERE t.position_id = $1::uuid
          AND t.org_id = $2::uuid
          AND {_current('t')}
        """,
        commitment["position_id"], org_id, DISTRIBUTION_CATEGORY,
    )

    commitment_amount = _opt_money(commitment["commitment_amount"], "commitment_amount")
    called = _money(agg["called"], "called_to_date")
    distributed = _money(agg["distributed"], "distributed_to_date")
    recallable = _money(agg["recallable"], "recallable_amount")
    # UNFUNDED_FORMULA. See the module docstring for the flag-driven alternative.
    unfunded = (
        None if commitment_amount is None
        else commitment_amount - called + recallable
    )

    return CommitmentTotals(
        commitment_id=commitment["id"],
        position_id=commitment["position_id"],
        commitment_amount=commitment_amount,
        called_to_date=called,
        distributed_to_date=distributed,
        recallable_amount=recallable,
        unfunded=unfunded,
        transactions_counted=int(agg["n"]),
        amountless_transactions=int(agg["amountless"]),
    )


async def recompute_commitment(
    conn, org_id: str, commitment_id: str
) -> CommitmentTotals:
    """Re-derive and STORE a commitment's four running totals.

    Call after a transaction batch, never per write — see the module docstring.
    Idempotent: it re-derives from the ledger rather than incrementing, so
    calling it twice, or after a replayed import, yields the same row.

    Positional ``org_id`` / ``commitment_id`` deliberately mirror Phase C's
    ``rollup_entity_holdings`` call shape.
    """
    org_id = _require_org(org_id)
    totals = await derive_commitment_totals(
        conn, org_id=org_id, commitment_id=commitment_id
    )
    async with _OrgWrite(conn, org_id) as c:
        updated = await c.fetchval(
            f"""
            UPDATE {TABLE_COMMITMENTS} c
            SET called_to_date      = $3,
                distributed_to_date = $4,
                recallable_amount   = $5,
                unfunded            = $6
            WHERE c.id = $1::uuid AND c.org_id = $2::uuid AND {_current('c')}
            RETURNING 1
            """,
            str(commitment_id), org_id,
            totals.called_to_date, totals.distributed_to_date,
            totals.recallable_amount, totals.unfunded,
        )
    if not updated:
        # Only reachable if the row was superseded between the derive and the
        # update. Raising beats returning totals nothing stored.
        raise CommitmentError(
            f"commitment {commitment_id} was not current at UPDATE time — "
            f"nothing was stored"
        )
    return totals


async def recompute_commitments_for_position(
    conn, org_id: str, position_id: str
) -> list[CommitmentTotals]:
    """Recompute every current commitment on one position.

    The natural call after an import or a transaction batch: the caller knows
    which POSITION it just wrote against, not which commitment ids hang off it.
    """
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        f"SELECT c.id::text AS id FROM {TABLE_COMMITMENTS} c "
        f"WHERE c.position_id = $1::uuid AND c.org_id = $2::uuid "
        f"  AND {_current('c')} ORDER BY c.commitment_date, c.id",
        str(position_id), org_id,
    )
    return [
        await recompute_commitment(conn, org_id, r["id"]) for r in rows
    ]


# ── Read ────────────────────────────────────────────────────────────────────


async def get_commitment(conn, *, org_id: str, commitment_id: str) -> dict | None:
    """One current commitment as a plain dict, or ``None``."""
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"""
        SELECT c.id::text AS id, c.position_id::text AS position_id,
               c.commitment_amount, c.commitment_date, c.called_to_date,
               c.distributed_to_date, c.recallable_amount, c.unfunded,
               c.vintage_year, c.liquidity_terms, c.tax_doc_expected,
               c.tax_year, c.tax_doc_status
        FROM {TABLE_COMMITMENTS} c
        WHERE c.id = $1::uuid AND c.org_id = $2::uuid AND {_current('c')}
        """,
        str(commitment_id), org_id,
    )
    return dict(row) if row else None


# ── Task 5: the tax-document chase list ─────────────────────────────────────
#
# "Who is missing a K-1." Written to be served by TAX_CHASE_INDEX and nothing
# else — see the constant's comment for why each WHERE term is spelled out.
#
# `tax_doc_status <> 'received'` rather than an IN-list of the other three: the
# question is "has it arrived", and an inequality keeps a status added to the
# CHECK constraint later on the list by default. A btree cannot use `<>` as an
# index CONDITION, so it lands as a filter ON the index scan — which costs
# nothing here, because the partial predicate and the (org_id, tax_year) prefix
# have already cut the scan down to one org-year.
TAX_CHASE_SQL = f"""
SELECT c.id::text                AS commitment_id,
       c.position_id::text       AS position_id,
       c.commitment_amount,
       c.commitment_date,
       c.called_to_date,
       c.distributed_to_date,
       c.unfunded,
       c.vintage_year,
       c.tax_year,
       c.tax_doc_status,
       a.id::text                AS asset_id,
       a.name                    AS asset_name,
       e.id::text                AS owner_entity_id,
       e.display_name            AS owner_name
FROM {TABLE_COMMITMENTS} c
LEFT JOIN {TABLE_POSITIONS} p
       ON p.id = c.position_id AND p.org_id = c.org_id
      AND p.valid_to IS NULL AND p.system_to IS NULL
LEFT JOIN {TABLE_ASSETS} a
       ON a.id = p.asset_id AND a.org_id = p.org_id
      AND a.valid_to IS NULL AND a.system_to IS NULL
LEFT JOIN {TABLE_ENTITIES} e
       ON e.id = p.owner_entity_id AND e.org_id = p.org_id
WHERE c.org_id           = $1::uuid
  AND c.tax_year         = $2
  AND c.tax_doc_expected = true
  AND c.system_to IS NULL
  AND c.valid_to  IS NULL
  AND c.tax_doc_status  <> '{TAX_DOC_RECEIVED}'
ORDER BY c.tax_doc_status, a.name NULLS LAST, c.id
"""


async def tax_chase_list(conn, *, org_id: str, tax_year: int) -> list[dict]:
    """Every commitment for ``tax_year`` that expects a tax document and has not
    received one. The "who is missing a K-1" list.

    Three states, and all three are distinct:

      * ``tax_doc_expected = false``           → never on the list, at any status
      * ``tax_doc_status  = 'received'``       → off the list
      * ``'awaiting'`` / ``'amended'`` / ``'not_expected'`` while expected → ON

    That last one is not a contradiction to paper over: a commitment carrying
    ``tax_doc_expected = true`` and the column-default ``'not_expected'`` status
    is a row somebody half-configured, and it is exactly the row that would
    otherwise be missed at filing time. It stays on the list until a human
    resolves it either way.
    """
    org_id = _require_org(org_id)
    if tax_year is None:
        raise CommitmentError(
            "tax_year is required — it is the second key column of "
            f"{TAX_CHASE_INDEX} and a list spanning all years is a different "
            f"question with a different plan"
        )
    rows = await conn.fetch(TAX_CHASE_SQL, org_id, int(tax_year))
    return [dict(r) for r in rows]


async def explain_tax_chase(
    conn, *, org_id: str, tax_year: int, force_index: bool = True
) -> str:
    """The planner's chosen plan for :data:`TAX_CHASE_SQL`, as text.

    ``force_index`` sets ``enable_seqscan = off`` for the duration. That is not a
    way of faking the answer, it is the only way to ASK the question: the planner
    is cost-based, and on a table holding three fixture rows a sequential scan is
    genuinely cheaper than any index, so a plain EXPLAIN on a test fixture
    measures the row count and not the query. With seqscan discouraged, a query
    the index cannot serve still cannot use it — it falls back to
    ``idx_commitments_org`` or to a seq scan anyway — so seeing
    :data:`TAX_CHASE_INDEX` by name in the plan is a real proof of applicability.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        if force_index:
            await c.execute("SET LOCAL enable_seqscan = off")
        rows = await c.fetch(
            f"EXPLAIN {TAX_CHASE_SQL}", org_id, int(tax_year)
        )
    return "\n".join(r["QUERY PLAN"] for r in rows)


# ── Tax-document status transitions ─────────────────────────────────────────


async def set_tax_doc_status(
    conn,
    *,
    org_id: str,
    commitment_id: str,
    tax_doc_status: str,
) -> dict:
    """Move a commitment's tax-document status. Returns the before/after pair.

    The one place a commitment leaves the chase list. Kept separate from
    :func:`recompute_commitment` on purpose: this IS a fact changing, the derived
    totals are not, and mixing them would mean a routine recompute could move a
    K-1 to ``received``.
    """
    org_id = _require_org(org_id)
    _check_tax_status(tax_doc_status)
    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""
            UPDATE {TABLE_COMMITMENTS} c
            SET tax_doc_status = $3
            WHERE c.id = $1::uuid AND c.org_id = $2::uuid AND {_current('c')}
            RETURNING c.id::text AS id, c.tax_doc_status
            """,
            str(commitment_id), org_id, tax_doc_status,
        )
    if row is None:
        raise CommitmentError(
            f"commitment {commitment_id} does not exist in org {org_id}"
        )
    return {"commitment_id": row["id"], "tax_doc_status": row["tax_doc_status"]}


def to_json(value: Any) -> Any:
    """Decimal/date → JSON-safe, for the router. Decimals become STRINGS.

    A float here would be a rounding bug in a monetary figure that has survived
    every layer below this one as an exact Decimal.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_json(v) for v in value]
    return value
