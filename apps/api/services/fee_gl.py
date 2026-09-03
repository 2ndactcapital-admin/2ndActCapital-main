"""fee43 — GL posting for fee runs and SPV carry runs.

This module answers design-doc open question #3. Until fee43,
``fee_runs.post_to_ledger`` was a marked stub that wrote nothing, and
``spv_carry_runs.post_run`` said in its own return payload that carry was
approved but not booked. Both now post for real, through this module.


WHERE A JOURNAL ENTRY GOES — THE WHOLE DECISION IN ONE PLACE
──────────────────────────────────────────────────────────────────────────────
``journal_entries.vehicle_id`` has no foreign key. Before fee43 it pointed at
an ``spvs.id`` by convention, in the single entry that had ever been written.
``vehicle_kind`` makes the convention explicit and admits a second target:

    vehicle_kind='SPV'          vehicle_id -> spvs.id
    vehicle_kind='LEDGER_BOOK'  vehicle_id -> ledger_books.id

There are two books, not one, and the reason is legal rather than tidy: the
registered investment adviser and the 501(c)(6) club are distinct entities that
happen to share an ``org_id``. Booking club dues into the RIA's book would
overstate the adviser's revenue and erase the club's.

``PRODUCT_TYPE_DESTINATION`` is the entire routing decision, one row per
DEPLOYED ``fee_schedules.product_type``, asserted against that vocabulary at
import so a new product type cannot be added without landing here.


WHAT CANNOT BE ROUTED, AND WHY IT IS SKIPPED RATHER THAN GUESSED — [F43-C]
──────────────────────────────────────────────────────────────────────────────
``fee_run_lines`` carries no SPV reference. Measured: no ``spv_id`` column, and
fee39 does not populate ``revenue_events.spv_id`` for fee-run-sourced rows
either. The only real join from a fee run line to a vehicle is
``fee_run_lines.entity_id -> spvs.vehicle_entity_id``, and that column is NULL
on every deployed SPV.

So ``SPV`` and ``STRUCTURED_INVESTMENT`` lines post only when that link happens
to be set. When it is not, the line is SKIPPED with a named reason that travels
back in the return payload and up through ``post_run``'s result — never routed
to the RIA book as a fallback. A fallback would put a vehicle's expense on the
adviser's income statement, which is precisely the error a reconciliation finds
a quarter later. The gap is reported, not papered over.

A skip is not the silent failure the sprint brief warns about: it is counted,
named, and returned. What must never happen — and what
:func:`post_fee_run` raises rather than allow — is a run reaching POSTED while a
line that COULD be routed failed to book.


NEGATIVE AMOUNTS
──────────────────────────────────────────────────────────────────────────────
``jl_one_side`` requires debit and credit both >= 0, so a negative fee cannot be
booked as a negative debit. A REVERSAL run's lines are the original's,
sign-flipped, and an ADJUSTMENT run can be negative too. Both book
``abs(total)`` with the template's sides swapped, which is the same entry the
other way round. ``services.ledger.posting.build_entry_on_conn`` takes
``reverse_sides`` for exactly this.


WHAT THIS MODULE DOES NOT DO
──────────────────────────────────────────────────────────────────────────────
* It does not populate ``journal_lines.dim_member_series_id``. Every template it
  uses declares ``dimension_source='none'``, because there is no
  ``dim_member_series`` table for that id to reference. ``v_capital_accounts``
  is therefore still empty and still structurally broken after this sprint —
  fee42b's finding stands unrepaired [F43-E].
* It does not write ``revenue_events.journal_entry_id``. That column exists and
  is exactly the revenue-to-GL link a reconciliation wants, but fee39 owns
  ``revenue_events`` and the sprint brief scopes its emission logic out. Left
  NULL deliberately, and reported [F43-F].
* It does not backfill. Runs POSTED before fee43 have no journal entries and do
  not acquire any; that is an explicit decision, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Optional

from services.fee_calc_inputs import PRODUCT_TYPES
from services.ledger.posting import (
    VEHICLE_KIND_LEDGER_BOOK,
    VEHICLE_KIND_SPV,
    build_entry_on_conn,
    post_on_conn,
)

ZERO = Decimal("0")

T_RUNS = "public.fee_runs"
T_LINES = "public.fee_run_lines"
T_CARRY_RUNS = "public.spv_carry_runs"
T_CARRY_LINES = "public.spv_carry_run_lines"
T_BOOKS = "public.ledger_books"
T_SPVS = "public.spvs"
T_ENTRIES = "public.journal_entries"


# ═══════════════════════════════════════════════════════════════════════════
# Books
# ═══════════════════════════════════════════════════════════════════════════

#: The adviser's own books — advisory, planning and transaction fee revenue.
BOOK_RIA_OPERATING = "RIA_OPERATING"
#: The 501(c)(6) club's own books — membership dues.
BOOK_CLUB_DUES = "CLUB_DUES"
BOOK_CODES = (BOOK_RIA_OPERATING, BOOK_CLUB_DUES)


class GLPostingError(ValueError):
    """A run cannot be booked. Raised, never swallowed.

    Every raise site here is reached from inside ``post_run``'s transaction, so
    raising rolls the status change back with it. A fee run that reached POSTED
    with no journal entry, because posting failed quietly, would be revenue
    recognised in one system and absent from the other — which is the failure
    this exception exists to make impossible.
    """

    def __init__(self, message: str, **context: Any):
        super().__init__(message)
        self.context = context


# ═══════════════════════════════════════════════════════════════════════════
# Routing
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Destination:
    """Where one product type's revenue books, and through which template."""

    vehicle_kind: str
    transaction_type_code: str
    #: Set for LEDGER_BOOK destinations; None when the vehicle is the SPV itself.
    book_code: Optional[str] = None

    @property
    def is_book(self) -> bool:
        return self.vehicle_kind == VEHICLE_KIND_LEDGER_BOOK


