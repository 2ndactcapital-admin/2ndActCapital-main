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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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
TABLE_UDF_DEFINITION_AUDIT = "portfolio.udf_definition_audit"
TABLE_UDF_TAG_ASSIGNMENTS = "portfolio.udf_tag_assignments"
TABLE_REFERENCE_DATA = "public.reference_data"
TABLE_REFERENCE_DATA_LISTS = "public.reference_data_lists"

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
#:
#: udf01a widened this from the original five. Widening the CHECK alone would
#: have changed NOTHING: :func:`_check_choice` refuses anything outside this
#: frozenset before a statement is ever issued, so the database constraint is
#: the second gate, not the first. Both move together or neither does.
DATA_TYPES = frozenset({
    "text", "long_text", "rich_text",
    "integer", "numeric", "currency", "percent",
    "date", "datetime", "boolean",
    "select", "multiselect", "tags",
    "email", "url", "phone",
})

#: Values validated against a value SET rather than a free literal.
CHOICE_TYPES = frozenset({"select", "multiselect"})

#: Values landing in ``value_numeric`` — a real ``numeric`` column, so Postgres
#: enforces the type and this module enforces the declared precision/scale.
NUMERIC_TYPES = frozenset({"integer", "numeric", "currency", "percent"})

#: Values landing in ``value_text``.
TEXT_TYPES = frozenset({"text", "long_text", "rich_text", "email", "url", "phone"})

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


class UdfTypeParamError(UdfError):
    """``type_params`` does not satisfy the contract for this ``data_type``.

    Distinct from :class:`UdfValueTypeError`, which is about a VALUE. This is
    about the FIELD's own declaration, and the router maps it to 422 rather than
    400 — the payload was well-formed, its contents were not satisfiable.
    """


class UdfImmutableError(UdfError):
    """An attempt to change a field that is immutable once set (``api_name``)."""


class UdfTypeChangeError(UdfError):
    """The requested ``data_type`` / bound change is not a widening.

    Carries ``affected_rows`` when the change was a NARROWING that would have
    been allowed had no stored value contradicted it — the caller needs the
    count to know whether cleaning up the data is even feasible.
    """

    def __init__(self, message: str, *, affected_rows: int | None = None):
        super().__init__(message)
        self.affected_rows = affected_rows


class UdfReferencedError(UdfError):
    """Soft delete refused: something still references this definition.

    Carries ``references`` — the real list, counted per referencing table — so
    the caller is told WHAT to clean up rather than merely that they cannot
    proceed.
    """

    def __init__(self, message: str, *, references: dict[str, int] | None = None):
        super().__init__(message)
        self.references = references or {}


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


# ── Type-parameter contract (udf01a Task 2b) ────────────────────────────────
#
# Every data_type declares what it needs to be a well-formed FIELD, checked at
# definition-save time and again on every value write. Checking twice is not
# belt-and-braces: a definition saved before a cap was tightened, or restated by
# update_definition, can carry params that no longer satisfy the contract, and
# the value write is the last point at which a bad declaration is still cheap to
# refuse. See :func:`validate_type_params`.

#: Params a data_type MUST declare.
TYPE_PARAM_REQUIRED: dict[str, tuple[str, ...]] = {
    "text":        ("length",),
    "long_text":   ("length",),
    "rich_text":   ("length",),
    "integer":     ("precision",),
    "numeric":     ("precision", "scale"),
    "percent":     ("precision", "scale"),
    "currency":    ("precision", "scale", "currency_code"),
    "select":      ("value_set_id",),
    "multiselect": ("value_set_id",),
    "tags":        (),
    "email":       ("length",),
    "url":         ("length",),
    "phone":       ("length",),
    "date":        (),
    "datetime":    (),
    "boolean":     (),
}

#: Params a data_type MAY declare. Anything outside required+optional is
#: REJECTED rather than ignored — a silently-dropped ``{"scale": 2}`` on a text
#: field is a caller who believes they configured something that does not exist.
#: This is also what gives date/datetime/boolean/tags — which require nothing —
#: a real negative case.
TYPE_PARAM_OPTIONAL: dict[str, tuple[str, ...]] = {
    "integer":  ("min", "max"),
    "numeric":  ("min", "max"),
    "percent":  ("min", "max"),
    "currency": ("min", "max"),
}

MAX_TEXT_LENGTH = 4000
MAX_INTEGER_PRECISION = 18
MAX_NUMERIC_PRECISION = 38

#: Fixed, not a default. A currency field is money, and money is stored at four
#: decimal places everywhere else in this database (fee_calc, revenue_events,
#: the GL). A tenant declaring scale=2 would silently round every half-cent it
#: ever stored, so the value is not configurable and scale != 4 is refused.
CURRENCY_SCALE = 4


def _int_param(params: dict, name: str, data_type: str) -> int:
    value = params[name]
    # bool is an int subclass; True would pass every bound check below.
    if isinstance(value, bool) or not isinstance(value, int):
        raise UdfTypeParamError(
            f"data_type={data_type!r} requires {name} to be a whole number — "
            f"got {type(value).__name__} ({value!r})"
        )
    return value


