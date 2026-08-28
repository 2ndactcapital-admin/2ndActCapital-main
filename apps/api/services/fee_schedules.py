"""Fee schedule catalog — create, version, approve, retire, assign. fee34.

This sprint builds the CATALOG and nothing that computes a dollar. A schedule
can be written, versioned, approved and assigned; no code here reads a schedule
to produce a bill. That is fee35, and the seam between them is
``services.fee_validation``, which fee35 re-runs rather than re-implements.


VERSIONING, AND WHY THE INDEX DECIDES IT
──────────────────────────────────────────────────────────────────────────────
Rule 3 offers two axes — valid-time restatement (close the row, insert a
successor with a NEW id) and system-time archival (keep the id, move
system_from/system_to). Task 1 measured that NEITHER is available for a
schedule edit:

    CREATE UNIQUE INDEX fee_schedules_code_version_uq
        ON public.fee_schedules (org_id, code, version)     -- no WHERE clause

The index is not partial. Closing a row and re-inserting the same
``(org_id, code, version)`` collides with the row just closed, because a closed
row still occupies the index. So a correction cannot be a restatement at all.

What the deployed shape supports instead is the third thing, which is also what
the sprint asks for and what a fee schedule actually needs:

    DRAFT      edited IN PLACE. Same id, same version. A draft has never
               governed a bill, so there is no history to preserve.
    APPROVED   never mutated. An edit writes version N+1 as a new DRAFT row.
               Version N keeps its id, keeps status APPROVED, and every
               fee_assignment pointing at it keeps resolving to it — the
               assignments are not migrated, and that is the point. An invoice
               produced last quarter must still be reproducible from the exact
               schedule that produced it.
    RETIRED    neither edited nor newly assigned. Existing assignments are left
               alone: retiring a schedule stops new business, it does not
               rewrite old business.

``fee_schedule_tiers`` has no temporal columns at all (measured in Task 1 — no
valid_*, no system_*, not even created_at). Tiers are therefore plain children
of the schedule row that owns them, and a version bump COPIES the tier set to
the new schedule id rather than closing anything. A DRAFT tier edit replaces
the draft's tier rows outright, which is safe for exactly the reason a DRAFT
in-place edit is safe.


PRECEDENCE, AND THE COLUMN THAT COULD INVERT IT
──────────────────────────────────────────────────────────────────────────────
``fee_assignments.precedence`` is ``integer NOT NULL`` with NO default and no
relationship to ``scope_type`` anywhere in the database. Nothing stops an
insert of ``('ORG_DEFAULT', precedence => 1)``, which would make the org-wide
fallback outrank every account-specific agreement — silently, and only visible
as a wrong number on an invoice.

So it is DERIVED here, from ``scope_type``, and :func:`create_assignment`
accepts no ``precedence`` argument at all. There is nothing for a request body
to carry and nothing for a later edit to start trusting. This is the same
shape as the ``org_id``-never-from-a-body rule and for the same reason.

Most-specific wins, lowest number first:

    ACCOUNT 10 < BILLING_GROUP 20 < HOUSEHOLD 30 < ENTITY 40 < ORG_DEFAULT 50

The gaps of ten are deliberate: a scope inserted between two existing ones
later needs a number, not a renumbering of every stored row.

The resolution itself mirrors ``portfolio_precedence.resolve_precedence``'s
shape — collect every candidate, rank them, take the winner, and keep the
losers visible rather than discarding them — applied to schedule assignment
instead of data-source resolution.


THE CROSS-SCOPE CHECK, AND WHY ONLY BILLING_GROUP GETS ONE
──────────────────────────────────────────────────────────────────────────────
``scope_id`` is a bare ``uuid`` with no foreign key, and it cannot have one: it
addresses four different tables depending on ``scope_type``. So a typo, a
stale id, or another tenant's id all insert cleanly.

The sprint asks for the BILLING_GROUP check specifically, and Task 1 measured
why that is the right place to stop: ``billing_groups`` carries both temporal
axes and therefore has a real CLOSED state to detect, exactly like fee32's
stale-account case. ``households`` and ``documents`` have NO temporal columns
at all — there is no such thing as a closed household, so "is it closed" is not
a question that can be asked of one. Existence is still checked for every scope
type; closure is checked where closure exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence
from uuid import UUID

from services.fee_validation import (
    ASSIGNMENT_SCOPE_TYPES,
    DEFAULT_ORDERING_POLICY,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_RETIRED,
    FeeScheduleInvalid,
    raise_if_invalid,
    validate_schedule,
)
from services.portfolio_assets import _OrgWrite, _require_org

TABLE_SCHEDULES = "public.fee_schedules"
TABLE_TIERS = "public.fee_schedule_tiers"
TABLE_ASSIGNMENTS = "public.fee_assignments"
TABLE_BILLING_GROUPS = "public.billing_groups"
TABLE_BILLING_GROUP_MEMBERS = "public.billing_group_members"
TABLE_ACCOUNTS = "public.accounts"
TABLE_HOUSEHOLDS = "public.households"
TABLE_ENTITIES = "public.entities"
TABLE_DOCUMENTS = "public.documents"

#: Same pair fee33 settled on. ``manage_billing`` rather than
#: ``manage_portfolio``: deciding what a client is charged is a narrower
#: authority than editing a holding. Both already exist in ``public.permissions``
#: (measured — manage_billing, manage_portfolio, view_portfolio are the three
#: that matched).
READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_billing"

#: Most-specific first. Lower number wins. Derived from scope_type on every
#: insert; never accepted from a caller. See the module docstring.
SCOPE_PRECEDENCE: dict[str, int] = {
    "ACCOUNT": 10,
    "BILLING_GROUP": 20,
    "HOUSEHOLD": 30,
    "ENTITY": 40,
    "ORG_DEFAULT": 50,
}

#: Asserted at import: the precedence map and the deployed CHECK's vocabulary
#: must cover exactly the same scopes. A scope added to one and not the other
#: would otherwise surface as a KeyError on a production write.
assert set(SCOPE_PRECEDENCE) == set(ASSIGNMENT_SCOPE_TYPES), (
    "SCOPE_PRECEDENCE and ASSIGNMENT_SCOPE_TYPES have drifted: "
    f"{set(SCOPE_PRECEDENCE) ^ set(ASSIGNMENT_SCOPE_TYPES)}"
)

#: The scope type that takes a NULL scope_id, mirroring
#: ``fee_assignments_scope_id_required``.
SCOPE_ORG_DEFAULT = "ORG_DEFAULT"

#: Which table each scope_type's scope_id addresses, and whether that table has
#: a closed state to check. Measured in Task 1, not assumed: households and
#: documents carry no temporal columns at all.
_SCOPE_TABLES: dict[str, tuple[str, bool]] = {
    "ACCOUNT": (TABLE_ACCOUNTS, True),
    "BILLING_GROUP": (TABLE_BILLING_GROUPS, True),
    "HOUSEHOLD": (TABLE_HOUSEHOLDS, False),
    "ENTITY": (TABLE_ENTITIES, True),
}

#: The fields that make up a schedule's DEFINITION — everything a version
#: carries forward. ``code`` is absent deliberately: it is the versioning
#: identity, so editing it would not produce version N+1 of anything, it would
#: produce version N+1 of a DIFFERENT schedule and collide or fork. ``version``,
#: ``status``, ``approved_by``/``approved_at`` are lifecycle, not definition.
DEFINITION_FIELDS = (
    "name",
    "product_type",
    "rate_type",
    "tier_method",
    "billing_frequency",
    "billing_timing",
    "valuation_method",
    "day_weight_flows",
    "day_weight_threshold",
    "proration_method",
    "minimum_fee",
    "minimum_fee_scope",
    "maximum_fee",
    "minimum_billable_value",
    "cash_treatment",
    "cash_exclusion_pct",
    "margin_treatment",
    "ordering_policy",
    "currency",
)

#: UX4's rule: this list is published to the client from the server's own
#: response and EMPTIED for a caller without WRITE_PERMISSION — never defaulted
#: client-side. A schedule is only editable while DRAFT; an APPROVED one is
#: "editable" solely in the sense that editing forks a new draft, which the
#: router publishes as a separate capability.
EDITABLE_SCHEDULE_FIELDS = DEFINITION_FIELDS

#: The columns of one tier row. ``id`` and ``fee_schedule_id`` are assigned by
#: the write, not carried from the caller.
TIER_FIELDS = ("tier_seq", "lower_bound", "upper_bound", "rate_bps", "flat_amount")

#: "Current" on both temporal axes, matching ``portfolio_assets._current``.
def _current(alias: str) -> str:
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class FeeScheduleError(ValueError):
    """A catalog write was refused for a reason the caller can fix."""


class FeeScheduleNotFoundError(FeeScheduleError):
    """The schedule is not this org's, or is not current.

    Deliberately indistinguishable from "does not exist". Same reasoning as
    fee33's ``BillingGroupNotFoundError``: telling a caller "that id exists but
    is not yours" confirms a row's existence across a tenant boundary.
    """


class ScheduleStatusError(FeeScheduleError):
    """The operation is not legal from the schedule's current status.

    Carries ``status`` and ``schedule_id`` as attributes so the router can turn
    it into a 409 that names the state rather than a 400 that sends the
    operator looking at their own input.
    """

    def __init__(self, message: str, *, schedule_id: str, status: str) -> None:
        super().__init__(message)
        self.schedule_id = schedule_id
        self.status = status


class ScopeLinkError(FeeScheduleError):
    """``scope_id`` does not resolve to a live row of the right kind.

    Shaped after fee32's ``AccountLinkError`` and for the identical reason:
    ``scope_id`` has no foreign key — it cannot, since it addresses four
    different tables — so a stale id, a typo, and another tenant's id all
    insert cleanly and only surface as a fee that resolves to nothing.

    ``reason`` is ``'missing'`` or ``'closed'``. The two are distinguished
    because they are different operator situations: 'missing' means look at the
    id, 'closed' means the group was retired and the assignment needs a
    different target.
    """

    def __init__(
        self,
        message: str,
        *,
        scope_type: str,
        scope_id: str | None,
        reason: str,
    ) -> None:
        super().__init__(message)
        self.scope_type = scope_type
        self.scope_id = scope_id
        self.reason = reason


class ScopeIdRequiredError(FeeScheduleError):
    """``scope_id`` is present for ORG_DEFAULT, or absent for anything else.

    ``fee_assignments_scope_id_required`` already enforces this. What it cannot
    do is explain it: the constraint's message names the constraint, and the
    fix ("ORG_DEFAULT is the org-wide fallback and applies to everything, so it
    has nothing to point at") is not derivable from the name.
    """

    def __init__(self, message: str, *, scope_type: str) -> None:
        super().__init__(message)
        self.scope_type = scope_type


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _as_uuid_text(value: Any, *, field: str) -> str:
    """Validate and normalise a uuid to text, or refuse with a clean message.

    Refusing here rather than at the database keeps a mistyped id from
    surfacing as ``invalid input syntax for type uuid``, which names neither
    the field nor the value.
    """
    if value is None:
        raise FeeScheduleError(f"{field} is required")
    if isinstance(value, UUID):
        return str(value)
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise FeeScheduleError(f"{field}={value!r} is not a valid uuid") from exc


def _clean_code(value: Any) -> str:
    """Trim, upper-case, refuse blank.

    Upper-cased because ``fee_schedules_code_version_uq`` is a plain unique
    index on ``code`` with no ``lower()`` — 'STANDARD' and 'standard' would be
    two different schedules that look identical in a list. Folding on the way
    in is the only symmetric fix; folding in the query instead would make the
    index unusable for the lookup.
    """
    code = str(value or "").strip().upper()
    if not code:
        raise FeeScheduleError("code is required and cannot be blank")
    return code


#: The definition fields that land in a ``numeric`` column. Every one of them
#: is money or a rate, and none may arrive as a float — see :func:`_coerce_money`.
_NUMERIC_DEFINITION_FIELDS = frozenset({
    "day_weight_threshold",
    "minimum_fee",
    "maximum_fee",
    "minimum_billable_value",
    "cash_exclusion_pct",
})


def _coerce_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse floats on every monetary definition field, before any bind.

    ``fee_validation`` refuses floats on the way IN to validation; this refuses
    them on the way OUT to the database. Both halves are needed: a caller can
    reach :func:`create_schedule` without having validated first, and asyncpg
    would encode ``0.1`` into a ``numeric`` as whatever its text repr rounds to
    — storing a value that is not the value the operator typed, with no error
    anywhere.
    """
    out = dict(definition)
    for field in _NUMERIC_DEFINITION_FIELDS & set(out):
        value = out[field]
        if value is None or isinstance(value, Decimal):
            continue
        if isinstance(value, bool):
            raise FeeScheduleError(f"{field} must be a decimal amount, not a boolean")
        if isinstance(value, float):
            raise FeeScheduleError(
                f"{field} arrived as a float ({value!r}); fee amounts must be "
                f"Decimal, int, or a decimal string — a float cannot represent "
                f"a monetary value exactly"
            )
        out[field] = Decimal(str(value))
    return out


def _ordering_policy_param(value: Any) -> str:
    """Render ordering_policy for a ``jsonb`` bind.

    asyncpg has no automatic Python-list-to-jsonb adaptation, so the value is
    passed as text and cast in SQL. ``None`` becomes the canonical default
    rather than being left to the column default, so the stored row states its
    own policy explicitly and a later read never has to know what the default
    was at write time.
    """
    if value is None:
        return json.dumps(DEFAULT_ORDERING_POLICY)
    if isinstance(value, str):
        return value
    return json.dumps(list(value))


def _row_to_schedule(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    policy = out.get("ordering_policy")
    if isinstance(policy, str):
        out["ordering_policy"] = json.loads(policy)
    return out


_SCHEDULE_COLUMNS = """
    s.id::text          AS id,
    s.org_id::text      AS org_id,
    s.code, s.version, s.name, s.product_type, s.rate_type, s.tier_method,
    s.billing_frequency, s.billing_timing, s.valuation_method,
    s.day_weight_flows, s.day_weight_threshold, s.proration_method,
    s.minimum_fee, s.minimum_fee_scope, s.maximum_fee, s.minimum_billable_value,
    s.cash_treatment, s.cash_exclusion_pct, s.margin_treatment,
    s.ordering_policy::text AS ordering_policy,
    s.currency, s.status,
    s.approved_by::text AS approved_by, s.approved_at,
    s.created_by::text  AS created_by, s.created_at,
    s.valid_from, s.valid_to, s.system_from, s.system_to
"""


async def load_schedule(conn, org_id: str, schedule_id: Any) -> dict[str, Any]:
    """One current schedule row, org-scoped explicitly.

    The org predicate is in the WHERE clause and not left to RLS. ``_OrgWrite``
    raises the org GUC FROM its argument, so a caller that passed the wrong
    org_id would satisfy the policy against its own mistake — RLS confirms the
    connection's context, it does not confirm the caller's intent.
    """
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"""
        SELECT {_SCHEDULE_COLUMNS}
        FROM {TABLE_SCHEDULES} s
        WHERE s.id = $1::uuid AND s.org_id = $2::uuid AND {_current('s')}
        """,
        _as_uuid_text(schedule_id, field="fee_schedule_id"), org_id,
    )
    if row is None:
        raise FeeScheduleNotFoundError(
            f"fee schedule {schedule_id} is not a current schedule in this org"
        )
    return _row_to_schedule(row)


async def load_tiers(conn, org_id: str, schedule_id: Any) -> list[dict[str, Any]]:
    """The schedule's tier rows, ordered by tier_seq.

    Ordered in SQL rather than in Python because every tier rule downstream is
    about ADJACENCY, and a validator handed an unordered list would report
    gaps and overlaps that are artefacts of the read.
    """
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        f"""
        SELECT t.id::text AS id, t.tier_seq, t.lower_bound, t.upper_bound,
               t.rate_bps, t.flat_amount
        FROM {TABLE_TIERS} t
        WHERE t.fee_schedule_id = $1::uuid AND t.org_id = $2::uuid
        ORDER BY t.tier_seq
        """,
        _as_uuid_text(schedule_id, field="fee_schedule_id"), org_id,
    )
    return [dict(r) for r in rows]


