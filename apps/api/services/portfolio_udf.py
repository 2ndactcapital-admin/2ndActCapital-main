"""User-defined fields — Portfolio Phase G.

PARALLEL NAMESPACES, NOT A CASCADE
──────────────────────────────────────────────────────────────────────────────
Four parties author custom fields and they do not compete:

* **platform** — Hollisworks ships an industry-standard classification feed.
* **org** — a client decides that, for them, preferred stock is debt.
* **team** — a coverage team keeps its own working view.
* **user** — one person keeps their own note.

The obvious design is a cascade: user overrides team overrides org overrides
platform, one winner per ``field_key``. That is NOT what this is, and the
difference is not cosmetic. Under a cascade, the platform's
``asset_classification`` and a client's ``asset_classification`` are the same
field with two candidate values, and something must pick one — which silently
destroys the ability to say "the standard feed says equity, *and* this client
books it as debt". Both facts are true, both are wanted, and a report that can
only see the winner cannot reconcile them.

So there is **no merge and no override**. ``resolve_visible_definitions``
returns every definition a user can see, from all four scopes, side by side.
Two definitions sharing a ``field_key`` across scopes is a normal, expected,
non-error state — proven directly in ``verify_portfoliog.py``. Values attach to
a ``definition_id``, never to a ``field_key``, so there is never a question of
which definition a stored value belongs to.

WHERE ENFORCEMENT LIVES, AND WHY IT IS SPLIT IN TWO
──────────────────────────────────────────────────────────────────────────────
RLS enforces the HARD boundary and only that — cross-org isolation, plus a
global read of platform-scope rows, plus Super-Admin for platform-scope writes.
The four deployed policies on ``udf_definitions`` and the one on ``udf_values``
were read out of ``pg_policies``, not assumed.

Team and user narrowing is NOT in RLS and deliberately is not. It lives here, in
:func:`resolve_visible_definitions`. This is the SAME division A2 already made
for the ownership-basis contract: ``portfolio.positions`` has no CHECK tying
``ownership_basis`` to the populated measure, and
``portfolio_assets._validate_basis`` is the only thing enforcing it. The reason
is the same in both cases — the database can cheaply prove a tenant boundary,
because ``org_id`` is on the row; it cannot cheaply prove "this user is on that
team" without a correlated subquery on every row of every read, and a policy
that is expensive gets disabled, at which point the boundary it was protecting
is gone. What RLS guarantees here is that a definition from ANOTHER TENANT can
never be returned no matter what this module does. What this module guarantees
is that a team-scope definition is not returned to a non-member.

That split has a consequence worth stating plainly: a caller who bypasses
:func:`resolve_visible_definitions` and does a raw ``SELECT * FROM
portfolio.udf_definitions`` **will** see their org's team-scope rows for teams
they are not on. That is not a hole in RLS; it is the boundary being drawn
where it was designed to be drawn. There is exactly one resolver, and it is
this one.

THE MEMBERSHIP CHECK IS THE REAL ONE
──────────────────────────────────────────────────────────────────────────────
"Is user X on team Y" is answered by ``public.team_members`` — PK
``(team_id, user_id)``, introspected, not assumed. It is NOT
``public.staff_assignments``: that table maps a team-or-user to an ENTITY
(``staff_assignments_exactly_one_target``) and answers "who covers this client",
which is a different question that happens to mention teams.

``team_members`` carries **no ``org_id``** — its own RLS policy reaches the org
through an EXISTS on ``teams``. So every membership predicate in this module
JOINs ``public.teams`` and constrains ``t.org_id``, exactly as
``services.staff_visibility.get_team_ids_for_users`` already does. Dropping that
join would make a membership row from another tenant satisfy the check.

WHAT THE DATABASE GATES, AND WHAT IS ONLY REPORTED
──────────────────────────────────────────────────────────────────────────────
**Duplicates are refused by the database, not by a Python ``if``.** There is no
pre-flight ``SELECT`` looking for an existing ``field_key``. The INSERT is
issued and ``idx_udf_def_key_unique`` — a PARTIAL unique index, live — raises
``UniqueViolationError``, which is caught and re-raised as
:class:`UdfDuplicateError` carrying the constraint name. A pre-flight check
would be a race (two concurrent creates both see nothing and both insert) and,
worse, would pass a verification suite even if the index had been dropped.

**Cross-scope ownership IS checked here, before the insert.** A ``team_id``
belonging to another org, or a ``user_id`` belonging to another org, is refused
at creation. There is no FK on ``owner_scope_id`` — it is a polymorphic column
holding either a team id or a user id, so the database cannot check it at all,
and a wrong value would sit there looking valid until a resolver silently failed
to match it months later.

SCHEMA QUALIFICATION
──────────────────────────────────────────────────────────────────────────────
``portfolio`` is NOT on ``app_service``'s ``search_path``. Every table name is a
``TABLE_*`` constant and there is no bare table reference in executable code —
the verification AST/regex-scans this file for exactly that.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

from services.portfolio_assets import (
    PortfolioError,
    _OrgWrite,
    _require_org,
)
from services.securities_global import (
    _require_super_admin,
    _SuperAdminWrite,
)

TABLE_UDF_DEFINITIONS = "portfolio.udf_definitions"
TABLE_UDF_VALUES = "portfolio.udf_values"
TABLE_TEAMS = "public.teams"
TABLE_TEAM_MEMBERS = "public.team_members"
TABLE_USERS = "public.users"

#: ``udf_def_scope_chk``, mirrored verbatim from the deployed CHECK.
OWNER_SCOPES = frozenset({"platform", "org", "team", "user"})
SCOPE_PLATFORM = "platform"
SCOPE_ORG = "org"
SCOPE_TEAM = "team"
SCOPE_USER = "user"

#: ``udf_def_applies_chk`` AND ``udf_values_target_chk`` — the SAME six values in
#: both deployed CHECKs, which is what makes the applies_to/target_type
#: agreement check in :func:`record_udf_value` a comparison of like with like.
APPLIES_TO = frozenset({
    "asset", "position", "valuation", "transaction", "commitment", "entity",
})
TARGET_TYPES = APPLIES_TO

#: ``udf_def_type_chk``, mirrored verbatim from the deployed CHECK.
DATA_TYPES = frozenset({"text", "numeric", "date", "boolean", "select"})

#: The partial unique index that is the REAL duplicate gate. Named so a caller
#: catching :class:`UdfDuplicateError` — and the verification — can assert that
#: the refusal came from THIS index and not from some other constraint that
#: happened to fire.
UDF_DEF_UNIQUE_INDEX = "idx_udf_def_key_unique"

#: The partial unique index behind the value upsert. ``udf_values`` has NO
#: unique CONSTRAINT — introspected — so ``ON CONFLICT ON CONSTRAINT`` cannot be
#: used and the conflict target must be inferred by repeating the index's column
#: list AND its predicate. See :data:`_VALUE_CONFLICT_TARGET`.
UDF_VALUE_UNIQUE_INDEX = "idx_udf_values_unique"

#: Inference for :data:`UDF_VALUE_UNIQUE_INDEX`. The ``WHERE`` clause is not
#: decoration: without it Postgres looks for a TOTAL unique index on those four
#: columns, finds none, and raises 42P10 ``there is no unique or exclusion
#: constraint matching the ON CONFLICT specification``.
_VALUE_CONFLICT_TARGET = (
    "(org_id, definition_id, target_type, target_id) "
    "WHERE system_to IS NULL AND valid_to IS NULL"
)

#: The four value columns. Exactly one is ever populated; the other three are
#: written as NULL rather than left alone, so an UPSERT that changes a
#: definition's shape cannot leave a stale measure behind next to the new one.
_VALUE_COLUMNS = ("value_text", "value_numeric", "value_date", "value_json")


def _current(alias: str) -> str:
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


# ── Errors ──────────────────────────────────────────────────────────────────


class UdfError(PortfolioError):
    """A user-defined-field write was refused for a reason the caller can fix."""


class UdfPermissionError(UdfError):
    """The caller lacks the privilege the requested scope requires."""


class UdfScopeError(UdfError):
    """The scope's owning object does not belong to the calling org.

    Raised for a ``team_id`` or ``user_id`` from another tenant. There is no FK
    on ``owner_scope_id`` — it is polymorphic — so this is the only check.
    """


class UdfDuplicateError(UdfError):
    """An ACTIVE definition already exists in this exact namespace.

    Always raised from an ``asyncpg.UniqueViolationError`` on
    :data:`UDF_DEF_UNIQUE_INDEX`, never from an application-level pre-check.
    """

    def __init__(self, message: str, *, constraint: str | None = None):
        super().__init__(message)
        self.constraint = constraint


class UdfValueTypeError(UdfError):
    """The supplied value does not match the definition's ``data_type``."""


