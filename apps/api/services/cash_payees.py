"""CASH08 — PAYEES: a household's payee registry, the governed change of bank details, SLOAs, and the third-party switch.

A payee is someone a household may one day pay who is NOT one of its own accounts — a vendor, a charity, a person, an
outside entity. Design: docs/cash/CASH08_NOTES.md; operator notes: docs/cash/CASH_PAYEES.md.

STATUS FLOW (enforced here first, then by ``cash_payees_guard_trg``)
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  draft ─submit→ pending_approval ─approve (human task; S30 FLOOR: never a maker)→ active
        ←reject─┘
  active ─request bank change→ suspended (pending_* details; task) ─approve WITH call-back→ active (new details)
                                                                    └reject→ active (the approved details stand)
  active ─suspend (reason)→ suspended ─resume→ pending_approval (re-approved by a different person)
  any but retired ─retire (reason)→ retired

BANK DETAILS: the custodian's / adapter's standing-instruction REFERENCE (free text the operator copies) and the LAST
FOUR digits — never a full account or routing number, anywhere (service first, ``cash_payees_no_numbers_trg`` in the
database). No tax id: the W-9 is a Chancery document.

THE BANK CHANGE (the classic fraud vector): a request SUSPENDS the payee until a DIFFERENT person approves it, and the
approver records a CALL-BACK — who they called, the number they called, when. The number must be the one ON FILE
(``contact_phone`` before the request); a number matching the request's own contact details is refused even when it also
matches the file. The requester never approves (the floor). Every state is an archived copy (system axis).

SLOA: a Chancery document linked to the payee (``document_record_links`` 'cash_payee_sloa') + source accounts, with
expiry / revocation; a missing / expired / revoked SLOA is FLAGGED (and will block payment when third-party payments
are enabled — ``sloa_blocks_payment``).

THIRD-PARTY PAYMENTS STAY OFF: ``third_party_refusal`` is what services.payments asks before anything else when a
payee is named as a destination. Turning the org setting ``cash.third_party_payments`` on is a floor item
(``third_party_payments_setting``): a Super Admin asks, naming a counsel sign-off document; a DIFFERENT Super Admin
decides — and in this release the approval is refused by ``THIRD_PARTY_RELEASE_LOCKED`` (counsel first — Joe).

FIRST PAYMENT TO A NEW PAYEE (dormant while third-party payments are off): the checker of the first instruction is
neither the payee's creator nor its approver — ``first_payment_gate`` (S30 item ``first_payment_to_new_payee``).

Every action is audited (``audit_log`` ``cash.payee.*`` / ``cash.third_party.*``) and published after commit
(``domain_events`` ``payee.*``, source the audit row). Agents: none — every actor is an admitted person
(services.human_tasks.assert_human).
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from services import human_tasks as HT
from services import sod
from services.bitemporal import archive_live_row

log = logging.getLogger(__name__)

T = "public.cash_payees"
MANAGE = "manage_payees"
APPROVE = "approve_payees"
READ_PERMISSIONS = ("view_portfolio", MANAGE, APPROVE)
SWITCH_PERMISSION = "manage_org_settings"
TASK_KIND = "payee_approval"
BANK_TASK_KIND = "payee_bank_change_approval"
SWITCH_TASK_KIND = "third_party_switch_approval"
SUBJECT = "cash_payee"
SWITCH_SUBJECT = "org_setting"
SETTING_THIRD_PARTY = "cash.third_party_payments"
#: CASH08 (Joe, 2026-11-08): the switch cannot be turned on in this release — counsel first. Removing this is a code
#: review decision AFTER counsel's written sign-off (docs/cash/CASH_PAYEES.md "Enabling third-party payments").
THIRD_PARTY_RELEASE_LOCKED = True
NOT_ENABLED = "third-party payments are not enabled"
RELEASE_LOCK_REASON = ("third-party payments cannot be turned on in this release — counsel review first; the switch "
                       "stays off")
DISCLOSURE_KEY = "cash.third_party"
THIRD_PARTY_DISCLOSURE = (
    "Payments to anyone other than the account owner (third-party payments) are not offered. When they are, a payment "
    "to a payee will need a Standing Letter of Authorization you signed with your custodian, a second person's "
    "approval, and a call-back to a number on file before any change of the payee's bank details. Bank details are "
    "held only as your custodian's reference and the last four digits. The custodian moves the money, not the firm; "
    "a payment to a third party may not be recoverable once sent.")

LINK_SLOA = "cash_payee_sloa"
LINK_INVOICE_TAG = "household_vendor_invoice"
LINK_PAYEE_INVOICE = "cash_payee_invoice"
LINK_OBLIGATION = "cash_obligation"
LINK_COUNSEL = "third_party_counsel_signoff"
SLOA_SOON_DAYS = 30

KINDS = (("vendor", "Vendor"), ("charity", "Charity"), ("person", "Person"), ("outside_entity", "Outside entity"))
KIND_KEYS = tuple(k for k, _ in KINDS)
ACCOUNT_TYPES = (("checking", "Checking"), ("savings", "Savings"), ("other", "Other"))
ACCOUNT_TYPE_KEYS = tuple(k for k, _ in ACCOUNT_TYPES)
STATUS_LABELS = {"draft": "Draft", "pending_approval": "Awaiting a second person's approval", "active": "Active",
                 "suspended": "Suspended", "retired": "Retired"}
FLAG_LABELS = {
    "sloa_missing": "No SLOA on file",
    "sloa_expired": "SLOA expired",
    "sloa_revoked": "SLOA revoked",
    "sloa_expiring_soon": "SLOA expires within 30 days",
    "bank_change_pending": "Bank-detail change awaiting a call-back and approval",
    "suspended": "Suspended",
}
ACTION_LABELS = {
    "edit": "Edit", "submit": "Submit for approval", "approve": "Approve", "reject": "Reject",
    "request_bank_change": "Change bank details", "approve_bank_change": "Approve bank change (call-back)",
    "reject_bank_change": "Reject bank change", "suspend": "Suspend", "resume": "Resume (re-approval)",
    "retire": "Retire", "attach_sloa": "Attach SLOA", "expire_sloa": "Mark SLOA expired", "revoke_sloa": "Revoke SLOA",
}

#: draft fields a maker may set / edit
FIELDS = ("kind", "display_name", "legal_name", "contact_name", "contact_email", "contact_phone", "bank_name",
          "account_type", "last_four", "payee_reference", "notes")
EDITABLE = FIELDS
BANK_CHANGE_FIELDS = ("bank_name", "account_type", "last_four", "payee_reference", "contact_phone", "contact_email")
#: every free-text column the number guard reads (mirrors cash_payees_no_numbers() in docs/cash08_part1.sql)
FREE_FIELDS = ("display_name", "legal_name", "contact_name", "contact_email", "bank_name", "payee_reference", "notes",
               "pending_bank_name", "pending_payee_reference", "pending_contact_email", "bank_change_request_contact",
               "bank_change_reason", "callback_person", "sloa_revoke_reason", "suspend_reason", "retire_reason")
PHONE_FIELDS = ("contact_phone", "pending_contact_phone", "bank_change_request_phone", "callback_number")
LIMITS = {"display_name": 200, "legal_name": 200, "contact_name": 200, "contact_email": 200, "bank_name": 120,
          "payee_reference": 200, "notes": 2000}

_RUN5 = re.compile(r"[0-9]{5,}")
_RUN9 = re.compile(r"[0-9]{9,}")
_SEP = re.compile(r"[\s./-]")
_PHONE = re.compile(r"^[+0-9 ().-]{7,25}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PayeeError(ValueError):
    """A request the rules refuse (422)."""


class PayeeNotFound(LookupError):
    pass


class PayeeConflict(Exception):
    """The payee's state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    pass


