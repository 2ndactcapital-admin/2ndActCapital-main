"""The tenant portfolio layer — assets, positions, valuations, transactions.

WHAT THIS LAYER IS, AND HOW IT DIFFERS FROM A1
──────────────────────────────────────────────────────────────────────────────
``services.securities_global`` (Portfolio A1) owns the ONE table set with no
``org_id``: a CUSIP means the same thing to every tenant, so the row is shared,
reads are unconditional and writes require Super Admin.

This module owns the opposite. ``portfolio.assets`` / ``asset_identifiers`` /
``positions`` / ``valuations`` / ``transactions`` / ``external_references`` are
**tenant data**. Every one carries ``org_id`` and exactly ONE RLS policy —

    org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
    OR current_setting('app.is_super_admin', true) = 'true'

— ``cmd=ALL``, covering both ``USING`` and ``WITH CHECK``. So the gate here is
**org isolation, not Super Admin**. Nothing in this module is Super-Admin-gated;
a Super Admin passes the policy the same way they pass every other one in the
codebase, via the explicit escape hatch, and needs no separate code path.

That difference is worth stating loudly because the failure mode is silent: if
A1's four-policy ``USING (true)`` global-read shape were ever copy-pasted onto
one of these six tables, every tenant would read every other tenant's positions
and nothing would raise. ``verify_portfolioa2.py`` asserts the policy count is
exactly one per table for precisely that reason.

FIVE THINGS MEASURED AGAINST THE DEPLOYED DATABASE, NOT ASSUMED
──────────────────────────────────────────────────────────────────────────────
1. **Every table name here is schema-qualified, always.** ``app_service`` has no
   ``pg_db_role_setting`` row, so its ``search_path`` is ``"$user", public`` and
   ``portfolio`` is NOT on it. An unqualified ``FROM assets`` raises
   ``UndefinedTableError`` under the production role while working fine in a
   psql session that happened to ``SET search_path`` — invisible in development,
   total in production. Hence the ``TABLE_*`` constants below and no bare table
   name anywhere in executable code. The verify script AST-parses this file and
   fails on any bare ``FROM``/``INTO``/``UPDATE`` of one of them.

2. **``portfolio.positions`` has NO CHECK constraint tying ``ownership_basis``
   to which measure is populated.** Introspected: the only CHECKs on that table
   are ``positions_basis_chk`` (the vocabulary), ``positions_authority_chk`` and
   ``positions_source_chk``. Nothing stops a ``value``-basis row from carrying a
   ``quantity``. :func:`create_position` is therefore the ONLY thing enforcing
   it — there is no database backstop to fall through to, which is the whole
   reason that validation is written out longhand instead of trusted to the DB.

3. **``portfolio.assets.asset_type`` is ``NOT NULL`` with no CHECK.** Unlike
   ``asset_class``, ``ownership_basis`` and ``valuation_method``, which all carry
   deployed vocabularies mirrored below, ``asset_type`` is open text. It is
   validated for non-emptiness and nothing more, deliberately: inventing a
   vocabulary in Python that the database does not share would reject rows the
   database would happily take, and the next person would have no way to tell
   which layer was wrong.

4. **``valuations`` is append-only, and supersession is a FORWARD pointer.**
   ``supersedes_valuation_id`` lives on the NEW row and points back at the old
   one. :func:`record_valuation` never updates the prior row — see its docstring
   for why the obvious alternative destroys the thing it is trying to record.

5. **``transaction_types.market`` was NULL on all 16 rows** until this sprint's
   backfill (``docs/portfolioa2_part2_backfill.sql``). :func:`record_transaction`
   treats a NULL ``market`` as "unclassified, no opinion" and allows it, because
   an org can add its own transaction type and there is no default; only an
   explicit ``public``/``private`` mismatch is refused.

WHAT IS DELIBERATELY NOT HERE
──────────────────────────────────────────────────────────────────────────────
No ingestion. No source-precedence resolution — ``positions.superseded_by_source``
is written when a caller supplies it and is never *computed*; the precedence
rules are Phase B. No rollup into ``entity_holdings`` (Phase C), no SPV
derivation view (Phase D), no cash modelling, no corporate actions, no router
and no UI.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# ── Schema-qualified table names. See point 1 in the module docstring. ───────
TABLE_ASSETS = "portfolio.assets"
TABLE_ASSET_IDENT = "portfolio.asset_identifiers"
TABLE_POSITIONS = "portfolio.positions"
TABLE_VALUATIONS = "portfolio.valuations"
TABLE_TRANSACTIONS = "portfolio.transactions"
TABLE_EXT_REF = "portfolio.external_references"

# `public` IS on the search_path, so these two do not strictly need qualifying.
# They are qualified anyway: a reader scanning this file for "which tables does
# it touch" should not have to know which schema each name happens to resolve
# in, and the symmetry is what keeps rule 1 a habit rather than a special case.
TABLE_TXN_TYPES = "public.transaction_types"
TABLE_ENTITIES = "public.entities"

# The permission a router should require before calling any write below. NOT
# checked in here: `services.rbac.has_permission` takes a pool and raises
# `HTTPException`, which is a router-layer concern, and A2 ships no router.
# Recorded so the Phase B endpoints do not invent a new permission name — this
# one already exists in `public.permissions`.
WRITE_PERMISSION = "manage_portfolio"
READ_PERMISSION = "view_portfolio"


# ── Vocabularies, mirrored verbatim from the deployed CHECK constraints ──────
# Duplicated in Python on purpose, exactly as `services.securities_global` does
# it: a CHECK violation surfaces as a 23514 naming a constraint, which tells a
# caller nothing about what it should have passed. These produce the real error.
# Each must stay in sync with the constraint named in its comment.

# assets_class_chk
ASSET_CLASSES = frozenset({"financial", "hard_asset"})

# assets_basis_chk / positions_basis_chk (the same three, on both tables)
UNITS, PERCENT, VALUE = "units", "percent", "value"
OWNERSHIP_BASES = frozenset({UNITS, PERCENT, VALUE})

# assets_valuation_chk
MARKET_PRICE = "market_price"
AMORTIZED_COST = "amortized_cost"
VALUATION_METHODS = frozenset({
    MARKET_PRICE, "nav", "appraisal", "mark_to_model", AMORTIZED_COST,
})
# Methods that mean "there is no listed market for this thing".
PRIVATE_VALUATION_METHODS = frozenset({"nav", "appraisal", "mark_to_model"})

# asset_ident_type_chk — note `parcel` and `vin`, which securities_global's
# equivalent constraint does NOT have. A tenant asset can be a house or a car.
IDENTIFIER_TYPES = frozenset({
    "cusip", "isin", "ticker", "sedol", "figi", "lei", "internal",
    "parcel", "vin",
})
# Case-insensitive by convention, stored upper-cased. `internal` is excluded for
# the same reason it is in A1 — an internal key is whatever its minter says it
# is, and folding its case would collide two distinct keys. `parcel` joins it:
# an APN can be case-significant and is not a market identifier.
_UPPERCASED_ID_TYPES = frozenset({"cusip", "isin", "ticker", "sedol", "figi", "lei", "vin"})

# positions_authority_chk / transactions_authority_chk
AUTHORITIES = frozenset({"aggregated", "custodial", "internal", "stated", "manual"})

# positions_source_chk. transactions has NO source CHECK, but the same
# vocabulary is applied there anyway — a transaction whose source_system is not
# one a position could have carried is a typo, not a new integration.
#
# `reporting_tool_import` was added by Phase B (docs/portfoliob_part1.sql). The
# four vendor-specific tokens each ASSERT which tool produced the data; the file
# importer cannot honestly assert that, because Black Diamond, Addepar, Orion and
# APX all export the same tabular shape and sniffing the vendor from column
# headers would manufacture provenance the file does not carry.
SOURCE_SYSTEMS = frozenset({
    "reporting_tool_bd", "reporting_tool_addepar", "reporting_tool_orion",
    "reporting_tool_apx", "reporting_tool_import", "altruist",
    "spv_subscriptions", "chancery", "manual",
})

# valuations_basis_chk
PER_UNIT, TOTAL = "per_unit", "total"
VALUE_BASES = frozenset({PER_UNIT, TOTAL})

# valuations_purpose_chk
VALUATION_PURPOSES = frozenset({"market", "net_worth", "insurance", "tax_basis", "estate"})

# valuations_status_chk
VALUATION_STATUSES = frozenset({"estimated", "preliminary", "final", "audited", "restated"})

# The resolver ladder. Lower number wins. See `resolve_current_value`.
_STATUS_PRIORITY = {"audited": 0, "final": 1, "preliminary": 2, "estimated": 3, "restated": 4}
# What a superseded row is demoted to, regardless of its own status.
_SUPERSEDED_PRIORITY = 9

# ext_ref_record_type_chk
EXT_REF_RECORD_TYPES = frozenset({"asset", "position", "transaction", "account"})

# transaction_types_market_chk
PUBLIC_MARKET, PRIVATE_MARKET, BOTH_MARKETS = "public", "private", "both"
MARKETS = frozenset({PUBLIC_MARKET, PRIVATE_MARKET, BOTH_MARKETS})

# The entity_type that is an operational node rather than a CRM relationship.
# Mirrored from `schemas.entities.OPERATIONAL_ENTITY_TYPES`, which is where the
# CRM-facing exclusions read it from. Kept as a plain constant here so this
# module does not import a Pydantic schema.
ACCOUNT_ENTITY_TYPE = "account"


# The "this row is the current truth" predicate, written once. Both temporal
# axes, because a row can be superseded (valid_to) or corrected (system_to).
# Alias-qualified on BOTH columns, always — written bare and interpolated after
# an alias prefix, only the first column gets qualified and the second is
# ambiguous the moment a second temporal table joins in.
def _current(alias: str) -> str:
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


# ── Errors ──────────────────────────────────────────────────────────────────


class PortfolioError(ValueError):
    """A write was refused for a reason the caller can fix."""


class OwnershipBasisError(PortfolioError):
    """The supplied measures do not match the declared ``ownership_basis``.

    Its own class because this is the one validation with no database backstop
    (module docstring point 2) — an ingestion pipeline that wants to quarantine
    malformed position rows and keep going needs to be able to catch exactly
    this, without also swallowing a missing FK or a bad vocabulary value.
    """


class TransactionMarketError(PortfolioError):
    """A transaction type's market does not fit the asset it was aimed at."""


