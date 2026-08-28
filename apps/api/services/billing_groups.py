"""Billing groups — the breakpoint aggregation unit. Sprint fee33.

A billing group answers ONE question: which accounts' values sum together to
decide something. It is deliberately NOT a household.

    household        a CRM relationship. "The Smiths." Who belongs with whom.
    billing group    an arithmetic container. Whose money adds up with whose.

They coincide often enough that conflating them looks harmless, and they
diverge in exactly the cases that cost money: a trust reported alongside the
family but billed standalone; two households that negotiated one combined
breakpoint. So ``billing_groups.household_id`` is an ADVISORY link — it tells
the admin screen which household to file a group under and nothing else.
Membership lives in ``billing_group_members`` and is read from nowhere else.

Task 1 looked for an existing structure implying a natural default group per
household and found the opposite. Two household groupings exist and
``services/households.py`` documents them as never-to-be-conflated:
``household_memberships`` is many-to-many and OVERLAPS by design, while
``entities.primary_household_id`` is at-most-one. Deriving a BREAKPOINT group
from the overlapping one would place one entity's value in two groups, which is
the one thing a breakpoint tier cannot survive. Nothing here auto-creates a
group, and ``create_household`` was left untouched.


THE ONE RULE, AND WHY IT IS PYTHON RATHER THAN AN INDEX
──────────────────────────────────────────────────────────────────────────────
An account may belong to at most one ACTIVE ``BREAKPOINT`` group at a time.
Two BREAKPOINT memberships means the account's value counts twice toward a
tier, and the client is billed at a rate they did not qualify for.

``STATEMENT`` and ``PAYER`` carry no such restriction, and that is a design
decision rather than an oversight. A joint account genuinely appears on two
different statement groupings — one per spouse — and split-billing arrangements
put one account behind two payers. Restricting those would break real
arrangements to enforce a rule only breakpoints need.

This cannot be a partial unique index. The predicate depends on
``billing_groups.group_type``, a column on the OTHER table, and a Postgres index
predicate may reference only the indexed table's own columns. The three ways
around that were each rejected:

  * Denormalise ``group_type`` onto the member row. The two copies drift the
    moment a group is retyped, and the stale copy is the one the index trusts.
  * A CHECK containing a subquery. Not supported by Postgres.
  * A constraint trigger. Puts a hard database refusal on a write path, which
    fee32's RFC already settled against for this table family — one bad row
    should not fail every good row behind it.

So it lives here, in :func:`assert_breakpoint_available`, and every write path
into ``billing_group_members`` calls it.


HOW IT HOLDS UNDER CONCURRENCY
──────────────────────────────────────────────────────────────────────────────
A check-then-insert in application code is a race by default: two transactions
both read "no conflict", both insert, and the invariant is gone with no error
raised anywhere. The database is not going to catch this one, because the whole
reason the rule is here is that no index can express it.

Every write therefore takes ``pg_advisory_xact_lock`` keyed on the ACCOUNT
before it reads. Two concurrent attempts to place the same account serialise;
the second sees the first's committed row and raises. The lock is per-account,
so unrelated accounts never contend, and it is a transaction-scoped advisory
lock rather than ``SELECT ... FOR UPDATE`` on ``public.accounts`` so that
placing an account in a group does not block an unrelated edit to the account
itself.

The lock key is namespaced by :data:`_ADVISORY_NAMESPACE` so it cannot collide
with an advisory lock some other subsystem takes on the same uuid.


THE OTHER DOOR INTO THE SAME VIOLATION
──────────────────────────────────────────────────────────────────────────────
[FIND] The sprint prompt specifies the check "on every insert/update to
billing_group_members". That is necessary but not sufficient. Retyping an
existing STATEMENT group to BREAKPOINT violates the rule for every member it
already holds, without touching billing_group_members at all.
:func:`update_billing_group` therefore runs the same check across the group's
whole active membership before allowing that transition. Without it the
constraint would be trivially bypassable by anyone who could edit a group.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.portfolio_assets import _OrgWrite, _require_org

TABLE_GROUPS = "public.billing_groups"
TABLE_MEMBERS = "public.billing_group_members"
TABLE_ACCOUNTS = "public.accounts"
TABLE_HOUSEHOLDS = "public.households"

#: Reads sit behind the same permission as the rest of the portfolio surface;
#: writes behind ``manage_billing``, matching fee31's custody importer rather
#: than inventing a fourth name. Both already exist in ``public.permissions``
#: (manage_billing → admin, super_admin; view_portfolio → six roles).
READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_billing"

GROUP_TYPE_BREAKPOINT = "BREAKPOINT"
GROUP_TYPE_STATEMENT = "STATEMENT"
GROUP_TYPE_PAYER = "PAYER"

#: Mirrors ``billing_groups_group_type_check``. Declared here so the CHECK and
#: the code that writes it cannot drift silently — a value this module invents
#: that the constraint does not admit fails loudly at write time.
GROUP_TYPES = (GROUP_TYPE_BREAKPOINT, GROUP_TYPE_STATEMENT, GROUP_TYPE_PAYER)

#: The types an account may hold simultaneously without restriction. Kept as an
#: explicit set rather than "everything that is not BREAKPOINT", so adding a
#: fourth group type in fee34 is a decision someone has to make here rather than
#: something that silently defaults to unrestricted.
UNRESTRICTED_GROUP_TYPES = frozenset({GROUP_TYPE_STATEMENT, GROUP_TYPE_PAYER})

#: Types subject to the at-most-one-active rule.
EXCLUSIVE_GROUP_TYPES = frozenset({GROUP_TYPE_BREAKPOINT})

#: Fields the admin screen may edit. UX4's rule: this list is published to the
#: client from the server's own response and EMPTIED for a caller without
#: WRITE_PERMISSION — never defaulted client-side.
EDITABLE_GROUP_FIELDS = ("name", "group_type", "household_id", "notes")

#: An arbitrary but FIXED namespace for this module's advisory locks. Changing
#: it silently disables the mutual exclusion between an old deploy and a new one
#: mid-rollout, so it is a constant and not a config value.
_ADVISORY_NAMESPACE = 0x0FEE0033

#: "Active" on both temporal axes, matching ``portfolio_assets._current``.
def _current(alias: str) -> str:
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


# ── Errors ───────────────────────────────────────────────────────────────────


class BillingGroupError(ValueError):
    """A billing-group write was refused for a reason the caller can fix."""


class BreakpointOverlapError(BillingGroupError):
    """The account is already in a different active BREAKPOINT group.

    Its own class, not a generic exception, for two reasons. An import path that
    wants to quarantine one bad mapping and keep going needs to catch exactly
    this without also swallowing a missing FK or a bad group type. And the
    router needs to turn it into a 409 with a message naming both groups, which
    it cannot do from a ``ValueError`` carrying only prose.

    The identifying ids are attributes, not just interpolations in the message,
    so a caller can build a link to the offending group without re-parsing the
    string.
    """

    def __init__(
        self,
        *,
        account_id: str,
        account_label: str | None,
        existing_group_id: str,
        existing_group_name: str,
        attempted_group_id: str | None,
        attempted_group_name: str | None,
    ) -> None:
        self.account_id = account_id
        self.account_label = account_label
        self.existing_group_id = existing_group_id
        self.existing_group_name = existing_group_name
        self.attempted_group_id = attempted_group_id
        self.attempted_group_name = attempted_group_name

        who = f"Account {account_label or account_id}"
        target = (
            f"BREAKPOINT group {attempted_group_name!r} ({attempted_group_id})"
            if attempted_group_id
            else "a second BREAKPOINT group"
        )
        super().__init__(
            f"{who} cannot join {target}: it is already an active member of "
            f"BREAKPOINT group {existing_group_name!r} ({existing_group_id}). "
            f"An account may belong to at most one active BREAKPOINT group — "
            f"remove it from {existing_group_name!r} first, or add it to a "
            f"STATEMENT or PAYER group instead, which carry no such restriction."
        )


class BillingGroupNotFoundError(BillingGroupError):
    """A referenced group or account is not this org's, or is not current.

    Deliberately indistinguishable from "does not exist at all". The FKs on
    ``billing_group_members`` reference ``id`` alone with no org predicate, so a
    caller-supplied id belonging to another tenant satisfies them — fee32 hit
    this exact shape on ``portfolio.positions.account_id``. Telling the caller
    "that id exists but is not yours" would confirm the row's existence across a
    tenant boundary.
    """


# ── Helpers ──────────────────────────────────────────────────────────────────


def _as_uuid(v):
    return v if not isinstance(v, str) else UUID(v)


def _check_group_type(value: str) -> str:
    if value not in GROUP_TYPES:
        raise BillingGroupError(
            f"group_type={value!r} is not one of {list(GROUP_TYPES)}"
        )
    return value


def _clean_name(value: str) -> str:
    """Trim and refuse blank, mirroring ``billing_groups_name_not_blank_check``.

    Checked here as well as in the database because a whitespace-only name
    reaching the CHECK produces a constraint-violation traceback rather than a
    message an operator can act on.
    """
    name = (value or "").strip()
    if not name:
        raise BillingGroupError("name is required and cannot be blank")
    return name


async def _lock_account(conn, account_id: str) -> None:
    """Serialise every membership decision about one account.

    See the module docstring. Transaction-scoped, so it releases with the
    enclosing ``_OrgWrite`` whether that commits or rolls back — there is no
    unlock path to forget.
    """
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1::text, $2))",
        str(account_id), _ADVISORY_NAMESPACE,
    )


async def _load_group(conn, org_id: str, group_id: str) -> dict:
    """The group, or :class:`BillingGroupNotFoundError`. Org-scoped explicitly."""
    row = await conn.fetchrow(
        f"""
        SELECT g.id::text AS id, g.name, g.group_type,
               g.household_id::text AS household_id, g.notes
        FROM {TABLE_GROUPS} g
        WHERE g.id = $1::uuid AND g.org_id = $2::uuid AND {_current('g')}
        """,
        str(group_id), org_id,
    )
    if row is None:
        raise BillingGroupNotFoundError(
            f"billing group {group_id} is not a current group in this org"
        )
    return dict(row)


async def _load_account(conn, org_id: str, account_id: str) -> dict:
    row = await conn.fetchrow(
        f"""
        SELECT a.id::text AS id, a.account_number_masked,
               a.household_id::text AS household_id
        FROM {TABLE_ACCOUNTS} a
        WHERE a.id = $1::uuid AND a.org_id = $2::uuid AND {_current('a')}
        """,
        str(account_id), org_id,
    )
    if row is None:
        raise BillingGroupNotFoundError(
            f"account {account_id} is not a current account in this org"
        )
    return dict(row)


# ── The constraint ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BreakpointConflict:
    """The active BREAKPOINT membership blocking a placement, if any."""

    account_id: str
    account_label: str | None
    group_id: str
    group_name: str
    member_id: str


async def find_breakpoint_conflict(
    conn,
    org_id: str,
    *,
    account_id: str,
    exclude_group_id: str | None = None,
    exclude_member_id: str | None = None,
) -> BreakpointConflict | None:
    """The account's existing active BREAKPOINT membership, or ``None``.

    Read-only, and callable on its own — the admin screen uses it to grey out a
    group in the picker BEFORE the operator submits, so the refusal is visible
    rather than merely correct.

    ``exclude_group_id`` skips a group the caller is already placing into, which
    is what makes re-adding an account to the group it is already in a no-op
    rather than a self-conflict. ``exclude_member_id`` does the same for the row
    being updated, so moving a membership does not collide with itself.

    Assumes the caller holds this account's advisory lock if it intends to act
    on the answer. On its own it is a snapshot and nothing more.
    """
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"""
        SELECT m.id::text          AS member_id,
               g.id::text          AS group_id,
               g.name              AS group_name,
               a.account_number_masked
        FROM {TABLE_MEMBERS} m
        JOIN {TABLE_GROUPS} g
          ON g.id = m.billing_group_id AND g.org_id = m.org_id AND {_current('g')}
        LEFT JOIN {TABLE_ACCOUNTS} a
          ON a.id = m.account_id AND a.org_id = m.org_id AND {_current('a')}
        WHERE m.account_id = $1::uuid
          AND m.org_id = $2::uuid
          AND {_current('m')}
          AND g.group_type = $3
          AND ($4::uuid IS NULL OR g.id <> $4::uuid)
          AND ($5::uuid IS NULL OR m.id <> $5::uuid)
        ORDER BY m.valid_from
        LIMIT 1
        """,
        str(account_id), org_id, GROUP_TYPE_BREAKPOINT,
        str(exclude_group_id) if exclude_group_id else None,
        str(exclude_member_id) if exclude_member_id else None,
    )
    if row is None:
        return None
    return BreakpointConflict(
        account_id=str(account_id),
        account_label=row["account_number_masked"],
        group_id=row["group_id"],
        group_name=row["group_name"],
        member_id=row["member_id"],
    )


async def assert_breakpoint_available(
    conn,
    org_id: str,
    *,
    account_id: str,
    target_group_id: str,
    target_group_type: str,
    target_group_name: str | None = None,
    exclude_member_id: str | None = None,
) -> None:
    """Raise :class:`BreakpointOverlapError` if this placement would double-count.

    THE function the prompt requires. Called by :func:`add_member`,
    :func:`move_member` and :func:`update_billing_group` — every path that can
    put an account into a BREAKPOINT group.

    A no-op for STATEMENT and PAYER targets: those are unrestricted by design,
    and short-circuiting here rather than in each caller means a future write
    path gets the correct behaviour for free instead of having to remember it.
    """
    if target_group_type not in EXCLUSIVE_GROUP_TYPES:
        return

    conflict = await find_breakpoint_conflict(
        conn, org_id,
        account_id=account_id,
        exclude_group_id=target_group_id,
        exclude_member_id=exclude_member_id,
    )
    if conflict is None:
        return

    raise BreakpointOverlapError(
        account_id=conflict.account_id,
        account_label=conflict.account_label,
        existing_group_id=conflict.group_id,
        existing_group_name=conflict.group_name,
        attempted_group_id=str(target_group_id),
        attempted_group_name=target_group_name,
    )


# ── Group CRUD ───────────────────────────────────────────────────────────────


async def create_billing_group(
    conn,
    org_id: str,
    *,
    name: str,
    group_type: str,
    household_id: str | None = None,
    notes: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Create a group. ``org_id`` is the caller's, never a request body's.

    ``household_id`` is optional and validated only for tenancy — a group with
    no household is a first-class case, not a degraded one.
    """
    org_id = _require_org(org_id)
    name = _clean_name(name)
    group_type = _check_group_type(group_type)

    async with _OrgWrite(conn, org_id) as c:
        if household_id is not None:
            ok = await c.fetchval(
                f"SELECT 1 FROM {TABLE_HOUSEHOLDS} WHERE id = $1::uuid AND org_id = $2::uuid",
                str(household_id), org_id,
            )
            if not ok:
                raise BillingGroupNotFoundError(
                    f"household {household_id} is not a household in this org"
                )
        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_GROUPS}
                (org_id, name, group_type, household_id, notes, created_by)
            VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6::uuid)
            RETURNING id::text AS id, org_id::text AS org_id, name, group_type,
                      household_id::text AS household_id, notes,
                      created_at, valid_from
            """,
            org_id, name, group_type,
            str(household_id) if household_id else None,
            notes,
            str(created_by) if created_by else None,
        )
    return dict(row)


async def update_billing_group(
    conn,
    org_id: str,
    group_id: str,
    *,
    name: str | None = None,
    group_type: str | None = None,
    household_id: str | None = None,
    notes: str | None = None,
    fields_set: set[str] | None = None,
) -> dict:
    """Restate a group on the VALID axis, per Rule 3.

    ``billing_group_members.billing_group_id`` is a live FK pointing at
    ``billing_groups.id``, so a valid-axis restatement that minted a new id would
    orphan every membership. This closes the old row on the SYSTEM axis and
    KEEPS the id — the archival form of Rule 3, the same choice
    ``portfolio.assets`` made for the same reason.

    ``fields_set`` carries the caller's ``model_fields_set`` so a sparse PATCH
    can distinguish "household_id: null" (unlink) from "household_id absent"
    (leave alone). Without it, every PATCH would silently unlink the household.

    RETYPING TO BREAKPOINT IS CHECKED, and this is the part the prompt's
    "on every insert/update to billing_group_members" does not cover. See the
    module docstring.
    """
    org_id = _require_org(org_id)
    touched = fields_set if fields_set is not None else {
        k for k, v in (
            ("name", name), ("group_type", group_type),
            ("household_id", household_id), ("notes", notes),
        ) if v is not None
    }
    unknown = touched - set(EDITABLE_GROUP_FIELDS)
    if unknown:
        raise BillingGroupError(f"not editable: {sorted(unknown)}")

    async with _OrgWrite(conn, org_id) as c:
        existing = await _load_group(c, org_id, group_id)

        merged = dict(existing)
        if "name" in touched:
            merged["name"] = _clean_name(name)
        if "group_type" in touched:
            merged["group_type"] = _check_group_type(group_type)
        if "household_id" in touched:
            merged["household_id"] = str(household_id) if household_id else None
        if "notes" in touched:
            merged["notes"] = notes

        # Validate the MERGED row, not just the supplied fields — a PATCH that
        # only sets group_type still has to be legal in combination with the
        # name and household it is silently keeping.
        if merged["household_id"] is not None:
            ok = await c.fetchval(
                f"SELECT 1 FROM {TABLE_HOUSEHOLDS} WHERE id = $1::uuid AND org_id = $2::uuid",
                merged["household_id"], org_id,
            )
            if not ok:
                raise BillingGroupNotFoundError(
                    f"household {merged['household_id']} is not a household in this org"
                )

        becoming_breakpoint = (
            merged["group_type"] == GROUP_TYPE_BREAKPOINT
            and existing["group_type"] != GROUP_TYPE_BREAKPOINT
        )
        if becoming_breakpoint:
            member_ids = [
                r["account_id"] for r in await c.fetch(
                    f"""
                    SELECT m.account_id::text AS account_id
                    FROM {TABLE_MEMBERS} m
                    WHERE m.billing_group_id = $1::uuid AND m.org_id = $2::uuid
                      AND {_current('m')}
                    ORDER BY m.valid_from
                    """,
                    str(group_id), org_id,
                )
            ]
            # Lock every affected account before checking any of them, so a
            # concurrent add cannot slip in behind an account already cleared.
            for account_id in member_ids:
                await _lock_account(c, account_id)
            for account_id in member_ids:
                await assert_breakpoint_available(
                    c, org_id,
                    account_id=account_id,
                    target_group_id=str(group_id),
                    target_group_type=GROUP_TYPE_BREAKPOINT,
                    target_group_name=merged["name"],
                )

        row = await c.fetchrow(
            f"""
            UPDATE {TABLE_GROUPS}
               SET name = $3, group_type = $4, household_id = $5::uuid,
                   notes = $6, updated_at = now()
             WHERE id = $1::uuid AND org_id = $2::uuid
               AND valid_to IS NULL AND system_to IS NULL
            RETURNING id::text AS id, org_id::text AS org_id, name, group_type,
                      household_id::text AS household_id, notes,
                      created_at, updated_at
            """,
            str(group_id), org_id, merged["name"], merged["group_type"],
            merged["household_id"], merged["notes"],
        )
    return dict(row)


async def archive_billing_group(conn, org_id: str, group_id: str) -> bool:
    """Retire a group and close its memberships. Never a hard delete.

    Memberships close alongside it. A closed group whose members stayed active
    would leave an account occupying a BREAKPOINT slot it can no longer be seen
    to occupy — the account would be unplaceable, with nothing in the UI
    explaining why.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        await _load_group(c, org_id, group_id)
        await c.execute(
            f"""
            UPDATE {TABLE_MEMBERS} SET valid_to = now(), system_to = now()
             WHERE billing_group_id = $1::uuid AND org_id = $2::uuid
               AND valid_to IS NULL AND system_to IS NULL
            """,
            str(group_id), org_id,
        )
        closed = await c.fetchval(
            f"""
            UPDATE {TABLE_GROUPS} SET valid_to = now(), system_to = now()
             WHERE id = $1::uuid AND org_id = $2::uuid
               AND valid_to IS NULL AND system_to IS NULL
            RETURNING id
            """,
            str(group_id), org_id,
        )
    return closed is not None


