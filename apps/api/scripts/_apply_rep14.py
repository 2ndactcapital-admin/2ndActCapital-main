"""REP14 — apply the Section 351 exit schema and prove it landed.

    python3 apps/api/scripts/_apply_rep14.py --schema    # docs/rep14_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep14.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep14.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner). Idempotent. Never prints a secret. THE MIGRATION WRITES NO ROWS (every new table starts empty; --verify
prints the live counts, never asserts a frozen number).
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

SCHEMA_SQL = REPO / "docs" / "rep14_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

ORG_TABLES = ("public.section351_sponsor_profiles", "public.section351_settings", "public.section351_plans",
              "public.section351_plan_accounts", "public.section351_basis_records", "public.section351_tracker_steps",
              "public.section351_transfers")
NEW_TABLES = ORG_TABLES + ("public.section351_asset_classes",)
PARTIAL_UNIQUES = ("section351_sponsor_profiles_live_uq", "section351_settings_live_uq",
                   "section351_asset_classes_live_uq", "section351_plan_accounts_live_uq",
                   "section351_basis_records_live_uq", "section351_tracker_steps_live_uq",
                   "section351_transfers_lot_uq")
PLAIN_UNIQUES = ("section351_sponsor_profiles_version_uq", "section351_settings_version_uq",
                 "section351_basis_records_version_uq")
CHECKS = {
    "public.section351_sponsor_profiles": ("section351_sponsor_profiles_version_chk",
                                           "section351_sponsor_profiles_name_chk",
                                           "section351_sponsor_profiles_status_chk",
                                           "section351_sponsor_profiles_cash_chk",
                                           "section351_sponsor_profiles_owner_chk",
                                           "section351_sponsor_profiles_assets_chk",
                                           "section351_sponsor_profiles_windows_chk"),
    "public.section351_settings": ("section351_settings_version_chk", "section351_settings_range_chk"),
    "public.section351_asset_classes": ("section351_asset_classes_scope_chk", "section351_asset_classes_class_chk",
                                        "section351_asset_classes_source_chk"),
    "public.section351_plans": ("section351_plans_name_chk", "section351_plans_status_chk",
                                "section351_plans_seed_chk", "section351_plans_ready_chk",
                                "section351_plans_pending_chk", "section351_plans_approved_chk",
                                "section351_plans_maker_checker_chk", "section351_plans_cancelled_chk",
                                "section351_plans_archive_chk"),
    "public.section351_plan_accounts": ("section351_plan_accounts_owner_chk", "section351_plan_accounts_us_chk",
                                        "section351_plan_accounts_verdict_chk",
                                        "section351_plan_accounts_consent_chk",
                                        "section351_plan_accounts_tax_adviser_chk",
                                        "section351_plan_accounts_cash_chk", "section351_plan_accounts_status_chk",
                                        "section351_plan_accounts_removed_chk",
                                        "section351_plan_accounts_transferred_chk",
                                        "section351_plan_accounts_archive_chk"),
    "public.section351_basis_records": ("section351_basis_records_version_chk", "section351_basis_records_sha_chk",
                                        "section351_basis_records_json_chk"),
    "public.section351_tracker_steps": ("section351_tracker_steps_key_chk", "section351_tracker_steps_status_chk",
                                        "section351_tracker_steps_done_chk", "section351_tracker_steps_archive_chk"),
    "public.section351_transfers": ("section351_transfers_kind_chk", "section351_transfers_status_chk",
                                    "section351_transfers_amount_chk", "section351_transfers_maker_checker_chk",
                                    "section351_transfers_sent_chk", "section351_transfers_confirmed_chk",
                                    "section351_transfers_cancelled_chk", "section351_transfers_archive_chk"),
}


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
        for full in NEW_TABLES:
            schema, t = full.split(".")
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2 "
                "ORDER BY ordinal_position", schema, t)]
            need(bool(cols), f"{full} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("valid_from", "valid_to", "system_from", "system_to")),
                 f"{full} carries both temporal axes")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full)
            need(rls is True, f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            quals = [(r["qual"] or "") + (r["with_check"] or "") for r in pol]
            if full in ORG_TABLES:
                need(len(quals) == 4 and all("NULLIF(current_setting('app.current_org_id'" in q for q in quals),
                     f"{full}: org isolation with NULLIF on every operation")
            else:
                need(all("NULLIF(current_setting('app.is_super_admin'" in q for q in quals if q != ""),
                     f"{full}: every policy names the super-admin GUC with NULLIF")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 "
                "AND table_name = $2 AND grantee = 'app_service'", schema, t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on {full}")
            trg = await conn.fetchval(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid = $1::regclass AND NOT tgisinternal "
                "AND tgname = $2", full, f"{t}_guard_trg")
            need(trg == 1, f"guard trigger on {full}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])",
            list(PARTIAL_UNIQUES + PLAIN_UNIQUES))}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:140])
        for name in PLAIN_UNIQUES:
            need("UNIQUE" in idx.get(name, ""), f"unique index {name}")
        for table, names in CHECKS.items():
            have = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass AND contype = 'c'", table)}
            for n in names:
                need(n in have, f"constraint {table}.{n}")
        for full in NEW_TABLES:
            print(f"[INFO] {full}: {await conn.fetchval(f'SELECT count(*) FROM {full}')} row(s)")
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