class PayeeRefused(PermissionError):
    """Refused by rule (403): the third-party gate, a Super-Admin-only step, the release lock."""


# ── time (injectable: the verify fixes "today") ─────────────────────────────────────────────────────────────────────
_CLOCK: dict = {"fn": None}


def set_clock(fn) -> None:
    _CLOCK["fn"] = fn


def today() -> date:
    if _CLOCK["fn"]:
        return _CLOCK["fn"]()
    from services import payments as PS

    return PS.today()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── pure rules ──────────────────────────────────────────────────────────────────────────────────────────────────────
def number_refusal(field: str, value) -> str | None:
    """The database's number guard, first, with the same reasons. Never echoes the value."""
    if value is None:
        return None
    v = str(value)
    if field in ("last_four", "pending_last_four"):
        return None if re.fullmatch(r"[0-9]{4}", v) else f"{field}: exactly the last four digits, never more"
    if field in PHONE_FIELDS:
        if not _PHONE.match(v) or _RUN5.search(v):
            return f"{field}: a phone number is digits with separators (e.g. 212-555-0100), no run over four digits"
        return None
    if _RUN5.search(v):
        return (f"{field}: a run of more than four digits looks like an account or routing number — store the "
                "custodian's reference and the last four only")
    if _RUN9.search(_SEP.sub("", v)):
        return f"{field}: nine or more digits (separators ignored) look like a routing or account number — never stored"
    return None


def number_refusals(values: dict) -> list[str]:
    return [r for r in (number_refusal(k, v) for k, v in values.items()) if r]


def phone_digits(v) -> str:
    """The digits of a phone number, a leading US country code dropped (so +1 212 … and 212 … compare equal)."""
    d = re.sub(r"\D", "", str(v or ""))
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def callback_refusals(*, person, number, called_at, on_file, request_contact, request_phone,
                      requested_at: datetime | None, now: datetime | None = None) -> list[str]:
    """The call-back attestation rule (pure): who, which number, when — all required; the number is the one ON FILE;
    never one taken from the request (even if it also matches the file); called after the request, not in the future."""
    reasons = []
    if not (person or "").strip():
        reasons.append("the call-back attestation names the person you called")
    if not (number or "").strip():
        reasons.append("the call-back attestation names the number you called")
    if called_at is None:
        reasons.append("the call-back attestation records when you called")
    if reasons:
        return reasons
    got = phone_digits(number)
    req = {phone_digits(request_phone)} if request_phone else set()
    req |= {phone_digits(m) for m in re.findall(r"[+0-9][0-9 ().-]{6,24}[0-9]", str(request_contact or ""))}
    req.discard("")
    if got in req:
        reasons.append("the call-back number is one given in the change request itself — call the number ON FILE, "
                       "never a number from the request")
    if not on_file or phone_digits(on_file) == "":
        reasons.append("this payee has no call-back number on file — a bank change cannot be verified; retire the "
                       "payee and create a new one through approval")
    elif got != phone_digits(on_file) and got not in req:
        reasons.append("the call-back number is not the number on file for this payee")
    n = now or _now()
    if called_at.tzinfo is None:
        called_at = called_at.replace(tzinfo=timezone.utc)
    if called_at > n + timedelta(minutes=5):
        reasons.append("the call-back time is in the future")
    if requested_at is not None and called_at < requested_at - timedelta(minutes=1):
        reasons.append("the call-back was made before the change was requested — call after the request")
    return reasons


def first_payment_conflict(checker_id, creator_id, approver_id) -> str | None:
    """Pure: the first payment to a new payee needs a checker who is neither its creator nor its approver."""
    c = str(checker_id) if checker_id else None
    if c is None:
        return "a check needs a person to make it"
    if creator_id and c == str(creator_id):
        return "the payee's creator may not approve the first payment to it"
    if approver_id and c == str(approver_id):
        return "the person who approved the payee may not approve the first payment to it"
    return None


def first_payment_applies(prior_approved_payments: int) -> bool:
    return int(prior_approved_payments or 0) == 0


def _as_date(v) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def flags(row: dict, t: date) -> list[str]:
    """The payee's flags (pure). SLOA flags never apply to a retired payee."""
    out = []
    if row.get("status") == "retired":
        return out
    if not row.get("sloa_document_id"):
        out.append("sloa_missing")
    else:
        exp = _as_date(row.get("sloa_expires_on"))
        if row.get("sloa_revoked_at"):
            out.append("sloa_revoked")
        elif row.get("sloa_expired_at") or (exp is not None and exp < t):
            out.append("sloa_expired")
        elif exp is not None and exp <= t + timedelta(days=SLOA_SOON_DAYS):
            out.append("sloa_expiring_soon")
    if row.get("bank_change_task_id"):
        out.append("bank_change_pending")
    if row.get("status") == "suspended":
        out.append("suspended")
    return out


def sloa_blocks_payment(row: dict, t: date, source_account_id=None) -> str | None:
    """When third-party payments are enabled: a payee whose SLOA is missing, expired or revoked (or does not cover the
    source account) blocks payment. Dormant today (no instruction can name a payee)."""
    f = flags(row, t)
    for k in ("sloa_missing", "sloa_expired", "sloa_revoked"):
        if k in f:
            return f"{FLAG_LABELS[k]} — a payment to this payee is blocked"
    if source_account_id is not None and str(source_account_id) not in {str(a) for a in row.get("sloa_account_ids") or []}:
        return "the SLOA does not cover this source account — a payment to this payee is blocked"
    return None


# ── small helpers ───────────────────────────────────────────────────────────────────────────────────────────────────
def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, uuid.UUID)
                      else o.isoformat() if isinstance(o, (date, datetime)) else str(o))


def _uuid(v, what: str = "payee") -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise PayeeError(f"{what}: not a valid id") from None


def _text(v, field: str) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    lim = LIMITS.get(field, 300)
    if len(s) > lim:
        raise PayeeError(f"{field}: longer than {lim} characters")
    return s


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict,
                 resource_type: str = "cash_payee") -> str:
    return str(await conn.fetchval(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb) RETURNING id",
        str(org_id), str(user_id) if user_id else None, action, resource_type,
        str(resource_id) if resource_id else None, _dumps(payload)))


async def _refused(org_id, actor_id, what: str, reasons: list[str], payee_id=None,
                   action: str = "cash.payee.refused") -> None:
    """A refusal is audited on a FRESH pooled connection (the caller is about to raise). Never raises. The reasons
    never carry a refused value (number_refusal names the column only)."""
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as c:
            await _audit(c, org_id, actor_id, action, payee_id, {"what": what, "reasons": reasons})
    except Exception:  # noqa: BLE001
        log.exception("cash_payees: could not audit a refusal (%s)", what)


async def _publish(pool, org_id, action: str, payee_id, audit_id: str, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    try:
        await publish_event(pool, str(org_id), f"payee.{action}", "audit_log", audit_id,
                            json.loads(_dumps({"payee_id": str(payee_id) if payee_id else None, **payload})),
                            created_by=str(actor_id) if actor_id else None)
    except Exception:  # noqa: BLE001 — the event is a notification; the write stands
        log.exception("payee.%s event not published", action)


async def _human(conn, actor_id, org_id) -> dict:
    try:
        return await HT.assert_human(conn, actor_id, org_id)
    except HT.AgentActorRefused as exc:
        raise PayeeError(str(exc)) from exc


async def load(conn, org_id, payee_id, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), _uuid(payee_id), str(org_id))
    if row is None:
        raise PayeeNotFound("payee not found")
    return dict(row)


