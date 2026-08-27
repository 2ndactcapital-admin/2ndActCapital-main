"""Position ↔ account linkage — sprint fee32, Task 2.

Part 1 added an OPTIONAL ``account_id`` to ``portfolio.positions``. Optional is
the whole design: a directly-held asset (real estate, a direct PE stake) and an
SPV interest (``portfolio.assets.internal_spv_id``) have no custodial account
and stay NULL forever. Nothing here backfills, defaults, or requires one.

WHAT THIS MODULE DECIDES, AND WHY IT IS NOT A CONSTRAINT
──────────────────────────────────────────────────────────────────────────────
When a position DOES carry an ``account_id``, its ``owner_entity_id`` ought to
be one of that account's active ``account_owners``. Ought, not must. The RFC
settles it as an application-time check rather than a trigger or a CHECK, for
the same reason ``ownership_basis`` is validated in application code on this
table: a database refusal on an ingestion path turns one bad mapping into a
failed import of every good row behind it.

So there are exactly three outcomes, and they are deliberately not two:

1. **The account is not this org's.** Hard refusal, raised. ``positions_account_id_fkey``
   references ``accounts(id)`` with NO org predicate — a caller-supplied
   ``account_id`` belonging to another tenant satisfies the FK. Treating that as
   a reviewable warning would make the exception list the audit trail of a
   cross-tenant write that had already succeeded. It is refused before the row
   exists.

2. **The account is this org's, but the owner is not one of its owners.** The
   position is WRITTEN and an exception row is recorded. This is the case the
   sprint exists for: a reporting-tool export that maps an account to the wrong
   member, or an account whose ownership was restated after the position was
   booked. Both are real, both are correctable, and neither is a reason to lose
   the holding.

3. **The owner is an active owner.** Nothing is written but the position.

The distinction between "this account has owners, none of them is you" and
"this account has no active owners at all" is kept as two ``reason_code`` values
rather than one. They fail for different reasons and are fixed by different
people: the first is a mis-mapped import, the second is an incomplete account
record, and folding them together hides the second inside the first.

WHY THE EXCEPTION IS ITS OWN TABLE
──────────────────────────────────────────────────────────────────────────────
See ``migrations/fee32_position_account_exceptions.sql``. Short version:
fee31's ``account_import_exceptions`` requires a NOT NULL custody ``batch_id``
that two of the three position write paths do not have.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from services.portfolio_assets import (
    PortfolioError,
    _OrgWrite,
    _current,
    _require_org,
)

TABLE_ACCOUNTS = "public.accounts"
TABLE_ACCOUNT_OWNERS = "public.account_owners"
TABLE_POSITION_ACCOUNT_EXCEPTIONS = "public.position_account_exceptions"

#: Mirrors ``position_account_exceptions_reason_code_check``. Declared here so
#: the CHECK and the code that writes it cannot drift silently — a code this
#: module invents that the constraint does not admit fails loudly at write time
#: rather than being quietly absent from the review list.
REASON_OWNER_NOT_ACCOUNT_OWNER = "owner_not_account_owner"
REASON_ACCOUNT_HAS_NO_OWNERS = "account_has_no_owners"

REASON_CODES = frozenset({
    REASON_OWNER_NOT_ACCOUNT_OWNER,
    REASON_ACCOUNT_HAS_NO_OWNERS,
})


class AccountLinkError(PortfolioError):
    """The ``account_id`` on a position write is unusable. Raised, not recorded.

    Reserved for outcome 1 in the module docstring — an account that is not this
    org's. Every other mismatch is a recorded exception, not an exception type.
    """


@dataclass(frozen=True)
class AccountLinkCheck:
    """The result of checking one (account_id, owner_entity_id) pair."""

    account_id: str
    owner_entity_id: str
    ok: bool
    reason_code: str | None
    reason: str | None
    #: The account's active owner entity ids at the moment of the check. Carried
    #: onto the exception row as evidence: "who DOES own this account" is the
    #: first question a reviewer asks, and re-deriving it later reads the
    #: ownership as it is then, not as it was when the mismatch happened.
    owner_entity_ids: tuple[str, ...] = ()
    account_number_masked: str | None = None
    household_id: str | None = None


async def check_account_link(
    conn, org_id: str, *, account_id: str, owner_entity_id: str
) -> AccountLinkCheck:
    """Is ``owner_entity_id`` an active owner of ``account_id``?

    Raises :class:`AccountLinkError` when the account is not a current account
    in this org — including when it belongs to another tenant, which the
    org-blind FK on ``portfolio.positions.account_id`` would otherwise admit.

    Returns an :class:`AccountLinkCheck` in every other case, ``ok`` telling the
    caller whether to record an exception. It never writes.
    """
    org_id = _require_org(org_id)
    account_id = str(account_id)
    owner_entity_id = str(owner_entity_id)

    async with _OrgWrite(conn, org_id) as c:
        account = await c.fetchrow(
            f"""
            SELECT a.id::text AS id, a.account_number_masked,
                   a.household_id::text AS household_id
            FROM {TABLE_ACCOUNTS} a
            WHERE a.id = $1::uuid AND a.org_id = $2::uuid AND {_current('a')}
            """,
            account_id, org_id,
        )
        if account is None:
            raise AccountLinkError(
                f"account {account_id} is not a current account in org {org_id}. "
                f"A position may not name an account that belongs to another "
                f"tenant or has been closed — the foreign key on "
                f"portfolio.positions.account_id references accounts(id) with no "
                f"org predicate, so this check is the only thing standing "
                f"between a supplied id and a cross-tenant reference."
            )

        owners = [
            r["entity_id"] for r in await c.fetch(
                f"""
                SELECT DISTINCT o.entity_id::text AS entity_id
                FROM {TABLE_ACCOUNT_OWNERS} o
                WHERE o.account_id = $1::uuid AND o.org_id = $2::uuid
                  AND {_current('o')}
                ORDER BY 1
                """,
                account_id, org_id,
            )
        ]

    masked = account["account_number_masked"]
    common = dict(
        account_id=account_id,
        owner_entity_id=owner_entity_id,
        owner_entity_ids=tuple(owners),
        account_number_masked=masked,
        household_id=account["household_id"],
    )

    if owner_entity_id in owners:
        return AccountLinkCheck(ok=True, reason_code=None, reason=None, **common)

    if not owners:
        return AccountLinkCheck(
            ok=False,
            reason_code=REASON_ACCOUNT_HAS_NO_OWNERS,
            reason=(
                f"position owner {owner_entity_id} was linked to account "
                f"{masked}, which has no active account_owners rows at all. The "
                f"position is kept — the account record is what is incomplete, "
                f"not the holding."
            ),
            **common,
        )

    return AccountLinkCheck(
        ok=False,
        reason_code=REASON_OWNER_NOT_ACCOUNT_OWNER,
        reason=(
            f"position owner {owner_entity_id} is not an active owner of "
            f"account {masked}. That account's active owners are "
            f"{owners}. The position is kept and flagged rather than refused: "
            f"an owner mismatch is a mapping to correct, not a holding to lose."
        ),
        **common,
    )


async def record_account_link_exception(
    conn,
    org_id: str,
    *,
    position_id: str,
    check: AccountLinkCheck,
    source_system: str | None = None,
) -> str | None:
    """Record a failed check against a position that was already written.

    ``None`` when ``check.ok`` (nothing to record) or when an identical OPEN
    exception already exists — the partial unique index makes re-validating the
    same position idempotent, while a mismatch re-raised AFTER a reviewer closed
    the previous one is a new finding and is recorded again.
    """
    if check.ok:
        return None
    if check.reason_code not in REASON_CODES:
        raise AccountLinkError(
            f"reason_code {check.reason_code!r} is not one the deployed CHECK "
            f"admits: {sorted(REASON_CODES)}"
        )

    org_id = _require_org(org_id)
    detail = {
        "account_owner_entity_ids": list(check.owner_entity_ids),
        "account_number_masked": check.account_number_masked,
        "account_household_id": check.household_id,
    }
    async with _OrgWrite(conn, org_id) as c:
        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_POSITION_ACCOUNT_EXCEPTIONS}
                (org_id, position_id, account_id, owner_entity_id,
                 reason_code, reason, source_system, detail)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8::jsonb)
            ON CONFLICT (org_id, position_id, account_id, reason_code)
                WHERE reviewed_at IS NULL
            DO NOTHING
            RETURNING id::text
            """,
            org_id, str(position_id), check.account_id, check.owner_entity_id,
            check.reason_code, check.reason, source_system, json.dumps(detail),
        )