def _book(book_code: str, txn: str) -> Destination:
    return Destination(VEHICLE_KIND_LEDGER_BOOK, txn, book_code)


def _vehicle(txn: str) -> Destination:
    return Destination(VEHICLE_KIND_SPV, txn, None)


#: One entry per deployed ``fee_schedules_product_type_check`` value.
#:
#: The three that book to RIA_OPERATING use three DIFFERENT templates and land
#: on three different revenue accounts, mirroring the three different
#: ``revenue_type`` values fee39 already assigns them (ADVISORY_FEE,
#: PLANNING_FEE, PLACEMENT_FEE). One shared "advisory" account would have put
#: planning and placement revenue on the adviser's advisory line and made the
#: GL impossible to reconcile against ``revenue_events`` line for line.
#:
#: ``SPV`` reuses the MANAGEMENT_FEE template that was already deployed — an SPV
#: accruing a management fee to its manager is exactly what that template says.
#: ``STRUCTURED_INVESTMENT`` gets its own, because booking a placement fee into
#: "Management Fee Expense" would misname it on the vehicle's own income
#: statement.
PRODUCT_TYPE_DESTINATION: Mapping[str, Destination] = {
    "ASSET_MANAGEMENT":      _book(BOOK_RIA_OPERATING, "ADVISORY_FEE_REVENUE"),
    "PLANNING":              _book(BOOK_RIA_OPERATING, "PLANNING_FEE_REVENUE"),
    "TRANSACTION":           _book(BOOK_RIA_OPERATING, "PLACEMENT_FEE_REVENUE"),
    "CLUB_DUES":             _book(BOOK_CLUB_DUES,     "CLUB_DUES_REVENUE"),
    "SPV":                   _vehicle("MANAGEMENT_FEE"),
    "STRUCTURED_INVESTMENT": _vehicle("SPV_PLACEMENT_FEE"),
}

assert set(PRODUCT_TYPE_DESTINATION) == set(PRODUCT_TYPES), (
    "PRODUCT_TYPE_DESTINATION has drifted from fee_schedules' deployed "
    f"product_type vocabulary: {set(PRODUCT_TYPE_DESTINATION) ^ set(PRODUCT_TYPES)}"
)

#: Carry books inside the SPV's OWN book, as an incentive-fee expense credited
#: to ``2100 Due to Affiliate`` — see the GP-entity finding [F43-D]. There is no
#: GP legal entity in this schema (``spvs`` has no GP, manager or sponsor
#: column, and no ``entities`` row models one), so carry cannot be booked as an
#: equity allocation to a GP capital account. It is booked as what it
#: demonstrably is here: an amount the vehicle owes its manager.
CARRY_TRANSACTION_TYPE = "CARRY_ALLOCATION"

#: Reasons a line was not booked. Each one names a measured fact, not a mood.
SKIP_SPV_UNRESOLVABLE = "spv_unresolvable"
SKIP_ZERO_AMOUNT = "zero_amount"