async def _archive(conn, payee_id) -> None:
    """System axis (Rule 3): the SHARED helper services.bitemporal.archive_live_row."""
    if await archive_live_row(conn, T, payee_id) is None:
        raise PayeeConflict("the payee is not live — refusing to change it")


async def _update(conn, row: dict, fields: dict) -> dict:
    """Archive, then update the live row (same id)."""
    await _archive(conn, row["id"])
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    new = await conn.fetchrow(f"UPDATE {T} SET {sets}, updated_at = now(), system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(row["id"]), *[fields[c] for c in cols])
    if new is None:
        raise PayeeConflict("the payee changed underneath this request — reload and retry")
    return dict(new)


def _need(row: dict, statuses: tuple[str, ...], what: str) -> None:
    if row["status"] not in statuses:
        raise PayeeConflict(f"cannot {what} a payee that is {STATUS_LABELS.get(row['status'], row['status']).lower()} "
                            f"(allowed from: {', '.join(STATUS_LABELS[s].lower() for s in statuses)})")


async def _withdraw_task(pool, task_id, reason: str) -> None:
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        log.info("task %s was not open to withdraw", task_id)


async def household_of(conn, org_id, household_id) -> dict:
    r = await conn.fetchrow("SELECT id, name FROM households WHERE id = $1::uuid AND org_id = $2::uuid",
                            _uuid(household_id, "household"), str(org_id))
    if r is None:
        raise PayeeNotFound("household not found")
    return dict(r)


def _validate_fields(data: dict, *, partial: bool) -> dict:
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise PayeeError(f"not a payee field: {', '.join(unknown)}")
    out = {}
    for f in FIELDS:
        if partial and f not in data:
            continue
        out[f] = _text(data.get(f), f)
    if not partial or "kind" in out:
        if out.get("kind") not in KIND_KEYS:
            raise PayeeError("kind is one of " + ", ".join(KIND_KEYS))
    if not partial or "display_name" in out:
        if not out.get("display_name"):
            raise PayeeError("name the payee (display name)")
    if out.get("account_type") is not None and out["account_type"] not in ACCOUNT_TYPE_KEYS:
        raise PayeeError("account type is one of " + ", ".join(ACCOUNT_TYPE_KEYS))
    if out.get("contact_email") is not None and not _EMAIL.match(out["contact_email"]):
        raise PayeeError("contact_email: not an email address")
    return out


async def _number_check(org_id, actor_id, what: str, values: dict, payee_id=None) -> None:
    reasons = number_refusals(values)
    if reasons:
        await _refused(org_id, actor_id, what, reasons, payee_id)
        raise PayeeError(" · ".join(reasons))


def _safe(row: dict) -> dict:
    """What an audit row / event may carry about a payee: never a number beyond the last four."""
    return {"display_name": row.get("display_name"), "kind": row.get("kind"), "status": row.get("status"),
            "last_four": row.get("last_four"), "household_id": str(row.get("household_id"))}


# ── create / edit / submit / decide ─────────────────────────────────────────────────────────────────────────────────
async def create(pool, *, org_id, actor_id, household_id, data: dict) -> dict:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        await household_of(conn, org_id, household_id)
    f = _validate_fields(data, partial=False)
    await _number_check(org_id, actor["id"], "create a payee", f)
    aid = uuid.UUID(str(actor["id"]))
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = dict(await conn.fetchrow(
                f"""INSERT INTO {T} (org_id, household_id, kind, display_name, legal_name, contact_name, contact_email,
                        contact_phone, bank_name, account_type, last_four, payee_reference, notes, status, created_by,
                        maker_ids)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'draft', $14,
                            ARRAY[$14]::uuid[]) RETURNING *""",
                str(org_id), _uuid(household_id, "household"), f["kind"], f["display_name"], f["legal_name"],
                f["contact_name"], f["contact_email"], f["contact_phone"], f["bank_name"], f["account_type"],
                f["last_four"], f["payee_reference"], f["notes"], aid))
            aud = await _audit(conn, org_id, aid, "cash.payee.created", row["id"], _safe(row))
    await _publish(pool, org_id, "created", row["id"], aud, {"status": "draft"}, aid)
    return row


async def edit(pool, *, org_id, actor_id, payee_id, changes: dict) -> dict:
    """A DRAFT's fields change (system-axis archive; the editor joins maker_ids — every editor is a maker)."""
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
    f = _validate_fields(changes or {}, partial=True)
    if not f:
        raise PayeeError("nothing to change")
    await _number_check(org_id, actor["id"], "edit a payee", f, payee_id)
    aid = uuid.UUID(str(actor["id"]))
    async with pool.acquire() as conn:
        async with conn.transaction():
            cur = await load(conn, org_id, payee_id, lock=True)
            _need(cur, ("draft",), "edit")
            makers = sorted({str(m) for m in cur["maker_ids"]} | {str(aid)})
            new = await _update(conn, cur, {**f, "maker_ids": [uuid.UUID(m) for m in makers]})
            aud = await _audit(conn, org_id, aid, "cash.payee.edited", cur["id"],
                               {**_safe(new), "fields": sorted(f)})
    await _publish(pool, org_id, "edited", new["id"], aud, {"fields": sorted(f)}, aid)
    return new


def _task_title(row: dict) -> str:
    return f"Approve payee: {row['display_name']} (····{row.get('last_four') or '----'})"


async def submit(pool, *, org_id, actor_id, payee_id) -> dict:
    """draft → pending_approval; opens the approval task (kind payee_approval). The submitter is a maker."""
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payee_id)
    _need(row, ("draft",), "submit")
    missing = [k for k in ("last_four", "payee_reference") if not row.get(k)]
    if missing:
        raise PayeeError("a payee is submitted with its custodian reference and the last four digits "
                         f"(missing: {', '.join(missing)})")
    return await _open_approval(pool, org_id=org_id, actor=actor, row=row, from_status="draft", action="submitted")


async def _open_approval(pool, *, org_id, actor: dict, row: dict, from_status: str, action: str) -> dict:
    aid = uuid.UUID(str(actor["id"]))
    makers = sorted({str(m) for m in row["maker_ids"]} | {str(aid)})
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=row["id"], maker_id=actor["id"],
        eligible_permission=APPROVE, title=_task_title(row),
        payload={"display_name": row["display_name"], "kind": row["kind"], "last_four": row.get("last_four"),
                 "maker_ids": makers, "floor": "no self-approval, ever (S30 floor — Super Admin included)"},
        action_path=f"/portfolio/households/{row['household_id']}?tab=treasury&payee={row['id']}#treasury-payees")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, row["id"], lock=True)
                _need(cur, (from_status,), "submit")
                fields = {"status": "pending_approval", "approval_task_id": uuid.UUID(task["id"]),
                          "maker_ids": [uuid.UUID(m) for m in makers], "approved_by": None, "approved_at": None}
                if from_status == "suspended":
                    fields.update({"suspended_by": None, "suspended_at": None, "suspend_reason": None})
                new = await _update(conn, cur, fields)
                aud = await _audit(conn, org_id, aid, f"cash.payee.{action}", cur["id"],
                                   {**_safe(new), "task_id": task["id"], "notified": task.get("notified", [])})
    except Exception:
        await _withdraw_task(pool, task["id"], "the payee could not be submitted")
        raise
    await _publish(pool, org_id, action, new["id"], aud, {"task_id": task["id"]}, aid)
    return {**new, "task": task}


def _refuse_payee(_d) -> Exception:
    return MakerCheckerViolation("maker-checker (S30 floor): you created, edited or submitted this payee, so a "
                                 f"different holder of {APPROVE} must approve it — no setting and no role (Super Admin "
                                 "included) relaxes this")


