"""DAILYREBAL1 — operator setup for scheduled rebalancing of ONE household's model-linked accounts.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/dailyrebal_setup.py \
        --org <org id> --household "Olivo" --frequency daily [--drift 0.02] --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/dailyrebal_setup.py \
        --org <org id> --household "Olivo" --frequency daily [--drift 0.02] --actor-email <your login's email>

WHAT IT DOES
* Finds the household (name match inside the org — exactly one, or it stops and lists the candidates) and its
  accounts that follow a model (an active account-model link).
* --dry-run (the default): prints, per account, the current rebalance settings and the change it WOULD propose. Writes
  nothing.
* Real run (--actor-email): proposes the settings change for each account through the EXISTING settings service
  (services.account_models.propose_change, change 'settings': a pending link version + its maker-checker task,
  audited as the actor). Nothing takes effect until a DIFFERENT holder of approve_proposals approves each change on
  the account's page (Portfolio → Accounts → the account). An account whose settings already match, or with a change
  already awaiting approval, is skipped with the reason.
* Either way, prints the SIMULATOR READINESS CHECKLIST — read-only: the org settings counterparty_sim.allowed +
  counterparty_sim.altruist_counterparty, the org's active simulator connection, each account's ALTRUIST_SIMULATOR
  crosswalk (and no real ALTRUIST one), the demo-household rule (counterparty_sim_may_connect), managed + integrated
  authority, the capability loader's evidence (altruist_simulator equity_trading_api), the ALTRUIST_SIMULATOR_* and
  CPSIM_BASE_URL environment entries (NAMES ONLY, never a value), and approve_proposals holders who can approve.
  Each line is OK or MISSING with what to do. The script never changes any of those — it reports.

Exit: 0 ok (dry run, or every proposal made / skipped), 1 a refusal / error, 2 bad arguments. Never prints a secret.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
import uuid
from decimal import Decimal, InvalidOperation

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _db_connect  # noqa: E402,F401 — puts apps/api/venv's site-packages on sys.path (runs under python3 too)

#: the environment entries the simulated route needs (names only are ever printed)
REQUIRED_ENV = ("ALTRUIST_SIMULATOR_CLIENT_ID", "ALTRUIST_SIMULATOR_CLIENT_SECRET", "ALTRUIST_SIMULATOR_WEBHOOK_SECRET",
                "CPSIM_BASE_URL")
OPTIONAL_ENV = ("ALTRUIST_SIMULATOR_REALTIME_USERNAME", "ALTRUIST_SIMULATOR_REALTIME_PASSWORD")


def parse(argv):
    ap = argparse.ArgumentParser(prog="dailyrebal_setup.py")
    ap.add_argument("--org", required=True)
    ap.add_argument("--household", required=True)
    ap.add_argument("--frequency", required=True)
    ap.add_argument("--drift", default=None, help="the link's own drift threshold, a fraction (0.02 = 2%%)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--actor-email", default=None)
    return ap.parse_args(argv)


async def checklist(conn, org_id: str, accounts: list[dict], environ) -> list[tuple[bool, str, str]]:
    """[(ok, item, what to do)] — reads only."""
    from services import authority as AU
    from services import account_models as AM
    from services.altruist_oauth import AltruistSimulatorRefused, get_active_connection, simulator_counterparty
    from services.custodian_capabilities import available, capability
    from services.custodian_execution import (CAPABILITY_KEY, SIMULATOR_CAPABILITY_CUSTODIAN, XWALK_REAL,
                                              XWALK_SIMULATOR, _xwalk, simulator_route)

    out = []
    try:
        slug = await simulator_counterparty(conn, org_id=org_id)
        out.append((True, "org settings counterparty_sim.allowed + counterparty_sim.altruist_counterparty",
                    f"counterparty {slug!r}"))
    except AltruistSimulatorRefused as exc:
        out.append((False, "org settings counterparty_sim.allowed + counterparty_sim.altruist_counterparty",
                    f"{exc} — Admin → Counterparties (Super Admin) switches it on and names the counterparty"))
    c = await get_active_connection(conn, org_id=org_id, environment="simulator")
    out.append((c is not None and c.get("status") == "connected", "an active connection to the simulated custodian",
                "connected" if c is not None and c.get("status") == "connected" else
                "connect the org's simulator environment (Admin → Counterparties / the Altruist connection page)"))
    cap = await capability(conn, SIMULATOR_CAPABILITY_CUSTODIAN, CAPABILITY_KEY)
    out.append((available(cap), f"capability loader: {SIMULATOR_CAPABILITY_CUSTODIAN} {CAPABILITY_KEY}",
                f"'{cap.value}'" + ("" if available(cap) else
                                    " — run scripts/cpsim03_capabilities.py --apply (operator; never run by a sprint)")))
    for name in REQUIRED_ENV:
        present = bool((environ.get(name) or "").strip())
        out.append((present, f"environment entry {name}", "present" if present else
                    "absent from this process's environment — add it to Doppler (hollisworks / prd) and to the "
                    "service's sync"))
    for name in OPTIONAL_ENV:
        present = bool((environ.get(name) or "").strip())
        out.append((True, f"environment entry {name} (account opening only — optional here)",
                    "present" if present else "absent (not needed for trading)"))
    n_appr = int(await conn.fetchval(
        """SELECT count(DISTINCT u.id) FROM users u JOIN user_roles ur ON ur.user_id = u.id
           JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id JOIN role_permissions rp ON rp.role_id = ur.role_id
           JOIN permissions p ON p.id = rp.permission_id
           WHERE u.org_id = $1::uuid AND p.name = $2 AND u.is_active AND COALESCE(u.status, 'active') = 'active'""",
        org_id, AM.APPROVE))
    out.append((n_appr >= 2, "approve_proposals holders (one approves the settings change, a different one each "
                             "system proposal's instructions)", f"{n_appr} holder(s)" + ("" if n_appr >= 2 else
                                                                                         " — grant a second approver")))
    for a in accounts:
        name = a["account_number_masked"]
        real = await _xwalk(conn, org_id, a["id"], XWALK_REAL)
        sim = await _xwalk(conn, org_id, a["id"], XWALK_SIMULATOR)
        out.append((bool(sim) and not real, f"{name}: crosswalk ALTRUIST_SIMULATOR (and no real ALTRUIST)",
                    "crosswalked" if sim and not real else ("crosswalked to REAL Altruist — never simulated" if real
                                                            else "not crosswalked — open / link it at the simulated "
                                                                 "custodian (cpsim06 account opening)")))
        may = await conn.fetchval("SELECT public.counterparty_sim_may_connect($1::uuid, $2::uuid)", org_id,
                                  str(a["household_id"]) if a.get("household_id") else None)
        out.append((bool(may), f"{name}: demo household (cpsim may_connect)", "allowed" if may else
                    "a real org's household must be tagged demo to use a simulated custodian"))
        e = AU.effective(a)
        ok_auth = e["custody"] == "integrated" and e["authority"] == "managed"
        out.append((ok_auth, f"{name}: authority managed + custody integrated",
                    f"{e['authority']} / {e['custody']}"))
        out.append((bool(a.get("is_discretionary")), f"{name}: discretionary (scheduled rebalancing skips "
                                                     "advice-only accounts)",
                    "discretionary" if a.get("is_discretionary") else "advice only — it will be skipped"))
        r = await simulator_route(conn, org_id, a)
        out.append((bool(r["ok"]), f"{name}: the simulated route decision (custodian_execution.simulator_route)",
                    r["reason"] if r["ok"] else f"first missing: {r['condition']} — {r['reason']}"))
    return out


async def main(argv, *, pool=None, environ=None, emit=print) -> int:
    try:
        args = parse(argv)
    except SystemExit:
        return 2
    environ = os.environ if environ is None else environ
    from services import account_models as AM

    if args.frequency not in AM.FREQUENCY_LABELS:
        emit(f"[FAIL] --frequency is one of {list(AM.FREQUENCY_LABELS)}")
        return 2
    try:
        org_id = str(uuid.UUID(args.org))
    except ValueError:
        emit("[FAIL] --org is an organization id (uuid)")
        return 2
    drift = None
    if args.drift is not None:
        try:
            drift = Decimal(args.drift)
        except InvalidOperation:
            emit("[FAIL] --drift is a decimal fraction, e.g. 0.02")
            return 2
    dry = args.dry_run or not args.actor_email
    if not args.dry_run and not args.actor_email:
        emit("[INFO] no --actor-email: running as a DRY RUN")
    from services.database import get_pool, reset_rls_context, set_rls_context

    own_pool = pool is None
    if own_pool:
        _db_connect.hydrate()
        pool = await get_pool()
    tokens = set_rls_context(org_id, False)
    try:
        async with pool.acquire() as conn:
            org = await conn.fetchrow("SELECT id, name, is_test FROM public.organizations WHERE id = $1::uuid", org_id)
            if org is None:
                emit("[FAIL] organization not found (or not visible)")
                return 1
            hh = [dict(r) for r in await conn.fetch(
                "SELECT id, name FROM households WHERE org_id = $1::uuid AND name ILIKE '%' || $2 || '%' ORDER BY name",
                org_id, args.household)]
            if len(hh) != 1:
                emit(f"[FAIL] {len(hh)} households match {args.household!r}" +
                     (": " + "; ".join(h["name"] for h in hh[:10]) if hh else ""))
                return 1
            household = hh[0]
            ids = [r["id"] for r in await conn.fetch(
                "SELECT id FROM public.accounts WHERE org_id = $1::uuid AND household_id = $2::uuid "
                "AND system_to IS NULL AND valid_to IS NULL ORDER BY account_number_masked", org_id, household["id"])]
            accounts, plan = [], []
            for aid in ids:
                a = await AM.load_account(conn, org_id, aid)
                link = await AM.active_link(conn, a["id"])
                if link is None:
                    plan.append((a, None, "skip", "does not follow a model"))
                    continue
                accounts.append(a)
                cur = AM.settings_of(link)
                want = {"rebalance_frequency": args.frequency}
                if drift is not None:
                    want["drift_threshold"] = drift
                same = all((cur.get(k) == v) if k != "drift_threshold" else
                           (cur.get(k) is not None and Decimal(str(cur[k])) == v) for k, v in want.items())
                pend = await AM.pending_link(conn, a["id"])
                if same:
                    plan.append((a, cur, "skip", "settings already as asked"))
                elif pend:
                    plan.append((a, cur, "skip", "a change is already awaiting approval — decide it first"))
                else:
                    plan.append((a, cur, "propose", want))
            emit(f"organization {org['name']} ({org_id}){' — TEST ORG' if org['is_test'] else ''}; household "
                 f"{household['name']}; mode {'DRY RUN' if dry else 'REAL — proposing settings changes'}")
            for a, cur, act, what in plan:
                now = (f"frequency {cur.get('rebalance_frequency') or '—'}, drift threshold "
                       f"{cur.get('drift_threshold') if cur and cur.get('drift_threshold') is not None else '—'}"
                       if cur else "no model")
                emit(f"  {act:7} {str(a['account_number_masked']):14} now: {now}"
                     + (f"  → {', '.join(f'{k} {v}' for k, v in what.items())}" if act == "propose"
                        else f"  ({what})"))
            items = await checklist(conn, org_id, accounts, environ)
        emit("SIMULATOR READINESS CHECKLIST (read-only)")
        for ok, item, note in items:
            emit(f"  [{'OK' if ok else 'MISSING'}] {item} — {note}")
        missing = sum(1 for ok, _i, _n in items if not ok)
        emit(f"{missing} item(s) missing" if missing else "every item is in place")
        todo = [(a, w) for a, _c, act, w in plan if act == "propose"]
        if dry:
            emit(f"DRY RUN — nothing written ({len(todo)} change(s) would be proposed)")
            return 0
        async with pool.acquire() as conn:
            actor = await conn.fetchrow("SELECT id FROM users WHERE org_id = $1::uuid AND lower(email) = lower($2)",
                                        org_id, args.actor_email)
        if actor is None:
            emit("[FAIL] --actor-email is not a user of this organization")
            return 1
        rc = 0
        for a, want in todo:
            try:
                row = await AM.propose_change(pool, org_id=org_id, actor_id=actor["id"], account_id=a["id"],
                                              change_kind="settings", settings=want,
                                              notes="dailyrebal_setup: scheduled rebalancing")
                emit(f"  proposed {a['account_number_masked']}: pending version {row['id']} — task "
                     f"{row['task']['id']} (a different approve_proposals holder approves it on the account's page)")
            except Exception as exc:  # noqa: BLE001 — one account never stops the others
                rc = 1
                emit(f"  [FAIL] {a['account_number_masked']}: {type(exc).__name__}: {exc}")
        return rc
    finally:
        reset_rls_context(tokens)
        if own_pool:
            from services.database import close_pool

            await close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