class UdfTargetMismatchError(UdfError):
    """``target_type`` disagrees with the definition's ``applies_to``."""


# ── Internal validation ─────────────────────────────────────────────────────


def _check_choice(value: Any, allowed: frozenset[str], field_name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise UdfError(
            f"{field_name}={value!r} is not one of {sorted(allowed)}. This "
            f"vocabulary is mirrored from the deployed CHECK constraint — a "
            f"value outside it would be refused by the database anyway, with a "
            f"23514 that names the constraint and not the argument."
        )
    return value


def _check_field_key(field_key: Any) -> str:
    """``field_key`` is the namespace key. Non-empty, trimmed, and nothing more.

    There is no deployed CHECK on its shape, so none is invented here. A regex
    demanding ``^[a-z_]+$`` in Python would reject keys the database accepts,
    and the next person to hit it would have no way to tell which layer was
    wrong — the same reasoning A2 applied to ``assets.asset_type``.
    """
    if not isinstance(field_key, str) or not field_key.strip():
        raise UdfError("field_key is required and must be a non-empty string")
    return field_key.strip()


def _check_label(label: Any) -> str:
    if not isinstance(label, str) or not label.strip():
        raise UdfError("label is required and must be a non-empty string")
    return label.strip()


def _normalize_options(data_type: str, options: Any) -> list[str] | None:
    """Coerce ``options`` to a list of choice strings, or ``None``.

    A ``select`` definition MUST carry a non-empty option list. That is not a
    style rule: :func:`record_udf_value` validates a select value against this
    list, so a select with no options is a field that can never accept any
    value at all. Refusing it at creation is the only point at which that is
    still obvious.

    Accepted shapes — a bare list, or ``{"choices": [...]}`` / ``{"options":
    [...]}``, because the Part 1 SQL left ``options`` as an unconstrained
    ``jsonb`` and both shapes are things a caller plausibly sends.
    """
    if data_type != "select":
        if options is None:
            return None
        # Not an error: a text field may legitimately carry suggestions. It is
        # simply not validated against.
        return _coerce_choice_list(options, required=False)

    choices = _coerce_choice_list(options, required=True)
    if not choices:
        raise UdfError(
            "data_type='select' requires a non-empty options list. A select "
            "field with no choices can never accept a value — record_udf_value "
            "validates every select value against this list."
        )
    return choices


def _coerce_choice_list(options: Any, *, required: bool) -> list[str] | None:
    if options is None:
        if required:
            raise UdfError("data_type='select' requires options")
        return None
    if isinstance(options, dict):
        for key in ("choices", "options", "values"):
            if key in options:
                options = options[key]
                break
        else:
            raise UdfError(
                f"options was a dict without a 'choices'/'options'/'values' "
                f"key: {sorted(options)}"
            )
    if not isinstance(options, (list, tuple)):
        raise UdfError(
            f"options must be a list of choices (or a dict wrapping one) — got "
            f"{type(options).__name__}"
        )
    choices = []
    for item in options:
        if not isinstance(item, str) or not item.strip():
            raise UdfError(
                f"every option must be a non-empty string — got {item!r}"
            )
        choices.append(item.strip())
    if len(set(choices)) != len(choices):
        raise UdfError(f"options contains duplicates: {choices}")
    return choices


def _numeric(value: Any, field_name: str) -> Decimal:
    """Coerce to Decimal, refusing float.

    Identical to ``portfolio_assets._money`` and ``securities_global._money``,
    and deliberately so — a UDF numeric is no less load-bearing than a position
    measure just because a tenant defined it rather than the schema. A float is
    refused rather than converted because ``Decimal(0.1)`` is
    ``0.1000000000000000055511151231257827021181583404541015625``, no error is
    raised anywhere downstream, and the wrong number is simply stored. If the
    caller has a float, the fix is at the source.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise UdfValueTypeError(
            f"{field_name} must be a Decimal, int or str — got "
            f"{type(value).__name__}. Binary floats cannot represent decimal "
            f"quantities exactly and Decimal(float) silently preserves the "
            f"error."
        )
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            raise UdfValueTypeError(
                f"{field_name}={value!r} is not a decimal number"
            ) from None
    raise UdfValueTypeError(
        f"{field_name} must be a Decimal, int or str — got "
        f"{type(value).__name__}"
    )


def _date(value: Any, field_name: str) -> date:
    """A real ``date``. A ``datetime`` is REFUSED, not truncated.

    ``datetime`` is a subclass of ``date``, so ``isinstance(value, date)`` is
    True for one and the column is ``date``, which means the time component
    would be dropped silently on the way in. A caller who has a timestamp and
    means a date should say which date they mean.
    """
    if isinstance(value, datetime):
        raise UdfValueTypeError(
            f"{field_name} is a datetime, not a date. Storing it in a `date` "
            f"column would drop the time component silently — pass "
            f"`value.date()` if that is what you mean, having decided which "
            f"timezone it should be read in."
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            raise UdfValueTypeError(
                f"{field_name}={value!r} is not an ISO-8601 date (YYYY-MM-DD)"
            ) from None
    raise UdfValueTypeError(
        f"{field_name} must be a date or an ISO-8601 date string — got "
        f"{type(value).__name__}"
    )


def _boolean(value: Any, field_name: str) -> bool:
    """A real ``bool``. ``1``, ``"true"`` and ``"yes"`` are refused.

    Python's truthiness would happily accept ``"false"`` as True, which is the
    exact failure this refuses. ``value_json`` is where a boolean lands (there
    is no ``value_boolean`` column), and jsonb ``true``/``false`` round-trips
    back as a real ``bool``.
    """
    if isinstance(value, bool):
        return value
    raise UdfValueTypeError(
        f"{field_name} must be a real bool — got {type(value).__name__} "
        f"({value!r}). Truthy coercion would read the string 'false' as True."
    )


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise UdfValueTypeError(
            f"{field_name} must be a string — got {type(value).__name__}"
        )
    if not value.strip():
        raise UdfValueTypeError(
            f"{field_name} is empty. A UDF with no value is an ABSENT value — "
            f"delete the row rather than storing a blank one, so a reader can "
            f"tell 'not recorded' from 'recorded as nothing'."
        )
    return value


def _jsonb(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


# ── Task 2: DEFINE ──────────────────────────────────────────────────────────


_DEF_INSERT = f"""
INSERT INTO {TABLE_UDF_DEFINITIONS}
    (org_id, owner_scope, owner_scope_id, applies_to, field_key, label,
     data_type, options, display_order, is_active)
VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7, $8::jsonb, $9, true)
RETURNING id::text
"""


def _validate_definition(applies_to, field_key, label, data_type, options):
    """The five arguments every scope shares, validated identically.

    One function rather than four copies, because the ONLY thing that legally
    differs between the scopes is who may write and what ``owner_scope_id``
    means — not what a valid field looks like. Four copies would drift.
    """
    data_type = _check_choice(data_type, DATA_TYPES, "data_type")
    return (
        _check_choice(applies_to, APPLIES_TO, "applies_to"),
        _check_field_key(field_key),
        _check_label(label),
        data_type,
        _normalize_options(data_type, options),
    )


async def _insert_definition(conn, params: tuple) -> str:
    """Issue the INSERT and translate a unique violation.

    The ``try`` wraps ONLY the statement, and the translation is keyed on the
    constraint name, so an unrelated unique violation — should one ever be added
    to this table — surfaces as itself rather than being mislabelled a duplicate
    ``field_key``.
    """
    try:
        row = await conn.fetchrow(_DEF_INSERT, *params)
    except asyncpg.UniqueViolationError as exc:
        constraint = getattr(exc, "constraint_name", None)
        raise UdfDuplicateError(
            f"an ACTIVE definition already exists in this namespace "
            f"(scope={params[1]!r}, org={params[0]}, scope_id={params[2]}, "
            f"applies_to={params[3]!r}, field_key={params[4]!r}). Refused by "
            f"{constraint or UDF_DEF_UNIQUE_INDEX} in the database — this is "
            f"not an application-level check and there is no pre-flight SELECT "
            f"to race against.",
            constraint=constraint,
        ) from exc
    return row["id"]


async def create_platform_definition(
    conn,
    *,
    applies_to: str,
    field_key: str,
    label: str,
    data_type: str,
    options: Any = None,
    display_order: int = 0,
    is_super_admin: bool = False,
) -> str:
    """A field authored by the platform, visible to EVERY tenant.

    Composes A1's real global-write pattern verbatim:
    ``securities_global._require_super_admin`` refuses BEFORE any statement is
    issued (so a refusal writes nothing — asserted directly in the
    verification), and ``_SuperAdminWrite`` raises ``app.is_super_admin`` for
    exactly one transaction via ``SET LOCAL``, which the deployed
    ``udf_definitions_scoped_write`` policy's ``WITH CHECK`` is what actually
    admits the row.

    ``org_id`` and ``owner_scope_id`` are both NULL and are not parameters. The
    deployed ``udf_def_scope_org_chk`` requires both — a platform definition
    that belonged to an org would be visible everywhere while claiming a tenant,
    which is not a state anything downstream could interpret.
    """
    _require_super_admin(is_super_admin, "create_platform_definition")
    applies_to, field_key, label, data_type, choices = _validate_definition(
        applies_to, field_key, label, data_type, options
    )
    async with _SuperAdminWrite(conn) as c:
        return await _insert_definition(c, (
            None, SCOPE_PLATFORM, None, applies_to, field_key, label,
            data_type, _jsonb(choices), int(display_order),
        ))


async def create_org_definition(
    conn,
    *,
    org_id: str,
    applies_to: str,
    field_key: str,
    label: str,
    data_type: str,
    options: Any = None,
    display_order: int = 0,
) -> str:
    """A field authored by one tenant, visible to everyone in that tenant.

    A standard org-write through A2's :class:`~services.portfolio_assets.
    _OrgWrite`: the ``org_id`` argument is compared against
    ``app.current_org_id`` by the policy's ``WITH CHECK``, so a mismatch is
    refused by the DATABASE and not by a Python ``if``.
    """
    org_id = _require_org(org_id)
    applies_to, field_key, label, data_type, choices = _validate_definition(
        applies_to, field_key, label, data_type, options
    )
    async with _OrgWrite(conn, org_id) as c:
        return await _insert_definition(c, (
            org_id, SCOPE_ORG, None, applies_to, field_key, label,
            data_type, _jsonb(choices), int(display_order),
        ))


async def create_team_definition(
    conn,
    *,
    org_id: str,
    team_id: str,
    applies_to: str,
    field_key: str,
    label: str,
    data_type: str,
    options: Any = None,
    display_order: int = 0,
) -> str:
    """A field authored by one team, visible only to that team's members.

    ``team_id`` is verified to belong to ``org_id`` FIRST, inside the same
    transaction as the insert. ``owner_scope_id`` is polymorphic — it holds a
    team id here and a user id in :func:`create_user_definition` — so there is
    no FK and the database cannot check it at all. A cross-org team id would
    therefore be accepted, sit in the row looking entirely valid, and fail only
    as a silent absence from every resolution months later. It is refused at
    creation instead.

    The check runs inside :class:`~services.portfolio_assets._OrgWrite`, so
    under ``app_service`` the ``teams`` RLS policy narrows it as well; the
    explicit ``AND org_id = $2`` is what makes it hold under a ``bypassrls``
    role too, which is the one that would otherwise let a bad row through.
    """
    org_id = _require_org(org_id)
    if not team_id:
        raise UdfError("team_id is required for a team-scope definition")
    applies_to, field_key, label, data_type, choices = _validate_definition(
        applies_to, field_key, label, data_type, options
    )
    async with _OrgWrite(conn, org_id) as c:
        owns = await c.fetchval(
            f"SELECT 1 FROM {TABLE_TEAMS} WHERE id = $1::uuid "
            f"AND org_id = $2::uuid",
            str(team_id), org_id,
        )
        if not owns:
            raise UdfScopeError(
                f"team {team_id} does not belong to org {org_id}. "
                f"udf_definitions.owner_scope_id is polymorphic (team id or "
                f"user id) and carries no FK, so nothing downstream would ever "
                f"have caught this."
            )
        return await _insert_definition(c, (
            org_id, SCOPE_TEAM, str(team_id), applies_to, field_key, label,
            data_type, _jsonb(choices), int(display_order),
        ))


async def create_user_definition(
    conn,
    *,
    org_id: str,
    user_id: str,
    applies_to: str,
    field_key: str,
    label: str,
    data_type: str,
    options: Any = None,
    display_order: int = 0,
) -> str:
    """A field authored by one person, visible only to them.

    ``user_id`` is verified to belong to ``org_id`` for the same reason
    :func:`create_team_definition` verifies its team — see there.
    """
    org_id = _require_org(org_id)
    if not user_id:
        raise UdfError("user_id is required for a user-scope definition")
    applies_to, field_key, label, data_type, choices = _validate_definition(
        applies_to, field_key, label, data_type, options
    )
    async with _OrgWrite(conn, org_id) as c:
        owns = await c.fetchval(
            f"SELECT 1 FROM {TABLE_USERS} WHERE id = $1::uuid "
            f"AND org_id = $2::uuid",
            str(user_id), org_id,
        )
        if not owns:
            raise UdfScopeError(
                f"user {user_id} does not belong to org {org_id}. "
                f"udf_definitions.owner_scope_id is polymorphic and carries no "
                f"FK, so nothing downstream would ever have caught this."
            )
        return await _insert_definition(c, (
            org_id, SCOPE_USER, str(user_id), applies_to, field_key, label,
            data_type, _jsonb(choices), int(display_order),
        ))


# ── Task 3: RESOLVE ─────────────────────────────────────────────────────────


_DEF_SELECT = """
       d.id::text              AS id,
       d.org_id::text          AS org_id,
       d.owner_scope           AS owner_scope,
       d.owner_scope_id::text  AS owner_scope_id,
       d.applies_to            AS applies_to,
       d.field_key             AS field_key,
       d.label                 AS label,
       d.data_type             AS data_type,
       d.options               AS options,
       d.display_order         AS display_order,
       d.is_active             AS is_active
"""

#: The whole of Task 3, in one predicate.
#:
#: Four disjuncts, one per scope, and they are PARALLEL — no ordering between
#: them, no winner, no suppression of one by another. A user in an org that has
#: its own ``asset_classification`` sees BOTH it and the platform's.
#:
#: The team disjunct is the enforcement this sprint adds beyond RLS. It JOINs
#: ``public.teams`` because ``team_members`` has no ``org_id`` of its own — a
#: membership row is only meaningful relative to the team's tenant, and matching
#: on ``tm.user_id`` alone would let a membership from another tenant satisfy
#: it. Precedent: ``services.staff_visibility.get_team_ids_for_users``.
#:
#: ``d.org_id = $1`` is repeated on the org, team and user disjuncts even though
#: RLS already restricts the read. That is not redundancy for its own sake: this
#: same function runs under a Super-Admin connection (``app.is_super_admin`` is
#: the second disjunct of ``udf_definitions_scoped_read``), where RLS restricts
#: NOTHING, and without it a Super-Admin resolving for one org would silently be
#: handed every tenant's private fields.
_VISIBLE_PREDICATE = f"""
    d.applies_to = $3
AND d.is_active  = true
AND {_current('d')}
AND (
       d.owner_scope = 'platform'
    OR (d.owner_scope = 'org'  AND d.org_id = $1::uuid)
    OR (d.owner_scope = 'team' AND d.org_id = $1::uuid
        AND EXISTS (
            SELECT 1
            FROM {TABLE_TEAM_MEMBERS} tm
            JOIN {TABLE_TEAMS} t ON t.id = tm.team_id
            WHERE tm.team_id = d.owner_scope_id
              AND tm.user_id = $2::uuid
              AND t.org_id   = $1::uuid
        ))
    OR (d.owner_scope = 'user' AND d.org_id = $1::uuid
        AND d.owner_scope_id = $2::uuid)
)
"""

#: Deterministic, and carrying NO precedence. ``owner_scope`` leads the sort
#: only so two runs return the same list; a caller reading meaning into the
#: order is reading meaning that is not there, which is why the scope is on
#: every returned row explicitly.
_VISIBLE_ORDER = (
    "ORDER BY d.display_order, d.field_key, "
    "array_position(ARRAY['platform','org','team','user'], d.owner_scope), d.id"
)

_VISIBLE_SQL = f"""
SELECT {_DEF_SELECT}
FROM {TABLE_UDF_DEFINITIONS} d
WHERE {_VISIBLE_PREDICATE}
{_VISIBLE_ORDER}
"""


async def resolve_visible_definitions(
    conn, *, org_id: str, user_id: str, applies_to: str
) -> list[dict]:
    """Every definition this specific user can see, for this ``applies_to``.

    The union of four parallel namespaces:

    * every ACTIVE platform-scope definition (all tenants see these);
    * this org's own org-scope definitions;
    * team-scope definitions for teams this user is ACTUALLY a member of, per
      ``public.team_members`` — the real mechanism, joined through
      ``public.teams`` for the org constraint;
    * this user's own user-scope definitions.

    A team-scope definition for a team the user is not on does NOT appear, and
    a second user in the same org who is not on that team gets a list without
    it. Both directions are asserted in the verification rather than one being
    inferred from the other — a resolver that returned nothing at all would
    satisfy the negative half on its own.

    NOT a cascade. Two rows sharing a ``field_key`` across scopes are both
    returned, both carrying their ``owner_scope`` and their own ``id``; nothing
    here picks a winner, and there is no merge step anywhere in this module.
    Values bind to ``id``, never to ``field_key``.
    """
    org_id = _require_org(org_id)
    if not user_id:
        raise UdfError(
            "user_id is required — resolution is per-user by construction. "
            "There is no 'all definitions for the org' call here on purpose: "
            "team and user scope only mean something relative to a person."
        )
    applies_to = _check_choice(applies_to, APPLIES_TO, "applies_to")
    rows = await conn.fetch(_VISIBLE_SQL, org_id, str(user_id), applies_to)
    return [_definition_row(r) for r in rows]


def _definition_row(row) -> dict:
    out = dict(row)
    if isinstance(out.get("options"), str):
        out["options"] = json.loads(out["options"])
    return out


async def get_definition(conn, *, definition_id: str) -> dict | None:
    """One definition by id, or ``None``.

    No ``org_id`` argument, deliberately: RLS is the boundary here, and adding a
    Python ``AND org_id = ...`` on top would make the function pass under a
    ``bypassrls`` role for a reason that has nothing to do with the policy being
    correct. The verification reads a cross-org definition through the real
    ``app_service`` connection and asserts ``None``.
    """
    row = await conn.fetchrow(
        f"SELECT {_DEF_SELECT} FROM {TABLE_UDF_DEFINITIONS} d "
        f"WHERE d.id = $1::uuid AND {_current('d')}",
        str(definition_id),
    )
    return _definition_row(row) if row else None


async def is_team_member(conn, *, org_id: str, team_id: str, user_id: str) -> bool:
    """Is user X on team Y, within this org — the REAL check, exposed.

    Public because the verification asserts the mechanism directly rather than
    only through its effect on resolution: "the definition did not appear" is
    also what a broken query returns.
    """
    org_id = _require_org(org_id)
    found = await conn.fetchval(
        f"""
        SELECT 1
        FROM {TABLE_TEAM_MEMBERS} tm
        JOIN {TABLE_TEAMS} t ON t.id = tm.team_id
        WHERE tm.team_id = $1::uuid
          AND tm.user_id = $2::uuid
          AND t.org_id   = $3::uuid
        """,
        str(team_id), str(user_id), org_id,
    )
    return bool(found)


# ── Task 4: VALUES ──────────────────────────────────────────────────────────


def coerce_value(data_type: str, value: Any, options: Any = None) -> dict:
    """Validate ``value`` against ``data_type`` and place it in ONE column.

    Returns a dict of all four value columns, exactly one of which is non-NULL.
    Returning all four — rather than just the one that matters — is what lets
    the UPSERT overwrite the other three with NULL, so a definition whose
    ``data_type`` was corrected does not leave a value stranded in the old
    column alongside the new one, where two readers would disagree about which
    is the value.

    ``None`` is refused. An absent UDF is an absent ROW; a row carrying four
    NULLs is indistinguishable from a bug, and a reader has no way to tell
    "recorded as nothing" from "never recorded".
    """
    if value is None:
        raise UdfValueTypeError(
            "value is None. A UDF with no value is an ABSENT value — delete "
            "the row instead, so a reader can tell 'not recorded' from "
            "'recorded as nothing'."
        )
    cols: dict[str, Any] = {c: None for c in _VALUE_COLUMNS}
    if data_type == "numeric":
        cols["value_numeric"] = _numeric(value, "value")
    elif data_type == "date":
        cols["value_date"] = _date(value, "value")
    elif data_type == "boolean":
        cols["value_json"] = _jsonb(_boolean(value, "value"))
    elif data_type == "text":
        cols["value_text"] = _text(value, "value")
    elif data_type == "select":
        text = _text(value, "value")
        choices = _coerce_choice_list(options, required=True)
        if text not in choices:
            raise UdfValueTypeError(
                f"value={text!r} is not one of this select field's options "
                f"{choices}. A select whose stored values drift outside its "
                f"own option list cannot be grouped or filtered on."
            )
        cols["value_text"] = text
    else:  # pragma: no cover — _check_choice already refused it
        raise UdfValueTypeError(f"unsupported data_type={data_type!r}")
    return cols


_VALUE_UPSERT = f"""
INSERT INTO {TABLE_UDF_VALUES}
    (org_id, definition_id, target_type, target_id,
     value_text, value_numeric, value_date, value_json)
VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7, $8::jsonb)
ON CONFLICT {_VALUE_CONFLICT_TARGET}
DO UPDATE SET value_text    = EXCLUDED.value_text,
              value_numeric = EXCLUDED.value_numeric,
              value_date    = EXCLUDED.value_date,
              value_json    = EXCLUDED.value_json
RETURNING id::text
"""


async def record_udf_value(
    conn,
    *,
    org_id: str,
    definition_id: str,
    target_type: str,
    target_id: str,
    value: Any,
) -> str:
    """Record one value against one definition for one target. Idempotent.

    Three refusals, in order, and each is load-bearing:

    1. **The definition must be readable from this org.** Read inside the
       ``_OrgWrite`` transaction, so RLS narrows it. A definition from another
       tenant is simply not there.

    2. **``target_type`` must equal the definition's ``applies_to``.** A numeric
       field defined for ``'commitment'`` does not silently accept a value keyed
       to an ``'asset'``. ``udf_values.target_id`` is polymorphic and carries no
       FK — it is a commitment id, an asset id or an entity id depending on
       ``target_type`` — so nothing in the database can catch this. A mismatched
       row does not error; it just never joins to anything, and the value looks
       like it was never recorded.

    3. **The value must match the definition's ``data_type``**, with ``float``
       refused for a numeric per the convention A2 established. See
       :func:`coerce_value`.

    UPSERT, not append. ``idx_udf_values_unique`` — a PARTIAL unique index, so
    the conflict target must repeat its predicate; see
    :data:`_VALUE_CONFLICT_TARGET` — makes the second recording for a target an
    UPDATE. That is a deliberate divergence from CLAUDE.md Rule 3, and the
    reason is the design's own words: "one current value per definition per
    target". A UDF value is a tenant's own annotation with no accounting
    consequence and no downstream restatement — unlike a position quantity,
    which a corporate action restates and which a report must be able to read
    "as of" a past date. Closing and re-inserting here would grow an unbounded
    history of edits to a free-text note that nothing reads. The bi-temporal
    columns remain on the table and the partial index is predicated on them, so
    if Phase H ever needs value history the switch is a change to this one
    statement and nothing else.
    """
    org_id = _require_org(org_id)
    target_type = _check_choice(target_type, TARGET_TYPES, "target_type")
    if not target_id:
        raise UdfError("target_id is required")

    async with _OrgWrite(conn, org_id) as c:
        definition = await c.fetchrow(
            f"SELECT d.id::text AS id, d.applies_to, d.data_type, d.options, "
            f"       d.owner_scope, d.org_id::text AS org_id "
            f"FROM {TABLE_UDF_DEFINITIONS} d "
            f"WHERE d.id = $1::uuid AND {_current('d')}",
            str(definition_id),
        )
        if definition is None:
            raise UdfError(
                f"definition {definition_id} is not a current definition "
                f"readable from org {org_id}. There IS an FK on "
                f"definition_id, but a 23503 names a constraint and cannot see "
                f"the temporal predicate or the tenant boundary at all."
            )
        if definition["applies_to"] != target_type:
            raise UdfTargetMismatchError(
                f"definition {definition_id} applies to "
                f"{definition['applies_to']!r}, but this value is keyed to a "
                f"{target_type!r} target. udf_values.target_id is polymorphic "
                f"and has no FK — a mismatched row would not error, it would "
                f"just never join to anything."
            )
        options = definition["options"]
        if isinstance(options, str):
            options = json.loads(options)
        cols = coerce_value(definition["data_type"], value, options)
        row = await c.fetchrow(
            _VALUE_UPSERT,
            org_id, str(definition_id), target_type, str(target_id),
            cols["value_text"], cols["value_numeric"], cols["value_date"],
            cols["value_json"],
        )
    return row["id"]


_VALUE_SELECT = """
       v.id::text            AS id,
       v.org_id::text        AS org_id,
       v.definition_id::text AS definition_id,
       v.target_type         AS target_type,
       v.target_id::text     AS target_id,
       v.value_text          AS value_text,
       v.value_numeric       AS value_numeric,
       v.value_date          AS value_date,
       v.value_json          AS value_json
"""


async def get_udf_value(
    conn, *, org_id: str, definition_id: str, target_type: str, target_id: str
) -> dict | None:
    """The current value for one (definition, target), or ``None``.

    Keyed on ``definition_id`` — never on ``field_key``. That is the whole
    parallel-namespace claim in one signature: with two definitions both named
    ``asset_classification`` on the same target, a ``field_key`` lookup would
    have to guess, and this one cannot.
    """
    org_id = _require_org(org_id)
    target_type = _check_choice(target_type, TARGET_TYPES, "target_type")
    row = await conn.fetchrow(
        f"SELECT {_VALUE_SELECT} FROM {TABLE_UDF_VALUES} v "
        f"WHERE v.org_id = $1::uuid AND v.definition_id = $2::uuid "
        f"  AND v.target_type = $3 AND v.target_id = $4::uuid "
        f"  AND {_current('v')}",
        org_id, str(definition_id), target_type, str(target_id),
    )
    return _value_row(row) if row else None


async def list_udf_values_for_target(
    conn, *, org_id: str, user_id: str, target_type: str, target_id: str
) -> list[dict]:
    """Every value on one target that THIS user is allowed to see.

    Joined to :func:`resolve_visible_definitions`' own predicate rather than
    filtering after the fact, so a team-scope value cannot leak to a
    non-member by the simple route of reading the value table directly.

    Each row carries its ``definition_id``, ``field_key`` AND ``owner_scope``.
    Two rows with the same ``field_key`` and different scopes is the normal
    case, not a conflict — the caller is expected to show both.

    ``$3`` does double duty as ``target_type`` and as
    :data:`_VISIBLE_PREDICATE`'s ``applies_to``. That is deliberate and not a
    coincidence of numbering: a value may only exist against a definition whose
    ``applies_to`` equals its ``target_type`` — :func:`record_udf_value`
    enforces exactly that — so binding both to one parameter is the same
    constraint stated once.
    """
    org_id = _require_org(org_id)
    if not user_id:
        raise UdfError("user_id is required — value visibility is per-user")
    target_type = _check_choice(target_type, TARGET_TYPES, "target_type")
    rows = await conn.fetch(
        f"""
        SELECT {_VALUE_SELECT},
               d.field_key   AS field_key,
               d.owner_scope AS owner_scope,
               d.data_type   AS data_type,
               d.label       AS label
        FROM {TABLE_UDF_VALUES} v
        JOIN {TABLE_UDF_DEFINITIONS} d ON d.id = v.definition_id
        WHERE v.org_id = $1::uuid
          AND v.target_type = $3
          AND v.target_id = $4::uuid
          AND {_current('v')}
          AND {_VISIBLE_PREDICATE}
        {_VISIBLE_ORDER}
        """,
        org_id, str(user_id), target_type, str(target_id),
    )
    return [_value_row(r) for r in rows]


def _value_row(row) -> dict:
    out = dict(row)
    if isinstance(out.get("value_json"), str):
        out["value_json"] = json.loads(out["value_json"])
    return out