async def _precheck(pool, task, outcome, actor_id):
    async with pool.acquire() as conn:
        row = await load(conn, task["org_id"], task["subject_id"])
        if row["status"] != "pending_approval" or str(row.get("approval_task_id")) != str(task["id"]):
            raise HT.HumanTaskConflict(f"the payee is {row['status']}, not awaiting this approval")
        if outcome == "approve":
            await sod.gate(conn, org_id=task["org_id"], item_type=sod.PAYEE, checker_id=actor_id,
                           maker_ids=[str(m) for m in row["maker_ids"]], item_id=str(row["id"]),
                           household_id=row["household_id"], site="cash_payees._precheck", refuse=_refuse_payee)


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
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
                await sod.gate(conn, org_id=org_id, item_type=sod.PAYEE, checker_id=checker,
                               maker_ids=[str(m) for m in row["maker_ids"]], item_id=str(row["id"]),
                               household_id=row["household_id"], site="cash_payees._apply", refuse=_refuse_payee)
                new = await _update(conn, row, {"status": "active", "approved_by": checker, "approved_at": _now()})
            else:
                new = await _update(conn, row, {"status": "draft", "approval_task_id": None})
            aud = await _audit(conn, org_id, checker, "cash.payee." + ("approved" if outcome == "approve" else
                                                                         "rejected"), row["id"],
                               {**_safe(new), "task_id": task["id"], "note": note})
    await _publish(pool, org_id, "approved" if outcome == "approve" else "rejected", new["id"], aud,
                   {"task_id": task["id"]}, actor_id)
    return new


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


async def _complete(pool, *, org_id, actor, task_id, outcome, note, household_id):
    try:
        return await HT.complete_task(pool, org_id=org_id, task_id=task_id, actor_id=actor["id"], outcome=outcome,
                                      note=(note or "").strip() or None, sod_household_id=household_id)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise PayeeConflict(str(exc)) from exc


async def decide(pool, *, org_id, actor_id, payee_id, outcome: str, note: str | None = None) -> dict:
    """The ONE decision path for a payee approval (the route and the inbox): the S30 floor against every maker (a
    refusal is recorded in sod_decisions and audited; nothing changes), then services.human_tasks.complete_task."""
    if outcome not in ("approve", "reject"):
        raise PayeeError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise PayeeError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payee_id)
    _need(row, ("pending_approval",), "approve or reject")
    async with pool.acquire() as conn:
        await sod.gate(conn, org_id=org_id, item_type=sod.PAYEE, checker_id=actor["id"],
                       maker_ids=[str(m) for m in row["maker_ids"]], item_id=str(row["id"]),
                       household_id=row["household_id"], site="cash_payees.decide", refuse=_refuse_payee)
    done = await _complete(pool, org_id=org_id, actor=actor, task_id=row["approval_task_id"], outcome=outcome,
                           note=note, household_id=row["household_id"])
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await load(conn, org_id, payee_id)
    return result


# ── the governed bank-detail change ─────────────────────────────────────────────────────────────────────────────────
_ATTESTATION: contextvars.ContextVar[dict | None] = contextvars.ContextVar("cash_payees_attestation", default=None)


def _refuse_bank(_d) -> Exception:
    return MakerCheckerViolation("maker-checker (S30 floor): you requested this bank-detail change, so a different "
                                 f"holder of {APPROVE} must verify and approve it — no setting and no role (Super Admin "
                                 "included) relaxes this")


async def request_bank_change(pool, *, org_id, actor_id, payee_id, data: dict) -> dict:
    """active → suspended: the requested details wait as pending_*; the approved ones stand until a DIFFERENT person
    approves with a call-back attestation. ``data``: the new bank_name / account_type / last_four / payee_reference
    (contact_phone / contact_email optionally), ``request_contact`` (how the request arrived — required),
    ``request_phone`` (a number given in the request, if any), ``reason``."""
    allowed = set(BANK_CHANGE_FIELDS) | {"request_contact", "request_phone", "reason"}
    unknown = sorted(set(data or {}) - allowed)
    if unknown:
        raise PayeeError(f"not a bank-change field: {', '.join(unknown)}")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payee_id)
    _need(row, ("active",), "change the bank details of")
    if row.get("bank_change_task_id"):
        raise PayeeConflict("a bank-detail change is already awaiting approval")
    new_vals = {f"pending_{k}": _text(data.get(k), k) for k in BANK_CHANGE_FIELDS}
    if new_vals["pending_account_type"] is not None and new_vals["pending_account_type"] not in ACCOUNT_TYPE_KEYS:
        raise PayeeError("account type is one of " + ", ".join(ACCOUNT_TYPE_KEYS))
    if not new_vals["pending_last_four"] or not new_vals["pending_payee_reference"]:
        raise PayeeError("a bank-detail change names the new custodian reference and the new last four digits")
    if new_vals["pending_contact_email"] is not None and not _EMAIL.match(new_vals["pending_contact_email"]):
        raise PayeeError("contact_email: not an email address")
    contact = _text(data.get("request_contact"), "bank_change_request_contact")
    if not contact:
        raise PayeeError("record how the change request arrived (request_contact: who, by which channel)")
    meta = {"bank_change_request_contact": contact,
            "bank_change_request_phone": _text(data.get("request_phone"), "bank_change_request_phone"),
            "bank_change_reason": _text(data.get("reason"), "bank_change_reason")}
    await _number_check(org_id, actor["id"], "request a bank-detail change", {**new_vals, **meta}, payee_id)
    aid = uuid.UUID(str(actor["id"]))
    task = await HT.open_task(
        pool, org_id=org_id, kind=BANK_TASK_KIND, subject_type=SUBJECT, subject_id=row["id"], maker_id=actor["id"],
        eligible_permission=APPROVE, title=f"Verify and approve a bank-detail change: {row['display_name']} "
                                          f"(····{row.get('last_four')} → ····{new_vals['pending_last_four']})",
        payload={"display_name": row["display_name"], "from_last_four": row.get("last_four"),
                 "to_last_four": new_vals["pending_last_four"], "maker_ids": [str(aid)],
                 "callback": "call the number ON FILE — never a number from the request",
                 "floor": "the requester never approves (S30 floor — Super Admin included)"},
        action_path=f"/portfolio/households/{row['household_id']}?tab=treasury&payee={row['id']}#treasury-payees")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, payee_id, lock=True)
                _need(cur, ("active",), "change the bank details of")
                if cur.get("bank_change_task_id"):
                    raise PayeeConflict("a bank-detail change is already awaiting approval")
                new = await _update(conn, cur, {
                    **new_vals, **meta, "status": "suspended", "bank_change_task_id": uuid.UUID(task["id"]),
                    "bank_change_requested_by": aid, "bank_change_requested_at": _now(),
                    "bank_change_approved_by": None, "bank_change_approved_at": None,
                    "callback_person": None, "callback_number": None, "callback_at": None})
                aud = await _audit(conn, org_id, aid, "cash.payee.bank_change_requested", cur["id"],
                                   {**_safe(new), "to_last_four": new_vals["pending_last_four"],
                                    "task_id": task["id"], "suspended": True})
    except Exception:
        await _withdraw_task(pool, task["id"], "the bank-detail change could not be recorded")
        raise
    await _publish(pool, org_id, "bank_change_requested", new["id"], aud, {"task_id": task["id"]}, aid)
    return {**new, "task": task}


def _attestation_of(row: dict, att: dict | None) -> list[str]:
    att = att or {}
    called = att.get("called_at")
    if isinstance(called, str):
        try:
            called = datetime.fromisoformat(called.replace("Z", "+00:00"))
        except ValueError:
            return ["the call-back time is not a date and time"]
    return callback_refusals(person=att.get("person"), number=att.get("number"), called_at=called,
                             on_file=row.get("contact_phone"), request_contact=row.get("bank_change_request_contact"),
                             request_phone=row.get("bank_change_request_phone"),
                             requested_at=row.get("bank_change_requested_at"))


