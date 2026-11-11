"""cpsim04 — the simulated custodian's TRANSFERS, and the simulated BANKS' receive path (contract §9 — every field
ASSUMED; docs/cpsim/CPSIM04_NOTES.md D1 / D3 / D6 / D8).

Every function takes a connection already inside a transaction AS ``cpsim_app`` (cpsim.db.tx / as_simulator). Nothing
here touches a table outside ``cpsim``.

LIFECYCLE — decided by the behaviour rows and the SENDING counterparty's clock (C):
  received         at receipt. Behaviour ``reject`` → rejected with its reason; ``consent_required`` →
                   pending_consent; ``delay`` → stays received until the next clock advance; otherwise acknowledged
                   at once.
  pending_consent  until the admin presses "Record member consent" (:func:`record_consent`) → acknowledged.
  acknowledged     a BANK destination is checked by the receiving bank first (:func:`bank_check`: the bank is active,
                   its behaviour does not reject, the routing / BIC / IBAN reference matches its FAKE details, the
                   account is in its book) — a mismatch rejects with the bank's reason; then the source must hold the
                   amount in cash (else rejected "insufficient funds") and is debited (ledger, ``*_out``).
                   settle_on: journal and wire → the value date; ACH → the value date + 1 US business day.
  settled          when C ≥ settle_on: the destination is credited (the same custodian's account, or the receiving
                   bank's book account; ``*_in``). An ACH with a return code — the sender's behaviour first, else the
                   receiving bank's — is due ``ach_return_after_days`` business days after settlement.
  returned         when C ≥ the return due date: both legs reversed (``ach_return`` on each account).
  rejected / cancelled   final. Cancel (DELETE) only while received / pending_consent (nothing has moved).
Every step re-runs at each clock advance (:func:`advance`); a transfer never skips a state (each one is a
``transfer_events`` row) and a cash leg never moves twice (``cash_movements`` UNIQUE).

Bi-temporal: a transfer is SYSTEM axis (events and movements reference its id): a status change archives a copy first
(services.bitemporal.archive_live_row) and keeps the id. Bank references are LAST FOUR digits only (a CHECK).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from cpsim import book as B
from cpsim import ledger as LG
from services.bitemporal import archive_live_row
from services.us_business_days import is_business_day, next_business_day

STATUSES = ("received", "pending_consent", "acknowledged", "settled", "rejected", "returned", "cancelled")
OPEN = ("received", "pending_consent", "acknowledged", "settled")
CANCELLABLE = ("received", "pending_consent")
RAILS = ("ach", "wire", "journal")
BANK_TYPES = {"domestic_bank": "domestic", "international_bank": "international"}
CUSTODIAN_TYPES = ("altruist",)
FIELDS = ("client_transfer_id", "from_account_id", "to_account_id", "to_bank", "amount", "currency", "rail",
          "value_date")
BANK_FIELDS = ("institution_slug", "account_id", "routing_last4", "bic", "iban_last4")
CLIENT_ID_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
AMOUNT_RX = re.compile(r"^\d+(\.\d{1,2})?$")
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LAST4_RX = re.compile(r"^[0-9]{4}$")
IBAN4_RX = re.compile(r"^[A-Z0-9]{4}$")
BIC_RX = re.compile(r"^[A-Z0-9]{8}([A-Z0-9]{3})?$")
OUT_TYPE = {"journal": "transfer_out", "ach": "ach_out", "wire": "wire_out"}
IN_TYPE = {"journal": "transfer_in", "ach": "ach_in", "wire": "wire_in"}
RETURN_TYPE = "ach_return"
STATUS_LABELS = {"received": "Received", "pending_consent": "Awaiting the member's consent",
                 "acknowledged": "Acknowledged", "settled": "Settled", "rejected": "Rejected", "returned": "Returned",
                 "cancelled": "Cancelled"}
#: NACHA return reason codes the simulator names (any other R-code is "ACH return")
RETURN_REASONS = {"R01": "Insufficient funds", "R02": "Account closed", "R03": "No account / unable to locate account",
                  "R04": "Invalid account number", "R08": "Payment stopped", "R10": "Customer advises not authorized",
                  "R16": "Account frozen", "R20": "Non-transaction account", "R29": "Corporate customer advises not "
                  "authorized"}
REJECT_DEFAULT = "rejected by the simulated custodian"
REJECT_FUNDS = "insufficient funds"
BANK_REJECT_DEFAULT = "rejected by the receiving bank"


class TransferError(Exception):
    status = 422

    def __init__(self, detail: str, errors: list | None = None):
        super().__init__(detail)
        self.errors = errors or []


class TransferNotFound(TransferError):
    status = 404


class TransferConflict(TransferError):
    status = 409


def _h(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def transfer_id_for(counterparty_id, client_transfer_id: str) -> str:
    return "sim-t-" + _h(counterparty_id, "transfer", client_transfer_id)


def add_business_days(d: date, n: int) -> date:
    out = d
    for _ in range(max(0, int(n))):
        out = next_business_day(out + timedelta(days=1))
    return out


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return v.isoformat()


def last4(v: str | None) -> str | None:
    s = re.sub(r"[^A-Za-z0-9]", "", str(v or ""))
    return s[-4:].upper() if len(s) >= 4 else None


# ── request parsing (strict: amount a decimal STRING, references only, nothing guessed) ─────────────────────────────
def parse(body) -> dict:
    if not isinstance(body, dict):
        raise TransferError("the body must be a JSON object", [{"field": "$", "message": "not an object"}])
    errors: list = []
    unknown = sorted(set(body) - set(FIELDS))
    if unknown:
        errors.append({"field": ",".join(unknown), "message": "unknown field(s)"})

    def txt(k):
        v = body.get(k)
        return v.strip() if isinstance(v, str) and v.strip() else None

    cid, frm, to, rail, cur = txt("client_transfer_id"), txt("from_account_id"), txt("to_account_id"), \
        txt("rail"), txt("currency") or "USD"
    if not cid or not CLIENT_ID_RX.match(cid):
        errors.append({"field": "client_transfer_id", "message": "required: 1–80 letters, digits, _ . : -"})
    if not frm:
        errors.append({"field": "from_account_id", "message": "required"})
    if rail not in RAILS:
        errors.append({"field": "rail", "message": f"one of {', '.join(RAILS)}"})
    if cur != "USD":
        errors.append({"field": "currency", "message": "USD is the only currency"})
    amt = None
    raw_amt = body.get("amount")
    if not isinstance(raw_amt, str) or not AMOUNT_RX.match(raw_amt.strip()):
        errors.append({"field": "amount", "message": "must be a positive decimal STRING with at most 2 decimals "
                                                     "(e.g. \"2500.00\")"})
    else:
        try:
            amt = Decimal(raw_amt.strip())
        except InvalidOperation:
            errors.append({"field": "amount", "message": "is not a decimal"})
        if amt is not None and amt <= 0:
            errors.append({"field": "amount", "message": "must be greater than zero"})
    vd = None
    raw_vd = txt("value_date")
    if not raw_vd or not DATE_RX.match(raw_vd):
        errors.append({"field": "value_date", "message": "required: ISO YYYY-MM-DD"})
    else:
        try:
            vd = date.fromisoformat(raw_vd)
        except ValueError:
            errors.append({"field": "value_date", "message": "not a date"})
        if vd is not None and not is_business_day(vd):
            errors.append({"field": "value_date", "message": "must be a US business day"})
    bank = body.get("to_bank")
    if (to is None) == (bank is None):
        errors.append({"field": "to_account_id", "message": "give exactly one of to_account_id or to_bank"})
    tb = None
    if bank is not None:
        if not isinstance(bank, dict):
            errors.append({"field": "to_bank", "message": "must be an object"})
        else:
            extra = sorted(set(bank) - set(BANK_FIELDS))
            if extra:
                errors.append({"field": "to_bank." + ",".join(extra), "message": "unknown field(s)"})
            tb = {k: (bank.get(k).strip() if isinstance(bank.get(k), str) and bank.get(k).strip() else None)
                  for k in BANK_FIELDS}
            if not tb["institution_slug"]:
                errors.append({"field": "to_bank.institution_slug", "message": "required"})
            if not tb["account_id"]:
                errors.append({"field": "to_bank.account_id", "message": "required"})
            if tb["routing_last4"] is not None and not LAST4_RX.match(tb["routing_last4"]):
                errors.append({"field": "to_bank.routing_last4", "message": "the LAST FOUR digits only — never a "
                                                                            "full routing number"})
            if tb["iban_last4"] is not None and not IBAN4_RX.match(tb["iban_last4"].upper()):
                errors.append({"field": "to_bank.iban_last4", "message": "the LAST FOUR characters only — never a "
                                                                         "full IBAN"})
            if tb["bic"] is not None and not BIC_RX.match(tb["bic"].upper()):
                errors.append({"field": "to_bank.bic", "message": "not a BIC"})
            if not (tb["routing_last4"] or (tb["bic"] and tb["iban_last4"])):
                errors.append({"field": "to_bank", "message": "a domestic bank needs routing_last4; an international "
                                                             "bank needs bic and iban_last4"})
    if rail == "journal" and bank is not None:
        errors.append({"field": "rail", "message": "a journal moves money between accounts at THIS custodian "
                                                   "(to_account_id)"})
    if rail in ("ach", "wire") and to is not None:
        errors.append({"field": "rail", "message": "ach / wire go to another institution (to_bank); between accounts "
                                                   "at this custodian use journal"})
    if errors:
        raise TransferError("malformed transfer", errors)
    if tb is not None:
        tb["iban_last4"] = tb["iban_last4"].upper() if tb["iban_last4"] else None
        tb["bic"] = tb["bic"].upper() if tb["bic"] else None
    return {"client_transfer_id": cid, "from_account_id": frm, "to_account_id": to, "to_bank": tb, "amount": amt,
            "rail": rail, "value_date": vd}


# ── reads ───────────────────────────────────────────────────────────────────────────────────────────────────────────
async def _live(conn, counterparty_id, *, transfer_id=None, client_transfer_id=None, for_update=False):
    col, val = ("transfer_id", transfer_id) if transfer_id is not None else ("client_transfer_id", client_transfer_id)
    r = await conn.fetchrow(
        f"SELECT * FROM cpsim.transfers WHERE counterparty_id = $1::uuid AND {col} = $2 AND system_to IS NULL"
        + (" FOR UPDATE" if for_update else ""), str(counterparty_id), val)
    return dict(r) if r else None


async def _events_by_row(conn, row_ids) -> dict:
    """The events of MANY transfers in ONE query (cpsim03 fix 3: never N+1 in a list)."""
    ids = [str(i) for i in row_ids]
    out: dict = {i: [] for i in ids}
    if ids:
        for e in await conn.fetch("SELECT transfer_row_id, seq, status, business_date, detail, at FROM "
                                  "cpsim.transfer_events WHERE transfer_row_id = ANY($1::uuid[]) "
                                  "ORDER BY transfer_row_id, seq", ids):
            out[str(e["transfer_row_id"])].append(e)
    return out


def transfer_out(r: dict, events) -> dict:
    bank = None
    if r["to_bank_slug"]:
        bank = {"institution_slug": r["to_bank_slug"], "account_id": r["to_bank_account_id"]}
        if r["to_bank_routing_last4"]:
            bank["routing_last4"] = r["to_bank_routing_last4"]
        if r["to_bank_bic"]:
            bank["bic"] = r["to_bank_bic"]
        if r["to_bank_iban_last4"]:
            bank["iban_last4"] = r["to_bank_iban_last4"]
    returned = r["status"] == "returned"
    return {"transfer_id": r["transfer_id"], "client_transfer_id": r["client_transfer_id"],
            "from_account_id": r["from_account_external_id"], "to_account_id": r["to_account_external_id"],
            "to_bank": bank, "amount": format(Decimal(r["amount"]).quantize(B.MONEY), "f"),
            "currency": r["currency_code"], "rail": r["rail"], "value_date": _iso(r["value_date"]),
            "status": r["status"], "reject_reason": r["reject_reason"],
            "return_code": r["return_code"] if returned else None,
            "return_reason": r["return_reason"] if returned else None,
            "received_on": _iso(r["received_on"]), "acknowledged_on": _iso(r["acknowledged_on"]),
            "settle_on": _iso(r["settle_on"]), "settled_on": _iso(r["settled_on"]),
            "returned_on": _iso(r["returned_on"]),
            "events": [{"status": e["status"], "on": _iso(e["business_date"]), "at": _iso(e["at"])} for e in events],
            "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"])}


async def get(conn, counterparty_id, transfer_id: str) -> dict:
    r = await _live(conn, counterparty_id, transfer_id=transfer_id)
    if r is None:
        raise TransferNotFound("no such transfer at this simulated custodian")
    ev = await _events_by_row(conn, [r["id"]])
    return transfer_out(r, ev[str(r["id"])])


async def list_for_account(conn, counterparty_id, account_id: str) -> list[dict]:
    rows = [dict(r) for r in await conn.fetch(
        "SELECT * FROM cpsim.transfers WHERE counterparty_id = $1::uuid AND system_to IS NULL AND "
        "(from_account_external_id = $2 OR to_account_external_id = $2) ORDER BY created_at DESC, transfer_id",
        str(counterparty_id), account_id)]
    ev = await _events_by_row(conn, [r["id"] for r in rows])
    return [transfer_out(r, ev[str(r["id"])]) for r in rows]


async def list_recent(conn, counterparty_id, limit: int = 100) -> list[dict]:
    """The admin page's Transfers tab: the newest transfers this custodian received, with their events."""
    rows = [dict(r) for r in await conn.fetch(
        "SELECT * FROM cpsim.transfers WHERE counterparty_id = $1::uuid AND system_to IS NULL "
        "ORDER BY created_at DESC, transfer_id LIMIT $2", str(counterparty_id), int(limit))]
    ev = await _events_by_row(conn, [r["id"] for r in rows])
    return [transfer_out(r, ev[str(r["id"])]) for r in rows]


