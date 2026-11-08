"""S30 — Task 1 discovery (READ ONLY). Prints the catalog facts docs/S30_DISCOVERY.md records.

    python3 apps/api/scripts/_discover_s30.py

Runs on the operator DSN and writes NOTHING. Never prints a secret.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


async def main() -> None:
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    try:
        print("user:", await conn.fetchval("SELECT current_user"), "via", how)

        async def show(label, sql, *args, limit=400):
            rows = await conn.fetch(sql, *args)
            print(f"\n== {label} ({len(rows)} rows)")
            for r in rows[:limit]:
                print("  ", " | ".join(f"{k}={v}" for k, v in dict(r).items()))

        await show("1a CHECK constraints naming maker/checker-like columns",
                   r"""SELECT n.nspname||'.'||c.relname tbl, con.conname, pg_get_constraintdef(con.oid) def
                   FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE con.contype='c' AND n.nspname IN ('public','portfolio')
                     AND (pg_get_constraintdef(con.oid) ~* '(maker|checker|approved_by|proposed_by|entered_by|drafted_by|decided_by|reviewed_by|requested_by|completed_by|published_by)'
                          AND pg_get_constraintdef(con.oid) ~ '<>|IS DISTINCT FROM|!=')
                   ORDER BY 1,2""")
        await show("1a triggers whose function body mentions maker/checker",
                   r"""SELECT n.nspname||'.'||c.relname tbl, t.tgname, p.proname
                   FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                   JOIN pg_proc p ON p.oid=t.tgfoid
                   WHERE NOT t.tgisinternal AND p.prosrc ~* '(maker|checker|self.approv|approved_by\s*=\s*\w*proposed_by)'
                   ORDER BY 1,2""")
        await show("1b roles per org (name, holders, perms)",
                   """SELECT o.slug, r.name, (SELECT count(*) FROM user_roles ur WHERE ur.role_id=r.id) holders,
                             (SELECT count(*) FROM role_permissions rp WHERE rp.role_id=r.id) perms
                      FROM roles r LEFT JOIN organizations o ON o.id=r.org_id ORDER BY 1,2""")
        await show("1b permissions (name, category?)",
                   "SELECT * FROM permissions ORDER BY name", limit=400)
        await show("1b columns of roles/user_roles/permission sets",
                   """SELECT table_name, string_agg(column_name, ',' ORDER BY ordinal_position) cols
                      FROM information_schema.columns WHERE table_schema='public'
                        AND table_name IN ('roles','user_roles','role_permissions','permissions','permission_sets',
                          'permission_set_permissions','user_permission_sets','profiles','profile_permissions',
                          'users','user_identities','workflow_triggers','workflow_definitions','agent_proposals',
                          'workflow_runs','workflow_step_runs','organizations','org_settings','audit_log','workflow_todos')
                      GROUP BY 1 ORDER BY 1""")
        await show("1b permission sets", "SELECT * FROM permission_sets ORDER BY 1", limit=60)
        await show("1c user_identities per user (counts)",
                   """SELECT count(*) users, sum(CASE WHEN n>0 THEN 1 ELSE 0 END) with_identities, max(n) max_ids FROM
                      (SELECT u.id, (SELECT count(*) FROM user_identities i WHERE i.user_id=u.id) n FROM users u) x""")
        await show("1c placeholder-email users", "SELECT count(*) FROM users WHERE email LIKE '%@placeholder.local'")
        await show("1d workflow_triggers columns ownership",
                   """SELECT column_name, data_type FROM information_schema.columns
                      WHERE table_schema='public' AND table_name='workflow_triggers' ORDER BY ordinal_position""")
        await show("rls policies on agent_proposals/workflow_triggers",
                   "SELECT tablename, policyname, cmd FROM pg_policies WHERE tablename IN ('agent_proposals','workflow_triggers','workflow_todos') ORDER BY 1,3")
        await show("org_settings columns", """SELECT column_name, data_type, is_nullable FROM information_schema.columns
                      WHERE table_schema='public' AND table_name='org_settings' ORDER BY ordinal_position""")
        await show("is_test columns", """SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='is_test' ORDER BY 1""")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