async def get_schedule(conn, org_id: str, schedule_id: Any) -> dict[str, Any]:
    """A schedule plus its tiers plus its current validation state.

    ``validation_errors`` is computed on every read, not only at submit time,
    so a DRAFT screen can show an operator what still blocks approval without
    them having to attempt it. Advisory: :func:`submit_for_approval` re-runs
    the same function against the same rows and is the binding check.
    """
    schedule = await load_schedule(conn, org_id, schedule_id)
    tiers = await load_tiers(conn, org_id, schedule_id)
    errors = validate_schedule(schedule, tiers)
    return {
        "schedule": schedule,
        "tiers": tiers,
        "validation_errors": [e.as_dict() for e in errors],
        "is_valid": not errors,
    }


async def list_schedules(
    conn,
    org_id: str,
    *,
    status: str | None = None,
    code: str | None = None,
    include_superseded: bool = True,
) -> list[dict[str, Any]]:
    """Current schedule rows for the org, newest version of each code first.

    ``include_superseded=False`` keeps only the highest version per code, which
    is what a picker wants. It is NOT the default: an audit screen needs to see
    that version 1 still exists and is still APPROVED, because assignments made
    before the fork are still resolving to it.
    """
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        f"""
        SELECT {_SCHEDULE_COLUMNS}
        FROM {TABLE_SCHEDULES} s
        WHERE s.org_id = $1::uuid AND {_current('s')}
          AND ($2::text IS NULL OR s.status = $2::text)
          AND ($3::text IS NULL OR s.code = $3::text)
        ORDER BY s.code, s.version DESC
        """,
        org_id, status, _clean_code(code) if code else None,
    )
    out = [_row_to_schedule(r) for r in rows]
    if not include_superseded:
        seen: set[str] = set()
        latest = []
        for row in out:          # already ordered version DESC within a code
            if row["code"] in seen:
                continue
            seen.add(row["code"])
            latest.append(row)
        return latest
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Create
# ═══════════════════════════════════════════════════════════════════════════