async def list_received(conn, bank_slug: str, limit: int = 100) -> list[dict]:
    """A bank's Received tab: every live transfer (from any simulated custodian) addressed to this bank."""
    rows = [dict(r) for r in await conn.fetch(
        "SELECT t.*, c.slug AS sender_slug FROM cpsim.transfers t JOIN cpsim.counterparties c ON c.id = "
        "t.counterparty_id AND c.system_to IS NULL WHERE t.to_bank_slug = $1 AND t.system_to IS NULL "
        "ORDER BY t.created_at DESC, t.transfer_id LIMIT $2", bank_slug, int(limit))]
    ev = await _events_by_row(conn, [r["id"] for r in rows])
    return [{**transfer_out(r, ev[str(r["id"])]), "sender": r["sender_slug"]} for r in rows]


# ── state changes ───────────────────────────────────────────────────────────────────────────────────────────────────
async def _event(conn, row: dict, status: str, clock: date, detail: dict | None = None) -> None:
    seq = await conn.fetchval("SELECT COALESCE(max(seq), 0) + 1 FROM cpsim.transfer_events WHERE transfer_row_id = "
                              "$1::uuid", str(row["id"]))
    await conn.execute(
        "INSERT INTO cpsim.transfer_events (counterparty_id, transfer_row_id, seq, status, business_date, detail) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)", str(row["counterparty_id"]), str(row["id"]), int(seq),
        status, clock, json.dumps(detail or {}, default=str))


