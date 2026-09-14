"""One-time (idempotent) data migration for the org_admin role reconciliation.

Makes ``org_admin`` a real row on the RBAC role axis (``roles`` /
``role_permissions`` / ``user_roles``), additive only:

  1. Create the ``manage_org_settings`` permission if it does not already
     exist (Task 1c found no suitable existing permission — ``manage_members``
     / ``manage_roles`` / ``manage_users`` are all narrower than org-level
     admin access, and ``routers/modeling_ta.py`` already referenced the
     string ``"manage_org_settings"`` in its envelope's ``write_permission``
     field without any real permission row backing it).
  2. Create an ``org_admin`` role in every org that currently has a
     ``users.role = 'org_admin'`` holder (``roles.org_id`` is NOT NULL and
     part of a ``(org_id, name)`` UNIQUE key — roles are per-org, there is no
     global role catalog to add one row to).
  3. Grant ``manage_org_settings`` to that role via ``role_permissions``.
  4. Grant every existing ``users.role = 'org_admin'`` holder the role via
     ``user_roles``.

Every statement is ON CONFLICT DO NOTHING — safe to re-run. ``users.role`` is
never read from a request body or written to here; this only adds rows to the
RBAC join tables. Run with:  python3 apps/api/scripts/_apply_orgadminrole_migration.py
"""
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

ORG_ADMIN_PERMISSION_NAME = "manage_org_settings"
ORG_ADMIN_ROLE_NAME = "org_admin"


async def main():
    url = await bootstrap_async()
    if not url:
        print("FATAL: no working DATABASE_URL from Doppler.")
        sys.exit(1)

    sys.path.insert(0, str(HERE.parents[1]))  # apps/api on sys.path
    from services.database import get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    tokens = set_rls_context(None, True)  # platform-level RBAC bootstrap
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                perm_id = await conn.fetchval(
                    """
                    INSERT INTO permissions (name, resource, action)
                    VALUES ($1, 'org_settings', 'manage')
                    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """,
                    ORG_ADMIN_PERMISSION_NAME,
                )
                print(f"[permission] {ORG_ADMIN_PERMISSION_NAME} -> {perm_id}")

                holders = await conn.fetch(
                    "SELECT id, org_id, email FROM users WHERE role = $1 ORDER BY org_id, email",
                    ORG_ADMIN_ROLE_NAME,
                )
                print(f"[discover] {len(holders)} existing org_admin holder(s)")

                org_ids = sorted({str(r["org_id"]) for r in holders})
                role_id_by_org = {}
                for org_id in org_ids:
                    role_id = await conn.fetchval(
                        """
                        INSERT INTO roles (org_id, name, description)
                        VALUES ($1, $2, 'Organization administrator — manages '
                                'org-level settings, profiles, permission sets, '
                                'and workflow authoring for this org.')
                        ON CONFLICT (org_id, name) DO UPDATE SET name = EXCLUDED.name
                        RETURNING id
                        """,
                        org_id, ORG_ADMIN_ROLE_NAME,
                    )
                    role_id_by_org[org_id] = role_id
                    print(f"[role] org={org_id} org_admin -> {role_id}")

                    await conn.execute(
                        """
                        INSERT INTO role_permissions (role_id, permission_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        role_id, perm_id,
                    )
                    print(f"[grant] role={role_id} -> permission={perm_id}")

                granted = []
                for h in holders:
                    role_id = role_id_by_org[str(h["org_id"])]
                    result = await conn.execute(
                        """
                        INSERT INTO user_roles (user_id, role_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        h["id"], role_id,
                    )
                    granted.append((str(h["id"]), h["email"], result))
                    print(f"[user_roles] user={h['id']} ({h['email']}) -> role={role_id}: {result}")

        print(f"\nDONE. {len(granted)} user_roles grant(s) ensured for "
              f"{len(org_ids)} org(s): {org_ids}")
    finally:
        reset_rls_context(tokens)
        await pool.close()


asyncio.run(main())