def validate_type_params(
    data_type: str, type_params: Any, *, max_rich_text_chars: int
) -> dict:
    """Validate ``type_params`` against ``data_type``'s contract.

    Returns the normalised params. Raises :class:`UdfTypeParamError` with a
    message naming the actual violated bound — the router turns that into a 422
    whose detail a UI can show verbatim.

    ``max_rich_text_chars`` is threaded in from ``org_settings`` rather than read
    here, so this function stays synchronous and testable without a connection,
    and so the cap is genuinely the org's and not a constant with a settings
    lookup bolted on top of it.
    """
    data_type = _check_choice(data_type, DATA_TYPES, "data_type")
    if type_params is None:
        type_params = {}
    if isinstance(type_params, str):
        type_params = json.loads(type_params)
    if not isinstance(type_params, dict):
        raise UdfTypeParamError(
            f"type_params must be an object — got {type(type_params).__name__}"
        )

    required = TYPE_PARAM_REQUIRED[data_type]
    optional = TYPE_PARAM_OPTIONAL.get(data_type, ())
    allowed = set(required) | set(optional)

    missing = [p for p in required if p not in type_params]
    if missing:
        raise UdfTypeParamError(
            f"data_type={data_type!r} requires type_params {sorted(required)}; "
            f"missing {missing}"
        )
    unknown = sorted(set(type_params) - allowed)
    if unknown:
        raise UdfTypeParamError(
            f"data_type={data_type!r} does not accept type_params {unknown} "
            f"(allowed: {sorted(allowed) or 'none'}). An unrecognised parameter "
            f"is refused rather than ignored — silently dropping it would leave "
            f"the caller believing they had configured something."
        )

    params = dict(type_params)

    if "length" in params:
        length = _int_param(params, "length", data_type)
        cap = max_rich_text_chars if data_type in ("long_text", "rich_text") else MAX_TEXT_LENGTH
        if length < 1 or length > cap:
            raise UdfTypeParamError(
                f"data_type={data_type!r} length must be between 1 and {cap} — "
                f"got {length}"
            )

    if "precision" in params:
        precision = _int_param(params, "precision", data_type)
        cap = MAX_INTEGER_PRECISION if data_type == "integer" else MAX_NUMERIC_PRECISION
        if precision < 1 or precision > cap:
            raise UdfTypeParamError(
                f"data_type={data_type!r} precision must be between 1 and {cap} "
                f"— got {precision}"
            )

    if "scale" in params:
        scale = _int_param(params, "scale", data_type)
        precision = _int_param(params, "precision", data_type)
        if scale < 0:
            raise UdfTypeParamError(f"scale must be >= 0 — got {scale}")
        if scale > precision:
            raise UdfTypeParamError(
                f"scale ({scale}) may not exceed precision ({precision}). A "
                f"number with more fractional digits than total digits is not "
                f"representable."
            )
        if data_type == "currency" and scale != CURRENCY_SCALE:
            raise UdfTypeParamError(
                f"data_type='currency' fixes scale at {CURRENCY_SCALE} — got "
                f"{scale}. Money is stored at four decimal places throughout "
                f"this database; a narrower scale would silently round every "
                f"sub-cent amount ever written to this field."
            )

    if data_type == "currency":
        code = params.get("currency_code")
        if not isinstance(code, str) or not code.strip():
            raise UdfTypeParamError(
                "data_type='currency' requires a non-empty currency_code"
            )
        params["currency_code"] = code.strip().upper()

    if data_type in CHOICE_TYPES:
        value_set_id = params.get("value_set_id")
        if not isinstance(value_set_id, str) or not value_set_id.strip():
            raise UdfTypeParamError(
                f"data_type={data_type!r} requires value_set_id — the id of a "
                f"public.reference_data_lists row. The FK udf_def_value_set_fk "
                f"refuses a non-existent list at write time."
            )
        params["value_set_id"] = value_set_id.strip()

    if "min" in params and "max" in params:
        lo, hi = _numeric(params["min"], "min"), _numeric(params["max"], "max")
        if lo > hi:
            raise UdfTypeParamError(f"min ({lo}) may not exceed max ({hi})")

    return params


def _normalize_options(
    data_type: str, options: Any, *, has_value_set: bool = False
) -> list[str] | None:
    """Coerce ``options`` to a list of choice strings, or ``None``.

    A ``select`` definition MUST carry a non-empty option list. That is not a
    style rule: :func:`record_udf_value` validates a select value against this
    list, so a select with no options is a field that can never accept any
    value at all. Refusing it at creation is the only point at which that is
    still obvious.

    Accepted shapes — a bare list, or ``{"choices": [...]}`` / ``{"options":
    [...]}``, because the Part 1 SQL left ``options`` as an unconstrained
    ``jsonb`` and both shapes are things a caller plausibly sends.

    udf01a: a ``select``/``multiselect`` may instead source its choices from a
    VALUE SET (``type_params.value_set_id`` → ``public.reference_data_lists``),
    in which case ``options`` is not required and the value set is
    authoritative. The inline-``options`` path is kept working unchanged for
    definitions that declare no value set — Phase G's four ``create_*`` entry
    points and their verification depend on it, and there was no stored data to
    migrate because ``udf_definitions`` was empty when udf01a ran.
    """
    if data_type not in CHOICE_TYPES or has_value_set:
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


def _datetime(value: Any, field_name: str) -> datetime:
    """A real ``datetime``. A bare ``date`` is REFUSED, not widened to midnight.

    The mirror of :func:`_date`'s refusal, and for the same reason: widening a
    date to 00:00 invents a time the caller never supplied, in a timezone
    nobody chose.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        raise UdfValueTypeError(
            f"{field_name} is a date, not a datetime. Widening it to midnight "
            f"would invent a time in an unstated timezone — pass a real "
            f"datetime if that is what you mean."
        )
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            raise UdfValueTypeError(
                f"{field_name}={value!r} is not an ISO-8601 datetime"
            ) from None
    raise UdfValueTypeError(
        f"{field_name} must be a datetime or an ISO-8601 string — got "
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
    """``json.dumps`` with a ``str`` fallback for non-JSON-native types.

    Load-bearing for the audit trail specifically: ``before``/``after`` snapshots
    come straight from ``get_definition``'s row (a ``datetime`` for
    ``deleted_at``/``updated_at``, a ``Decimal`` if a caller's ``default_value``
    ever carries one) rather than from caller-supplied JSON, so a plain
    ``json.dumps`` would raise on the very first soft-delete. ``str()`` is a
    lossy but sufficient representation for an audit record — nothing reads
    ``before_state``/``after_state`` back into typed Python.
    """
    return None if value is None else json.dumps(value, default=str)


# ── Task 2: DEFINE ──────────────────────────────────────────────────────────


_DEF_INSERT = f"""
INSERT INTO {TABLE_UDF_DEFINITIONS}
    (org_id, owner_scope, owner_scope_id, applies_to, field_key, label,
     data_type, options, display_order, is_active,
     type_params, api_name, help_text, description, is_required,
     default_value, is_unique, unique_case_sensitive, is_external_id,
     is_platform_managed, value_set_id)
VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7, $8::jsonb, $9, true,
        $10::jsonb, $11, $12, $13, $14,
        $15::jsonb, $16, $17, $18,
        $19, $20::uuid)
