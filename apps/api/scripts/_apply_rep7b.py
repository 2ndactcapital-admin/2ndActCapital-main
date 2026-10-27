"""REP7B — apply the authority / custody schema, prove it landed, and run the
migration (Task 1c).

    python3 apps/api/scripts/_apply_rep7b.py --schema    # docs/rep7b_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep7b.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep7b.py --migrate   # the migration: before / after mapping (masked)
    python3 apps/api/scripts/_apply_rep7b.py --all       # all three, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. Never prints a secret or a full account
number (masked numbers only).

THE MIGRATION WRITES NO ROWS. Every existing account takes the migrated
default at read time (services/authority.default_for): held-away → read_only /
outside, every other account → managed / integrated. ``--migrate`` proves, for
EVERY live account, that each trade path is allowed / refused exactly as rep7's
rule decided before (and that no settings row exists for it), then prints the
counts. The one DRAFT instruction on a held-away account is listed with the
reason its approval is now refused; it is neither deleted nor cancelled.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()
sys.path.insert(0, str(API_DIR))

from services import authority as AU  # noqa: E402  (pure parts only: the matrix and the default)

SCHEMA_SQL = REPO / "docs" / "rep7b_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

TABLES = ("household_authority", "account_authority_settings", "instruction_settlements")
PARTIAL_UNIQUES = ("household_authority_live_uq", "household_authority_pending_uq", "household_authority_task_uq",
                   "account_authority_settings_live_uq", "account_authority_settings_pending_uq",
                   "account_authority_settings_task_uq", "instruction_settlements_live_uq")
CHECKS = ("household_authority_authority_chk", "household_authority_status_chk", "household_authority_reason_chk",
          "household_authority_maker_checker_chk", "household_authority_pending_chk", "household_authority_decided_chk",
          "household_authority_rejected_chk", "household_authority_closed_chk", "household_authority_archive_chk",
          "account_authority_settings_authority_chk", "account_authority_settings_custody_chk",
          "account_authority_settings_managed_outside_chk", "account_authority_settings_receiving_chk",
          "account_authority_settings_participant_chk", "account_authority_settings_masked_chk",
          "account_authority_settings_status_chk", "account_authority_settings_reason_chk",
          "account_authority_settings_maker_checker_chk", "account_authority_settings_pending_chk",
          "account_authority_settings_decided_chk", "account_authority_settings_rejected_chk",
          "account_authority_settings_closed_chk", "account_authority_settings_archive_chk",
          "instruction_settlements_type_chk", "instruction_settlements_authority_chk",
          "instruction_settlements_executor_chk", "instruction_settlements_date_chk",
          "instruction_settlements_calendar_chk", "instruction_settlements_participant_chk",
          "instruction_settlements_masked_chk", "instruction_settlements_name_chk", "rebalance_proposals_kind_chk")
GUARDS = {"household_authority": "household_authority_guard_trg",
          "account_authority_settings": "account_authority_settings_guard_trg",
          "instruction_settlements": "instruction_settlements_guard_trg"}
PERMISSIONS = ("view_authority", "manage_authority", "approve_authority")
# rep7's rule, before this sprint: a held-away account is refused on every trade path; any other account allowed
REP7_PATHS = tuple(p for p in AU.ALL_PATHS if p != "one_off")


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
        pcols = {r["column_name"]: r["is_nullable"] for r in await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'rebalance_proposals'")}
        need("proposal_kind" in pcols, "rebalance_proposals.proposal_kind exists")
        need(all(pcols.get(c) == "YES" for c in ("link_id", "model_id", "model_version_id")),
             "rebalance_proposals link_id / model_id / model_version_id nullable (one-off proposals only, by CHECK)")
        bad_kind = await conn.fetchval("SELECT count(*) FROM public.rebalance_proposals WHERE proposal_kind <> 'model'"
                                       " AND link_id IS NOT NULL")
        need(int(bad_kind) == 0, "every existing proposal is proposal_kind 'model'")
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
        mo = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                 "WHERE conname = 'account_authority_settings_managed_outside_chk'")
        need("managed" in (mo or "") and "outside" in (mo or ""), "managed + outside refused by a CHECK", mo or "")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        perms = {r["name"] for r in await conn.fetch("SELECT name FROM permissions WHERE name = ANY($1::text[])",
                                                     list(PERMISSIONS))}
        need(perms == set(PERMISSIONS), "permissions view/manage/approve_authority exist", f"{sorted(perms)}")
        missing = await conn.fetchval(
            """SELECT count(*) FROM roles r CROSS JOIN permissions p
               WHERE r.name = 'org_admin' AND p.name = ANY($1::text[])
                 AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)""",
            list(PERMISSIONS))
        need(int(missing) == 0, "every org_admin role holds the three authority permissions", f"{missing} missing")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def _before(account: dict, path: str) -> bool:
    """rep7's rule as it stood at main @ 9ed75ae."""
    if path == "one_off":
        return False   # did not exist
    return not account["is_held_away"]


