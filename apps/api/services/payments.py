"""CASH07 — PAYMENT INSTRUCTIONS (stage A, manual): the governed way to move cash between a family's OWN accounts.

A payment instruction records what a PERSON asks the custodian to do — move ``amount`` USD from one account to another
account of the SAME owner in the SAME household, by a rail the source custodian declares, on a business-day value date,
for a stated purpose — and what people report: approved (by a DIFFERENT person, the S30 floor), sent (and how),
acknowledged, settled (ACH: provisionally, through its return window), reconciled against the custodian's transaction
feed (or confirmed by hand by someone other than the sender), closed — or rejected, returned, cancelled, expired.
NOTHING HERE SENDS MONEY: the manual adapter renders a sheet a person sends (services.payment_adapters).

STATUS FLOW (one vocabulary with trades, CASH_TRACK_PLAN v1.2 §2.2; enforced here first, then by the DB trigger)
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  draft ─submit→ pending_approval ─approve (human task; the S30 FLOOR: never a maker)→ approved
        ←reject─┘                                                     (the checker's reject returns it to draft)
  approved ─mark sent (person + channel; adapter manual)→ sent ─┬→ awaiting_client_consent (the custodian wants the
                                                                │   member's e-consent) ─→ acknowledged
                                                                └→ acknowledged ─settle→ settled ─reconcile→ reconciled
  reconciled ─close→ closed (ACH: only after the return window — "provisional until …")
  rejected   the custodian refused it (sent / awaiting consent / acknowledged), with its reason
  returned   an ACH return code (R01 …) after settlement (settled / reconciled)
  cancelled  any open state, with a reason (an open approval task is withdrawn)
  expired    the sweep: value date passed before it was sent; or consent never came (switched OFF by default)

Every transition writes ONE ``payment_events`` row — written BY THE DATABASE (``payment_instructions_event_trg``) from
the action / actor / detail this module sets LOCAL (``payments.*``); a status change naming no actor is refused by
``payment_events_human_chk``. Plus an ``audit_log`` row (``cash.payment.<action>``) and, after commit, a
``domain_events`` row (``payment.<action>``, source_type ``payment_event``). The row is archived on the SYSTEM axis
before every change (its id is stable: events, the approval task and sod_decisions point at it).

THE TWO AUTHORITY CHECKS (CASH_TRACK_PLAN v1.2 §2.3), both on every person's step that moves the instruction toward
money (draft, edit, submit, approve, mark sent), reusing their services — never copying them:
  * custody — services.trading_authority (SOC5): the ACTOR's tier for the source account's owner (entity) must be
    'limited' or higher for ``cash.payment_instruction`` (a same-owner move — no third party); 'inquiry' never.
  * account — services.authority (rep7b): the SOURCE account's effective authority must be managed
    (``authority.payment_refusal``).
Both reasons are reported together. Refusals (authority, third party, rail, value date) are audited
(``cash.payment.refused``); a maker-checker refusal is recorded in ``sod_decisions`` by services.sod.

SAME OWNER ONLY: both accounts of this org, in the instruction's household, owned by the same entity with the same
live account_owners set — otherwise "third-party payments come later" (the DB trigger refuses it too).

RAILS: only what the source custodian DECLARES (custodian_capabilities; 'unknown' is not available).
VALUE DATE: a US business day (services.us_business_days), not in the past. A weekend / holiday is REFUSED, naming the
next business day — never moved silently (a person decides; moving a payment can miss the obligation it funds).
BANK DETAILS: none stored. Accounts are platform accounts, shown by their stored mask (last four) only — never
``custodian_account_id``, never a full number, in any response, audit row, sheet or AI context.
AGENTS: none. The assistant may at most draft (declared, not registered — services.workflow_autonomy).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from services import authority as AU
from services import custodian_capabilities as CAP
from services import human_tasks as HT
from services import payment_adapters as PA
from services import sod
from services import trading_authority as TA
from services import us_business_days as BD
from services.bitemporal import archive_live_row
from services.money_text import fixed

log = logging.getLogger(__name__)

T = "public.payment_instructions"
T_EVT = "public.payment_events"
INITIATE = "initiate_payment"
APPROVE = "approve_payment"
READ_PERMISSIONS = ("view_portfolio", INITIATE, APPROVE)
TASK_KIND = "payment_instruction_approval"
REVIEW_KIND = "payment_reconcile_review"
SUBJECT = "payment_instruction"
EVENT_SOURCE = "payment_event"
MONEY_ACTION = "cash.payment_instruction"        # services.trading_authority.MONEY_MOVEMENT_ACTIONS
DISCLOSURE_KEY = "cash.payments"
SWEEP_ENV = "PAYMENT_SWEEP_ENABLED"              # the expiry / unmatched sweep: OFF unless set to "1"
SETTING_UNMATCHED = "cash.payments.unmatched_business_days"
SETTING_ACH_WINDOW = "cash.payments.ach_return_window_business_days"
DEFAULT_UNMATCHED_DAYS = 3
DEFAULT_ACH_WINDOW = 2
MATCH_BEFORE_BD = 2          # the feed window: value date − 2 business days …
MATCH_AFTER_BD = 5           # … to value date + 5 business days
CONSENT_GRACE_BD = 5         # awaiting the member's consent this long past the value date → expired
THIRD_PARTY = "third-party payments come later"

#: the offline fallback of the disclosure-library key ``cash.payments`` (a library version replaces it once published)
PAYMENTS_DISCLOSURE = (
    "A payment instruction asks the custodian to move money between your own accounts; the custodian moves it, on a "
    "business day. ACH transfers settle provisionally: the bank can return one (for example for insufficient funds) "
    "for a period after it settles, and a returned payment is reversed. Wires are final once sent. Nothing moves "
    "until a second person at the firm has approved the instruction.")

RAILS = (("ach", "ACH"), ("wire", "Wire"), ("journal", "Journal between accounts"), ("check", "Check"))
RAIL_KEYS = tuple(k for k, _ in RAILS)
STATUS_LABELS = {
    "draft": "Draft", "pending_approval": "Awaiting a second person's approval", "approved": "Approved — not sent",
    "sent": "Sent to the custodian", "awaiting_client_consent": "Awaiting the member's consent",
    "acknowledged": "Acknowledged by the custodian", "settled": "Settled", "reconciled": "Reconciled",
    "closed": "Closed", "rejected": "Rejected by the custodian", "returned": "Returned", "cancelled": "Cancelled",
    "expired": "Expired",
}
OPEN_STATUSES = ("draft", "pending_approval", "approved", "sent", "awaiting_client_consent", "acknowledged")
PRE_SEND = ("draft", "pending_approval", "approved")
TERMINAL = ("closed", "rejected", "returned", "cancelled", "expired")
ACTION_LABELS = {
    "create": "Drafted", "edit": "Edited", "submit": "Submitted for approval", "approve": "Approved",
    "reject": "Returned to draft by the checker", "mark_sent": "Marked sent", "consent_requested":
    "Member consent requested", "acknowledge": "Acknowledged by the custodian", "settle": "Settled",
    "custodian_reject": "Rejected by the custodian", "return": "Returned (ACH)", "reconcile": "Reconciled",
    "close": "Closed", "cancel": "Cancelled", "expire": "Expired", "sheet": "Payment sheet generated",
    "unmatched_alert": "Not matched to the custodian feed — review task opened", "transition": "Status changed",
}
RECONCILE_METHOD_LABELS = {"custodian_data": "Matched to the custodian's transaction feed",
                           "manual": "Confirmed manually (a different person from the sender)"}
PURPOSE_REF_LABELS = {"cash_obligation": "Cash obligation", "cash_proposal": "Cash proposal",
                      "cash_rule": "Cash rule"}   # CASH06 — an instruction a standing rule drafted
#: what a PERSON may link a draft to (CASH06: only a rule's firing — draft_and_submit — links an instruction to a rule)
PERSON_PURPOSE_REFS = ("cash_obligation", "cash_proposal")
RULE_ONLY_REF = "only a cash rule's own firing links a payment instruction to a cash rule"
EDITABLE = ("source_account_id", "destination_account_id", "amount", "rail", "value_date", "purpose",
            "purpose_ref_type", "purpose_ref_id")


class PaymentError(ValueError):
    """A request the rules refuse (422)."""


class PaymentNotFound(LookupError):
    pass


class PaymentConflict(Exception):
    """The instruction's state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    pass


class PaymentAuthorityError(PermissionError):
    """One or both authority checks refused (403); ``reasons`` lists each."""

    def __init__(self, reasons: list[str]):
        super().__init__(" · ".join(reasons))
        self.reasons = list(reasons)


class ThirdPartyRefused(PaymentError):
    pass


# ── time (injectable: the verify fixes "today") ─────────────────────────────────────────────────────────────────────
_CLOCK: dict = {"fn": None}


def set_clock(fn) -> None:
    _CLOCK["fn"] = fn


def today() -> date:
    return _CLOCK["fn"]() if _CLOCK["fn"] else date.today()


# ── small helpers ───────────────────────────────────────────────────────────────────────────────────────────────────
def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: fixed(o) if isinstance(o, Decimal) else str(o) if isinstance(o, uuid.UUID)
                      else o.isoformat() if isinstance(o, (date, datetime)) else str(o))


def _uuid(v, what: str = "payment instruction") -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise PaymentNotFound(f"{what} not found") from None


