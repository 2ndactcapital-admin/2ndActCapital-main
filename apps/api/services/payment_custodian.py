"""cpsim04 — the custodian-API route for PAYMENT instructions: who may take it (D4), "Send to custodian" (D5),
"Refresh status from custodian" (D7), the transfer-webhook to-do, the payment page's panel, and the bank-crosswalk
helpers (D6). Contract: docs/cpsim/ALTRUIST_CONTRACT.md §9 (every field ASSUMED); notes: docs/cpsim/CPSIM04_NOTES.md.

A PERSON DECIDES EVERY STEP THAT MATTERS:
  * a person drafts and submits, a DIFFERENT person approves (unchanged: services.payments — the S30 floor);
  * a person presses "Send to custodian" (:func:`send_to_custodian`, initiate_payment): the SAME checks
    ``payments.mark_sent`` runs (the rail, the value date, BOTH authority checks) run BEFORE anything is sent, then the
    transfer is POSTed and the instruction moves to ``sent`` through the EXISTING ``mark_sent`` (channel
    ``custodian_api``, adapter ``altruist_simulator``, external_ref = the custodian's transfer id);
  * a person presses "Refresh status from custodian" (:func:`refresh_from_custodian`, initiate_payment or
    approve_payment): each NEW custodian status is applied through the EXISTING person function — consent_requested,
    acknowledge, settle (the custodian's settle date), custodian_reject (its reason), record_return (its code) — with
    the refreshing person as the actor, one graph edge at a time, never re-applied, never skipped;
  * reconciling against the synced feed is the EXISTING ``payments.reconcile`` (custodian_data) — unchanged.
Neither action is registered as an assistant / workflow action, a trigger, a sweep or an unattended (S29b) verb:
``cash.payment_send`` and ``cash.payment_refresh`` are declared NEVER (money) in services.workflow_autonomy and NOT
registered. A ``transfer.updated`` webhook only raises a to-do for the sender.

ONLY A SIMULATED COUNTERPARTY. Services talk to it over HTTP (services/altruist_transfers.py) and never read the
simulator's schema. A real-Altruist-crosswalked account NEVER takes this route.

D4 — a payment goes to the simulator only when ALL hold (:func:`simulator_route`; the first one missing is the reason,
and the payment then takes today's route exactly — manual, with the reason shown):
  1. the SOURCE is NOT crosswalked ``ALTRUIST`` (a real Altruist account never moves money through a simulator);
  2. it IS crosswalked ``ALTRUIST_SIMULATOR`` (cpsim02 D5);
  3. its org's ``counterparty_sim.allowed`` is on and names a counterparty;
  4. its org has an active ``simulator`` connection;
  5. ``counterparty_sim_may_connect(org, household)`` — a test org freely, a real org's demo household only;
  6. the org setting ``cash.payment_adapters`` contains ``altruist_simulator`` (a NEW key — never ``altruist``);
  7. ``payment_status_feed`` of the simulator's capability code is an evidenced 'yes';
  8. the chosen rail (ach / wire / journal — check stays manual) is an evidenced 'yes' for that code;
  9. the DESTINATION is crosswalked to the same simulated custodian (rail journal) or to a simulated BANK (source
     ``CPSIM_BANK``; ACH or wire to a domestic bank, wire to an international one).
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

from services import payment_adapters as PA

SIMULATOR_KEY = PA.SIMULATOR_KEY
SIMULATOR_CAPABILITY_CUSTODIAN = "altruist_simulator"
CHANNEL = PA.CUSTODIAN_API
ENVIRONMENT = "simulator"
XWALK_SIMULATOR = "ALTRUIST_SIMULATOR"
XWALK_REAL = "ALTRUIST"
XWALK_BANK = "CPSIM_BANK"
STATUS_KEY = "payment_status_feed"
TRANSFER_RAILS = ("ach", "wire", "journal")
TODO_SOURCE = "payment_custodian_status"
TRANSFER_EVENT = "transfer.updated"
REFRESHABLE = ("sent", "awaiting_client_consent", "acknowledged", "settled", "reconciled")
#: D4 conditions, in the order they are checked (the verify removes each one in turn)
CONDITIONS = ("real_crosswalk", "crosswalk", "allowed", "connection", "demo_household", "adapter_enabled",
              "status_feed", "rail", "destination")
_BANK_REF_RX = re.compile(r"^(?:a(?P<ach4>[0-9]{4})w(?P<wire4>[0-9]{4})|i(?P<iban4>[A-Z0-9]{4})b(?P<bic>[A-Z0-9]{8}"
                          r"(?:[A-Z0-9]{3})?))$")
_override: dict = {"capability_custodian": None}


# ── the capability code (a verify substitutes a FIXTURE code; read at call time) ───────────────────────────────────
def set_capability_custodian(code: str | None) -> None:
    _override["capability_custodian"] = code


def capability_code(key: str) -> str:
    """The custodian_capabilities code a payment custodian key reads: the simulator's code for the simulator key."""
    if key == SIMULATOR_KEY:
        return _override["capability_custodian"] or SIMULATOR_CAPABILITY_CUSTODIAN
    return key