async def list_billing_groups(
    conn,
    org_id: str,
    *,
    group_type: str | None = None,
    household_id: str | None = None,
    include_unhoused: bool = True,
) -> list[dict]:
    """Active groups for this org, with their live member counts.

    ``include_unhoused`` exists because filtering by household must not quietly
    hide the household_id IS NULL groups when no filter was asked for. A group
    with no household is not an orphan to be tidied away; it is the case this
    table exists to model.
    """
    org_id = _require_org(org_id)
    if group_type is not None:
        _check_group_type(group_type)
    rows = await conn.fetch(
        f"""
        SELECT g.id::text AS id, g.name, g.group_type,
               g.household_id::text AS household_id,
               h.name AS household_name,
               g.notes, g.created_at, g.updated_at,
               COALESCE(m.member_count, 0) AS member_count
        FROM {TABLE_GROUPS} g
        LEFT JOIN {TABLE_HOUSEHOLDS} h
          ON h.id = g.household_id AND h.org_id = g.org_id
        LEFT JOIN (
            SELECT billing_group_id, count(*) AS member_count
            FROM {TABLE_MEMBERS}
            WHERE valid_to IS NULL AND system_to IS NULL
            GROUP BY billing_group_id
        ) m ON m.billing_group_id = g.id
        WHERE g.org_id = $1::uuid AND {_current('g')}
          AND ($2::text IS NULL OR g.group_type = $2)
          AND ($3::uuid IS NULL OR g.household_id = $3::uuid
               OR ($4 AND g.household_id IS NULL))
        ORDER BY g.group_type, lower(g.name)
        """,
        org_id, group_type,
        str(household_id) if household_id else None,
        bool(include_unhoused) and household_id is None,
    )
    return [dict(r) for r in rows]