async def _set(conn, row: dict, clock: date, status: str, **fields) -> dict:
    await archive_live_row(conn, "cpsim.transfers", row["id"])
    cols = {"status": status, **fields}
    names = list(cols)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(names))
    new = await conn.fetchrow(f"UPDATE cpsim.transfers SET {sets}, updated_at = now(), system_from = now() WHERE "
                              "id = $1::uuid AND system_to IS NULL RETURNING *", row["id"], *[cols[c] for c in names])
    new = dict(new)
    await _event(conn, new, status, clock, {k: v for k, v in fields.items() if k in ("reject_reason", "return_code")})
    return new


async def bank_of(conn, slug: str) -> dict | None:
    r = await conn.fetchrow("SELECT * FROM cpsim.counterparties WHERE slug = $1 AND system_to IS NULL", slug)
    return dict(r) if r else None


async def bank_check(conn, row: dict) -> str | None:
    """D6 — the RECEIVING bank's verdict on a transfer addressed to it: None (accepted) or its reason."""
    bank = await bank_of(conn, row["to_bank_slug"])
    if bank is None or bank["institution_type"] not in BANK_TYPES:
        return "the receiving institution is not a bank this custodian can reach"
    if bank["status"] != "active":
        return "the receiving bank is not accepting transfers"
    beh = dict(await conn.fetchrow("SELECT default_outcome, reject_reason FROM cpsim.counterparty_behaviour WHERE "
                                   "counterparty_id = $1::uuid AND valid_to IS NULL", bank["id"]) or {})
    if beh.get("default_outcome") == "reject":
        return f"the receiving bank refused: {(beh.get('reject_reason') or '').strip() or BANK_REJECT_DEFAULT}"
    kind = BANK_TYPES[bank["institution_type"]]
    d = await conn.fetchrow("SELECT aba_wire, aba_ach, bic, iban FROM cpsim.counterparty_bank_details WHERE "
                            "counterparty_id = $1::uuid AND kind = $2 AND valid_to IS NULL", bank["id"], kind)
    if d is None:
        return "the receiving bank has no routing details on file"
    if kind == "domestic":
        if row["rail"] not in ("ach", "wire"):
            return "a domestic bank receives ACH or wire only"
        aba = d["aba_ach"] if row["rail"] == "ach" else d["aba_wire"]
        if not row["to_bank_routing_last4"] or row["to_bank_routing_last4"] != last4(aba):
            return (f"the routing number does not match the receiving bank's {'ACH' if row['rail'] == 'ach' else 'wire'} "
                    "routing number")
    else:
        if row["rail"] != "wire":
            return "an international bank receives wires only"
        if not row["to_bank_bic"] or row["to_bank_bic"] != (d["bic"] or "").upper():
            return "the BIC does not match the receiving bank"
        if not row["to_bank_iban_last4"] or row["to_bank_iban_last4"] != last4(d["iban"]):
            return "the IBAN does not match the receiving bank"
    if not await B.account_exists(conn, bank["id"], row["to_bank_account_id"]):
        return "the beneficiary account is not held at the receiving bank"
    return None


