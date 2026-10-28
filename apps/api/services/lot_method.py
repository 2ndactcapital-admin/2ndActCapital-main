"""The LOT-RELIEF METHOD (lotmethod) — which lots a sale relieves, chosen by
the adviser: a household default, an account override, a per-proposal
override. services.rebalance_engine applies it; this module stores it, resolves
the effective setting and builds the engine's :class:`LotMethod`.

EFFECTIVE METHOD = the proposal's override ?? the account's approved override
?? its household's approved default ?? NO METHOD SET. "No method set" is
TODAY'S RULE exactly (losses first, then highest cost; long-term before
short-term at equal cost; then oldest; unknown cost never sold against a
budget) — the migration wrote no rows, so no existing proposal, plan or X-ray
figure changes until an adviser sets one.

tax_optimized, once SET, ranks by estimated tax per dollar raised at the tax
subject's approved rep6 profile's combined rates (tax_profiles.combined_rates);
with no profile it is today's rule and every sale line says so. Never a
guessed rate.

THE CUSTODIAN'S OWN METHOD (``custodian_cost_basis_method`` on the account
setting: as reported by the custodian, or entered by the adviser; 'unknown'
until then). Every Hollisworks sale names its lots (specific identification),
so when the account's method differs from a KNOWN custodian default other than
specific identification, the proposal and every instruction carry a warning
that the named lots must be given to the custodian. 'unknown' warns nothing
(it is not a known difference, and warning on every account would change every
existing proposal's warnings).

AVERAGE COST: the account setting names the securities its custodian carries at
average cost (a mutual-fund / DRIP election — the security master marks no such
thing). The engine makes no lot choice for them.

CHANGES are maker-checker (Task 1b — the same class as rep5's gain budget and
short-term guard, which are approved link versions): a request opens the human
task ``approve_lot_method_change`` (approve_proposals, never the maker). In
effect from approval; a newer approved change closes the previous one
(valid_to). The per-proposal override needs no task of its own — it makes a NEW
proposal (the old draft superseded), and that proposal's own maker-checker
approval is its approval.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal

from services import human_tasks as HT
from services import rebalance_engine as E

log = logging.getLogger(__name__)

VIEW, MANAGE, APPROVE = "view_proposals", "manage_proposals", "approve_proposals"
T_HH = "public.household_lot_method"
T_ACC = "public.account_lot_method_settings"
TASK = "approve_lot_method_change"
SUBJECT_HH = "household_lot_method"
SUBJECT_ACC = "account_lot_method"
EVENT_SOURCE = "lot_method"

METHODS = E.METHODS
PREFERENCES = E.GAIN_PREFERENCES
CUSTODIAN_METHODS = ("unknown", "fifo", "lifo", "hifo", "lowest_cost", "highest_cost", "average_cost", "specific",
                     "tax_optimized")
CUSTODIAN_LABELS = {"unknown": "Not on record", "fifo": "FIFO", "lifo": "LIFO", "hifo": "HIFO",
                    "lowest_cost": "Lowest cost", "highest_cost": "Highest cost", "average_cost": "Average cost",
                    "specific": "Specific identification", "tax_optimized": "The custodian's tax-lot optimizer"}
CUSTODIAN_SOURCES = {"custodian": "As reported by the custodian", "adviser": "Entered by the adviser"}
SOURCE_LABELS = {"proposal": "Chosen on this proposal", "account": "Set on this account",
                 "household": "The household's default", "default": "No method set — today's rule"}
STATUS_LABELS = {"draft": "Awaiting a second person's approval", "approved": "Approved", "rejected": "Rejected"}
MISMATCH_CODE = "lot_method_mismatch"


class LotMethodError(Exception):
    """A request the rules refuse (422)."""


class LotMethodNotFound(LotMethodError, LookupError):
    pass


class LotMethodConflict(LotMethodError):
    """The state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    pass