# ── Membership ───────────────────────────────────────────────────────────────


async def list_members(conn, org_id: str, group_id: str) -> list[dict]:
    """Active members of one group."""
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        f"""
        SELECT m.id::text AS id, m.account_id::text AS account_id,
               a.account_number_masked, a.custodian_code, a.registration_type,
               a.is_billable, a.household_id::text AS account_household_id,
               m.valid_from, m.created_at
        FROM {TABLE_MEMBERS} m
        JOIN {TABLE_GROUPS} g
          ON g.id = m.billing_group_id AND g.org_id = m.org_id AND {_current('g')}
        LEFT JOIN {TABLE_ACCOUNTS} a
          ON a.id = m.account_id AND a.org_id = m.org_id AND {_current('a')}
        WHERE m.billing_group_id = $1::uuid AND m.org_id = $2::uuid
          AND {_current('m')}
        ORDER BY a.account_number_masked NULLS LAST, m.valid_from
        """,
        str(group_id), org_id,
    )
    return [dict(r) for r in rows]


async def add_member(
    conn,
    org_id: str,
    *,
    group_id: str,
    account_id: str,
    added_by: str | None = None,
) -> dict:
    """Place an account in a group.

    Raises :class:`BreakpointOverlapError` if the target is a BREAKPOINT group
    and the account already sits in a different active one — naming both.

    Idempotent for an account already active in THIS group: returns the existing
    membership rather than raising. Re-adding what is already there is an
    operator double-click, not an error, and the partial unique index would
    otherwise surface it as a constraint traceback.
    """
    org_id = _require_org(org_id)

    async with _OrgWrite(conn, org_id) as c:
        group = await _load_group(c, org_id, group_id)
        account = await _load_account(c, org_id, account_id)

        # Lock BEFORE the conflict read, or two concurrent placements both see a
        # clean slate. Everything from here to COMMIT is serialised per account.
        await _lock_account(c, account["id"])

        existing = await c.fetchrow(
            f"""
            SELECT m.id::text AS id
            FROM {TABLE_MEMBERS} m
            WHERE m.billing_group_id = $1::uuid AND m.account_id = $2::uuid
              AND m.org_id = $3::uuid AND {_current('m')}
            """,
            group["id"], account["id"], org_id,
        )
        if existing is not None:
            return await _member_row(c, org_id, existing["id"])

        await assert_breakpoint_available(
            c, org_id,
            account_id=account["id"],
            target_group_id=group["id"],
            target_group_type=group["group_type"],
            target_group_name=group["name"],
        )

        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_MEMBERS}
                (org_id, billing_group_id, account_id, added_by)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid)
            RETURNING id::text AS id
            """,
            org_id, group["id"], account["id"],
            str(added_by) if added_by else None,
        )
        return await _member_row(c, org_id, row["id"])


async def remove_member(
    conn,
    org_id: str,
    *,
    group_id: str,
    account_id: str,
) -> bool:
    """End a membership by CLOSING the row. Never a hard delete.

    Sets both ``valid_to`` and ``system_to``, which is what takes the row out of
    every ``_current`` predicate — including the BREAKPOINT conflict query — and
    so frees the account to join a different BREAKPOINT group immediately.

    The row itself stays. "This account was in the Smith breakpoint group for Q1
    and moved out in Q2" is a fee input, and deleting it makes a past invoice
    unreproducible.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        await _lock_account(c, str(account_id))
        closed = await c.fetchval(
            f"""
            UPDATE {TABLE_MEMBERS} SET valid_to = now(), system_to = now()
             WHERE billing_group_id = $1::uuid AND account_id = $2::uuid
               AND org_id = $3::uuid
               AND valid_to IS NULL AND system_to IS NULL
            RETURNING id
            """,
            str(group_id), str(account_id), org_id,
        )
    return closed is not None