# ── Internal helpers ────────────────────────────────────────────────────────


def _check_choice(value: str, allowed: frozenset[str], field_name: str) -> str:
    if value not in allowed:
        raise PortfolioError(f"{field_name}={value!r} is not one of {sorted(allowed)}")
    return value


def _money(value: Any, field_name: str) -> Decimal:
    """Coerce to Decimal, refusing float.

    Identical to ``securities_global._money`` and deliberately so. A float is
    refused rather than converted because ``Decimal(0.1)`` is
    ``0.1000000000000000055511151231257827021181583404541015625`` and no error
    is raised at any point downstream — the wrong number just gets stored. If a
    caller has a float, the fix is at the source (parse to str/Decimal), not
    here.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise PortfolioError(
            f"{field_name} must be a Decimal, int or str — got "
            f"{type(value).__name__}. Binary floats cannot represent decimal "
            f"money exactly and Decimal(float) silently preserves the error."
        )
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as exc:
            raise PortfolioError(f"{field_name}={value!r} is not numeric") from exc
    raise PortfolioError(
        f"{field_name} must be a Decimal, int or str — got {type(value).__name__}"
    )


def _opt_money(value: Any, field_name: str) -> Decimal | None:
    return None if value is None else _money(value, field_name)


def _require_org(org_id: Any) -> str:
    """Every write takes ``org_id`` explicitly, and it is never optional.

    STANDING RULE: this value comes from JWT claims at the router, NEVER from a
    request body. There is no default here and no fallback to
    ``app.current_org_id`` — a service function that quietly inherited whatever
    org the connection happened to be set to would write the right row into the
    wrong tenant on any code path that forgot to set it.
    """
    if not org_id:
        raise PortfolioError(
            "org_id is required and must come from the caller's JWT claims, "
            "never from a request body"
        )
    return str(org_id)


class _OrgWrite:
    """Transaction + ``SET LOCAL app.current_org_id`` for one org-scoped write.

    Shaped after ``securities_global._SuperAdminWrite``, but it raises org
    context rather than privilege. This is what makes RLS the REAL gate: the
    policy's ``WITH CHECK`` compares the inserted ``org_id`` against this GUC,
    so a mismatch between the ``org_id`` argument and the connection's context
    is refused by the database rather than by a Python ``if``.

    ``SET LOCAL``, not ``SET``, so it cannot outlive the statement it was raised
    for on a pooled backend. If the caller's connection is already inside a
    transaction (every connection from ``services.database``'s pool is), asyncpg
    nests this as a SAVEPOINT and the ``set_config`` applies to the enclosing
    transaction — which is correct: the caller asked for one org-scoped write
    and gets exactly one.

    Deliberately does NOT touch ``app.is_super_admin``. A Super Admin already
    satisfies the second disjunct of every policy here; elevating on their
    behalf would mean this module could not tell the two cases apart, and a bug
    that wrote to the wrong org would pass silently instead of raising.
    """

    __slots__ = ("_conn", "_org_id", "_tr")

    def __init__(self, conn, org_id: str):
        self._conn = conn
        self._org_id = org_id
        self._tr = None

    async def __aenter__(self):
        self._tr = self._conn.transaction()
        await self._tr.start()
        try:
            await self._conn.execute(
                "SELECT set_config('app.current_org_id', $1, true)", self._org_id
            )
        except BaseException:
            await self._tr.rollback()
            raise
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            await self._tr.commit()
        else:
            await self._tr.rollback()
        return False


def normalize_identifier_value(id_type: str, id_value: str) -> str:
    """Fold an identifier value to its stored form.

    Must be applied symmetrically by writes and lookups, exactly as in A1 — a
    lookup that upper-cased one side with ``upper(id_value)`` in SQL could not
    use an index on the column at all.
    """
    value = (id_value or "").strip()
    if not value:
        raise PortfolioError("id_value is empty")
    if id_type in _UPPERCASED_ID_TYPES:
        return value.upper()
    return value


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AssetValue:
    """The resolved current market value of an asset — or an honest absence.

    ``value`` is ``None`` when no valuation qualifies, and ``reason`` says why.
    It is NEVER ``Decimal(0)`` for a missing mark: a zero is indistinguishable
    from a genuine zero position once it has been summed into a rollup, and by
    then the information that it was never measured is gone.
    """

    asset_id: str
    value: Decimal | None
    reason: str | None = None
    valuation_id: str | None = None
    valuation_date: date | None = None
    status: str | None = None
    value_basis: str | None = None
    currency_code: str | None = None
    is_superseded: bool = False

    @property
    def found(self) -> bool:
        return self.value is not None


# ── Assets ──────────────────────────────────────────────────────────────────


async def create_asset(
    conn,
    *,
    org_id: str,
    name: str,
    asset_type: str,
    asset_class: str = "financial",
    ownership_basis: str = UNITS,
    valuation_method: str = MARKET_PRICE,
    short_name: str | None = None,
    global_security_id: str | None = None,
    default_taxonomy_key: str | None = None,
    currency_code: str | None = None,
    issuer_entity_id: str | None = None,
    internal_spv_id: str | None = None,
    inception_date: date | None = None,
    maturity_date: date | None = None,
    include_in_performance: bool = True,
) -> str:
    """Insert a tenant asset. Returns its id.

    Org-scoped, not Super-Admin-gated — this is tenant data. The RLS policy's
    ``WITH CHECK`` is the real gate; :class:`_OrgWrite` supplies the context it
    compares against.

    ``global_security_id`` is nullable on purpose. A listed equity points at the
    A1 security master so a price series and any structured-note terms come for
    free; a rental property, a private LLC interest or a painting has no global
    counterpart and must not be forced to invent one.

    ``asset_type`` is validated for non-emptiness ONLY — see module docstring
    point 3.
    """
    org_id = _require_org(org_id)
    _check_choice(asset_class, ASSET_CLASSES, "asset_class")
    _check_choice(ownership_basis, OWNERSHIP_BASES, "ownership_basis")
    _check_choice(valuation_method, VALUATION_METHODS, "valuation_method")
    if not (name or "").strip():
        raise PortfolioError("name is required")
    if not (asset_type or "").strip():
        raise PortfolioError(
            "asset_type is required (NOT NULL in the schema, and deliberately "
            "unconstrained — it is open text, so an empty string would be "
            "accepted by the database)"
        )

    new_id = str(uuid.uuid4())
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"""
            INSERT INTO {TABLE_ASSETS}
                (id, org_id, global_security_id, name, short_name, asset_class,
                 asset_type, ownership_basis, valuation_method,
                 include_in_performance, default_taxonomy_key, currency_code,
                 issuer_entity_id, internal_spv_id, inception_date, maturity_date)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13::uuid, $14::uuid, $15, $16)
            """,
            new_id, org_id,
            str(global_security_id) if global_security_id else None,
            name.strip(), short_name, asset_class, asset_type.strip(),
            ownership_basis, valuation_method, bool(include_in_performance),
            default_taxonomy_key, currency_code,
            str(issuer_entity_id) if issuer_entity_id else None,
            str(internal_spv_id) if internal_spv_id else None,
            inception_date, maturity_date,
        )
    return new_id


async def add_identifier(
    conn,
    *,
    org_id: str,
    asset_id: str,
    id_type: str,
    id_value: str,
    is_primary: bool = False,
) -> str:
    """Attach an identifier to a tenant asset. Returns the identifier row id.

    ``asset_identifiers`` carries its own ``org_id`` (it is not inferred from
    the asset) because its RLS policy is evaluated on the row itself, not
    through a join. The asset is looked up under the SAME org context first, so
    an identifier can never be attached across a tenant boundary: a foreign
    ``asset_id`` is invisible to the lookup and reports as "does not exist"
    rather than leaking its existence.
    """
    org_id = _require_org(org_id)
    _check_choice(id_type, IDENTIFIER_TYPES, "id_type")
    value = normalize_identifier_value(id_type, id_value)

    async with _OrgWrite(conn, org_id) as c:
        exists = await c.fetchval(
            f"SELECT 1 FROM {TABLE_ASSETS} WHERE id = $1::uuid AND org_id = $2::uuid",
            str(asset_id), org_id,
        )
        if not exists:
            raise PortfolioError(f"asset {asset_id} does not exist in this org")
        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_ASSET_IDENT}
                (asset_id, org_id, id_type, id_value, is_primary)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5)
            RETURNING id::text
            """,
            str(asset_id), org_id, id_type, value, bool(is_primary),
        )