def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else o.isoformat()
                      if isinstance(o, (date, datetime)) else str(o))


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise LotMethodNotFound(f"{what} not found") from None


def _iso(v):
    return None if v is None else v.isoformat()


def _s(v):
    return None if v is None else str(v)


def vocabularies() -> dict:
    def pairs(keys, labels):
        return [{"key": k, "label": labels[k]} for k in keys]

    return {"methods": pairs(METHODS, E.METHOD_LABELS), "gain_preferences": pairs(PREFERENCES, E.PREFERENCE_LABELS),
            "custodian_methods": pairs(CUSTODIAN_METHODS, CUSTODIAN_LABELS),
            "custodian_sources": [{"key": k, "label": v} for k, v in CUSTODIAN_SOURCES.items()],
            "sources": [{"key": k, "label": v} for k, v in SOURCE_LABELS.items()],
            "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "line_methods": [{"key": k, "label": v} for k, v in E.METHOD_LABELS.items()],
            "today_rule": E.TODAY_RULE,
            "precedence": "wash-sale holds and hard blocks > the short-term guard and the gain budget > the method"}


# ── Reading the effective setting ───────────────────────────────────────────
async def _in_effect(conn, table: str, key_col: str, key, org_id) -> dict | None:
    if not key:
        return None
    row = await conn.fetchrow(
        f"SELECT * FROM {table} WHERE org_id = $1::uuid AND {key_col} = $2::uuid AND status = 'approved' "
        "AND valid_to IS NULL AND system_to IS NULL ORDER BY decided_at DESC LIMIT 1", str(org_id), str(key))
    return dict(row) if row else None


async def household_in_effect(conn, org_id, household_id) -> dict | None:
    return await _in_effect(conn, T_HH, "household_id", household_id, org_id)


async def account_in_effect(conn, org_id, account_id) -> dict | None:
    return await _in_effect(conn, T_ACC, "account_id", account_id, org_id)


def compose(s: dict | None, h: dict | None) -> dict:
    """The effective setting from the account row ``s`` and household row ``h``."""
    if s and s["lot_method_override"]:
        method, source = s["lot_method_override"], "account"
    elif h:
        method, source = h["lot_method"], "household"
    else:
        method, source = E.DEFAULT_METHOD, "default"
    if s and s["gain_preference_override"]:
        pref, target = s["gain_preference_override"], s["realize_gains_target"]
    elif h:
        pref, target = h["gain_preference"], h["realize_gains_target"]
    else:
        pref, target = E.DEFAULT_PREFERENCE, None
    return {"lot_method": method, "lot_method_source": source, "gain_preference": pref,
            "realize_gains_target": None if target is None else Decimal(str(target)),
            "custodian_cost_basis_method": s["custodian_cost_basis_method"] if s else "unknown",
            "custodian_method_source": s["custodian_method_source"] if s else None,
            "average_cost_security_ids": sorted(str(x) for x in (s["average_cost_security_ids"] or [])) if s else [],
            "settings_id": _s(s["id"]) if s else None, "household_setting_id": _s(h["id"]) if h else None}


async def effective(conn, org_id, account: dict) -> dict:
    s = await account_in_effect(conn, org_id, account["id"])
    h = await household_in_effect(conn, org_id, account.get("household_id"))
    return compose(s, h)


def mismatch(eff: dict) -> dict | None:
    """The custodian-mismatch warning, or None. Specific identification at the
    custodian, an unknown custodian method, or the same method → none."""
    c = eff.get("custodian_cost_basis_method") or "unknown"
    m = eff["lot_method"]
    if c in ("unknown", "specific") or c == m:
        return None
    return {"code": MISMATCH_CODE,
            "message": f"the account's lot method ({E.METHOD_LABELS.get(m, m).split(' —')[0]}) differs from the "
                       f"custodian's default ({CUSTODIAN_LABELS[c]}): every sale names its lots — give the custodian "
                       "the specific lots on each instruction, or it relieves lots by its own default",
            "lot_method": m, "custodian_cost_basis_method": c}


