"""One-time (idempotent) drift reconciliation for orgadminwrites.structural
Task 3.

Scans EVERY user for a mismatch between the free-text ``users.role`` column
and the real RBAC grant (``user_roles`` -> ``roles`` named ``org_admin``, in
the user's OWN org), in EITHER direction, and reconciles it using the exact
same additive mechanism ``services.rbac.grant_org_admin`` /
``ensure_org_admin_role`` uses for new writes (Task 2) — not a second,
bespoke migration path.

  Direction 1: ``users.role = 'org_admin'`` but no matching ``user_roles``
  grant exists in that user's own org (the invite-path gap, pre-Task-2 shape).
  Fixed by GRANTING — additive, mirrors what the fixed ``create_invite``
  now does at write time.

  Direction 2: a real ``user_roles`` grant for that org's ``org_admin`` role
  exists but ``users.role`` is not ``'org_admin'`` (the assign_role-path gap,
  pre-Task-2 shape). Fixed by setting the STRING to match. Never touches a
  ``'super_admin'`` string — that axis is untouched by RBAC
  (``services.rbac.is_super_admin``).

Never a TRUNCATE, never a blind UPDATE — every row touched is reported by id
and email, per the standing verify-script-discipline rule ("report exactly
which users and which direction, rather than silently fixing").

Run:  python3 apps/api/scripts/_reconcile_orgadminwrites_drift.py
"""
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402


async def find_drift(conn):
    """Return (string_only, grant_only) — the two drift directions, unfixed."""
    string_only = await conn.fetch(
        """
        SELECT u.id, u.email, u.org_id
        FROM users u
        WHERE u.role = 'org_admin'
          AND NOT EXISTS (
              SELECT 1 FROM user_roles ur
              JOIN roles r ON r.id = ur.role_id
              WHERE ur.user_id = u.id AND r.org_id = u.org_id AND r.name = 'org_admin'
          )
        ORDER BY u.email
        """
    )
    grant_only = await conn.fetch(
        """
        SELECT DISTINCT u.id, u.email, u.org_id, u.role
        FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id
        WHERE r.name = 'org_admin' AND u.role <> 'org_admin' AND u.role <> 'super_admin'
        ORDER BY u.email
        """
    )
    return string_only, grant_only


async def reconcile(conn) -> dict:
    """Fix every drifted row found by :func:`find_drift`. Returns a report."""
    from services.rbac import grant_org_admin

    string_only, grant_only = await find_drift(conn)

    granted = []
    for row in string_only:
        await grant_org_admin(conn, row["id"], row["org_id"])
        granted.append({"id": str(row["id"]), "email": row["email"], "org_id": str(row["org_id"])})

    stringed = []
    for row in grant_only:
        await conn.execute(
            "UPDATE users SET role = 'org_admin', updated_at = now() WHERE id = $1",
            row["id"],
        )
        stringed.append({
            "id": str(row["id"]), "email": row["email"], "org_id": str(row["org_id"]),
            "previous_role": row["role"],
        })

    return {"granted": granted, "stringed": stringed}


async def main():
    url = await bootstrap_async()
    if not url:
        print("FATAL: no working DATABASE_URL from Doppler.")
        sys.exit(1)

    sys.path.insert(0, str(HERE.parents[1]))  # apps/api on sys.path
    from services.database import get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    tokens = set_rls_context(None, True)  # platform-level: this scans every org
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                result = await reconcile(conn)
    finally:
        reset_rls_context(tokens)
        await pool.close()

    print(f"[reconcile] direction 1 (string had no grant) — {len(result['granted'])} user(s) granted:")
    for r in result["granted"]:
        print(f"  {r['id']} ({r['email']}, org={r['org_id']})")
    print(f"[reconcile] direction 2 (grant had wrong string) — {len(result['stringed'])} user(s) corrected:")
    for r in result["stringed"]:
        print(f"  {r['id']} ({r['email']}, org={r['org_id']}): was role={r['previous_role']!r}")
    if not result["granted"] and not result["stringed"]:
        print("[reconcile] zero drift found — every org_admin string already has its grant, "
              "and every org_admin grant already has its string.")


if __name__ == "__main__":
    asyncio.run(main())
