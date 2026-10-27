"""Account AUTHORITY and CUSTODY (rep7b) — two separate settings per account.

AUTHORITY — what the adviser may do with the account:
    read_only   data only. Every trade path refuses (rep7's held-away rule).
    advise      one-off recommendations: a manual one-off proposal (no model,
                no join, no drift / model-change / cash-flow / harvest), which
                once approved becomes instructions the MEMBER or ANOTHER
                ADVISER executes, each with rep2's letter of instruction.
                Never routed to a custodian API.
    managed     Hollisworks runs the model (rep5): joins, every proposal
                trigger, harvest. At the integrated custodian, as today
                (adapter routing). At ANOTHER custodian, the instructions are
                MANUAL DVP settlement instructions (executor broker / trader)
                carrying the receiving custodian's details — never an API call.

CUSTODY — where the account is held:
    integrated        the integrated custodian feed (accounts.is_held_away = false)
    other_custodian   another custodian, settled delivery-versus-payment
    outside           outside the platform (no custodian to settle with)

managed + outside is not a setting: managed needs a custodian. It is refused
as a request (and by a CHECK); an account that ends up there by inheriting a
household's managed authority is refused on every path with the reason.

ONE SOURCE OF TRUTH per decision (Task 1b):
    which paths are allowed      → the effective authority × custody (here)
    who executes, when managed   → rep5's advice_only (= not is_discretionary at
                                   join time), unchanged: a non-discretionary
                                   managed account's proposals go to the member
    who executes, when advise    → the authority itself (member / other_adviser)
"advise ⇔ not discretionary" was NOT adopted: a non-discretionary account that
follows a model today gets drift / model-change proposals as advice (rep5);
mapping it to advise would refuse those — a behaviour change no one approved.

EFFECTIVE AUTHORITY = the account's approved override ?? its household's
approved authority ?? the migrated default. The migrated default (no row was
written — Task 1c) is rep7's behaviour exactly:
    is_held_away = true   → read_only, custody outside
    is_held_away = false  → managed,   custody integrated
A row is in effect from its effective_from; among approved rows in effect the
latest DECISION wins (a later approval replaces an earlier one).

CHANGES are maker-checker: a request (new authority, effective date, reason)
opens the human task 'approve_authority_change' (permission approve_authority,
never the maker). Approval never alters an open proposal: a draft or pending
proposal the new authority would not allow is withdrawn with a plain reason
(rep5's expire path, its task withdrawn); an approved proposal that has not
routed stays approved but every route refuses it.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from services import human_tasks as HT

log = logging.getLogger(__name__)

VIEW, MANAGE, APPROVE = "view_authority", "manage_authority", "approve_authority"
T_HH = "public.household_authority"
T_ACC = "public.account_authority_settings"
T_SET = "public.instruction_settlements"
TASK = "approve_authority_change"
SUBJECT_HH = "household_authority"
SUBJECT_ACC = "account_authority"
EVENT_SOURCE = "authority"

AUTHORITIES = ("read_only", "advise", "managed")
CUSTODIES = ("integrated", "other_custodian", "outside")
AUTHORITY_LABELS = {"read_only": "Read-only — data only",
                    "advise": "Advise — one-off recommendations the member or another adviser executes",
                    "managed": "Managed — Hollisworks runs the model"}
AUTHORITY_SHORT = {"read_only": "read-only", "advise": "advise", "managed": "managed"}
CUSTODY_LABELS = {"integrated": "Integrated custodian", "other_custodian": "Another custodian — settled DVP",
                  "outside": "Outside the platform"}
CUSTODY_SHORT = {"integrated": "integrated custodian", "other_custodian": "another custodian (DVP)",
                 "outside": "outside the platform"}
SOURCE_LABELS = {"account": "Set on this account", "household": "The household's setting",
                 "default": "Default (held-away accounts read-only; others managed)"}
STATUS_LABELS = {"draft": "Awaiting a second person's approval", "approved": "Approved", "rejected": "Rejected"}
CALENDAR_BASIS = "weekends_only"
CALENDAR_NOTE = ("settlement date = trade date + 1 business day, counting weekends only — no holiday calendar "
                 "exists in this build")

TRIGGERS = ("join", "model_change", "drift", "cash_flow", "manual", "harvest")
PATH_LABELS = {
    "join": "Join a model", "change_model": "Change model", "settings": "Change model settings",
    "transition": "Transition preview", **{f"proposal:{t}": f"Proposal — {t.replace('_', ' ')}" for t in TRIGGERS},
    "one_off": "One-off trade proposal", "harvest": "Tax-loss harvest scan",
    "execute": "Route an approved proposal to instructions", "instructions": "Create execution instructions",
    "route": "Route a trade list",
}
MODEL_PATHS = frozenset({"join", "change_model", "settings", "transition", "harvest",
                         *(f"proposal:{t}" for t in TRIGGERS)})
EXEC_PATHS = frozenset({"execute", "instructions", "route"})
ALL_PATHS = tuple(PATH_LABELS)
ALL_EXECUTORS = ("custodian_desk", "broker", "member", "other_adviser", "trader")
ADVISE_EXECUTORS = ("member", "other_adviser")
DVP_EXECUTORS = ("broker", "trader")


class AuthorityError(Exception):
    """A request the rules refuse (422)."""


class AuthorityNotFound(AuthorityError, LookupError):
    pass


class AuthorityConflict(AuthorityError):
    """The state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    pass