async def lot_method_for(conn, org_id, account: dict, today: date, override: dict | None = None
                         ) -> tuple[E.LotMethod, dict]:
    """(the engine's LotMethod, the effective setting + any proposal override).
    Rates are loaded only for a SET tax_optimized method (never for 'no
    method set' — that is today's rule exactly)."""
    from services import tax_profiles as TP

    eff = await effective(conn, org_id, account)
    if override:
        if override.get("lot_method") is not None:
            eff["lot_method"] = override["lot_method"]
            eff["lot_method_source"] = "proposal"
        if override.get("gain_preference") is not None:
            eff["gain_preference"] = override["gain_preference"]
            eff["realize_gains_target"] = override.get("realize_gains_target")
            eff["lot_method_source"] = "proposal"
    eff["lot_picks"] = (override or {}).get("lots") if eff["lot_method"] == "specific" else None
    rates = None
    if eff["lot_method"] == "tax_optimized" and eff["lot_method_source"] != "default":
        rates = TP.combined_rates(await TP.profile_for_account(conn, org_id, account, today.year))
    eff["rates"] = TP.rates_out(rates) if eff["lot_method_source"] != "default" else None
    picks: dict[str, list] = {}
    for p in eff["lot_picks"] or []:
        picks.setdefault(str(p["global_security_id"]), []).append((str(p["lot_id"]), Decimal(str(p["quantity"]))))
    m = E.LotMethod(method=eff["lot_method"], gain_preference=eff["gain_preference"],
                    realize_target=eff["realize_gains_target"], rates=rates, picks=picks,
                    average_cost=frozenset(eff["average_cost_security_ids"]), source=eff["lot_method_source"])
    return m, eff


def clean_override(raw: dict) -> dict:
    """A per-proposal override (method, gain preference, target, picked lots)."""
    out: dict = {}
    method = raw.get("lot_method")
    if method is not None and method not in METHODS:
        raise LotMethodError(f"lot_method is one of {list(METHODS)}")
    pref = raw.get("gain_preference")
    if pref is not None and pref not in PREFERENCES:
        raise LotMethodError(f"gain_preference is one of {list(PREFERENCES)}")
    target = raw.get("realize_gains_target")
    _check_target(pref, target)
    lots = raw.get("lots")
    if lots and method != "specific":
        raise LotMethodError("picked lots go with lot_method 'specific'")
    if method == "specific" and not lots:
        raise LotMethodError("lot_method 'specific' needs the picked lots")
    clean = []
    for x in lots or []:
        q = x.get("quantity")
        if not isinstance(q, Decimal) or q <= 0:
            raise LotMethodError("a picked lot's quantity is a decimal above zero")
        clean.append({"global_security_id": _uuid(x.get("global_security_id"), "security"),
                      "lot_id": _uuid(x.get("lot_id"), "tax lot"), "quantity": q})
    if method is None and pref is None:
        raise LotMethodError("choose a lot method or a gain preference")
    out.update(lot_method=method, gain_preference=pref, realize_gains_target=target, lots=clean or None)
    return out


def _check_target(pref, target) -> None:
    if pref == "realize_gains":
        if not isinstance(target, Decimal) or target <= 0:
            raise LotMethodError("realize_gains needs the target gain the adviser enters (a decimal above zero) — "
                                 "it is never computed from income Hollisworks does not hold")
    elif target is not None:
        raise LotMethodError("a target gain goes only with realize_gains")


def method_out(eff: dict) -> dict:
    m = eff["lot_method"]
    return {"lot_method": m, "lot_method_label": E.METHOD_LABELS.get(m, m),
            "lot_method_source": eff["lot_method_source"],
            "lot_method_source_label": SOURCE_LABELS[eff["lot_method_source"]],
            "gain_preference": eff["gain_preference"],
            "gain_preference_label": E.PREFERENCE_LABELS[eff["gain_preference"]],
            "realize_gains_target": _s(eff["realize_gains_target"]),
            "custodian_cost_basis_method": eff["custodian_cost_basis_method"],
            "custodian_cost_basis_method_label": CUSTODIAN_LABELS[eff["custodian_cost_basis_method"]],
            "custodian_method_source": eff["custodian_method_source"],
            "average_cost_security_ids": eff["average_cost_security_ids"],
            "today_rule": eff["lot_method_source"] == "default", "mismatch": mismatch(eff)}