# ═══════════════════════════════════════════════════════════════════════════
# Resolution
# ═══════════════════════════════════════════════════════════════════════════


async def resolve_book_id(conn, org_id: str, book_code: str) -> str:
    """The live ``ledger_books.id`` for a book code, in the CALLER's org.

    Scoped by org_id here as well as by the RLS policy: ``build_entry_on_conn``
    derives the entry's org from the vehicle row, so an unscoped lookup that
    returned another org's book would silently write into that org's ledger.
    """
    row = await conn.fetchrow(
        f"SELECT id::text AS id FROM {T_BOOKS} "
        f"WHERE org_id = $1::uuid AND book_code = $2 AND system_to IS NULL",
        str(org_id), book_code,
    )
    if not row:
        raise GLPostingError(
            f"ledger_book {book_code!r} does not exist for org {org_id}. "
            f"fee43's seed creates {list(BOOK_CODES)}; without it, fee revenue "
            f"has no book to post to",
            org_id=str(org_id), book_code=book_code,
        )
    return row["id"]


async def _resolve_spv_for_line(conn, org_id: str, line: Mapping[str, Any]) -> Optional[str]:
    """The SPV a vehicle-scoped fee line belongs to, or None.

    ``fee_run_lines`` has no ``spv_id``. The one real path is the billed
    entity: an SPV's vehicle entity is ``spvs.vehicle_entity_id``. Returns None
    rather than guessing when the line's entity is not an SPV's vehicle entity —
    see [F43-C].
    """
    entity_id = line.get("entity_id")
    if not entity_id:
        return None
    row = await conn.fetchrow(
        f"SELECT id::text AS id FROM {T_SPVS} "
        f"WHERE org_id = $1::uuid AND vehicle_entity_id = $2::uuid",
        str(org_id), str(entity_id),
    )
    return row["id"] if row else None


# ═══════════════════════════════════════════════════════════════════════════
# Fee runs
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class _Group:
    """One journal entry's worth of lines: same vehicle, same template."""

    vehicle_kind: str
    vehicle_id: str
    transaction_type_code: str
    book_code: Optional[str]
    total: Decimal
    line_ids: list[str]
    product_types: set[str]


