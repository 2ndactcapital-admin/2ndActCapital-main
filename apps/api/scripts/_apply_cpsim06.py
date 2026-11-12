"""CPSIM06 — apply docs/cpsim06_part1.sql and read it back from the catalog.

    python3 apps/api/scripts/_apply_cpsim06.py --schema      # the migration (its own BEGIN / COMMIT)
    python3 apps/api/scripts/_apply_cpsim06.py --verify      # read every object back (namespace + relname, no ::regclass)
    python3 apps/api/scripts/_apply_cpsim06.py --all

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the owner). Idempotent. Never
prints a secret. Writes no data row. cpsim schema only (no public DDL — CPSIM06_NOTES §1d finding 7).
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

SCHEMA_SQL = REPO / "docs" / "cpsim06_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
#: the documented cpsim_app privileges per new table (the shared _cpsim_fixtures.CPSIM_APP_WITHHELD carries the same)
EXPECT_PRIVS = {
    "realtime_users": {"SELECT", "INSERT", "UPDATE"},
    "realtime_tokens": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "realtime_contacts": {"SELECT", "INSERT", "UPDATE"},
    "realtime_drafts": {"SELECT", "INSERT", "UPDATE"},
    "realtime_beneficiaries": {"SELECT", "INSERT"},
    "realtime_validation_log": {"SELECT", "INSERT"},
}
APPEND_ONLY = ("realtime_beneficiaries", "realtime_validation_log")
INDEXES = ("realtime_users_live_uq", "realtime_tokens_hash_uq", "realtime_contacts_live_uq", "realtime_contacts_ref_uq",
           "realtime_drafts_live_uq", "realtime_drafts_ref_uq", "realtime_drafts_account_uq",
           "realtime_beneficiaries_row_uq", "realtime_validation_log_recent_idx")
REL = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 "
       "AND c.relname = $2")


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def _public_policy_count(conn) -> int:
    return int(await conn.fetchval("SELECT count(*) FROM pg_policies WHERE schemaname = 'public'"))


async def apply_schema() -> None:
    conn = await _conn()
    try:
        before = await _public_policy_count(conn)
        await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        after = await _public_policy_count(conn)
        print("[OK] schema applied")
        print(f"[{'OK' if before == after else 'FAIL'}] public RLS policy count unchanged ({before} → {after})")
    finally:
        await conn.close()


async def _condef(conn, nsp, rel, con):
    return await conn.fetchval(
        "SELECT pg_get_constraintdef(k.oid) FROM pg_constraint k WHERE k.conrelid = (" + REL + ") AND k.conname = $3",
        nsp, rel, con)


async def verify() -> int:
    conn = await _conn()
    bad = 0

    def need(ok, label, detail=""):
        nonlocal bad
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
        bad += 0 if ok else 1

    try:
        app_role = await conn.fetchval("SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'")
        for t, want in EXPECT_PRIVS.items():
            oid = await conn.fetchval(REL, "cpsim", t)
            need(oid is not None, f"cpsim.{t} exists")
            if oid is None:
                continue
            privs = {r["privilege_type"] for r in await conn.fetch(
                "SELECT x.privilege_type, x.grantee FROM pg_class c, aclexplode(c.relacl) x WHERE c.oid = $1", oid)
                if r["grantee"] == app_role}
            need(privs == want, f"cpsim_app on cpsim.{t} = {sorted(want)}", f"{sorted(privs)}")
            others = await conn.fetch(
                "SELECT r.rolname, x.privilege_type FROM pg_class c, aclexplode(c.relacl) x JOIN pg_roles r ON "
                "r.oid = x.grantee WHERE c.oid = $1 AND r.rolname NOT IN ('cpsim_app', 'postgres')", oid)
            need(not others, f"no other role holds a privilege on cpsim.{t}", f"{[dict(r) for r in others]}")
            if t in APPEND_ONLY:
                trg = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = $1 AND NOT tgisinternal",
                                          oid)
                need(trg == 1, f"cpsim.{t} has its append-only guard trigger", str(trg))
        idx = {r["indexname"] for r in await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'cpsim' AND tablename = ANY($1::text[])",
            list(EXPECT_PRIVS))}
        for i in INDEXES:
            need(i in idx, f"cpsim index {i} exists")
        d = await _condef(conn, "cpsim", "realtime_contacts", "realtime_contacts_tax_last4_chk")
        need(bool(d and "0,4" in d), "realtime_contacts_tax_last4_chk refuses more than four digits", str(d))
        d = await _condef(conn, "cpsim", "realtime_contacts", "realtime_contacts_no_taxid_text_chk")
        need(bool(d and "{9}" in d), "realtime_contacts_no_taxid_text_chk refuses a 9-digit run", str(d))
        d = await _condef(conn, "cpsim", "realtime_validation_log", "realtime_validation_log_no_taxid_text_chk")
        need(bool(d and "{9}" in d), "the validation log refuses a 9-digit run", str(d))
        cols = {r["attname"] for r in await conn.fetch(
            "SELECT attname FROM pg_attribute WHERE attrelid = (" + REL + ") AND attnum > 0 AND NOT attisdropped",
            "cpsim", "realtime_contacts")}
        need(not ({"date_of_birth", "dob", "tax_id", "tax_id_value"} & cols),
             "realtime_contacts has no date-of-birth or full tax-id column", str(sorted(cols)))
        for c in ("date_open", "account_number"):
            n = await conn.fetchval("SELECT count(*) FROM pg_attribute WHERE attrelid = (" + REL + ") AND attname = $3 "
                                    "AND NOT attisdropped", "cpsim", "book_accounts", c)
            need(n == 1, f"cpsim.book_accounts.{c} exists")
        app_usage = await conn.fetchval("SELECT has_schema_privilege('app_service', 'cpsim', 'USAGE')")
        need(app_usage is False, "app_service still has no USAGE on schema cpsim")
    finally:
        await conn.close()
    print(f"VERIFY: {'OK' if not bad else f'{bad} FAIL'}")
    return bad


async def main(argv) -> int:
    if "--schema" in argv or "--all" in argv:
        await apply_schema()
    if "--verify" in argv or "--all" in argv:
        return 1 if await verify() else 0
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