# ── The matrix (pure) ───────────────────────────────────────────────────────
def allowed_paths(authority: str, custody: str) -> frozenset:
    if authority == "managed" and custody in ("integrated", "other_custodian"):
        return MODEL_PATHS | EXEC_PATHS
    if authority == "advise":
        return frozenset({"one_off"}) | EXEC_PATHS
    return frozenset()


def allowed_executors(authority: str, custody: str) -> tuple[str, ...]:
    if authority == "managed" and custody == "integrated":
        return ALL_EXECUTORS
    if authority == "managed" and custody == "other_custodian":
        # DVP (broker / trader); member / other adviser only for rep5's advice-only (non-discretionary) proposals
        return DVP_EXECUTORS + ADVISE_EXECUTORS
    if authority == "advise":
        return ADVISE_EXECUTORS
    return ()


def valid_setting(authority: str, custody: str) -> bool:
    return not (authority == "managed" and custody == "outside")


def matrix() -> list[dict]:
    return [{"authority": a, "custody": c, "valid": valid_setting(a, c),
             "paths": sorted(allowed_paths(a, c)), "executors": list(allowed_executors(a, c))}
            for a in AUTHORITIES for c in CUSTODIES]


def default_for(account: dict) -> tuple[str, str]:
    """The migrated default — rep7's behaviour, exactly."""
    return ("read_only", "outside") if account.get("is_held_away") else ("managed", "integrated")


def effective(account: dict) -> dict:
    """The attached effective authority, or the migrated default when the
    caller's account dict was loaded without :func:`attach`."""
    if account.get("authority") and account.get("custody"):
        return {"authority": account["authority"], "custody": account["custody"],
                "authority_source": account.get("authority_source") or "default"}
    a, c = default_for(account)
    return {"authority": a, "custody": c, "authority_source": "default"}


def path_allowed(account: dict, path: str, executor: str | None = None) -> bool:
    e = effective(account)
    if path not in allowed_paths(e["authority"], e["custody"]):
        return False
    if executor is not None and path in EXEC_PATHS:
        return executor in allowed_executors(e["authority"], e["custody"])
    return True


def refusal(account: dict | None, action: str, *, path: str | None = None, executor: str | None = None) -> str | None:
    """The plain-words reason a path refuses this account, or None. Names the
    account (masked), its authority and custody, and what would allow it."""
    if not account:
        return None
    path = path or "route"
    if path_allowed(account, path, executor):
        return None
    e = effective(account)
    a, c = e["authority"], e["custody"]
    name = account.get("account_number_masked") or "this account"
    head = (f"{name} is a held-away account (authority {AUTHORITY_SHORT[a]}, custody {CUSTODY_SHORT[c]})"
            if account.get("is_held_away") else f"{name} (authority {AUTHORITY_SHORT[a]}, custody {CUSTODY_SHORT[c]})")
    if a == "read_only":
        why = ("its data is read-only and Hollisworks never trades it. What would allow it: an approved authority "
               "change to advise (one-off trades the member or another adviser executes) or to managed (at another "
               "custodian, settled DVP)")
    elif a == "managed" and c == "outside":
        why = ("managed needs a custodian and this account is outside the platform. What would allow it: an approved "
               "custody change to another custodian (settled DVP) with its receiving-custodian details")
    elif a == "advise" and path in MODEL_PATHS:
        why = ("advise covers one-off trades only — no model, join, drift, model-change, cash-flow or harvest. "
               "What would allow it: an approved authority change to managed")
    elif a == "advise" and path in EXEC_PATHS:
        why = ("under advise the member or another adviser executes — choose executor member or other_adviser "
               "(a letter of instruction goes with each instruction)")
    elif a == "managed" and path == "one_off":
        why = "a one-off trade proposal is the advise path; a managed account uses its model (Rebalance now)"
    elif a == "managed" and c == "other_custodian" and path in EXEC_PATHS:
        why = ("at another custodian the trade settles DVP — executor broker or trader (or the member, for an "
               "advice-only proposal); never the integrated custodian's desk or API")
    else:
        why = "the authority and custody do not allow it"
    return f"{head} — {action} refused: {why}."


