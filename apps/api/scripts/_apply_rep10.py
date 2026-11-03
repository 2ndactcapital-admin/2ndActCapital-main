"""REP10 — apply the tilts / completion / gifting / backtest schema and prove it landed.

    python3 apps/api/scripts/_apply_rep10.py --schema    # docs/rep10_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep10.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep10.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. Never prints a secret.

The migration writes NO business rows: no tilt set, donee, gift, backtest or
hypothetical-performance policy exists until a firm writes one (the policy
text is the firm's own words, approved by a second person). It adds one
permission (manage_hypothetical_policy, granted to every org_admin role), the
'factor_score' data type (csv_upload + the not-connected vendor slot) and its
platform rank rows.
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

SCHEMA_SQL = REPO / "docs" / "rep10_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

TABLES = ("tilt_sets", "tilt_lines", "completion_settings", "charitable_donees", "gift_proposals", "gift_lots",
          "hypothetical_performance_policies", "backtests")
PARTIAL_UNIQUES = ("tilt_sets_live_uq", "tilt_sets_open_uq", "tilt_sets_version_uq", "tilt_sets_task_uq",
                   "tilt_lines_live_uq", "completion_settings_live_uq", "gift_proposals_task_uq", "gift_lots_live_uq",
                   "hypothetical_policies_live_uq", "hypothetical_policies_pending_uq",
                   "hypothetical_policies_version_uq", "hypothetical_policies_task_uq")
CHECKS = (
    "tilt_sets_name_chk", "tilt_sets_reason_chk", "tilt_sets_version_chk", "tilt_sets_status_chk",
    "tilt_sets_maker_checker_chk", "tilt_sets_pending_chk", "tilt_sets_decided_chk", "tilt_sets_rejected_chk",
    "tilt_sets_archived_chk", "tilt_sets_archive_chk",
    "tilt_lines_kind_chk", "tilt_lines_subject_chk", "tilt_lines_delta_chk", "tilt_lines_reason_chk",
    "tilt_lines_line_chk",
    "completion_settings_authorities_chk", "completion_settings_stale_chk", "completion_settings_reason_chk",
    "charitable_donees_name_chk", "charitable_donees_kind_chk", "charitable_donees_ein_chk",
    "charitable_donees_broker_chk", "charitable_donees_dtc_chk", "charitable_donees_masked_chk",
    "charitable_donees_hash_chk", "charitable_donees_status_chk", "charitable_donees_archive_chk",
    "gift_proposals_subject_chk", "gift_proposals_donee_kind_chk", "gift_proposals_year_chk",
    "gift_proposals_target_chk", "gift_proposals_agi_chk", "gift_proposals_itemizes_chk", "gift_proposals_inputs_chk",
    "gift_proposals_carryforward_chk", "gift_proposals_rebuy_chk", "gift_proposals_status_chk",
    "gift_proposals_maker_checker_chk", "gift_proposals_pending_chk", "gift_proposals_approved_chk",
    "gift_proposals_sent_chk", "gift_proposals_received_chk", "gift_proposals_ack_chk",
    "gift_proposals_cancelled_chk", "gift_proposals_archive_chk",
    "gift_lots_quantity_chk", "gift_lots_period_chk", "gift_lots_price_chk", "gift_lots_gain_chk",
    "gift_lots_receipt_chk",
    "hypothetical_policies_text_chk", "hypothetical_policies_audience_chk", "hypothetical_policies_disclosure_chk",
    "hypothetical_policies_reason_chk", "hypothetical_policies_version_chk", "hypothetical_policies_status_chk",
    "hypothetical_policies_maker_checker_chk", "hypothetical_policies_pending_chk",
    "hypothetical_policies_decided_chk", "hypothetical_policies_rejected_chk", "hypothetical_policies_closed_chk",
    "hypothetical_policies_archive_chk",
    "backtests_window_chk", "backtests_dates_chk", "backtests_status_chk", "backtests_hypothetical_chk",
    "backtests_result_chk",
)
GUARDS = {"tilt_sets": "tilt_sets_guard_trg", "tilt_lines": "tilt_lines_guard_trg",
          "completion_settings": "completion_settings_guard_trg", "charitable_donees": "charitable_donees_guard_trg",
          "gift_proposals": "gift_proposals_guard_trg", "gift_lots": "gift_lots_guard_trg",
          "hypothetical_performance_policies": "hypothetical_policies_guard_trg",
          "backtests": "backtests_immutable_trg"}
PERMISSION = "manage_hypothetical_policy"
TYPE_CHECKS = (("portfolio", "data_providers_capabilities_chk"), ("portfolio", "data_source_rank_type_chk"),
               ("portfolio", "data_uploads_type_chk"), ("portfolio", "data_quality_events_type_chk"))


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
        for t in TABLES:
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = $1 ORDER BY ordinal_position", t)]
            need(bool(cols), f"public.{t} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("org_id", "valid_from", "valid_to", "system_from", "system_to")),
                 f"public.{t} is org-scoped and bi-temporal")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{t}")
            need(rls is True, f"public.{t} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'public' "
                                   "AND tablename = $1", t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"public.{t}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            quals = [(r["qual"] or "") + (r["with_check"] or "") for r in pol]
            need(len(quals) == 4 and all("NULLIF(current_setting('app.current_org_id'" in q for q in quals),
                 f"public.{t}: org isolation with NULLIF on every operation")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'public' "
                "AND table_name = $1 AND grantee = 'app_service'", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on public.{t}")
        # the factor-score store (mixed scope, like securities_global_prices)
        rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = "
                                  "'portfolio.security_factor_scores'::regclass")
        need(rls is True, "portfolio.security_factor_scores exists with RLS enabled")
        cmds = {r["cmd"] for r in await conn.fetch("SELECT cmd FROM pg_policies WHERE schemaname = 'portfolio' "
                                                   "AND tablename = 'security_factor_scores'")}
        need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= cmds, "security_factor_scores: a policy for every operation",
             f"{sorted(cmds)}")
        for schema, con in TYPE_CHECKS:
            d = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1 "
                                    "AND connamespace = $2::regnamespace", con, schema)
            need("factor_score" in (d or ""), f"{con} admits 'factor_score'", (d or "absent")[:120])
        caps = {r["key"]: list(r["capabilities"]) for r in await conn.fetch(
            "SELECT key, capabilities FROM portfolio.data_providers WHERE key IN ('csv_upload', 'vendor_slot') "
            "AND valid_to IS NULL AND system_to IS NULL")}
        need(all("factor_score" in caps.get(k, []) for k in ("csv_upload", "vendor_slot")),
             "csv_upload and vendor_slot carry the factor_score capability", f"{caps}")
        rank = [(r["provider_key"], r["rank"]) for r in await conn.fetch(
            "SELECT provider_key, rank FROM portfolio.data_source_rank WHERE owner_scope = 'platform' "
            "AND data_type = 'factor_score' AND valid_to IS NULL AND system_to IS NULL ORDER BY rank")]
        need(rank == [("csv_upload", 1), ("vendor_slot", 2)], "factor_score platform rank: csv_upload 1, vendor_slot 2",
             f"{rank}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY($1::text[])",
            list(PARTIAL_UNIQUES))}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:160])
        cons = {r["conname"] for r in await conn.fetch(
            "SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace")}
        for name in CHECKS:
            need(name in cons, f"constraint {name}")
        hyp = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                  "WHERE conname = 'backtests_hypothetical_chk'")
        need("hypothetical = true" in (hyp or ""), "the database admits only hypothetical = true backtests", hyp or "")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        perm = await conn.fetchval("SELECT count(*) FROM permissions WHERE name = $1", PERMISSION)
        need(int(perm) == 1, f"permission {PERMISSION} exists")
        missing = await conn.fetchval(
            """SELECT count(*) FROM roles r CROSS JOIN permissions p
               WHERE r.name = 'org_admin' AND p.name = $1
                 AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)""",
            PERMISSION)
        need(int(missing) == 0, f"every org_admin role holds {PERMISSION}", f"{missing} missing")
        rows = {t: int(await conn.fetchval(f"SELECT count(*) FROM public.{t}")) for t in TABLES}
        rows["portfolio.security_factor_scores"] = int(await conn.fetchval(
            "SELECT count(*) FROM portfolio.security_factor_scores"))
        print(f"[INFO] row counts: {rows}")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


async def main() -> int:
    args = set(sys.argv[1:]) or {"--verify"}
    if "--schema" in args or "--all" in args:
        await apply_schema()
    if "--verify" in args or "--all" in args:
        return await verify_schema()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
