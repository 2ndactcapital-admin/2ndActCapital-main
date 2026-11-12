"""cpsim05 — the simulated counterparty's FIX 4.4 endpoint: ONE tag=value message in, the reply message(s) out, carried
over HTTP (docs/cpsim/CPSIM05_NOTES.md D1 / D2; the message catalogue in §3). Deliberately simple: no FIX session, no
logon, no heartbeat, no sequence-number recovery — MsgSeqNum (34) is recorded, never enforced.

    POST /cpsim/c/{slug}/fix      Authorization: Bearer <access token> (the orders API's auth)
                                  body: one message, SOH (\\x01) or '|' delimited; reply: the reply messages, newline
                                  separated, in the request's delimiter. A malformed message → a Reject (35=3) with the
                                  reason in 58 — HTTP 200, never a 500.

WIRE RULES (``parse``): 8=FIX.4.4 first, 9 BodyLength second, 35 MsgType third, 10 CheckSum last (3 digits). BodyLength
= the bytes after the 9 field's delimiter up to and including the delimiter before 10=. CheckSum = the sum of the bytes
before 10= modulo 256, computed on the SOH form — a '|' message is normalised to SOH first (the two delimiters are one
byte each, so BodyLength is identical in both forms). 49 / 56 must equal the counterparty's live FIX session
(``sender_comp_id`` = Hollisworks, ``target_comp_id`` = this counterparty — cpsim01's orientation); the reply swaps them.

MESSAGES (D2) — the OUTCOMES are cpsim03's own code (cpsim/orders.submit / cancel / get; behaviour accept / reject /
partial / delay, the platform close on the counterparty clock, unsupported instrument / no price / insufficient position):
  D  NewOrderSingle      → 35=8 New (150=0, 39=0) then one 150=F Trade report per execution; rejected → one 150=8 / 39=8
                           with 58. A repeat ClOrdID → the SAME order's trade reports + a 150=I status report (never a
                           second order). 54=5 (sell short) → rejected "no shorting declared" (no order is created).
                           591 present → a BLOCK order (executes without a book move; the AllocationInstruction moves
                           the accounts — D6).
  F  OrderCancelRequest  → 35=8 150=4 / 39=4, or 35=9 OrderCancelReject (102 0 too late / 1 unknown order).
  H  OrderStatusRequest  → every trade report + a 150=I status report (Hollisworks collects fills after an advance).
  J  AllocationInstruction (a filled block order) → 35=P AllocationInstructionAck 87=0 accepted, each account moved
                           (transaction sim-a-…, position, cash on settle — cpsim04); 87=2 rejected with 88 + 58.
                           Idempotent on AllocID (a repeat → the same Ack, nothing booked twice).

Every message in and out is written to cpsim.fix_messages (append-only) — a Password (554) / RawData (96) value is
redacted first. Nothing here touches a table outside ``cpsim`` (book moves are cpsim/orders.py's).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from cpsim import book as B
from cpsim import orders as SO
from services.us_business_days import next_business_day

SOH = "\x01"
PIPE = "|"
BEGIN = "FIX.4.4"
SECRET_TAGS = (554, 96)
MAX_MESSAGE = 8000
NONE_ORDER_ID = "NONE"
CUSTODIAN_DESK = "CUSTODIAN-DESK"

MSG_NAMES = {"D": "NewOrderSingle", "F": "OrderCancelRequest", "H": "OrderStatusRequest",
             "J": "AllocationInstruction", "8": "ExecutionReport", "9": "OrderCancelReject",
             "P": "AllocationInstructionAck", "3": "Reject"}
INBOUND = ("D", "F", "H", "J")
TAG_NAMES = {8: "BeginString", 9: "BodyLength", 35: "MsgType", 49: "SenderCompID", 56: "TargetCompID",
             34: "MsgSeqNum", 52: "SendingTime", 10: "CheckSum", 11: "ClOrdID", 41: "OrigClOrdID", 1: "Account",
             55: "Symbol", 48: "SecurityID", 22: "SecurityIDSource", 54: "Side", 38: "OrderQty", 152: "CashOrderQty",
             40: "OrdType", 44: "Price", 59: "TimeInForce", 60: "TransactTime", 591: "PreallocMethod", 37: "OrderID",
             17: "ExecID", 150: "ExecType", 39: "OrdStatus", 31: "LastPx", 32: "LastQty", 14: "CumQty",
             151: "LeavesQty", 6: "AvgPx", 64: "SettlDate", 75: "TradeDate", 58: "Text", 102: "CxlRejReason",
             434: "CxlRejResponseTo", 45: "RefSeqNum", 371: "RefTagID", 372: "RefMsgType",
             373: "SessionRejectReason", 70: "AllocID", 71: "AllocTransType", 626: "AllocType", 53: "Quantity",
             73: "NoOrders", 78: "NoAllocs", 79: "AllocAccount", 80: "AllocQty", 467: "IndividualAllocID", 87: "AllocStatus",
             88: "AllocRejCode", 554: "Password", 96: "RawData"}
#: required tags per inbound message type (after the header)
REQUIRED = {"D": (11, 1, 54, 40), "F": (41, 11, 54), "H": (11,), "J": (70, 71, 54, 53, 6, 73, 11, 78)}
#: SessionRejectReason (373)
SRR_INVALID_TAG, SRR_REQUIRED_MISSING, SRR_VALUE_INCORRECT, SRR_FORMAT, SRR_COMPID, SRR_MSGTYPE, SRR_OTHER = \
    0, 1, 5, 6, 9, 11, 99
ORD_STATUS = {"accepted": "0", "working": "0", "partially_filled": "1", "filled": "2", "rejected": "8",
              "cancelled": "4"}
SIDE_IN = {"1": "buy", "2": "sell"}
SIDE_OUT = {"buy": "1", "sell": "2"}
TIF_IN = {"0": "day", "1": "gtc"}
NO_SHORTING = "sell short refused: the simulated custodian declares no shorting"


class FixReject(Exception):
    """A malformed message → a session-level Reject (35=3)."""

    def __init__(self, text: str, *, ref_tag: int | None = None, reason: int = SRR_OTHER):
        super().__init__(text)
        self.text, self.ref_tag, self.reason = text, ref_tag, reason


# ── codec ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def checksum(prefix_soh: str) -> str:
    """Sum of the bytes (SOH form) modulo 256, three digits."""
    return f"{sum(prefix_soh.encode('utf-8')) % 256:03d}"


def utc_stamp(ts: datetime | None = None) -> str:
    ts = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return ts.strftime("%Y%m%d-%H:%M:%S.") + f"{ts.microsecond // 1000:03d}"


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def encode(msg_type: str, fields: list[tuple[int, str]], *, sender: str, target: str, seq: int,
           delim: str = SOH, sending_time: datetime | None = None) -> str:
    """A complete message: header (8, 9, 35, 49, 56, 34, 52), the body fields in order, trailer (10)."""
    head = [(35, msg_type), (49, sender), (56, target), (34, str(int(seq))), (52, utc_stamp(sending_time))]
    body = "".join(f"{t}={v}{SOH}" for t, v in head + [(t, str(v)) for t, v in fields if v is not None and v != ""])
    prefix = f"8={BEGIN}{SOH}9={len(body.encode('utf-8'))}{SOH}{body}"
    msg = f"{prefix}10={checksum(prefix)}{SOH}"
    return msg if delim == SOH else msg.replace(SOH, delim)


def parse(raw: str) -> tuple[str, list[tuple[int, str]]]:
    """(delimiter, fields in order) — or FixReject with the reason. Every wire rule is checked."""
    if not isinstance(raw, str) or not raw.strip():
        raise FixReject("empty message", reason=SRR_FORMAT)
    raw = raw.strip("\r\n ")
    if len(raw) > MAX_MESSAGE:
        raise FixReject(f"message longer than {MAX_MESSAGE} characters", reason=SRR_FORMAT)
    delim = SOH if SOH in raw else (PIPE if PIPE in raw else None)
    if delim is None:
        raise FixReject("no field delimiter (SOH or '|')", reason=SRR_FORMAT)
    soh = raw if delim == SOH else raw.replace(PIPE, SOH)
    if not soh.endswith(SOH):
        soh += SOH          # a trailing delimiter after 10= is customary; tolerate its absence
    tokens = soh.split(SOH)[:-1]
    fields: list[tuple[int, str]] = []
    for tok in tokens:
        tag, sep, val = tok.partition("=")
        if not sep or not tag.isdigit() or not val:
            raise FixReject(f"malformed field {tok[:40]!r} (expected tag=value)", reason=SRR_FORMAT)
        fields.append((int(tag), val))
    if len(fields) < 4 or fields[0][0] != 8:
        raise FixReject("BeginString (8) must be the first field", ref_tag=8, reason=SRR_REQUIRED_MISSING)
    if fields[0][1] != BEGIN:
        raise FixReject(f"BeginString must be {BEGIN} (got {fields[0][1]!r})", ref_tag=8, reason=SRR_VALUE_INCORRECT)
    if fields[1][0] != 9:
        raise FixReject("BodyLength (9) must be the second field", ref_tag=9, reason=SRR_REQUIRED_MISSING)
    if fields[2][0] != 35:
        raise FixReject("MsgType (35) must be the third field", ref_tag=35, reason=SRR_REQUIRED_MISSING)
    if fields[-1][0] != 10:
        raise FixReject("CheckSum (10) must be the last field", ref_tag=10, reason=SRR_REQUIRED_MISSING)
    head = f"8={fields[0][1]}{SOH}9={fields[1][1]}{SOH}"
    tail_at = soh.rfind(f"{SOH}10=") + 1
    body = soh[len(head):tail_at]
    if not fields[1][1].isdigit() or int(fields[1][1]) != len(body.encode("utf-8")):
        raise FixReject(f"BodyLength (9) is {fields[1][1]}, the body is {len(body.encode('utf-8'))} bytes",
                        ref_tag=9, reason=SRR_VALUE_INCORRECT)
    cs = fields[-1][1]
    if len(cs) != 3 or not cs.isdigit():
        raise FixReject("CheckSum (10) must be three digits", ref_tag=10, reason=SRR_FORMAT)
    want = checksum(soh[:tail_at])
    if cs != want:
        raise FixReject(f"CheckSum (10) is {cs}, the message sums to {want}", ref_tag=10, reason=SRR_VALUE_INCORRECT)
    return delim, fields


def first(fields: list[tuple[int, str]], tag: int) -> str | None:
    return next((v for t, v in fields if t == tag), None)


def redact(raw: str) -> str:
    """A Password (554) / RawData (96) value never reaches the log."""
    out = []
    delim = SOH if SOH in raw else PIPE
    for tok in raw.split(delim):
        tag, sep, _ = tok.partition("=")
        out.append(f"{tag}=[redacted]" if sep and tag.isdigit() and int(tag) in SECRET_TAGS else tok)
    return delim.join(out)


def _dec(fields, tag: int, *, required: bool = False) -> Decimal | None:
    v = first(fields, tag)
    if v is None:
        if required:
            raise FixReject(f"{TAG_NAMES.get(tag, tag)} ({tag}) is required", ref_tag=tag, reason=SRR_REQUIRED_MISSING)
        return None
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise FixReject(f"{TAG_NAMES.get(tag, tag)} ({tag}) is not a decimal", ref_tag=tag, reason=SRR_FORMAT) from None
    if not d.is_finite() or d <= 0:
        raise FixReject(f"{TAG_NAMES.get(tag, tag)} ({tag}) must be greater than zero", ref_tag=tag,
                        reason=SRR_VALUE_INCORRECT)
    return d


def _plain(d) -> str:
    d = Decimal(d)
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _h(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


# ── the session ─────────────────────────────────────────────────────────────────────────────────────────────────────
async def session(conn, counterparty_id) -> dict | None:
    r = await conn.fetchrow("SELECT begin_string, sender_comp_id, target_comp_id FROM cpsim.counterparty_fix_sessions "
                            "WHERE counterparty_id = $1::uuid AND valid_to IS NULL", str(counterparty_id))
    return dict(r) if r else None


def check_session(fields, sess: dict | None) -> None:
    if sess is None:
        raise FixReject("no FIX session is configured for this simulated counterparty (Admin → Counterparties → FIX)",
                        reason=SRR_COMPID)
    if sess["begin_string"] != BEGIN:
        raise FixReject(f"this counterparty's FIX session is {sess['begin_string']}, not {BEGIN}", ref_tag=8,
                        reason=SRR_VALUE_INCORRECT)
    if first(fields, 49) != sess["sender_comp_id"]:
        raise FixReject(f"SenderCompID (49) {first(fields, 49)!r} is not this session's", ref_tag=49,
                        reason=SRR_COMPID)
    if first(fields, 56) != sess["target_comp_id"]:
        raise FixReject(f"TargetCompID (56) {first(fields, 56)!r} is not this session's", ref_tag=56,
                        reason=SRR_COMPID)


def check_required(msg_type: str, fields) -> None:
    if msg_type not in INBOUND:
        raise FixReject(f"MsgType (35) {msg_type!r} is not accepted here (accepted: {', '.join(INBOUND)})", ref_tag=35,
                        reason=SRR_MSGTYPE)
    for tag in REQUIRED[msg_type]:
        if first(fields, tag) is None:
            raise FixReject(f"{TAG_NAMES.get(tag, tag)} ({tag}) is required in {MSG_NAMES[msg_type]}", ref_tag=tag,
                            reason=SRR_REQUIRED_MISSING)


# ── the reports ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _instrument(order: dict) -> list[tuple[int, str]]:
    out = []
    if order.get("symbol"):
        out.append((55, order["symbol"]))
    if order.get("cusip"):
        out += [(48, order["cusip"]), (22, "1")]
    return out


def _amount(order: dict) -> list[tuple[int, str]]:
    return [(38, order["quantity"])] if order.get("quantity") else [(152, order["notional"])]


def _leaves(order: dict, cum: Decimal) -> str:
    """LeavesQty: 0 once terminal; a cash (notional) order reports 0 (ASSUMED — its share count is not known)."""
    if order["status"] in ("filled", "rejected", "cancelled") or not order.get("quantity"):
        return "0"
    return _plain(Decimal(order["quantity"]) - cum)


def _avg(n: Decimal, q: Decimal) -> str:
    return _plain((n / q).quantize(SO.FRACTION, rounding=ROUND_HALF_UP)) if q else "0"


def _base(order: dict) -> list[tuple[int, str]]:
    return [(37, order["order_id"]), (11, order["client_order_id"]), (1, order["account_id"]), *_instrument(order),
            (54, SIDE_OUT[order["side"]]), *_amount(order), (40, "2" if order["order_type"] == "limit" else "1"),
            *([(44, order["limit_price"])] if order.get("limit_price") else [])]


def trade_reports(order: dict) -> list[tuple[str, list]]:
    """One 150=F report per execution, cumulative through it."""
    out, q, n = [], Decimal(0), Decimal(0)
    execs = order.get("executions") or []
    for i, e in enumerate(execs):
        lq, lp = Decimal(e["quantity"]), Decimal(e["price"])
        q, n = q + lq, n + lq * lp
        last = i == len(execs) - 1
        st = order["status"] if last and order["status"] in ("filled", "partially_filled") else (
            "filled" if order.get("quantity") and q >= Decimal(order["quantity"]) else "partially_filled")
        out.append(("8", _base(order) + [
            (17, e["exec_id"]), (150, "F"), (39, ORD_STATUS[st]), (31, _plain(lp)), (32, _plain(lq)),
            (14, _plain(q)), (151, "0" if st == "filled" else (_plain(Decimal(order["quantity"]) - q)
                                                            if order.get("quantity") else "0")),
            (6, _avg(n, q)), (60, utc_stamp(_ts(e["executed_at"]))), (75, _d8(e.get("trade_date"))),
            (64, _d8(e.get("settle_date")))]))
    return out


def _ts(v) -> datetime:
    if isinstance(v, datetime):
        return v
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _d8(v) -> str | None:
    if not v:
        return None
    return str(v)[:10].replace("-", "")


def _cum(order: dict) -> tuple[Decimal, Decimal]:
    q = sum((Decimal(e["quantity"]) for e in order.get("executions") or []), Decimal(0))
    n = sum((Decimal(e["quantity"]) * Decimal(e["price"]) for e in order.get("executions") or []), Decimal(0))
    return q, n


def status_report(order: dict, exec_type: str) -> tuple[str, list]:
    q, n = _cum(order)
    fields = _base(order) + [(17, "sim-s-" + _h(order["order_id"], exec_type, order["status"], _plain(q))),
                             (150, exec_type), (39, ORD_STATUS[order["status"]]), (14, _plain(q)),
                             (151, _leaves(order, q)), (6, _avg(n, q)), (60, utc_stamp())]
    if order.get("reject_reason"):
        fields.append((58, order["reject_reason"]))
    return "8", fields


def rejected_without_order(cl_ord_id: str, fields_in, reason: str) -> tuple[str, list]:
    """A business refusal before any order exists (OrderID NONE)."""
    sym = [(55, first(fields_in, 55))] if first(fields_in, 55) else []
    return "8", [(37, NONE_ORDER_ID), (11, cl_ord_id), (1, first(fields_in, 1) or ""), *sym,
                 (54, first(fields_in, 54) or ""), (17, "sim-s-" + _h(cl_ord_id, "reject", reason)), (150, "8"),
                 (39, "8"), (14, "0"), (151, "0"), (6, "0"), (60, utc_stamp()), (58, reason)]


# ── handlers (one transaction as cpsim_app; the caller holds it) ────────────────────────────────────────────────────
def _order_body(fields) -> tuple[dict, bool]:
    side = first(fields, 54)
    if side not in SIDE_IN:
        raise FixReject(f"Side (54) {side!r} is not accepted (1 buy, 2 sell)", ref_tag=54, reason=SRR_VALUE_INCORRECT)
    ord_type = first(fields, 40)
    if ord_type not in ("1", "2"):
        raise FixReject(f"OrdType (40) {ord_type!r} is not accepted (1 market, 2 limit)", ref_tag=40,
                        reason=SRR_VALUE_INCORRECT)
    tif = first(fields, 59) or "0"
    if tif not in TIF_IN:
        raise FixReject(f"TimeInForce (59) {tif!r} is not accepted (0 day, 1 GTC)", ref_tag=59,
                        reason=SRR_VALUE_INCORRECT)
    q, n = _dec(fields, 38), _dec(fields, 152)
    if (q is None) == (n is None):
        raise FixReject("give exactly one of OrderQty (38) or CashOrderQty (152)", ref_tag=38,
                        reason=SRR_REQUIRED_MISSING if q is None else SRR_VALUE_INCORRECT)
    px = _dec(fields, 44, required=ord_type == "2")
    if ord_type == "1" and first(fields, 44) is not None:
        raise FixReject("a market order carries no Price (44)", ref_tag=44, reason=SRR_VALUE_INCORRECT)
    sid, src = first(fields, 48), first(fields, 22)
    if sid is not None and src != "1":
        raise FixReject("SecurityIDSource (22) must be 1 (CUSIP)", ref_tag=22, reason=SRR_VALUE_INCORRECT)
    if not (first(fields, 55) or sid):
        raise FixReject("Symbol (55) and/or SecurityID (48) is required", ref_tag=55, reason=SRR_REQUIRED_MISSING)
    body = {"client_order_id": first(fields, 11), "account_id": first(fields, 1), "side": SIDE_IN[side],
            "order_type": "limit" if ord_type == "2" else "market", "time_in_force": TIF_IN[tif]}
    if first(fields, 55):
        body["symbol"] = first(fields, 55)
    if sid:
        body["cusip"] = sid
    if q is not None:
        body["quantity"] = _plain(q)
    else:
        body["notional"] = _plain(n)
    if px is not None:
        body["limit_price"] = _plain(px)
    return body, first(fields, 591) is not None


async def new_order(conn, cp: dict, fields, *, clock: date, behaviour: dict) -> list[tuple[str, list]]:
    cl = first(fields, 11)
    existing = await SO._live(conn, cp["id"], client_order_id=cl)
    if existing is not None:      # idempotent on ClOrdID: the SAME order's reports, whatever the repeat says
        order = await SO.get(conn, cp["id"], existing["order_id"])
        return trade_reports(order) + [status_report(order, "I")]
    if first(fields, 54) == "5":
        return [rejected_without_order(cl, fields, NO_SHORTING)]
    body, block = _order_body(fields)
    try:
        order, created, _ = await SO.submit(conn, cp, body, clock=clock, behaviour=behaviour, protocol="fix",
                                            block=block)
    except SO.OrderNotFound as exc:
        return [rejected_without_order(cl, fields, str(exc))]
    except SO.OrderError as exc:
        why = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in exc.errors) or str(exc)
        if exc.status == 422 and any(e.get("field") in ("symbol",) for e in exc.errors):
            return [rejected_without_order(cl, fields, f"{exc}: {why}")]
        raise FixReject(f"{exc}: {why}", reason=SRR_VALUE_INCORRECT) from exc
    if not created:
        return trade_reports(order) + [status_report(order, "I")]
    if order["status"] == "rejected":
        return [status_report(order, "8")]
    new_ack = status_report({**order, "status": "accepted", "executions": [], "reject_reason": None}, "0")
    return [new_ack] + trade_reports(order)


async def cancel_order(conn, cp: dict, fields) -> list[tuple[str, list]]:
    orig, cl = first(fields, 41), first(fields, 11)
    row = await SO._live(conn, cp["id"], client_order_id=orig)

    def reject(code: str, text: str, status: str) -> list:
        return [("9", [(37, row["order_id"] if row else NONE_ORDER_ID), (11, cl), (41, orig), (39, status),
                       (434, "1"), (102, code), (58, text)])]
    if row is None:
        return reject("1", "unknown order (OrigClOrdID)", "8")
    try:
        order = await SO.cancel(conn, cp, row["order_id"])
    except SO.OrderError as exc:
        return reject("0", str(exc), ORD_STATUS.get(row["status"], "8"))
    return [status_report(order, "4")]


async def order_status(conn, cp: dict, fields) -> list[tuple[str, list]]:
    cl = first(fields, 11)
    row = await SO._live(conn, cp["id"], client_order_id=cl)
    if row is None or (first(fields, 37) and first(fields, 37) != row["order_id"]):
        return [rejected_without_order(cl, fields, "unknown order")]
    order = await SO.get(conn, cp["id"], row["order_id"])
    return trade_reports(order) + [status_report(order, "I")]


def _alloc_ack(alloc_id: str, status: str, trade_date: date, *, code: str | None = None,
               text: str | None = None, legs: list[tuple[str, str]] | None = None) -> tuple[str, list]:
    """35=P. Accepted: each account with its booked transaction (78 / 79 / 467 IndividualAllocID — the id the
    account's synced transaction carries, so Hollisworks reconciles against the feed)."""
    f = [(70, alloc_id), (75, ymd(trade_date)), (87, status)]
    if code is not None:
        f.append((88, code))
    if text:
        f.append((58, text))
    if legs:
        f.append((78, str(len(legs))))
        for acct, txn in legs:
            f += [(79, acct), (467, txn)]
    return "P", f


def _alloc_groups(fields) -> list[tuple[str, Decimal]]:
    n = first(fields, 78)
    if not (n or "").isdigit() or int(n) < 1:
        raise FixReject("NoAllocs (78) must be a positive count", ref_tag=78, reason=SRR_VALUE_INCORRECT)
    accts = [v for t, v in fields if t == 79]
    qtys = [v for t, v in fields if t == 80]
    if len(accts) != int(n) or len(qtys) != int(n):
        raise FixReject(f"NoAllocs (78) is {n}, but {len(accts)} AllocAccount (79) / {len(qtys)} AllocQty (80) follow",
                        ref_tag=78, reason=SRR_VALUE_INCORRECT)
    out = []
    for a, q in zip(accts, qtys):
        try:
            d = Decimal(q)
        except InvalidOperation:
            raise FixReject("AllocQty (80) is not a decimal", ref_tag=80, reason=SRR_FORMAT) from None
        if not d.is_finite() or d <= 0:
            raise FixReject("AllocQty (80) must be greater than zero", ref_tag=80, reason=SRR_VALUE_INCORRECT)
        out.append((a, d))
    if len({a for a, _ in out}) != len(out):
        raise FixReject("an AllocAccount (79) appears twice", ref_tag=79, reason=SRR_VALUE_INCORRECT)
    return out


async def allocate(conn, cp: dict, fields, *, clock: date) -> list[tuple[str, list]]:
    """35=J on a filled (or partially filled) BLOCK order: each account's leg booked at the instructed AvgPx (6 — the
    Hollisworks average; the simulator books what it is told). Idempotent per AllocID."""
    alloc_id = first(fields, 70)
    if not SO.CLIENT_ID_RX.match(alloc_id or ""):
        raise FixReject("AllocID (70) must be 1–80 letters, digits, _ . : -", ref_tag=70, reason=SRR_VALUE_INCORRECT)
    if first(fields, 71) != "0":
        raise FixReject("AllocTransType (71) must be 0 (new)", ref_tag=71, reason=SRR_VALUE_INCORRECT)
    legs = _alloc_groups(fields)
    total = _dec(fields, 53, required=True)
    price = _dec(fields, 6, required=True)
    cid = str(cp["id"])
    done = await conn.fetch("SELECT account_external_id, book_transaction_id FROM cpsim.fix_allocations WHERE "
                            "counterparty_id = $1::uuid AND alloc_id = $2 ORDER BY account_external_id", cid, alloc_id)
    if done:
        return [_alloc_ack(alloc_id, "0", clock, text=f"already accepted ({len(done)} account(s)) — nothing booked twice",
                           legs=[(r["account_external_id"], r["book_transaction_id"]) for r in done])]
    row = await SO._live(conn, cid, client_order_id=first(fields, 11))
    if row is None or (first(fields, 37) and first(fields, 37) != row["order_id"]):
        return [_alloc_ack(alloc_id, "2", clock, code="7", text="unknown block order (ClOrdID / OrderID)")]
    if not row["is_block"]:
        return [_alloc_ack(alloc_id, "2", clock, code="7", text="the order is not a block order (591 was not sent)")]
    if SIDE_OUT[row["side"]] != first(fields, 54):
        return [_alloc_ack(alloc_id, "2", clock, code="7", text="Side (54) does not match the block order")]
    execs = await SO._executions(conn, row["id"])
    filled, _ = SO._filled(execs)
    if filled <= 0:
        return [_alloc_ack(alloc_id, "2", clock, code="7", text=f"the block order is {row['status']} — nothing filled")]
    if sum((q for _, q in legs), Decimal(0)) != total or total != filled:
        return [_alloc_ack(alloc_id, "2", clock, code="7",
                           text=f"the allocations total {_plain(sum((q for _, q in legs), Decimal(0)))}, Quantity (53) "
                                f"is {_plain(total)}, the block filled {_plain(filled)} — they must agree")]
    for acct, q in legs:
        if not await B.account_exists(conn, cid, acct):
            return [_alloc_ack(alloc_id, "2", clock, code="0", text=f"unknown account {acct!r} (AllocAccount)")]
        if row["side"] == "sell" and q > await SO._position_qty(conn, cid, acct, row["security_id"], clock):
            return [_alloc_ack(alloc_id, "2", clock, code="7", text=f"{acct}: {SO.REJECT_INSUFFICIENT}")]
    sec = await B.security_row(conn, str(row["security_id"]))
    idents = await SO._identifiers(conn, row["security_id"])
    settle = next_business_day(clock + timedelta(days=1))
    booked = []
    for acct, q in legs:
        txn = "sim-a-" + _h(cid, "alloc", alloc_id, acct)
        booked.append((acct, txn))
        await SO._book_move(conn, cid, {**dict(row), "account_external_id": acct}, sec, idents, q, price, clock, txn,
                            settle, txn_external_id=txn)
        await conn.execute(
            "INSERT INTO cpsim.fix_allocations (counterparty_id, order_row_id, alloc_id, account_external_id, quantity, "
            "price, trade_date, settle_date, book_transaction_id) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, "
            "$9)", cid, row["id"], alloc_id, acct, q, price, clock, settle, txn)
    return [_alloc_ack(alloc_id, "0", clock, legs=sorted(booked))]


async def handle(conn, cp: dict, fields, *, clock: date, behaviour: dict) -> list[tuple[str, list]]:
    msg_type = first(fields, 35)
    check_required(msg_type, fields)
    if msg_type == "D":
        if not SO.CLIENT_ID_RX.match(first(fields, 11) or ""):
            raise FixReject("ClOrdID (11) must be 1–80 letters, digits, _ . : -", ref_tag=11,
                            reason=SRR_VALUE_INCORRECT)
        return await new_order(conn, cp, fields, clock=clock, behaviour=behaviour)
    if msg_type == "F":
        return await cancel_order(conn, cp, fields)
    if msg_type == "H":
        return await order_status(conn, cp, fields)
    return await allocate(conn, cp, fields, clock=clock)


def reject(text: str, *, ref_seq: str | None, ref_tag: int | None, ref_msg_type: str | None, reason: int
           ) -> tuple[str, list]:
    f = []
    if ref_seq and ref_seq.isdigit():
        f.append((45, ref_seq))
    if ref_tag is not None:
        f.append((371, str(ref_tag)))
    if ref_msg_type:
        f.append((372, ref_msg_type))
    f += [(373, str(reason)), (58, text[:400])]
    return "3", f


# ── the log ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _meta(raw: str) -> dict:
    """Index fields of a message for the log (best effort on a malformed one)."""
    delim = SOH if SOH in raw else PIPE
    got: dict = {}
    for tok in raw.split(delim):
        tag, sep, val = tok.partition("=")
        if sep and tag.isdigit() and int(tag) not in got:
            got[int(tag)] = val
    seq = got.get(34)
    return {"msg_type": got.get(35) if (got.get(35) or "").isalnum() and len(got.get(35, "")) <= 2 else None,
            "msg_seq_num": int(seq) if (seq or "").isdigit() and len(seq) < 10 else None,
            "sender": (got.get(49) or "")[:64] or None, "target": (got.get(56) or "")[:64] or None,
            "cl_ord_id": (got.get(11) or "")[:80] or None, "order_id": (got.get(37) or "")[:80] or None,
            "exec_id": (got.get(17) or "")[:80] or None, "alloc_id": (got.get(70) or "")[:80] or None}


async def log_exchange(conn, *, counterparty_id, raw_in: str, replies: list[str], valid: bool,
                       reject_reason: str | None) -> str:
    """The message in (seq 0) and every reply (seq 1…), one exchange id. Redacted, never a secret."""
    ex = str(uuid.uuid4())
    rows = [("in", 0, raw_in, valid, reject_reason)] + [("out", i, r, True, None) for i, r in enumerate(replies, 1)]
    for direction, seq, raw, ok, why in rows:
        safe = redact(raw)[:MAX_MESSAGE] or "[empty]"
        m = _meta(safe)
        await conn.execute(
            "INSERT INTO cpsim.fix_messages (counterparty_id, exchange_id, seq, direction, msg_type, msg_seq_num, "
            "sender_comp_id, target_comp_id, cl_ord_id, order_id, exec_id, alloc_id, valid, reject_reason, raw) "
            "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)",
            str(counterparty_id) if counterparty_id else None, ex, seq, direction, m["msg_type"], m["msg_seq_num"],
            m["sender"], m["target"], m["cl_ord_id"], m["order_id"], m["exec_id"], m["alloc_id"], bool(ok),
            (why or "")[:400] or None, safe)
    return ex


async def list_messages(conn, counterparty_id, limit: int = 200) -> list[dict]:
    """The admin FIX tab: newest exchanges first — ONE query (no per-row reads)."""
    rows = await conn.fetch(
        "SELECT id, exchange_id, seq, direction, msg_type, msg_seq_num, sender_comp_id, target_comp_id, cl_ord_id, "
        "order_id, exec_id, alloc_id, valid, reject_reason, raw, created_at FROM cpsim.fix_messages "
        "WHERE counterparty_id = $1::uuid ORDER BY created_at DESC, exchange_id, direction, seq LIMIT $2",
        str(counterparty_id), int(limit))
    return [{**{k: r[k] for k in ("seq", "direction", "msg_type", "msg_seq_num", "sender_comp_id", "target_comp_id",
                                  "cl_ord_id", "order_id", "exec_id", "alloc_id", "valid", "reject_reason")},
             "id": str(r["id"]), "exchange_id": str(r["exchange_id"]), "msg_name": MSG_NAMES.get(r["msg_type"] or ""),
             "raw": r["raw"].replace(SOH, PIPE), "created_at": r["created_at"].isoformat()} for r in rows]


def readable(raw: str) -> list[dict]:
    """tag / name / value for a person to read (the admin tab)."""
    delim = SOH if SOH in raw else PIPE
    out = []
    for tok in raw.split(delim):
        tag, sep, val = tok.partition("=")
        if sep and tag.isdigit():
            out.append({"tag": int(tag), "name": TAG_NAMES.get(int(tag), ""), "value": val})
    return out