def account_actions(account: dict) -> list[str]:
    """The trade-path actions the matrix allows this account (for envelopes)."""
    e = effective(account)
    allowed = allowed_paths(e["authority"], e["custody"])
    return [p for p in ALL_PATHS if p in allowed]


def vocabularies() -> dict:
    return {"authorities": [{"key": k, "label": v} for k, v in AUTHORITY_LABELS.items()],
            "custodies": [{"key": k, "label": v} for k, v in CUSTODY_LABELS.items()],
            "sources": [{"key": k, "label": v} for k, v in SOURCE_LABELS.items()],
            "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "paths": [{"key": k, "label": v} for k, v in PATH_LABELS.items()],
            "matrix": matrix(), "calendar_note": CALENDAR_NOTE}


# ── Small helpers ───────────────────────────────────────────────────────────
def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else o.isoformat()
                      if isinstance(o, (date, datetime)) else str(o))


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise AuthorityNotFound(f"{what} not found") from None


def _iso(v):
    return v.isoformat() if v else None


def _s(v):
    return None if v is None else str(v)


def mask_last4(v: str | None) -> str | None:
    """The receiving account is stored MASKED only: '****' + its last 1–4
    characters. A request carries the last four, never the full number."""
    if v is None or not str(v).strip():
        return None
    tail = str(v).strip()
    if not tail.isalnum() or len(tail) > 4:
        raise AuthorityError("the receiving account is given as its last four characters only — never the full "
                             "number")
    return "****" + tail


def next_business_day(d: date) -> date:
    """T+1 counting weekends only (no holiday calendar exists — CALENDAR_NOTE)."""
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"authority.{action}", EVENT_SOURCE, str(resource_id),
        _dumps(payload))


async def _publish(pool, org_id, action: str, resource_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    try:
        await publish_event(pool, str(org_id), f"authority.{action}", EVENT_SOURCE, str(resource_id),
                            json.loads(_dumps(payload)), created_by=str(actor_id) if actor_id else None)
    except Exception:  # noqa: BLE001 — the event is a notification; the write stands
        log.exception("authority.%s event not published", action)


async def _archive(conn, table: str, row_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {table} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(row_id))
    if got is None:
        raise AuthorityConflict("the change is not live — refusing to change it")


# ── Reading the effective setting ───────────────────────────────────────────
async def _in_effect(conn, table: str, key_col: str, key, org_id, today: date) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT * FROM {table} WHERE org_id = $1::uuid AND {key_col} = $2::uuid AND status = 'approved' "
        "AND system_to IS NULL AND effective_from <= $3 ORDER BY decided_at DESC, created_at DESC LIMIT 1",
        str(org_id), str(key), today)
    return dict(row) if row else None


async def household_in_effect(conn, org_id, household_id, today: date | None = None) -> dict | None:
    if not household_id:
        return None
    return await _in_effect(conn, T_HH, "household_id", household_id, org_id, today or date.today())


async def account_in_effect(conn, org_id, account_id, today: date | None = None) -> dict | None:
    return await _in_effect(conn, T_ACC, "account_id", account_id, org_id, today or date.today())


def _compose(account: dict, s: dict | None, h: dict | None) -> dict:
    d_auth, d_cust = default_for(account)
    custody = s["custody"] if s else d_cust
    if s and s["authority_override"]:
        authority, source = s["authority_override"], "account"
    elif h:
        authority, source = h["authority"], "household"
    else:
        authority, source = d_auth, "default"
    return {"authority": authority, "authority_source": source, "custody": custody,
            "custody_source": "account" if s else "default", "settings_id": str(s["id"]) if s else None,
            "household_authority_id": str(h["id"]) if h else None,
            "authority_valid": valid_setting(authority, custody),
            "receiving": ({"custodian_name": s["receiving_custodian_name"],
                           "participant_number": s["receiving_participant_number"],
                           "agent_bank": s["receiving_agent_bank"],
                           "account_masked": s["receiving_account_masked"],
                           "contra_broker": s["contra_broker"]} if s and s["custody"] == "other_custodian" else None)}


async def attach(conn, org_id, account: dict, today: date | None = None) -> dict:
    """Adds the effective authority / custody (and their sources) to the
    account dict in place, and returns it. RLS-scoped reads."""
    today = today or date.today()
    s = await account_in_effect(conn, org_id, account["id"], today)
    h = await household_in_effect(conn, org_id, account.get("household_id"), today)
    account.update(_compose(account, s, h))
    return account


