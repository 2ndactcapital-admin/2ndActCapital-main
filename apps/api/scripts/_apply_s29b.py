"""S29B — apply docs/s29b_part1.sql and prove it landed; report Task 0's live effect first.

    python3 apps/api/scripts/_apply_s29b.py --effect    # READ-ONLY: who can no longer change their own access
    python3 apps/api/scripts/_apply_s29b.py --schema    # docs/s29b_part1.sql, one transaction
    python3 apps/api/scripts/_apply_s29b.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_s29b.py --all       # effect, schema, verify — in that order

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). Idempotent.
Never prints a secret. Writes the platform catalog (sod rows, one platform policy version) and DDL only — no org row,
and never switches autonomy on anywhere.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

SCHEMA_SQL = REPO / "docs" / "s29b_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
PLATFORM_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"
NEW_COLUMNS = {
    "workflow_runs": ("autonomy_mode", "shadow_review_outcome", "shadow_reviewed_by", "shadow_reviewed_at",
                      "shadow_review_note"),
    "workflow_run_steps": ("autonomy_mode", "delegate_for", "idempotency_key", "autonomy_claimed_at"),
    "domain_event_deliveries": ("workflow_version_id",),
}
CHECKS = {
    "workflow_autonomy_settings": ("was_scope_chk", "was_key_chk", "was_key_scope_chk", "was_status_chk",
                                   "was_maker_checker_chk", "was_checked_chk", "was_on_checked_chk",
                                   "was_rejected_chk", "was_reason_chk"),
    "workflow_runs": ("workflow_runs_autonomy_mode_chk", "workflow_runs_shadow_review_chk"),
    "workflow_run_steps": ("workflow_run_steps_autonomy_mode_chk", "workflow_run_steps_unattended_chk"),
    "domain_event_deliveries": ("domain_event_deliveries_status_check",),
}
UNIQUES = ("was_active_uq", "was_draft_uq", "workflow_run_steps_idempotency_uq",
           "domain_event_deliveries_event_version_uq")

GRANTERS_SQL = """
SELECT o.id::text AS org_id, o.name AS org, u.id::text AS id, u.full_name, u.email, u.role,
       (SELECT count(*) FROM user_identities ui WHERE ui.user_id = u.id) AS logins,
       u.role = 'super_admin' AS super_admin
  FROM users u JOIN organizations o ON o.id = u.org_id
 WHERE NOT COALESCE(o.is_test, false) AND NOT COALESCE(u.is_test, false)
   AND COALESCE(u.status, 'active') NOT IN ('retired', 'pending')
   AND (u.role = 'super_admin' OR EXISTS (
        SELECT 1 FROM user_roles ur JOIN role_permissions rp ON rp.role_id = ur.role_id
          JOIN permissions p ON p.id = rp.permission_id
         WHERE ur.user_id = u.id AND p.name = ANY($1::text[])))
 ORDER BY o.name, u.full_name, u.id
"""

PERSON_SQL = """
SELECT ids.id::text AS id, COALESCE(pl.person_user_id, dm.user_id, ids.id)::text AS person
  FROM unnest($1::uuid[]) AS ids(id)
  LEFT JOIN public.user_person_links pl ON pl.user_id = ids.id
  LEFT JOIN public.users u ON u.id = ids.id
  LEFT JOIN LATERAL (SELECT ui.user_id FROM public.user_identities ui
                      WHERE u.status = 'retired' AND u.auth0_sub IS NOT NULL AND ui.auth0_sub = u.auth0_sub
                        AND ui.user_id <> u.id ORDER BY ui.created_at LIMIT 1) dm ON true
