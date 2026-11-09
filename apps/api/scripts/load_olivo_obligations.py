"""CASH01 Task 7 — load the Olivo demo family's MANUAL obligations (docs/demo/olivo/data/obligations.csv).

    doppler run -- python3 apps/api/scripts/load_olivo_obligations.py --dry-run
    doppler run -- python3 apps/api/scripts/load_olivo_obligations.py --operator-email you@firm.com

--dry-run lists every row it would write and the coverage the Treasury tab would then show, per entity and for the
household — computed through the SAME code (services.cash_coverage.household_report) with the planned rows passed IN
MEMORY (``extra_stored``): nothing is inserted, nothing is written anywhere.

The real run writes as the OPERATOR (``--operator-email`` names the operator's users row → created_by; the audit row
names them) through services.cash_obligations.create / revise, on the app's own connection under the Olivo org's
RLS context. IDEMPOTENT: each row carries ``external_key = 'demo-olivo:<key>'`` (one live row per key, a unique
index): an identical live row is skipped, a changed one is REVISED (a new version, the old kept), never duplicated.
The rows are demo data: the crmdetail1 delete guard refuses to delete them (their payers are demo-* records).

Derived obligations (projected calls, debt service, the CRUT's unitrust) are NOT loaded — they are computed at read
time. The GRAT annuity, ILIT premium and foundation payout ARE loaded as manual rows because the Olivo CRM records do
not carry the new CASH01 detail fields (annuity amount / premium / next dates); a stored row of the same kind for the
same entity suppresses the derived line, so nothing is counted twice when the fields are filled in later.
Never prints a secret. No network beyond the database (and Doppler for the secrets).
"""

from __future__ import annotations

import asyncio
import csv
import json
import pathlib
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

CSV = REPO / "docs" / "demo" / "olivo" / "data" / "obligations.csv"
HOUSEHOLD_NAME = "Olivo Family"
TAG = "demo-olivo"
FIELDS = ("key", "payer_name", "source_type", "name", "amount_usd", "amount_rule", "due_date", "rrule", "end_date",
          "certainty", "notes")


def read_csv(path=CSV) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        missing = [f for f in FIELDS if f not in r]
        if missing:
            raise SystemExit(f"obligations.csv lacks columns {missing}")
    keys = [r["key"] for r in rows]
    if len(set(keys)) != len(keys):
        raise SystemExit("obligations.csv: duplicate keys")
    return rows


async def _ctx(conn, org_id, super_admin=False):
    await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', $2, true)",
                       str(org_id) if org_id else "", "true" if super_admin else "false")


async def locate(conn) -> tuple[str, str, dict]:
    """(org id, household id, {display name: entity id}) — the demo household and its demo-tagged members."""
    async with conn.transaction():
        await _ctx(conn, None, True)
        hh = await conn.fetch("SELECT h.id::text AS id, h.org_id::text AS org FROM households h WHERE h.name = $1 "
                              "AND public.crm_is_demo_household(h.id)", HOUSEHOLD_NAME)
    if len(hh) != 1:
        raise SystemExit(f"expected exactly one demo household named {HOUSEHOLD_NAME!r}, found {len(hh)}")
    org, hid = hh[0]["org"], hh[0]["id"]
    async with conn.transaction():
        await _ctx(conn, org)
        ents = await conn.fetch(
            "SELECT e.id::text AS id, e.display_name FROM entities e JOIN household_memberships m ON m.entity_id = e.id "
            "WHERE m.household_id = $1::uuid AND e.org_id = $2::uuid AND e.valid_to IS NULL AND e.system_to IS NULL "
            "AND $3 = ANY(e.tags)", hid, org, TAG)
    names: dict[str, str] = {}
    for r in ents:
        if r["display_name"] in names:
            raise SystemExit(f"two demo members share the name {r['display_name']!r}")
        names[r["display_name"]] = r["id"]
    return org, hid, names