def _iso(v):
    return v.isoformat() if v else None


def money(v) -> str | None:
    return fixed(v.quantize(Decimal("0.01"))) if isinstance(v, Decimal) else fixed(v)


def parse_amount(v) -> Decimal:
    """Decimal > 0 with at most two decimals; a binary float is refused (never converted through float)."""
    if isinstance(v, float) or isinstance(v, bool):
        raise PaymentError("the amount is a decimal string (e.g. \"25000.00\"), never a binary float")
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v).strip())
    except (InvalidOperation, AttributeError):
        raise PaymentError("the amount must be a number") from None
    if not d.is_finite() or d <= 0:
        raise PaymentError("the amount must be greater than zero")
    if d != d.quantize(Decimal("0.01")):
        raise PaymentError("the amount has at most two decimals (cents)")
    if d >= Decimal("1e18"):
        raise PaymentError("the amount is too large")
    return d.quantize(Decimal("0.01"))


def parse_date(v, what: str) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except (TypeError, ValueError):
        raise PaymentError(f"{what} is a date (YYYY-MM-DD)") from None


def add_business_days(d: date, n: int) -> date:
    """``d`` + ``n`` US business days (services.us_business_days — the ONE calendar)."""
    step = 1 if n >= 0 else -1
    out, left = d, abs(n)
    while left:
        out += timedelta(days=step)
        if BD.is_business_day(out):
            left -= 1
    return out


def business_days_between(a: date, b: date) -> int:
    """Business days strictly after ``a`` up to and including ``b`` (0 when b <= a)."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if BD.is_business_day(d):
            n += 1
    return n


def value_date_refusal(d: date, t: date) -> str | None:
    """THE RULE: a value date is a US business day, today or later. A weekend / holiday is REFUSED, naming the next
    business day (never moved silently)."""
    if d < t:
        return f"the value date {d.isoformat()} is in the past (today is {t.isoformat()})"
    if not BD.is_business_day(d):
        why = BD.holiday_name(d)
        what = (f"a holiday ({why.replace('_', ' ')})" if why else ("a Saturday" if d.weekday() == 5 else "a Sunday"))
        return (f"the value date {d.isoformat()} is {what}, not a business day — choose a business day "
                f"(the next one is {BD.next_business_day(d).isoformat()})")
    return None


async def _setting_int(conn, org_id, key: str, default: int) -> int:
    from services.org_settings import get_setting

    try:
        v = await get_setting(conn, str(org_id), key)
        n = int(v) if v is not None else default
        return n if 0 <= n <= 60 else default
    except (TypeError, ValueError):
        return default


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, 'payment_instruction', $4::uuid, $5::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"cash.payment.{action}",
        str(resource_id) if resource_id else None, _dumps(payload))


async def _refused(org_id, actor_id, what: str, reasons: list[str], payment_id=None) -> None:
    """A refusal is audited on a FRESH pooled connection (the caller is about to raise). Never raises."""
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as c:
            await _audit(c, org_id, actor_id, "refused", payment_id, {"what": what, "reasons": reasons})
    except Exception:  # noqa: BLE001
        log.exception("payments: could not audit a refusal (%s)", what)


async def _publish(pool, org_id, action: str, payment_id, payload: dict, actor_id, event_id=None) -> None:
    from services.domain_events import publish_event

    try:
        await publish_event(pool, str(org_id), f"payment.{action}", EVENT_SOURCE, str(event_id or payment_id),
                            json.loads(_dumps({"payment_id": str(payment_id), **payload})),
                            created_by=str(actor_id) if actor_id else None)
    except Exception:  # noqa: BLE001 — the event is a notification; the write stands
        log.exception("payment.%s event not published", action)


async def _human(conn, actor_id, org_id) -> dict:
    try:
        return await HT.assert_human(conn, actor_id, org_id)
    except HT.AgentActorRefused as exc:
        raise PaymentError(str(exc)) from exc


# ── reading ─────────────────────────────────────────────────────────────────────────────────────────────────────────
async def load(conn, org_id, payment_id, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), _uuid(payment_id), str(org_id))
    if row is None:
        raise PaymentNotFound("payment instruction not found")
    return dict(row)


ACCOUNT_COLS = ("id, org_id, account_number_masked, custodian_code, registration_type, tax_status, primary_entity_id, "
                "household_id, is_discretionary, is_held_away, closed_on")


async def load_account(conn, org_id, account_id, t: date | None = None) -> dict:
    row = await conn.fetchrow(f"SELECT {ACCOUNT_COLS} FROM public.accounts WHERE id = $1::uuid AND org_id = $2::uuid "
                              "AND system_to IS NULL AND valid_to IS NULL", _uuid(account_id, "account"), str(org_id))
    if row is None:
        raise PaymentNotFound("account not found")
    acct = dict(row)
    await AU.attach(conn, org_id, acct, t or today())
    return acct


async def _owners(conn, account_id) -> list[str]:
    return sorted(str(r["entity_id"]) for r in await conn.fetch(
        "SELECT DISTINCT entity_id FROM public.account_owners WHERE account_id = $1::uuid AND valid_to IS NULL "
        "AND system_to IS NULL", str(account_id)))


async def household_accounts(conn, org_id, household_id, t: date | None = None) -> list[dict]:
    rows = [dict(r) for r in await conn.fetch(
        f"SELECT {ACCOUNT_COLS} FROM public.accounts WHERE org_id = $1::uuid AND household_id = $2::uuid "
        "AND system_to IS NULL AND valid_to IS NULL ORDER BY account_number_masked, id", str(org_id), str(household_id))]
    for a in rows:
        await AU.attach(conn, org_id, a, t or today())
        a["owners"] = await _owners(conn, a["id"])
    return rows


async def _names(conn, ids) -> dict[str, str]:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = await conn.fetch("SELECT id, full_name FROM public.users WHERE id = ANY($1::uuid[])", ids)
    return {str(r["id"]): (r["full_name"] or "A colleague") for r in rows}


async def _entity_names(conn, ids) -> dict[str, str]:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = await conn.fetch("SELECT DISTINCT ON (id) id, display_name FROM public.entities WHERE id = ANY($1::uuid[]) "
                            "ORDER BY id, valid_to NULLS FIRST", ids)
    return {str(r["id"]): r["display_name"] for r in rows}


def same_owner_refusal(src: dict, dst: dict, household_id, src_owners: list, dst_owners: list) -> str | None:
    """None when the two accounts are a same-owner pair of this household; else the third-party refusal."""
    if str(src["id"]) == str(dst["id"]):
        return "the source and destination are the same account"
    hh = str(household_id)
    if str(src.get("household_id")) != hh or str(dst.get("household_id")) != hh:
        return f"{THIRD_PARTY}: both accounts must belong to this household"
    if str(src["primary_entity_id"]) != str(dst["primary_entity_id"]) or sorted(src_owners) != sorted(dst_owners):
        return f"{THIRD_PARTY}: the two accounts must have the same owner"
    return None


async def declared_rails(conn, account: dict) -> dict[str, dict]:
    """{rail: {available, value, evidence}} for the account's custodian (custodian_capabilities)."""
    key = PA.custodian_key(account)
    out = {}
    for rail in RAIL_KEYS:
        try:
            cap = await CAP.capability(conn, key, rail)
        except CAP.CapabilityError:
            cap = CAP.Capability("unknown", "no capability record", None)
        out[rail] = {"available": CAP.available(cap), "value": cap.value, "evidence": cap.evidence}
    return out


async def rail_refusal(conn, account: dict, rail: str) -> str | None:
    if rail not in RAIL_KEYS:
        return f"rail is one of {list(RAIL_KEYS)}"
    r = (await declared_rails(conn, account))[rail]
    if r["available"]:
        return None
    return (f"the source custodian ({PA.custodian_key(account) or 'unknown'}) does not declare {dict(RAILS)[rail]} "
            f"(capability '{rail}' is {r['value']}) — only a declared rail can be used")


async def authority_refusals(pool, conn, org_id, actor_id, src: dict) -> list[str]:
    """BOTH checks (§2.3), each through its own service; every refusal reason, together."""
    reasons = []
    try:
        await TA.assert_can_propose(pool, str(org_id), str(src["primary_entity_id"]), str(actor_id), MONEY_ACTION)
    except TA.AuthorityError as exc:
        reasons.append(f"trading authority (custody): {exc} — 'inquiry' is view only; a same-owner payment needs "
                       f"'{TA.required_tier(MONEY_ACTION)}'")
    why = AU.payment_refusal(src)
    if why:
        reasons.append(f"account authority: {why}")
    return reasons