async def _bank_precheck(pool, task, outcome, actor_id):
    """BEFORE the engine completes the task (it stays open on any refusal): still pending; on approve the floor against
    the requester and a valid call-back attestation handed in by decide_bank_change (the generic inbox cannot)."""
    async with pool.acquire() as conn:
        row = await load(conn, task["org_id"], task["subject_id"])
        if str(row.get("bank_change_task_id")) != str(task["id"]):
            raise HT.HumanTaskConflict("this bank-detail change is no longer awaiting approval")
        if outcome != "approve":
            return
        await sod.gate(conn, org_id=task["org_id"], item_type=sod.PAYEE_BANK_CHANGE, checker_id=actor_id,
                       maker_ids=[str(row["bank_change_requested_by"])], item_id=str(row["id"]),
                       household_id=row["household_id"], site="cash_payees._bank_precheck", refuse=_refuse_bank)
    att = _ATTESTATION.get()
    if att is None or att.get("task_id") != str(task["id"]):
        raise PayeeError("a bank-detail change is approved only with a call-back attestation (who you called, the "
                         "number ON FILE, when) — approve it from the payee screen")
    reasons = _attestation_of(row, att)
    if reasons:
        raise PayeeError(" · ".join(reasons))


async def _bank_apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    att = _ATTESTATION.get() or {}
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T} WHERE bank_change_task_id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if row is None:
                return None
            row = dict(row)
            checker = uuid.UUID(str(actor_id))
            clear = {f"pending_{k}": None for k in BANK_CHANGE_FIELDS}
            if outcome == "approve":
                await sod.gate(conn, org_id=org_id, item_type=sod.PAYEE_BANK_CHANGE, checker_id=checker,
                               maker_ids=[str(row["bank_change_requested_by"])], item_id=str(row["id"]),
                               household_id=row["household_id"], site="cash_payees._bank_apply", refuse=_refuse_bank)
                reasons = _attestation_of(row, att) if att.get("task_id") == str(task["id"]) else ["no attestation"]
                if reasons:
                    raise PayeeError(" · ".join(reasons))
                called = att["called_at"]
                if isinstance(called, str):
                    called = datetime.fromisoformat(called.replace("Z", "+00:00"))
                fields = {**clear, "status": "active", "bank_change_task_id": None,
                          "bank_name": row["pending_bank_name"] or row["bank_name"],
                          "account_type": row["pending_account_type"] or row["account_type"],
                          "last_four": row["pending_last_four"], "payee_reference": row["pending_payee_reference"],
                          "contact_phone": row["pending_contact_phone"] or row["contact_phone"],
                          "contact_email": row["pending_contact_email"] or row["contact_email"],
                          "bank_change_approved_by": checker, "bank_change_approved_at": _now(),
                          "callback_person": str(att["person"]).strip(), "callback_number": str(att["number"]).strip(),
                          "callback_at": called}
                new = await _update(conn, row, fields)
                aud = await _audit(conn, org_id, checker, "cash.payee.bank_change_approved", row["id"],
                                   {**_safe(new), "from_last_four": row["last_four"], "task_id": task["id"],
                                    "callback": {"person": fields["callback_person"], "number_on_file": True,
                                                 "at": called.isoformat()}, "note": note})
            else:
                new = await _update(conn, row, {**clear, "status": "active", "bank_change_task_id": None})
                aud = await _audit(conn, org_id, checker, "cash.payee.bank_change_rejected", row["id"],
                                   {**_safe(new), "task_id": task["id"], "note": note,
                                    "kept": "the previously approved bank details stand"})
    await _publish(pool, org_id, "bank_change_" + ("approved" if outcome == "approve" else "rejected"), new["id"], aud,
                   {"task_id": task["id"]}, actor_id)
    return new


async def _on_bank_complete(pool, task, outcome, actor_id, note):
    return await _bank_apply(pool, task, outcome, actor_id, note)


async def decide_bank_change(pool, *, org_id, actor_id, payee_id, outcome: str, attestation: dict | None = None,
                             note: str | None = None) -> dict:
    """Approve (WITH the call-back attestation: person, number, called_at) or reject a pending bank-detail change. The
    floor first (the requester never approves — recorded), then the attestation (each refusal audited, the payee left
    suspended and unchanged), then services.human_tasks.complete_task."""
    if outcome not in ("approve", "reject"):
        raise PayeeError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise PayeeError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payee_id)
    if not row.get("bank_change_task_id"):
        raise PayeeConflict("no bank-detail change is awaiting approval for this payee")
    if outcome == "approve":
        async with pool.acquire() as conn:
            await sod.gate(conn, org_id=org_id, item_type=sod.PAYEE_BANK_CHANGE, checker_id=actor["id"],
                           maker_ids=[str(row["bank_change_requested_by"])], item_id=str(row["id"]),
                           household_id=row["household_id"], site="cash_payees.decide_bank_change",
                           refuse=_refuse_bank)
        att = dict(attestation or {})
        att_safe = {k: att.get(k) for k in ("person", "number", "called_at")}
        reasons = number_refusals({"callback_person": att_safe["person"], "callback_number": att_safe["number"]}) \
            if (att_safe["person"] or att_safe["number"]) else []
        reasons += _attestation_of(row, att_safe)
        if reasons:
            await _refused(org_id, actor["id"], "approve a bank-detail change", reasons, row["id"])
            raise PayeeError(" · ".join(reasons))
        att_safe["task_id"] = str(row["bank_change_task_id"])
    else:
        att_safe = None
    token = _ATTESTATION.set(att_safe)
    try:
        done = await _complete(pool, org_id=org_id, actor=actor, task_id=row["bank_change_task_id"], outcome=outcome,
                               note=note, household_id=row["household_id"])
    finally:
        _ATTESTATION.reset(token)
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await load(conn, org_id, payee_id)
    return result


# ── suspend / resume / retire ───────────────────────────────────────────────────────────────────────────────────────
async def suspend(pool, *, org_id, actor_id, payee_id, reason: str) -> dict:
    """active → suspended (a tightening: no checker). Resuming needs a re-approval by a different person."""
    reason = (reason or "").strip()
    if not reason:
        raise PayeeError("say why the payee is suspended")
    await _number_check(org_id, actor_id, "suspend a payee", {"suspend_reason": reason}, payee_id)
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        async with conn.transaction():
            cur = await load(conn, org_id, payee_id, lock=True)
            _need(cur, ("active",), "suspend")
            new = await _update(conn, cur, {"status": "suspended", "suspended_by": aid, "suspended_at": _now(),
                                            "suspend_reason": reason})
            aud = await _audit(conn, org_id, aid, "cash.payee.suspended", cur["id"], {**_safe(new), "reason": reason})
    await _publish(pool, org_id, "suspended", new["id"], aud, {}, aid)
    return new


async def resume(pool, *, org_id, actor_id, payee_id) -> dict:
    """A manually suspended payee goes back through approval (suspended → pending_approval); the resumer is a maker."""
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, payee_id)
    _need(row, ("suspended",), "resume")
    if row.get("bank_change_task_id"):
        raise PayeeConflict("this payee is suspended for a bank-detail change — approve or reject the change instead")
    return await _open_approval(pool, org_id=org_id, actor=actor, row=row, from_status="suspended", action="resumed")