async def create_schedule(
    conn,
    org_id: str,
    *,
    code: str,
    tiers: Sequence[Mapping[str, Any]] | None = None,
    created_by: Any = None,
    **definition: Any,
) -> dict[str, Any]:
    """Write a new schedule at version 1, status DRAFT.

    Always DRAFT and always version 1. There is no argument to create something
    already APPROVED: approval is a transition that runs validation, and a
    creation path that could skip it would be the one door around the gate this
    sprint exists to build. ``version`` is likewise not an argument — version 2
    is produced by editing version 1, not by asserting it.

    Tiers are written in the same transaction. A schedule that committed with
    its tier ladder half-written would be a DRAFT that fails validation for a
    reason the operator did not cause.
    """
    org_id = _require_org(org_id)
    code = _clean_code(code)

    unknown = set(definition) - set(DEFINITION_FIELDS)
    if unknown:
        raise FeeScheduleError(
            f"unknown schedule field(s) {sorted(unknown)}; editable fields are "
            f"{list(DEFINITION_FIELDS)}"
        )
    definition = _coerce_definition(definition)

    async with _OrgWrite(conn, org_id) as tx:
        clash = await tx.fetchval(
            f"SELECT count(*) FROM {TABLE_SCHEDULES} s "
            f"WHERE s.org_id = $1::uuid AND s.code = $2",
            org_id, code,
        )
        if clash:
            raise FeeScheduleError(
                f"a fee schedule with code {code!r} already exists in this org. "
                f"Codes identify a schedule across its versions — edit the "
                f"existing one to produce a new version rather than creating a "
                f"second schedule under the same code"
            )

        row = await tx.fetchrow(
            f"""
            INSERT INTO {TABLE_SCHEDULES}
                (org_id, code, version, name, product_type, rate_type,
                 tier_method, billing_frequency, billing_timing,
                 valuation_method, day_weight_flows, day_weight_threshold,
                 proration_method, minimum_fee, minimum_fee_scope, maximum_fee,
                 minimum_billable_value, cash_treatment, cash_exclusion_pct,
                 margin_treatment, ordering_policy, currency, status, created_by)
            VALUES ($1::uuid, $2, 1, $3, $4, $5, $6, $7, $8, $9,
                    COALESCE($10::boolean, true), $11::numeric,
                    COALESCE($12::text, 'CALENDAR_DAYS'), $13::numeric, $14,
                    $15::numeric, $16::numeric,
                    COALESCE($17::text, 'INCLUDE'), $18::numeric,
                    COALESCE($19::text, 'IGNORE'), $20::jsonb,
                    COALESCE($21::text, 'USD'), $22, $23::uuid)
            RETURNING id::text
            """,
            org_id, code,
            definition.get("name"),
            definition.get("product_type"),
            definition.get("rate_type"),
            definition.get("tier_method"),
            definition.get("billing_frequency"),
            definition.get("billing_timing"),
            definition.get("valuation_method"),
            definition.get("day_weight_flows"),
            definition.get("day_weight_threshold"),
            definition.get("proration_method"),
            definition.get("minimum_fee"),
            definition.get("minimum_fee_scope"),
            definition.get("maximum_fee"),
            definition.get("minimum_billable_value"),
            definition.get("cash_treatment"),
            definition.get("cash_exclusion_pct"),
            definition.get("margin_treatment"),
            _ordering_policy_param(definition.get("ordering_policy")),
            definition.get("currency"),
            STATUS_DRAFT,
            str(created_by) if created_by else None,
        )
        schedule_id = row["id"]
        if tiers:
            await _write_tiers(tx, org_id, schedule_id, tiers)

    return await get_schedule(conn, org_id, schedule_id)