async def plan(conn, org, rows, names, today: date) -> list[dict]:
    """Each CSV row → the obligation data it becomes (amount rules resolved through the net-worth core)."""
    from services import networth as NW

    out = []
    for r in rows:
        eid = names.get(r["payer_name"])
        if eid is None:
            raise SystemExit(f"row {r['key']}: no demo member named {r['payer_name']!r} in {HOUSEHOLD_NAME}")
        amount = r["amount_usd"].strip()
        rule = r["amount_rule"].strip()
        basis = None
        if rule:
            kind, _, pct = rule.partition(":")
            if kind != "pct_of_value":
                raise SystemExit(f"row {r['key']}: unknown amount rule {rule!r}")
            ctx = await NW.load(conn, org, [eid], as_of=today)
            gross = NW.figures(ctx, {eid})["gross"]
            amount = format((gross * Decimal(pct) / Decimal(100)).quantize(Decimal("0.01")), "f")
            basis = f"{pct}% × {NW.money(gross)} (net-worth core, {today.isoformat()})"
        data = {"entity_id": eid, "source_type": r["source_type"], "name": r["name"], "amount": amount,
                "due_date": r["due_date"], "certainty": r["certainty"]}
        for k in ("rrule", "end_date", "notes"):
            if r[k].strip():
                data[k] = r[k].strip()
        if basis:
            data["notes"] = ((data.get("notes") or "") + f" [amount: {basis}]").strip()
        out.append({"key": f"{TAG}:{r['key']}", "data": data, "payer_name": r["payer_name"]})
    return out


def _same(live, v) -> bool:
    return (str(live["amount"]) == str(Decimal(v["amount"]).quantize(Decimal("0.01"))) and live["name"] == v["name"]
            and live["due_date"].isoformat() == str(v["due_date"]) and (live["rrule"] or None) == v.get("rrule")
            and ((live["end_date"].isoformat() if live["end_date"] else None) == v.get("end_date"))
            and live["certainty"] == v["certainty"] and live["source_type"] == v["source_type"]
            and (live["notes"] or None) == (v.get("notes") or None))


def _memory_row(org, p, today) -> dict:
    d = p["data"]
    return {"id": uuid.uuid4(), "lineage_id": uuid.uuid4(), "org_id": org, "entity_id": d["entity_id"],
            "source_type": d["source_type"], "name": d["name"], "amount": Decimal(d["amount"]), "currency_code": "USD",
            "due_date": date.fromisoformat(d["due_date"]), "rrule": d.get("rrule"),
            "end_date": date.fromisoformat(d["end_date"]) if d.get("end_date") else None, "certainty": d["certainty"],
            "source_ref_type": None, "source_ref_id": None, "owner_user_id": None, "notes": d.get("notes"),
            "external_key": p["key"], "as_of": today, "change_reason": "dry run", "end_reason": None,
            "created_by": None, "created_at": None, "valid_from": None, "valid_to": None}


def print_report(rep) -> None:
    h = rep.household
    print(f"\nHOUSEHOLD {HOUSEHOLD_NAME}: liquid {h['liquid']}; monthly outflow {h['monthly_outflow']}; months "
          f"{h['months'] or h['months_text']}; covered {'beyond' if h['covered_beyond_horizon'] else 'through'} "
          f"{h['covered_through']}; next gap: "
          + (f"{h['next_gap']['name']} ~{h['next_gap']['amount']} {h['next_gap']['date']}" if h["next_gap"] else "none")
          + f"; flags {h['flags']}")
    print("\nPER ENTITY (entity | liquid | months | covered through | next gap | flags)")
    for r in rep.entities:
        gap = f"{r['next_gap']['name']} ~{r['next_gap']['amount']} {r['next_gap']['date']}" if r["next_gap"] else "—"
        print(f"  {r['display_name']} | {r['liquid'] if r['has_cash'] else 'no cash'} | "
              f"{r['months'] or r['months_text']} | {'beyond ' if r['covered_beyond_horizon'] else ''}"
              f"{r['covered_through']} | {gap} | {','.join(r['flags']) or '—'}")
    if rep.not_estimated:
        print("\nNOT ESTIMATED")
        for x in rep.not_estimated:
            print(f"  {x['name']} ({x['source_type']}): needs {x['needs']}")
    if rep.lines_of_credit:
        print("\nLINES OF CREDIT (shown separately, never counted)")
        for x in rep.lines_of_credit:
            print(f"  {x['name']}: drawn {x['drawn']}, undrawn {x['undrawn'] or '— ' + x['note']}")