async def retire(pool, *, org_id, actor_id, payee_id, reason: str) -> dict:
    reason = (reason or "").strip()
    if not reason:
        raise PayeeError("say why the payee is retired")
    await _number_check(org_id, actor_id, "retire a payee", {"retire_reason": reason}, payee_id)
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        async with conn.transaction():
            cur = await load(conn, org_id, payee_id, lock=True)
            _need(cur, ("draft", "pending_approval", "active", "suspended"), "retire")
            tasks = [t for t in (cur.get("approval_task_id"), cur.get("bank_change_task_id")) if t]
            fields = {"status": "retired", "retired_by": aid, "retired_at": _now(), "retire_reason": reason,
                      "approval_task_id": None, "bank_change_task_id": None,
                      **{f"pending_{k}": None for k in BANK_CHANGE_FIELDS}}
            new = await _update(conn, cur, fields)
            aud = await _audit(conn, org_id, aid, "cash.payee.retired", cur["id"], {**_safe(new), "reason": reason})
    for t in tasks:
        await _withdraw_task(pool, t, "the payee was retired")
    await _publish(pool, org_id, "retired", new["id"], aud, {}, aid)
    return new


# ── SLOA ────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def attach_sloa(pool, *, org_id, actor_id, payee_id, document_id, account_ids: list, signed_on=None,
                      expires_on=None) -> dict:
    """Link a Chancery document as the payee's SLOA for the household's source account(s) (any earlier SLOA state is
    replaced — the archived copy keeps it)."""
    if not account_ids:
        raise PayeeError("name the source account(s) the SLOA covers")
    signed, expires = _as_date(signed_on), _as_date(expires_on)
    if signed and expires and expires <= signed:
        raise PayeeError("the SLOA expires after the day it was signed")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        row = await load(conn, org_id, payee_id)
        _need(row, ("draft", "pending_approval", "active", "suspended"), "attach an SLOA to")
        if not await conn.fetchval("SELECT 1 FROM documents WHERE id = $1::uuid AND org_id = $2::uuid",
                                   _uuid(document_id, "document"), str(org_id)):
            raise PayeeError("the SLOA is a Chancery document of this organization")
        accts = sorted({_uuid(a, "account") for a in account_ids})
        ok = {str(r["id"]) for r in await conn.fetch(
            "SELECT id FROM public.accounts WHERE id = ANY($1::uuid[]) AND org_id = $2::uuid AND household_id = "
            "$3::uuid AND system_to IS NULL AND valid_to IS NULL", accts, str(org_id), str(row["household_id"]))}
        if set(accts) - ok:
            raise PayeeError("every SLOA source account is a live account of this household")
        async with conn.transaction():
            cur = await load(conn, org_id, payee_id, lock=True)
            new = await _update(conn, cur, {
                "sloa_document_id": uuid.UUID(_uuid(document_id)), "sloa_account_ids": [uuid.UUID(a) for a in accts],
                "sloa_signed_on": signed, "sloa_expires_on": expires, "sloa_attached_by": aid,
                "sloa_attached_at": _now(), "sloa_expired_at": None, "sloa_expired_by": None,
                "sloa_revoked_at": None, "sloa_revoked_by": None, "sloa_revoke_reason": None})
            await conn.execute(
                "INSERT INTO document_record_links (document_id, org_id, record_type, record_id, created_by) VALUES "
                "($1::uuid, $2::uuid, $3, $4::uuid, $5::uuid) ON CONFLICT (document_id, record_type, record_id) "
                "DO NOTHING", _uuid(document_id), str(org_id), LINK_SLOA, str(cur["id"]), str(aid))
            aud = await _audit(conn, org_id, aid, "cash.payee.sloa_attached", cur["id"],
                               {**_safe(new), "document_id": _uuid(document_id), "accounts": len(accts),
                                "expires_on": expires})
    await _publish(pool, org_id, "sloa_attached", new["id"], aud, {"document_id": _uuid(document_id)}, aid)
    return new


async def expire_sloa(pool, *, org_id, actor_id, payee_id) -> dict:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        async with conn.transaction():
            cur = await load(conn, org_id, payee_id, lock=True)
            if not cur.get("sloa_document_id") or cur.get("sloa_revoked_at") or cur.get("sloa_expired_at"):
                raise PayeeConflict("there is no current SLOA to mark expired")
            new = await _update(conn, cur, {"sloa_expired_at": _now(), "sloa_expired_by": aid})
            aud = await _audit(conn, org_id, aid, "cash.payee.sloa_expired", cur["id"], _safe(new))
    await _publish(pool, org_id, "sloa_expired", new["id"], aud, {}, aid)
    return new


async def revoke_sloa(pool, *, org_id, actor_id, payee_id, reason: str) -> dict:
    reason = (reason or "").strip()
    if not reason:
        raise PayeeError("say why the SLOA is revoked")
    await _number_check(org_id, actor_id, "revoke an SLOA", {"sloa_revoke_reason": reason}, payee_id)
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        async with conn.transaction():
            cur = await load(conn, org_id, payee_id, lock=True)
            if not cur.get("sloa_document_id") or cur.get("sloa_revoked_at"):
                raise PayeeConflict("there is no current SLOA to revoke")
            new = await _update(conn, cur, {"sloa_revoked_at": _now(), "sloa_revoked_by": aid,
                                            "sloa_revoke_reason": reason})
            aud = await _audit(conn, org_id, aid, "cash.payee.sloa_revoked", cur["id"], {**_safe(new), "reason": reason})
    await _publish(pool, org_id, "sloa_revoked", new["id"], aud, {}, aid)
    return new


# ── the third-party switch ──────────────────────────────────────────────────────────────────────────────────────────
async def third_party_enabled(conn, org_id) -> bool:
    """True only for a stored JSON ``true`` AND no release lock. An absent row is off (the platform default)."""
    if THIRD_PARTY_RELEASE_LOCKED:
        return False
    v = await conn.fetchval("SELECT setting_value FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
                            str(org_id), SETTING_THIRD_PARTY)
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return False
    return v is True


async def stored_switch_value(conn, org_id):
    """The raw stored value (None when absent) — what the verify asserts is still not true at the end."""
    v = await conn.fetchval("SELECT setting_value FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
                            str(org_id), SETTING_THIRD_PARTY)
    return json.loads(v) if isinstance(v, str) else v


async def third_party_refusal(conn, org_id) -> str | None:
    """What services.payments asks first when an instruction names a payee: the switch."""
    return None if await third_party_enabled(conn, org_id) else NOT_ENABLED


async def _is_super(conn, user_id) -> bool:
    from services import authz

    p = await authz.load_principal(conn, user_id)
    return bool(p is not None and p.is_super_admin)


async def switch_task(conn, org_id) -> dict | None:
    t = await HT.list_tasks(conn, org_id, status="open", kind=SWITCH_TASK_KIND, subject_type=SWITCH_SUBJECT,
                            subject_id=str(org_id), limit=1)
    return t[0] if t else None


