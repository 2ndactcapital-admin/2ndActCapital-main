"""Households — flexible rollup groups + strict primary household (SOC Phase 3).

Two DISTINCT grouping mechanisms live side by side here; they must never be
conflated:

  * ``household_memberships`` is MANY-TO-MANY. One entity may belong to any
    number of households at once. This models flexible reporting rollups
    ("show me everything the Smith family touches"), where overlap between
    households is expected and fine.

  * ``entities.primary_household_id`` is a single nullable FK — AT MOST ONE per
    entity. This is the STRICT grouping used for net-worth / billing, where an
    entity must be counted exactly once and overlap would corrupt the total.

Because an entity can sit in several flexible households, summing across
flexible households double-counts. The primary-household FK exists precisely to
give a non-overlapping partition for money math. See the two rollup functions
below — ``household_rollup`` (flexible, Task 2) vs
``primary_household_networth`` (strict, Task 3) — for the guardrails.

CONFIRMED DESIGN DECISION (SOC Phase 3): household membership does NOT grant
staff visibility. Nothing in this module reads or writes ``staff_assignments``
or calls the staff-visibility resolver. Visibility into a household's entities
remains its own separate, explicit grant.

Money is Decimal end to end, reusing the aggregation approach from the S23
investment roll-up (``services/spv_rollup.py``): COALESCE(SUM(...)) in SQL,
numeric columns coerced to Decimal without ever going via float.
"""

from decimal import Decimal
from uuid import UUID

ZERO = Decimal("0")


def _dec(v) -> Decimal:
    """Coerce a numeric column (or None) to Decimal without going via float.

    Same helper contract as ``spv_rollup._dec`` — asyncpg already returns
    Postgres ``numeric`` as ``Decimal``; the ``str()`` path only fires for the
    defensive non-Decimal case, never for a float.
    """
    if v is None:
        return ZERO
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _as_uuid(v):
    return v if not isinstance(v, str) else UUID(v)


# ==========================================================================
# TASK 1 — Household CRUD + membership management
# ==========================================================================
async def create_household(conn, org_id, name: str, *, created_by=None) -> dict:
    """Create a household. org_id is supplied by the caller, never a request body."""
    row = await conn.fetchrow(
        """
        INSERT INTO households (org_id, name, created_by)
        VALUES ($1, $2, $3)
        RETURNING id, org_id, name, created_at
        """,
        _as_uuid(org_id), name, _as_uuid(created_by) if created_by else None,
    )
    return dict(row)


async def rename_household(conn, org_id, household_id, name: str) -> dict | None:
    """Rename a household. Returns the updated row, or None if not found in org."""
    row = await conn.fetchrow(
        """
        UPDATE households SET name = $3
        WHERE id = $1 AND org_id = $2
        RETURNING id, org_id, name, created_at
        """,
        _as_uuid(household_id), _as_uuid(org_id), name,
    )
    return dict(row) if row else None


async def delete_household(conn, org_id, household_id) -> bool:
    """Delete a household. Clears the strict primary pointer on any entity that
    referenced it, and removes its flexible memberships, before deleting the row
    so no dangling FK is left behind. Returns True if a household was deleted.
    """
    hid = _as_uuid(household_id)
    oid = _as_uuid(org_id)
    # Detach strict primary references first (single-column update per entity).
    await conn.execute(
        "UPDATE entities SET primary_household_id = NULL "
        "WHERE org_id = $1 AND primary_household_id = $2",
        oid, hid,
    )
    # Remove flexible memberships.
    await conn.execute(
        "DELETE FROM household_memberships WHERE household_id = $1", hid,
    )
    deleted = await conn.fetchval(
        "DELETE FROM households WHERE id = $1 AND org_id = $2 RETURNING id",
        hid, oid,
    )
    return deleted is not None


async def add_entity_to_household(conn, org_id, household_id, entity_id,
                                  *, added_by=None) -> bool:
    """Add an entity to a household (MANY-TO-MANY). Idempotent.

    Validates that both the household and the entity belong to org_id — the
    junction table itself carries no org_id, so scoping is enforced here.
    Returns True on a fresh insert, False if the pair already existed.
    """
    hid, eid, oid = _as_uuid(household_id), _as_uuid(entity_id), _as_uuid(org_id)
    ok = await conn.fetchval(
        """
        SELECT (EXISTS (SELECT 1 FROM households WHERE id = $1 AND org_id = $3))
           AND (EXISTS (SELECT 1 FROM entities   WHERE id = $2 AND org_id = $3))
        """,
        hid, eid, oid,
    )
    if not ok:
        raise ValueError("household or entity not found in org")
    inserted = await conn.fetchval(
        """
        INSERT INTO household_memberships (household_id, entity_id, added_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (household_id, entity_id) DO NOTHING
        RETURNING entity_id
        """,
        hid, eid, _as_uuid(added_by) if added_by else None,
    )
    return inserted is not None