# ── Positions — the edge ────────────────────────────────────────────────────


def _validate_basis(
    ownership_basis: str,
    quantity: Decimal | None,
    ownership_pct: Decimal | None,
    market_value: Decimal | None,
) -> None:
    """Enforce the ownership-basis contract. THE ONLY THING ENFORCING IT.

    ``portfolio.positions`` has no CHECK constraint covering this combination —
    introspected, not assumed (module docstring point 2). There is no database
    backstop, so this function is written out longhand rather than leaning on a
    23514 to catch what it misses.

        units    → quantity REQUIRED, ownership_pct MUST be NULL
        percent  → ownership_pct REQUIRED, quantity MUST be NULL
        value    → market_value REQUIRED, quantity AND ownership_pct MUST be NULL

    ``market_value`` is permitted on all three: it is the valued amount, not the
    basis. What the basis selects is which measure is AUTHORITATIVE — for a
    units position the value is derived from quantity × price and can be
    recomputed; for a value position the value IS the fact and there is nothing
    to derive it from.

    The mutual exclusion is what makes the column mean anything. A row declaring
    ``value`` while carrying a quantity is not a harmless extra field: a rollup
    that trusts ``ownership_basis`` computes from ``market_value``, another that
    sees a populated ``quantity`` computes from quantity × price, and the two
    silently disagree. Refusing the write is the only point at which that is
    still cheap to fix.
    """
    supplied = {
        "quantity": quantity is not None,
        "ownership_pct": ownership_pct is not None,
        "market_value": market_value is not None,
    }
    required, forbidden = {
        UNITS: ("quantity", ("ownership_pct",)),
        PERCENT: ("ownership_pct", ("quantity",)),
        VALUE: ("market_value", ("quantity", "ownership_pct")),
    }[ownership_basis]

    if not supplied[required]:
        raise OwnershipBasisError(
            f"ownership_basis={ownership_basis!r} requires {required}, which "
            f"was not supplied. Supplied: "
            f"{sorted(k for k, v in supplied.items() if v) or 'nothing'}."
        )
    wrong = [f for f in forbidden if supplied[f]]
    if wrong:
        raise OwnershipBasisError(
            f"ownership_basis={ownership_basis!r} authoritatively measures "
            f"{required}, so {', '.join(wrong)} must be NULL — got "
            f"{', '.join(f'{f}=set' for f in wrong)}. A position carrying both "
            f"measures makes two rollups disagree about the same holding."
        )