async def request_third_party_switch(pool, *, org_id, actor_id, counsel_document_id, reason: str) -> dict:
    """A Super Admin asks to turn ON third-party payments, naming counsel's sign-off (a Chancery document of the org).
    Opens the decision task; changes nothing."""
    reason = (reason or "").strip()
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        if not await _is_super(conn, aid):
            await _refused(org_id, aid, "request third-party payments", ["only a Super Admin may ask"], None,
                           "cash.third_party.refused")
            raise PayeeRefused("only a Super Admin may ask to turn on third-party payments")
        if not counsel_document_id or not await conn.fetchval(
                "SELECT 1 FROM documents WHERE id = $1::uuid AND org_id = $2::uuid",
                _uuid(counsel_document_id, "counsel document"), str(org_id)):
            await _refused(org_id, aid, "request third-party payments",
                           ["a counsel sign-off document (Chancery) is required"], None, "cash.third_party.refused")
            raise PayeeError("turning on third-party payments needs counsel's sign-off — a Chancery document of this "
                             "organization (counsel_document_id)")
        if not reason:
            raise PayeeError("say why (reason)")
    task = await HT.open_task(
        pool, org_id=org_id, kind=SWITCH_TASK_KIND, subject_type=SWITCH_SUBJECT, subject_id=org_id, maker_id=aid,
        eligible_permission=SWITCH_PERMISSION, title="Decide: turn on third-party payments (two Super Admins)",
        payload={"setting": SETTING_THIRD_PARTY, "value": True, "counsel_document_id": _uuid(counsel_document_id),
                 "maker_ids": [str(aid)], "reason": reason,
                 "floor": "a DIFFERENT Super Admin decides (S30 floor); counsel's sign-off is on file"},
        action_path="/admin/settings")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO document_record_links (document_id, org_id, record_type, record_id, created_by) VALUES "
                "($1::uuid, $2::uuid, $3, $2::uuid, $4::uuid) ON CONFLICT (document_id, record_type, record_id) "
                "DO NOTHING", _uuid(counsel_document_id), str(org_id), LINK_COUNSEL, str(aid))
            aud = await _audit(conn, org_id, aid, "cash.third_party.requested", task["id"],
                               {"setting": SETTING_THIRD_PARTY, "value": True, "task_id": task["id"],
                                "counsel_document_id": _uuid(counsel_document_id), "reason": reason},
                               resource_type="org_setting")
    await _publish(pool, org_id, "third_party_requested", None, aud, {"task_id": task["id"]}, aid)
    return task


def _refuse_switch(_d) -> Exception:
    return MakerCheckerViolation("maker-checker (S30 floor): you asked for third-party payments, so a DIFFERENT Super "
                                 "Admin must decide — no setting and no role relaxes this")


async def _switch_checks(pool, conn, task: dict, outcome: str, actor_id) -> None:
    org_id = task["org_id"]
    await sod.gate(conn, org_id=org_id, item_type=sod.THIRD_PARTY_SETTING, checker_id=actor_id,
                   maker_ids=HT.task_makers(task), item_id=str(task["id"]), site="cash_payees._switch_checks",
                   refuse=_refuse_switch)
    if not await _is_super(conn, actor_id):
        raise PayeeRefused("a second Super Admin decides third-party payments")
    if outcome != "approve":
        return
    doc = (task.get("payload") or {}).get("counsel_document_id")
    if not doc or not await conn.fetchval("SELECT 1 FROM documents WHERE id = $1::uuid AND org_id = $2::uuid",
                                          str(doc), str(org_id)):
        raise PayeeRefused("counsel's sign-off document is no longer on file")
    if THIRD_PARTY_RELEASE_LOCKED:
        raise PayeeRefused(RELEASE_LOCK_REASON)


async def _switch_precheck(pool, task, outcome, actor_id):
    async with pool.acquire() as conn:
        await _switch_checks(pool, conn, task, outcome, actor_id)


async def _switch_apply(pool, task: dict, outcome: str, actor_id, note):
    """Unreachable for an approval while THIRD_PARTY_RELEASE_LOCKED (the precheck refuses first); kept so the path is
    reviewed, not invented, when counsel signs off. A rejection only records the decision."""
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            if outcome == "approve":
                if THIRD_PARTY_RELEASE_LOCKED:
                    raise PayeeRefused(RELEASE_LOCK_REASON)
                await conn.execute(
                    "INSERT INTO org_settings (org_id, setting_key, setting_value, category, updated_by) VALUES "
                    "($1::uuid, $2, 'true'::jsonb, 'cash', $3::uuid) ON CONFLICT (org_id, setting_key) DO UPDATE SET "
                    "setting_value = 'true'::jsonb, updated_by = $3::uuid, updated_at = now()",
                    str(org_id), SETTING_THIRD_PARTY, str(actor_id))
            aud = await _audit(conn, org_id, actor_id, "cash.third_party." + ("approved" if outcome == "approve"
                                                                               else "rejected"), task["id"],
                               {"task_id": task["id"], "note": note}, resource_type="org_setting")
    await _publish(pool, org_id, "third_party_" + ("approved" if outcome == "approve" else "rejected"), None, aud,
                   {"task_id": task["id"]}, actor_id)
    return {"setting": SETTING_THIRD_PARTY, "outcome": outcome}


async def decide_third_party_switch(pool, *, org_id, actor_id, outcome: str, note: str | None = None) -> dict:
    """A DIFFERENT Super Admin decides the open request. Approval is refused in this release (the lock); each refusal
    is recorded (sod_decisions for the floor, audit_log for the rest) and the switch stays off."""
    if outcome not in ("approve", "reject"):
        raise PayeeError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise PayeeError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        task = await switch_task(conn, org_id)
        if task is None:
            raise PayeeConflict("no request to turn on third-party payments is open")
        try:
            await _switch_checks(pool, conn, task, outcome, actor["id"])
        except PayeeRefused as exc:
            await _refused(org_id, actor["id"], "decide third-party payments", [str(exc)], None,
                           "cash.third_party.refused")
            raise
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"], outcome=outcome,
                                      note=(note or "").strip() or None)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise PayeeConflict(str(exc)) from exc
    return done.get("subject_result") or {"setting": SETTING_THIRD_PARTY, "outcome": outcome}


async def withdraw_third_party_request(pool, *, org_id, actor_id, reason: str) -> dict:
    async with pool.acquire() as conn:
        task = await switch_task(conn, org_id)
    if task is None:
        raise PayeeConflict("no request to turn on third-party payments is open")
    return await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor_id, reason=reason)


# ── first payment to a new payee (dormant while third-party payments are off) ───────────────────────────────────────
def _refuse_first(_d) -> Exception:
    return MakerCheckerViolation("the first payment to a new payee is approved by someone who neither created nor "
                                 "approved the payee (S30 floor)")


async def first_payment_gate(conn, *, org_id, payee: dict, checker_id, instruction_makers=(), item_id=None,
                             household_id=None, site: str = "cash_payees.first_payment_gate"):
    """The S30 gate for ``first_payment_to_new_payee``: the payee's creator and approver (and the instruction's own
    makers) are all makers. Called by services.payments when an instruction names a payee — today none can."""
    makers = [str(m) for m in (payee.get("created_by"), payee.get("approved_by"), *instruction_makers) if m]
    return await sod.gate(conn, org_id=org_id, item_type=sod.FIRST_PAYEE_PAYMENT, checker_id=checker_id,
                          maker_ids=makers, item_id=str(item_id) if item_id else None, household_id=household_id,
                          site=site, refuse=_refuse_first)


