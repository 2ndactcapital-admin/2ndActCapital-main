"""DEALROOM3 — apply the Phase 3 schema, then prove it landed.

    doppler run -- python3 apps/api/scripts/_apply_dealroom3.py --schema   # apply docs/dealroom3_part1.sql, then check
    doppler run -- python3 apps/api/scripts/_apply_dealroom3.py --check    # the read-only landing check only

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler, because DDL needs the table owner) in ONE transaction. It is idempotent.

Before applying, it fingerprints every existing deal_room_settings and
deal_access row. After applying, it proves:
  * every new object exists;
  * every new table has RLS enabled and a policy for each operation it takes;
  * the column defaults are as the migration says (watermark 'on' for new rooms);
  * every EXISTING settings row reads downloads 'allowed' / watermark 'off' /
    access_period 'none' / offering_type 'not_set' (today's behaviour);
  * the existing rows' original columns are unchanged (same fingerprint).

Never prints a secret.
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

SCHEMA_SQL = REPO / "docs" / "dealroom3_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

NEW_TABLES = ("deal_room_advisors", "deal_room_questions", "deal_room_answers", "deal_room_return_notices",
              "crm_relationship_dates", "deal_room_activity", "deal_room_watermarks")
APPEND_ONLY = ("deal_room_activity", "deal_room_watermarks")
SETTINGS_FP = ("SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) AS s FROM "
               "(SELECT id, deal_id, eligibility_rule, approval_mode, sponsor_display, code_name, valid_from, valid_to "
               "FROM deal_room_settings) t")
ACCESS_FP = ("SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) AS s FROM "
             "(SELECT id, deal_id, user_id, status, level_granted, prior_level, valid_from, valid_to "
             "FROM deal_access) t")


async def landing(conn, before: dict | None) -> int:
    fails = 0

    def ok(cond: bool, label: str, detail: str = "") -> None:
        nonlocal fails
        print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
        fails += 0 if cond else 1

    for t in NEW_TABLES:
        reg = await conn.fetchval("SELECT to_regclass($1)::text", f"public.{t}")
        rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass($1)", f"public.{t}")
        cmds = {r["cmd"] for r in await conn.fetch("SELECT cmd FROM pg_policies WHERE schemaname = 'public' "
                                                   "AND tablename = $1", t)}
        want = {"SELECT", "INSERT"} if t in APPEND_ONLY else {"SELECT", "INSERT", "UPDATE", "DELETE"}
        ok(reg is not None and rls is True and cmds == want,
           f"{t}: exists, RLS on, a policy for exactly {sorted(want)}", f"{reg} rls={rls} cmds={sorted(cmds)}")
    for t in APPEND_ONLY:
        trg = {r["tgname"] for r in await conn.fetch(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass($1) AND NOT tgisinternal", f"public.{t}")}
        ok({f"{t}_append_only", f"{t}_no_truncate"} <= trg, f"{t}: append-only + no-truncate triggers", f"{trg}")
    cols = {(r["table_name"], r["column_name"]): r["column_default"] for r in await conn.fetch(
        "SELECT table_name, column_name, column_default FROM information_schema.columns WHERE table_schema = 'public' "
        "AND table_name IN ('deal_room_settings', 'deal_access', 'deal_documents')")}
    for c in ("downloads", "watermark", "access_period", "access_until", "access_days", "expiry_notice_days",
              "notify_publisher_on", "offering_type"):
        ok(("deal_room_settings", c) in cols, f"deal_room_settings.{c} exists")
    ok("'on'" in (cols.get(("deal_room_settings", "watermark")) or ""),
       "new deal rooms default watermark 'on'", f"{cols.get(('deal_room_settings', 'watermark'))}")
    ok("'allowed'" in (cols.get(("deal_room_settings", "downloads")) or ""), "downloads defaults to 'allowed'")
    for c in ("access_ends_on", "paused_reason"):
        ok(("deal_access", c) in cols, f"deal_access.{c} exists")
    ok(("deal_documents", "share_unwatermarked") in cols, "deal_documents.share_unwatermarked exists")
    defs = {r["conname"]: r["d"] for r in await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = 'public.deal_access'::regclass")}
    ok("deal_access_status_v2_check" in defs and "'expired'" in defs["deal_access_status_v2_check"]
       and "'paused'" in defs["deal_access_status_v2_check"] and "deal_access_status_v3_check" not in defs,
       "deal_access status CHECK (name kept: deal_access_status_v2_check) carries expired + paused")
    ok("deal_access_prior_level_check" in defs and "'paused'" in defs["deal_access_prior_level_check"]
       and "deal_access_prior_level_v2_check" not in defs,
       "prior_level CHECK (name kept) allows paused", f"{sorted(defs)}")
    fn = await conn.fetchval("SELECT pg_get_functiondef('public.deal_room_folder_open(uuid,uuid)'::regprocedure)")
    ok("deal_room_advisors" in (fn or ""), "deal_room_folder_open is advisor-aware")
    ok(await conn.fetchval("SELECT to_regprocedure('public.deal_room_offering_open(uuid,uuid)') IS NOT NULL"),
       "deal_room_offering_open exists")
    legacy = await conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE downloads = 'allowed' AND watermark = 'off' AND access_period = "
        "'none' AND offering_type = 'not_set') AS today FROM deal_room_settings")
    ok(before is None or legacy["n"] == before["settings"][0],
       "no settings row added or removed by the migration", f"{legacy['n']}")
    if before is not None:
        ok(int(legacy["today"]) == before["settings"][0],
           "every EXISTING room keeps downloads allowed / watermark off / no period / offering not_set",
           f"{legacy['today']} of {legacy['n']}")
        s = await conn.fetchrow(SETTINGS_FP)
        a = await conn.fetchrow(ACCESS_FP)
        ok((s["n"], s["s"]) == before["settings"], "existing settings rows: original columns unchanged (fingerprint)")
        ok((a["n"], a["s"]) == before["access"], "existing access rows unchanged (fingerprint)",
           f"{before['access'][0]} → {a['n']}")
    labels = await conn.fetchval("SELECT count(*) FROM config WHERE org_id = '00000000-0000-0000-0000-000000000001' "
                                 "AND config_key IN ('deal_room_status_expired', 'deal_room_status_paused', "
                                 "'deal_room_offering_rule_506b')")
    ok(int(labels) == 3, "labels seeded on the default org (the seed every new org copies)", f"{labels}")
    print(f"[INFO] landing check: {fails} FAIL")
    return fails


async def main_async(apply: bool) -> int:
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        print(f"[FAIL] no operator DSN: {how}")
        return 2
    conn = await connect(dsn)
    try:
        print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
        before = None
        if apply:
            s = await conn.fetchrow(SETTINGS_FP)
            a = await conn.fetchrow(ACCESS_FP)
            before = {"settings": (s["n"], s["s"]), "access": (a["n"], a["s"])}
            print(f"[INFO] before: {s['n']} settings row(s), {a['n']} access row(s)")
            async with conn.transaction():
                await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            print("[OK] schema applied")
        return 1 if await landing(conn, before) else 0
    finally:
        await conn.close()


def main() -> int:
    args = sys.argv[1:]
    if "--schema" in args:
        return asyncio.run(main_async(True))
    if "--check" in args:
        return asyncio.run(main_async(False))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