async def move_member(
    conn,
    org_id: str,
    *,
    member_id: str,
    target_group_id: str,
    moved_by: str | None = None,
) -> dict:
    """Move one membership to another group, atomically.

    The UPDATE path the prompt asks to cover. Close-then-open in ONE transaction
    rather than a bare ``UPDATE ... SET billing_group_id``, because the
    membership's valid_from is a fee input: an account that moved groups
    mid-quarter belongs to both for part of it, and rewriting the row in place
    would erase the first half.

    The conflict check runs against the state AFTER the old row is closed —
    otherwise moving an account between two BREAKPOINT groups would always
    conflict with the membership it is leaving.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        current = await c.fetchrow(
            f"""
            SELECT m.id::text AS id, m.account_id::text AS account_id,
                   m.billing_group_id::text AS billing_group_id
            FROM {TABLE_MEMBERS} m
            WHERE m.id = $1::uuid AND m.org_id = $2::uuid AND {_current('m')}
            """,
            str(member_id), org_id,
        )
        if current is None:
            raise BillingGroupNotFoundError(
                f"membership {member_id} is not an active membership in this org"
            )
        target = await _load_group(c, org_id, target_group_id)
        await _lock_account(c, current["account_id"])

        if current["billing_group_id"] == target["id"]:
            return await _member_row(c, org_id, current["id"])

        await c.execute(
            f"UPDATE {TABLE_MEMBERS} SET valid_to = now(), system_to = now() "
            f"WHERE id = $1::uuid AND org_id = $2::uuid",
            current["id"], org_id,
        )
        await assert_breakpoint_available(
            c, org_id,
            account_id=current["account_id"],
            target_group_id=target["id"],
            target_group_type=target["group_type"],
            target_group_name=target["name"],
            exclude_member_id=current["id"],
        )
        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_MEMBERS}
                (org_id, billing_group_id, account_id, added_by)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid)
            RETURNING id::text AS id
            """,
            org_id, target["id"], current["account_id"],
            str(moved_by) if moved_by else None,
        )
        return await _member_row(c, org_id, row["id"])