async def attach_many(conn, org_id, accounts: list[dict], today: date | None = None) -> list[dict]:
    for a in accounts:
        await attach(conn, org_id, a, today)
    return accounts


def authority_out(account: dict) -> dict:
    e = effective(account)
    return {"authority": e["authority"], "authority_label": AUTHORITY_LABELS[e["authority"]],
            "authority_source": e["authority_source"], "custody": e["custody"],
            "custody_label": CUSTODY_LABELS[e["custody"]], "custody_source": account.get("custody_source") or "default",
            "valid": valid_setting(e["authority"], e["custody"]), "receiving": account.get("receiving"),
            "allowed_paths": account_actions(account)}


# ── Change requests ─────────────────────────────────────────────────────────
async def _account(conn, org_id, account_id) -> dict:
    row = await conn.fetchrow(
        "SELECT id, org_id, account_number_masked, custodian_code, registration_type, tax_status, household_id, "
        "is_discretionary, is_held_away, closed_on FROM public.accounts WHERE id = $1::uuid AND org_id = $2::uuid "
        "AND system_to IS NULL AND valid_to IS NULL", _uuid(account_id, "account"), str(org_id))
    if row is None:
        raise AuthorityNotFound("account not found")
    return dict(row)


async def _household(conn, org_id, household_id) -> dict:
    row = await conn.fetchrow("SELECT id, org_id, name FROM public.households WHERE id = $1::uuid AND org_id = $2::uuid",
                              _uuid(household_id, "household"), str(org_id))
    if row is None:
        raise AuthorityNotFound("household not found")
    return dict(row)


async def household_accounts(conn, org_id, household_id, today: date | None = None) -> list[dict]:
    rows = [dict(r) for r in await conn.fetch(
        "SELECT id, org_id, account_number_masked, custodian_code, registration_type, tax_status, household_id, "
        "is_discretionary, is_held_away, closed_on FROM public.accounts WHERE org_id = $1::uuid "
        "AND household_id = $2::uuid AND system_to IS NULL AND valid_to IS NULL ORDER BY account_number_masked",
        str(org_id), str(household_id))]
    return await attach_many(conn, org_id, rows, today)


def _check_dates(effective_from: date, today: date) -> None:
    if not isinstance(effective_from, date):
        raise AuthorityError("effective_from is a date")
    if effective_from < today:
        raise AuthorityError(f"an authority change takes effect today or later (effective_from "
                             f"{effective_from.isoformat()} is before {today.isoformat()})")


def _inheritors_outside(accounts: list[dict], authority: str) -> list[str]:
    """Accounts that would inherit managed while outside the platform."""
    if authority != "managed":
        return []
    return [a["account_number_masked"] for a in accounts
            if a["authority_source"] != "account" and a["custody"] == "outside"]