async def remove_entity_from_household(conn, household_id, entity_id) -> bool:
    """Remove an entity from a household (MANY-TO-MANY). Returns True if removed.

    This ONLY touches the flexible membership. It deliberately does NOT clear
    the entity's primary_household_id — the strict pointer is managed
    separately (see set/clear_primary_household).
    """
    removed = await conn.fetchval(
        """
        DELETE FROM household_memberships
        WHERE household_id = $1 AND entity_id = $2
        RETURNING entity_id
        """,
        _as_uuid(household_id), _as_uuid(entity_id),
    )
    return removed is not None


async def set_primary_household(conn, org_id, entity_id, household_id) -> bool:
    """Set an entity's STRICT primary household.

    This is a plain single-column UPDATE of ``entities.primary_household_id`` —
    NOT a bi-temporal close-and-insert and NOT a second junction row. Because
    the column holds at most one value, assigning a new primary REPLACES any
    prior one; an entity can never have two primary households. Returns True if
    the entity was found in org and updated.
    """
    updated = await conn.fetchval(
        """
        UPDATE entities SET primary_household_id = $3
        WHERE id = $1 AND org_id = $2
        RETURNING id
        """,
        _as_uuid(entity_id), _as_uuid(org_id), _as_uuid(household_id),
    )
    return updated is not None


async def clear_primary_household(conn, org_id, entity_id) -> bool:
    """Clear an entity's strict primary household (single-column update to NULL)."""
    updated = await conn.fetchval(
        """
        UPDATE entities SET primary_household_id = NULL
        WHERE id = $1 AND org_id = $2
        RETURNING id
        """,
        _as_uuid(entity_id), _as_uuid(org_id),
    )
    return updated is not None


async def list_households_for_entity(conn, org_id, entity_id) -> list[dict]:
    """ALL households an entity belongs to via the flexible MANY-TO-MANY table.

    This is a DIFFERENT query from ``get_primary_household`` and must not be
    conflated with it: an entity can appear in several rows here but has at most
    one primary household.
    """
    rows = await conn.fetch(
        """
        SELECT h.id, h.name, h.created_at, hm.added_at
        FROM household_memberships hm
        JOIN households h ON h.id = hm.household_id
        WHERE hm.entity_id = $1 AND h.org_id = $2
        ORDER BY h.name
        """,
        _as_uuid(entity_id), _as_uuid(org_id),
    )
    return [dict(r) for r in rows]


async def get_primary_household(conn, org_id, entity_id) -> dict | None:
    """The entity's SINGLE primary household (via entities.primary_household_id),
    or None if it has none.

    Deliberately separate from ``list_households_for_entity``: this follows the
    strict single-value FK, never the flexible junction table.
    """
    row = await conn.fetchrow(
        """
        SELECT h.id, h.name, h.created_at
        FROM entities e
        JOIN households h ON h.id = e.primary_household_id
        WHERE e.id = $1 AND e.org_id = $2
        """,
        _as_uuid(entity_id), _as_uuid(org_id),
    )
    return dict(row) if row else None


# ==========================================================================
# Shared aggregation core (reused by both rollups — Decimal-exact)
# ==========================================================================
# Latest holdings snapshot per (entity_id, taxonomy_key) on or before as_of,
# summed to a single total. This mirrors the S23/S21 pattern: DISTINCT ON to
# pick the current snapshot, then COALESCE(SUM(...)) so an empty set is a clean
# Decimal 0 rather than NULL. Postgres numeric SUM is exact and asyncpg returns
# it as Decimal, so no float ever enters the money path.
_HOLDINGS_TOTAL_SQL = """
    SELECT
      COALESCE(SUM(latest.market_value), 0)        AS total_value,
      COUNT(DISTINCT latest.entity_id)             AS entities_with_holdings
    FROM (
      SELECT DISTINCT ON (h.entity_id, h.taxonomy_key)
        h.entity_id, h.taxonomy_key, h.market_value
      FROM entity_holdings h
      WHERE h.org_id = $1
        AND h.entity_id = ANY($2::uuid[])
        AND h.as_of_date <= $3
      ORDER BY h.entity_id, h.taxonomy_key, h.as_of_date DESC
    ) latest
"""