async def create_position(
    conn,
    *,
    org_id: str,
    owner_entity_id: str,
    asset_id: str,
    as_of_date: date,
    authority: str,
    source_system: str,
    ownership_basis: str | None = None,
    quantity: Decimal | int | str | None = None,
    ownership_pct: Decimal | int | str | None = None,
    market_value: Decimal | int | str | None = None,
    market_value_native: Decimal | int | str | None = None,
    cost_basis: Decimal | int | str | None = None,
    accrued_income: Decimal | int | str | None = None,
    fx_rate_id: str | None = None,
    taxonomy_key: str | None = None,
    is_reconciled: bool = False,
    superseded_by_source: str | None = None,
    account_id: str | None = None,
) -> str:
    """Create a position — the edge between an owner entity and an asset.

    ``owner_entity_id`` may be ANY entity: an ``account`` node, or a trust, an
    LLC or an individual holding the asset directly with no account in between.
    Accounts are optional, not required — nothing here inserts one, defaults to
    one, or checks for one. (They ARE hidden from CRM-facing entity lists; see
    ``schemas.entities.OPERATIONAL_ENTITY_TYPES``. Hidden from the CRM is not
    the same as unusable as an owner, and this is the function that proves it.)

    ``account_id`` (fee32) is the OPTIONAL link to a custodial account in
    ``public.accounts``. It stays NULL for a directly-held asset or an SPV
    interest, and nothing here defaults or backfills it. When it IS supplied it
    is checked against the account's active ``account_owners`` by
    ``portfolio_account_link.validate_position_account`` — an account belonging
    to another org is REFUSED (the FK is org-blind, so this check is the only
    tenant boundary), while an owner mismatch inside this org is written and
    recorded as a reviewable exception rather than raised. See that module for
    why the two outcomes differ.

    ``ownership_basis`` defaults to the ASSET's declared basis when omitted, so
    the common case cannot drift, but an explicit value is accepted: one source
    may carry an LLC interest as a percentage while another states it at value,
    and the position is where that per-source truth lives.

    Every monetary argument goes through :func:`_money`, which refuses ``float``.
    """
    org_id = _require_org(org_id)
    _check_choice(authority, AUTHORITIES, "authority")
    _check_choice(source_system, SOURCE_SYSTEMS, "source_system")
    if not isinstance(as_of_date, date):
        raise PortfolioError(
            f"as_of_date must be a datetime.date — got {type(as_of_date).__name__}"
        )

    qty = _opt_money(quantity, "quantity")
    pct = _opt_money(ownership_pct, "ownership_pct")
    mv = _opt_money(market_value, "market_value")
    mv_native = _opt_money(market_value_native, "market_value_native")
    cost = _opt_money(cost_basis, "cost_basis")
    accrued = _opt_money(accrued_income, "accrued_income")

    new_id = str(uuid.uuid4())
    async with _OrgWrite(conn, org_id) as c:
        asset = await c.fetchrow(
            f"SELECT ownership_basis FROM {TABLE_ASSETS} "
            f"WHERE id = $1::uuid AND org_id = $2::uuid",
            str(asset_id), org_id,
        )
        if asset is None:
            raise PortfolioError(f"asset {asset_id} does not exist in this org")

        basis = ownership_basis or asset["ownership_basis"]
        _check_choice(basis, OWNERSHIP_BASES, "ownership_basis")
        # Validated AFTER the asset lookup so an inherited basis is validated
        # too — a caller who omits the argument entirely is still held to the
        # contract, against whichever basis actually applies.
        _validate_basis(basis, qty, pct, mv)

        owner_exists = await c.fetchval(
            f"SELECT 1 FROM {TABLE_ENTITIES} e "
            f"WHERE e.id = $1::uuid AND e.org_id = $2::uuid AND {_current('e')}",
            str(owner_entity_id), org_id,
        )
        if not owner_exists:
            raise PortfolioError(
                f"owner_entity_id {owner_entity_id} is not a current entity in "
                f"this org"
            )

        await c.execute(
            f"""
            INSERT INTO {TABLE_POSITIONS}
                (id, org_id, owner_entity_id, asset_id, as_of_date,
                 ownership_basis, quantity, ownership_pct, cost_basis,
                 market_value, market_value_native, fx_rate_id, accrued_income,
                 authority, source_system, taxonomy_key, is_reconciled,
                 superseded_by_source, account_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5,
                    $6, $7, $8, $9, $10, $11, $12::uuid, $13,
                    $14, $15, $16, $17, $18, $19::uuid)
            """,
            new_id, org_id, str(owner_entity_id), str(asset_id), as_of_date,
            basis, qty, pct, cost, mv, mv_native,
            str(fx_rate_id) if fx_rate_id else None, accrued,
            authority, source_system, taxonomy_key, bool(is_reconciled),
            superseded_by_source, str(account_id) if account_id else None,
        )

        if account_id:
            # AFTER the insert and inside the same transaction, so the
            # exception can name a real position_id and a rolled-back position
            # cannot leave an orphan exception behind. Imported here rather
            # than at module scope: portfolio_account_link imports _OrgWrite,
            # _current and PortfolioError from THIS module, and a top-level
            # import would be a cycle.
            from services.portfolio_account_link import validate_position_account

            await validate_position_account(
                c, org_id,
                position_id=new_id,
                account_id=str(account_id),
                owner_entity_id=str(owner_entity_id),
                source_system=source_system,
            )
    return new_id