async def _member_row(conn, org_id: str, member_id: str) -> dict:
    row = await conn.fetchrow(
        f"""
        SELECT m.id::text AS id, m.billing_group_id::text AS billing_group_id,
               m.account_id::text AS account_id, m.valid_from, m.created_at,
               g.name AS group_name, g.group_type,
               a.account_number_masked
        FROM {TABLE_MEMBERS} m
        JOIN {TABLE_GROUPS} g ON g.id = m.billing_group_id AND g.org_id = m.org_id
        LEFT JOIN {TABLE_ACCOUNTS} a
          ON a.id = m.account_id AND a.org_id = m.org_id AND {_current('a')}
        WHERE m.id = $1::uuid AND m.org_id = $2::uuid
        """,
        str(member_id), org_id,
    )
    return dict(row)


async def list_account_memberships(conn, org_id: str, account_id: str) -> list[dict]:
    """Every active group this account is in, across all types.

    The admin screen's "why can't I add this?" answer, and the shape check 3
    asserts against: one account legitimately holding a BREAKPOINT, a STATEMENT
    and a PAYER membership at once.
    """
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        f"""
        SELECT m.id::text AS id, g.id::text AS group_id, g.name AS group_name,
               g.group_type, m.valid_from
        FROM {TABLE_MEMBERS} m
        JOIN {TABLE_GROUPS} g
          ON g.id = m.billing_group_id AND g.org_id = m.org_id AND {_current('g')}
        WHERE m.account_id = $1::uuid AND m.org_id = $2::uuid AND {_current('m')}
        ORDER BY g.group_type, lower(g.name)
        """,
        str(account_id), org_id,
    )
    return [dict(r) for r in rows]