async def _purpose_ref(conn, org_id, household_id, ref_type, ref_id) -> dict | None:
    if ref_type is None and ref_id is None:
        return None
    if ref_type not in PURPOSE_REF_LABELS or ref_id is None:
        raise PaymentError(f"a purpose link is one of {list(PURPOSE_REF_LABELS)} with its id")
    from services import household_total as HHT

    members = set(await HHT.household_members(conn, org_id, household_id))
    if ref_type == "cash_obligation":
        r = await conn.fetchrow("SELECT id, name, entity_id, due_date, amount FROM public.cash_obligations "
                                "WHERE id = $1::uuid AND org_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL",
                                _uuid(ref_id, "obligation"), str(org_id))
        if r is None or str(r["entity_id"]) not in members:
            raise PaymentError("the linked obligation is not one of this household's")
        return {"type": ref_type, "id": str(r["id"]), "label": f"{r['name']} — due {r['due_date'].isoformat()}"}
    if ref_type == "cash_rule":
        # CASH06 — the rule's stable id (cash_rules.rule_id); the rule must be this household's
        r = await conn.fetchrow("SELECT rule_id, name FROM public.cash_rules WHERE rule_id = $1::uuid AND org_id = "
                                "$2::uuid AND household_id = $3::uuid AND system_to IS NULL ORDER BY version_no DESC "
                                "LIMIT 1", _uuid(ref_id, "cash rule"), str(org_id), str(household_id))
        if r is None:
            raise PaymentError("the linked cash rule is not one of this household's")
        return {"type": ref_type, "id": str(r["rule_id"]), "label": f"Cash rule — {r['name']}"}
    r = await conn.fetchrow("SELECT p.id, p.status, a.household_id FROM public.rebalance_proposals p JOIN public.accounts "
                            "a ON a.id = p.account_id AND a.system_to IS NULL AND a.valid_to IS NULL WHERE p.id = "
                            "$1::uuid AND p.org_id = $2::uuid AND p.proposal_kind = 'cash' AND p.system_to IS NULL",
                            _uuid(ref_id, "proposal"), str(org_id))
    if r is None or str(r["household_id"]) != str(household_id):
        raise PaymentError("the linked cash proposal is not one of this household's")
    return {"type": ref_type, "id": str(r["id"]), "label": f"Cash proposal ({r['status']})"}


# ── writing primitives ──────────────────────────────────────────────────────────────────────────────────────────────
async def _archive(conn, payment_id) -> None:
    """System axis (Rule 3): the SHARED helper services.bitemporal.archive_live_row — never a copy of its SQL."""
    if await archive_live_row(conn, T, payment_id) is None:
        raise PaymentConflict("the payment instruction is not live — refusing to change it")


async def _event_context(conn, *, action: str, actor_id, detail: dict) -> str:
    """Name the transition for the DB's event trigger (transaction-local). Returns the event id it will write."""
    eid = str(uuid.uuid4())
    await conn.execute(
        "SELECT set_config('payments.event_id', $1, true), set_config('payments.action', $2, true), "
        "set_config('payments.actor_id', $3, true), set_config('payments.detail', $4, true)",
        eid, action, str(actor_id) if actor_id else "", _dumps(detail))
    return eid


async def _clear_event_context(conn) -> None:
    await conn.execute("SELECT set_config('payments.event_id', '', true), set_config('payments.action', '', true), "
                       "set_config('payments.actor_id', '', true), set_config('payments.detail', '', true)")


async def _update(conn, row: dict, fields: dict, *, action: str | None = None, actor_id=None,
                  detail: dict | None = None) -> tuple[dict, str | None]:
    """Archive, then update the live row (same id). A status change names its action / actor for the DB event."""
    await _archive(conn, row["id"])
    eid = None
    if "status" in fields and fields["status"] != row["status"]:
        eid = await _event_context(conn, action=action or "transition", actor_id=actor_id, detail=detail or {})
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    new = await conn.fetchrow(f"UPDATE {T} SET {sets}, updated_at = now(), system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(row["id"]), *[fields[c] for c in cols])
    if eid:
        await _clear_event_context(conn)
    if new is None:
        raise PaymentConflict("the payment instruction changed underneath this request — reload and retry")
    return dict(new), eid


async def _plain_event(conn, *, org_id, payment_id, action: str, status: str, actor_id, detail: dict) -> str:
    """An action that changes no status (edit, sheet, unmatched alert) — inserted by the service."""
    return str(await conn.fetchval(
        f"INSERT INTO {T_EVT} (org_id, payment_id, action, from_status, to_status, actor_id, detail) VALUES ($1::uuid, "
        "$2::uuid, $3, $4, $4, $5::uuid, $6::jsonb) RETURNING id", str(org_id), str(payment_id), action, status,
        str(actor_id) if actor_id else None, _dumps(detail)))


def _need(row: dict, statuses: tuple[str, ...], what: str) -> None:
    if row["status"] not in statuses:
        raise PaymentConflict(f"cannot {what} a payment instruction that is "
                              f"{STATUS_LABELS.get(row['status'], row['status']).lower()} (allowed from: "
                              f"{', '.join(STATUS_LABELS[s].lower() for s in statuses)})")


async def _validate(pool, conn, *, org_id, actor_id, household_id, data: dict, t: date, what: str) -> dict:
    """Every rule a draft must pass, ALL reasons gathered: the accounts, same owner, the rail, the value date, the
    purpose link, both authority checks. Returns the normalised fields."""
    try:
        src = await load_account(conn, org_id, data.get("source_account_id"), t)
        dst = await load_account(conn, org_id, data.get("destination_account_id"), t)
    except PaymentNotFound as exc:
        raise PaymentError(f"{exc}") from None
    amount = parse_amount(data.get("amount"))
    if (data.get("currency_code") or "USD") != "USD":
        raise PaymentError("payments are in USD")
    rail = str(data.get("rail") or "")
    vdate = parse_date(data.get("value_date"), "the value date")
    purpose = (data.get("purpose") or "").strip()
    if not purpose:
        raise PaymentError("a payment needs a purpose")
    third = same_owner_refusal(src, dst, household_id, await _owners(conn, src["id"]), await _owners(conn, dst["id"]))
    if third:
        await _refused(org_id, actor_id, what, [third])
        raise ThirdPartyRefused(third)
    problems = [x for x in (await rail_refusal(conn, src, rail), value_date_refusal(vdate, t)) if x]
    if problems:
        await _refused(org_id, actor_id, what, problems)
        raise PaymentError(" · ".join(problems))
    ref = await _purpose_ref(conn, org_id, household_id, data.get("purpose_ref_type"), data.get("purpose_ref_id"))
    reasons = await authority_refusals(pool, conn, org_id, actor_id, src)
    if reasons:
        await _refused(org_id, actor_id, what, reasons)
        raise PaymentAuthorityError(reasons)
    return {"source_account_id": uuid.UUID(str(src["id"])), "destination_account_id": uuid.UUID(str(dst["id"])),
            "amount": amount, "rail": rail, "value_date": vdate, "purpose": purpose,
            "purpose_ref_type": ref["type"] if ref else None,
            "purpose_ref_id": uuid.UUID(ref["id"]) if ref else None, "_src": src, "_dst": dst}


def _terms(f: dict) -> dict:
    return {k: (money(v) if isinstance(v, Decimal) else (str(v) if isinstance(v, uuid.UUID) else v))
            for k, v in f.items() if not k.startswith("_")}


# ── draft / edit / submit ───────────────────────────────────────────────────────────────────────────────────────────
async def draft(pool, *, org_id, actor_id, household_id, data: dict) -> dict:
    if data.get("purpose_ref_type") == "cash_rule":
        raise PaymentError(RULE_ONLY_REF)
    t = today()
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        f = await _validate(pool, conn, org_id=org_id, actor_id=actor["id"], household_id=household_id, data=data, t=t,
                            what="draft a payment instruction")
        aid = uuid.UUID(str(actor["id"]))
        async with conn.transaction():
            await _event_context(conn, action="create", actor_id=aid, detail={"terms": _terms(f)})
            row = await conn.fetchrow(
                f"""INSERT INTO {T} (org_id, household_id, source_account_id, destination_account_id, amount, rail,
                        value_date, purpose, purpose_ref_type, purpose_ref_id, status, maker_id, maker_ids)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, 'draft', $11, ARRAY[$11]::uuid[])
                    RETURNING *""",
                str(org_id), str(household_id), f["source_account_id"], f["destination_account_id"], f["amount"],
                f["rail"], f["value_date"], f["purpose"], f["purpose_ref_type"], f["purpose_ref_id"], aid)
            await _clear_event_context(conn)
            await _audit(conn, org_id, aid, "created", row["id"], {"household_id": str(household_id), **_terms(f),
                                                                    "source": f["_src"]["account_number_masked"],
                                                                    "destination": f["_dst"]["account_number_masked"]})
    await _publish(pool, org_id, "create", row["id"], {"status": "draft"}, actor["id"])
    return dict(row)


async def edit(pool, *, org_id, actor_id, payment_id, changes: dict) -> dict:
    """A DRAFT's terms change (system-axis archive; the editor joins maker_ids — every editor is a maker)."""
    bad = set(changes) - set(EDITABLE)
    if bad:
        raise PaymentError(f"not editable: {sorted(bad)}")
    if changes.get("purpose_ref_type") == "cash_rule":
        raise PaymentError(RULE_ONLY_REF)
    t = today()
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        cur = await load(conn, org_id, payment_id)
        _need(cur, ("draft",), "edit")
        merged = {k: cur[k] for k in EDITABLE}
        merged.update(changes)
        f = await _validate(pool, conn, org_id=org_id, actor_id=actor["id"], household_id=cur["household_id"],
                            data=merged, t=t, what="edit a payment instruction")
        aid = uuid.UUID(str(actor["id"]))
        async with conn.transaction():
            row = await load(conn, org_id, payment_id, lock=True)
            _need(row, ("draft",), "edit")
            makers = list(row["maker_ids"]) + ([aid] if aid not in row["maker_ids"] else [])
            fields = {k: v for k, v in f.items() if not k.startswith("_")}
            new, _ = await _update(conn, row, {**fields, "maker_ids": makers})
            eid = await _plain_event(conn, org_id=org_id, payment_id=row["id"], action="edit", status="draft",
                                     actor_id=aid, detail={"changes": _terms({k: fields[k] for k in changes
                                                                              if k in fields})})
            await _audit(conn, org_id, aid, "edited", row["id"], {"event_id": eid, "changes": sorted(changes)})
    await _publish(pool, org_id, "edit", new["id"], {"status": "draft"}, actor["id"], eid)
    return new


def _task_title(row: dict, src: dict, dst: dict) -> str:
    return (f"Approve payment: {money(row['amount'])} USD {dict(RAILS)[row['rail']]} from {src['account_number_masked']} "
            f"to {dst['account_number_masked']} on {row['value_date'].isoformat()}")


async def submit(pool, *, org_id, actor_id, payment_id) -> dict:
    """draft → pending_approval; opens the approval task (kind payment_instruction_approval, maker-checker). The
    submitter is a maker. Every rule is re-checked at submit (authority as of now, the value date, the rail)."""
    t = today()
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payment_id)
        _need(row, ("draft",), "submit")
        f = await _validate(pool, conn, org_id=org_id, actor_id=actor["id"], household_id=row["household_id"],
                            data={k: row[k] for k in EDITABLE}, t=t, what="submit a payment instruction")
    aid = uuid.UUID(str(actor["id"]))
    makers = sorted({str(m) for m in row["maker_ids"]} | {str(aid)})
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=row["id"], maker_id=actor["id"],
        eligible_permission=APPROVE, title=_task_title(row, f["_src"], f["_dst"]),
        payload={"amount": money(row["amount"]), "currency": "USD", "rail": row["rail"],
                 "value_date": row["value_date"].isoformat(), "source": f["_src"]["account_number_masked"],
                 "destination": f["_dst"]["account_number_masked"], "purpose": row["purpose"],
                 # every drafter / editor + the submitter: the inbox treats each as a maker (human_tasks.task_makers)
                 "maker_ids": makers, "floor": "no self-approval, ever (S30 floor — Super Admin included)"},
        action_path=f"/portfolio/households/{row['household_id']}?tab=treasury&payment={row['id']}#treasury-payments")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, payment_id, lock=True)
                _need(cur, ("draft",), "submit")
                new, eid = await _update(conn, cur, {"status": "pending_approval", "approval_task_id":
                                                     uuid.UUID(task["id"]), "maker_ids": [uuid.UUID(m) for m in makers]},
                                         action="submit", actor_id=aid,
                                         detail={"task_id": task["id"], "notified": task.get("notified", [])})
                await _audit(conn, org_id, aid, "submitted", cur["id"], {"event_id": eid, "task_id": task["id"]})
    except Exception:
        await _withdraw_task(pool, task["id"], "the payment instruction could not be submitted")
        raise
    await _publish(pool, org_id, "submit", new["id"], {"task_id": task["id"]}, actor["id"], eid)
    return {**new, "task": task}


