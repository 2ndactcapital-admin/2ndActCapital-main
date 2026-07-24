"""SOC Phase 6 · Task 1 — Trusted Contacts (notify-only, ZERO data access).

A trusted contact is a "who to call", NOT a "who can see". It is the person a
member authorizes 2nd Act to REACH OUT TO — e.g. on suspected diminished
capacity, or when the member is unreachable. It confers NO visibility into the
member's entities, documents, holdings, or anything else.

CRITICAL BOUNDARY (do not cross): ``trusted_contacts`` and these helpers must
NEVER be read by any visibility-granting code path —

  * services/staff_visibility.py   (get_staff_visible_entity_ids)
  * services/entity_graph.py       (resolve_entity_set)
  * services/restricted_access.py  (filter_restricted)

None of those modules reference this table, and none ever should. A trusted
contact appearing in a resolved entity set would be a data-access leak.
``verify_soc6.py`` asserts this both structurally (the visibility modules contain
no textual reference to ``trusted_contact``) and behaviorally (a seeded contact
never appears in, and never affects, a resolved set for anyone).

This relationship is DISTINCT from ownership/beneficiary edges
(``entity_relationships``) and from staff assignment/hierarchy visibility
(Phases 2/4) — it must not be conflated with either.

Structural SOC posture (same as Phases 2/4/5): standalone, importable, exercised
directly by the verify script, HELD for manual wiring. It changes no existing
endpoint's behavior on its own. ``org_id`` is always supplied by the caller from
a server-resolved value — never from a request body.
"""


async def create_trusted_contact(
    pool,
    org_id,
    member_entity_id,
    *,
    contact_name,
    contact_phone=None,
    contact_email=None,
    relationship_to_member=None,
    added_by=None,
) -> str:
    """Add a trusted contact for a member entity. Returns the new contact id."""
    async with pool.acquire() as conn:
        return str(
            await conn.fetchval(
                """
                INSERT INTO trusted_contacts
                    (org_id, member_entity_id, contact_name, contact_phone,
                     contact_email, relationship_to_member, added_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                org_id,
                member_entity_id,
                contact_name,
                contact_phone,
                contact_email,
                relationship_to_member,
                added_by,
            )
        )


async def list_trusted_contacts(pool, org_id, member_entity_id) -> list[dict]:
    """List a member's trusted contacts (org-scoped)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, member_entity_id, contact_name, contact_phone,
                   contact_email, relationship_to_member, added_at, added_by
            FROM trusted_contacts
            WHERE org_id = $1 AND member_entity_id = $2
            ORDER BY added_at
            """,
            org_id,
            member_entity_id,
        )
    return [dict(r) for r in rows]


async def remove_trusted_contact(pool, org_id, contact_id) -> bool:
    """Remove a trusted contact. Returns True if a row was deleted."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM trusted_contacts WHERE org_id = $1 AND id = $2",
            org_id,
            contact_id,
        )
    return result.endswith(" 1")