# ── Transactions ────────────────────────────────────────────────────────────


def _asset_market(valuation_method: str) -> str:
    """Which market an asset trades in, derived from how it is valued.

    ``valuation_method`` is the honest signal available on the deployed schema:
    ``market_price`` means a listed price series exists, which is what "public"
    means operationally. ``nav`` / ``appraisal`` / ``mark_to_model`` all mean
    there is no listed market. ``amortized_cost`` is genuinely ambiguous — a
    held-to-maturity public bond and a private note are both carried that way —
    so it declines to have an opinion and reports ``both``.

    ``asset_type`` would be the obvious signal and is NOT used: it has no CHECK
    constraint on the deployed table (module docstring point 3), so its values
    are whatever callers write, and a check keyed to open text would start
    silently passing everything the first time somebody typed "Equity".
    """
    if valuation_method == MARKET_PRICE:
        return PUBLIC_MARKET
    if valuation_method in PRIVATE_VALUATION_METHODS:
        return PRIVATE_MARKET
    return BOTH_MARKETS


async def record_transaction(
    conn,
    *,
    org_id: str,
    position_id: str,
    transaction_type_code: str,
    trade_date: date,
    authority: str,
    source_system: str,
    settle_date: date | None = None,
    quantity: Decimal | int | str | None = None,
    price: Decimal | int | str | None = None,
    gross_amount: Decimal | int | str | None = None,
    fees: Decimal | int | str | None = None,
    taxes: Decimal | int | str | None = None,
    net_amount: Decimal | int | str | None = None,
    currency_code: str | None = None,
    fx_rate_id: str | None = None,
    external_ref: str | None = None,
    related_transaction_id: str | None = None,
    corporate_action_id: str | None = None,
    is_corporate_action_adjustment: bool = False,
) -> str:
    """Record a transaction against a position. Returns its id.

    ``is_corporate_action_adjustment`` (added by Phase F, whose Part 1 SQL added
    the column — A2 shipped before it existed, so this INSERT did not name it and
    every adjustment would have silently stored the column default) is the flag a
    realized-gain calculation filters on. It is deliberately a plain boolean
    parameter rather than something derived from ``corporate_action_id IS NOT
    NULL``: a report must be able to exclude adjustments **without knowing the
    corporate-action machinery exists**, and a derived flag would make the two
    facts impossible to disagree — including in the case where they should, e.g.
    a genuine cash-in-lieu *sale* that cites a corporate action and IS a realized
    gain. See ``services.portfolio_corporate_actions``.

    Two validations beyond the vocabularies:

    **The transaction type must exist and be active.** There is an FK on
    ``transaction_type_code``, but a 23503 names a constraint and not the code
    that was wrong, and the FK cannot see ``is_active`` at all — a retired type
    would still insert cleanly.

    **The type's ``market`` must fit the asset.** A capital call against a listed
    equity, or a buy against a private fund interest, is a mis-mapped feed, and
    the moment it lands the position's basis and its transaction history describe
    two different instruments. The check is deliberately narrow:

    * type market ``both`` or ``NULL``  → always allowed (``NULL`` means the type
      predates the A2 backfill or is an org-specific addition — unclassified, so
      no opinion, rather than a guess);
    * asset market ``both`` (``amortized_cost``) → always allowed;
    * otherwise the two must be equal.

    Three of the sixteen seeded types are ``both`` (``adjustment``,
    ``fee_expense``, ``interest``), which is what keeps this from being a rules
    engine: the genuinely universal types opt out by classification, not by a
    special case in here.
    """
    org_id = _require_org(org_id)
    _check_choice(authority, AUTHORITIES, "authority")
    _check_choice(source_system, SOURCE_SYSTEMS, "source_system")
    if not isinstance(trade_date, date):
        raise PortfolioError(
            f"trade_date must be a datetime.date — got {type(trade_date).__name__}"
        )

    qty = _opt_money(quantity, "quantity")
    px = _opt_money(price, "price")
    gross = _opt_money(gross_amount, "gross_amount")
    fee = _opt_money(fees, "fees")
    tax = _opt_money(taxes, "taxes")
    net = _opt_money(net_amount, "net_amount")

    new_id = str(uuid.uuid4())
    async with _OrgWrite(conn, org_id) as c:
        ttype = await c.fetchrow(
            f"SELECT code, label, market, is_active FROM {TABLE_TXN_TYPES} WHERE code = $1",
            transaction_type_code,
        )
        if ttype is None:
            raise PortfolioError(
                f"transaction_type_code={transaction_type_code!r} does not exist "
                f"in {TABLE_TXN_TYPES}"
            )
        if not ttype["is_active"]:
            raise PortfolioError(
                f"transaction_type_code={transaction_type_code!r} "
                f"({ttype['label']!r}) is retired (is_active=false)"
            )

        pos = await c.fetchrow(
            f"""
            SELECT p.id, a.name AS asset_name, a.valuation_method
            FROM {TABLE_POSITIONS} p
            JOIN {TABLE_ASSETS} a ON a.id = p.asset_id AND a.org_id = p.org_id
            WHERE p.id = $1::uuid AND p.org_id = $2::uuid
            """,
            str(position_id), org_id,
        )
        if pos is None:
            raise PortfolioError(f"position {position_id} does not exist in this org")

        type_market = ttype["market"]
        asset_market = _asset_market(pos["valuation_method"])
        if (
            type_market is not None
            and type_market != BOTH_MARKETS
            and asset_market != BOTH_MARKETS
            and type_market != asset_market
        ):
            raise TransactionMarketError(
                f"transaction type {transaction_type_code!r} ({ttype['label']!r}) "
                f"is a {type_market}-markets type, but the position's asset "
                f"{pos['asset_name']!r} is valued by "
                f"{pos['valuation_method']!r}, which makes it a {asset_market}"
                f"-markets asset. This is almost always a mis-mapped feed."
            )

        await c.execute(
            f"""
            INSERT INTO {TABLE_TRANSACTIONS}
                (id, org_id, position_id, transaction_type_code,
                 corporate_action_id, trade_date, settle_date, quantity, price,
                 gross_amount, fees, taxes, net_amount, currency_code,
                 fx_rate_id, authority, source_system, external_ref,
                 related_transaction_id, is_corporate_action_adjustment)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::uuid, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15::uuid, $16, $17, $18, $19::uuid,
                    $20)
            """,
            new_id, org_id, str(position_id), transaction_type_code,
            str(corporate_action_id) if corporate_action_id else None,
            trade_date, settle_date, qty, px, gross, fee, tax, net,
            currency_code, str(fx_rate_id) if fx_rate_id else None,
            authority, source_system, external_ref,
            str(related_transaction_id) if related_transaction_id else None,
            bool(is_corporate_action_adjustment),
        )
    return new_id