async def main(argv) -> int:
    from _db_connect import admin_dsn, connect, hydrate

    hydrate()
    from services import cash_coverage as CC
    from services import cash_obligations as OB

    dry = "--dry-run" in argv
    email = argv[argv.index("--operator-email") + 1] if "--operator-email" in argv else None
    if not dry and not email:
        print("the real run names the operator: --operator-email <their sign-in email> (or use --dry-run)")
        return 2
    rows = read_csv()
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"no database: {how}")
        return 1
    conn = await connect(dsn)
    today = datetime.now(timezone.utc).date()
    try:
        org, hid, names = await locate(conn)
        async with conn.transaction():
            await _ctx(conn, org)
            planned = await plan(conn, org, rows, names, today)
            live = {r["external_key"]: r for r in await conn.fetch(
                "SELECT * FROM cash_obligations WHERE org_id = $1::uuid AND external_key = ANY($2::text[]) AND "
                "valid_to IS NULL AND system_to IS NULL", org, [p["key"] for p in planned])}
        print(f"[INFO] database via {how}; org {org}; household {hid}; {len(planned)} rows in obligations.csv")
        actions = []
        for p in planned:
            cur = live.get(p["key"])
            act = "create" if cur is None else ("unchanged" if _same(cur, p["data"]) else "revise")
            actions.append((act, p, cur))
            d = p["data"]
            print(f"  [{act}] {p['key']}: {d['name']} — {d['amount']} {d['source_type']} {d['certainty']} from "
                  f"{d['due_date']} {d.get('rrule') or 'once'}{' to ' + d['end_date'] if d.get('end_date') else ''} "
                  f"(payer {p['payer_name']})")
        if dry:
            mem = [_memory_row(org, p, today) for act, p, _c in actions if act == "create"]
            mem += [{**_memory_row(org, p, today), "lineage_id": c["lineage_id"]} for act, p, c in actions
                    if act == "revise"]
            revised = {c["lineage_id"] for act, _p, c in actions if act == "revise"}
            async with conn.transaction():
                await _ctx(conn, org)
                orig = OB.stored_rows

                async def without_revised(c, o, e):
                    return [r for r in await orig(c, o, e) if r["lineage_id"] not in revised]

                OB.stored_rows = without_revised
                try:
                    rep = await CC.household_report(conn, org, hid, today=today, extra_stored=mem)
                finally:
                    OB.stored_rows = orig
            print_report(rep)
            print(f"\nDRY RUN — nothing written ({sum(1 for a in actions if a[0] == 'create')} would be created, "
                  f"{sum(1 for a in actions if a[0] == 'revise')} revised, "
                  f"{sum(1 for a in actions if a[0] == 'unchanged')} unchanged).")
            return 0
        async with conn.transaction():
            await _ctx(conn, org)
            op = await conn.fetchval("SELECT id FROM users WHERE lower(email) = lower($1) AND org_id = $2::uuid "
                                     "AND COALESCE(status, 'active') = 'active'", email, org)
        if op is None:
            print(f"no active users row for {email!r} in the Olivo org")
            return 1
        n = {"create": 0, "revise": 0, "unchanged": 0}
        async with conn.transaction():
            await _ctx(conn, org)
            for act, p, cur in actions:
                n[act] += 1
                if act == "create":
                    row = await OB.create(conn, org_id=org, actor_id=op, data=p["data"], today=today,
                                          external_key=p["key"], reason="loaded by load_olivo_obligations.py")
                elif act == "revise":
                    ch = {k: v for k, v in p["data"].items() if k != "entity_id"}
                    row = await OB.revise(conn, org_id=org, actor_id=op, obligation_id=cur["id"], changes=ch,
                                          today=today, reason="reloaded from obligations.csv")
                else:
                    continue
                await conn.execute("INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, "
                                   "payload) VALUES ($1::uuid, $2, $3, 'cash_obligation', $4::uuid, $5::jsonb)",
                                   org, op, f"cash.obligation.{'created' if act == 'create' else 'revised'}",
                                   row["lineage_id"], json.dumps({"external_key": p["key"], "loader": True}))
        print(f"WROTE: {n}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