async def validate_position_account(
    conn,
    org_id: str,
    *,
    position_id: str,
    account_id: str,
    owner_entity_id: str,
    source_system: str | None = None,
) -> AccountLinkCheck:
    """Check, then record if it failed. The one call a write path makes.

    Called from ``portfolio_assets.create_position`` AFTER the INSERT, inside
    the same transaction, so an exception can name a real ``position_id`` and a
    rolled-back position cannot leave an orphan exception behind.
    """
    check = await check_account_link(
        conn, org_id, account_id=account_id, owner_entity_id=owner_entity_id
    )
    if not check.ok:
        await record_account_link_exception(
            conn, org_id, position_id=position_id, check=check,
            source_system=source_system,
        )
    return check


# ── The review list ─────────────────────────────────────────────────────────


async def list_account_link_exceptions(
    conn,
    org_id: str,
    *,
    include_reviewed: bool = False,
    position_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Open (or all) linkage exceptions for one org. Read-only.

    ``total`` is the count BEFORE the limit, so a truncated page never looks
    complete.
    """
    org_id = _require_org(org_id)
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    where = ["x.org_id = $1::uuid"]
    args: list[Any] = [org_id]
    if not include_reviewed:
        where.append("x.reviewed_at IS NULL")
    if position_id:
        args.append(str(position_id))
        where.append(f"x.position_id = ${len(args)}::uuid")
    clause = " AND ".join(where)

    async with _OrgWrite(conn, org_id) as c:
        total = await c.fetchval(
            f"SELECT count(*) FROM {TABLE_POSITION_ACCOUNT_EXCEPTIONS} x "
            f"WHERE {clause}",
            *args,
        )
        rows = await c.fetch(
            f"""
            SELECT x.id::text AS id, x.position_id::text AS position_id,
                   x.account_id::text AS account_id,
                   x.owner_entity_id::text AS owner_entity_id,
                   x.reason_code, x.reason, x.source_system, x.detail,
                   x.created_at, x.reviewed_at,
                   x.reviewed_by::text AS reviewed_by,
                   e.display_name AS owner_name,
                   a.account_number_masked AS account_number_masked
            FROM {TABLE_POSITION_ACCOUNT_EXCEPTIONS} x
            LEFT JOIN public.entities e ON e.id = x.owner_entity_id
            LEFT JOIN {TABLE_ACCOUNTS} a ON a.id = x.account_id
            WHERE {clause}
            ORDER BY x.created_at DESC, x.id
            LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
            """,
            *args, limit, offset,
        )

    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "exceptions": [
            {
                "id": r["id"],
                "position_id": r["position_id"],
                "account_id": r["account_id"],
                "account_number_masked": r["account_number_masked"],
                "owner_entity_id": r["owner_entity_id"],
                "owner_name": r["owner_name"],
                "reason_code": r["reason_code"],
                "reason": r["reason"],
                "source_system": r["source_system"],
                "detail": (
                    json.loads(r["detail"])
                    if isinstance(r["detail"], str) else r["detail"]
                ),
                "created_at": r["created_at"].isoformat(),
                "reviewed_at": (
                    r["reviewed_at"].isoformat() if r["reviewed_at"] else None
                ),
                "reviewed_by": r["reviewed_by"],
                "is_open": r["reviewed_at"] is None,
            }
            for r in rows
        ],
    }


async def review_account_link_exception(
    conn, org_id: str, *, exception_id: str, reviewed_by: str
) -> bool:
    """Close one exception. ``False`` if it was not open (or not this org's).

    Closing does NOT correct anything — the position keeps whatever
    ``account_id`` it was written with. It records that a human looked. The fix,
    when there is one, is an edit to the position or to the account's owners,
    and either of those raising the mismatch again is a new finding the partial
    unique index deliberately allows.
    """
    org_id = _require_org(org_id)
    if not reviewed_by:
        raise AccountLinkError(
            "reviewed_by is required — an exception closed by nobody is not a "
            "review, it is a deletion with extra steps"
        )
    async with _OrgWrite(conn, org_id) as c:
        updated = await c.fetchval(
            f"""
            WITH upd AS (
                UPDATE {TABLE_POSITION_ACCOUNT_EXCEPTIONS} x
                SET reviewed_at = now(), reviewed_by = $3::uuid
                WHERE x.id = $1::uuid AND x.org_id = $2::uuid
                  AND x.reviewed_at IS NULL
                RETURNING 1
            ) SELECT count(*) FROM upd
            """,
            str(exception_id), org_id, str(reviewed_by),
        )
    return bool(updated)


__all__ = [
    "AccountLinkCheck",
    "AccountLinkError",
    "REASON_ACCOUNT_HAS_NO_OWNERS",
    "REASON_CODES",
    "REASON_OWNER_NOT_ACCOUNT_OWNER",
    "TABLE_POSITION_ACCOUNT_EXCEPTIONS",
    "check_account_link",
    "list_account_link_exceptions",
    "record_account_link_exception",
    "review_account_link_exception",
    "validate_position_account",
]