async def request_household_change(pool, *, org_id, actor_id, household_id, authority: str, effective_from: date,
                                   reason: str, today: date | None = None) -> dict:
    """A DRAFT household authority change + its approval task."""
    today = today or date.today()
    if authority not in AUTHORITIES:
        raise AuthorityError(f"authority is one of {list(AUTHORITIES)}")
    if not (reason or "").strip():
        raise AuthorityError("an authority change needs a reason")
    _check_dates(effective_from, today)
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        hh = await _household(conn, org_id, household_id)
        if await conn.fetchval(f"SELECT 1 FROM {T_HH} WHERE household_id = $1 AND status = 'draft' "
                               "AND system_to IS NULL", hh["id"]):
            raise AuthorityConflict("a change for this household is already awaiting approval — decide it first")
        accts = await household_accounts(conn, org_id, hh["id"], today)
        cur = await household_in_effect(conn, org_id, hh["id"], today)
    bad = _inheritors_outside(accts, authority)
    if bad:
        raise AuthorityConflict(f"managed needs a custodian: {', '.join(bad)} would inherit managed while outside "
                                "the platform — record their custody (another custodian, settled DVP) or give them "
                                "their own authority first")
    rid = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK, subject_type=SUBJECT_HH, subject_id=rid, maker_id=actor["id"],
        eligible_permission=APPROVE, title=f"Approve authority change: {hh['name']} → {AUTHORITY_SHORT[authority]}",
        payload={"household": hh["name"], "authority": authority, "from": cur["authority"] if cur else None,
                 "effective_from": effective_from.isoformat(), "reason": reason.strip()},
        action_path=f"/portfolio/households/{hh['id']}/authority")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_HH} (id, org_id, household_id, authority, effective_from, reason, status,
                            maker_id, approval_task_id)
                        VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, 'draft', $7::uuid, $8::uuid) RETURNING *""",
                    rid, str(org_id), str(hh["id"]), authority, effective_from, reason.strip(), str(actor["id"]),
                    task["id"])
                await _audit(conn, org_id, actor["id"], "household_requested", rid,
                             {"household_id": str(hh["id"]), "authority": authority,
                              "effective_from": effective_from.isoformat(), "task_id": task["id"]})
    except Exception:
        await _withdraw_task(pool, task["id"], "the authority change could not be recorded")
        raise
    await _publish(pool, org_id, "household_requested", rid, {"task_id": task["id"], "authority": authority},
                   actor["id"])
    return {**change_out(dict(row), SUBJECT_HH), "task": task}


async def request_account_change(pool, *, org_id, actor_id, account_id, authority_override: str | None,
                                 custody: str, effective_from: date, reason: str, receiving: dict | None = None,
                                 today: date | None = None) -> dict:
    """A DRAFT account setting (override + custody + receiving details) + its
    approval task."""
    today = today or date.today()
    if authority_override is not None and authority_override not in AUTHORITIES:
        raise AuthorityError(f"authority is one of {list(AUTHORITIES)} (or none, to follow the household)")
    if custody not in CUSTODIES:
        raise AuthorityError(f"custody is one of {list(CUSTODIES)}")
    if not (reason or "").strip():
        raise AuthorityError("an authority change needs a reason")
    _check_dates(effective_from, today)
    rc = dict(receiving or {})
    unknown = set(rc) - {"custodian_name", "participant_number", "agent_bank", "account_last4", "contra_broker"}
    if unknown:
        raise AuthorityError(f"unknown receiving-custodian field(s): {sorted(unknown)}")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        acct = await _account(conn, org_id, account_id)
        await attach(conn, org_id, acct, today)
        if await conn.fetchval(f"SELECT 1 FROM {T_ACC} WHERE account_id = $1 AND status = 'draft' "
                               "AND system_to IS NULL", acct["id"]):
            raise AuthorityConflict("a change for this account is already awaiting approval — decide it first")
        h = await household_in_effect(conn, org_id, acct.get("household_id"), today)
    if acct["is_held_away"] and custody == "integrated":
        raise AuthorityError(f"{acct['account_number_masked']} is held away — its data does not come from the "
                             "integrated custodian; custody is another custodian or outside the platform")
    if not acct["is_held_away"] and custody != "integrated":
        raise AuthorityError(f"{acct['account_number_masked']} comes from the integrated custodian feed — its "
                             "custody is integrated")
    resulting = authority_override or (h["authority"] if h else default_for(acct)[0])
    if not valid_setting(resulting, custody):
        raise AuthorityConflict(f"managed needs a custodian: {acct['account_number_masked']} would be managed "
                                "while outside the platform — choose another custodian (settled DVP) with its "
                                "receiving-custodian details, or a different authority")
    if custody == "other_custodian":
        name = (rc.get("custodian_name") or "").strip()
        part = (rc.get("participant_number") or "").strip()
        if not name or not part:
            raise AuthorityError("another custodian needs the receiving custodian's name and its DTC / participant "
                                 "number")
        if not part.isdigit() or len(part) > 5:
            raise AuthorityError("the DTC / participant number is up to five digits")
        fields = (name, part, (rc.get("agent_bank") or "").strip() or None, mask_last4(rc.get("account_last4")),
                  (rc.get("contra_broker") or "").strip() or None)
    else:
        if any((v or "") for v in rc.values()):
            raise AuthorityError("receiving-custodian details belong only to an account at another custodian")
        fields = (None, None, None, None, None)
    rid = uuid.uuid4()
    title = (f"Approve account authority: {acct['account_number_masked']} → "
             f"{AUTHORITY_SHORT[authority_override] if authority_override else 'the household setting'}, "
             f"{CUSTODY_SHORT[custody]}")
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK, subject_type=SUBJECT_ACC, subject_id=rid, maker_id=actor["id"],
        eligible_permission=APPROVE, title=title,
        payload={"account": acct["account_number_masked"], "authority_override": authority_override,
                 "custody": custody, "from": {"authority": acct["authority"], "custody": acct["custody"]},
                 "effective_from": effective_from.isoformat(), "reason": reason.strip()},
        action_path=f"/portfolio/accounts/{acct['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_ACC} (id, org_id, account_id, authority_override, custody,
                            receiving_custodian_name, receiving_participant_number, receiving_agent_bank,
                            receiving_account_masked, contra_broker, effective_from, reason, status, maker_id,
                            approval_task_id)
                        VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'draft', $13::uuid,
                                $14::uuid) RETURNING *""",
                    rid, str(org_id), str(acct["id"]), authority_override, custody, *fields, effective_from,
                    reason.strip(), str(actor["id"]), task["id"])
                await _audit(conn, org_id, actor["id"], "account_requested", rid,
                             {"account_id": str(acct["id"]), "authority_override": authority_override,
                              "custody": custody, "effective_from": effective_from.isoformat(),
                              "task_id": task["id"]})
    except Exception:
        await _withdraw_task(pool, task["id"], "the authority change could not be recorded")
        raise
    await _publish(pool, org_id, "account_requested", rid, {"task_id": task["id"]}, actor["id"])
    return {**change_out(dict(row), SUBJECT_ACC), "task": task}