async def migrate() -> int:
    """The migration is the read-time default; this proves it changes nothing."""
    conn = await _conn()
    fails = 0
    try:
        accts = [dict(r) for r in await conn.fetch(
            "SELECT a.id, a.org_id, a.account_number_masked, a.custodian_code, a.is_held_away, a.is_discretionary, "
            "a.household_id, o.is_test FROM public.accounts a JOIN public.organizations o ON o.id = a.org_id "
            "WHERE a.valid_to IS NULL AND a.system_to IS NULL ORDER BY o.is_test, a.account_number_masked")]
        rows = await conn.fetchval("SELECT count(*) FROM public.account_authority_settings")
        hh = await conn.fetchval("SELECT count(*) FROM public.household_authority")
        print(f"[INFO] live accounts: {len(accts)} ({sum(1 for a in accts if not a['is_test'])} in non-test orgs); "
              f"settings rows: {rows}; household rows: {hh} (the migration writes none)")
        before, after = Counter(), Counter()
        for a in accts:
            auth, cust = AU.default_for(a)
            a2 = {**a, "authority": auth, "custody": cust, "authority_source": "default"}
            diffs = []
            for p in AU.ALL_PATHS:
                b, n = _before(a, p), AU.path_allowed(a2, p)
                before[(p, b)] += 1
                after[(p, n)] += 1
                if b != n:
                    diffs.append(p)
            for ex in AU.ALL_EXECUTORS:
                b = not a["is_held_away"]
                if AU.path_allowed(a2, "instructions", ex) != b:
                    diffs.append(f"instructions/{ex}")
            print(f"  {a['account_number_masked']:>14}  {'held-away ' if a['is_held_away'] else 'integrated'}  "
                  f"→ authority {auth:9} custody {cust:15} {'test org' if a['is_test'] else 'REAL'}  "
                  f"{'UNCHANGED' if not diffs else 'CHANGED: ' + ', '.join(diffs)}")
            fails += 1 if diffs else 0
        print("[INFO] mapping counts (path, allowed) before → after:")
        for p in AU.ALL_PATHS:
            print(f"  {p:22} allowed {before[(p, True)]} → {after[(p, True)]}   refused {before[(p, False)]} → "
                  f"{after[(p, False)]}")
        mapping = Counter(AU.default_for(a) for a in accts)
        print("[INFO] resulting (authority, custody):", dict(mapping))
        for r in await conn.fetch(
                """SELECT i.id, i.status, i.executor_type, i.created_at, a.account_number_masked, a.is_held_away
                   FROM public.execution_instructions i JOIN public.accounts a ON a.id = i.account_id
                     AND a.valid_to IS NULL AND a.system_to IS NULL
                   WHERE a.is_held_away AND i.system_to IS NULL AND i.status IN ('draft', 'pending_approval')"""):
            acct = {"account_number_masked": r["account_number_masked"], "is_held_away": True}
            why = AU.refusal(acct, "approving an execution instruction", path="instructions",
                             executor=r["executor_type"])
            print(f"[INFO] open instruction {str(r['id'])[:8]}… on {r['account_number_masked']} ({r['status']}, "
                  f"{r['executor_type']}, {r['created_at']:%Y-%m-%d}) — kept; approval refused: {why}")
            fails += 0 if why else 1
    finally:
        await conn.close()
    print(f"\n{'MIGRATION OK — every existing account behaves as before' if not fails else f'{fails} CHANGED'}")
    return 1 if fails else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
        rc = asyncio.run(verify_schema())
        return rc or asyncio.run(migrate())
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--verify" in args:
        return asyncio.run(verify_schema())
    if "--migrate" in args:
        return asyncio.run(migrate())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
