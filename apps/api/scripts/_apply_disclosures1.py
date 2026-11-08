"""DISCLOSURES1 — apply the disclosure-library schema and prove it landed.

    python3 apps/api/scripts/_apply_disclosures1.py --schema    # docs/disclosures1_part1.sql, one transaction
    python3 apps/api/scripts/_apply_disclosures1.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_disclosures1.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner). Idempotent. Never prints a secret.

THE ONLY ROWS THE MIGRATION WRITES: the permissions view_disclosures / manage_disclosures and their grant to every
existing org_admin role. Live counts are printed, never asserted against a frozen number.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

SCHEMA_SQL = REPO / "docs" / "disclosures1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

TABLES = ("public.disclosures", "public.disclosure_versions", "public.disclosure_acknowledgements")
UNIQUES = ("disclosures_key_live_uq", "disclosure_versions_no_uq", "disclosure_versions_r2_key_uq")
CHECKS = {
    "public.disclosures": ("disclosures_key_chk", "disclosures_title_chk", "disclosures_category_chk",
                           "disclosures_audience_chk", "disclosures_archive_chk"),
    "public.disclosure_versions": ("disclosure_versions_scope_chk", "disclosure_versions_version_chk",
                                   "disclosure_versions_key_chk", "disclosure_versions_type_chk",
                                   "disclosure_versions_size_chk", "disclosure_versions_hash_chk",
                                   "disclosure_versions_summary_chk", "disclosure_versions_status_chk",
                                   "disclosure_versions_maker_checker_chk", "disclosure_versions_published_chk",
                                   "disclosure_versions_draft_chk", "disclosure_versions_retired_chk",
                                   "disclosure_versions_dates_chk", "disclosure_versions_archive_chk"),
    "public.disclosure_acknowledgements": ("disclosure_ack_subject_chk", "disclosure_ack_screen_chk"),
}
GUARDS = {"public.disclosures": "disclosures_guard_trg",
          "public.disclosure_versions": "disclosure_versions_guard_trg",
          "public.disclosure_acknowledgements": "disclosure_ack_guard_trg"}


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


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
        for full in TABLES:
            schema, t = full.split(".")
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2 "
                "ORDER BY ordinal_position", schema, t)]
            need(bool(cols), f"{full} exists", f"{len(cols)} columns")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full)
            need(rls is True, f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 "
                "AND table_name = $2 AND grantee = 'app_service'", schema, t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on {full}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
        for name in UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:140])
        for table, names in CHECKS.items():
            have = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass AND contype::text = 'c'", table)}
            for n in names:
                need(n in have, f"constraint {table}.{n}")
        fk = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                                 "'disclosure_ack_version_fk'")
        need(bool(fk and "content_sha256" in fk), "acknowledgement → (version id, content hash) composite FK", fk or "")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT n.nspname || '.' || c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c "
            "ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        perms = {r["name"] for r in await conn.fetch(
            "SELECT name FROM public.permissions WHERE name IN ('view_disclosures', 'manage_disclosures')")}
        need(perms == {"view_disclosures", "manage_disclosures"}, "permissions view_disclosures / manage_disclosures",
             f"{sorted(perms)}")
        missing = await conn.fetchval(
            "SELECT count(*) FROM public.roles r WHERE r.name = 'org_admin' AND (SELECT count(*) FROM "
            "public.role_permissions rp JOIN public.permissions p ON p.id = rp.permission_id WHERE rp.role_id = r.id "
            "AND p.name IN ('view_disclosures', 'manage_disclosures')) < 2")
        need(missing == 0, "every org_admin role holds both disclosure permissions", f"{missing} without")
        print(f"[INFO] disclosures: {await conn.fetchval('SELECT count(*) FROM public.disclosures')}; versions: "
              f"{await conn.fetchval('SELECT count(*) FROM public.disclosure_versions')}; acknowledgements: "
              f"{await conn.fetchval('SELECT count(*) FROM public.disclosure_acknowledgements')}")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
        return asyncio.run(verify_schema())
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--verify" in args:
        return asyncio.run(verify_schema())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