async def _withdraw_task(pool, task_id, reason: str) -> None:
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        log.info("task %s was not open to withdraw", task_id)


# ── Decisions (through the human task) ──────────────────────────────────────
async def load_change(conn, org_id, change_id) -> tuple[dict, str]:
    cid = _uuid(change_id, "authority change")
    for table, subject in ((T_HH, SUBJECT_HH), (T_ACC, SUBJECT_ACC)):
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1::uuid AND org_id = $2::uuid "
                                  "AND system_to IS NULL", cid, str(org_id))
        if row is not None:
            return dict(row), subject
    raise AuthorityNotFound("authority change not found")


async def _precheck(pool, task, outcome, actor_id):
    """Re-checked BEFORE the task commits (the task stays open on refusal):
    the change is still a draft, and approving it never produces managed
    outside the platform."""
    if outcome != "approve":
        return
    today = date.today()
    async with pool.acquire() as conn:
        row, subject = await load_change(conn, task["org_id"], task["subject_id"])
        if row["status"] != "draft":
            raise HT.HumanTaskConflict(f"the authority change is {row['status']}, not awaiting approval")
        if subject == SUBJECT_HH:
            accts = await household_accounts(conn, task["org_id"], row["household_id"], today)
            bad = _inheritors_outside(accts, row["authority"])
            if bad:
                raise HT.HumanTaskConflict(f"managed needs a custodian: {', '.join(bad)} would inherit managed while "
                                           "outside the platform")
        else:
            acct = await _account(conn, task["org_id"], row["account_id"])
            h = await household_in_effect(conn, task["org_id"], acct.get("household_id"), today)
            resulting = row["authority_override"] or (h["authority"] if h else default_for(acct)[0])
            if not valid_setting(resulting, row["custody"]):
                raise HT.HumanTaskConflict(f"managed needs a custodian: {acct['account_number_masked']} would be "
                                           "managed while outside the platform")


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    table = T_HH if task["subject_type"] == SUBJECT_HH else T_ACC
    key = "household_id" if table == T_HH else "account_id"
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["subject_id"], str(org_id))
            if row is None or row["status"] != "draft":
                return dict(row) if row else None
            checker = uuid.UUID(str(actor_id))
            if checker == row["maker_id"]:
                raise MakerCheckerViolation("maker-checker: the person who asked for an authority change cannot "
                                            "approve it")
            status = "approved" if outcome == "approve" else "rejected"
            if status == "approved":
                prev = await conn.fetchrow(f"SELECT id FROM {table} WHERE {key} = $1 AND status = 'approved' "
                                           "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", row[key])
                if prev:
                    await conn.execute(f"UPDATE {table} SET valid_to = now(), superseded_reason = $2 WHERE id = $1",
                                       prev["id"], f"superseded by authority change {row['id']}")
            await _archive(conn, table, row["id"])
            new = dict(await conn.fetchrow(
                f"UPDATE {table} SET status = $2, approved_by = $3, decided_at = now(), decision_note = $4, "
                "system_from = now() WHERE id = $1 AND system_to IS NULL RETURNING *",
                row["id"], status, checker, note))
            await _audit(conn, org_id, checker, f"{'household' if table == T_HH else 'account'}_{status}", row["id"],
                         {"task_id": task["id"], "note": note})
    withdrawn = []
    if status == "approved":
        withdrawn = await withdraw_forbidden(pool, org_id=org_id, change=new, table=table)
    await _publish(pool, org_id, status, new["id"], {"task_id": task["id"], "withdrawn": withdrawn}, actor_id)
    return {**change_out(new, task["subject_type"]), "withdrawn": withdrawn}


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK, label="Account authority change", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)
HT.register_precheck(TASK, _precheck)