async def _sum_holdings(conn, org_id, entity_ids: list, as_of) -> dict:
    """Sum the latest holdings market_value across a GIVEN set of entity ids.

    The single point where holdings dollars are aggregated. Both rollups feed it
    a member-id list; they differ ONLY in how that list is derived (flexible
    memberships vs strict primary FK). Returns Decimal totals.
    """
    if not entity_ids:
        return {"total_value": ZERO, "entities_with_holdings": 0}
    from datetime import date as _date
    if as_of is None:
        as_of = _date.today()
    row = await conn.fetchrow(
        _HOLDINGS_TOTAL_SQL,
        _as_uuid(org_id),
        [_as_uuid(e) for e in entity_ids],
        as_of,
    )
    return {
        "total_value": _dec(row["total_value"]),
        "entities_with_holdings": int(row["entities_with_holdings"]),
    }


# ==========================================================================
# TASK 2 — FLEXIBLE rollup across ALL many-to-many members
# ==========================================================================
async def household_rollup(conn, org_id, household_id, *, as_of=None) -> dict:
    """FLEXIBLE reporting rollup for one household.

    Aggregates holdings across EVERY member entity reached through the
    MANY-TO-MANY ``household_memberships`` table. An entity that belongs to
    several households is counted in EACH of their flexible rollups — that is
    intended for reporting, but it means you must NEVER sum flexible rollups
    across households to get an org total (that double-counts overlap). For a
    non-overlapping money total, use ``primary_household_networth`` instead.

    org_id is always the caller's authenticated org, never a request body.
    """
    member_rows = await conn.fetch(
        "SELECT entity_id FROM household_memberships WHERE household_id = $1",
        _as_uuid(household_id),
    )
    member_ids = [str(r["entity_id"]) for r in member_rows]
    totals = await _sum_holdings(conn, org_id, member_ids, as_of)
    return {
        "household_id": str(household_id),
        "basis": "flexible_membership",
        "member_count": len(member_ids),
        "member_entity_ids": member_ids,
        "total_holdings_value": totals["total_value"],
        "currency": "USD",
    }


# ==========================================================================
# TASK 3 — STRICT primary-household net-worth / billing aggregate
# ==========================================================================
async def primary_household_networth(conn, org_id, household_id=None,
                                     *, as_of=None) -> dict | list[dict]:
    """STRICT net-worth / billing aggregate grouped by primary_household_id ONLY.

    Use THIS for any money that must not be double-counted (net worth, billing).
    Because ``entities.primary_household_id`` holds at most one value per
    entity, every entity contributes to exactly one primary household — the
    aggregate is a true partition, with no overlap regardless of how many
    flexible households the entity also sits in.

    NEVER built from ``household_memberships``. Do not confuse this with
    ``household_rollup`` (flexible, may double-count) — that one is for
    reporting, this one is for billing.

    * household_id given  → the strict aggregate for that one primary household.
    * household_id None   → every primary household in the org, each total
                            computed independently so no entity is counted twice.
    """
    oid = _as_uuid(org_id)

    if household_id is not None:
        member_rows = await conn.fetch(
            "SELECT id FROM entities WHERE org_id = $1 AND primary_household_id = $2",
            oid, _as_uuid(household_id),
        )
        member_ids = [str(r["id"]) for r in member_rows]
        totals = await _sum_holdings(conn, org_id, member_ids, as_of)
        return {
            "household_id": str(household_id),
            "basis": "primary_household",
            "member_count": len(member_ids),
            "member_entity_ids": member_ids,
            "total_holdings_value": totals["total_value"],
            "currency": "USD",
        }

    # Org-wide: one aggregate per primary household, each independent.
    groups = await conn.fetch(
        """
        SELECT h.id AS household_id, h.name,
               array_agg(e.id) AS entity_ids
        FROM households h
        JOIN entities e
          ON e.primary_household_id = h.id AND e.org_id = h.org_id
        WHERE h.org_id = $1
        GROUP BY h.id, h.name
        ORDER BY h.name
        """,
        oid,
    )
    out = []
    for g in groups:
        member_ids = [str(x) for x in g["entity_ids"]]
        totals = await _sum_holdings(conn, org_id, member_ids, as_of)
        out.append({
            "household_id": str(g["household_id"]),
            "household_name": g["name"],
            "basis": "primary_household",
            "member_count": len(member_ids),
            "member_entity_ids": member_ids,
            "total_holdings_value": totals["total_value"],
            "currency": "USD",
        })
    return out
