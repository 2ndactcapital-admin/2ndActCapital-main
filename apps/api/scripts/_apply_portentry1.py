"""PORTENTRY1 — apply the schema (CRM vocabularies, edit_crm_contact, manual-asset columns, relink report, the
integrated-account provisional import, the family rollup queue) and prove it landed.

    python3 apps/api/scripts/_apply_portentry1.py --schema   # docs/portentry1_part1.sql, one transaction
    python3 apps/api/scripts/_apply_portentry1.py --verify   # read it all back from the catalog
    python3 apps/api/scripts/_apply_portentry1.py --all      # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner; the Supabase MCP cancels migration text containing DROP). Idempotent. Never prints a secret.

THE MIGRATION WRITES NO OLIVO ROW. It writes label rows (public.config), one permission catalog row and its grants
to support_staff roles and the 'CSA / Ops' profile — nothing else.
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
sys.path.insert(0, str(API_DIR))

SCHEMA_SQL = REPO / "docs" / "portentry1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"
NEW_TABLES = ("asset_relinks", "provisional_imports", "provisional_positions", "rollup_requests")
CRM_CATEGORIES = ("crm_entity_statuses", "crm_marital_statuses", "crm_sub_types")


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


async def verify() -> int:
    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        cats = {r["category"]: r["n"] for r in await conn.fetch(
            "SELECT category, count(*) AS n FROM public.config WHERE org_id = $1::uuid AND category = ANY($2::text[]) "
            "GROUP BY 1", DEFAULT_ORG, list(CRM_CATEGORIES))}
        need(set(cats) == set(CRM_CATEGORIES), "the default org holds the three CRM vocabularies", f"{cats}")
        keys = {r["config_key"] for r in await conn.fetch(
            "SELECT config_key FROM public.config WHERE org_id = $1::uuid AND category = ANY($2::text[])",
            DEFAULT_ORG, list(CRM_CATEGORIES))}
        want = {"crm_status_client", "crm_status_contact", "crm_marital_domestic_partnership",
                "crm_subtype.llc.real_estate", "crm_subtype.llc.operating", "crm_subtype.llc.collectibles"}
        need(want <= keys, "client / contact, marital statuses, LLC real estate / operating / collectibles",
             f"missing {sorted(want - keys)}")
        fn = await conn.fetchrow("SELECT prosecdef FROM pg_proc WHERE proname = 'crm_seed_org_config'")
        need(bool(fn and fn["prosecdef"]), "crm_seed_org_config exists, SECURITY DEFINER")
        perm = await conn.fetchval("SELECT id FROM public.permissions WHERE name = 'edit_crm_contact'")
        need(perm is not None, "permission edit_crm_contact in the catalog")
        rs = await conn.fetch(
            "SELECT r.org_id, EXISTS (SELECT 1 FROM public.role_permissions rp WHERE rp.role_id = r.id AND "
            "rp.permission_id = $1) AS granted FROM public.roles r WHERE r.name = 'support_staff'", perm)
        need(bool(rs) and all(r["granted"] for r in rs), "every support_staff role holds edit_crm_contact",
             f"{[(str(r['org_id']), r['granted']) for r in rs]}")
        client = await conn.fetchval(
            "SELECT count(*) FROM public.role_permissions rp JOIN public.roles r ON r.id = rp.role_id "
            "WHERE rp.permission_id = $1 AND r.name IN ('member', 'next_gen')", perm)
        need(client == 0, "no client role (member / next_gen) holds edit_crm_contact", f"{client}")
        pf = await conn.fetch(
            "SELECT pr.org_id, EXISTS (SELECT 1 FROM public.profile_permissions pp WHERE pp.profile_id = pr.id AND "
            "pp.permission_key = 'edit_crm_contact') AS granted FROM public.profiles pr WHERE pr.name = 'CSA / Ops'")
        need(bool(pf) and all(r["granted"] for r in pf), "the 'CSA / Ops' profile holds edit_crm_contact",
             f"{[(str(r['org_id']), r['granted']) for r in pf]}")
        acols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' AND "
            "table_name = 'assets'")}
        need({"manual_terms", "notes"} <= acols, "portfolio.assets.manual_terms + notes")
        for t in NEW_TABLES:
            cols = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' AND "
                "table_name = $1", t)}
            need({"valid_from", "valid_to", "system_from", "system_to", "org_id"} <= cols,
                 f"portfolio.{t} exists, bi-temporal, org-scoped", f"{sorted(cols)[:6]}…")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass",
                                      f"portfolio.{t}")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'portfolio' AND "
                                   "tablename = $1", t)
            need(rls is True and {r["cmd"] for r in pol} == {"SELECT", "INSERT", "UPDATE", "DELETE"}
                 and all("NULLIF" in ((r["qual"] or "") + (r["with_check"] or "")) for r in pol),
                 f"portfolio.{t}: RLS on, a NULLIF policy per operation", f"{sorted(r['cmd'] for r in pol)}")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'portfolio' "
                "AND table_name = $1 AND grantee = 'app_service'", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on portfolio.{t}")
        uq = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND "
                                 "indexname = 'rollup_requests_pending_uq'")
        need(bool(uq and "UNIQUE" in uq and "pending" in uq), "one pending rollup per family (partial unique)",
             uq or "missing")
        for t in ("provisional_imports", "provisional_positions", "rollup_requests", "asset_relinks"):
            n = await conn.fetchval(f"SELECT count(*) FROM portfolio.{t}")
            print(f"[INFO] portfolio.{t} rows now: {n}")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
        return asyncio.run(verify())
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--verify" in args:
        return asyncio.run(verify())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