# ── crosswalks ──────────────────────────────────────────────────────────────────────────────────────────────────────
async def payment_custodian_keys(conn, org_id, account_ids) -> dict[str, str]:
    """{account id: 'altruist_simulator'} for the accounts crosswalked to a SIMULATED custodian and NOT to real
    Altruist — ONE query for any number of accounts (a list never runs N+1)."""
    ids = sorted({str(i) for i in account_ids if i})
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT record_id::text AS id, bool_or(source_system = $3) AS sim, bool_or(source_system = $4) AS real "
        "FROM portfolio.external_references WHERE org_id = $1::uuid AND record_type = 'account' AND record_id = "
        "ANY($2::uuid[]) AND source_system IN ($3, $4) GROUP BY record_id", str(org_id), ids, XWALK_SIMULATOR, XWALK_REAL)
    return {r["id"]: SIMULATOR_KEY for r in rows if r["sim"] and not r["real"]}


async def _xwalk(conn, org_id, account_id, source: str) -> str | None:
    return await conn.fetchval(
        "SELECT external_id FROM portfolio.external_references WHERE org_id = $1::uuid AND source_system = $2 "
        "AND record_type = 'account' AND record_id = $3::uuid ORDER BY last_seen DESC LIMIT 1",
        str(org_id), source, str(account_id))


def bank_xwalk_id(slug: str, bank_account_id: str, refs: dict) -> str:
    """``<bank slug>:<bank account id>:<references>`` — LAST FOUR digits only (a1234w5678 = the ACH / wire routing
    numbers' last four; i1234b<BIC> = the IBAN's last four and the fake BIC). Never a full number."""
    if refs.get("bic"):
        ref = f"i{refs['iban4']}b{refs['bic']}"
    else:
        ref = f"a{refs['ach4']}w{refs['wire4']}"
    if not _BANK_REF_RX.match(ref):
        raise ValueError("bank references must be last-four digits (and a BIC)")
    return f"{slug}:{bank_account_id}:{ref}"


def parse_bank_xwalk(external_id: str | None) -> dict | None:
    if not external_id or external_id.count(":") < 2:
        return None
    slug, rest = external_id.split(":", 1)
    acct, ref = rest.rsplit(":", 1)
    m = _BANK_REF_RX.match(ref)
    if not m or not slug or not acct:
        return None
    return {"slug": slug, "account_id": acct, **{k: v for k, v in m.groupdict().items() if v}}


def mask_refs(refs: dict) -> dict:
    """What a screen may show of a bank's references: last four digits (they already are) and the BIC's last four."""
    out = {}
    for k, label in (("ach4", "ach_routing_last4"), ("wire4", "wire_routing_last4"), ("iban4", "iban_last4")):
        if refs.get(k):
            out[label] = refs[k]
    if refs.get("bic"):
        out["bic_last4"] = refs["bic"][-4:]
    return out


async def bank_links(conn, slug: str) -> list[dict]:
    """Every platform account linked to a book account of the bank ``slug`` (Super Admin read; masks only)."""
    rows = await conn.fetch(
        "SELECT x.org_id, x.record_id, x.external_id, x.last_seen, a.account_number_masked, o.name AS org_name "
        "FROM portfolio.external_references x LEFT JOIN public.accounts a ON a.id = x.record_id AND a.system_to IS NULL "
        "AND a.valid_to IS NULL LEFT JOIN public.organizations o ON o.id = x.org_id WHERE x.source_system = $1 AND "
        "x.record_type = 'account' AND x.external_id LIKE $2 || ':%' ORDER BY x.last_seen DESC", XWALK_BANK, slug)
    out = []
    for r in rows:
        p = parse_bank_xwalk(r["external_id"]) or {}
        out.append({"org_id": str(r["org_id"]), "org_name": r["org_name"], "account_id": str(r["record_id"]),
                    "account_number_masked": r["account_number_masked"], "bank_account_id": p.get("account_id"),
                    "references": mask_refs(p), "linked_at": r["last_seen"]})
    return out


