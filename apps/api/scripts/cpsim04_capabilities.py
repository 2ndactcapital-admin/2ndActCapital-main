"""cpsim04 D9 — the PAYMENT capability rows of the SIMULATED Altruist-compatible custodian (code ``altruist_simulator``).

    python3 apps/api/scripts/cpsim04_capabilities.py                      # --dry-run (the default): print the plan
    python3 apps/api/scripts/cpsim04_capabilities.py --apply --actor-email <a Super Admin's email>

A sibling of scripts/cpsim03_capabilities.py (which owns the trading keys and, once this loader has recorded them,
skips the payment keys). Writes ONLY through the existing capability write path
(services.custodian_capabilities.set_capability — Super Admin, versioned on the valid axis, one audit_log row per
change), never by SQL. Idempotent: a key whose live value and evidence already match is skipped. ``--dry-run`` writes
nothing. The sprint ran it ``--dry-run`` only; applying it is an OPERATOR step (docs/cpsim/CPSIM_ADMIN.md) — until then
no payment can take the simulator route (D4 conditions 7 and 8).

The real Altruist rows (custodian code ``altruist``) are never touched: ach / wire / journal stay 'unknown',
payment_status_feed 'no'. Exit: 0 ok, 1 a refusal / error, 2 bad arguments. Never prints a secret.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

CODE = "altruist_simulator"
EVIDENCE = "simulated counterparty — cpsim04"
#: the simulated custodian's payment matrix (docs/cpsim/CPSIM04_NOTES.md D9): ACH, wire and journal through the
#: transfers API, a status feed; checks stay manual; the sheet is the MANUAL route's, not the custodian's
PLAN: dict[str, str] = {
    "ach": "yes", "wire": "yes", "journal": "yes", "check": "no", "payment_status_feed": "yes", "payment_sheet": "no",
}
REAL_KEYS = ("ach", "wire", "journal", "payment_status_feed")


async def plan_rows(conn) -> list[dict]:
    from services.custodian_capabilities import CAPABILITY_KEYS, capability, valid_value

    unknown = sorted(set(PLAN) - set(CAPABILITY_KEYS))
    if unknown:
        raise SystemExit(f"[FAIL] the plan names unknown keys {unknown}")
    out = []
    for key, value in PLAN.items():
        if not valid_value(key, value):
            raise SystemExit(f"[FAIL] {key}={value!r} is not a valid value")
        cur = await capability(conn, CODE, key)
        same = cur.version_no is not None and cur.value == value and cur.evidence == EVIDENCE
        out.append({"key": key, "current": cur.value if cur.version_no is not None else "(no row)",
                    "planned": value, "action": "skip" if same else "set"})
    return out


async def main(argv) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--actor-email", default=None)
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2
    if args.apply and not args.actor_email:
        print("[FAIL] --apply needs --actor-email (a Super Admin)")
        return 2
    hydrate()
    dsn, how = await resolve_dsn("DATABASE_URL")
    if not dsn:
        print(f"[FAIL] no database: {how}")
        return 1
    conn = await connect(dsn)
    try:
        rows = await plan_rows(conn)
        print(f"custodian code {CODE!r} — evidence {EVIDENCE!r} (assumed); mode {'APPLY' if args.apply else 'DRY RUN'}")
        for r in rows:
            print(f"  {r['action']:4}  {r['key']:32} {r['current']:>10} -> {r['planned']}")
        todo = [r for r in rows if r["action"] == "set"]
        print(f"{len(todo)} to set, {len(rows) - len(todo)} already as planned")
        real = {r["capability_key"]: r["value"] for r in await conn.fetch(
            "SELECT capability_key, value FROM public.custodian_capabilities WHERE custodian_code = 'altruist' AND "
            "capability_key = ANY($1::text[]) AND valid_to IS NULL AND system_to IS NULL", list(REAL_KEYS))}
        print("real altruist " + ", ".join(f"{k} = {real.get(k)!r}" for k in REAL_KEYS) + " (never changed by this "
              "script)")
        if not args.apply:
            print("DRY RUN — nothing written")
            return 0
        from services.custodian_capabilities import set_capability

        async with conn.transaction():
            actor = await conn.fetchrow("SELECT id, org_id, role, status, is_active FROM users WHERE lower(email) = "
                                        "lower($1)", args.actor_email)
            if actor is None or actor["role"] != "super_admin" or not actor["is_active"] \
                    or (actor["status"] or "active") != "active":
                print("[FAIL] the actor is not an active Super Admin")
                return 1
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true), "
                               "set_config('app.current_org_id', $1, true)", str(actor["org_id"]))
            for r in todo:
                await set_capability(conn, custodian_code=CODE, key=r["key"], value=r["planned"],
                                     evidence_kind="assumed", evidence=EVIDENCE, as_of=date.today(),
                                     actor_id=actor["id"], actor_org_id=actor["org_id"], is_super_admin=True,
                                     note="cpsim04 loader")
        print(f"APPLIED — {len(todo)} version(s) recorded")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