# ── Change requests ─────────────────────────────────────────────────────────
async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"lot_method.{action}", EVENT_SOURCE, str(resource_id),
        _dumps(payload))


async def _publish(pool, org_id, action: str, resource_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    await publish_event(pool, str(org_id), f"lot_method.{action}", EVENT_SOURCE, str(resource_id),
                        json.loads(_dumps(payload)), created_by=str(actor_id) if actor_id else None)


async def _archive(conn, table: str, row_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {table} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(row_id))
    if got is None:
        raise LotMethodConflict("the change is not live — refusing to change it")


async def _account(conn, org_id, account_id) -> dict:
    row = await conn.fetchrow(
        "SELECT id, org_id, account_number_masked, custodian_code, household_id, is_held_away, tax_status, "
        "registration_type, primary_entity_id FROM public.accounts WHERE id = $1::uuid AND org_id = $2::uuid "
        "AND system_to IS NULL AND valid_to IS NULL", _uuid(account_id, "account"), str(org_id))
    if row is None:
        raise LotMethodNotFound("account not found")
    return dict(row)


async def _household(conn, org_id, household_id) -> dict:
    row = await conn.fetchrow("SELECT id, org_id, name FROM public.households WHERE id = $1::uuid AND org_id = $2::uuid",
                              _uuid(household_id, "household"), str(org_id))
    if row is None:
        raise LotMethodNotFound("household not found")
    return dict(row)


async def _withdraw_task(pool, task_id, reason: str) -> None:
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        log.info("task %s was not open to withdraw", task_id)


async def request_household_change(pool, *, org_id, actor_id, household_id, lot_method: str, gain_preference: str,
                                   realize_gains_target: Decimal | None, reason: str) -> dict:
    if lot_method not in METHODS:
        raise LotMethodError(f"lot_method is one of {list(METHODS)}")
    if gain_preference not in PREFERENCES:
        raise LotMethodError(f"gain_preference is one of {list(PREFERENCES)}")
    _check_target(gain_preference, realize_gains_target)
    if not (reason or "").strip():
        raise LotMethodError("a lot-method change needs a reason")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        hh = await _household(conn, org_id, household_id)
        if await conn.fetchval(f"SELECT 1 FROM {T_HH} WHERE household_id = $1 AND status = 'draft' "
                               "AND system_to IS NULL", hh["id"]):
            raise LotMethodConflict("a lot-method change for this household is already awaiting approval — decide "
                                    "it first")
    rid = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK, subject_type=SUBJECT_HH, subject_id=rid, maker_id=actor["id"],
        eligible_permission=APPROVE,
        title=f"Approve household lot method: {hh['name']} → {E.METHOD_LABELS[lot_method].split(' —')[0]}",
        payload={"household": hh["name"], "lot_method": lot_method, "gain_preference": gain_preference,
                 "realize_gains_target": _s(realize_gains_target), "reason": reason.strip()},
        action_path=f"/households/{hh['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_HH} (id, org_id, household_id, lot_method, gain_preference,
                            realize_gains_target, reason, status, maker_id, approval_task_id)
                        VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, 'draft', $8::uuid, $9::uuid) RETURNING *""",
                    rid, str(org_id), str(hh["id"]), lot_method, gain_preference, realize_gains_target,
                    reason.strip(), str(actor["id"]), task["id"])
                await _audit(conn, org_id, actor["id"], "household_requested", rid,
                             {"household_id": str(hh["id"]), "lot_method": lot_method,
                              "gain_preference": gain_preference, "task_id": task["id"]})
    except Exception:
        await _withdraw_task(pool, task["id"], "the lot-method change could not be recorded")
        raise
    await _publish(pool, org_id, "household_requested", rid, {"task_id": task["id"]}, actor["id"])
    return {**change_out(dict(row), SUBJECT_HH), "task": task}


async def request_account_change(pool, *, org_id, actor_id, account_id, lot_method_override: str | None,
                                 gain_preference_override: str | None, realize_gains_target: Decimal | None,
                                 custodian_cost_basis_method: str, custodian_method_source: str | None,
                                 average_cost_security_ids: list | None, reason: str) -> dict:
    if lot_method_override is not None and lot_method_override not in METHODS:
        raise LotMethodError(f"lot_method_override is one of {list(METHODS)} (or none, to follow the household)")
    if gain_preference_override is not None and gain_preference_override not in PREFERENCES:
        raise LotMethodError(f"gain_preference_override is one of {list(PREFERENCES)} (or none)")
    _check_target(gain_preference_override, realize_gains_target)
    if custodian_cost_basis_method not in CUSTODIAN_METHODS:
        raise LotMethodError(f"custodian_cost_basis_method is one of {list(CUSTODIAN_METHODS)}")
    if (custodian_cost_basis_method == "unknown") != (custodian_method_source is None):
        raise LotMethodError("a known custodian method says where it came from (custodian | adviser); 'unknown' "
                             "has no source")
    if custodian_method_source is not None and custodian_method_source not in CUSTODIAN_SOURCES:
        raise LotMethodError(f"custodian_method_source is one of {list(CUSTODIAN_SOURCES)}")
    if not (reason or "").strip():
        raise LotMethodError("a lot-method change needs a reason")
    avg = sorted({_uuid(x, "security") for x in (average_cost_security_ids or [])})
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        acct = await _account(conn, org_id, account_id)
        if await conn.fetchval(f"SELECT 1 FROM {T_ACC} WHERE account_id = $1 AND status = 'draft' "
                               "AND system_to IS NULL", acct["id"]):
            raise LotMethodConflict("a lot-method change for this account is already awaiting approval — decide "
                                    "it first")
        if avg:
            known = {str(r["id"]) for r in await conn.fetch(
                "SELECT id FROM portfolio.securities_global WHERE id = ANY($1::uuid[]) AND system_to IS NULL "
                "AND valid_to IS NULL", avg)}
            if set(avg) - known:
                raise LotMethodError(f"not in the security master: {sorted(set(avg) - known)}")
    rid = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK, subject_type=SUBJECT_ACC, subject_id=rid, maker_id=actor["id"],
        eligible_permission=APPROVE,
        title=f"Approve account lot method: {acct['account_number_masked']} → "
              f"{E.METHOD_LABELS[lot_method_override].split(' —')[0] if lot_method_override else 'the household'}",
        payload={"account": acct["account_number_masked"], "lot_method_override": lot_method_override,
                 "gain_preference_override": gain_preference_override,
                 "custodian_cost_basis_method": custodian_cost_basis_method, "average_cost": len(avg),
                 "reason": reason.strip()},
        action_path=f"/portfolio/accounts/{acct['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_ACC} (id, org_id, account_id, lot_method_override, gain_preference_override,
                            realize_gains_target, custodian_cost_basis_method, custodian_method_source,
                            average_cost_security_ids, reason, status, maker_id, approval_task_id)
                        VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9::uuid[], $10, 'draft', $11::uuid,
                                $12::uuid) RETURNING *""",
                    rid, str(org_id), str(acct["id"]), lot_method_override, gain_preference_override,
                    realize_gains_target, custodian_cost_basis_method, custodian_method_source, avg, reason.strip(),
                    str(actor["id"]), task["id"])
                await _audit(conn, org_id, actor["id"], "account_requested", rid,
                             {"account_id": str(acct["id"]), "lot_method_override": lot_method_override,
                              "custodian_cost_basis_method": custodian_cost_basis_method, "task_id": task["id"]})
    except Exception:
        await _withdraw_task(pool, task["id"], "the lot-method change could not be recorded")
        raise
    await _publish(pool, org_id, "account_requested", rid, {"task_id": task["id"]}, actor["id"])
    return {**change_out(dict(row), SUBJECT_ACC), "task": task}