async def _write_tiers(
    conn, org_id: str, schedule_id: str, tiers: Sequence[Mapping[str, Any]]
) -> None:
    """Replace the schedule's tier set. Caller must already hold a transaction.

    A DELETE-then-INSERT rather than a diff. Tiers have no temporal columns and
    no identity a caller refers to — ``tier_seq`` is a position, not a name —
    so there is nothing a diff would preserve, and a partial update is the
    shape that leaves an orphaned tier above the new top of the ladder.

    Only ever called for a DRAFT (or for a version being created), which is
    what makes destroying the previous set safe: an APPROVED schedule's tiers
    are never touched.
    """
    await conn.execute(
        f"DELETE FROM {TABLE_TIERS} WHERE fee_schedule_id = $1::uuid "
        f"AND org_id = $2::uuid",
        schedule_id, org_id,
    )
    for tier in tiers:
        await conn.execute(
            f"""
            INSERT INTO {TABLE_TIERS}
                (org_id, fee_schedule_id, tier_seq, lower_bound, upper_bound,
                 rate_bps, flat_amount)
            VALUES ($1::uuid, $2::uuid, $3::integer, $4::numeric, $5::numeric,
                    $6::numeric, $7::numeric)
            """,
            org_id, schedule_id,
            tier.get("tier_seq") if isinstance(tier, Mapping)
            else getattr(tier, "tier_seq", None),
            _numeric(tier, "lower_bound"),
            _numeric(tier, "upper_bound"),
            _numeric(tier, "rate_bps"),
            _numeric(tier, "flat_amount"),
        )


def _numeric(tier: Any, key: str) -> Decimal | None:
    """Pull one tier field and hand asyncpg a Decimal, never a float.

    A float reaching a ``numeric`` bind is silently rounded by the driver's
    text encoding, and the value stored is not the value validated. The
    validator refuses floats; this is the second half of the same rule, on the
    write side, so the two cannot disagree.
    """
    value = tier.get(key) if isinstance(tier, Mapping) else getattr(tier, key, None)
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise FeeScheduleError(
            f"tier field {key} arrived as a float ({value!r}); fee amounts must "
            f"be Decimal, int, or a decimal string"
        )
    return Decimal(str(value))


# ═══════════════════════════════════════════════════════════════════════════
# Edit — in place for a DRAFT, version+1 for an APPROVED one
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EditOutcome:
    """What an edit actually did.

    ``versioned`` is the fact a caller most needs and most easily assumes: the
    UI's "saved" toast is wrong if it does not say a new draft was created. The
    id is returned separately from the source id precisely so the two can be
    compared rather than presumed equal.
    """

    schedule_id: str
    source_schedule_id: str
    version: int
    status: str
    versioned: bool