async def _return_plan(conn, row: dict, sender_behaviour: dict) -> tuple[str | None, str | None, int | None]:
    """An ACH's return code: the SENDER's behaviour first, else the receiving bank's (D6)."""
    if row["rail"] != "ach":
        return None, None, None
    if sender_behaviour.get("ach_return_code"):
        return sender_behaviour["ach_return_code"], "sender", int(sender_behaviour.get("ach_return_after_days") or 0)
    bank = await bank_of(conn, row["to_bank_slug"]) if row["to_bank_slug"] else None
    if bank is not None:
        b = await conn.fetchrow("SELECT ach_return_code, ach_return_after_days FROM cpsim.counterparty_behaviour "
                                "WHERE counterparty_id = $1::uuid AND valid_to IS NULL", bank["id"])
        if b is not None and b["ach_return_code"]:
            return b["ach_return_code"], "receiver", int(b["ach_return_after_days"] or 0)
    return None, None, None


async def _acknowledge(conn, row: dict, clock: date, sender_behaviour: dict) -> dict:
    if row["to_bank_slug"]:
        why = await bank_check(conn, row)
        if why:
            return await _set(conn, row, clock, "rejected", reject_reason=why)
    try:
        await LG.move(conn, row["counterparty_id"], row["from_account_external_id"], kind="transfer_debit",
                      source_ref=row["transfer_id"], amount=-Decimal(row["amount"]), clock=clock,
                      txn={"id": f"{row['transfer_id']}-out", "type": OUT_TYPE[row["rail"]],
                           "net_amount": Decimal(row["amount"])})
    except LG.InsufficientFunds:
        return await _set(conn, row, clock, "rejected", reject_reason=REJECT_FUNDS)
    settle_on = row["value_date"] if row["rail"] in ("journal", "wire") else add_business_days(row["value_date"], 1)
    code, source, days = await _return_plan(conn, row, sender_behaviour)
    return await _set(conn, row, clock, "acknowledged", acknowledged_on=clock, settle_on=max(settle_on, clock),
                      return_code=code, return_source=source, return_after_days=days)