async def decide(pool, *, org_id, actor_id, change_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise AuthorityError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise AuthorityError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row, subject = await load_change(conn, org_id, change_id)
    if row["status"] != "draft":
        raise AuthorityConflict(f"the authority change is {row['status']}, not awaiting approval")
    if actor["id"] == row["maker_id"]:
        raise MakerCheckerViolation("maker-checker: the person who asked for an authority change cannot decide it")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise AuthorityConflict(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            r2, _ = await load_change(conn, org_id, change_id)
        result = change_out(r2, subject)
    return result


# ── Open proposals the new authority forbids are withdrawn ─────────────────
def proposal_path(p: dict) -> str:
    return "one_off" if p.get("proposal_kind") == "one_off" else f"proposal:{p['trigger']}"


async def withdraw_forbidden(pool, *, org_id, change: dict, table: str, today: date | None = None) -> list[dict]:
    """Every open draft / pending proposal on an affected account that the
    authority now in effect would not allow → expired with the plain reason,
    its task withdrawn (rep5's expire path). Never executed, never edited."""
    from services import account_models as AM
    from services import rebalance_proposals as RP

    today = today or date.today()
    async with pool.acquire() as conn:
        if table == T_HH:
            ids = [str(r["id"]) for r in await conn.fetch(
                "SELECT id FROM public.accounts WHERE org_id = $1::uuid AND household_id = $2::uuid "
                "AND system_to IS NULL AND valid_to IS NULL", str(org_id), str(change["household_id"]))]
        else:
            ids = [str(change["account_id"])]
    out = []
    for aid in ids:
        async with RP.account_lock(pool, aid):
            async with pool.acquire() as conn:
                acct = await AM.load_account(conn, org_id, aid)
                props = [dict(r) for r in await conn.fetch(
                    f"SELECT * FROM {RP.T_P} WHERE account_id = $1::uuid AND org_id = $2::uuid "
                    "AND status IN ('draft', 'pending_approval', 'approved') AND system_to IS NULL", aid, str(org_id))]
            for p in props:
                why = refusal(acct, f"the open {p['trigger']} proposal", path=proposal_path(p))
                if not why:
                    continue
                if p["status"] == "approved":
                    out.append({"proposal_id": str(p["id"]), "account": acct["account_number_masked"],
                                "status": "approved", "action": "kept — routing refuses it", "reason": why})
                    continue
                reason = f"withdrawn: the account's authority changed (authority change {change['id']}) — {why}"
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        cur = await RP.load(conn, org_id, p["id"], lock=True)
                        if cur["status"] not in ("draft", "pending_approval"):
                            continue
                        await RP._update(conn, cur["id"], {"status": "expired", "status_reason": reason})
                        await RP._audit(conn, org_id, None, "withdrawn", cur["id"],
                                        {"reason": reason, "authority_change_id": str(change["id"])})
                if p.get("approval_task_id"):
                    await RP._withdraw_task(pool, p["approval_task_id"], reason)
                await RP._publish(pool, org_id, "withdrawn", p["id"], {"reason": reason}, None)
                out.append({"proposal_id": str(p["id"]), "account": acct["account_number_masked"],
                            "status": "expired", "action": "withdrawn", "reason": reason})
    return out


# ── Reads for the panels ────────────────────────────────────────────────────
def change_out(r: dict, subject: str) -> dict:
    base = {"id": str(r["id"]), "kind": "household" if subject == SUBJECT_HH else "account",
            "effective_from": _iso(r["effective_from"]), "reason": r["reason"], "status": r["status"],
            "status_label": STATUS_LABELS[r["status"]], "maker_id": str(r["maker_id"]),
            "approved_by": _s(r["approved_by"]), "approval_task_id": _s(r["approval_task_id"]),
            "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"],
            "superseded": r["valid_to"] is not None, "superseded_reason": r.get("superseded_reason"),
            "created_at": _iso(r["created_at"])}
    if subject == SUBJECT_HH:
        base.update({"household_id": str(r["household_id"]), "authority": r["authority"],
                     "authority_label": AUTHORITY_LABELS[r["authority"]]})
    else:
        base.update({"account_id": str(r["account_id"]), "authority_override": r["authority_override"],
                     "custody": r["custody"], "custody_label": CUSTODY_LABELS[r["custody"]],
                     "receiving": ({"custodian_name": r["receiving_custodian_name"],
                                    "participant_number": r["receiving_participant_number"],
                                    "agent_bank": r["receiving_agent_bank"],
                                    "account_masked": r["receiving_account_masked"],
                                    "contra_broker": r["contra_broker"]} if r["custody"] == "other_custodian" else None)})
    return base


def change_actions(r: dict, *, can_approve: bool, user_id) -> list[str]:
    if r["status"] == "draft" and can_approve and str(r["maker_id"]) != str(user_id):
        return ["approve", "reject"]
    return []


async def history(conn, org_id, table: str, key_col: str, key) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {table} WHERE org_id = $1::uuid AND {key_col} = $2::uuid AND system_to IS NULL "
        "ORDER BY created_at DESC", str(org_id), str(key))]


