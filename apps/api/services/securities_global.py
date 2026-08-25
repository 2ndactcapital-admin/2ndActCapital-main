"""The global security master — reads for everyone, writes for Super Admin only.

WHAT THIS LAYER IS
──────────────────────────────────────────────────────────────────────────────
``portfolio.securities_global`` and its three satellites are the ONE table set
in this codebase with no ``org_id``. A CUSIP means the same thing to every
tenant, so the row is shared and the RLS shape is inverted from everything in
``public``: read is unconditional (``USING true``), write requires
``app.is_super_admin``. This module is the only sanctioned way in.

FOUR THINGS MEASURED AGAINST THE DEPLOYED DATABASE, NOT ASSUMED
──────────────────────────────────────────────────────────────────────────────
1. **Every table name here is schema-qualified, always.** ``app_service`` has no
   ``pg_db_role_setting`` row, so its ``search_path`` is ``"$user", public`` —
   ``portfolio`` is NOT on it. An unqualified ``FROM securities_global`` raises
   ``UndefinedTableError`` under the production role while working fine under a
   psql session that happened to ``SET search_path``. That is the worst kind of
   bug: invisible in development, total in production. Hence ``TABLE_*``
   constants and no bare table names anywhere below.

2. **The RLS GUCs are connection-level, not schema-scoped.**
   ``services.database`` emits ``set_config('app.is_super_admin', ..., true)``
   — i.e. ``SET LOCAL`` — as the first statement of every transaction. A
   ``SET LOCAL`` GUC has no schema binding, which is precisely why policies in a
   non-``public`` schema can read it. Nothing extra is needed to make RLS work
   over here.

3. **``canonical_id`` is the read path; ``merged_into_id`` is the audit trail.**
   :func:`get_by_identifier` resolves a merged security in ONE join. It does not
   recurse and it does not loop. At corpus scale — tens of thousands of notes,
   each with an identifier — walking ``merged_into_id`` row by row is an N+1
   that turns a lookup into a table scan.

4. **``canonical_id`` was NULL on all 67 pre-existing corpus rows.** The column
   shipped in the Part 1 SQL but nothing ever populated it. So every read here
   uses ``COALESCE(canonical_id, id)`` — still one join, still no walk — and
   :func:`backfill_canonical_ids` exists to close the gap for good. A read path
   that trusted a bare ``canonical_id`` would have silently returned zero rows
   for the entire live corpus.

WHY ``add_price`` REFUSES STRUCTURED NOTES
──────────────────────────────────────────────────────────────────────────────
See :func:`add_price`. Short version: the obvious loop is wrong here, and it is
wrong in a way that looks like it worked.

BITEMPORALITY, AND THE ONE COLUMN EXEMPT FROM IT
──────────────────────────────────────────────────────────────────────────────
``valid_from``/``valid_to``/``system_from``/``system_to`` match
``entity_relationships``. Business facts are superseded by closing the old row
and inserting a new one (Rule 3), never updated in place.

``canonical_id`` is the deliberate exception, and it has to be. It is not a
business fact — it is a *materialized derivation* of ``merged_into_id``, kept
only so reads stay O(1). Superseding a row to change it would mint a new ``id``
and orphan every foreign key pointing at the old one. It is therefore updated
in place, by :func:`merge_securities` and :func:`backfill_canonical_ids` and
nowhere else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

# ── Schema-qualified table names. See point 1 in the module docstring. ───────
TABLE_SEC = "portfolio.securities_global"
TABLE_IDENT = "portfolio.securities_global_identifiers"
TABLE_PRICE = "portfolio.securities_global_prices"
TABLE_REL = "portfolio.securities_global_relationships"

# ── Vocabularies, mirrored verbatim from the deployed CHECK constraints ──────
# Duplicated in Python on purpose: a CHECK violation surfaces as a 23514 naming
# a constraint, which tells a caller nothing about what it should have passed.
# These give the real error. They must stay in sync with the constraints named
# in each comment.

# securities_global_type_chk
SECURITY_TYPES = frozenset({
    "equity", "fixed_income", "fund", "private_fund", "structured_note",
    "index", "real_asset", "cash", "other",
})

# securities_global_price_coverage_chk
PRICE_COVERAGES = frozenset({"has_series", "no_public_source", "unknown"})
HAS_SERIES = "has_series"

# sec_global_ident_type_chk
IDENTIFIER_TYPES = frozenset({
    "cusip", "isin", "ticker", "sedol", "figi", "lei", "internal",
})

# sec_global_price_type_chk
PRICE_TYPES = frozenset({"close", "intraday_indicative", "model"})

# sec_global_rel_state_chk / sec_global_rel_type_chk
RESOLVED = "resolved"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"
LINK_STATES = frozenset({RESOLVED, UNRESOLVED, AMBIGUOUS})
RELATIONSHIP_TYPES = frozenset({"underlying_of", "constituent_of", "successor_of"})

STRUCTURED_NOTE = "structured_note"

# Identifier types whose values are case-insensitive by convention and stored
# upper-cased. 'internal' is excluded — an internal key is whatever its minter
# says it is, and folding its case would collide two distinct keys.
_UPPERCASED_ID_TYPES = frozenset({"cusip", "isin", "ticker", "sedol", "figi", "lei"})

# The "this row is the current truth" predicate, written once. Both temporal
# axes, because a row can be superseded (valid_to) or corrected (system_to).
#
# Alias-qualified on BOTH columns, always. Written as a bare
# "valid_to IS NULL AND system_to IS NULL" and interpolated after an alias
# prefix, only the first column gets qualified and the second is ambiguous the
# moment a second temporal table joins in — which is every query in this module.
def _current(alias: str) -> str:
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


class SecuritiesGlobalError(ValueError):
    """A write was refused for a reason the caller can fix."""


class SecuritiesGlobalPermissionError(PermissionError):
    """A write was attempted without Super Admin context."""


class StructuredNotePricingError(SecuritiesGlobalError):
    """A price was aimed at a structured note. See :func:`add_price`.

    Its own class, not a bare ValueError, so an ingestion pipeline can decide
    to skip-and-count these while still failing hard on everything else — the
    one legitimate reason to catch this rather than crash.
    """


# ── Internal guards ─────────────────────────────────────────────────────────


def _require_super_admin(is_super_admin: bool, action: str) -> None:
    """App-layer gate. The RLS policy is the REAL gate — this one just makes the
    refusal legible.

    Both exist for a reason. Without the policy this is a promise; without this
    the caller gets ``new row violates row-level security policy``, which does
    not say which privilege was missing or why anyone thought it was needed.
    """
    if not is_super_admin:
        raise SecuritiesGlobalPermissionError(
            f"{action} writes to the global security master, which requires "
            f"Super Admin. The global tables are shared across every tenant; "
            f"there is no org-scoped write path to fall back to."
        )


class _SuperAdminWrite:
    """Transaction + ``SET LOCAL app.is_super_admin='true'`` for one write.

    Elevation happens ONLY after :func:`_require_super_admin` has already
    accepted the caller's claim, and it is transaction-local, so it cannot
    outlive the statement it was raised for — including on a pooled backend,
    which is the whole point of ``SET LOCAL`` over ``SET``.

    If the caller's connection is already inside a transaction (every connection
    from ``services.database``'s pool is), asyncpg nests this as a SAVEPOINT and
    the ``set_config`` applies to the enclosing transaction. That is correct:
    the caller asked for an elevated write and gets exactly one.
    """

    __slots__ = ("_conn", "_tr")

    def __init__(self, conn):
        self._conn = conn
        self._tr = None

    async def __aenter__(self):
        self._tr = self._conn.transaction()
        await self._tr.start()
        try:
            await self._conn.execute(
                "SELECT set_config('app.is_super_admin', 'true', true)"
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


def _check_choice(value: str, allowed: frozenset[str], field_name: str) -> str:
    if value not in allowed:
        raise SecuritiesGlobalError(
            f"{field_name}={value!r} is not one of {sorted(allowed)}"
        )
    return value


def _money(value: Any, field_name: str) -> Decimal:
    """Coerce to Decimal, refusing float.

    A float is refused rather than converted because ``Decimal(0.1)`` is
    ``0.1000000000000000055511151231257827021181583404541015625`` and no error
    is raised at any point downstream — the wrong number just gets stored. If a
    caller has a float, the fix is at the source (parse to str/Decimal), not
    here.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise SecuritiesGlobalError(
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
            raise SecuritiesGlobalError(f"{field_name}={value!r} is not numeric") from exc
    raise SecuritiesGlobalError(
        f"{field_name} must be a Decimal, int or str — got {type(value).__name__}"
    )


def normalize_identifier_value(id_type: str, id_value: str) -> str:
    """Fold an identifier value to its stored form.

    Applied symmetrically by :func:`add_identifier` and
    :func:`get_by_identifier`, which is the only thing that makes
    ``idx_sec_global_ident_lookup`` usable — a lookup that upper-cased one side
    with ``upper(id_value)`` in SQL would not be able to use the index at all.
    """
    value = (id_value or "").strip()
    if not value:
        raise SecuritiesGlobalError("id_value is empty")
    if id_type in _UPPERCASED_ID_TYPES:
        return value.upper()
    return value


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoreabilityGap:
    """One specific reason a security is not scoreable.

    Carries ``raw_underlying_text`` because that string — the verbatim
    prospectus wording — is the only handle a reviewer has on an edge that was
    never resolved. There is no target name to show; there is no target.
    """

    relationship_id: str
    raw_underlying_text: str
    link_state: str
    gap: str  # 'unresolved' | 'no_price_series'
    target_global_security_id: str | None = None
    target_name: str | None = None
    target_price_coverage: str | None = None

    def describe(self) -> str:
        if self.gap == "unresolved":
            return (
                f"underlying {self.raw_underlying_text!r} is not resolved "
                f"(link_state={self.link_state!r})"
            )
        return (
            f"underlying {self.raw_underlying_text!r} resolves to "
            f"{self.target_name!r} which has "
            f"price_coverage={self.target_price_coverage!r}, not {HAS_SERIES!r}"
        )


@dataclass(frozen=True)
class Scoreability:
    """Derived, never stored — see :func:`resolve_scoreability`."""

    global_security_id: str
    canonical_id: str
    scoreable: bool
    relationship_count: int
    reason: str | None = None
    gaps: list[ScoreabilityGap] = field(default_factory=list)


# ── Canonical-id maintenance (the helper that keeps reads cheap) ─────────────


async def canonical_id_for(conn, global_security_id: str) -> str | None:
    """Return the surviving id for ``global_security_id``, or None if unknown.

    ``COALESCE(canonical_id, id)`` rather than a bare ``canonical_id`` because
    the column was NULL on every pre-existing corpus row (see module docstring
    point 4). Still one row read; still no walk.
    """
    return await conn.fetchval(
        f"SELECT COALESCE(canonical_id, id)::text FROM {TABLE_SEC} WHERE id = $1::uuid",
        str(global_security_id),
    )


async def merge_securities(
    conn,
    *,
    source_id: str,
    target_id: str,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    """Merge ``source_id`` into ``target_id`` and materialize the result.

    THIS is the helper that keeps :func:`get_by_identifier` a single join. Three
    writes, in one transaction:

    1. ``source.merged_into_id`` — the audit trail: what happened, once.
    2. ``source.canonical_id`` — the read path: where to go now.
    3. Every OTHER row already pointing at ``source`` as its canonical gets
       re-pointed at the target.

    Step 3 is the one that is easy to leave out and the one that matters. Merge
    C into B, then B into A: without it, C still says "canonical = B" and a
    reader lands on a merged-away row. Chains are collapsed at write time, so
    the read side never has to know chains exist.

    The target is itself canonicalized first, so merging into an
    already-merged-away security lands on the survivor rather than creating the
    two-hop chain this function exists to prevent.
    """
    _require_super_admin(is_super_admin, "merge_securities")
    source_id, target_id = str(source_id), str(target_id)
    if source_id == target_id:
        raise SecuritiesGlobalError("cannot merge a security into itself")

    async with _SuperAdminWrite(conn):
        target_canonical = await canonical_id_for(conn, target_id)
        if target_canonical is None:
            raise SecuritiesGlobalError(f"target security {target_id} does not exist")
        if target_canonical == source_id:
            raise SecuritiesGlobalError(
                f"refusing to merge {source_id} into {target_id}: the target "
                f"already canonicalizes to the source, which would create a cycle"
            )
        if await canonical_id_for(conn, source_id) is None:
            raise SecuritiesGlobalError(f"source security {source_id} does not exist")

        await conn.execute(
            f"UPDATE {TABLE_SEC} SET merged_into_id = $2::uuid, canonical_id = $2::uuid "
            f"WHERE id = $1::uuid",
            source_id, target_canonical,
        )
        # Chain collapse. Anything that used to canonicalize to the source now
        # canonicalizes to the survivor, in one statement, no recursion.
        repointed = await conn.execute(
            f"UPDATE {TABLE_SEC} SET canonical_id = $2::uuid "
            f"WHERE canonical_id = $1::uuid AND id <> $1::uuid",
            source_id, target_canonical,
        )

    return {
        "source_id": source_id,
        "target_id": target_id,
        "canonical_id": target_canonical,
        "chain_rows_repointed": int(repointed.rsplit(" ", 1)[-1]) if repointed else 0,
    }


async def backfill_canonical_ids(conn, *, is_super_admin: bool = False) -> int:
    """Materialize ``canonical_id`` wherever it is NULL. Returns rows written.

    Needed because the Part 1 SQL added the column without populating it: all 67
    live corpus rows carried NULL. Idempotent, and safe to run at any time — the
    invariant it writes (``canonical_id = COALESCE(merged_into_id, id)``) is the
    same one :func:`merge_securities` maintains going forward.

    In-place UPDATE, not a bitemporal supersede, for the reason in the module
    docstring: minting a new ``id`` to change a materialized derivation would
    orphan every FK pointing at the old one.
    """
    _require_super_admin(is_super_admin, "backfill_canonical_ids")
    async with _SuperAdminWrite(conn):
        status = await conn.execute(
            f"UPDATE {TABLE_SEC} SET canonical_id = COALESCE(merged_into_id, id) "
            f"WHERE canonical_id IS NULL"
        )
    return int(status.rsplit(" ", 1)[-1]) if status else 0


# ── Reads ───────────────────────────────────────────────────────────────────


async def get_by_identifier(conn, id_type: str, id_value: str) -> dict[str, Any] | None:
    """Resolve an identifier to the SURVIVING security, following any merge.

    One statement. Two joins, both on a primary key. No recursion, no loop, no
    second round trip — the merge chain was already collapsed at write time by
    :func:`merge_securities`, so following it is a join and not a traversal.

    The alternative — fetch the row, check ``merged_into_id``, fetch again,
    repeat — is an N+1 that is invisible on a fixture of three securities and
    ruinous on a corpus of tens of thousands. It is also unbounded: nothing in
    the schema stops a merge chain from being ten deep.

    Returns the canonical security's columns plus:
      ``matched_global_security_id`` — what the identifier literally points at
      ``was_merged``               — True when those two differ
    so a caller can tell "you asked for B and got A" without a second query.
    Returns None if no current identifier matches.
    """
    _check_choice(id_type, IDENTIFIER_TYPES, "id_type")
    value = normalize_identifier_value(id_type, id_value)

    row = await conn.fetchrow(
        f"""
        SELECT canonical.id::text            AS id,
               canonical.name,
               canonical.short_name,
               canonical.security_type,
               canonical.currency_code,
               canonical.price_coverage,
               canonical.merged_into_id::text AS merged_into_id,
               canonical.canonical_id::text   AS canonical_id,
               matched.id::text               AS matched_global_security_id,
               (matched.id <> canonical.id)   AS was_merged
        FROM {TABLE_IDENT} i
        JOIN {TABLE_SEC} matched
          ON matched.id = i.global_security_id
        JOIN {TABLE_SEC} canonical
          ON canonical.id = COALESCE(matched.canonical_id, matched.id)
        WHERE i.id_type = $1
          AND i.id_value = $2
          AND {_current("i")}
        """,
        id_type, value,
    )
    return dict(row) if row else None


async def resolve_scoreability(conn, global_security_id: str) -> Scoreability:
    """Can this security be scored? Derived on read. NEVER stored.

    Scoreable means: it has at least one current relationship, every one of them
    is ``link_state='resolved'``, and every target carries
    ``price_coverage='has_series'``.

    Derived rather than stored because every input moves independently. An
    underlying gets resolved; a price series is found for an index that had
    none; a target is merged into one with better coverage. A stored flag would
    have to be invalidated by all three, and the failure mode of a stale
    scoreability flag is a note that silently stops being scored — or worse,
    silently starts.

    The return names the SPECIFIC blocker. A bare ``False`` turns into "we
    couldn't score 4,000 notes" with nothing to act on; ``gaps`` turns it into a
    list of underlyings somebody can go resolve. Zero relationships is likewise
    reported as an honest gap, not silently treated as vacuously true — a note
    with no recorded underlyings is not a note we can score, it is a note we
    have not finished reading.
    """
    canonical = await canonical_id_for(conn, global_security_id)
    if canonical is None:
        raise SecuritiesGlobalError(
            f"security {global_security_id} does not exist"
        )

    rows = await conn.fetch(
        f"""
        SELECT r.id::text                     AS relationship_id,
               r.raw_underlying_text,
               r.link_state,
               target.id::text                AS target_id,
               target.name                    AS target_name,
               target.price_coverage          AS target_price_coverage
        FROM {TABLE_REL} r
        LEFT JOIN {TABLE_SEC} matched
               ON matched.id = r.to_global_security_id
        LEFT JOIN {TABLE_SEC} target
               ON target.id = COALESCE(matched.canonical_id, matched.id)
        WHERE r.from_global_security_id IN (
                  -- Edges recorded against a security that has since been
                  -- merged away still belong to the survivor.
                  SELECT s.id FROM {TABLE_SEC} s
                  WHERE COALESCE(s.canonical_id, s.id) = $1::uuid
              )
          AND {_current("r")}
        ORDER BY r.raw_underlying_text
        """,
        canonical,
    )

    gaps: list[ScoreabilityGap] = []
    for r in rows:
        if r["link_state"] != RESOLVED or r["target_id"] is None:
            gaps.append(ScoreabilityGap(
                relationship_id=r["relationship_id"],
                raw_underlying_text=r["raw_underlying_text"],
                link_state=r["link_state"],
                gap="unresolved",
            ))
        elif r["target_price_coverage"] != HAS_SERIES:
            gaps.append(ScoreabilityGap(
                relationship_id=r["relationship_id"],
                raw_underlying_text=r["raw_underlying_text"],
                link_state=r["link_state"],
                gap="no_price_series",
                target_global_security_id=r["target_id"],
                target_name=r["target_name"],
                target_price_coverage=r["target_price_coverage"],
            ))

    if not rows:
        return Scoreability(
            global_security_id=str(global_security_id),
            canonical_id=canonical,
            scoreable=False,
            relationship_count=0,
            reason=(
                "no current underlying relationships are recorded for this "
                "security — nothing to score against"
            ),
        )

    if gaps:
        return Scoreability(
            global_security_id=str(global_security_id),
            canonical_id=canonical,
            scoreable=False,
            relationship_count=len(rows),
            reason="; ".join(g.describe() for g in gaps),
            gaps=gaps,
        )

    return Scoreability(
        global_security_id=str(global_security_id),
        canonical_id=canonical,
        scoreable=True,
        relationship_count=len(rows),
    )


# ── Writes (Super Admin only) ───────────────────────────────────────────────


async def create_security(
    conn,
    *,
    name: str,
    security_type: str,
    short_name: str | None = None,
    currency_code: str | None = None,
    price_coverage: str = "unknown",
    is_super_admin: bool = False,
) -> str:
    """Insert a global security. Returns its id.

    The id is minted here rather than left to the column DEFAULT
    (``extensions.uuid_generate_v4()``) so that
    ``canonical_id`` can be set to it in the SAME statement. A security that is
    canonical from the instant it exists has no window in which a concurrent
    reader sees ``canonical_id IS NULL`` — which is exactly the state that made
    the existing 67 corpus rows unresolvable.
    """
    _require_super_admin(is_super_admin, "create_security")
    _check_choice(security_type, SECURITY_TYPES, "security_type")
    _check_choice(price_coverage, PRICE_COVERAGES, "price_coverage")
    if not (name or "").strip():
        raise SecuritiesGlobalError("name is required")

    new_id = str(uuid.uuid4())
    async with _SuperAdminWrite(conn):
        await conn.execute(
            f"""
            INSERT INTO {TABLE_SEC}
                (id, canonical_id, name, short_name, security_type,
                 currency_code, price_coverage)
            VALUES ($1::uuid, $1::uuid, $2, $3, $4, $5, $6)
            """,
            new_id, name.strip(), short_name, security_type,
            currency_code, price_coverage,
        )
    return new_id


async def add_identifier(
    conn,
    *,
    global_security_id: str,
    id_type: str,
    id_value: str,
    is_primary: bool = False,
    is_super_admin: bool = False,
) -> str:
    """Attach an identifier to a security. Returns the identifier row id.

    The identifier is attached to the security as given, NOT to its canonical.
    That is deliberate: after a merge, "this CUSIP was issued against B" stays
    true, and :func:`get_by_identifier` forwards the read to A anyway. Rewriting
    the identifier's owner would destroy the only record of which duplicate the
    market actually used.
    """
    _require_super_admin(is_super_admin, "add_identifier")
    _check_choice(id_type, IDENTIFIER_TYPES, "id_type")
    value = normalize_identifier_value(id_type, id_value)

    async with _SuperAdminWrite(conn) as c:
        if await canonical_id_for(c, global_security_id) is None:
            raise SecuritiesGlobalError(
                f"security {global_security_id} does not exist"
            )
        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_IDENT}
                (global_security_id, id_type, id_value, is_primary)
            VALUES ($1::uuid, $2, $3, $4)
            RETURNING id::text
            """,
            str(global_security_id), id_type, value, bool(is_primary),
        )


async def add_price(
    conn,
    *,
    global_security_id: str,
    price_date: date,
    price: Decimal | int | str,
    currency_code: str | None = None,
    price_type: str = "close",
    source: str | None = None,
    is_super_admin: bool = False,
) -> str:
    """Write one price point. **Refuses structured notes. This is a hard rule.**

    ─────────────────────────────────────────────────────────────────────────
    WHY THE REFUSAL IS IN CODE AND NOT IN A COMMENT
    ─────────────────────────────────────────────────────────────────────────
    ``securities_global`` holds two kinds of thing: the notes themselves, and
    the indices and equities they reference. Only the second kind has a daily
    price series. A structured note's "price" is a sporadic TRACE print — a
    handful of dealer marks over a multi-year life, at wide spreads, often
    months apart. It is not a series, it does not support a return
    calculation, and interpolating it produces numbers that look like
    performance and are not.

    The natural implementation of a price loader is ``for security in
    securities: fetch_prices(security)``. Over this corpus that is roughly 54
    notes to 13 indices today and will be thousands-to-hundreds later. It would
    not crash. It would not log anything alarming. It would quietly write
    ~250k rows that are individually plausible and collectively meaningless,
    and every downstream scoring run would then be computing against them.

    A convention ("we only price underlyings") cannot stop that, because the
    person writing the loader six months from now will not have read the
    convention. A raised exception can, and does: the loader fails on its first
    note, loudly, naming the security and saying what to do instead.

    Deliberately NOT log-and-continue. A skip counter reading "54 skipped" at
    the end of a run is indistinguishable from a healthy run to anyone not
    reading the logs, and it converts a design invariant into a statistic.
    """
    _require_super_admin(is_super_admin, "add_price")
    _check_choice(price_type, PRICE_TYPES, "price_type")
    if not isinstance(price_date, date):
        raise SecuritiesGlobalError(
            f"price_date must be a datetime.date — got {type(price_date).__name__}"
        )
    amount = _money(price, "price")

    async with _SuperAdminWrite(conn) as c:
        # Resolve through the merge chain first: a price aimed at a merged-away
        # duplicate belongs on the survivor, and the survivor's type is the one
        # that governs.
        row = await c.fetchrow(
            f"""
            SELECT canonical.id::text AS id,
                   canonical.name,
                   canonical.security_type
            FROM {TABLE_SEC} matched
            JOIN {TABLE_SEC} canonical
              ON canonical.id = COALESCE(matched.canonical_id, matched.id)
            WHERE matched.id = $1::uuid
            """,
            str(global_security_id),
        )
        if row is None:
            raise SecuritiesGlobalError(
                f"security {global_security_id} does not exist"
            )

        if row["security_type"] == STRUCTURED_NOTE:
            raise StructuredNotePricingError(
                f"refusing to write a price for {row['name']!r} "
                f"({row['id']}): it is a structured_note, and structured notes "
                f"are never priced in securities_global_prices. A note's "
                f"secondary marks are sporadic TRACE prints, not a daily "
                f"series, and storing them produces a series that looks "
                f"continuous and is not. Price the note's UNDERLYINGS instead "
                f"— see securities_global_relationships for this note's edges, "
                f"and resolve_scoreability() for which of them still lack "
                f"price_coverage='has_series'."
            )

        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_PRICE}
                (global_security_id, price_date, price, currency_code,
                 price_type, source)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
            RETURNING id::text
            """,
            row["id"], price_date, amount, currency_code, price_type, source,
        )