async def _settle(conn, row: dict, clock: date) -> dict:
    amt = Decimal(row["amount"])
    if row["to_bank_slug"]:
        bank = await bank_of(conn, row["to_bank_slug"])
        await LG.move(conn, bank["id"], row["to_bank_account_id"], kind="transfer_credit", source_ref=row["transfer_id"],
                      amount=amt, clock=clock, create=True,
                      txn={"id": f"{row['transfer_id']}-in", "type": IN_TYPE[row["rail"]], "net_amount": amt})
    else:
        await LG.move(conn, row["counterparty_id"], row["to_account_external_id"], kind="transfer_credit",
                      source_ref=row["transfer_id"], amount=amt, clock=clock, create=True,
                      txn={"id": f"{row['transfer_id']}-in", "type": IN_TYPE[row["rail"]], "net_amount": amt})
    due = add_business_days(clock, row["return_after_days"] or 0) if row["return_code"] else None
    return await _set(conn, row, clock, "settled", settled_on=clock, return_due_on=due)


async def _return(conn, row: dict, clock: date) -> dict:
    amt = Decimal(row["amount"])
    await LG.move(conn, row["counterparty_id"], row["from_account_external_id"], kind="return_credit",
                  source_ref=row["transfer_id"], amount=amt, clock=clock,  create=True,
                  txn={"id": f"{row['transfer_id']}-rtn-src", "type": RETURN_TYPE, "net_amount": amt})
    bank = await bank_of(conn, row["to_bank_slug"])
    await LG.move(conn, bank["id"], row["to_bank_account_id"], kind="return_debit", source_ref=row["transfer_id"],
                  amount=-amt, clock=clock, allow_negative=True,
                  txn={"id": f"{row['transfer_id']}-rtn-dst", "type": RETURN_TYPE, "net_amount": -amt})
    return await _set(conn, row, clock, "returned", returned_on=clock,
                      return_reason=RETURN_REASONS.get(row["return_code"], "ACH return"))