"""


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def effect() -> None:
    """READ-ONLY: what turning the role-grant floor on changes for real people."""
    conn = await _conn()
    try:
        pol = await conn.fetchrow("SELECT version_no, enforce_floor FROM sod_rules WHERE org_id IS NULL AND "
                                  "rule_key = 'policy:role_grant' AND status = 'active' AND valid_to IS NULL")
        print(f"[EFFECT] live platform policy:role_grant v{pol['version_no']} enforce_floor={pol['enforce_floor']}")
        perms = await conn.fetchval("SELECT permissions FROM sod_duties WHERE duty_key = 'manage_roles'")
        rows = [dict(r) for r in await conn.fetch(GRANTERS_SQL, perms)]
        persons = {r["id"]: r["person"] for r in await conn.fetch(PERSON_SQL, [x["id"] for x in rows])}
        supers = {persons[r["id"]] for r in rows if r["super_admin"]}
        print(f"[EFFECT] grant-holding duty permissions: {perms} (plus every Super Admin)")
        by_org: dict[str, list[dict]] = {}
        for r in rows:
            r["person"] = persons[r["id"]]
            by_org.setdefault(r["org"], []).append(r)
        for org, people in by_org.items():
            org_persons = {p["person"] for p in people}
            print(f"[EFFECT] {org}: {len(people)} login row(s) that can grant access, {len(org_persons)} person(s)")
            for p in people:
                others = sorted((org_persons | supers) - {p["person"]})
                print(f"[EFFECT]   {p['full_name'] or '?'} <{p['email']}> row {p['id'][:8]} "
                      f"({'Super Admin' if p['super_admin'] else p['role']}, person {p['person'][:8]}, "
                      f"{p['logins']} linked login(s)) — can no longer change their OWN access; "
                      f"{len(others)} other person(s) can: {', '.join(o[:8] for o in others) or 'NOBODY'}")
        print("[EFFECT] note: rows are compared as PERSONS (user_person_links / duplicate merge). Two active rows of "
              "one human that are not linked are two persons to the rule — link them (People & Access) to make the "
              "floor bind across both.")
    finally:
        await conn.close()


async def apply_schema() -> None:
    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
    finally:
        await conn.close()


async def verify_schema() -> int:
    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        pol = await conn.fetch("SELECT version_no, status, enforce_floor, valid_to FROM sod_rules WHERE org_id IS NULL "
                               "AND rule_key = 'policy:role_grant' ORDER BY version_no")
        live = [r for r in pol if r["status"] == "active" and r["valid_to"] is None]
        need(len(live) == 1 and live[0]["enforce_floor"] is True and live[0]["version_no"] >= 2,
             "Task 0: the live platform policy:role_grant enforces the floor (a new version)",
             f"{[dict(r) for r in pol]}")
        need(any(r["status"] == "retired" and r["enforce_floor"] is False for r in pol),
             "Task 0: version 1 (floor off) is kept, retired — history, not an edit")
        n = await conn.fetchval("SELECT count(*) FROM audit_log WHERE action = 'sod.platform_rule_versioned' "
                                "AND payload->>'rule_key' = 'policy:role_grant'")
        need(n >= 1, "Task 0: the change is audited", f"{n} audit row(s)")
        for it in ("workflow_autonomy_setting", "workflow_autonomy_optin"):
            k = {r["rule_kind"] for r in await conn.fetch(
                "SELECT rule_kind FROM sod_rules WHERE org_id IS NULL AND item_type = $1 AND status = 'active' "
                "AND valid_to IS NULL", it)}
            need(k == {"default", "policy"}, f"sod item type {it}: platform default rule + policy", f"{k}")
        n = await conn.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                                "AND table_name = 'workflow_autonomy_settings'")
        need(n > 0, "workflow_autonomy_settings exists", f"{n} columns")
        need(await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = "
                                 "'public.workflow_autonomy_settings'::regclass") is True,
             "workflow_autonomy_settings has RLS enabled")
        cmds = {r["cmd"] for r in await conn.fetch("SELECT cmd FROM pg_policies WHERE tablename = "
                                                   "'workflow_autonomy_settings'")}
        need(cmds >= {"SELECT", "INSERT", "UPDATE", "DELETE"}, "a policy for every operation", f"{sorted(cmds)}")
        need(await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgname = "
                                 "'workflow_autonomy_settings_guard_trg'") == 1, "the content guard trigger exists")
        for t, cols in NEW_COLUMNS.items():
            have = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1",
                t)}
            need(set(cols) <= have, f"{t}: new columns", f"missing {sorted(set(cols) - have)}")
        for t, names in CHECKS.items():
            have = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = ('public.' || $1)::regclass", t)}
            need(set(names) <= have, f"{t}: CHECK constraints", f"missing {sorted(set(names) - have)}")
        have = {r["indexname"] for r in await conn.fetch("SELECT indexname FROM pg_indexes WHERE indexname = "
                                                         "ANY($1::text[])", list(UNIQUES))}
        need(set(UNIQUES) <= have, "unique indexes", f"missing {sorted(set(UNIQUES) - have)}")
        on = await conn.fetchval("SELECT count(*) FROM workflow_autonomy_settings s JOIN organizations o "
                                 "ON o.id = s.org_id WHERE NOT COALESCE(o.is_test, false) AND s.status = 'active' "
                                 "AND s.valid_to IS NULL AND s.setting_key = 'autonomy' AND s.setting_value = "
                                 "'true'::jsonb")
        need(on == 0, "autonomy is OFF on every real org (no active 'autonomy: true' row)", f"{on}")
    finally:
        await conn.close()
    print("SCHEMA OK" if not gaps else f"SCHEMA GAPS: {len(gaps)}")
    return 0 if not gaps else 1


async def main(argv: list[str]) -> int:
    if "--effect" in argv or "--all" in argv:
        await effect()
    if "--schema" in argv or "--all" in argv:
        await apply_schema()
    if "--verify" in argv or "--all" in argv:
        return await verify_schema()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:] or ["--verify"])))
