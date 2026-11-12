"""cpsim05 — the Hollisworks side of the SIMULATED FIX counterparty: build / parse / checksum FIX 4.4 tag=value
messages and carry ONE message per HTTP POST to the counterparty's ``…/fix`` endpoint (docs/cpsim/CPSIM05_NOTES.md D3;
the message catalogue §3).

ONLY A SIMULATED COUNTERPARTY. There is no FIX engine, no socket and no session here: a message in, the reply
message(s) out. The ``simulator`` environment is the only one this module talks to — asked for any other it refuses
before a request (no Altruist FIX contract has been seen; real custodians stay manual).

Conventions = the cpsim03 orders client's (services/altruist_trading.py), call for call:
  * hosts + access token resolved PER CALL for the org (:func:`session` = ``altruist_trading.order_session``: the
    allowed flag, the counterparty, an active ``simulator`` connection, ``check_destination``), read on a connection
    the caller then RELEASES before the HTTP call;
  * ``Authorization: Bearer <access token>``; the body is the message (text/plain);
  * 15 s timeout; ``transport`` threaded on EVERY call through ``altruist_oauth._transport_for`` (the given transport,
    else the injected simulator transport — tests drive the simulator in-process);
  * a transport failure or HTTP ≥ 400 → ``AltruistOAuthError`` (``FixRefused`` carries the status and detail).

THE CODEC is this module's own (independent of the simulator's — the cpsim boundary forbids sharing one; the
conformance ``fix`` group proves the two agree). BodyLength / CheckSum are computed on the SOH form; ``delim='|'`` is a
readable rendering of the same bytes.

CompIDs (49 / 56): Hollisworks' orientation, as cpsim01 stores the session — sender ``HOLLISWORKS``, target the
counterparty slug upper-cased (``-`` → ``_``); an org setting ``counterparty_sim.fix_comp_ids``
``{"sender_comp_id", "target_comp_id"}`` overrides either (when a Super Admin edited the counterparty's FIX tab).

This module decides nothing about WHETHER to send: its callers are services/fix_sim_auto.py (the automatic step, D4 /
D5 guard) and services/fix_sim_blocks.py (a person's block action, D6).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from services.altruist_oauth import SIMULATOR, AltruistOAuthError, _transport_for

SOH = "\x01"
PIPE = "|"
BEGIN = "FIX.4.4"
FIX_PATH = "/fix"
TIMEOUT_S = 15.0
DEFAULT_SENDER = "HOLLISWORKS"
SETTING_COMP_IDS = "counterparty_sim.fix_comp_ids"
NO_CONTRACT = ("no Altruist FIX contract has been seen — FIX messages are sent only to a simulated counterparty (the "
               "real route stays manual)")
SIDE = {"buy": "1", "sell": "2"}
ORD_STATUS = {"0": "new", "1": "partially_filled", "2": "filled", "4": "cancelled", "8": "rejected"}
MSG_NAMES = {"D": "NewOrderSingle", "F": "OrderCancelRequest", "H": "OrderStatusRequest", "J": "AllocationInstruction",
             "8": "ExecutionReport", "9": "OrderCancelReject", "P": "AllocationInstructionAck", "3": "Reject"}
TAG_NAMES = {8: "BeginString", 9: "BodyLength", 35: "MsgType", 49: "SenderCompID", 56: "TargetCompID", 34: "MsgSeqNum",
             52: "SendingTime", 10: "CheckSum", 11: "ClOrdID", 41: "OrigClOrdID", 1: "Account", 55: "Symbol",
             48: "SecurityID", 22: "SecurityIDSource", 54: "Side", 38: "OrderQty", 152: "CashOrderQty", 40: "OrdType",
             44: "Price", 59: "TimeInForce", 60: "TransactTime", 591: "PreallocMethod", 37: "OrderID", 17: "ExecID",
             150: "ExecType", 39: "OrdStatus", 31: "LastPx", 32: "LastQty", 14: "CumQty", 151: "LeavesQty",
             6: "AvgPx", 64: "SettlDate", 75: "TradeDate", 58: "Text", 102: "CxlRejReason", 434: "CxlRejResponseTo",
             45: "RefSeqNum", 371: "RefTagID", 372: "RefMsgType", 373: "SessionRejectReason", 70: "AllocID",
             71: "AllocTransType", 626: "AllocType", 53: "Quantity", 73: "NoOrders", 78: "NoAllocs",
             79: "AllocAccount", 80: "AllocQty", 467: "IndividualAllocID", 87: "AllocStatus", 88: "AllocRejCode"}


class FixClientError(AltruistOAuthError):
    """A reply that is not well-formed FIX (bad checksum / body length / framing) — never acted on."""


class FixRefused(AltruistOAuthError):
    def __init__(self, message: str, *, status: int, detail: str | None):
        super().__init__(message)
        self.status = status
        self.detail = detail


# ── codec ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def checksum(prefix_soh: str) -> str:
    return f"{sum(prefix_soh.encode('utf-8')) % 256:03d}"


def stamp(ts: datetime | None = None) -> str:
    ts = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return ts.strftime("%Y%m%d-%H:%M:%S.") + f"{ts.microsecond // 1000:03d}"


def plain(d) -> str:
    d = Decimal(d)
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def build(msg_type: str, fields: list[tuple[int, Any]], *, sender: str, target: str, seq: int = 1,
          delim: str = SOH, sending_time: datetime | None = None) -> str:
    head = [(35, msg_type), (49, sender), (56, target), (34, str(int(seq))), (52, stamp(sending_time))]
    pairs = head + [(t, str(v)) for t, v in fields if v is not None and str(v) != ""]
    body = "".join(f"{t}={str(v).replace(SOH, ' ').replace(PIPE, '/')}{SOH}" for t, v in pairs)   # no delimiter in a value
    prefix = f"8={BEGIN}{SOH}9={len(body.encode('utf-8'))}{SOH}{body}"
    msg = f"{prefix}10={checksum(prefix)}{SOH}"
    return msg if delim == SOH else msg.replace(SOH, delim)


def parse(raw: str) -> list[tuple[int, str]]:
    """Fields in order, after checking framing, BodyLength and CheckSum. Raises FixClientError."""
    raw = (raw or "").strip("\r\n ")
    if not raw:
        raise FixClientError("empty FIX reply")
    soh = raw.replace(PIPE, SOH) if SOH not in raw else raw
    if not soh.endswith(SOH):
        soh += SOH
    fields = []
    for tok in soh.split(SOH)[:-1]:
        tag, sep, val = tok.partition("=")
        if not sep or not tag.isdigit():
            raise FixClientError(f"malformed FIX field {tok[:30]!r}")
        fields.append((int(tag), val))
    if len(fields) < 4 or fields[0] != (8, BEGIN) or fields[1][0] != 9 or fields[2][0] != 35 or fields[-1][0] != 10:
        raise FixClientError("a FIX reply must start 8=FIX.4.4, 9, 35 and end with 10")
    tail_at = soh.rfind(f"{SOH}10=") + 1
    body = soh[len(f"8={BEGIN}{SOH}9={fields[1][1]}{SOH}"):tail_at]
    if not fields[1][1].isdigit() or int(fields[1][1]) != len(body.encode("utf-8")):
        raise FixClientError("FIX reply BodyLength does not match")
    if fields[-1][1] != checksum(soh[:tail_at]):
        raise FixClientError("FIX reply CheckSum does not match")
    return fields


def first(fields, tag: int) -> str | None:
    return next((v for t, v in fields if t == tag), None)


def readable(raw: str) -> list[dict]:
    soh = raw.replace(PIPE, SOH) if SOH not in raw else raw
    out = []
    for tok in soh.split(SOH):
        tag, sep, val = tok.partition("=")
        if sep and tag.isdigit():
            out.append({"tag": int(tag), "name": TAG_NAMES.get(int(tag), ""), "value": val})
    return out


def pipe(raw: str) -> str:
    return raw.replace(SOH, PIPE)


# ── message bodies ──────────────────────────────────────────────────────────────────────────────────────────────────
def _instrument(sec: dict) -> list[tuple[int, str]]:
    ids = sec.get("identifiers") or {}
    out = []
    if ids.get("ticker"):
        out.append((55, ids["ticker"]))
    if ids.get("cusip"):
        out += [(48, ids["cusip"]), (22, "1")]
    return out


def new_order_single(*, cl_ord_id: str, account: str, side: str, sec: dict, quantity=None, notional=None,
                     price_kind: str = "market", limit_price=None, block: bool = False) -> list[tuple[int, str]]:
    """NewOrderSingle (35=D) fields. ClOrdID = the Hollisworks instruction (or block) id; Account = the crosswalked
    external account id (a block: the executing reference); market / limit; TimeInForce 0 day."""
    if side not in SIDE:
        raise ValueError(f"the simulated FIX route carries buy and sell only (not {side!r})")
    f = [(11, cl_ord_id), (1, account), *_instrument(sec), (54, SIDE[side])]
    f += [(38, plain(quantity))] if quantity is not None else [(152, plain(notional))]
    f += [(40, "2" if price_kind == "limit" else "1")]
    if price_kind == "limit":
        f.append((44, plain(limit_price)))
    f += [(59, "0"), (60, stamp())]
    if block:
        f.append((591, "0"))      # ASSUMED convention: a block order — its AllocationInstruction follows
    return f


def status_request(*, cl_ord_id: str, order_id: str | None = None, side: str | None = None) -> list[tuple[int, str]]:
    return [(11, cl_ord_id), *([(37, order_id)] if order_id else []), *([(54, SIDE[side])] if side in SIDE else [])]


def cancel_request(*, cl_ord_id: str, orig_cl_ord_id: str, side: str, sec: dict, quantity=None, notional=None
                   ) -> list[tuple[int, str]]:
    f = [(41, orig_cl_ord_id), (11, cl_ord_id), *_instrument(sec), (54, SIDE[side]), (60, stamp())]
    f += [(38, plain(quantity))] if quantity is not None else ([(152, plain(notional))] if notional is not None else [])
    return f


def allocation_instruction(*, alloc_id: str, cl_ord_id: str, order_id: str, side: str, sec: dict, quantity,
                           avg_price, trade_date: str, legs: list[tuple[str, Any]]) -> list[tuple[int, str]]:
    """AllocationInstruction (35=J): 6 AvgPx is the HOLLISWORKS average (blocks.allocate's) — the counterparty books
    what it is told; each leg = (crosswalked account, quantity)."""
    f = [(70, alloc_id), (71, "0"), (626, "1"), (54, SIDE[side]), *_instrument(sec), (53, plain(quantity)),
         (6, plain(avg_price)), (75, trade_date), (73, "1"), (11, cl_ord_id), (37, order_id), (78, str(len(legs)))]
    for acct, q in legs:
        f += [(79, acct), (80, plain(q))]
    return f


# ── reading the replies ─────────────────────────────────────────────────────────────────────────────────────────────
def summarize(replies: list[list[tuple[int, str]]]) -> dict:
    """What the replies say: the order id, the final OrdStatus, every trade (150=F) in order, a reject reason, a
    session Reject, a cancel reject, an allocation ack."""
    out = {"order_id": None, "status": None, "trades": [], "reject_reason": None, "session_reject": None,
           "cancel_reject": None, "alloc": None, "cum_qty": None}
    for f in replies:
        mt = first(f, 35)
        if mt == "3":
            out["session_reject"] = first(f, 58) or "rejected (no reason given)"
        elif mt == "9":
            out["cancel_reject"] = {"reason_code": first(f, 102), "text": first(f, 58),
                                    "status": ORD_STATUS.get(first(f, 39) or "")}
            out["order_id"] = out["order_id"] or (first(f, 37) if first(f, 37) != "NONE" else None)
        elif mt == "P":
            accts, refs = [v for t, v in f if t == 79], [v for t, v in f if t == 467]
            out["alloc"] = {"alloc_id": first(f, 70), "accepted": first(f, 87) == "0", "status": first(f, 87),
                            "reject_code": first(f, 88), "text": first(f, 58),
                            "legs": dict(zip(accts, refs)) if len(accts) == len(refs) else {}}
        elif mt == "8":
            oid = first(f, 37)
            if oid and oid != "NONE":
                out["order_id"] = oid
            out["status"] = ORD_STATUS.get(first(f, 39) or "", first(f, 39))
            out["cum_qty"] = first(f, 14)
            if first(f, 150) == "F":
                out["trades"].append({"exec_id": first(f, 17), "quantity": first(f, 32), "price": first(f, 31),
                                      "transact_time": first(f, 60), "settle_date": first(f, 64)})
            if first(f, 39) == "8":
                out["reject_reason"] = first(f, 58) or "rejected by the simulated counterparty"
    return out


def transact_time(v: str) -> datetime:
    """``YYYYMMDD-HH:MM:SS.sss`` UTC → an aware datetime."""
    return datetime.strptime(v, "%Y%m%d-%H:%M:%S.%f").replace(tzinfo=timezone.utc)


# ── the session + the HTTP call ─────────────────────────────────────────────────────────────────────────────────────
async def comp_ids(conn, *, org_id: str, slug: str) -> tuple[str, str]:
    raw = await conn.fetchval("SELECT setting_value FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
                              str(org_id), SETTING_COMP_IDS)
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        v = None
    v = v if isinstance(v, dict) else {}
    sender = str(v.get("sender_comp_id") or DEFAULT_SENDER)[:64]
    target = str(v.get("target_comp_id") or slug.upper().replace("-", "_"))[:64]
    return sender, target


async def session(conn, *, org_id: str, environment: str = SIMULATOR) -> dict:
    """What a FIX call needs — api base, access token, CompIDs — read on ``conn`` so the caller RELEASES it before the
    HTTP call. Same refusals as the orders client (allowed flag, counterparty, active simulator connection,
    destination check)."""
    from services import altruist_trading as AT
    from services.altruist_oauth import simulator_counterparty

    if environment != SIMULATOR:
        raise AltruistOAuthError(NO_CONTRACT)
    s = await AT.order_session(conn, org_id=str(org_id), environment=environment)
    slug = await simulator_counterparty(conn, org_id=str(org_id))
    sender, target = await comp_ids(conn, org_id=str(org_id), slug=slug)
    return {**s, "slug": slug, "sender": sender, "target": target}


async def post_message(*, api_base: str, access_token: str, message: str, timeout: float = TIMEOUT_S,
                       transport: Any = None) -> list[str]:
    url = f"{api_base}{FIX_PATH}"
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        try:
            r = await client.post(url, content=message.encode("utf-8"),
                                  headers={"Authorization": f"Bearer {access_token}", "Content-Type": "text/plain",
                                           "Accept": "text/plain"})
        except httpx.HTTPError as exc:
            raise AltruistOAuthError(f"POST {url} failed at the transport layer: {type(exc).__name__}") from exc
    if r.status_code >= 400:
        try:
            detail = (r.json() or {}).get("detail")
        except ValueError:
            detail = None
        raise FixRefused(f"POST {url} was refused: HTTP {r.status_code}", status=r.status_code,
                         detail=str(detail)[:400] if detail else None)
    return [line for line in r.text.split("\n") if line.strip()]


async def send(sess: dict, msg_type: str, fields: list[tuple[int, Any]], *, seq: int = 1, delim: str = PIPE,
               timeout: float = TIMEOUT_S, transport: Any = None) -> dict:
    """ONE message → ``{sent, received, replies, summary}``: ``sent`` / ``received`` are the raw messages (readable
    '|' form), ``replies`` the parsed fields of each, ``summary`` :func:`summarize`. No database connection is held."""
    msg = build(msg_type, fields, sender=sess["sender"], target=sess["target"], seq=seq, delim=delim)
    received = await post_message(api_base=sess["api_base"], access_token=sess["access_token"], message=msg,
                                  timeout=timeout, transport=_transport_for(sess["environment"], transport))
    parsed = [parse(r) for r in received]
    for f in parsed:
        if first(f, 49) != sess["target"] or first(f, 56) != sess["sender"]:
            raise FixClientError("a FIX reply's CompIDs are not this session's (49 / 56 swapped back expected)")
    return {"sent": pipe(msg), "received": [pipe(r) for r in received], "replies": parsed,
            "summary": summarize(parsed)}