HT.register_kind(TASK_KIND, label="Payee approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)
HT.register_precheck(TASK_KIND, _precheck)
HT.register_kind(BANK_TASK_KIND, label="Bank-detail change (call-back)", outcomes=("approve", "reject"),
                 maker_checker=True, on_complete=_on_bank_complete)
HT.register_precheck(BANK_TASK_KIND, _bank_precheck)
HT.register_kind(SWITCH_TASK_KIND, label="Third-party payments switch", outcomes=("approve", "reject"),
                 maker_checker=True, on_complete=_switch_apply)
HT.register_precheck(SWITCH_TASK_KIND, _switch_precheck)


# ── reading ─────────────────────────────────────────────────────────────────────────────────────────────────────────
async def _names(conn, ids) -> dict[str, str]:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = await conn.fetch("SELECT id, full_name FROM public.users WHERE id = ANY($1::uuid[])", ids)
    return {str(r["id"]): (r["full_name"] or "A colleague") for r in rows}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def payee_out(r: dict, *, users: dict, t: date, member: bool = False) -> dict:
    """A payee for a screen. A MEMBER sees the name, kind, last four and status only — never the reference, the
    contact, the SLOA document or the attestation."""
    base = {"id": str(r["id"]), "household_id": str(r["household_id"]), "display_name": r["display_name"],
            "kind": r["kind"], "kind_label": dict(KINDS).get(r["kind"], r["kind"]), "last_four": r.get("last_four"),
            "status": r["status"], "status_label": STATUS_LABELS.get(r["status"], r["status"])}
    if member:
        return base
    fl = flags(r, t)
    who = lambda k: users.get(str(r.get(k))) if r.get(k) else None  # noqa: E731
    return {**base,
            "legal_name": r.get("legal_name"), "contact_name": r.get("contact_name"),
            "contact_email": r.get("contact_email"), "contact_phone": r.get("contact_phone"),
            "bank_name": r.get("bank_name"), "account_type": r.get("account_type"),
            "payee_reference": r.get("payee_reference"), "notes": r.get("notes"),
            "created_by": str(r["created_by"]), "created_by_name": who("created_by"),
            "maker_ids": [str(m) for m in r.get("maker_ids") or []],
            "approved_by_name": who("approved_by"), "approved_at": _iso(r.get("approved_at")),
            "flags": [{"key": k, "label": FLAG_LABELS[k]} for k in fl],
            "sloa": {"document_id": str(r["sloa_document_id"]) if r.get("sloa_document_id") else None,
                     "account_ids": [str(a) for a in r.get("sloa_account_ids") or []],
                     "signed_on": _iso(r.get("sloa_signed_on")), "expires_on": _iso(r.get("sloa_expires_on")),
                     "expired_at": _iso(r.get("sloa_expired_at")), "revoked_at": _iso(r.get("sloa_revoked_at")),
                     "revoke_reason": r.get("sloa_revoke_reason"),
                     "state": next((k for k in ("sloa_missing", "sloa_revoked", "sloa_expired", "sloa_expiring_soon")
                                    if k in fl), "sloa_current"),
                     "blocks_payment": sloa_blocks_payment(r, t)},
            "bank_change": ({"pending_last_four": r.get("pending_last_four"),
                             "pending_payee_reference": r.get("pending_payee_reference"),
                             "pending_bank_name": r.get("pending_bank_name"),
                             "pending_account_type": r.get("pending_account_type"),
                             "requested_by": str(r["bank_change_requested_by"]),
                             "requested_by_name": who("bank_change_requested_by"),
                             "requested_at": _iso(r.get("bank_change_requested_at")),
                             "request_contact": r.get("bank_change_request_contact"),
                             "reason": r.get("bank_change_reason"),
                             "callback_number_on_file": r.get("contact_phone")}
                            if r.get("bank_change_task_id") else None),
            "last_callback": ({"person": r.get("callback_person"), "number": r.get("callback_number"),
                               "at": _iso(r.get("callback_at")), "approved_by_name": who("bank_change_approved_by")}
                              if r.get("bank_change_approved_at") else None),
            "suspend_reason": r.get("suspend_reason"), "retire_reason": r.get("retire_reason")}


async def list_payees(conn, org_id, household_id, *, member: bool = False, status: str | None = None) -> list[dict]:
    if status is not None and status not in STATUS_LABELS:
        raise PayeeError("status is one of " + ", ".join(STATUS_LABELS))
    rows = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T} WHERE org_id = $1::uuid AND household_id = $2::uuid AND system_to IS NULL "
        "AND ($3::text IS NULL OR status = $3::text) ORDER BY lower(display_name), id",
        str(org_id), str(household_id), status)]
    if member:
        rows = [r for r in rows if r["status"] == "active"]
    users = {} if member else await _names(conn, [u for r in rows for u in (r["created_by"], r.get("approved_by"),
                                                                             r.get("bank_change_requested_by"),
                                                                             r.get("bank_change_approved_by"))])
    t = today()
    return [payee_out(r, users=users, t=t, member=member) for r in rows]


async def history(conn, org_id, payee_id) -> list[dict]:
    """Every state of the payee, oldest first (archived copies on the system axis + the live row)."""
    rows = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T} WHERE org_id = $1::uuid AND (id = $2::uuid OR archived_from_id = $2::uuid) "
        "ORDER BY system_from, system_to NULLS LAST", str(org_id), _uuid(payee_id))]
    users = await _names(conn, [u for r in rows for u in (r.get("approved_by"), r.get("bank_change_requested_by"),
                                                          r.get("bank_change_approved_by"))])
    return [{"from": _iso(r["system_from"]), "to": _iso(r["system_to"]), "status": r["status"],
             "status_label": STATUS_LABELS.get(r["status"], r["status"]), "last_four": r.get("last_four"),
             "payee_reference": r.get("payee_reference"), "bank_name": r.get("bank_name"),
             "pending_last_four": r.get("pending_last_four"),
             "callback_person": r.get("callback_person"), "callback_at": _iso(r.get("callback_at")),
             "bank_change_approved_by_name": users.get(str(r.get("bank_change_approved_by")))
             if r.get("bank_change_approved_by") else None,
             "current": r["system_to"] is None} for r in rows]


async def detail(conn, org_id, payee_id, *, member: bool = False) -> dict:
    row = await load(conn, org_id, payee_id)
    if member and row["status"] != "active":
        raise PayeeNotFound("payee not found")
    users = {} if member else await _names(conn, [row["created_by"], row.get("approved_by"),
                                                  row.get("bank_change_requested_by"),
                                                  row.get("bank_change_approved_by")])
    out = payee_out(row, users=users, t=today(), member=member)
    if not member:
        out["history"] = await history(conn, org_id, payee_id)
        if row.get("sloa_document_id"):
            d = await conn.fetchrow("SELECT original_filename FROM documents WHERE id = $1 AND org_id = $2::uuid",
                                    row["sloa_document_id"], str(org_id))
            out["sloa"]["document_name"] = d["original_filename"] if d else None
    return out


def actions_for(p: dict, *, can_manage: bool, can_approve: bool, user_id, member: bool) -> list[dict]:
    """The server's own action list for one payee (the screens render ONLY these)."""
    if member:
        return []
    out = []
    st = p["status"]
    me = str(user_id) if user_id else None
    makers = set(p.get("maker_ids") or [])
    if can_manage:
        if st == "draft":
            out += ["edit", "submit"]
        if st == "active" and not p.get("bank_change"):
            out += ["request_bank_change", "suspend"]
        if st == "suspended" and not p.get("bank_change"):
            out.append("resume")
        if st != "retired":
            out.append("attach_sloa")
            sl = p.get("sloa") or {}
            if sl.get("document_id") and not sl.get("revoked_at"):
                if not sl.get("expired_at"):
                    out.append("expire_sloa")
                out.append("revoke_sloa")
            out.append("retire")
    if can_approve:
        if st == "pending_approval" and sod.may_check_display(me, makers):
            out += ["approve", "reject"]
        bc = p.get("bank_change")
        if bc and sod.may_check_display(me, [bc.get("requested_by")]):
            out += ["approve_bank_change", "reject_bank_change"]
    return [{"key": k, "label": ACTION_LABELS[k]} for k in out]


def vocabularies(*, can_write: bool) -> dict:
    return {"kinds": [{"key": k, "label": v} for k, v in KINDS],
            "account_types": [{"key": k, "label": v} for k, v in ACCOUNT_TYPES],
            "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "flags": [{"key": k, "label": v} for k, v in FLAG_LABELS.items()],
            "editable": list(EDITABLE) if can_write else [], "inline_editable": []}