async def update_schedule(
    conn,
    org_id: str,
    schedule_id: Any,
    *,
    tiers: Sequence[Mapping[str, Any]] | None = None,
    created_by: Any = None,
    **changes: Any,
) -> EditOutcome:
    """Edit a schedule, forking a new version if the source is APPROVED.

    DRAFT     → UPDATE in place. Same id, same version.
    APPROVED  → INSERT version N+1 as a DRAFT. The APPROVED row is not read
                for update, not closed, and not touched in any way; every
                ``fee_assignment`` pointing at its id still resolves to it.
    RETIRED   → refused.

    Only fields actually passed are changed. A sparse edit that carried the
    caller's absent fields through as NULL would blank half a schedule on a
    one-field correction — and the validation that runs at submit time would
    then be validating a row the operator never wrote.

    ``tiers=None`` means "leave the tier set alone". On a version fork that
    means COPY it forward; on a draft edit it means do not touch it. An empty
    list is a real instruction and is honoured — it clears the ladder.
    """
    org_id = _require_org(org_id)
    schedule_id = _as_uuid_text(schedule_id, field="fee_schedule_id")

    unknown = set(changes) - set(DEFINITION_FIELDS)
    if unknown:
        raise FeeScheduleError(
            f"unknown schedule field(s) {sorted(unknown)}; editable fields are "
            f"{list(DEFINITION_FIELDS)}"
        )
    changes = _coerce_definition(changes)

    async with _OrgWrite(conn, org_id) as tx:
        current = await load_schedule(tx, org_id, schedule_id)
        status = current["status"]

        if status == STATUS_RETIRED:
            raise ScheduleStatusError(
                f"fee schedule {current['code']} v{current['version']} is "
                f"RETIRED and cannot be edited. Retiring stops new business; "
                f"it does not reopen the schedule for changes. Create a new "
                f"schedule, or edit a non-retired version",
                schedule_id=schedule_id, status=status,
            )

        if status == STATUS_DRAFT:
            await _apply_definition(tx, org_id, schedule_id, changes)
            if tiers is not None:
                await _write_tiers(tx, org_id, schedule_id, tiers)
            return EditOutcome(
                schedule_id=schedule_id,
                source_schedule_id=schedule_id,
                version=int(current["version"]),
                status=STATUS_DRAFT,
                versioned=False,
            )

        # APPROVED — fork.
        #
        # max(version)+1 across the CODE, not current['version']+1. Editing an
        # older approved version while a newer draft already exists would
        # otherwise try to write a version number that is taken, and collide
        # with fee_schedules_code_version_uq. Taking the max is what makes
        # "fork from any version" work.
        next_version = await tx.fetchval(
            f"SELECT max(s.version) + 1 FROM {TABLE_SCHEDULES} s "
            f"WHERE s.org_id = $1::uuid AND s.code = $2",
            org_id, current["code"],
        )
        merged = {field: current.get(field) for field in DEFINITION_FIELDS}
        merged.update(changes)

        new_row = await tx.fetchrow(
            f"""
            INSERT INTO {TABLE_SCHEDULES}
                (org_id, code, version, name, product_type, rate_type,
                 tier_method, billing_frequency, billing_timing,
                 valuation_method, day_weight_flows, day_weight_threshold,
                 proration_method, minimum_fee, minimum_fee_scope, maximum_fee,
                 minimum_billable_value, cash_treatment, cash_exclusion_pct,
                 margin_treatment, ordering_policy, currency, status, created_by)
            VALUES ($1::uuid, $2, $3::integer, $4, $5, $6, $7, $8, $9, $10,
                    $11::boolean, $12::numeric, $13, $14::numeric, $15,
                    $16::numeric, $17::numeric, $18, $19::numeric, $20,
                    $21::jsonb, $22, $23, $24::uuid)
            RETURNING id::text
            """,
            org_id, current["code"], int(next_version),
            merged.get("name"), merged.get("product_type"),
            merged.get("rate_type"), merged.get("tier_method"),
            merged.get("billing_frequency"), merged.get("billing_timing"),
            merged.get("valuation_method"), merged.get("day_weight_flows"),
            merged.get("day_weight_threshold"), merged.get("proration_method"),
            merged.get("minimum_fee"), merged.get("minimum_fee_scope"),
            merged.get("maximum_fee"), merged.get("minimum_billable_value"),
            merged.get("cash_treatment"), merged.get("cash_exclusion_pct"),
            merged.get("margin_treatment"),
            _ordering_policy_param(merged.get("ordering_policy")),
            merged.get("currency"), STATUS_DRAFT,
            str(created_by) if created_by else None,
        )
        new_id = new_row["id"]

        if tiers is not None:
            await _write_tiers(tx, org_id, new_id, tiers)
        else:
            # Copy the source's ladder forward. Done in SQL so a schedule with
            # many tiers does not round-trip each one, and so the copy cannot
            # observe a partially-written set.
            await tx.execute(
                f"""
                INSERT INTO {TABLE_TIERS}
                    (org_id, fee_schedule_id, tier_seq, lower_bound,
                     upper_bound, rate_bps, flat_amount)
                SELECT t.org_id, $1::uuid, t.tier_seq, t.lower_bound,
                       t.upper_bound, t.rate_bps, t.flat_amount
                FROM {TABLE_TIERS} t
                WHERE t.fee_schedule_id = $2::uuid AND t.org_id = $3::uuid
                """,
                new_id, schedule_id, org_id,
            )

        return EditOutcome(
            schedule_id=new_id,
            source_schedule_id=schedule_id,
            version=int(next_version),
            status=STATUS_DRAFT,
            versioned=True,
        )