async def household_panel(conn, org_id, household_id, today: date | None = None) -> dict:
    today = today or date.today()
    hh = await _household(conn, org_id, household_id)
    cur = await household_in_effect(conn, org_id, hh["id"], today)
    rows = await history(conn, org_id, T_HH, "household_id", hh["id"])
    accts = await household_accounts(conn, org_id, hh["id"], today)
    return {"household": {"id": str(hh["id"]), "name": hh["name"]},
            "current": ({"authority": cur["authority"], "authority_label": AUTHORITY_LABELS[cur["authority"]],
                         "effective_from": _iso(cur["effective_from"]), "change_id": str(cur["id"])} if cur else
                        {"authority": None, "authority_label": None, "effective_from": None, "change_id": None,
                         "note": "no household setting — each account takes its default (held-away read-only; "
                                 "others managed)"}),
            "history": [change_out(r, SUBJECT_HH) for r in rows], "raw": rows,
            "pending": next((change_out(r, SUBJECT_HH) for r in rows if r["status"] == "draft"), None),
            "accounts": [{"id": str(a["id"]), "account_number_masked": a["account_number_masked"],
                          "is_held_away": a["is_held_away"], **authority_out(a)} for a in accts]}


async def account_panel(conn, org_id, account_id, today: date | None = None) -> dict:
    today = today or date.today()
    acct = await _account(conn, org_id, account_id)
    await attach(conn, org_id, acct, today)
    rows = await history(conn, org_id, T_ACC, "account_id", acct["id"])
    return {"account": {"id": str(acct["id"]), "account_number_masked": acct["account_number_masked"],
                        "household_id": _s(acct["household_id"]), "is_held_away": acct["is_held_away"],
                        "custodian_code": acct["custodian_code"]},
            "effective": authority_out(acct), "history": [change_out(r, SUBJECT_ACC) for r in rows], "raw": rows,
            "pending": next((change_out(r, SUBJECT_ACC) for r in rows if r["status"] == "draft"), None)}


# ── DVP settlement detail (managed, other custodian) ───────────────────────
async def write_settlement(conn, *, org_id, account: dict, instruction: dict, actor_id, trade_date: date) -> dict:
    """The instruction's manual DVP settlement detail (same transaction as the
    instruction). Refuses when the account's receiving details are missing."""
    rc = account.get("receiving") or {}
    if not rc.get("custodian_name") or not rc.get("participant_number"):
        raise AuthorityConflict(f"{account['account_number_masked']}: record the receiving custodian's name and "
                                "DTC / participant number (an approved custody change) before a DVP settlement "
                                "instruction")
    row = await conn.fetchrow(
        f"""INSERT INTO {T_SET} (org_id, instruction_id, account_id, settings_id, authority, custody, executor_type,
                receiving_custodian_name, receiving_participant_number, receiving_agent_bank,
                receiving_account_masked, contra_broker, trade_date, settlement_date, calendar_basis, created_by)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, 'managed', 'other_custodian', $5, $6, $7, $8, $9, $10,
                    $11, $12, $13, $14::uuid) RETURNING *""",
        str(org_id), str(instruction["id"]), str(account["id"]), account.get("settings_id"),
        instruction["executor_type"], rc["custodian_name"], rc["participant_number"], rc.get("agent_bank"),
        rc.get("account_masked"), rc.get("contra_broker"), trade_date, next_business_day(trade_date),
        CALENDAR_BASIS, str(actor_id))
    return dict(row)


async def settlement_of(conn, org_id, instruction_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE org_id = $1::uuid AND instruction_id = $2::uuid "
                              "AND valid_to IS NULL AND system_to IS NULL", str(org_id), str(instruction_id))
    return dict(row) if row else None


def settlement_out(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "settlement_type": r["settlement_type"], "executor_type": r["executor_type"],
            "receiving_custodian_name": r["receiving_custodian_name"],
            "receiving_participant_number": r["receiving_participant_number"],
            "receiving_agent_bank": r["receiving_agent_bank"],
            "receiving_account_masked": r["receiving_account_masked"], "contra_broker": r["contra_broker"],
            "trade_date": _iso(r["trade_date"]), "settlement_date": _iso(r["settlement_date"]),
            "calendar_basis": r["calendar_basis"], "calendar_note": CALENDAR_NOTE}