# ── Valuations ──────────────────────────────────────────────────────────────


async def record_valuation(
    conn,
    *,
    org_id: str,
    asset_id: str,
    valuation_date: date,
    value: Decimal | int | str,
    value_basis: str = TOTAL,
    status: str = "final",
    purpose: str = "market",
    currency_code: str | None = None,
    valuation_method: str | None = None,
    valuation_source: str | None = None,
    supersedes_valuation_id: str | None = None,
) -> str:
    """Insert a valuation. Returns its id. **Never updates the prior row.**

    ─────────────────────────────────────────────────────────────────────────
    WHY SUPERSESSION IS AN INSERT AND NOT AN UPDATE
    ─────────────────────────────────────────────────────────────────────────
    ``supersedes_valuation_id`` is a FORWARD pointer living on the NEW row.
    When it is supplied, this function verifies the target exists in the same
    org and on the same asset — and then leaves it completely alone. No
    ``valid_to``, no status change, no flag. The prior row's every column is
    byte-identical after the restatement.

    The obvious alternative — close the old row, mark it superseded, write the
    new one — destroys exactly the thing a restatement exists to record. "The
    Q2 mark was 4.2M and we later restated it to 3.8M" is a two-row fact. Once
    the first row has been edited to say it was never really the answer, the
    only surviving statement is the new number, and the question a restatement
    is always asked in service of — *what did we report at the time, and what
    changed* — has no answer.

    It is also not a CLAUDE.md Rule 3 supersede. Rule 3 closes a row because the
    old value stopped being TRUE. A superseded valuation never stopped being
    true: it remains, permanently, the number that was struck on that date by
    that source. What changed is which number is CURRENT, and that is a
    resolution question, answered on read by :func:`resolve_current_value` —
    which demotes superseded rows below every status rather than hiding them.

    Both rows stay independently queryable forever. That is the point.
    """
    org_id = _require_org(org_id)
    _check_choice(value_basis, VALUE_BASES, "value_basis")
    _check_choice(status, VALUATION_STATUSES, "status")
    _check_choice(purpose, VALUATION_PURPOSES, "purpose")
    if valuation_method is not None:
        _check_choice(valuation_method, VALUATION_METHODS, "valuation_method")
    if not isinstance(valuation_date, date):
        raise PortfolioError(
            f"valuation_date must be a datetime.date — got "
            f"{type(valuation_date).__name__}"
        )
    amount = _money(value, "value")

    new_id = str(uuid.uuid4())
    async with _OrgWrite(conn, org_id) as c:
        asset_exists = await c.fetchval(
            f"SELECT 1 FROM {TABLE_ASSETS} WHERE id = $1::uuid AND org_id = $2::uuid",
            str(asset_id), org_id,
        )
        if not asset_exists:
            raise PortfolioError(f"asset {asset_id} does not exist in this org")

        if supersedes_valuation_id is not None:
            prior = await c.fetchrow(
                f"SELECT asset_id::text AS asset_id FROM {TABLE_VALUATIONS} "
                f"WHERE id = $1::uuid AND org_id = $2::uuid",
                str(supersedes_valuation_id), org_id,
            )
            if prior is None:
                raise PortfolioError(
                    f"supersedes_valuation_id {supersedes_valuation_id} does not "
                    f"exist in this org"
                )
            if prior["asset_id"] != str(asset_id):
                raise PortfolioError(
                    f"supersedes_valuation_id {supersedes_valuation_id} belongs to "
                    f"asset {prior['asset_id']}, not {asset_id}. A valuation can "
                    f"only supersede another valuation of the SAME asset."
                )
            if str(supersedes_valuation_id) == new_id:  # pragma: no cover
                raise PortfolioError("a valuation cannot supersede itself")

        await c.execute(
            f"""
            INSERT INTO {TABLE_VALUATIONS}
                (id, org_id, asset_id, valuation_date, value, value_basis,
                 currency_code, purpose, status, valuation_method,
                 valuation_source, supersedes_valuation_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10,
                    $11, $12::uuid)
            """,
            new_id, org_id, str(asset_id), valuation_date, amount, value_basis,
            currency_code, purpose, status, valuation_method, valuation_source,
            str(supersedes_valuation_id) if supersedes_valuation_id else None,
        )
        # Deliberately NOTHING here touching supersedes_valuation_id's row.
        # If a future sprint adds an UPDATE below this line, the A2 verification
        # fails: it re-reads every column of the prior row and compares it to a
        # snapshot taken before this call.
    return new_id