RETURNING id::text
"""


def _check_api_name(api_name: Any) -> str | None:
    if api_name is None:
        return None
    if not isinstance(api_name, str) or not api_name.strip():
        raise UdfError("api_name, if given, must be a non-empty string")
    return api_name.strip()


def _validate_definition(
    applies_to, field_key, label, data_type, options, *,
    type_params=None, api_name=None, help_text=None, description=None,
    is_required=False, default_value=None, is_unique=False,
    unique_case_sensitive=False, is_external_id=False,
    is_platform_managed=False, value_set_id=None,
    max_rich_text_chars: int,
) -> dict:
    """The arguments every scope shares, validated identically.

    One function rather than four copies, because the ONLY thing that legally
    differs between the scopes is who may write and what ``owner_scope_id``
    means — not what a valid field looks like. Four copies would drift.

    Returns a dict rather than a tuple: udf01a added enough fields that a
    positional tuple shared across four call sites would be a silent-reorder
    bug waiting to happen.
    """
    data_type = _check_choice(data_type, DATA_TYPES, "data_type")
    type_params = dict(type_params or {})
    if value_set_id and data_type in CHOICE_TYPES:
        type_params.setdefault("value_set_id", value_set_id)
    type_params = validate_type_params(
        data_type, type_params, max_rich_text_chars=max_rich_text_chars
    )
    has_value_set = bool(type_params.get("value_set_id"))
    return {
        "applies_to": _check_choice(applies_to, APPLIES_TO, "applies_to"),
        "field_key": _check_field_key(field_key),
        "label": _check_label(label),
        "data_type": data_type,
        "options": _normalize_options(data_type, options, has_value_set=has_value_set),
        "type_params": type_params,
        "api_name": _check_api_name(api_name),
        "help_text": help_text,
        "description": description,
        "is_required": bool(is_required),
        "default_value": default_value,
        "is_unique": bool(is_unique),
        "unique_case_sensitive": bool(unique_case_sensitive),
        "is_external_id": bool(is_external_id),
        "is_platform_managed": bool(is_platform_managed),
        "value_set_id": type_params.get("value_set_id"),
    }


async def _insert_definition(
    conn, *, org_id, owner_scope, owner_scope_id, field: dict, display_order: int,
    created_by: str | None = None,
) -> str:
    """Issue the INSERT and translate a unique violation.

    The ``try`` wraps ONLY the statement, and the translation is keyed on the
    constraint name, so an unrelated unique violation — should one ever be added
    to this table — surfaces as itself rather than being mislabelled a duplicate.
    Two different unique indexes can fire here (``idx_udf_def_key_unique`` on
    ``field_key``, ``udf_def_api_name_uq`` on ``api_name``) and the message
    names which one actually did.
    """
    params = (
        org_id, owner_scope, owner_scope_id, field["applies_to"],
        field["field_key"], field["label"], field["data_type"],
        _jsonb(field["options"]), int(display_order),
        _jsonb(field["type_params"]), field["api_name"], field["help_text"],
        field["description"], field["is_required"], _jsonb(field["default_value"]),
        field["is_unique"], field["unique_case_sensitive"], field["is_external_id"],
        field["is_platform_managed"], field["value_set_id"],
    )
    try:
        row = await conn.fetchrow(_DEF_INSERT, *params)
    except asyncpg.UniqueViolationError as exc:
        constraint = getattr(exc, "constraint_name", None)
        raise UdfDuplicateError(
            f"an ACTIVE definition already exists in this namespace "
            f"(scope={owner_scope!r}, org={org_id}, scope_id={owner_scope_id}, "
            f"applies_to={field['applies_to']!r}, field_key={field['field_key']!r}, "
            f"api_name={field['api_name']!r}). Refused by "
            f"{constraint or UDF_DEF_UNIQUE_INDEX} in the database — this is "
            f"not an application-level check and there is no pre-flight SELECT "
            f"to race against.",
            constraint=constraint,
        ) from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise UdfTypeParamError(
            f"value_set_id={field['value_set_id']!r} does not name an existing "
            f"public.reference_data_lists row. Refused by udf_def_value_set_fk "
            f"in the database, not an application-level lookup."
        ) from exc
    definition_id = row["id"]
    # 'create' is a valid udf_definition_audit.change_kind and every OTHER
    # lifecycle transition writes one row per call — creation is the first
    # lifecycle event a definition has, and skipping it here would make the
    # audit trail start silently one event late.
    await _lifecycle_audit(
        conn, definition_id=definition_id, org_id=org_id, changed_by=created_by,
        change_kind="create", before=None,
        after={**field, "id": definition_id, "org_id": org_id,
               "owner_scope": owner_scope, "owner_scope_id": owner_scope_id,
               "display_order": int(display_order)},
    )
    return definition_id


#: The udf01a metadata fields shared by every ``create_*`` entry point's
#: signature. Spelled out once so the four functions below cannot drift.
_FIELD_KWARGS = (
    "type_params", "api_name", "help_text", "description", "is_required",
    "default_value", "is_unique", "unique_case_sensitive", "is_external_id",
    "is_platform_managed", "value_set_id",
)


async def _default_max_rich_text_chars() -> int:
    """The PLATFORM default cap, for a definition with no org (platform-scope).

    ``org_settings.get_setting`` requires an ``org_id`` — there is no
    platform-scope settings row, by the same reasoning ``org_settings.py``
    documents for ``DEFAULT_SETTINGS`` being the platform default. A
    platform-scope field is bounded by that default directly, never by any
    tenant's override.
    """
    from services.org_settings import DEFAULT_SETTINGS

    return int(DEFAULT_SETTINGS["crm.udf.max_rich_text_chars"])


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
    created_by: str | None = None,
    **field_kwargs: Any,
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

    ``**field_kwargs`` — udf01a's metadata (``type_params``, ``api_name``, …,
    see :data:`_FIELD_KWARGS`) — is accepted here and by every sibling below,
    keyword-only, so an old caller passing only the Phase G five arguments is
    unaffected.
    """
    _require_super_admin(is_super_admin, "create_platform_definition")
    field = _validate_definition(
        applies_to, field_key, label, data_type, options,
        max_rich_text_chars=await _default_max_rich_text_chars(),
        **{k: v for k, v in field_kwargs.items() if k in _FIELD_KWARGS},
    )
    async with _SuperAdminWrite(conn) as c:
        return await _insert_definition(
            c, org_id=None, owner_scope=SCOPE_PLATFORM, owner_scope_id=None,
            field=field, display_order=int(display_order), created_by=created_by,
        )


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
    created_by: str | None = None,
    **field_kwargs: Any,
) -> str:
    """A field authored by one tenant, visible to everyone in that tenant.

    A standard org-write through A2's :class:`~services.portfolio_assets.
    _OrgWrite`: the ``org_id`` argument is compared against
    ``app.current_org_id`` by the policy's ``WITH CHECK``, so a mismatch is
    refused by the DATABASE and not by a Python ``if``.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        field = _validate_definition(
            applies_to, field_key, label, data_type, options,
            max_rich_text_chars=await _max_rich_text_chars(c, org_id),
            **{k: v for k, v in field_kwargs.items() if k in _FIELD_KWARGS},
        )
        return await _insert_definition(
            c, org_id=org_id, owner_scope=SCOPE_ORG, owner_scope_id=None,
            field=field, display_order=int(display_order), created_by=created_by,
        )


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
    created_by: str | None = None,
    **field_kwargs: Any,
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
        field = _validate_definition(
            applies_to, field_key, label, data_type, options,
            max_rich_text_chars=await _max_rich_text_chars(c, org_id),
            **{k: v for k, v in field_kwargs.items() if k in _FIELD_KWARGS},
        )
        return await _insert_definition(
            c, org_id=org_id, owner_scope=SCOPE_TEAM, owner_scope_id=str(team_id),
            field=field, display_order=int(display_order), created_by=created_by,
        )


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
    created_by: str | None = None,
    **field_kwargs: Any,
) -> str:
    """A field authored by one person, visible only to them.

    ``user_id`` is verified to belong to ``org_id`` for the same reason
    :func:`create_team_definition` verifies its team — see there.
    """
    org_id = _require_org(org_id)
    if not user_id:
        raise UdfError("user_id is required for a user-scope definition")
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
        field = _validate_definition(
            applies_to, field_key, label, data_type, options,
            max_rich_text_chars=await _max_rich_text_chars(c, org_id),
            **{k: v for k, v in field_kwargs.items() if k in _FIELD_KWARGS},
        )
        return await _insert_definition(
            c, org_id=org_id, owner_scope=SCOPE_USER, owner_scope_id=str(user_id),
            field=field, display_order=int(display_order), created_by=created_by,
        )


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
       d.is_active              AS is_active,
       d.type_params            AS type_params,
       d.api_name               AS api_name,
       d.help_text              AS help_text,
       d.description            AS description,
       d.is_required            AS is_required,
       d.default_value          AS default_value,
       d.is_unique              AS is_unique,
       d.unique_case_sensitive  AS unique_case_sensitive,
       d.is_external_id         AS is_external_id,
       d.is_platform_managed    AS is_platform_managed,
       d.value_set_id::text     AS value_set_id,
       d.deleted_at             AS deleted_at
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
AND d.deleted_at IS NULL
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
    for key in ("options", "type_params", "default_value"):
        if isinstance(out.get(key), str):
            out[key] = json.loads(out[key])
    return out


async def get_definition(
    conn, *, definition_id: str, include_deleted: bool = False
) -> dict | None:
    """One definition by id, or ``None``.

    No ``org_id`` argument, deliberately: RLS is the boundary here, and adding a
    Python ``AND org_id = ...`` on top would make the function pass under a
    ``bypassrls`` role for a reason that has nothing to do with the policy being
    correct. The verification reads a cross-org definition through the real
    ``app_service`` connection and asserts ``None``.

    ``include_deleted`` exists for the lifecycle functions themselves — a
    soft-delete reversal needs to read the row it is reversing.
    """
    deleted_clause = "" if include_deleted else "AND d.deleted_at IS NULL"
    row = await conn.fetchrow(
        f"SELECT {_DEF_SELECT} FROM {TABLE_UDF_DEFINITIONS} d "
        f"WHERE d.id = $1::uuid {deleted_clause} AND {_current('d')}",
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


# ── Task 2c/2d: LIFECYCLE ────────────────────────────────────────────────────
#
# Phase G shipped CREATE and READ only — no way to change a field, retire it, or
# bring it back. This section is that missing half. Every write here funnels
# through :func:`_lifecycle_audit`, which is the ONLY place that inserts into
# ``udf_definition_audit`` — so "one audit row per lifecycle call" is a property
# of there being one call site, not four matching implementations.

#: (old_data_type, new_data_type) transitions Task 2d allows. Anything else —
#: including any narrowing, and any pair not listed here — is refused with
#: "create a new field instead". Same-type (no data_type change at all) is
#: handled separately: it is always allowed and goes through the bound checks
#: below instead of this map.
ALLOWED_TYPE_WIDENING = frozenset({
    ("text", "long_text"),
    ("integer", "numeric"),
    ("select", "multiselect"),
})

#: type_params keys where a DECREASE narrows what is already stored.
_NARROWING_KEYS = ("scale", "length", "precision")


async def _lifecycle_write_scope(conn, definition: dict, *, org_id: str, is_super_admin: bool):
    """The write context this definition's scope requires — never chosen by
    the caller. A platform-scope definition can ONLY be written under
    ``_SuperAdminWrite``; anything else is written under ``_OrgWrite`` for the
    definition's OWN org, not the caller's claimed one, so a caller cannot aim
    a write at a definition it does not own by supplying a different org_id.
    """
    if definition["owner_scope"] == SCOPE_PLATFORM:
        _require_super_admin(is_super_admin, "modify a platform-scope definition")
        return _SuperAdminWrite(conn)
    def_org = definition["org_id"]
    if def_org != _require_org(org_id):
        raise UdfScopeError(
            f"definition belongs to org {def_org}, not the calling org {org_id}"
        )
    return _OrgWrite(conn, def_org)


async def _lifecycle_audit(
    conn, *, definition_id: str, org_id: str | None, changed_by: str | None,
    change_kind: str, before: dict | None, after: dict | None,
) -> None:
    await conn.execute(
        f"""INSERT INTO {TABLE_UDF_DEFINITION_AUDIT}
            (definition_id, org_id, changed_by, change_kind, before_state, after_state)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::jsonb, $6::jsonb)""",
        str(definition_id), org_id, str(changed_by) if changed_by else None,
        change_kind, _jsonb(before), _jsonb(after),
    )


async def _dry_run_narrow_count(conn, *, definition_id: str, key: str, new_value) -> int:
    """How many CURRENT values would violate a narrower bound, right now.

    Scoped to ``system_to IS NULL AND valid_to IS NULL`` — history rows are
    never affected by a narrower bound on the field going forward, only what a
    reader would see as the value today.
    """
    if key == "length":
        predicate = "value_text IS NOT NULL AND length(value_text) > $2"
        param = int(new_value)
    elif key == "precision":
        # Total significant digits as Postgres's own text rendering would show
        # them — the same count NUMERIC(p,s) itself enforces.
        predicate = (
            "value_numeric IS NOT NULL AND "
            "length(replace(trim(leading '-' from trim(trailing '0' from "
            "trim(trailing '.' from value_numeric::text))), '.', '')) > $2"
        )
        param = int(new_value)
    elif key == "min":
        predicate = "value_numeric IS NOT NULL AND value_numeric < $2"
        param = _numeric(new_value, "min")
    elif key == "max":
        predicate = "value_numeric IS NOT NULL AND value_numeric > $2"
        param = _numeric(new_value, "max")
    else:  # pragma: no cover — internal callers only pass the keys above
        raise ValueError(f"no dry-run predicate for {key!r}")
    return await conn.fetchval(
        f"""SELECT count(*) FROM {TABLE_UDF_VALUES}
            WHERE definition_id = $1::uuid
              AND system_to IS NULL AND valid_to IS NULL
              AND {predicate}""",
        str(definition_id), param,
    )


async def _validate_type_change(
    conn, *, definition_id: str, old_data_type: str, old_params: dict,
    new_data_type: str, new_params: dict, max_rich_text_chars: int,
) -> dict:
    """Enforce Task 2d's widening-only matrix. Returns the validated new params.

    Raises immediately, with NO dry run, for a scale decrease — "unconditional"
    per the spec, because a stored value already quantized to the old (larger)
    scale cannot be un-rounded by a bound change no matter how few rows exist.
    Raises with ``affected_rows`` for every other narrowing, ONLY if the dry
    run found at least one row that would violate it.
    """
    if new_data_type != old_data_type:
        if (old_data_type, new_data_type) not in ALLOWED_TYPE_WIDENING:
            raise UdfTypeChangeError(
                f"{old_data_type!r} -> {new_data_type!r} is not a supported "
                f"widening. Only text->long_text, integer->numeric and "
                f"select->multiselect are — create a new field instead."
            )

    merged = dict(old_params)
    merged.update(new_params)

    if "scale" in old_params and "scale" in merged:
        if merged["scale"] < old_params["scale"]:
            raise UdfTypeChangeError(
                f"scale may not decrease ({old_params['scale']} -> "
                f"{merged['scale']}) — blocked unconditionally, not subject to "
                f"a dry run. A value already stored at the wider scale cannot "
                f"be un-rounded by narrowing the bound."
            )

    for key in ("length", "precision"):
        if key in old_params and key in merged and merged[key] < old_params[key]:
            affected = await _dry_run_narrow_count(
                conn, definition_id=definition_id, key=key, new_value=merged[key]
            )
            if affected:
                raise UdfTypeChangeError(
                    f"{key} may not decrease from {old_params[key]} to "
                    f"{merged[key]}: {affected} stored value(s) would violate "
                    f"the new bound. Clean up the data first, or create a new "
                    f"field instead.",
                    affected_rows=affected,
                )

    for key, cmp in (("min", "min"), ("max", "max")):
        if key in old_params and key in merged:
            old_v, new_v = _numeric(old_params[key], key), _numeric(merged[key], key)
            narrows = (key == "min" and new_v > old_v) or (key == "max" and new_v < old_v)
            if narrows:
                affected = await _dry_run_narrow_count(
                    conn, definition_id=definition_id, key=key, new_value=merged[key]
                )
                if affected:
                    raise UdfTypeChangeError(
                        f"{key} may not narrow from {old_params[key]} to "
                        f"{merged[key]}: {affected} stored value(s) would "
                        f"violate it. Clean up the data first, or create a new "
                        f"field instead.",
                        affected_rows=affected,
                    )

    return validate_type_params(
        new_data_type, merged, max_rich_text_chars=max_rich_text_chars
    )


_UPDATABLE_SCALAR_FIELDS = (
    "label", "help_text", "description", "is_required", "default_value",
    "is_unique", "unique_case_sensitive", "is_external_id", "display_order",
    "options",
)


async def update_definition(
    conn, *, definition_id: str, org_id: str, changed_by: str | None,
    changes: dict[str, Any], is_super_admin: bool = False,
) -> dict:
    """Apply a sparse PATCH. Only keys PRESENT in ``changes`` are touched.

    ``changes`` — not ``**kwargs`` — because "field omitted" and "field set to
    None" must stay distinguishable (``default_value`` and ``help_text`` are
    both legitimately nullable). The router builds this from
    ``model_fields_set``, the same convention the Workflow triggers CRUD screen
    established for a sparse PATCH.

    ``api_name`` is immutable: present in ``changes`` with any value other than
    the current one raises :class:`UdfImmutableError` before anything else runs.

    A ``data_type`` or ``type_params`` change goes through Task 2d's
    widening-only matrix (:func:`_validate_type_change`) rather than the plain
    field update below.
    """
    current = await get_definition(conn, definition_id=definition_id)
    if current is None:
        raise UdfError(f"definition {definition_id} does not exist or is deleted")

    if "api_name" in changes and changes["api_name"] != current["api_name"]:
        raise UdfImmutableError(
            f"api_name is immutable once set (current={current['api_name']!r}, "
            f"attempted={changes['api_name']!r}). label is free to change; "
            f"api_name is not."
        )

    scope_cm = await _lifecycle_write_scope(
        conn, current, org_id=org_id, is_super_admin=is_super_admin
    )
    async with scope_cm as c:
        cap = (
            await _default_max_rich_text_chars() if current["org_id"] is None
            else await _max_rich_text_chars(c, current["org_id"])
        )

        set_clauses = ["updated_at = now()", "updated_by = $2::uuid"]
        params: list[Any] = [str(definition_id), str(changed_by) if changed_by else None]

        if "data_type" in changes or "type_params" in changes:
            new_data_type = changes.get("data_type", current["data_type"])
            new_data_type = _check_choice(new_data_type, DATA_TYPES, "data_type")
            new_params = await _validate_type_change(
                c, definition_id=definition_id,
                old_data_type=current["data_type"], old_params=current["type_params"] or {},
                new_data_type=new_data_type, new_params=changes.get("type_params") or {},
                max_rich_text_chars=cap,
            )
            params.append(new_data_type)
            set_clauses.append(f"data_type = ${len(params)}")
            params.append(_jsonb(new_params))
            set_clauses.append(f"type_params = ${len(params)}::jsonb")
            new_value_set_id = new_params.get("value_set_id")
            params.append(new_value_set_id)
            set_clauses.append(f"value_set_id = ${len(params)}::uuid")

        for field in _UPDATABLE_SCALAR_FIELDS:
            if field not in changes:
                continue
            value = changes[field]
            if field == "label":
                value = _check_label(value)
            elif field == "options":
                value = _jsonb(value)
            elif field in ("default_value",):
                value = _jsonb(value)
            column = "options" if field == "options" else field
            cast = "::jsonb" if field in ("options", "default_value") else ""
            params.append(value)
            set_clauses.append(f"{column} = ${len(params)}{cast}")

        if len(set_clauses) == 2:  # only updated_at/updated_by — nothing real changed
            return current

        await c.execute(
            f"UPDATE {TABLE_UDF_DEFINITIONS} SET {', '.join(set_clauses)} "
            f"WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
            *params,
        )
        after = await get_definition(c, definition_id=definition_id)
        await _lifecycle_audit(
            c, definition_id=definition_id, org_id=current["org_id"],
            changed_by=changed_by, change_kind="update",
            before=current, after=after,
        )
        return after


async def _set_active(
    conn, *, definition_id: str, org_id: str, changed_by: str | None,
    is_super_admin: bool, target_active: bool, change_kind: str,
) -> dict:
    current = await get_definition(conn, definition_id=definition_id)
    if current is None:
        raise UdfError(f"definition {definition_id} does not exist or is deleted")
    if current["is_active"] == target_active:
        return current
    scope_cm = await _lifecycle_write_scope(
        conn, current, org_id=org_id, is_super_admin=is_super_admin
    )
    async with scope_cm as c:
        await c.execute(
            f"UPDATE {TABLE_UDF_DEFINITIONS} SET is_active = $2, "
            f"updated_at = now(), updated_by = $3::uuid "
            f"WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
            str(definition_id), target_active,
            str(changed_by) if changed_by else None,
        )
        after = await get_definition(c, definition_id=definition_id)
        await _lifecycle_audit(
            c, definition_id=definition_id, org_id=current["org_id"],
            changed_by=changed_by, change_kind=change_kind,
            before=current, after=after,
        )
        return after


async def deactivate_definition(
    conn, *, definition_id: str, org_id: str, changed_by: str | None,
    is_super_admin: bool = False,
) -> dict:
    """``is_active = false``. Values remain untouched and readable; only NEW
    value writes and resolution for new callers stop — ``record_udf_value``
    refuses on an inactive definition, ``_VISIBLE_PREDICATE`` excludes it."""
    return await _set_active(
        conn, definition_id=definition_id, org_id=org_id, changed_by=changed_by,
        is_super_admin=is_super_admin, target_active=False, change_kind="deactivate",
    )


async def reactivate_definition(
    conn, *, definition_id: str, org_id: str, changed_by: str | None,
    is_super_admin: bool = False,
) -> dict:
    """The reverse of :func:`deactivate_definition`."""
    return await _set_active(
        conn, definition_id=definition_id, org_id=org_id, changed_by=changed_by,
        is_super_admin=is_super_admin, target_active=True, change_kind="reactivate",
    )


async def get_definition_references(conn, *, definition_id: str) -> dict[str, int]:
    """How many rows, per referencing table, currently point at this definition.

    Both tables carry a live FK on ``definition_id``: ``udf_values`` (a value
    was ever recorded, including history) and ``udf_tag_assignments`` (a tag
    was ever assigned). Either kind of reference blocks a soft delete — see
    :func:`soft_delete_definition`.
    """
    values = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_VALUES} WHERE definition_id = $1::uuid",
        str(definition_id),
    )
    tags = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_TAG_ASSIGNMENTS} WHERE definition_id = $1::uuid",
        str(definition_id),
    )
    refs = {}
    if values:
        refs["udf_values"] = values
    if tags:
        refs["udf_tag_assignments"] = tags
    return refs


async def soft_delete_definition(
    conn, *, definition_id: str, org_id: str, changed_by: str | None,
    is_super_admin: bool = False,
) -> dict:
    """``deleted_at``/``deleted_by``, refused if anything references this field.

    The reference check runs in the SAME transaction as the write, so a value
    recorded a moment after the check but before the UPDATE commits is not a
    real race: this connection holds the row via its own read, and a
    concurrent ``record_udf_value`` on a DIFFERENT definition is unaffected;
    a concurrent value write against THIS definition_id would need the same
    definition row, which Postgres locks for the duration of this UPDATE.
    """
    current = await get_definition(conn, definition_id=definition_id)
    if current is None:
        raise UdfError(f"definition {definition_id} does not exist or is already deleted")
    refs = await get_definition_references(conn, definition_id=definition_id)
    if refs:
        raise UdfReferencedError(
            f"definition {definition_id} is referenced and cannot be deleted: "
            f"{refs}. Deactivate it instead, or clear the references first.",
            references=refs,
        )
    scope_cm = await _lifecycle_write_scope(
        conn, current, org_id=org_id, is_super_admin=is_super_admin
    )
    async with scope_cm as c:
        await c.execute(
            f"UPDATE {TABLE_UDF_DEFINITIONS} SET deleted_at = now(), "
            f"deleted_by = $2::uuid, updated_at = now(), updated_by = $2::uuid "
            f"WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
            str(definition_id), str(changed_by) if changed_by else None,
        )
        after = await get_definition(c, definition_id=definition_id, include_deleted=True)
        await _lifecycle_audit(
            c, definition_id=definition_id, org_id=current["org_id"],
            changed_by=changed_by, change_kind="soft_delete",
            before=current, after=after,
        )
        return after


async def undelete_definition(
    conn, *, definition_id: str, org_id: str, changed_by: str | None,
    is_super_admin: bool = False,
) -> dict:
    """Reverse :func:`soft_delete_definition`. Values were never touched by the
    delete, so nothing here needs to restore them."""
    current = await get_definition(conn, definition_id=definition_id, include_deleted=True)
    if current is None:
        raise UdfError(f"definition {definition_id} does not exist")
    if current["deleted_at"] is None:
        return current
    scope_cm = await _lifecycle_write_scope(
        conn, current, org_id=org_id, is_super_admin=is_super_admin
    )
    async with scope_cm as c:
        await c.execute(
            f"UPDATE {TABLE_UDF_DEFINITIONS} SET deleted_at = NULL, "
            f"deleted_by = NULL, updated_at = now(), updated_by = $2::uuid "
            f"WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
            str(definition_id), str(changed_by) if changed_by else None,
        )
        after = await get_definition(c, definition_id=definition_id)
        await _lifecycle_audit(
            c, definition_id=definition_id, org_id=current["org_id"],
            changed_by=changed_by, change_kind="reactivate",
            before=current, after=after,
        )
        return after


# ── Task 4: VALUES ──────────────────────────────────────────────────────────


def coerce_value(
    data_type: str, value: Any, options: Any = None, type_params: Any = None
) -> dict:
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
    params = dict(type_params or {})
    cols: dict[str, Any] = {c: None for c in _VALUE_COLUMNS}

    if data_type in NUMERIC_TYPES:
        cols["value_numeric"] = _coerce_numeric_value(data_type, value, params)
    elif data_type == "date":
        cols["value_date"] = _date(value, "value")
    elif data_type == "datetime":
        # There is no value_timestamp column and value_date would silently drop
        # the time. A datetime lands in value_json as an ISO-8601 string, the
        # same column a boolean uses, and round-trips exactly.
        cols["value_json"] = _jsonb(_datetime(value, "value").isoformat())
    elif data_type == "boolean":
        cols["value_json"] = _jsonb(_boolean(value, "value"))
    elif data_type in TEXT_TYPES:
        text = _text(value, "value")
        _check_length(data_type, text, params)
        cols["value_text"] = text
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
    elif data_type == "multiselect":
        choices = _coerce_choice_list(options, required=True)
        if not isinstance(value, (list, tuple)):
            raise UdfValueTypeError(
                f"data_type='multiselect' takes a list of codes — got "
                f"{type(value).__name__}"
            )
        picked = [_text(v, "value") for v in value]
        stray = [v for v in picked if v not in choices]
        if stray:
            raise UdfValueTypeError(
                f"values {stray} are not in this multiselect's value set "
                f"{choices}"
            )
        if len(set(picked)) != len(picked):
            raise UdfValueTypeError(f"multiselect value contains duplicates: {picked}")
        # A list lands in value_json, not value_text: value_text holds ONE
        # scalar everywhere else, and a comma-joined string could not be
        # filtered on without parsing it back out.
        cols["value_json"] = _jsonb(picked)
    elif data_type == "tags":
        raise UdfValueTypeError(
            "data_type='tags' values are not written through record_udf_value. "
            "Tags live in portfolio.udf_tag_assignments — one row per assigned "
            "tag — so that a vocabulary can be merged and renamed without "
            "rewriting every record that carries it. Use "
            "services.portfolio_udf_tags.assign_tags."
        )
    else:  # pragma: no cover — _check_choice already refused it
        raise UdfValueTypeError(f"unsupported data_type={data_type!r}")
    return cols


def _check_length(data_type: str, text: str, params: dict) -> None:
    length = params.get("length")
    if isinstance(length, int) and not isinstance(length, bool) and len(text) > length:
        raise UdfValueTypeError(
            f"value is {len(text)} characters; this {data_type} field declares "
            f"length={length}"
        )


def _coerce_numeric_value(data_type: str, value: Any, params: dict) -> Decimal:
    """A numeric value at the field's DECLARED scale, never a float.

    ``quantize`` with ``ROUND_HALF_UP`` rather than Python's default
    banker's rounding: a tenant declaring scale=2 and writing 1.005 means 1.01,
    and ``ROUND_HALF_EVEN`` would give 1.00 for that and 1.02 for 1.015, which
    is indefensible in a number a client may be billed on.

    The quantize happens BEFORE the write, so ``value_numeric`` — an unbounded
    ``numeric`` column that would otherwise accept any scale — holds exactly
    what the field declares. Postgres enforces that it is a number; only this
    enforces that it is the right SHAPE of number.
    """
    number = _numeric(value, "value")
    if data_type == "integer":
        if number != number.to_integral_value():
            raise UdfValueTypeError(
                f"data_type='integer' takes a whole number — got {number}"
            )
        number = number.to_integral_value()
    else:
        scale = params.get("scale")
        if isinstance(scale, int) and not isinstance(scale, bool):
            number = number.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)

    precision = params.get("precision")
    if isinstance(precision, int) and not isinstance(precision, bool):
        digits = len(number.as_tuple().digits)
        exponent = number.as_tuple().exponent
        # Total significant digits to the left of the point plus the declared
        # fractional digits — the same thing NUMERIC(p,s) counts.
        used = max(digits, digits + int(exponent) if exponent < 0 else digits)
        if used > precision:
            raise UdfValueTypeError(
                f"value {number} needs {used} digits; this {data_type} field "
                f"declares precision={precision}"
            )

    lo, hi = params.get("min"), params.get("max")
    if lo is not None and number < _numeric(lo, "min"):
        raise UdfValueTypeError(f"value {number} is below the declared min {lo}")
    if hi is not None and number > _numeric(hi, "max"):
        raise UdfValueTypeError(f"value {number} is above the declared max {hi}")
    return number


#: udf01a Task 2a — close the predecessor on the SYSTEM axis.
#:
#: The valid axis is deliberately untouched. A UDF value correction is "we
#: recorded the wrong thing", not "the thing changed on this date" — system-time
#: archival is the axis for that, and it is the one the 52 bi-temporal tables in
#: this database already use for the same situation (CLAUDE.md Rule 3).
#:
#: This also keeps ``idx_udf_values_unique`` satisfied without any ON CONFLICT:
#: the index is PARTIAL on ``system_to IS NULL AND valid_to IS NULL``, so
#: stamping ``system_to`` drops the predecessor out of the index and the
#: successor INSERT sees no conflict at all.
_VALUE_CLOSE = f"""
UPDATE {TABLE_UDF_VALUES}
   SET system_to = now()
 WHERE org_id = $1::uuid AND definition_id = $2::uuid
   AND target_type = $3 AND target_id = $4::uuid
   AND system_to IS NULL AND valid_to IS NULL