async def linkable_households(conn, org_id: str) -> list[dict]:
    """This org's households, for the admin screen's optional link picker.

    Published inside the billing-groups envelope rather than fetched from a
    separate endpoint. ``routers.households`` exposes create/rename/delete and
    per-entity lookups but no plain org-wide list, and adding one here would
    mean choosing a permission for a household directory as a side effect of a
    billing sprint. The screen needs names to render a dropdown, and Rule 1 says
    those come from the API's own response — so they come from this one.
    """
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        f"SELECT id::text AS id, name FROM {TABLE_HOUSEHOLDS} "
        f"WHERE org_id = $1::uuid ORDER BY lower(name)",
        org_id,
    )
    return [dict(r) for r in rows]


async def assignable_accounts(
    conn, org_id: str, *, group_id: str
) -> list[dict[str, Any]]:
    """This org's current accounts, each annotated with why it may be blocked.

    Powers the add-member picker. Every account is returned — a blocked one is
    returned WITH its blocker rather than filtered out, so the operator sees
    "already in the Smith Breakpoint group" instead of an account that silently
    is not in the list and looks like a data problem.
    """
    org_id = _require_org(org_id)
    group = await _load_group(conn, org_id, group_id)
    exclusive = group["group_type"] in EXCLUSIVE_GROUP_TYPES
    rows = await conn.fetch(
        f"""
        SELECT a.id::text AS id, a.account_number_masked, a.custodian_code,
               a.registration_type, a.is_billable,
               a.household_id::text AS household_id,
               EXISTS (
                   SELECT 1 FROM {TABLE_MEMBERS} m
                   WHERE m.account_id = a.id AND m.billing_group_id = $2::uuid
                     AND m.org_id = a.org_id AND {_current('m')}
               ) AS already_in_group,
               blocker.group_id::text   AS blocking_group_id,
               blocker.group_name       AS blocking_group_name
        FROM {TABLE_ACCOUNTS} a
        LEFT JOIN LATERAL (
            SELECT g.id AS group_id, g.name AS group_name
            FROM {TABLE_MEMBERS} m
            JOIN {TABLE_GROUPS} g
              ON g.id = m.billing_group_id AND g.org_id = m.org_id AND {_current('g')}
            WHERE m.account_id = a.id AND m.org_id = a.org_id AND {_current('m')}
              AND $3 AND g.group_type = $4 AND g.id <> $2::uuid
            LIMIT 1
        ) blocker ON true
        WHERE a.org_id = $1::uuid AND {_current('a')}
        ORDER BY a.account_number_masked
        """,
        org_id, group["id"], exclusive, GROUP_TYPE_BREAKPOINT,
    )
    return [dict(r) for r in rows]
