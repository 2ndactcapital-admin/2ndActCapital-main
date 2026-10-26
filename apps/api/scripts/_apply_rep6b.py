"""REP6 FIX 4 — one model_change proposal per (account, model version).

    python3 apps/api/scripts/_apply_rep6b.py --schema   # docs/rep6fix4_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep6b.py --verify   # read it back from the catalog

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — the index needs the table owner). ``--schema`` first counts live
rows that would violate the index (counts only — it never edits or deletes a
row) and refuses to apply while any exist. ``--verify`` reads the index back
(unique, valid, its exact columns and predicate), proves the database refuses a
second live model_change row for one account + version (a copy of a TEST org's
closed row, inside a transaction it always rolls back), and that pg_advisory_xact_lock(hashtextextended(...)) is callable.
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

import asyncpg  # noqa: E402

SCHEMA_SQL = REPO / "docs" / "rep6fix4_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
INDEX = "rebalance_proposals_model_change_uq"
DUPLICATES_SQL = """SELECT count(*) FROM (SELECT account_id, model_version_id FROM public.rebalance_proposals
                    WHERE trigger = 'model_change' AND system_to IS NULL
                    GROUP BY account_id, model_version_id HAVING count(*) > 1) d"""


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def apply_schema() -> int:
    conn = await _conn()
    try:
        dups = await conn.fetchval(DUPLICATES_SQL)
        print(f"[INFO] (account, model version) pairs with more than one live model_change proposal: {dups}")
        if dups:
            print("[REFUSED] duplicates exist — the index would fail; nothing applied, no row touched")
            return 1
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
        return 0
    finally:
        await conn.close()


async def verify_schema() -> int:
    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'GAP '}] {label}" + (f"  — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        row = await conn.fetchrow(
            """SELECT i.indisunique, i.indisvalid, pg_get_indexdef(i.indexrelid) AS def,
                      pg_get_expr(i.indpred, i.indrelid) AS pred
               FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relname = $1""", INDEX)
        need(row is not None, f"{INDEX} exists")
        if row is not None:
            need(row["indisunique"] and row["indisvalid"], f"{INDEX} is unique and valid")
            need("(account_id, model_version_id)" in row["def"], f"{INDEX} keys (account_id, model_version_id)",
                 row["def"])
            pred = row["pred"] or ""
            need("model_change" in pred and "system_to IS NULL" in pred, f"{INDEX} predicate: live model_change rows",
                 pred)
        need(await conn.fetchval(DUPLICATES_SQL) == 0, "no live duplicate model_change pairs")
        # the database refuses a second live model_change row — probed with a copy of a TEST org's closed
        # model_change row (never a real org's), inside a transaction that is always rolled back
        src = await conn.fetchval(
            "SELECT p.id FROM public.rebalance_proposals p JOIN public.organizations o ON o.id = p.org_id "
            "WHERE o.is_test AND p.trigger = 'model_change' AND p.system_to IS NULL "
            "AND p.status IN ('superseded', 'rejected', 'expired', 'executed') LIMIT 1")
        if src is None:
            print("[FIND] no closed test-org model_change proposal to copy — the refusal is proven by "
                  "verify_rep6's concurrency check instead")
        else:
            refused = None
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute(
                    """INSERT INTO public.rebalance_proposals
                       SELECT (jsonb_populate_record(NULL::public.rebalance_proposals, to_jsonb(t) ||
                               jsonb_build_object('id', gen_random_uuid(), 'approval_task_id', NULL,
                                                  'superseded_by_id', NULL))).*
                       FROM public.rebalance_proposals t WHERE t.id = $1""", src)
            except asyncpg.UniqueViolationError as exc:
                refused = exc.constraint_name
            finally:
                await tr.rollback()
            need(refused == INDEX, "a second live model_change row for one account + version is refused", str(refused))
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                               "rebalance_proposal_account:00000000-0000-0000-0000-000000000000")
            held = await conn.fetchval("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                                       "AND pid = pg_backend_pid() AND granted")
        after = await conn.fetchval("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                                    "AND pid = pg_backend_pid()")
        need(held >= 1 and after == 0, "pg_advisory_xact_lock(hashtextextended(text, 0)) is granted and released "
             "at commit", f"held {held}, after commit {after}")
        print(f"[INFO] idle_in_transaction_session_timeout = "
              f"{await conn.fetchval('SHOW idle_in_transaction_session_timeout')}")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--schema" in args:
        return asyncio.run(apply_schema())
    if "--verify" in args:
        return asyncio.run(verify_schema())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