async def add_relationship(
    conn,
    *,
    from_global_security_id: str,
    raw_underlying_text: str,
    to_global_security_id: str | None = None,
    link_state: str = UNRESOLVED,
    relationship_type: str = "underlying_of",
    weight: Decimal | int | str | None = None,
    resolution_notes: str | None = None,
    is_super_admin: bool = False,
) -> str:
    """Record an underlying edge. Returns the relationship row id.

    ``to_global_security_id`` IS NULLABLE AND THAT IS THE POINT. An edge is
    created the moment a prospectus is read, when all that is known is the
    verbatim string — "the EURO STOXX 50 ® Index", "Class A Common Stock of
    X". Requiring a resolved target at insert time would mean either dropping
    those edges (the note then looks like it has no underlyings) or minting
    placeholder securities for strings nobody has checked (the corpus fills
    with duplicates of the same index under five spellings). Neither is
    recoverable later. ``raw_underlying_text`` is NOT NULL instead: an edge
    always knows what it read, even when it does not yet know what it means.

    This function cannot resolve anything. ``link_state='resolved'`` is written
    by exactly one function in the codebase —
    ``services.underlying_resolution.confirm_resolution`` — behind a human
    Super-Admin confirm, and underneath that by
    ``trg_sec_global_rel_confirm_gate``, which rejects any transition into
    'resolved' without a transaction-local confirm token this module never
    sets. The refusal below is the polite version of an error the database would
    raise anyway; it exists so the caller is told where the real door is.
    """
    _require_super_admin(is_super_admin, "add_relationship")
    _check_choice(link_state, LINK_STATES, "link_state")
    _check_choice(relationship_type, RELATIONSHIP_TYPES, "relationship_type")
    if not (raw_underlying_text or "").strip():
        raise SecuritiesGlobalError(
            "raw_underlying_text is required — an edge must record the string "
            "it was read from even when the target is unknown"
        )

    if link_state == RESOLVED:
        raise SecuritiesGlobalError(
            "add_relationship cannot create a resolved edge. Resolution is "
            "human-gated: create the edge unresolved, then go through "
            "services.underlying_resolution.propose_resolution() and "
            "confirm_resolution(). The database trigger "
            "trg_sec_global_rel_confirm_gate would reject this write regardless."
        )
    if link_state == AMBIGUOUS:
        raise SecuritiesGlobalError(
            "link_state='ambiguous' means 'a matcher proposed a target', which "
            "is what services.underlying_resolution.propose_resolution() "
            "writes — it also fills proposed_global_security_id, which "
            "sec_global_rel_ambiguous_has_proposal requires. Create the edge "
            "unresolved and let the matcher run."
        )

    async with _SuperAdminWrite(conn) as c:
        if await canonical_id_for(c, from_global_security_id) is None:
            raise SecuritiesGlobalError(
                f"security {from_global_security_id} does not exist"
            )
        if to_global_security_id is not None:
            if await canonical_id_for(c, to_global_security_id) is None:
                raise SecuritiesGlobalError(
                    f"target security {to_global_security_id} does not exist"
                )
        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_REL}
                (from_global_security_id, to_global_security_id,
                 raw_underlying_text, link_state, relationship_type,
                 weight, resolution_notes)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)
            RETURNING id::text
            """,
            str(from_global_security_id),
            str(to_global_security_id) if to_global_security_id else None,
            raw_underlying_text.strip(), link_state, relationship_type,
            _money(weight, "weight") if weight is not None else None,
            resolution_notes,
        )


async def set_price_coverage(
    conn,
    *,
    global_security_id: str,
    price_coverage: str,
    is_super_admin: bool = False,
) -> None:
    """Record whether a security has a usable price series.

    Its own function because it is the single input to
    :func:`resolve_scoreability` that a human decides rather than a pipeline
    derives — "we looked, and there is no public source for this decrement
    index" is a finding, and ``'unknown'`` (the column default) is honestly
    different from ``'no_public_source'``.
    """
    _require_super_admin(is_super_admin, "set_price_coverage")
    _check_choice(price_coverage, PRICE_COVERAGES, "price_coverage")
    async with _SuperAdminWrite(conn) as c:
        status = await c.execute(
            f"UPDATE {TABLE_SEC} SET price_coverage = $2 WHERE id = $1::uuid",
            str(global_security_id), price_coverage,
        )
    if status.rsplit(" ", 1)[-1] == "0":
        raise SecuritiesGlobalError(f"security {global_security_id} does not exist")