# ── Decisions (through the human task) ──────────────────────────────────────
async def load_change(conn, org_id, change_id) -> tuple[dict, str]:
    cid = _uuid(change_id, "lot-method change")
    for table, subject in ((T_HH, SUBJECT_HH), (T_ACC, SUBJECT_ACC)):
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1::uuid AND org_id = $2::uuid "
                                  "AND system_to IS NULL", cid, str(org_id))
        if row is not None:
            return dict(row), subject
    raise LotMethodNotFound("lot-method change not found")


async def _precheck(pool, task, outcome, actor_id):
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        row, _ = await load_change(conn, task["org_id"], task["subject_id"])
    if row["status"] != "draft":
        raise HT.HumanTaskConflict(f"the lot-method change is {row['status']}, not awaiting approval")


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
                raise MakerCheckerViolation("maker-checker: the person who asked for a lot-method change cannot "
                                            "approve it")
            status = "approved" if outcome == "approve" else "rejected"
            if status == "approved":
                prev = await conn.fetchrow(f"SELECT id FROM {table} WHERE {key} = $1 AND status = 'approved' "
                                           "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", row[key])
                if prev:
                    await conn.execute(f"UPDATE {table} SET valid_to = now(), superseded_reason = $2 WHERE id = $1",
                                       prev["id"], f"superseded by lot-method change {row['id']}")
            await _archive(conn, table, row["id"])
            new = dict(await conn.fetchrow(
                f"UPDATE {table} SET status = $2, approved_by = $3, decided_at = now(), decision_note = $4, "
                "system_from = now() WHERE id = $1 AND system_to IS NULL RETURNING *",
                row["id"], status, checker, note))
            await _audit(conn, org_id, checker, f"{'household' if table == T_HH else 'account'}_{status}", row["id"],
                         {"task_id": task["id"], "note": note})
    await _publish(pool, org_id, status, new["id"], {"task_id": task["id"]}, actor_id)
    return change_out(new, task["subject_type"])


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK, label="Lot-method change", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)
HT.register_precheck(TASK, _precheck)