# ── D4 ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _refused(condition: str, reason: str) -> dict:
    return {"ok": False, "condition": condition, "reason": reason, "slug": None, "from_account_id": None,
            "destination": None}


async def destination_of(conn, org_id, account_id, slug: str | None, rail: str) -> tuple[dict | None, str | None]:
    """(destination for the transfer body, None) or (None, the reason it cannot go to the custodian)."""
    if await _xwalk(conn, org_id, account_id, XWALK_REAL):
        return None, "the destination is crosswalked to real Altruist — never through a simulated custodian"
    sim = await _xwalk(conn, org_id, account_id, XWALK_SIMULATOR)
    if sim:
        if rail != "journal":
            return None, ("the destination is at the same simulated custodian — that is a journal (choose the "
                          "journal rail)")
        return {"to_account_id": sim}, None
    bank = parse_bank_xwalk(await _xwalk(conn, org_id, account_id, XWALK_BANK))
    if bank is None:
        return None, ("the destination is not crosswalked to the simulated custodian or to a simulated bank — the "
                      "simulator can only move money to an account it knows")
    if rail == "journal":
        return None, "a journal moves money between accounts at the same custodian; the destination is at a bank"
    if bank.get("bic"):
        if rail != "wire":
            return None, "an international bank receives wires only"
        return {"to_bank": {"institution_slug": bank["slug"], "account_id": bank["account_id"], "bic": bank["bic"],
                            "iban_last4": bank["iban4"]}}, None
    return {"to_bank": {"institution_slug": bank["slug"], "account_id": bank["account_id"],
                        "routing_last4": bank["ach4"] if rail == "ach" else bank["wire4"]}}, None


async def simulator_route(conn, org_id, src: dict, row: dict) -> dict:
    """D4 — ``{ok, condition, reason, slug, from_account_id, destination}``. Reads public tables only (the crosswalks,
    the org settings, the connection, the public may-connect wrapper, the capability matrix)."""
    from services.altruist_oauth import AltruistSimulatorRefused, get_active_connection, simulator_counterparty
    from services.custodian_capabilities import available, capability

    if await _xwalk(conn, org_id, src["id"], XWALK_REAL):
        return _refused("real_crosswalk", "the source account is crosswalked to real Altruist — the real Altruist "
                                          "payment route stays manual (no published money-movement contract)")
    ext = await _xwalk(conn, org_id, src["id"], XWALK_SIMULATOR)
    if not ext:
        return _refused("crosswalk", "the source account is not crosswalked to a simulated custodian")
    try:
        slug = await simulator_counterparty(conn, org_id=str(org_id))
    except AltruistSimulatorRefused as exc:
        return _refused("allowed", str(exc))
    c = await get_active_connection(conn, org_id=str(org_id), environment=ENVIRONMENT)
    if c is None or c.get("status") != "connected":
        return _refused("connection", "the organization has no active connection to its simulated custodian")
    may = await conn.fetchval("SELECT public.counterparty_sim_may_connect($1::uuid, $2::uuid)", str(org_id),
                              str(src["household_id"]) if src.get("household_id") else None)
    if not may:
        return _refused("demo_household", "not a demo household: in a real organization only a household tagged demo "
                                          "may use a simulated custodian")
    if SIMULATOR_KEY not in await PA.enabled_for_org(conn, org_id):
        return _refused("adapter_enabled", f"the organization has not enabled the simulated custodian's payment "
                                           f"adapter ({PA.ORG_SETTING} does not list {SIMULATOR_KEY})")
    code = capability_code(SIMULATOR_KEY)
    cap = await capability(conn, code, STATUS_KEY)
    if not available(cap):
        return _refused("status_feed", f"{code} {STATUS_KEY} is '{cap.value}' — unknown is treated as not available")
    rail = row.get("rail")
    if rail not in TRANSFER_RAILS:
        return _refused("rail", f"the {rail} rail is carried out manually (the transfers contract has ACH, wire and "
                                "journal)")
    cap = await capability(conn, code, rail)
    if not available(cap):
        return _refused("rail", f"{code} {rail} is '{cap.value}' — only an evidenced rail goes to the custodian API")
    dest, why = await destination_of(conn, org_id, row["destination_account_id"], slug, rail)
    if why:
        return _refused("destination", why)
    return {"ok": True, "condition": None, "reason": f"simulated custodian {slug}", "slug": slug,
            "from_account_id": ext, "destination": dest}