async def draft_and_submit(pool, *, org_id, actor_id, household_id, data: dict) -> dict:
    """CASH06 — the ONE verb a cash rule's firing calls: draft + submit, the instruction row written in ONE database
    transaction (it is never seen as a lone draft). ``actor_id`` is the RULE OWNER — the maker of the instruction, so
    the cash07 floor makes its checker someone else. Every rule a person's draft passes is checked first (same owner,
    the declared rail, a business-day value date, both authority checks for the owner).

    The approval task is opened FIRST, by the engine on its own connections (services.human_tasks.open_task — the same
    reason ``submit`` is two steps), for an instruction id chosen here; then the INSERT (status draft — the DB writes the
    'create' event) and the move to pending_approval (the 'submit' event) commit together. On any failure the task is
    withdrawn and nothing remains. NOTHING IS APPROVED OR SENT HERE: the result is pending_approval."""
    t = today()
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        f = await _validate(pool, conn, org_id=org_id, actor_id=actor["id"], household_id=household_id, data=data, t=t,
                            what="draft a payment instruction from a cash rule")
    aid = uuid.UUID(str(actor["id"]))
    pid = uuid.uuid4()
    probe = {"amount": f["amount"], "rail": f["rail"], "value_date": f["value_date"]}
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=pid, maker_id=actor["id"],
        eligible_permission=APPROVE, title=_task_title(probe, f["_src"], f["_dst"]),
        payload={"amount": money(f["amount"]), "currency": "USD", "rail": f["rail"],
                 "value_date": f["value_date"].isoformat(), "source": f["_src"]["account_number_masked"],
                 "destination": f["_dst"]["account_number_masked"], "purpose": f["purpose"],
                 "drafted_by_rule": str(f["purpose_ref_id"]) if f["purpose_ref_type"] == "cash_rule" else None,
                 "maker_ids": [str(aid)], "floor": "no self-approval, ever (S30 floor — Super Admin included); the "
                                                   "rule's owner is this instruction's maker"},
        action_path=f"/portfolio/households/{household_id}?tab=treasury&payment={pid}#treasury-payments")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _event_context(conn, action="create", actor_id=aid, detail={"terms": _terms(f), "by_rule": True})
                row = await conn.fetchrow(
                    f"""INSERT INTO {T} (id, org_id, household_id, source_account_id, destination_account_id, amount,
                            rail, value_date, purpose, purpose_ref_type, purpose_ref_id, status, maker_id, maker_ids)
                        VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12,
                                ARRAY[$12]::uuid[]) RETURNING *""",
                    pid, str(org_id), str(household_id), f["source_account_id"], f["destination_account_id"],
                    f["amount"], f["rail"], f["value_date"], f["purpose"], f["purpose_ref_type"], f["purpose_ref_id"],
                    aid)
                await _clear_event_context(conn)
                await _audit(conn, org_id, aid, "created", row["id"], {"household_id": str(household_id), **_terms(f),
                                                                        "source": f["_src"]["account_number_masked"],
                                                                        "destination": f["_dst"]["account_number_masked"],
                                                                        "by_rule": True})
                new, eid = await _update(conn, dict(row), {"status": "pending_approval", "approval_task_id":
                                                           uuid.UUID(task["id"]), "maker_ids": [aid]},
                                         action="submit", actor_id=aid,
                                         detail={"task_id": task["id"], "notified": task.get("notified", []),
                                                 "by_rule": True})
                await _audit(conn, org_id, aid, "submitted", row["id"], {"event_id": eid, "task_id": task["id"],
                                                                          "by_rule": True})
    except Exception:
        await _withdraw_task(pool, task["id"], "the rule's payment instruction could not be drafted")
        raise
    await _publish(pool, org_id, "create", new["id"], {"status": "draft", "by_rule": True}, actor["id"])
    await _publish(pool, org_id, "submit", new["id"], {"task_id": task["id"], "by_rule": True}, actor["id"], eid)
    return {**new, "task": task}


async def _withdraw_task(pool, task_id, reason: str) -> None:
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        log.info("task %s was not open to withdraw", task_id)


# ── approval (the S30 FLOOR) ────────────────────────────────────────────────────────────────────────────────────────
def _refuse(_d) -> Exception:
    return MakerCheckerViolation("maker-checker (S30 floor): you drafted, edited or submitted this payment, so a "
                                 f"different holder of {APPROVE} must approve it — no setting and no role (Super Admin "
                                 "included) relaxes this")


async def _checker_refusals(pool, conn, org_id, actor_id, row: dict, t: date) -> list[str]:
    src = await load_account(conn, org_id, row["source_account_id"], t)
    reasons = await authority_refusals(pool, conn, org_id, actor_id, src)
    vd = value_date_refusal(row["value_date"], t)
    if vd:
        reasons.append(vd)
    return reasons


async def _precheck(pool, task, outcome, actor_id):
    """BEFORE the engine completes the task (it stays open on any refusal): still pending; on approve, the floor
    against EVERY maker (the task itself knows only the submitter), the checker's own authority, the value date."""
    async with pool.acquire() as conn:
        row = await load(conn, task["org_id"], task["subject_id"])
        if row["status"] != "pending_approval":
            raise HT.HumanTaskConflict(f"the payment instruction is {row['status']}, not awaiting approval")
        if outcome != "approve":
            return
        await sod.gate(conn, org_id=task["org_id"], item_type=sod.PAYMENT_INSTRUCTION, checker_id=actor_id,
                       maker_ids=[str(m) for m in row["maker_ids"]], item_id=str(row["id"]),
                       household_id=row["household_id"], site="payments._precheck", refuse=_refuse)
        reasons = await _checker_refusals(pool, conn, task["org_id"], actor_id, row, today())
    if reasons:
        await _refused(task["org_id"], actor_id, "approve a payment instruction", reasons, row["id"])
        raise PaymentAuthorityError(reasons)


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    """The decision lands (idempotent): approve → approved; reject → back to draft (task cleared)."""
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid AND "
                                      "system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if row is None or row["status"] != "pending_approval":
                return dict(row) if row else None
            row = dict(row)
            checker = uuid.UUID(str(actor_id))
            if outcome == "approve":
                await sod.gate(conn, org_id=org_id, item_type=sod.PAYMENT_INSTRUCTION, checker_id=checker,
                               maker_ids=[str(m) for m in row["maker_ids"]], item_id=str(row["id"]),
                               household_id=row["household_id"], site="payments._apply", refuse=_refuse)
                new, eid = await _update(conn, row, {"status": "approved", "approved_by": checker,
                                                     "approved_at": datetime.now(timezone.utc)},
                                         action="approve", actor_id=checker, detail={"task_id": task["id"], "note": note})
            else:
                new, eid = await _update(conn, row, {"status": "draft", "approval_task_id": None},
                                         action="reject", actor_id=checker, detail={"task_id": task["id"], "note": note})
            await _audit(conn, org_id, checker, "approved" if outcome == "approve" else "rejected", row["id"],
                         {"event_id": eid, "task_id": task["id"], "note": note})
    await _publish(pool, org_id, "approve" if outcome == "approve" else "reject", new["id"], {"task_id": task["id"]},
                   actor_id, eid)
    return new


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK_KIND, label="Payment instruction approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)
HT.register_precheck(TASK_KIND, _precheck)
# the unmatched-settlement review (opened by the sweep — a system review, never maker-checker)
HT.register_kind(REVIEW_KIND, label="Payment not matched to the custodian feed", outcomes=("done",),
                 maker_checker=False, system_open=True)