RETURNING id::text
"""

_VALUE_INSERT = f"""
INSERT INTO {TABLE_UDF_VALUES}
    (org_id, definition_id, target_type, target_id,
     value_text, value_numeric, value_date, value_json)
VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7, $8::jsonb)
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

    APPEND, not upsert — changed by udf01a. Phase G stored one row per
    (definition, target) and OVERWROTE it in place, on the reasoning that a UDF
    value is an annotation nothing reads "as of" a past date. That reasoning
    does not survive this layer becoming the CRM's general custom-field store:
    a field can now be a currency amount or a compliance flag, and destroying
    the prior value on edit leaves no answer to "what did this say when the
    decision was made". Phase G anticipated exactly this and scoped the fix to
    one statement; this is that change.

    Close-predecessor-then-insert, both inside the SAME ``_OrgWrite``
    transaction, so a reader never sees zero current rows or two. The result is
    an append-only history with no new table: the current value is the row with
    ``system_to IS NULL``, and every prior value is still there behind it.
    """
    org_id = _require_org(org_id)
    target_type = _check_choice(target_type, TARGET_TYPES, "target_type")
    if not target_id:
        raise UdfError("target_id is required")

    async with _OrgWrite(conn, org_id) as c:
        definition = await c.fetchrow(
            f"SELECT d.id::text AS id, d.applies_to, d.data_type, d.options, "
            f"       d.type_params, d.is_active, "
            f"       d.owner_scope, d.org_id::text AS org_id "
            f"FROM {TABLE_UDF_DEFINITIONS} d "
            f"WHERE d.id = $1::uuid AND d.deleted_at IS NULL AND {_current('d')}",
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
        if not definition["is_active"]:
            raise UdfError(
                f"definition {definition_id} is deactivated. A deactivated "
                f"field keeps its stored values readable but accepts no new "
                f"ones — reactivate it first."
            )
        options = definition["options"]
        if isinstance(options, str):
            options = json.loads(options)
        params = definition["type_params"]
        if isinstance(params, str):
            params = json.loads(params)
        params = params or {}

        # Re-validated on EVERY value write, not only at definition save. A
        # definition restated by update_definition, or saved before a cap moved,
        # can carry params that no longer satisfy the contract; this is the last
        # point at which refusing is still cheap.
        if params:
            validate_type_params(
                definition["data_type"], params,
                max_rich_text_chars=await _max_rich_text_chars(c, org_id),
            )
        # A value set, when declared, is authoritative over inline options.
        if definition["data_type"] in CHOICE_TYPES and params.get("value_set_id"):
            options = await _value_set_codes(c, params["value_set_id"])

        cols = coerce_value(definition["data_type"], value, options, params)
        await c.execute(
            _VALUE_CLOSE, org_id, str(definition_id), target_type, str(target_id)
        )
        row = await c.fetchrow(
            _VALUE_INSERT,
            org_id, str(definition_id), target_type, str(target_id),
            cols["value_text"], cols["value_numeric"], cols["value_date"],
            cols["value_json"],
        )
    return row["id"]


async def _max_rich_text_chars(conn, org_id: str) -> int:
    """The org's ``crm.udf.max_rich_text_chars``, resolved from org_settings.

    Imported lazily: ``org_settings`` is a public.* module and importing it at
    module scope from a ``portfolio.*`` service would make the dependency
    two-directional for no benefit.
    """
    from services.org_settings import get_setting

    return int(await get_setting(conn, org_id, "crm.udf.max_rich_text_chars"))


async def _value_set_codes(conn, value_set_id: str) -> list[str]:
    """The ACTIVE codes of a value set, in display order.

    Reads through ``list_id`` — the real FK added by udf01a — rather than
    matching on ``list_key`` text, so an org list and the platform list of the
    same name cannot be confused for one another.
    """
    rows = await conn.fetch(
        f"SELECT code FROM {TABLE_REFERENCE_DATA} "
        f"WHERE list_id = $1::uuid AND is_active = true "
        f"ORDER BY display_order, code",
        str(value_set_id),
    )
    return [r["code"] for r in rows]


_VALUE_HISTORY_SQL = f"""
SELECT v.id::text AS id, v.value_text, v.value_numeric, v.value_date,
       v.value_json, v.system_from, v.system_to
FROM {TABLE_UDF_VALUES} v
WHERE v.org_id = $1::uuid AND v.definition_id = $2::uuid
  AND v.target_type = $3 AND v.target_id = $4::uuid
ORDER BY v.system_from, v.id
"""


async def get_value_history(
    conn, *, org_id: str, definition_id: str, target_type: str, target_id: str
) -> list[dict]:
    """Every value ever recorded for one (definition, target), oldest first.

    The current value is the single row with ``system_to IS NULL``. This is the
    17a-4 read: a value overwritten last week is still here, with the exact
    window it was believed during.
    """
    org_id = _require_org(org_id)
    target_type = _check_choice(target_type, TARGET_TYPES, "target_type")
    rows = await conn.fetch(
        _VALUE_HISTORY_SQL, org_id, str(definition_id), target_type, str(target_id)
    )
    return [_value_row(r) for r in rows]


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