def transfer_body(row: dict, route: dict) -> dict:
    """The POST /v2/transfers body (contract §9.2): the amount as a 2-dp STRING; client_transfer_id = the payment id."""
    return {"client_transfer_id": str(row["id"]), "from_account_id": route["from_account_id"],
            **route["destination"], "amount": format(row["amount"], "f") if not isinstance(row["amount"], str)
            else row["amount"], "currency": "USD", "rail": row["rail"], "value_date": row["value_date"].isoformat()}


def _api_errors() -> tuple:
    from services.altruist_oauth import (AltruistConfigError, AltruistNotConnected, AltruistOAuthError,
                                         AltruistSimulatorRefused)
    return (AltruistOAuthError, AltruistNotConnected, AltruistSimulatorRefused, AltruistConfigError)


def _api_error(exc: Exception):
    from services import altruist_transfers as AT
    from services import payments as P

    if isinstance(exc, AT.AltruistTransferRefused):
        return P.PaymentConflict(f"the simulated custodian refused the request (HTTP {exc.status})"
                                 + (f": {exc.detail}" if exc.detail else ""))
    return P.PaymentConflict(f"the simulated custodian could not be reached: {exc}")


# ── D5: Send to custodian ───────────────────────────────────────────────────────────────────────────────────────────
async def send_to_custodian(pool, *, org_id, actor_id, payment_id, transport: Any = None) -> dict:
    """An APPROVED payment whose D4 route holds → POST /v2/transfers → the EXISTING mark_sent (channel custodian_api,
    adapter altruist_simulator, external_ref = the transfer id). Every mark_sent check runs BEFORE the transfer is sent
    (and again inside mark_sent). The connection is released before the HTTP call."""
    from services import altruist_transfers as AT
    from services import payments as P

    async with pool.acquire() as conn:
        actor = await P._human(conn, actor_id, org_id)
        row = await P.load(conn, org_id, payment_id)
        P._need(row, ("approved",), "send to the custodian")
        t = P.today()
        src = await P.load_account(conn, org_id, row["source_account_id"], t)
        route = await simulator_route(conn, org_id, src, row)
        if not route["ok"]:
            await P._refused(org_id, actor["id"], "send a payment to the custodian", [route["reason"]], row["id"])
            raise P.PaymentConflict(f"the custodian route is not available: {route['reason']} — use the manual route "
                                    "(download the payment sheet, send it and confirm the channel)")
        reasons = [x for x in (await P.rail_refusal(conn, src, row["rail"]), P.value_date_refusal(row["value_date"], t))
                   if x]
        reasons += await P.authority_refusals(pool, conn, org_id, actor["id"], src)
        if reasons:
            await P._refused(org_id, actor["id"], "send a payment to the custodian", reasons, row["id"])
            raise P.PaymentAuthorityError(reasons)
        body = transfer_body(row, route)
        try:
            session = await AT.transfer_session(conn, org_id=str(org_id))
        except _api_errors() as exc:
            raise _api_error(exc) from exc
    # the connection (and the transaction the RLS pool opened on it) is released BEFORE the HTTP call
    adapter = PA.AltruistSimulatorPaymentAdapter(route)
    try:
        res = await adapter.send(row, session=session, body=body, transport=transport)
    except _api_errors() as exc:
        raise _api_error(exc) from exc
    transfer = getattr(res, "transfer", {}) or {}
    if transfer.get("client_transfer_id") != str(row["id"]) or not res.reference:
        raise P.PaymentConflict("the simulated custodian answered with a transfer that is not this instruction's")
    new = await P.mark_sent(pool, org_id=org_id, actor_id=actor["id"], payment_id=row["id"], channel=CHANNEL,
                            note=f"transfer {res.reference} at simulated custodian {route['slug']} — "
                                 f"{transfer.get('status')} (SIMULATED)",
                            external_ref=res.reference, adapter=SIMULATOR_KEY, via_custodian=True)
    return {"payment": new, "transfer": transfer, "http_status": getattr(res, "http_status", None),
            "counterparty": route["slug"], "simulated": True}