async def decide(pool, *, org_id, actor_id, payment_id, outcome: str, note: str | None = None) -> dict:
    """The ONE decision path (the route and bulk approve both call it): the S30 floor against every maker (a refusal is
    recorded in sod_decisions and audited, nothing changes), then services.human_tasks.complete_task (which re-checks
    the floor, the approve_payment permission and this module's precheck)."""
    if outcome not in ("approve", "reject"):
        raise PaymentError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise PaymentError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payment_id)
    _need(row, ("pending_approval",), "approve or reject")
    async with pool.acquire() as conn:
        await sod.gate(conn, org_id=org_id, item_type=sod.PAYMENT_INSTRUCTION, checker_id=actor["id"],
                       maker_ids=[str(m) for m in row["maker_ids"]], item_id=str(row["id"]),
                       household_id=row["household_id"], site="payments.decide", refuse=_refuse)
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=(note or "").strip() or None,
                                      sod_household_id=row["household_id"])
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise PaymentConflict(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await load(conn, org_id, payment_id)
    return result


# ── the sheet and sending (the adapter) ─────────────────────────────────────────────────────────────────────────────
SHEET_STATUSES = ("approved", "sent", "awaiting_client_consent", "acknowledged", "settled", "reconciled", "closed")


async def adapter_of(conn, org_id, row: dict) -> PA.PaymentAdapter:
    src = await load_account(conn, org_id, row["source_account_id"])
    return PA.adapter_for(src, await PA.enabled_for_org(conn, org_id))


async def sheet(pool, *, org_id, actor_id, payment_id) -> tuple[bytes, str]:
    """The manual adapter's "send": the printable payment sheet (masked accounts only). Logged (event 'sheet' + audit);
    the status does not change — the operator then confirms the channel (:func:`mark_sent`)."""
    from services import disclosures as DS

    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payment_id)
        _need(row, SHEET_STATUSES, "print the sheet of")
        adapter = await adapter_of(conn, org_id, row)
        d = await detail(conn, org_id, payment_id)
        org_name = await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))
        disc = await DS.resolve_safe(conn, DISCLOSURE_KEY, org_id=org_id, household_id=row["household_id"])
    try:
        res = await adapter.send(d, org_name=org_name or "", prepared_by=actor.get("full_name") or "",
                                 disclosure=disc.get("text"))
    except PA.PaymentAdapterNotConnected as exc:
        await _refused(org_id, actor["id"], "send a payment instruction", [str(exc)], row["id"])
        raise PaymentConflict(str(exc)) from exc
    async with pool.acquire() as conn:
        async with conn.transaction():
            eid = await _plain_event(conn, org_id=org_id, payment_id=row["id"], action="sheet", status=row["status"],
                                     actor_id=actor["id"], detail={"adapter": res.adapter, "file_name": res.file_name})
            await _audit(conn, org_id, actor["id"], "sheet", row["id"], {"event_id": eid, "adapter": res.adapter})
    await _publish(pool, org_id, "sheet", row["id"], {"adapter": res.adapter}, actor["id"], eid)
    return res.document or b"", res.file_name or "payment-sheet.pdf"


async def _transition(pool, *, org_id, actor_id, payment_id, allowed: tuple, what: str, action: str, to_status: str,
                      fields, detail: dict | None = None, check=None) -> dict:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, payment_id, lock=True)
            _need(row, allowed, what)
            if check is not None:
                await check(conn, row, actor)
            extra = fields(row, actor) if callable(fields) else fields
            new, eid = await _update(conn, row, {"status": to_status, **extra}, action=action, actor_id=actor["id"],
                                     detail=detail or {})
            await _audit(conn, org_id, actor["id"], action, row["id"], {"event_id": eid, "from": row["status"],
                                                                        "to": to_status, **(detail or {})})
    await _publish(pool, org_id, action, new["id"], {"from_status": row["status"], "to_status": to_status},
                   actor["id"], eid)
    return new


async def mark_sent(pool, *, org_id, actor_id, payment_id, channel: str, note: str | None = None,
                    external_ref: str | None = None) -> dict:
    """approved → sent, when the operator confirms the channel they sent the sheet by. Re-checks the sender's two
    authority checks, the rail and the value date; a not-connected adapter refuses with its reason."""
    if channel not in PA.CHANNEL_LABELS:
        raise PaymentError(f"channel is one of {list(PA.CHANNEL_LABELS)}")
    t = today()

    async def _check(conn, row, actor):
        adapter = await adapter_of(conn, org_id, row)
        if not adapter.connected:
            reason = getattr(adapter, "reason", "the adapter is not connected")
            await _refused(org_id, actor["id"], "send a payment instruction", [reason], row["id"])
            raise PaymentConflict(reason)
        src = await load_account(conn, org_id, row["source_account_id"], t)
        reasons = [x for x in (await rail_refusal(conn, src, row["rail"]), value_date_refusal(row["value_date"], t)) if x]
        reasons += await authority_refusals(pool, conn, org_id, actor["id"], src)
        if reasons:
            await _refused(org_id, actor["id"], "send a payment instruction", reasons, row["id"])
            raise PaymentAuthorityError(reasons)

    now = datetime.now(timezone.utc)
    return await _transition(
        pool, org_id=org_id, actor_id=actor_id, payment_id=payment_id, allowed=("approved",), what="mark sent",
        action="mark_sent", to_status="sent", check=_check,
        fields=lambda row, a: {"sent_by": uuid.UUID(str(a["id"])), "sent_at": now, "sent_channel": channel,
                               "sent_note": (note or "").strip() or None, "adapter": PA.MANUAL,
                               "external_ref": (external_ref or "").strip() or None},
        detail={"channel": channel, "adapter": PA.MANUAL, "note": note, "external_ref": external_ref})


async def consent_requested(pool, *, org_id, actor_id, payment_id, note: str | None = None) -> dict:
    """sent → awaiting_client_consent: the custodian asks the member to e-consent (some rails / custodians)."""
    return await _transition(
        pool, org_id=org_id, actor_id=actor_id, payment_id=payment_id, allowed=("sent",),
        what="record a consent request on", action="consent_requested", to_status="awaiting_client_consent",
        fields={"consent_requested_at": datetime.now(timezone.utc)}, detail={"note": note})


async def acknowledge(pool, *, org_id, actor_id, payment_id, external_ref: str | None = None,
                      note: str | None = None) -> dict:
    return await _transition(
        pool, org_id=org_id, actor_id=actor_id, payment_id=payment_id, allowed=("sent", "awaiting_client_consent"),
        what="record the custodian's acknowledgement of", action="acknowledge", to_status="acknowledged",
        fields=lambda row, a: {"acknowledged_by": uuid.UUID(str(a["id"])), "acknowledged_at": datetime.now(timezone.utc),
                               "external_ref": (external_ref or "").strip() or row["external_ref"]},
        detail={"external_ref": external_ref, "note": note})


async def settle(pool, *, org_id, actor_id, payment_id, settled_on, note: str | None = None) -> dict:
    """acknowledged → settled on ``settled_on``. ACH settles PROVISIONALLY: provisional_until = settled_on + the
    org's return window (business days, setting ``cash.payments.ach_return_window_business_days``, default 2)."""
    d = parse_date(settled_on, "the settlement date")
    if d > today():
        raise PaymentError("the settlement date is in the future")

    async def _window(conn, row, actor):
        row["_window"] = await _setting_int(conn, org_id, SETTING_ACH_WINDOW, DEFAULT_ACH_WINDOW)

    def _fields(row, a):
        prov = add_business_days(d, row["_window"]) if row["rail"] == "ach" else None
        return {"settled_by": uuid.UUID(str(a["id"])), "settled_at": datetime.now(timezone.utc), "settled_on": d,
                "provisional_until": prov}

    return await _transition(
        pool, org_id=org_id, actor_id=actor_id, payment_id=payment_id, allowed=("acknowledged",), what="settle",
        action="settle", to_status="settled", fields=_fields, check=_window,
        detail={"settled_on": d.isoformat(), "note": note})