async def resolve_current_value(
    conn,
    *,
    org_id: str,
    asset_id: str,
    as_of: date | None = None,
    purpose: str = "market",
    quantity: Decimal | int | str | None = None,
) -> AssetValue:
    """Resolve an asset's CURRENT market value. Returns :class:`AssetValue`.

    ─────────────────────────────────────────────────────────────────────────
    THE LADDER
    ─────────────────────────────────────────────────────────────────────────
    Among current rows (``valid_to``/``system_to`` NULL) for this asset, this
    purpose, and ``valuation_date <= as_of`` when ``as_of`` is given:

      1. latest ``valuation_date`` wins;
      2. within a date, ``audited > final > preliminary > estimated``;
      3. **any row that another current valuation supersedes is demoted below
         all four**, whatever its own status says;
      4. ties break on ``system_from`` — the most recently recorded.

    Step 3 is what makes supersession do work without an in-place update. The
    prior row is untouched by :func:`record_valuation`, so a rule keyed only to
    ``status`` would keep returning a restated-away ``audited`` figure forever.
    Demotion rather than exclusion, because a superseded row is still better
    than nothing: if it is the only mark that exists, it is returned, flagged
    ``is_superseded=True``.

    ─────────────────────────────────────────────────────────────────────────
    WHAT IT RETURNS WHEN THERE IS NOTHING
    ─────────────────────────────────────────────────────────────────────────
    ``AssetValue(value=None, reason=...)``. NEVER ``Decimal(0)``. A zero for "we
    have no mark" is indistinguishable from a genuine zero position the instant
    it is summed into a rollup, and the fact that it was never measured is gone
    for good. Every no-value path names which of the three reasons applies: the
    asset does not exist, no valuation matched, or the best row is ``per_unit``
    and no quantity was supplied to multiply it by.

    That last one is a real case, not a technicality: a fund NAV per share is a
    perfectly valid valuation and is not a market value on its own. Returning
    the per-unit figure as if it were the position's worth understates a
    thousand-unit holding by three orders of magnitude, and nothing raises.
    """
    org_id = _require_org(org_id)
    _check_choice(purpose, VALUATION_PURPOSES, "purpose")
    asset_id = str(asset_id)

    asset_exists = await conn.fetchval(
        f"SELECT 1 FROM {TABLE_ASSETS} WHERE id = $1::uuid AND org_id = $2::uuid",
        asset_id, org_id,
    )
    if not asset_exists:
        return AssetValue(
            asset_id=asset_id, value=None,
            reason=f"asset {asset_id} does not exist in org {org_id}",
        )

    rows = await conn.fetch(
        f"""
        SELECT v.id::text AS id,
               v.valuation_date,
               v.value,
               v.value_basis,
               v.status,
               v.currency_code,
               v.system_from,
               EXISTS (
                   SELECT 1 FROM {TABLE_VALUATIONS} s
                   WHERE s.supersedes_valuation_id = v.id
                     AND s.org_id = v.org_id
                     AND {_current('s')}
               ) AS is_superseded
        FROM {TABLE_VALUATIONS} v
        WHERE v.org_id = $1::uuid
          AND v.asset_id = $2::uuid
          AND v.purpose = $3
          AND ($4::date IS NULL OR v.valuation_date <= $4::date)
          AND {_current('v')}
        """,
        org_id, asset_id, purpose, as_of,
    )
    if not rows:
        window = f" on or before {as_of.isoformat()}" if as_of else ""
        return AssetValue(
            asset_id=asset_id, value=None,
            reason=(
                f"no current {purpose!r} valuation exists for asset {asset_id}"
                f"{window} — this is an ABSENCE of data, not a value of zero"
            ),
        )

    def _rank(r):
        priority = (
            _SUPERSEDED_PRIORITY if r["is_superseded"]
            else _STATUS_PRIORITY.get(r["status"], _SUPERSEDED_PRIORITY)
        )
        # Negate the dates so a single ascending sort means "latest date, then
        # best status". date/datetime are not negatable, so sort descending on
        # them by using a reverse-ordered key via ordinal arithmetic.
        return (-r["valuation_date"].toordinal(), priority,
                -r["system_from"].timestamp())

    best = min(rows, key=_rank)

    if best["value_basis"] == PER_UNIT:
        if quantity is None:
            return AssetValue(
                asset_id=asset_id, value=None,
                reason=(
                    f"the governing valuation ({best['id']}, "
                    f"{best['valuation_date'].isoformat()}, status "
                    f"{best['status']!r}) is value_basis={PER_UNIT!r} — a "
                    f"per-unit mark is not a market value on its own. Supply "
                    f"quantity= to resolve it, or ask for a {TOTAL!r} valuation."
                ),
                valuation_id=best["id"],
                valuation_date=best["valuation_date"],
                status=best["status"],
                value_basis=best["value_basis"],
                currency_code=best["currency_code"],
                is_superseded=best["is_superseded"],
            )
        amount = best["value"] * _money(quantity, "quantity")
    else:
        amount = best["value"]

    return AssetValue(
        asset_id=asset_id,
        value=amount,
        reason=None,
        valuation_id=best["id"],
        valuation_date=best["valuation_date"],
        status=best["status"],
        value_basis=best["value_basis"],
        currency_code=best["currency_code"],
        is_superseded=best["is_superseded"],
    )