# ── D7: Refresh status from custodian ───────────────────────────────────────────────────────────────────────────────
def next_step(status: str, transfer: dict, today_: date) -> tuple[str | None, dict, str | None]:
    """The ONE next edge to apply for a payment in ``status`` given the custodian's transfer: (action, kwargs, note).
    None when nothing (more) applies — the note says why when something is pending."""
    seen = [e.get("status") for e in (transfer.get("events") or [])]
    cst = transfer.get("status")
    if status == "sent" and "pending_consent" in seen:
        return "consent_requested", {"note": "the custodian asked for the member's consent (SIMULATED)"}, None
    if status in ("sent", "awaiting_client_consent") and "acknowledged" in seen:
        return "acknowledge", {"external_ref": transfer.get("transfer_id"),
                               "note": "acknowledged by the simulated custodian"}, None
    if status in ("sent", "awaiting_client_consent", "acknowledged") and cst == "rejected":
        return "custodian_reject", {"reason": transfer.get("reject_reason") or "rejected by the custodian"}, None
    if status == "acknowledged" and "settled" in seen:
        on = date.fromisoformat(transfer["settled_on"]) if transfer.get("settled_on") else None
        if on is None:
            return None, {}, "the custodian reports the transfer settled without a settlement date"
        if on > today_:
            return None, {}, (f"the custodian settled on {on.isoformat()}, after today — refresh again on or after that "
                              "date")
        return "settle", {"settled_on": on, "note": "settled at the simulated custodian"}, None
    if status in ("settled", "reconciled") and cst == "returned":
        return "record_return", {"return_code": transfer.get("return_code") or "",
                                 "reason": transfer.get("return_reason")}, None
    if cst == "cancelled" and status in ("sent", "awaiting_client_consent"):
        return None, {}, "the custodian reports the transfer cancelled — cancel the payment instruction"
    return None, {}, None


async def refresh_from_custodian(pool, *, org_id, actor_id, payment_id, transport: Any = None) -> dict:
    """GET the transfer; apply each NEW custodian status in graph order through the EXISTING person functions, the
    refreshing person the actor of every step (a payment_events row each, written by the database). Idempotent: a
    repeat applies nothing; a state is never jumped."""
    from services import altruist_transfers as AT
    from services import payments as P

    async with pool.acquire() as conn:
        actor = await P._human(conn, actor_id, org_id)
        row = await P.load(conn, org_id, payment_id)
        P._need(row, REFRESHABLE, "refresh the custodian status of")
        if row["adapter"] != SIMULATOR_KEY or row["sent_channel"] != CHANNEL or not row["external_ref"]:
            raise P.PaymentConflict("this payment was not sent to a custodian API — confirm its status by hand")
        try:
            session = await AT.transfer_session(conn, org_id=str(org_id))
        except _api_errors() as exc:
            raise _api_error(exc) from exc
    adapter = PA.AltruistSimulatorPaymentAdapter()
    try:
        st = await adapter.status(row["external_ref"], session=session, transport=transport)
    except _api_errors() as exc:
        raise _api_error(exc) from exc
    transfer = st.detail or {}
    if transfer.get("client_transfer_id") != str(row["id"]) or transfer.get("transfer_id") != row["external_ref"]:
        raise P.PaymentConflict("the custodian's transfer is not this instruction's")
    calls = {"consent_requested": P.consent_requested, "acknowledge": P.acknowledge, "settle": P.settle,
             "custodian_reject": P.custodian_reject, "record_return": P.record_return}
    applied, pending, status = [], None, row["status"]
    for _ in range(len(calls) + 1):
        action, kw, note = next_step(status, transfer, P.today())
        if action is None:
            pending = note
            break
        new = await calls[action](pool, org_id=org_id, actor_id=actor["id"], payment_id=row["id"], **kw)
        applied.append({"action": action, "from": status, "to": new["status"]})
        status = new["status"]
    async with pool.acquire() as conn:
        cur = await P.load(conn, org_id, row["id"])
    return {"payment": cur, "transfer": transfer, "applied": applied, "pending": pending, "simulated": True}