async def post_fee_run(
    conn,
    org_id: str,
    run_id: str,
    *,
    posted_by: Optional[str] = None,
    entry_date: Optional[date] = None,
) -> dict[str, Any]:
    """Book a fee run's lines to the general ledger.

    Called from ``fee_runs.post_run`` INSIDE the transaction that sets
    ``status='POSTED'``. Raising here rolls that status change back.

    One journal entry per (vehicle, template) group, for the group's total —
    not one entry per line. A quarterly advisory run over three hundred accounts
    is one revenue recognition event, and three hundred identical two-line
    entries would bury it. The per-line detail already exists, in
    ``fee_run_lines`` and in ``revenue_events``; ``source_event_id`` on the
    entry is the join back to it.
    """
    org_id = str(org_id)
    run_id = str(run_id)

    run = await conn.fetchrow(
        f"SELECT id::text AS id, status, run_type, period_end "
        f"FROM {T_RUNS} WHERE id = $1::uuid AND org_id = $2::uuid",
        run_id, org_id,
    )
    if not run:
        raise GLPostingError(f"fee_run {run_id} not found in org {org_id}",
                             run_id=run_id, org_id=org_id)

    lines = await conn.fetch(
        f"SELECT id::text AS id, product_type, net_fee, entity_id::text AS entity_id "
        f"FROM {T_LINES} WHERE fee_run_id = $1::uuid AND org_id = $2::uuid "
        f"ORDER BY id",
        run_id, org_id,
    )

    groups: dict[tuple[str, str, str], _Group] = {}
    skipped: list[dict[str, Any]] = []

    for line in lines:
        product_type = line["product_type"]
        dest = PRODUCT_TYPE_DESTINATION.get(product_type)
        if dest is None:
            # Not a skip. A product type with no destination is a routing hole,
            # and letting the run post around it would recognise revenue the GL
            # never sees.
            raise GLPostingError(
                f"fee_run_line {line['id']} has product_type {product_type!r}, "
                f"which has no GL destination. Known: "
                f"{sorted(PRODUCT_TYPE_DESTINATION)}",
                run_id=run_id, line_id=line["id"], product_type=product_type,
            )

        if dest.is_book:
            vehicle_id = await resolve_book_id(conn, org_id, dest.book_code)
        else:
            vehicle_id = await _resolve_spv_for_line(conn, org_id, line)
            if vehicle_id is None:
                skipped.append({
                    "line_id": line["id"],
                    "product_type": product_type,
                    "reason": SKIP_SPV_UNRESOLVABLE,
                    "detail": (
                        "fee_run_lines carries no spv_id, and this line's "
                        "entity_id is not any SPV's vehicle_entity_id. Routing "
                        "it to the RIA book would book a vehicle's expense as "
                        "the adviser's revenue"
                    ),
                })
                continue

        key = (dest.vehicle_kind, vehicle_id, dest.transaction_type_code)
        group = groups.get(key)
        if group is None:
            group = groups[key] = _Group(
                vehicle_kind=dest.vehicle_kind, vehicle_id=vehicle_id,
                transaction_type_code=dest.transaction_type_code,
                book_code=dest.book_code, total=ZERO, line_ids=[],
                product_types=set(),
            )
        group.total += Decimal(str(line["net_fee"]))
        group.line_ids.append(line["id"])
        group.product_types.add(product_type)

    when = entry_date or run["period_end"]
    entries: list[dict[str, Any]] = []
    booked_total = ZERO

    for key in sorted(groups, key=lambda k: (k[0], k[2], k[1])):
        group = groups[key]
        if group.total == ZERO:
            # Real: a group whose discounts and credits exactly cancel its
            # gross fee. There is nothing to book, and build_entry refuses a
            # zero amount anyway. Named, counted, and returned.
            skipped.append({
                "line_id": None,
                "product_type": sorted(group.product_types),
                "reason": SKIP_ZERO_AMOUNT,
                "detail": (
                    f"{len(group.line_ids)} line(s) for "
                    f"{group.transaction_type_code} net to exactly zero"
                ),
                "line_ids": group.line_ids,
            })
            continue

        entry = await build_entry_on_conn(
            conn,
            vehicle_id=group.vehicle_id,
            transaction_type_code=group.transaction_type_code,
            entry_date=when,
            amount=abs(group.total),
            dims={},
            created_by=posted_by,
            vehicle_kind=group.vehicle_kind,
            memo=f"fee_run {run_id} — {group.transaction_type_code}",
            source_event_id=run_id,
            reverse_sides=group.total < ZERO,
        )
        posted = await post_on_conn(conn, str(entry["id"]), posted_by)
        booked_total += group.total
        entries.append({
            "entry_id": str(entry["id"]),
            "vehicle_kind": group.vehicle_kind,
            "vehicle_id": group.vehicle_id,
            "book_code": group.book_code,
            "transaction_type_code": group.transaction_type_code,
            "amount": group.total,
            "reversed_sides": group.total < ZERO,
            "posted_at": posted.get("posted_at"),
            "line_ids": group.line_ids,
            "product_types": sorted(group.product_types),
            "lines": [
                {"line_no": ln["line_no"], "account_code": ln["account_code"],
                 "account_name": ln["account_name"], "side": ln["side"],
                 "debit": ln["debit"], "credit": ln["credit"]}
                for ln in entry["lines"]
            ],
        })

    return {
        "posted": bool(entries),
        "run_id": run_id,
        "run_type": run["run_type"],
        "journal_entries_written": len(entries),
        "entries": entries,
        "booked_total": booked_total,
        "skipped": skipped,
        "line_count": len(lines),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Carry runs
# ═══════════════════════════════════════════════════════════════════════════


async def post_carry_run(
    conn,
    org_id: str,
    run_id: str,
    *,
    posted_by: Optional[str] = None,
    entry_date: Optional[date] = None,
) -> dict[str, Any]:
    """Book an SPV carry run's ``carry_to_gp`` to the general ledger.

    Inside the SPV's OWN book — ``vehicle_kind='SPV'``, ``vehicle_id`` the run's
    ``spv_id``, which unlike a fee run line is NOT NULL and needs no guessing.

    Only ``carry_to_gp`` books. ``net_to_lp`` is the LPs' own money moving
    through a distribution the SPV ledger already books through its DISTRIBUTION
    template; booking it again here would double-count the distribution.
    """
    org_id = str(org_id)
    run_id = str(run_id)

    run = await conn.fetchrow(
        f"SELECT r.id::text AS id, r.status, r.spv_id::text AS spv_id, r.created_at, "
        f"       t.txn_date "
        f"FROM {T_CARRY_RUNS} r "
        f"LEFT JOIN public.spv_transactions t ON t.id = r.triggering_transaction_id "
        f"WHERE r.id = $1::uuid AND r.org_id = $2::uuid",
        run_id, org_id,
    )
    if not run:
        raise GLPostingError(f"spv_carry_run {run_id} not found in org {org_id}",
                             run_id=run_id, org_id=org_id)

    total_carry = await conn.fetchval(
        f"SELECT COALESCE(SUM(carry_to_gp), 0) FROM {T_CARRY_LINES} "
        f"WHERE spv_carry_run_id = $1::uuid AND org_id = $2::uuid",
        run_id, org_id,
    )
    total_carry = Decimal(str(total_carry or 0))

    # The entry date is the realization's own date when there is one — carry is
    # earned when the gain is realized, not when somebody ran the calculation.
    when = entry_date or run["txn_date"] or run["created_at"].date()

    if total_carry == ZERO:
        return {
            "posted": False,
            "run_id": run_id,
            "journal_entries_written": 0,
            "entries": [],
            "total_carry_to_gp": ZERO,
            "skipped": [{
                "reason": SKIP_ZERO_AMOUNT,
                "detail": (
                    "the run's lines allocate no carry to the GP — below the "
                    "hurdle, or a loss. There is nothing to book"
                ),
            }],
        }

    entry = await build_entry_on_conn(
        conn,
        vehicle_id=run["spv_id"],
        transaction_type_code=CARRY_TRANSACTION_TYPE,
        entry_date=when,
        amount=abs(total_carry),
        dims={},
        created_by=posted_by,
        vehicle_kind=VEHICLE_KIND_SPV,
        memo=f"spv_carry_run {run_id} — carried interest",
        source_event_id=run_id,
        reverse_sides=total_carry < ZERO,
    )
    posted = await post_on_conn(conn, str(entry["id"]), posted_by)

    return {
        "posted": True,
        "run_id": run_id,
        "journal_entries_written": 1,
        "total_carry_to_gp": total_carry,
        "gp_entity": (
            "none. Carry books inside the SPV's own book as an incentive-fee "
            "expense credited to 2100 Due to Affiliate. No GP legal entity "
            "exists in this schema to hold a capital account — finding F43-D"
        ),
        "entries": [{
            "entry_id": str(entry["id"]),
            "vehicle_kind": VEHICLE_KIND_SPV,
            "vehicle_id": run["spv_id"],
            "book_code": None,
            "transaction_type_code": CARRY_TRANSACTION_TYPE,
            "amount": total_carry,
            "reversed_sides": total_carry < ZERO,
            "posted_at": posted.get("posted_at"),
            "lines": [
                {"line_no": ln["line_no"], "account_code": ln["account_code"],
                 "account_name": ln["account_name"], "side": ln["side"],
                 "debit": ln["debit"], "credit": ln["credit"]}
                for ln in entry["lines"]
            ],
        }],
        "skipped": [],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Reading back
# ═══════════════════════════════════════════════════════════════════════════


async def entries_for_run(conn, org_id: str, run_id: str) -> list[dict[str, Any]]:
    """Every journal entry a run produced, via ``source_event_id``.

    The only link between a run and its GL entries — neither ``fee_runs`` nor
    ``spv_carry_runs`` has a journal_entry column, and ``journal_entries`` has
    no run column.
    """
    rows = await conn.fetch(
        f"SELECT id::text AS id, vehicle_id::text AS vehicle_id, vehicle_kind, "
        f"       transaction_type_code, entry_date, memo, posted_at "
        f"FROM {T_ENTRIES} "
        f"WHERE org_id = $1::uuid AND source_event_id = $2::uuid "
        f"ORDER BY transaction_type_code, id",
        str(org_id), str(run_id),
    )
    return [dict(r) for r in rows]


__all__ = [
    "BOOK_CLUB_DUES",
    "BOOK_CODES",
    "BOOK_RIA_OPERATING",
    "CARRY_TRANSACTION_TYPE",
    "Destination",
    "GLPostingError",
    "PRODUCT_TYPE_DESTINATION",
    "SKIP_SPV_UNRESOLVABLE",
    "SKIP_ZERO_AMOUNT",
    "entries_for_run",
    "post_carry_run",
    "post_fee_run",
    "resolve_book_id",
]