async def step(conn, row: dict, clock: date, sender_behaviour: dict, *, at_receipt: bool) -> tuple[dict, bool]:
    """Advance one transfer as far as the clock allows, one state at a time. Returns (row, changed)."""
    changed = False
    for _ in range(6):
        st = row["status"]
        if st == "received":
            if at_receipt and sender_behaviour.get("default_outcome") == "reject":
                row = await _set(conn, row, clock, "rejected",
                                 reject_reason=(sender_behaviour.get("reject_reason") or "").strip() or REJECT_DEFAULT)
            elif at_receipt and sender_behaviour.get("consent_required"):
                row = await _set(conn, row, clock, "pending_consent")
                return row, True
            elif at_receipt and sender_behaviour.get("default_outcome") == "delay":
                return row, changed
            else:
                row = await _acknowledge(conn, row, clock, sender_behaviour)
            changed = True
            continue
        if st == "acknowledged" and row["settle_on"] is not None and clock >= row["settle_on"]:
            row, changed = await _settle(conn, row, clock), True
            continue
        if st == "settled" and row["return_code"] and row["return_due_on"] is not None and clock >= row["return_due_on"]:
            row, changed = await _return(conn, row, clock), True
            continue
        return row, changed
    return row, changed


async def submit(conn, cp: dict, body, *, clock: date, behaviour: dict | None) -> tuple[dict, bool, list[str]]:
    """POST /v2/transfers. Returns (transfer, created, events). created False for a repeat client_transfer_id (the
    ORIGINAL returned unchanged, whatever the repeated body says — §9.1)."""
    if cp["institution_type"] not in CUSTODIAN_TYPES:
        raise TransferError("this simulated counterparty does not send transfers (a bank only receives them)")
    req = parse(body)
    cid = str(cp["id"])
    existing = await _live(conn, cid, client_transfer_id=req["client_transfer_id"])
    if existing is not None:
        ev = await _events_by_row(conn, [existing["id"]])
        return transfer_out(existing, ev[str(existing["id"])]), False, []
    if not await B.account_exists(conn, cid, req["from_account_id"]):
        raise TransferNotFound("no such account in this simulated custodian's book")
    if req["value_date"] < clock:
        raise TransferError("the value date is before the custodian's business date",
                            [{"field": "value_date", "message": f"on or after {clock.isoformat()}"}])
    tb = req["to_bank"]
    if req["rail"] == "journal":
        if req["to_account_id"] == req["from_account_id"]:
            raise TransferError("the source and destination are the same account",
                                [{"field": "to_account_id", "message": "same as from_account_id"}])
        if not await B.account_exists(conn, cid, req["to_account_id"]):
            raise TransferError("no such destination account at this custodian",
                                [{"field": "to_account_id", "message": "not in this custodian's book"}])
    else:
        bank = await bank_of(conn, tb["institution_slug"])
        if bank is None or bank["institution_type"] not in BANK_TYPES:
            raise TransferError("unknown receiving institution",
                                [{"field": "to_bank.institution_slug", "message": "not a bank this custodian reaches"}])
        if BANK_TYPES[bank["institution_type"]] == "international" and req["rail"] != "wire":
            raise TransferError("an international bank receives wires only",
                                [{"field": "rail", "message": "wire for an international bank"}])
    beh = behaviour or {}
    row = dict(await conn.fetchrow(
        "INSERT INTO cpsim.transfers (counterparty_id, transfer_id, client_transfer_id, from_account_external_id, "
        "to_account_external_id, to_bank_slug, to_bank_account_id, to_bank_routing_last4, to_bank_bic, "
        "to_bank_iban_last4, amount, rail, value_date, status, received_on) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, "
        "$8, $9, $10, $11, $12, $13, 'received', $14) RETURNING *", cid,
        transfer_id_for(cid, req["client_transfer_id"]), req["client_transfer_id"], req["from_account_id"],
        req["to_account_id"], tb and tb["institution_slug"], tb and tb["account_id"], tb and tb["routing_last4"],
        tb and tb["bic"], tb and tb["iban_last4"], req["amount"], req["rail"], req["value_date"], clock))
    await _event(conn, row, "received", clock)
    row, _changed = await step(conn, row, clock, beh, at_receipt=True)
    ev = await _events_by_row(conn, [row["id"]])
    return transfer_out(row, ev[str(row["id"])]), True, ["transfer.updated"]