async def decide(pool, *, org_id, actor_id, change_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise LotMethodError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise LotMethodError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row, subject = await load_change(conn, org_id, change_id)
    if row["status"] != "draft":
        raise LotMethodConflict(f"the lot-method change is {row['status']}, not awaiting approval")
    if actor["id"] == row["maker_id"]:
        raise MakerCheckerViolation("maker-checker: the person who asked for a lot-method change cannot decide it")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise LotMethodConflict(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            r2, _ = await load_change(conn, org_id, change_id)
        result = change_out(r2, subject)
    return result


# ── Out ────────────────────────────────────────────────────────────────────
def change_out(r: dict, subject: str) -> dict:
    base = {"id": str(r["id"]), "kind": "household" if subject == SUBJECT_HH else "account", "reason": r["reason"],
            "status": r["status"], "status_label": STATUS_LABELS[r["status"]], "maker_id": str(r["maker_id"]),
            "approved_by": _s(r["approved_by"]), "approval_task_id": _s(r["approval_task_id"]),
            "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"],
            "superseded": r["valid_to"] is not None, "superseded_reason": r.get("superseded_reason"),
            "created_at": _iso(r["created_at"]), "realize_gains_target": _s(r["realize_gains_target"])}
    if subject == SUBJECT_HH:
        base.update({"household_id": str(r["household_id"]), "lot_method": r["lot_method"],
                     "lot_method_label": E.METHOD_LABELS[r["lot_method"]], "gain_preference": r["gain_preference"],
                     "gain_preference_label": E.PREFERENCE_LABELS[r["gain_preference"]]})
    else:
        base.update({"account_id": str(r["account_id"]), "lot_method_override": r["lot_method_override"],
                     "gain_preference_override": r["gain_preference_override"],
                     "custodian_cost_basis_method": r["custodian_cost_basis_method"],
                     "custodian_cost_basis_method_label": CUSTODIAN_LABELS[r["custodian_cost_basis_method"]],
                     "custodian_method_source": r["custodian_method_source"],
                     "average_cost_security_ids": sorted(str(x) for x in (r["average_cost_security_ids"] or []))})
    return base


def change_actions(r: dict, *, can_approve: bool, user_id) -> list[str]:
    if r["status"] == "draft" and can_approve and str(r["maker_id"]) != str(user_id):
        return ["approve", "reject"]
    return []


async def history(conn, org_id, table: str, key_col: str, key) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {table} WHERE org_id = $1::uuid AND {key_col} = $2::uuid AND system_to IS NULL "
        "ORDER BY created_at DESC", str(org_id), str(key))]