# ── the webhook: a to-do for the sender, never a status ────────────────────────────────────────────────────────────
async def transfer_event_todo(conn, *, org_id, payload: dict) -> dict | None:
    """For a ``transfer.updated`` event signed by the simulator: the payment named by client_transfer_id (this org's,
    sent to the custodian with this transfer id) gets a to-do for its sender. Nothing is applied."""
    if str(payload.get("event_type") or "") != TRANSFER_EVENT:
        return None
    try:
        pid = str(uuid.UUID(str(payload.get("client_transfer_id"))))
    except (TypeError, ValueError):
        return {"todo": None, "reason": "client_transfer_id is not a payment instruction id"}
    row = await conn.fetchrow(
        "SELECT id, household_id, sent_by, status, external_ref, adapter FROM public.payment_instructions WHERE id = "
        "$1::uuid AND org_id = $2::uuid AND system_to IS NULL", pid, str(org_id))
    if row is None or row["adapter"] != SIMULATOR_KEY or row["external_ref"] != payload.get("transfer_id"):
        return {"todo": None, "reason": "no payment of this organization carries that transfer"}
    if row["status"] not in REFRESHABLE or not row["sent_by"]:
        return {"todo": None, "reason": f"the payment is {row['status']}"}
    if await conn.fetchval("SELECT 1 FROM member_todos WHERE org_id = $1::uuid AND user_id = $2::uuid AND source = $3 "
                           "AND related_id = $4::uuid AND status = 'open'", str(org_id), str(row["sent_by"]),
                           TODO_SOURCE, pid):
        return {"todo": None, "reason": "an open to-do already asks for a refresh", "payment_id": pid}
    tid = await conn.fetchval(
        "INSERT INTO member_todos (org_id, user_id, kind, category, source, related_type, related_id, title, detail, "
        "action_key, priority, status) VALUES ($1::uuid, $2::uuid, 'actual', 'payments', $3, 'payment_instruction', "
        "$4::uuid, $5, $6, $7, 15, 'open') RETURNING id", str(org_id), str(row["sent_by"]), TODO_SOURCE, pid,
        "Custodian status changed — refresh", f"The simulated custodian reports a status change for transfer "
        f"{payload.get('transfer_id')}. Open the payment and press Refresh status from custodian (nothing is applied "
        "until you do).", f"/households/{row['household_id']}?tab=treasury&payment={pid}")
    return {"todo": str(tid), "payment_id": pid}


# ── the payment page ────────────────────────────────────────────────────────────────────────────────────────────────
async def panel(conn, org_id, row: dict) -> dict | None:
    """What the payment page shows about the custodian route (no network): whether Send is available now (D4, re-read)
    and why not; whether Refresh applies. None for a payment whose source is not at a simulated custodian."""
    from services import payments as P

    src = await P.load_account(conn, org_id, row["source_account_id"])
    sent_api = row.get("adapter") == SIMULATOR_KEY and row.get("sent_channel") == CHANNEL
    # CPSIM04 FIX 1: a source crosswalked to a simulated custodian AND to real Altruist is not the simulator's
    # (payment_custodian_keys), but the person must still see WHY it is manual (D4 "with the reason shown") — the
    # panel is shown with Send never available. A source with no simulator crosswalk: today's page exactly (None).
    if (PA.custodian_key(src) != SIMULATOR_KEY and not sent_api
            and not await _xwalk(conn, org_id, src["id"], XWALK_SIMULATOR)):
        return None
    out = {"simulated": True, "adapter": row.get("adapter"), "transfer_id": row.get("external_ref") if sent_api else None,
           "send_available": False, "send_refused": None, "refresh_available": False, "counterparty": None,
           "manual_offered": True}
    if row["status"] == "approved":
        r = await simulator_route(conn, org_id, src, row)
        out["counterparty"] = r["slug"]
        out["send_available"] = bool(r["ok"])
        out["send_refused"] = None if r["ok"] else f"manual: {r['reason']}"
    out["refresh_available"] = sent_api and row["status"] in REFRESHABLE
    return out


async def transfer_view(pool, *, org_id, payment_id, transport: Any = None) -> dict:
    """The live custodian transfer of a payment and the synced feed rows the reconcile picker preselects (matches()
    whose custodian reference starts with the transfer id)."""
    from services import altruist_transfers as AT
    from services import payments as P

    async with pool.acquire() as conn:
        row = await P.load(conn, org_id, payment_id)
        if row["adapter"] != SIMULATOR_KEY or not row["external_ref"]:
            raise P.PaymentConflict("this payment was not sent to a custodian API")
        found = await P.matches(conn, org_id, row)
        try:
            session = await AT.transfer_session(conn, org_id=str(org_id))
        except _api_errors() as exc:
            raise _api_error(exc) from exc
    try:
        t = await AT.fetch_with_session(session, transfer_id=row["external_ref"], transport=transport)
    except _api_errors() as exc:
        raise _api_error(exc) from exc
    pre = [m["id"] for m in found if (m.get("external_ref") or "").startswith(row["external_ref"] + "-")]
    return {"transfer": t, "matches": found, "preselect_transaction_ids": pre, "simulated": True,
            "reconcile_reference": row["external_ref"]}
