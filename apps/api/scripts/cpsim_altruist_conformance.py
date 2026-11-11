"""cpsim02 D11 — run the Altruist-compatible CONFORMANCE SUITE (services/altruist_conformance.py) against a target.

    python3 apps/api/scripts/cpsim_altruist_conformance.py --target simulator
        In-process (default): a fixture simulated counterparty (slug prefix FIXTURE_PREFIX) with a fixture book is
        created, the suite runs through an in-process ASGI transport (no network — asserted), the fixture is torn
        down. Needs the database (Doppler) only.
    python3 apps/api/scripts/cpsim_altruist_conformance.py --target simulator --slug <slug> --base-url
        Against ``CPSIM_BASE_URL`` + /cpsim/c/<slug> over HTTPS, with ALTRUIST_SIMULATOR_CLIENT_ID / _CLIENT_SECRET.
    python3 apps/api/scripts/cpsim_altruist_conformance.py --target sandbox --i-have-sandbox-access
        Against Altruist's real sandbox. REFUSES TO START unless ALTRUIST_SANDBOX_CLIENT_ID / _CLIENT_SECRET are set
        AND the flag is passed. (Never run in cpsim02 — no sandbox access exists.)

Output: a table on stdout + JSON at logs/cpsim_conformance_<date>.json (``--json-out`` overrides). Exit 0 only when
every check passes; 1 on any failure; 2 when it refused to start. Never prints a key, a token or a signature.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import secrets
import socket
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
for _p in (str(HERE), str(API_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_site) not in sys.path:
        sys.path.insert(0, str(_site))

import _cpsim_fixtures as FX  # noqa: E402

FIXTURE_PREFIX = "cpsimconf-"
IN_PROCESS_BASE = "https://cpsim.conformance.invalid"
REFUSED = 2


def _refuse(msg: str) -> int:
    print(f"REFUSED: {msg}")
    return REFUSED


class _NoNetwork:
    """In-process mode: any real socket connect is a bug (the database pool is the only allowed peer)."""

    def __init__(self, allow_hosts: set[str]):
        self.allow = allow_hosts
        self.attempts: list[str] = []
        self._orig = socket.socket.connect

    def __enter__(self):
        guard = self

        def connect(sock, address):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in guard.allow:
                guard.attempts.append(str(host))
                raise ConnectionRefusedError(f"network disabled in-process: {host}")
            return guard._orig(sock, address)
        socket.socket.connect = connect
        return self

    def __exit__(self, *a):
        socket.socket.connect = self._orig


def fixture_book(security: dict | None, as_of: date) -> dict:
    hh, acct = "conf-hh-1", "conf-acct-1"
    pos = [{"position_id": "conf-pos-cash", "account_id": acct, "as_of": as_of.isoformat(), "row_type": "cash",
            "name": "Cash", "market_value": "1250.00", "currency_code": "USD"},
           # cpsim04 — the journal's destination: a second account of the same household, its own cash row
           {"position_id": "conf-pos-cash2", "account_id": "conf-acct-2", "as_of": as_of.isoformat(), "row_type": "cash",
            "name": "Cash", "market_value": "0.00", "currency_code": "USD"}]
    txn = [{"transaction_id": "conf-txn-int", "account_id": acct, "type": "interest",
            "trade_date": as_of.isoformat(), "row_type": "cash", "name": "Cash", "gross_amount": "1.25",
            "net_amount": "1.25", "currency_code": "USD"}]
    lots = []
    if security:
        pos.append({"position_id": "conf-pos-sec", "account_id": acct, "as_of": as_of.isoformat(),
                    "row_type": "security", security["id_type"]: security["id_value"], "quantity": "10",
                    "market_value": "1000.00", "cost_basis": "900.00", "currency_code": "USD"})
        # ALTREAD1 — two uploaded tax lots for the security (the cost-basis route serves them)
        lots = [{"lot_id": "conf-lot-1", "account_id": acct, "as_of": as_of.isoformat(),
                 security["id_type"]: security["id_value"], "quantity": "6", "market_value": "600.00",
                 "cost_basis_unadjusted": "500.00", "cost_basis_adjusted": "510.00",
                 "open_date": (as_of - timedelta(days=730)).isoformat(), "currency_code": "USD"},
                {"lot_id": "conf-lot-2", "account_id": acct, "as_of": as_of.isoformat(),
                 security["id_type"]: security["id_value"], "quantity": "4", "market_value": "400.00",
                 "cost_basis_unadjusted": "390.00", "cost_basis_adjusted": "390.00",
                 "open_date": as_of.isoformat(), "currency_code": "USD"}]
        txn.append({"transaction_id": "conf-txn-buy", "account_id": acct, "type": "buy",
                    "trade_date": as_of.isoformat(), "row_type": "security", security["id_type"]: security["id_value"],
                    "quantity": "10", "price": "100", "gross_amount": "1000", "net_amount": "1000",
                    "currency_code": "USD"})
    return {"households": [{"household_id": hh, "name": "Conformance household"},
                           # ALTREAD1 — a second household so the paging check (C10) walks more than one page
                           {"household_id": "conf-hh-2", "name": "Conformance household two"}],
            "accounts": [{"account_id": acct, "household_id": hh, "name": "Conformance account"},
                         {"account_id": "conf-acct-2", "household_id": hh, "name": "Conformance account 2"}],
            "positions": pos, "transactions": txn, "lots": lots}


async def _pick_security(conn, as_of: date) -> dict | None:
    """One live security with a CUSIP that resolves uniquely (read at run time, never frozen). cpsim03: an equity / fund
    with a platform close on or before the clock date, so the orders group can trade it (§8.5)."""
    r = await conn.fetchrow(
        "SELECT i.id_type, i.id_value FROM portfolio.securities_global_identifiers i JOIN portfolio.securities_global s "
        "ON s.id = i.global_security_id WHERE i.id_type = 'cusip' AND i.valid_to IS NULL AND i.system_to IS NULL AND "
        "s.valid_to IS NULL AND s.system_to IS NULL AND s.merged_into_id IS NULL AND s.security_type IN ('equity', "
        "'fund') AND (SELECT count(*) FROM portfolio.securities_global_identifiers j WHERE j.id_type = 'cusip' AND "
        "upper(j.id_value) = upper(i.id_value) AND j.valid_to IS NULL AND j.system_to IS NULL) = 1 AND EXISTS ("
        "SELECT 1 FROM portfolio.securities_global_prices p WHERE p.global_security_id = s.id AND p.org_id IS NULL "
        "AND p.price_type = 'close' AND p.price_date <= $1 AND p.valid_to IS NULL AND p.system_to IS NULL) LIMIT 1",
        as_of)
    return dict(r) if r else None


def webhook_signing_check() -> dict:
    """§6 item 7 / T8 — the simulator's signature verifies with the receiver's own code, forcing the simulator."""
    from cpsim import webhooks as W
    from services import altruist_webhook as AW

    key = "cpsim_whsec_" + secrets.token_hex(20)
    saved = {k: os.environ.get(k) for k in (AW.SIMULATOR_SECRET_ENV_VAR, AW.SECRET_ENV_VAR)}
    try:
        os.environ[AW.SIMULATOR_SECRET_ENV_VAR] = key
        os.environ[AW.SECRET_ENV_VAR] = "real-" + secrets.token_hex(16)
        body = W.event_body("account.positions_updated", "conf-acct-1")
        sig = W.sign(key, body)
        doc = json.loads(body)
        ok = (AW.verify_signature_source(body, sig) == AW.SIGNED_SIMULATOR and len(sig) == 64 and sig == sig.lower()
              and {"event_id", "event_type", "account_id", "environment"} <= set(doc)
              and doc["environment"] == "simulator")
        detail = "" if ok else "signature or body shape did not verify"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"id": "C19", "contract": "§6.7 / T8", "label": "webhook: lowercase hex HMAC-SHA256 over the raw body, "
            "verified by the receiver's own verify_signature_source, body {event_id, event_type, account_id, "
            "environment}", "passed": ok, "detail": detail}


def order_webhook_check() -> dict:
    """cpsim03 §8.4 — an ORDER event is signed the same way and names the order (D9)."""
    from cpsim import webhooks as W
    from services import altruist_webhook as AW

    key = "cpsim_whsec_" + secrets.token_hex(20)
    saved = {k: os.environ.get(k) for k in (AW.SIMULATOR_SECRET_ENV_VAR, AW.SECRET_ENV_VAR)}
    try:
        os.environ[AW.SIMULATOR_SECRET_ENV_VAR] = key
        os.environ[AW.SECRET_ENV_VAR] = "real-" + secrets.token_hex(16)
        body = W.event_body("order.filled", "conf-acct-1", order={"order_id": "sim-o-" + "0" * 16,
                                                                  "client_order_id": "conf-order"})
        doc = json.loads(body)
        ok = (AW.verify_signature_source(body, W.sign(key, body)) == AW.SIGNED_SIMULATOR
              and list(doc) == ["event_id", "event_type", "order_id", "client_order_id", "account_id", "environment",
                                "occurred_at"] and doc["environment"] == "simulator")
        detail = "" if ok else f"keys {list(doc)}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"id": "O14", "contract": "ASSUMED (not published — Realtime / FIX) §8.4", "label": "order webhook: {event_id, event_type, order_id, client_order_id, "
            "account_id, environment, occurred_at}, signed and verified as §6.7 (forces the simulator)", "passed": ok,
            "detail": detail}


def transfer_webhook_check() -> dict:
    """cpsim04 §9.5 — a TRANSFER event is signed the same way and names the transfer (D7)."""
    from cpsim import webhooks as W
    from services import altruist_webhook as AW

    key = "cpsim_whsec_" + secrets.token_hex(20)
    saved = {k: os.environ.get(k) for k in (AW.SIMULATOR_SECRET_ENV_VAR, AW.SECRET_ENV_VAR)}
    try:
        os.environ[AW.SIMULATOR_SECRET_ENV_VAR] = key
        os.environ[AW.SECRET_ENV_VAR] = "real-" + secrets.token_hex(16)
        body = W.event_body("transfer.updated", "conf-acct-1", transfer={"transfer_id": "sim-t-" + "0" * 16,
                                                                        "client_transfer_id": "conf-transfer"})
        doc = json.loads(body)
        ok = (AW.verify_signature_source(body, W.sign(key, body)) == AW.SIGNED_SIMULATOR
              and list(doc) == ["event_id", "event_type", "transfer_id", "client_transfer_id", "account_id",
                                "environment", "occurred_at"] and doc["environment"] == "simulator")
        detail = "" if ok else f"keys {list(doc)}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"{type(exc).__name__}"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"id": "T16", "contract": "ASSUMED (not published — Realtime / FIX) §9.5", "label": "transfer webhook: {event_id, event_type, transfer_id, "
            "client_transfer_id, account_id, environment, occurred_at}, signed and verified as §6.7 (forces the "
            "simulator)", "passed": ok, "detail": detail}


async def fixture_banks(conn, clock: date) -> dict:
    """cpsim04 — a domestic and an international simulated bank (slug prefix FIXTURE_PREFIX), each active with a book
    holding one cash account; returns their slugs, account ids and LAST-FOUR references (never a full number)."""
    from cpsim import registry as R
    from cpsim import transfers as ST

    out = {}
    for kind, typ in (("domestic", "domestic_bank"), ("international", "international_bank")):
        made = await R.create(conn, name=f"CPSIM CONFORMANCE {kind.upper()} BANK", institution_type=typ,
                              slug=FIXTURE_PREFIX + kind[:4] + "-" + secrets.token_hex(3), actor=None)
        await R.set_status(conn, made["id"], "active", None)
        acct = f"conf-bank-{kind[:4]}"
        await FX.plant_book(conn, made["id"], {
            "households": [{"household_id": "conf-bank-hh", "name": "Bank customers"}],
            "accounts": [{"account_id": acct, "household_id": "conf-bank-hh", "name": f"{kind} customer"}],
            "positions": [{"position_id": f"{acct}-cash", "account_id": acct, "as_of": clock.isoformat(),
                           "row_type": "cash", "name": "Cash", "market_value": "0.00", "currency_code": "USD"}]})
        d = await conn.fetchrow("SELECT aba_wire, aba_ach, bic, iban FROM cpsim.counterparty_bank_details WHERE "
                                "counterparty_id = $1::uuid AND kind = $2 AND valid_to IS NULL", made["id"], kind)
        out[kind] = ({"slug": made["slug"], "account_id": acct, "ach4": ST.last4(d["aba_ach"]),
                      "wire4": ST.last4(d["aba_wire"])} if kind == "domestic" else
                     {"slug": made["slug"], "account_id": acct, "bic": d["bic"], "iban4": ST.last4(d["iban"])})
    return out


def report(out: dict, path: pathlib.Path) -> None:
    print(f"{'ID':4} {'RESULT':6} {'CONTRACT':28} CHECK")
    for r in out["results"]:
        res = "PASS" if r["passed"] is True else ("N/A" if r["passed"] is None else "FAIL")
        print(f"{r['id']:4} {res:6} {r['contract'][:28]:28} {r['label']}")
        if r["passed"] is False and r.get("detail"):
            print(f"{'':12}detail: {r['detail']}")
    print(f"CONFORMANCE ({out['target']}): {out['passed']} PASS, {out['failed']} FAIL")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"[json] {path}")


async def run_in_process(json_out: pathlib.Path, *, transform=None) -> dict:
    """The fixture run. ``transform`` (tests only) rewrites a response — the planted-divergence proof."""
    from _db_connect import hydrate

    from cpsim import db as SDB
    from services import altruist_conformance as AC
    from services import altruist_oauth as AO

    hydrate()
    os.environ[AO.CPSIM_BASE_URL_ENV] = IN_PROCESS_BASE
    slug = FIXTURE_PREFIX + secrets.token_hex(4)
    await FX.teardown(FIXTURE_PREFIX)
    try:
        async with SDB.tx() as conn:
            cp = await FX.create_counterparty(conn, name="CPSIM CONFORMANCE FIXTURE", slug=slug)
            clock = await conn.fetchval("SELECT business_date FROM cpsim.counterparty_clock WHERE counterparty_id = "
                                        "$1::uuid AND valid_to IS NULL", cp["id"])
            sec = await _pick_security(conn, clock)
            planted = await FX.plant_book(conn, cp["id"], fixture_book(sec, clock))
            banks = await fixture_banks(conn, clock)
        rejected = {k: v["counts"]["rejected"] for k, v in planted.items()}
        transport = FX.TimeoutASGITransport(app=FX.mounted_app())
        if transform is not None:
            transport = transform(transport)
        hosts = AO.simulator_hosts(slug)
        db_host = urlsplit(os.environ.get("DATABASE_URL", "")).hostname or ""
        allow = {db_host} | {ai[4][0] for ai in socket.getaddrinfo(db_host, None)} if db_host else set()
        with _NoNetwork(allow_hosts=allow) as guard:
            # the DB pool is already open (the fixture used it); every simulator call is in-process
            fx = {"account_id": "conf-acct-1", "cusip": sec["id_value"], "held": "10"} if sec else None
            tfx = {"from": "conf-acct-1", "to": "conf-acct-2", "cash": "1250.00", "value_date": clock.isoformat(),
                   **banks}
            out = await AC.run(environment=AO.SIMULATOR, hosts=hosts, credentials=(cp["client_id"], cp["key"]),
                               transport=transport, orders=fx, transfers=tfx, today=clock)
        if not sec:      # cpsim03 — the orders group needs a priced equity / fund: its absence is a failure, not a skip
            out["results"].append({"id": "O00", "contract": "§8 (orders)", "label": "orders group: a priced equity / "
                                   "fund security with a CUSIP exists for the fixture book", "passed": False,
                                   "detail": "none found in the security master"})
        out["results"].append(webhook_signing_check())
        out["results"].append(order_webhook_check())
        out["results"].append(transfer_webhook_check())
        out["results"].append({"id": "C20", "contract": "(harness)", "label": "no network: the run opened no socket "
                               "outside the database", "passed": not guard.attempts,
                               "detail": f"{guard.attempts[:3]}"})
        out["passed"] = sum(1 for r in out["results"] if r["passed"] is True)
        out["failed"] = sum(1 for r in out["results"] if r["passed"] is False)
        out.update({"target": "simulator (in-process fixture)", "fixture_rejected_rows": rejected,
                    "fixture_security": bool(sec), "run_at": datetime.now(timezone.utc).isoformat(),
                    "db_host_allowed": bool(db_host)})
    finally:
        await FX.teardown(FIXTURE_PREFIX)
        await SDB.close_pool()
    report(out, json_out)
    return out


async def run_network(target: str, slug: str | None, json_out: pathlib.Path) -> dict:
    from services import altruist_conformance as AC
    from services import altruist_oauth as AO

    if target == "sandbox":
        hosts, env = AO.ALTRUIST_HOSTS["sandbox"], "sandbox"
    else:
        hosts, env = AO.simulator_hosts(slug), AO.SIMULATOR
    creds = AO.client_credentials(env)
    out = await AC.run(environment=env, hosts=hosts, credentials=creds,
                       redirect_uri=os.environ.get("ALTRUIST_REDIRECT_URI") or AC.DEFAULT_REDIRECT)
    out.update({"target": target, "run_at": datetime.now(timezone.utc).isoformat()})
    report(out, json_out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", choices=("simulator", "sandbox"), required=True)
    ap.add_argument("--i-have-sandbox-access", action="store_true")
    ap.add_argument("--base-url", action="store_true", help="simulator over HTTPS at CPSIM_BASE_URL (needs --slug)")
    ap.add_argument("--slug")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)
    out_path = pathlib.Path(a.json_out) if a.json_out else REPO / "logs" / f"cpsim_conformance_{date.today():%Y%m%d}.json"
    if a.target == "sandbox":
        from services import altruist_conformance as AC

        print(f"orders group: skipped — {AC.orders_group_skip('sandbox', None)}")
        print(f"transfers group: skipped — {AC.transfers_group_skip('sandbox', None)}")
        if not a.i_have_sandbox_access:
            return _refuse("--target sandbox needs --i-have-sandbox-access (it calls Altruist's real sandbox)")
        if not (os.environ.get("ALTRUIST_SANDBOX_CLIENT_ID") and os.environ.get("ALTRUIST_SANDBOX_CLIENT_SECRET")):
            return _refuse("ALTRUIST_SANDBOX_CLIENT_ID / ALTRUIST_SANDBOX_CLIENT_SECRET are not set")
        out = asyncio.run(run_network("sandbox", None, out_path))
    elif a.base_url:
        if not a.slug:
            return _refuse("--base-url needs --slug")
        out = asyncio.run(run_network("simulator", a.slug, out_path))
    else:
        out = asyncio.run(run_in_process(out_path))
    return 0 if out["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