async def cancel(conn, cp: dict, transfer_id: str, clock: date) -> dict:
    row = await _live(conn, cp["id"], transfer_id=transfer_id, for_update=True)
    if row is None:
        raise TransferNotFound("no such transfer at this simulated custodian")
    if row["status"] not in CANCELLABLE:
        raise TransferConflict(f"the transfer is already {STATUS_LABELS[row['status']].lower()} — it cannot be "
                               "cancelled")
    row = await _set(conn, row, clock, "cancelled")
    ev = await _events_by_row(conn, [row["id"]])
    return transfer_out(row, ev[str(row["id"])])


async def record_consent(conn, counterparty_id, transfer_id: str, clock: date, behaviour: dict | None) -> dict:
    """D8 — the admin's "Record member consent" (the custodian's e-consent screen): pending_consent → acknowledged
    (the bank check and the debit run now), then settled at once if the clock already allows."""
    row = await _live(conn, counterparty_id, transfer_id=transfer_id, for_update=True)
    if row is None:
        raise TransferNotFound("no such transfer at this simulated custodian")
    if row["status"] != "pending_consent":
        raise TransferConflict(f"the transfer is {STATUS_LABELS[row['status']].lower()} — no consent is awaited")
    row = await _acknowledge(conn, row, clock, behaviour or {})
    row = await _set_consent_time(conn, row)
    row, _ = await step(conn, row, clock, behaviour or {}, at_receipt=False)
    ev = await _events_by_row(conn, [row["id"]])
    return transfer_out(row, ev[str(row["id"])])


async def _set_consent_time(conn, row: dict) -> dict:
    await archive_live_row(conn, "cpsim.transfers", row["id"])
    return dict(await conn.fetchrow("UPDATE cpsim.transfers SET consent_recorded_at = now(), updated_at = now(), "
                                    "system_from = now() WHERE id = $1::uuid AND system_to IS NULL RETURNING *",
                                    row["id"]))


async def advance(conn, counterparty_id, clock: date, behaviour: dict | None) -> list[dict]:
    """Every clock advance: each open transfer stepped as far as the new date allows (delayed receipts acknowledged,
    due settlements, due ACH returns). Returns what changed (the admin page shows it; webhooks are sent from it)."""
    rows = [dict(r) for r in await conn.fetch(
        "SELECT * FROM cpsim.transfers WHERE counterparty_id = $1::uuid AND system_to IS NULL AND status = ANY($2::text[]) "
        "AND status <> 'pending_consent' ORDER BY created_at, transfer_id FOR UPDATE", str(counterparty_id), list(OPEN))]
    out = []
    for row in rows:
        before = row["status"]
        new, changed = await step(conn, row, clock, behaviour or {}, at_receipt=False)
        if changed:
            out.append({"transfer_id": new["transfer_id"], "client_transfer_id": new["client_transfer_id"],
                        "account_id": new["from_account_external_id"], "from": before, "status": new["status"],
                        "reject_reason": new["reject_reason"],
                        "return_code": new["return_code"] if new["status"] == "returned" else None,
                        "amount": format(Decimal(new["amount"]).quantize(B.MONEY), "f"), "rail": new["rail"]})
    return out