async def custodian_reject(pool, *, org_id, actor_id, payment_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise PaymentError("a custodian rejection needs the custodian's reason")
    return await _transition(
        pool, org_id=org_id, actor_id=actor_id, payment_id=payment_id,
        allowed=("sent", "awaiting_client_consent", "acknowledged"), what="record a custodian rejection of",
        action="custodian_reject", to_status="rejected", fields={"custodian_rejected_reason": reason.strip()},
        detail={"reason": reason.strip()})


async def record_return(pool, *, org_id, actor_id, payment_id, return_code: str, reason: str | None = None) -> dict:
    """settled / reconciled → returned: an ACH return code (R01 …) after settlement. Only an ACH payment returns."""
    code = (return_code or "").strip().upper()
    if not re.fullmatch(r"R[0-9]{2}", code):
        raise PaymentError("an ACH return code is R followed by two digits (e.g. R01)")

    async def _ach(conn, row, actor):
        if row["rail"] != "ach":
            raise PaymentConflict("only an ACH payment is returned with a return code")

    return await _transition(
        pool, org_id=org_id, actor_id=actor_id, payment_id=payment_id, allowed=("settled", "reconciled"),
        what="record a return on", action="return", to_status="returned", check=_ach,
        fields=lambda row, a: {"return_code": code, "return_reason": (reason or "").strip() or None,
                               "returned_by": uuid.UUID(str(a["id"])), "returned_at": datetime.now(timezone.utc)},
        detail={"return_code": code, "reason": reason})


# ── reconciliation against the custodian feed ──────────────────────────────────────────────────────────────────────
MATCH_SQL = """
SELECT x.id, x.transaction_type_code, x.trade_date, x.settle_date, x.external_ref, x.source_system,
       COALESCE(x.net_amount, x.gross_amount, x.quantity * x.price) AS amount, p.account_id
  FROM portfolio.transactions x
  JOIN portfolio.positions p ON p.id = x.position_id
 WHERE x.org_id = $1::uuid AND p.account_id = ANY($2::uuid[]) AND x.valid_to IS NULL AND x.system_to IS NULL
   AND COALESCE(x.settle_date, x.trade_date) BETWEEN $3::date AND $4::date
   AND COALESCE(x.net_amount, x.gross_amount, x.quantity * x.price) = $5::numeric
   AND NOT EXISTS (SELECT 1 FROM public.payment_instructions o WHERE o.org_id = x.org_id AND o.system_to IS NULL
                     AND o.id <> $6::uuid AND x.id = ANY(o.reconciled_transaction_ids))
 ORDER BY COALESCE(x.settle_date, x.trade_date), x.id
"""


def leg_of(code: str, account_id, row: dict) -> str | None:
    """'out' — money leaving the SOURCE account's holding; 'in' — arriving at the DESTINATION. The direction is the
    type's effect on the holding (services.cash_activity.TYPE_MAP: a sell takes money out of the holding, a buy puts
    it in) — never a second sign table."""
    from services import cash_activity as CA

    _t, sign = CA.TYPE_MAP.get(code, (None, 0))
    if str(account_id) == str(row["source_account_id"]) and sign < 0:
        return "out"
    if str(account_id) == str(row["destination_account_id"]) and sign > 0:
        return "in"
    return None


async def matches(conn, org_id, row: dict) -> list[dict]:
    """Candidate custodian-feed transactions for this payment: either account, the exact amount, the right direction
    on each leg, inside value date − 2 … + 5 business days, not already used by another payment."""
    lo, hi = add_business_days(row["value_date"], -MATCH_BEFORE_BD), add_business_days(row["value_date"], MATCH_AFTER_BD)
    rows = await conn.fetch(MATCH_SQL, str(org_id), [str(row["source_account_id"]), str(row["destination_account_id"])],
                            lo, hi, row["amount"], str(row["id"]))
    out = []
    for r in rows:
        leg = leg_of(r["transaction_type_code"], r["account_id"], row)
        if leg is None:
            continue
        out.append({"id": str(r["id"]), "leg": leg, "type": r["transaction_type_code"],
                    "date": (r["settle_date"] or r["trade_date"]).isoformat(), "amount": money(Decimal(r["amount"])),
                    "external_ref": r["external_ref"], "source_system": r["source_system"]})
    return out


async def reconcile(pool, *, org_id, actor_id, payment_id, method: str, transaction_ids: list | None = None,
                    reference: str | None = None, note: str | None = None) -> dict:
    """settled → reconciled. ``custodian_data``: the named feed transactions must be among :func:`matches` (and the
    source's OUT leg among them); ``manual``: a note saying how it was confirmed, by a DIFFERENT person from the
    sender (the DB CHECK backs it). An open unmatched-review task is closed with it."""
    if method not in RECONCILE_METHOD_LABELS:
        raise PaymentError(f"method is one of {list(RECONCILE_METHOD_LABELS)}")
    ids = [_uuid(x, "transaction") for x in (transaction_ids or [])]
    if method == "custodian_data" and not ids:
        raise PaymentError("matching to the custodian feed names the feed transaction(s) it matched")
    if method == "manual" and not (note or "").strip():
        raise PaymentError("a manual reconciliation needs a note saying how it was confirmed")
    review = {}

    async def _check(conn, row, actor):
        if method == "manual" and row["sent_by"] is not None and str(row["sent_by"]) == str(actor["id"]):
            await _refused(org_id, actor["id"], "reconcile a payment manually",
                           ["the person who sent a payment cannot also confirm it by hand"], row["id"])
            raise MakerCheckerViolation("a manual reconciliation is confirmed by a different person from the one who "
                                        "sent the payment")
        if method == "custodian_data":
            cand = {m["id"]: m for m in await matches(conn, org_id, row)}
            bad = [x for x in ids if x not in cand]
            if bad or not any(cand[x]["leg"] == "out" for x in ids if x in cand):
                raise PaymentError("the named transactions are not matches for this payment (the source account's "
                                   f"outgoing leg, the exact amount, inside the date window): {bad or ids}")
            review["refs"] = [cand[x]["external_ref"] for x in ids if cand[x]["external_ref"]]
        review["task"] = await conn.fetchval(
            f"SELECT detail->>'task_id' FROM {T_EVT} WHERE payment_id = $1::uuid AND action = 'unmatched_alert' "
            "ORDER BY created_at DESC LIMIT 1", str(row["id"]))

    def _fields(row, a):
        ref = (reference or "").strip() or (", ".join(review.get("refs") or []) or row["external_ref"]
                                            or f"feed transactions {', '.join(i[:8] for i in ids)}")
        return {"reconciled_by": uuid.UUID(str(a["id"])), "reconciled_at": datetime.now(timezone.utc),
                "reconcile_method": method, "reconcile_reference": ref if method == "custodian_data" else
                ((reference or "").strip() or None), "reconcile_note": (note or "").strip() or None,
                "reconciled_transaction_ids": [uuid.UUID(i) for i in ids]}

    new = await _transition(pool, org_id=org_id, actor_id=actor_id, payment_id=payment_id, allowed=("settled",),
                            what="reconcile", action="reconcile", to_status="reconciled", fields=_fields, check=_check,
                            detail={"method": method, "transaction_ids": ids, "note": note})
    if review.get("task"):
        await _withdraw_task(pool, review["task"], "the payment was reconciled")
    return new


async def close(pool, *, org_id, actor_id, payment_id, note: str | None = None) -> dict:
    """reconciled → closed. An ACH payment closes only after its return window ("provisional until …")."""
    t = today()

    async def _final(conn, row, actor):
        if row["provisional_until"] is not None and row["provisional_until"] >= t:
            raise PaymentConflict(f"an ACH settlement is provisional until {row['provisional_until'].isoformat()} (the "
                                  "return window) — close it after that")

    return await _transition(
        pool, org_id=org_id, actor_id=actor_id, payment_id=payment_id, allowed=("reconciled",), what="close",
        action="close", to_status="closed", check=_final,
        fields=lambda row, a: {"closed_by": uuid.UUID(str(a["id"])), "closed_at": datetime.now(timezone.utc)},
        detail={"note": note})


async def cancel(pool, *, org_id, actor_id, payment_id, reason: str) -> dict:
    """Any open state → cancelled, with a reason. An open approval task is withdrawn first. A SENT payment is
    cancelled only after the custodian has been asked to recall it (the reason says so)."""
    if not (reason or "").strip():
        raise PaymentError("a cancellation needs a reason")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payment_id)
    _need(row, OPEN_STATUSES, "cancel")
    if row["status"] == "pending_approval" and row["approval_task_id"]:
        try:
            await HT.cancel_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                 reason=f"payment instruction cancelled: {reason}")
        except HT.HumanTaskError:
            pass   # already decided — the transition re-checks the status
    return await _transition(
        pool, org_id=org_id, actor_id=actor["id"], payment_id=payment_id, allowed=OPEN_STATUSES, what="cancel",
        action="cancel", to_status="cancelled",
        fields=lambda r, a: {"cancelled_by": uuid.UUID(str(a["id"])), "cancelled_at": datetime.now(timezone.utc),
                             "cancel_reason": reason.strip()},
        detail={"reason": reason.strip(), "previous_task_id": str(row["approval_task_id"])
                if row["approval_task_id"] else None})


