"""Unified staff-visibility resolver (SOC Phase 2).

Given a staff user, resolve the set of entity ids that user is *entitled* to
see, combining three sources:

  1. Direct assignment — rows in ``staff_assignments`` where
     ``assigned_to_user_id`` is this user.
  2. Team assignment — rows in ``staff_assignments`` where
     ``assigned_to_team_id`` is a team this user belongs to (via
     ``team_members``).
  3. Hierarchy — every user who reports to this user, directly or
     transitively (walking ``users.manager_id`` downward), contributes THEIR
     direct + team assignments too. A manager inherits the visibility of
     everyone beneath them.

SAFETY / SCOPE (SOC Phase 2):
    This is a STANDALONE, side-effect-free resolver. As of this phase it is
    NOT called from any request-handling path and is NOT wired into any
    endpoint as an enforcement gate. Existing endpoints keep their current
    (org-wide) visibility behavior unchanged. Switching an endpoint to use
    this resolver for enforcement is a deliberate, separate, later decision.

The hierarchy walk is cycle-safe (a ``visited`` set), mirroring the cycle
discipline used by the entity-graph BFS in ``services/entity_graph.py`` —
``users.manager_id`` is a self-referential FK and a malformed chain
(A→B→A) must not loop forever.
"""

from collections import deque


async def get_report_user_ids(pool, org_id: str, user_id: str) -> set[str]:
    """All users who report to ``user_id`` directly or transitively.

    Includes ``user_id`` itself. Walks ``users.manager_id`` downward (find
    users whose manager is a user already in the set) breadth-first, guarding
    against cycles with a ``visited`` set. Scoped to ``org_id``.
    """
    relevant: set[str] = {user_id}
    visited: set[str] = {user_id}
    queue: deque[str] = deque([user_id])

    async with pool.acquire() as conn:
        while queue:
            current = queue.popleft()
            rows = await conn.fetch(
                """
                SELECT id
                FROM users
                WHERE org_id = $1
                  AND manager_id = $2
                """,
                org_id,
                current,
            )
            for row in rows:
                child_id = str(row["id"])
                if child_id in visited:
                    # cycle guard — already seen this user on some path
                    continue
                visited.add(child_id)
                relevant.add(child_id)
                queue.append(child_id)

    return relevant


async def get_team_ids_for_users(
    pool, org_id: str, user_ids: set[str]
) -> set[str]:
    """Team ids (scoped to ``org_id``) that any of ``user_ids`` belong to."""
    if not user_ids:
        return set()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tm.team_id
            FROM team_members tm
            JOIN teams t ON t.id = tm.team_id
            WHERE t.org_id = $1
              AND tm.user_id = ANY($2::uuid[])
            """,
            org_id,
            list(user_ids),
        )
    return {str(r["team_id"]) for r in rows}


async def get_staff_visible_entity_ids(
    pool, user_id: str, org_id: str
) -> set[str]:
    """Entity ids visible to staff ``user_id`` within ``org_id``.

    Union of direct assignments, team assignments, and the direct + team
    assignments of every user who reports to this user (transitively).
    Returns an EMPTY set when the user has no assignments, is on no assigned
    team, and manages no one with access — i.e. the resolver genuinely
    restricts rather than defaulting to org-wide.
    """
    # 1. This user + everyone who rolls up to them (cycle-safe hierarchy walk).
    relevant_user_ids = await get_report_user_ids(pool, org_id, user_id)

    # 2. Every team any of those users belong to.
    team_ids = await get_team_ids_for_users(pool, org_id, relevant_user_ids)

    # 3. Entities assigned to any relevant user OR any relevant team.
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT entity_id
            FROM staff_assignments
            WHERE org_id = $1
              AND (
                    assigned_to_user_id = ANY($2::uuid[])
                 OR assigned_to_team_id = ANY($3::uuid[])
              )
            """,
            org_id,
            list(relevant_user_ids),
            list(team_ids),
        )

    return {str(r["entity_id"]) for r in rows}