async def _apply_definition(
    conn, org_id: str, schedule_id: str, changes: Mapping[str, Any]
) -> None:
    """UPDATE only the columns actually supplied. Caller holds the transaction."""
    if not changes:
        return
    sets: list[str] = []
    params: list[Any] = []
    for field, value in changes.items():
        params.append(
            _ordering_policy_param(value) if field == "ordering_policy" else value
        )
        cast = "::jsonb" if field == "ordering_policy" else ""
        sets.append(f"{field} = ${len(params)}{cast}")
    params.extend([schedule_id, org_id])
    await conn.execute(
        f"UPDATE {TABLE_SCHEDULES} SET {', '.join(sets)} "
        f"WHERE id = ${len(params) - 1}::uuid AND org_id = ${len(params)}::uuid "
        f"AND valid_to IS NULL AND system_to IS NULL",
        *params,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Status transitions
# ═══════════════════════════════════════════════════════════════════════════


async def submit_for_approval(
    conn, org_id: str, schedule_id: Any, *, approved_by: Any = None
) -> dict[str, Any]:
    """Validate, and transition DRAFT → APPROVED only on an all-clear.

    Raises :class:`FeeScheduleInvalid` carrying EVERY error when validation
    fails, and the row is left exactly as it was — the transaction is rolled
    back by ``_OrgWrite``'s ``__aexit__``, so a partially-applied approval is
    not a state this can reach.

    The schedule is re-read from the database inside the transaction rather
    than validated from whatever the caller last passed in. A gate that
    validated the caller's copy would approve a row that the database does not
    actually contain.
    """
    org_id = _require_org(org_id)
    schedule_id = _as_uuid_text(schedule_id, field="fee_schedule_id")

    async with _OrgWrite(conn, org_id) as tx:
        current = await load_schedule(tx, org_id, schedule_id)
        status = current["status"]
        if status != STATUS_DRAFT:
            raise ScheduleStatusError(
                f"fee schedule {current['code']} v{current['version']} is "
                f"{status}, not DRAFT; only a DRAFT can be submitted for "
                f"approval"
                + (
                    ". Edit the approved version to produce a new draft"
                    if status == STATUS_APPROVED
                    else ""
                ),
                schedule_id=schedule_id, status=status,
            )

        tiers = await load_tiers(tx, org_id, schedule_id)
        raise_if_invalid(validate_schedule(current, tiers))

        await tx.execute(
            f"""
            UPDATE {TABLE_SCHEDULES}
               SET status = $1, approved_by = $2::uuid, approved_at = now()
             WHERE id = $3::uuid AND org_id = $4::uuid
               AND status = $5
               AND valid_to IS NULL AND system_to IS NULL
            """,
            STATUS_APPROVED,
            str(approved_by) if approved_by else None,
            schedule_id, org_id, STATUS_DRAFT,
        )

    return await get_schedule(conn, org_id, schedule_id)


async def retire_schedule(conn, org_id: str, schedule_id: Any) -> dict[str, Any]:
    """APPROVED (or DRAFT) → RETIRED.

    Existing assignments are deliberately NOT touched. Retiring says "do not
    put new business on this"; rewriting the assignments already pointing at it
    would silently re-price live clients, which is a fee run's decision to
    surface, not a catalog edit's to make.
    """
    org_id = _require_org(org_id)
    schedule_id = _as_uuid_text(schedule_id, field="fee_schedule_id")

    async with _OrgWrite(conn, org_id) as tx:
        current = await load_schedule(tx, org_id, schedule_id)
        if current["status"] == STATUS_RETIRED:
            return await get_schedule(tx, org_id, schedule_id)
        await tx.execute(
            f"UPDATE {TABLE_SCHEDULES} SET status = $1 "
            f"WHERE id = $2::uuid AND org_id = $3::uuid "
            f"AND valid_to IS NULL AND system_to IS NULL",
            STATUS_RETIRED, schedule_id, org_id,
        )
    return await get_schedule(conn, org_id, schedule_id)


# ═══════════════════════════════════════════════════════════════════════════
# Assignment
# ═══════════════════════════════════════════════════════════════════════════


async def _assert_scope_resolves(
    conn, org_id: str, scope_type: str, scope_id: str | None
) -> None:
    """The referenced scope row exists, is this org's, and is not closed.

    Existence is checked for every scope type; CLOSURE only where a closed
    state exists. Task 1 measured that ``households`` and ``documents`` carry
    no temporal columns at all — asking "is this household closed" of a table
    with no ``system_to`` would either crash or, worse, be written as a
    predicate that is vacuously true and proves nothing.
    """
    if scope_type == SCOPE_ORG_DEFAULT:
        return
    table, has_closed_state = _SCOPE_TABLES[scope_type]

    exists = await conn.fetchval(
        f"SELECT count(*) FROM {table} x "
        f"WHERE x.id = $1::uuid AND x.org_id = $2::uuid",
        scope_id, org_id,
    )
    if not exists:
        raise ScopeLinkError(
            f"scope_type={scope_type} scope_id={scope_id} does not resolve to a "
            f"row in this org. scope_id carries no foreign key — it addresses a "
            f"different table per scope_type — so a stale or mistyped id would "
            f"otherwise insert cleanly and only surface as a fee that resolves "
            f"to nothing",
            scope_type=scope_type, scope_id=scope_id, reason="missing",
        )

    if not has_closed_state:
        return

    open_now = await conn.fetchval(
        f"SELECT count(*) FROM {table} x "
        f"WHERE x.id = $1::uuid AND x.org_id = $2::uuid AND {_current('x')}",
        scope_id, org_id,
    )
    if not open_now:
        raise ScopeLinkError(
            f"scope_type={scope_type} scope_id={scope_id} exists but is closed "
            f"(valid_to or system_to is set). A closed {scope_type.lower()} has "
            f"no live membership, so a fee assigned to it would apply to "
            f"nothing. Assign to an open one, or reopen this one first",
            scope_type=scope_type, scope_id=scope_id, reason="closed",
        )


async def create_assignment(
    conn,
    org_id: str,
    *,
    fee_schedule_id: Any,
    scope_type: str,
    scope_id: Any = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    agreement_document_id: Any = None,
    created_by: Any = None,
    replace_existing: bool = True,
) -> dict[str, Any]:
    """Point a scope at a schedule.

    ``precedence`` is DERIVED from ``scope_type`` and is not a parameter. See
    the module docstring: the column is NOT NULL with no default and no tie to
    scope_type in the database, so accepting it would let a caller invert the
    whole resolution order silently.

    A RETIRED schedule cannot be NEWLY assigned. Assignments that already point
    at one are untouched — that is the difference between retiring a schedule
    and rewriting history.

    ``replace_existing`` closes the incumbent assignment on the same scope
    first. It defaults to True because Task 1 measured that ``fee_assignments``
    has NO unique index: two active assignments on one scope_id would both
    match at the same precedence, and which one won would depend on row order.
    Passing False refuses instead of replacing, for a caller that wants to see
    the conflict.
    """
    org_id = _require_org(org_id)
    fee_schedule_id = _as_uuid_text(fee_schedule_id, field="fee_schedule_id")

    if scope_type not in ASSIGNMENT_SCOPE_TYPES:
        raise FeeScheduleError(
            f"scope_type={scope_type!r} is not one of "
            f"{list(ASSIGNMENT_SCOPE_TYPES)}"
        )

    # Mirrors fee_assignments_scope_id_required, with the reason attached.
    if scope_type == SCOPE_ORG_DEFAULT:
        if scope_id is not None:
            raise ScopeIdRequiredError(
                "scope_type=ORG_DEFAULT is the org-wide fallback and applies to "
                "everything in the org, so it has nothing to point at — "
                "scope_id must be null. To assign to one specific thing, use "
                "scope_type ACCOUNT, BILLING_GROUP, HOUSEHOLD or ENTITY",
                scope_type=scope_type,
            )
        scope_id_text = None
    else:
        if scope_id is None:
            raise ScopeIdRequiredError(
                f"scope_type={scope_type} names one specific "
                f"{scope_type.lower().replace('_', ' ')}, so scope_id is "
                f"required. Use scope_type=ORG_DEFAULT for an assignment that "
                f"applies org-wide with no target",
                scope_type=scope_type,
            )
        scope_id_text = _as_uuid_text(scope_id, field="scope_id")

    precedence = SCOPE_PRECEDENCE[scope_type]
    effective_from = effective_from or date.today()

    if effective_to is not None and effective_to <= effective_from:
        raise FeeScheduleError(
            f"effective_to ({effective_to}) must be after effective_from "
            f"({effective_from}); an assignment that ends before it starts "
            f"never applies"
        )

    async with _OrgWrite(conn, org_id) as tx:
        schedule = await load_schedule(tx, org_id, fee_schedule_id)
        if schedule["status"] == STATUS_RETIRED:
            raise ScheduleStatusError(
                f"fee schedule {schedule['code']} v{schedule['version']} is "
                f"RETIRED and cannot be newly assigned. Assignments already "
                f"pointing at it keep working — retiring stops new business, it "
                f"does not rewrite existing arrangements",
                schedule_id=fee_schedule_id, status=STATUS_RETIRED,
            )

        await _assert_scope_resolves(tx, org_id, scope_type, scope_id_text)

        if agreement_document_id is not None:
            document_id = _as_uuid_text(
                agreement_document_id, field="agreement_document_id"
            )
            found = await tx.fetchval(
                f"SELECT count(*) FROM {TABLE_DOCUMENTS} d "
                f"WHERE d.id = $1::uuid AND d.org_id = $2::uuid",
                document_id, org_id,
            )
            if not found:
                # The FK to documents(id) is org-blind — it references id alone,
                # so another tenant's document satisfies it. Checked explicitly
                # for the same reason fee32 checks account_id.
                raise ScopeLinkError(
                    f"agreement_document_id {document_id} is not a document in "
                    f"this org",
                    scope_type="DOCUMENT", scope_id=document_id, reason="missing",
                )
        else:
            document_id = None

        incumbent = await tx.fetchrow(
            f"""
            SELECT a.id::text AS id, a.fee_schedule_id::text AS fee_schedule_id
            FROM {TABLE_ASSIGNMENTS} a
            WHERE a.org_id = $1::uuid AND a.scope_type = $2
              AND a.scope_id IS NOT DISTINCT FROM $3::uuid
              AND {_current('a')}
              AND (a.effective_to IS NULL OR a.effective_to > $4::date)
            ORDER BY a.effective_from DESC, a.created_at DESC
            LIMIT 1
            """,
            org_id, scope_type, scope_id_text, effective_from,
        )
        replaced_id = None
        if incumbent is not None:
            if not replace_existing:
                raise FeeScheduleError(
                    f"scope_type={scope_type} scope_id={scope_id_text} already "
                    f"has an active assignment ({incumbent['id']}). Two active "
                    f"assignments on one scope resolve at the same precedence "
                    f"and the winner would depend on row order — end the "
                    f"existing one first, or pass replace_existing=True"
                )
            await _close_assignment(tx, org_id, incumbent["id"], effective_from)
            replaced_id = incumbent["id"]

        row = await tx.fetchrow(
            f"""
            INSERT INTO {TABLE_ASSIGNMENTS}
                (org_id, fee_schedule_id, scope_type, scope_id, precedence,
                 effective_from, effective_to, agreement_document_id, created_by)
            VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5::integer,
                    $6::date, $7::date, $8::uuid, $9::uuid)
            RETURNING id::text AS id, precedence, effective_from
            """,
            org_id, fee_schedule_id, scope_type, scope_id_text, precedence,
            effective_from, effective_to, document_id,
            str(created_by) if created_by else None,
        )

    return {
        "id": row["id"],
        "fee_schedule_id": fee_schedule_id,
        "scope_type": scope_type,
        "scope_id": scope_id_text,
        "precedence": int(row["precedence"]),
        "effective_from": row["effective_from"],
        "effective_to": effective_to,
        "replaced_assignment_id": replaced_id,
    }


async def _close_assignment(
    conn, org_id: str, assignment_id: str, ends_on: date
) -> None:
    """End an assignment by CLOSING it — never by deleting it.

    Both axes are set: ``effective_to`` is the business fact (the arrangement
    ended on this date) and ``valid_to`` is the record fact (this row is no
    longer the current statement of it). The row survives, because a fee run
    for a past period has to be able to see the assignment that governed it.
    """
    await conn.execute(
        f"""
        UPDATE {TABLE_ASSIGNMENTS}
           SET effective_to = $1::date, valid_to = now()
         WHERE id = $2::uuid AND org_id = $3::uuid
           AND valid_to IS NULL AND system_to IS NULL
        """,
        ends_on, assignment_id, org_id,
    )


async def end_assignment(
    conn, org_id: str, assignment_id: Any, *, effective_to: date | None = None
) -> dict[str, Any]:
    """Close one assignment. Idempotent-safe: closing a closed one is refused
    with a message rather than silently re-closing it at a new date."""
    org_id = _require_org(org_id)
    assignment_id = _as_uuid_text(assignment_id, field="assignment_id")
    ends_on = effective_to or date.today()

    async with _OrgWrite(conn, org_id) as tx:
        row = await tx.fetchrow(
            f"""
            SELECT a.id::text AS id, a.scope_type, a.effective_from
            FROM {TABLE_ASSIGNMENTS} a
            WHERE a.id = $1::uuid AND a.org_id = $2::uuid AND {_current('a')}
            """,
            assignment_id, org_id,
        )
        if row is None:
            raise FeeScheduleNotFoundError(
                f"fee assignment {assignment_id} is not a current assignment in "
                f"this org"
            )
        if ends_on < row["effective_from"]:
            raise FeeScheduleError(
                f"effective_to ({ends_on}) is before the assignment's "
                f"effective_from ({row['effective_from']})"
            )
        await _close_assignment(tx, org_id, assignment_id, ends_on)

    return {"id": assignment_id, "effective_to": ends_on}


# ═══════════════════════════════════════════════════════════════════════════
# Precedence resolution
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ResolvedAssignment:
    """The assignment that governs an account, and the ones it beat.

    ``losers`` is carried rather than discarded, mirroring fee32's
    ``PrecedenceOutcome``: "why is this account on the household schedule and
    not the org default" is a question an operator asks, and answering it from
    a function that returned only the winner means re-deriving the whole
    resolution by hand.
    """

    assignment_id: str
    fee_schedule_id: str
    scope_type: str
    scope_id: str | None
    precedence: int
    schedule_code: str
    schedule_version: int
    schedule_status: str
    losers: tuple[dict[str, Any], ...] = ()


async def resolve_assignment_for_account(
    conn, org_id: str, account_id: Any, *, as_of: date | None = None
) -> ResolvedAssignment | None:
    """Which schedule governs this account on ``as_of``, and why.

    Candidate scopes, gathered from the account itself:

        ACCOUNT        the account id
        BILLING_GROUP  every group it is an ACTIVE member of
        HOUSEHOLD      accounts.household_id
        ENTITY         accounts.primary_entity_id
        ORG_DEFAULT    scope_id IS NULL

    Ranked by ``precedence`` ascending. Ties inside one precedence — which the
    database does not prevent, since ``fee_assignments`` has no unique index —
    are broken by the later ``effective_from``, then the later ``created_at``,
    so the answer is at least deterministic. :func:`create_assignment` closes
    the incumbent to stop ties arising in the first place.

    Returns ``None`` when nothing matches, rather than falling back to
    anything. An account with no assignment and no org default is not billed at
    zero; it is not billed, and a caller has to decide what that means.
    """
    org_id = _require_org(org_id)
    account_id = _as_uuid_text(account_id, field="account_id")
    as_of = as_of or date.today()

    account = await conn.fetchrow(
        f"""
        SELECT a.id::text AS id,
               a.household_id::text AS household_id,
               a.primary_entity_id::text AS primary_entity_id
        FROM {TABLE_ACCOUNTS} a
        WHERE a.id = $1::uuid AND a.org_id = $2::uuid AND {_current('a')}
        """,
        account_id, org_id,
    )
    if account is None:
        raise FeeScheduleNotFoundError(
            f"account {account_id} is not a current account in this org"
        )

    group_rows = await conn.fetch(
        f"""
        SELECT m.billing_group_id::text AS id
        FROM {TABLE_BILLING_GROUP_MEMBERS} m
        JOIN {TABLE_BILLING_GROUPS} g
          ON g.id = m.billing_group_id AND g.org_id = m.org_id AND {_current('g')}
        WHERE m.account_id = $1::uuid AND m.org_id = $2::uuid AND {_current('m')}
        """,
        account_id, org_id,
    )
    group_ids = [r["id"] for r in group_rows]

    rows = await conn.fetch(
        f"""
        SELECT a.id::text              AS assignment_id,
               a.fee_schedule_id::text AS fee_schedule_id,
               a.scope_type, a.scope_id::text AS scope_id,
               a.precedence, a.effective_from, a.effective_to, a.created_at,
               s.code AS schedule_code, s.version AS schedule_version,
               s.status AS schedule_status
        FROM {TABLE_ASSIGNMENTS} a
        JOIN {TABLE_SCHEDULES} s
          ON s.id = a.fee_schedule_id AND s.org_id = a.org_id AND {_current('s')}
        WHERE a.org_id = $1::uuid
          AND {_current('a')}
          AND a.effective_from <= $2::date
          AND (a.effective_to IS NULL OR a.effective_to > $2::date)
          AND (
                (a.scope_type = 'ACCOUNT'       AND a.scope_id = $3::uuid)
             OR (a.scope_type = 'BILLING_GROUP' AND a.scope_id = ANY($4::uuid[]))
             OR (a.scope_type = 'HOUSEHOLD'     AND a.scope_id = $5::uuid)
             OR (a.scope_type = 'ENTITY'        AND a.scope_id = $6::uuid)
             OR (a.scope_type = 'ORG_DEFAULT'   AND a.scope_id IS NULL)
          )
        ORDER BY a.precedence ASC, a.effective_from DESC, a.created_at DESC
        """,
        org_id, as_of, account_id, group_ids,
        account["household_id"], account["primary_entity_id"],
    )
    if not rows:
        return None

    winner = rows[0]
    return ResolvedAssignment(
        assignment_id=winner["assignment_id"],
        fee_schedule_id=winner["fee_schedule_id"],
        scope_type=winner["scope_type"],
        scope_id=winner["scope_id"],
        precedence=int(winner["precedence"]),
        schedule_code=winner["schedule_code"],
        schedule_version=int(winner["schedule_version"]),
        schedule_status=winner["schedule_status"],
        losers=tuple(
            {
                "assignment_id": r["assignment_id"],
                "scope_type": r["scope_type"],
                "scope_id": r["scope_id"],
                "precedence": int(r["precedence"]),
                "fee_schedule_id": r["fee_schedule_id"],
            }
            for r in rows[1:]
        ),
    )


async def list_assignments(
    conn,
    org_id: str,
    *,
    fee_schedule_id: Any = None,
    scope_type: str | None = None,
    scope_id: Any = None,
    include_ended: bool = False,
) -> list[dict[str, Any]]:
    """Assignments, filtered. ``include_ended`` brings back closed rows too."""
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        f"""
        SELECT a.id::text              AS id,
               a.fee_schedule_id::text AS fee_schedule_id,
               a.scope_type, a.scope_id::text AS scope_id, a.precedence,
               a.effective_from, a.effective_to,
               a.agreement_document_id::text AS agreement_document_id,
               a.valid_to, a.system_to,
               s.code AS schedule_code, s.version AS schedule_version,
               s.status AS schedule_status
        FROM {TABLE_ASSIGNMENTS} a
        JOIN {TABLE_SCHEDULES} s
          ON s.id = a.fee_schedule_id AND s.org_id = a.org_id
        WHERE a.org_id = $1::uuid
          AND ($2::boolean OR (a.valid_to IS NULL AND a.system_to IS NULL))
          AND ($3::uuid IS NULL OR a.fee_schedule_id = $3::uuid)
          AND ($4::text IS NULL OR a.scope_type = $4::text)
          AND ($5::uuid IS NULL OR a.scope_id = $5::uuid)
        ORDER BY a.precedence ASC, a.effective_from DESC
        """,
        org_id, include_ended,
        _as_uuid_text(fee_schedule_id, field="fee_schedule_id")
        if fee_schedule_id else None,
        scope_type,
        _as_uuid_text(scope_id, field="scope_id") if scope_id else None,
    )
    return [dict(r) for r in rows]


__all__ = [
    "DEFINITION_FIELDS",
    "EDITABLE_SCHEDULE_FIELDS",
    "READ_PERMISSION",
    "SCOPE_PRECEDENCE",
    "STATUS_APPROVED",
    "STATUS_DRAFT",
    "STATUS_RETIRED",
    "TIER_FIELDS",
    "WRITE_PERMISSION",
    "EditOutcome",
    "FeeScheduleError",
    "FeeScheduleInvalid",
    "FeeScheduleNotFoundError",
    "ResolvedAssignment",
    "ScheduleStatusError",
    "ScopeIdRequiredError",
    "ScopeLinkError",
    "create_assignment",
    "create_schedule",
    "end_assignment",
    "get_schedule",
    "list_assignments",
    "list_schedules",
    "load_schedule",
    "load_tiers",
    "resolve_assignment_for_account",
    "retire_schedule",
    "submit_for_approval",
    "update_schedule",
]