# ── the sweeps (expiry + unmatched settlements) — SWITCHED OFF unless PAYMENT_SWEEP_ENABLED=1 ─────────────────────────
async def expire_due(pool, *, today_: date | None = None, org_id=None) -> list[str]:
    """Value date passed before it was sent (draft / pending / approved), or the member's consent never came
    (awaiting consent ``CONSENT_GRACE_BD`` business days past the value date) → expired, logged with no actor. The
    cross-org sweep never acts on a test org; a verify passes its own org_id."""
    from services.workflow_engine import cancel_user_task

    t = today_ or today()
    out = []
    async with pool.acquire() as conn:
        sql = (f"SELECT id, org_id, status, value_date FROM {T} WHERE system_to IS NULL AND "
               "((status = ANY($1::text[]) AND value_date < $2) OR status = 'awaiting_client_consent')")
        args: list = [list(PRE_SEND), t]
        if org_id:
            sql += " AND org_id = $3::uuid"
            args.append(str(org_id))
        else:
            from services import orgs as ORGS

            sql += " AND " + ORGS.not_test_org_sql(f"{T}.org_id")
        due = await conn.fetch(sql + " ORDER BY value_date, id", *args)
    for d in due:
        if d["status"] == "awaiting_client_consent" and add_business_days(d["value_date"], CONSENT_GRACE_BD) >= t:
            continue
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await load(conn, d["org_id"], d["id"], lock=True)
                if row["status"] not in PRE_SEND + ("awaiting_client_consent",):
                    continue
                reason = (f"the member's consent was not given within {CONSENT_GRACE_BD} business days of the value "
                          f"date {row['value_date'].isoformat()}" if row["status"] == "awaiting_client_consent" else
                          f"not sent by the value date {row['value_date'].isoformat()}")
                task_id = row["approval_task_id"] if row["status"] == "pending_approval" else None
                new, eid = await _update(conn, row, {"status": "expired", "expired_at": datetime.now(timezone.utc),
                                                     "expiry_reason": reason},
                                         action="expire", actor_id=None, detail={"reason": reason})
                await _audit(conn, row["org_id"], None, "expired", row["id"], {"event_id": eid, "reason": reason})
        if task_id:
            try:
                await cancel_user_task(pool, uuid.UUID(str(task_id)), None, "the payment instruction expired")
            except Exception:  # noqa: BLE001
                pass
        await _publish(pool, d["org_id"], "expire", d["id"], {"reason": reason}, None, eid)
        out.append(str(d["id"]))
    return out


async def unmatched_due(pool, *, today_: date | None = None, org_id=None) -> list[str]:
    """A SETTLED payment still not reconciled ``cash.payments.unmatched_business_days`` (default 3) business days after
    its settlement → ONE review task (kind payment_reconcile_review, for the holders of initiate_payment) and an
    ``unmatched_alert`` event — once per payment."""
    t = today_ or today()
    out = []
    async with pool.acquire() as conn:
        sql = (f"SELECT p.id, p.org_id, p.settled_on FROM {T} p WHERE p.system_to IS NULL AND p.status = 'settled' "
               f"AND NOT EXISTS (SELECT 1 FROM {T_EVT} e WHERE e.payment_id = p.id AND e.action = 'unmatched_alert')")
        args: list = []
        if org_id:
            sql += " AND p.org_id = $1::uuid"
            args.append(str(org_id))
        else:
            from services import orgs as ORGS

            sql += " AND " + ORGS.not_test_org_sql("p.org_id")
        due = await conn.fetch(sql + " ORDER BY p.settled_on, p.id", *args)
    for d in due:
        async with pool.acquire() as conn:
            n = await _setting_int(conn, d["org_id"], SETTING_UNMATCHED, DEFAULT_UNMATCHED_DAYS)
            row = await load(conn, d["org_id"], d["id"])
            if business_days_between(row["settled_on"], t) < n:
                continue
            src = await conn.fetchval("SELECT account_number_masked FROM accounts WHERE id = $1::uuid",
                                      str(row["source_account_id"]))
        task = await HT.open_task(
            pool, org_id=d["org_id"], kind=REVIEW_KIND, subject_type=SUBJECT, subject_id=row["id"], maker_id=None,
            eligible_permission=INITIATE, actor_kind="system",
            title=f"Payment not matched: {money(row['amount'])} USD from {src} settled {row['settled_on'].isoformat()}",
            payload={"settled_on": row["settled_on"].isoformat(), "business_days": n},
            action_path=f"/portfolio/households/{row['household_id']}?tab=treasury&payment={row['id']}"
                        "#treasury-payments")
        async with pool.acquire() as conn:
            async with conn.transaction():
                eid = await _plain_event(conn, org_id=d["org_id"], payment_id=row["id"], action="unmatched_alert",
                                         status=row["status"], actor_id=None,
                                         detail={"task_id": task["id"], "business_days": n,
                                                 "settled_on": row["settled_on"].isoformat()})
                await _audit(conn, d["org_id"], None, "unmatched_alert", row["id"], {"event_id": eid,
                                                                                    "task_id": task["id"]})
        out.append(str(row["id"]))
    return out


async def sweep_tick(pool, *, today_: date | None = None) -> dict:
    """The cron step — SWITCHED OFF unless ``PAYMENT_SWEEP_ENABLED=1`` (platform scope; never a test org)."""
    if os.environ.get(SWEEP_ENV) != "1":
        return {"enabled": False, "expired": [], "unmatched": []}
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        return {"enabled": True, "expired": await expire_due(pool, today_=today_),
                "unmatched": await unmatched_due(pool, today_=today_)}
    finally:
        reset_rls_context(tokens)


# ── reads ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _acct_out(a: dict | None, names: dict[str, str]) -> dict | None:
    """An account as EVERY payment read shows it: the stored mask (last four) — never custodian_account_id."""
    if not a:
        return None
    return {"id": str(a["id"]), "account_number_masked": a["account_number_masked"],
            "custodian_label": a.get("custodian_code"), "owner_id": str(a["primary_entity_id"]),
            "owner_name": names.get(str(a["primary_entity_id"])) or "An entity"}


def payment_out(r: dict, *, accts: dict, owners: dict, users: dict, member: bool = False) -> dict:
    out = {
        "id": str(r["id"]), "household_id": str(r["household_id"]), "status": r["status"],
        "status_label": STATUS_LABELS[r["status"]], "amount": money(r["amount"]), "currency_code": r["currency_code"],
        "rail": r["rail"], "rail_label": dict(RAILS)[r["rail"]], "value_date": r["value_date"].isoformat(),
        "purpose": r["purpose"], "purpose_ref_type": r["purpose_ref_type"],
        "purpose_ref_id": str(r["purpose_ref_id"]) if r["purpose_ref_id"] else None,
        "source": _acct_out(accts.get(str(r["source_account_id"])), owners),
        "destination": _acct_out(accts.get(str(r["destination_account_id"])), owners),
        "approved_at": _iso(r["approved_at"]), "sent_at": _iso(r["sent_at"]), "settled_on": _iso(r["settled_on"]),
        "provisional_until": _iso(r["provisional_until"]),
        "provisional": bool(r["provisional_until"] and r["provisional_until"] >= today()
                            and r["status"] in ("settled", "reconciled")),
        "created_at": _iso(r["created_at"]),
    }
    if member:
        return out
    out.update({
        "maker_ids": [str(m) for m in r["maker_ids"]], "makers": [users.get(str(m), "A colleague") for m in r["maker_ids"]],
        "approved_by_name": users.get(str(r["approved_by"])) if r["approved_by"] else None,
        "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
        "adapter": r["adapter"], "sent_by_name": users.get(str(r["sent_by"])) if r["sent_by"] else None,
        "sent_channel": r["sent_channel"], "sent_channel_label": PA.CHANNEL_LABELS.get(r["sent_channel"] or ""),
        "external_ref": r["external_ref"], "return_code": r["return_code"], "return_reason": r["return_reason"],
        "custodian_rejected_reason": r["custodian_rejected_reason"], "reconcile_method": r["reconcile_method"],
        "reconcile_method_label": RECONCILE_METHOD_LABELS.get(r["reconcile_method"] or ""),
        "reconcile_reference": r["reconcile_reference"], "reconcile_note": r["reconcile_note"],
        "reconciled_by_name": users.get(str(r["reconciled_by"])) if r["reconciled_by"] else None,
        "reconciled_transaction_ids": [str(x) for x in (r["reconciled_transaction_ids"] or [])],
        "cancel_reason": r["cancel_reason"], "expiry_reason": r["expiry_reason"],
    })
    return out


async def _context(conn, org_id, rows: list[dict]) -> tuple[dict, dict, dict]:
    ids = {str(r[k]) for r in rows for k in ("source_account_id", "destination_account_id")}
    accts = {}
    if ids:
        accts = {str(a["id"]): dict(a) for a in await conn.fetch(
            f"SELECT DISTINCT ON (id) {ACCOUNT_COLS} FROM public.accounts WHERE id = ANY($1::uuid[]) AND org_id = "
            "$2::uuid AND system_to IS NULL ORDER BY id, valid_to NULLS FIRST", sorted(ids), str(org_id))}
    owners = await _entity_names(conn, [a["primary_entity_id"] for a in accts.values()])
    users = await _names(conn, [u for r in rows for u in (list(r["maker_ids"] or []) + [r["approved_by"], r["sent_by"],
                                                                                         r["reconciled_by"]])])
    return accts, owners, users