async def household_panel(conn, org_id, household_id) -> dict:
    hh = await _household(conn, org_id, household_id)
    cur = await household_in_effect(conn, org_id, hh["id"])
    rows = await history(conn, org_id, T_HH, "household_id", hh["id"])
    return {"household": {"id": str(hh["id"]), "name": hh["name"]},
            "current": method_out(compose(None, cur)), "current_change_id": _s(cur["id"]) if cur else None,
            "history": [change_out(r, SUBJECT_HH) for r in rows], "raw": rows,
            "pending": next((change_out(r, SUBJECT_HH) for r in rows if r["status"] == "draft"), None)}


async def account_panel(conn, org_id, account_id) -> dict:
    acct = await _account(conn, org_id, account_id)
    eff = await effective(conn, org_id, acct)
    rows = await history(conn, org_id, T_ACC, "account_id", acct["id"])
    return {"account": {"id": str(acct["id"]), "account_number_masked": acct["account_number_masked"],
                        "household_id": _s(acct["household_id"]), "custodian_code": acct["custodian_code"]},
            "effective": method_out(eff), "history": [change_out(r, SUBJECT_ACC) for r in rows], "raw": rows,
            "pending": next((change_out(r, SUBJECT_ACC) for r in rows if r["status"] == "draft"), None)}


async def lot_options(conn, org_id, proposal: dict, trades: list[dict]) -> list[dict]:
    """The lot picker: each SELL line's open lots of this account (cost, term
    on today, acquired, quantity, est. gain at the line's price)."""
    from services import execution as X

    out = []
    for t in trades:
        if t["side"] != "sell":
            continue
        lots = await X.account_lots(conn, org_id, proposal["account_id"], str(t["global_security_id"]))
        price = None if t["price"] is None else Decimal(str(t["price"]))
        rows = []
        for lot in lots:
            q = E.d(lot["quantity"])
            if q is None or q <= 0:
                continue
            cost = E.d(lot["adjusted_cost"]) if lot["adjusted_cost"] is not None else E.d(lot["original_cost"])
            unit = None if cost is None else cost / q
            acq = date.fromisoformat(lot["acquired_date"]) if lot["acquired_date"] else None
            term = E.term_of(E.Lot(lot["id"], q, unit, acq, lot["term"]), date.today())
            rows.append({"lot_id": str(lot["id"]), "quantity": str(q),
                         "unit_cost": None if unit is None else str(unit.quantize(E.QTY)), "term": term,
                         "acquired_date": lot["acquired_date"],
                         "est_gain": None if unit is None or price is None else str(((price - unit) * q).quantize(
                             E.CENT))})
        out.append({"global_security_id": str(t["global_security_id"]), "label": t.get("security_name"),
                    "line_quantity": None if t["quantity"] is None else str(t["quantity"]), "lots": rows})
    return out