# ── External references (ingestion idempotency key) ─────────────────────────


async def upsert_external_reference(
    conn,
    *,
    org_id: str,
    source_system: str,
    external_id: str,
    record_type: str,
    record_id: str,
) -> str:
    """Map an upstream system's id onto one of our records. Returns the row id.

    THE A2 DEFECT IS FIXED — and this function had to change with it. A2 shipped
    against a UNIQUE of ``(source_system, external_id, record_type)`` with no
    ``org_id``: two tenants ingesting from the same source system with colliding
    external ids hard-conflicted, and the loser got a unique violation on a row
    RLS would not let it see. That constraint was replaced ahead of Phase B by
    ``external_references_org_source_ext_type_key``, UNIQUE on
    ``(org_id, source_system, external_id, record_type)``.

    The ``ON CONFLICT`` target below is not cosmetic. Postgres matches an
    inference clause against a real unique index, so the old three-column target
    now matches NOTHING and raises ``InvalidColumnReferenceError`` on every
    call — which would have made Phase B's whole idempotency story fail closed
    the first time an import re-ran. It is re-pointed at the constraint as it
    actually exists, re-introspected from the live database, not assumed.

    The cross-org guard A2 needed is gone because the constraint now does that
    job: ``org_id`` is part of the key, so two tenants can hold the same
    ``(source_system, external_id, record_type)`` independently and neither can
    conflict with, or re-point, the other's row.
    """
    org_id = _require_org(org_id)
    _check_choice(record_type, EXT_REF_RECORD_TYPES, "record_type")
    if not (source_system or "").strip():
        raise PortfolioError("source_system is required")
    if not (external_id or "").strip():
        raise PortfolioError("external_id is required")

    async with _OrgWrite(conn, org_id) as c:
        row_id = await c.fetchval(
            f"""
            INSERT INTO {TABLE_EXT_REF}
                (org_id, source_system, external_id, record_type, record_id)
            VALUES ($1::uuid, $2, $3, $4, $5::uuid)
            ON CONFLICT (org_id, source_system, external_id, record_type)
            DO UPDATE SET record_id = EXCLUDED.record_id, last_seen = now()
            RETURNING id::text
            """,
            org_id, source_system.strip(), external_id.strip(), record_type,
            str(record_id),
        )
        return row_id


async def find_external_reference(
    conn,
    *,
    org_id: str,
    source_system: str,
    external_id: str,
    record_type: str,
) -> str | None:
    """Return the ``record_id`` an upstream id already maps to, or ``None``.

    The read half of the idempotency key, and the reason a re-import is a no-op
    rather than a second row. :func:`upsert_external_reference` alone is not
    enough: it makes the MAPPING idempotent, but a caller that inserts a
    position first and re-points the mapping afterwards has already written the
    duplicate. The check has to happen BEFORE the insert, which needs a read.

    ``org_id`` is part of the lookup as well as the RLS context, deliberately
    belt-and-braces: the lookup must not depend on the GUC having been set
    correctly, because the failure mode if it were not is a cross-tenant read.
    """
    org_id = _require_org(org_id)
    _check_choice(record_type, EXT_REF_RECORD_TYPES, "record_type")
    async with _OrgWrite(conn, org_id) as c:
        return await c.fetchval(
            f"""
            SELECT record_id::text FROM {TABLE_EXT_REF}
            WHERE org_id = $1::uuid AND source_system = $2
              AND external_id = $3 AND record_type = $4
            """,
            org_id, (source_system or "").strip(), (external_id or "").strip(),
            record_type,
        )