async def list_payments(conn, org_id, household_id, *, status: str | None = None, rail: str | None = None,
                        account_id=None, date_from: date | None = None, date_to: date | None = None,
                        member: bool = False) -> list[dict]:
    args: list = [str(org_id), str(household_id)]
    sql = f"SELECT * FROM {T} WHERE org_id = $1::uuid AND household_id = $2::uuid AND system_to IS NULL"
    if status:
        if status == "open":
            args.append(list(OPEN_STATUSES))
            sql += f" AND status = ANY(${len(args)}::text[])"
        else:
            if status not in STATUS_LABELS:
                raise PaymentError(f"status is open or one of {list(STATUS_LABELS)}")
            args.append(status)
            sql += f" AND status = ${len(args)}"
    if rail:
        if rail not in RAIL_KEYS:
            raise PaymentError(f"rail is one of {list(RAIL_KEYS)}")
        args.append(rail)
        sql += f" AND rail = ${len(args)}"
    if account_id:
        args.append(_uuid(account_id, "account"))
        sql += f" AND (source_account_id = ${len(args)}::uuid OR destination_account_id = ${len(args)}::uuid)"
    if date_from:
        args.append(date_from)
        sql += f" AND value_date >= ${len(args)}::date"
    if date_to:
        args.append(date_to)
        sql += f" AND value_date <= ${len(args)}::date"
    rows = [dict(r) for r in await conn.fetch(sql + " ORDER BY value_date DESC, created_at DESC, id", *args)]
    accts, owners, users = await _context(conn, org_id, rows)
    return [payment_out(r, accts=accts, owners=owners, users=users, member=member) for r in rows]


async def events_of(conn, org_id, payment_id, *, member: bool = False) -> list[dict]:
    # cash06 fix 1: created_at is the trigger's now() — the SAME for every transition of one transaction — and id is
    # random, so draft_and_submit's create + submit (one transaction) read back in either order. The create row (the
    # only one with no from_status) sorts first among ties.
    rows = await conn.fetch(f"SELECT * FROM {T_EVT} WHERE payment_id = $1::uuid AND org_id = $2::uuid "
                            "ORDER BY created_at, (from_status IS NOT NULL), id", str(payment_id), str(org_id))
    names = await _names(conn, [r["actor_id"] for r in rows])
    out = []
    for r in rows:
        e = {"id": str(r["id"]), "action": r["action"], "label": ACTION_LABELS.get(r["action"], r["action"]),
             "from_status": r["from_status"], "to_status": r["to_status"],
             "to_label": STATUS_LABELS.get(r["to_status"], r["to_status"]), "at": _iso(r["created_at"]),
             "who": (names.get(str(r["actor_id"])) if r["actor_id"] else "System (logged)")}
        if member:
            e["who"] = "Your adviser's team" if r["actor_id"] else "System"
        else:
            det = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")
            e["detail"] = {k: v for k, v in det.items() if k in ("channel", "note", "reason", "method", "return_code",
                                                                  "settled_on", "external_ref", "task_id", "adapter")}
        out.append(e)
    return out


async def detail(conn, org_id, payment_id, *, member: bool = False) -> dict:
    row = await load(conn, org_id, payment_id)
    accts, owners, users = await _context(conn, org_id, [row])
    out = payment_out(row, accts=accts, owners=owners, users=users, member=member)
    if row["purpose_ref_type"]:
        try:
            ref = await _purpose_ref(conn, org_id, row["household_id"], row["purpose_ref_type"], row["purpose_ref_id"])
            out["purpose_ref_label"] = ref["label"] if ref else None
        except PaymentError:
            out["purpose_ref_label"] = f"{PURPOSE_REF_LABELS[row['purpose_ref_type']]} (no longer current)"
    out["timeline"] = await events_of(conn, org_id, row["id"], member=member)
    return out


async def options(pool, conn, org_id, household_id, actor_id) -> dict:
    """The new-instruction form, decided by the server: every source account with whether THIS caller may instruct
    it (both authority reasons), its declared rails and its same-owner destinations; the household's obligations and
    cash proposals to link; the business-day rule."""
    t = today()
    accts = await household_accounts(conn, org_id, household_id, t)
    owners = await _entity_names(conn, [a["primary_entity_id"] for a in accts])
    sources = []
    for a in accts:
        reasons = await authority_refusals(pool, conn, org_id, actor_id, a)
        rails = await declared_rails(conn, a)
        dests = [b for b in accts if b["id"] != a["id"]
                 and same_owner_refusal(a, b, household_id, a["owners"], b["owners"]) is None]
        sources.append({**_acct_out(a, owners), "eligible": not reasons and any(r["available"] for r in rails.values()),
                        "reasons": reasons + ([] if any(r["available"] for r in rails.values()) else
                                              [f"the custodian ({PA.custodian_key(a) or 'unknown'}) declares no payment "
                                               "rail"]),
                        "rails": [{"key": k, "label": dict(RAILS)[k]} for k, r in rails.items() if r["available"]],
                        "destinations": [_acct_out(b, owners) for b in dests]})
    from services import household_total as HHT

    members = sorted(await HHT.household_members(conn, org_id, household_id))
    obls = await conn.fetch("SELECT id, name, due_date, amount FROM public.cash_obligations WHERE org_id = $1::uuid "
                            "AND entity_id = ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS NULL "
                            "ORDER BY due_date, name LIMIT 200", str(org_id), members)
    props = await conn.fetch("SELECT p.id, p.status FROM public.rebalance_proposals p JOIN public.accounts a ON a.id = "
                             "p.account_id AND a.system_to IS NULL AND a.valid_to IS NULL WHERE p.org_id = $1::uuid "
                             "AND a.household_id = $2::uuid AND p.proposal_kind = 'cash' AND p.system_to IS NULL "
                             "ORDER BY p.created_at DESC LIMIT 50", str(org_id), str(household_id))
    return {"today": t.isoformat(), "next_business_day": BD.next_business_day(t).isoformat(),
            "value_date_rule": "a US business day, today or later — a weekend or holiday is refused (choose the next "
                               "business day)",
            "sources": sources,
            "links": [{"type": "cash_obligation", "id": str(o["id"]),
                       "label": f"{o['name']} — due {o['due_date'].isoformat()} ({money(Decimal(o['amount']))})"}
                      for o in obls] + [{"type": "cash_proposal", "id": str(p["id"]),
                                         "label": f"Cash proposal ({p['status']})"} for p in props]}


def actions_for(row: dict, *, can_initiate: bool, can_approve: bool, user_id, member: bool, t: date) -> list[dict]:
    """The server's own list of what this viewer may do next (labels included). A member: nothing."""
    if member:
        return []
    st = row["status"]
    makers = row.get("maker_ids") or []
    acts = []
    if can_initiate and st == "draft":
        acts += [("edit", "Edit"), ("submit", "Submit for approval")]
    if can_approve and st == "pending_approval" and sod.may_check_display(user_id, makers):
        acts += [("approve", "Approve"), ("reject", "Return to draft")]
    if can_initiate and st in SHEET_STATUSES:
        acts.append(("sheet", "Download payment sheet"))
    if can_initiate and st == "approved":
        acts.append(("mark_sent", "Confirm sent"))
    if can_initiate and st == "sent":
        acts.append(("consent_requested", "Member consent requested"))
    if can_initiate and st in ("sent", "awaiting_client_consent"):
        acts.append(("acknowledge", "Custodian acknowledged"))
    if can_initiate and st == "acknowledged":
        acts.append(("settle", "Settled"))
    if can_initiate and st in ("sent", "awaiting_client_consent", "acknowledged"):
        acts.append(("custodian_reject", "Custodian rejected"))
    if can_initiate and st == "settled":
        acts.append(("reconcile", "Reconcile"))
    if can_initiate and st in ("settled", "reconciled") and row.get("rail") == "ach":
        acts.append(("return", "Record an ACH return"))
    if can_initiate and st == "reconciled" and not row.get("provisional"):
        acts.append(("close", "Close"))
    if (can_initiate or can_approve) and st in OPEN_STATUSES:
        acts.append(("cancel", "Cancel"))
    return [{"key": k, "label": lbl} for k, lbl in acts]


def vocabularies(*, can_write: bool) -> dict:
    return {"statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "rails": [{"key": k, "label": v} for k, v in RAILS],
            "channels": [{"key": k, "label": v} for k, v in PA.CHANNEL_LABELS.items()] if can_write else [],
            "reconcile_methods": [{"key": k, "label": v} for k, v in RECONCILE_METHOD_LABELS.items()] if can_write else [],
            "purpose_links": [{"key": k, "label": PURPOSE_REF_LABELS[k]} for k in PERSON_PURPOSE_REFS] if can_write
            else [],
            "editable": list(EDITABLE) if can_write else [], "inline_editable": []}


async def pending_for_bulk(conn, org_id) -> list[dict]:
    """Every payment awaiting approval in the org, with its household (the bulk-approve list / visibility)."""
    rows = await conn.fetch(
        f"""SELECT p.*, h.name AS household_name, s.account_number_masked AS src_masked,
                   d.account_number_masked AS dst_masked
              FROM {T} p
              JOIN public.households h ON h.id = p.household_id
              LEFT JOIN public.accounts s ON s.id = p.source_account_id AND s.system_to IS NULL AND s.valid_to IS NULL
              LEFT JOIN public.accounts d ON d.id = p.destination_account_id AND d.system_to IS NULL
                                         AND d.valid_to IS NULL
             WHERE p.org_id = $1::uuid AND p.status = 'pending_approval' AND p.system_to IS NULL
             ORDER BY p.updated_at""", str(org_id))
    return [dict(r) for r in rows]
