"""CRMDETAIL1 — apply the schema (person columns, portfolio.liabilities, the demo-data delete guard) and prove it landed.

    python3 apps/api/scripts/_apply_crmdetail1.py --schema   # docs/crmdetail1_part1.sql, one transaction
    python3 apps/api/scripts/_apply_crmdetail1.py --verify   # read it all back from the catalog
    python3 apps/api/scripts/_apply_crmdetail1.py --all      # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner; the Supabase MCP cancels migration text containing DROP). Idempotent. Never prints a secret.

THE MIGRATION WRITES NO DATA ROW. --verify also proves the guard works, inside a transaction it ROLLS BACK: a
throwaway demo-tagged entity in the default org cannot be deleted, not even with the fixture GUC (the default org is
not is_test); nothing it inserted survives.
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

SCHEMA_SQL = REPO / "docs" / "crmdetail1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"
GUARDED = ("public.entities", "public.households", "public.household_memberships", "public.entity_relationships",
           "public.entity_attributes", "public.entity_addresses", "public.entity_notes", "public.entity_ownership",
           "portfolio.liabilities")
LIABILITY_COLUMNS = ("id", "lineage_id", "org_id", "owner_entity_id", "lender_entity_id", "lender_name", "name", "kind",
                     "original_amount", "current_balance", "currency_code", "as_of", "rate_type", "rate_text",
                     "maturity", "collateral_account_id", "collateral_asset_id", "collateral_text", "notes",
                     "change_reason", "valid_from", "valid_to", "system_from", "system_to", "created_by", "created_at")
CHECKS = ("liabilities_kind_chk", "liabilities_rate_type_chk", "liabilities_balance_chk", "liabilities_original_chk",
          "liabilities_name_chk", "liabilities_currency_chk", "liabilities_valid_order_chk",
          "liabilities_not_self_lender_chk")


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


class _Rollback(Exception):
    pass


async def verify() -> int:
    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        ecols = {r["column_name"]: r["data_type"] for r in await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'public' AND "
            "table_name = 'entities' AND column_name = ANY($1::text[])", ["maiden_name", "date_of_death"])}
        need(ecols == {"maiden_name": "text", "date_of_death": "date"}, "entities.maiden_name text, "
             "entities.date_of_death date", f"{ecols}")
        lcols = [r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' AND "
            "table_name = 'liabilities' ORDER BY ordinal_position")]
        need(lcols == list(LIABILITY_COLUMNS), "portfolio.liabilities has exactly the designed columns", f"{lcols}")
        money = {r["column_name"]: (r["data_type"], r["numeric_scale"]) for r in await conn.fetch(
            "SELECT column_name, data_type, numeric_scale FROM information_schema.columns WHERE table_schema = "
            "'portfolio' AND table_name = 'liabilities' AND column_name IN ('original_amount', 'current_balance')")}
        need(money == {"original_amount": ("numeric", 2), "current_balance": ("numeric", 2)},
             "money columns are numeric(20,2) (Decimal end to end)", f"{money}")
        chk = {r["conname"] for r in await conn.fetch(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'portfolio.liabilities'::regclass AND contype = 'c'")}
        need(set(CHECKS) <= chk, "every liabilities CHECK", f"{sorted(chk)}")
        idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND indexname = "
                                  "'liabilities_lineage_live_uq'")
        need(bool(idx and "UNIQUE" in idx and "valid_to IS NULL" in idx), "one live version per lineage (partial "
             "unique index)", idx or "missing")
        rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = 'portfolio.liabilities'::regclass")
        need(rls is True, "portfolio.liabilities has RLS enabled")
        pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'portfolio' AND "
                               "tablename = 'liabilities'")
        need({"SELECT", "INSERT", "UPDATE", "DELETE"} == {r["cmd"] for r in pol},
             "a policy for every operation (SELECT / INSERT / UPDATE / DELETE)", f"{sorted(r['cmd'] for r in pol)}")
        need(all("NULLIF" in ((r["qual"] or "") + (r["with_check"] or "")) for r in pol),
             "every policy NULLIFs the org GUC")
        grants = {r["privilege_type"] for r in await conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'portfolio' AND "
            "table_name = 'liabilities' AND grantee = 'app_service'")}
        need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, "app_service grants on portfolio.liabilities",
             f"{sorted(grants)}")
        trg = {r["t"] for r in await conn.fetch(
            "SELECT tgrelid::regclass::text AS t FROM pg_trigger WHERE tgname = 'crm_demo_delete_guard_trg' "
            "AND NOT tgisinternal")}
        want = {t.replace("public.", "") for t in GUARDED}
        need(want <= trg, "the demo delete guard is on every CRM table", f"{sorted(trg)}")
        sd = await conn.fetch("SELECT proname, prosecdef FROM pg_proc WHERE proname = ANY($1::text[])",
                              ["crm_is_demo_entity", "crm_is_demo_household", "crm_org_is_test",
                               "crm_demo_delete_guard"])
        need(len(sd) == 4 and all(r["prosecdef"] for r in sd), "guard functions exist, SECURITY DEFINER",
             f"{[(r['proname'], r['prosecdef']) for r in sd]}")
        n = await conn.fetchval("SELECT count(*) FROM portfolio.liabilities")
        print(f"[INFO] liabilities rows now: {n} (the migration wrote none; HollisMuse enters them)")
        # behaviour, rolled back: a demo-tagged throwaway in the default (real, not is_test) org cannot be deleted
        probe = {}
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                eid = await conn.fetchval(
                    "INSERT INTO entities (org_id, entity_type, display_name, tags) VALUES ($1, 'individual', "
                    "'crmdetail1 apply probe (rolled back)', ARRAY['demo-applyprobe']) RETURNING id", DEFAULT_ORG)
                for label, gucs in (("plain", ()), ("with the fixture GUC", ("crm.demo_teardown",))):
                    try:
                        async with conn.transaction():          # a savepoint: a refusal rolls back only itself
                            for g in gucs:
                                await conn.execute("SELECT set_config($1, 'fixture', true)", g)
                            await conn.execute("DELETE FROM entities WHERE id = $1", eid)
                            probe[label] = "DELETED"
                    except Exception as exc:  # noqa: BLE001
                        probe[label] = f"refused: {str(exc)[:200]}"
                raise _Rollback()
        except _Rollback:
            pass
        need(all(v.startswith("refused") and "permanent demo data" in v for v in probe.values()) and len(probe) == 2,
             "behaviour: a real org's demo-tagged entity cannot be deleted (rolled back probe)", f"{probe}")
        left = await conn.fetchval("SELECT count(*) FROM entities WHERE display_name = 'crmdetail1 apply probe "
                                   "(rolled back)'")
        need(left == 0, "the probe left nothing behind", f"{left}")
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
