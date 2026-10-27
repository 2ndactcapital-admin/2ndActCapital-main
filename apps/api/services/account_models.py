"""Accounts join models (rep5) — the link, its personal settings, and its fees.

A new account JOINS an existing approved model from rep3's one registry (it
never forks one, and nothing here creates a model). Everything personal lives
on the ACCOUNT's link, not on the model: exclusions (each with a reason), the
exclusion policy, the cash floor, a drift-threshold and rebalance-frequency
override, the gain budget, the short-term guard and the harvesting-engine
switch (rep6 reads it), the harvest settings (rep6: ``harvest_enabled`` —
OPT-IN, default false; loss % and $ thresholds, harvest to cash, swap back —
NULL = the org default), and a fee-schedule override.

VERSIONS AND APPROVAL
──────────────────────────────────────────────────────────────────────────────
Every change — join, settings, change of model, end — is a NEW version row
(``public.account_model_links``: status 'pending', its own id, version_no + 1
in the link's ``link_group_id``) and a maker-checker human task
(``account_model_link_approval``, decided by a holder of approve_proposals who
is not the maker; the CHECK ``account_model_links_maker_checker_chk`` backs it).
Approval, in ONE transaction: the version becomes 'active' (system-axis
archive — the id is kept), the version it replaces is closed ('superseded' /
'ended', valid_to), and the fee rows it proposed are written through the fee
module's own services. After commit a join or change of model generates the
transition PROPOSAL (services.rebalance_proposals, trigger 'join'). Reject
needs a note. One active link and one pending change per account (partial
unique indexes).

THE ACCOUNT
──────────────────────────────────────────────────────────────────────────────
A closed account is refused. A NON-discretionary account may join, but every
proposal for it is "advice only" and its trades go to the member as
instructions (executor 'member'). A held-away account may join (its trades are
always manual execution instructions — rep2's route_trade_list).

FEES — through services.fee_schedules, never a direct write
──────────────────────────────────────────────────────────────────────────────
The approval writes (1) the ACCOUNT-scope assignment of the model's default
fee schedule (portfolio.models.default_fee_schedule_id) or the link's override
(fee_schedules.create_assignment — precedence derived, the incumbent replaced,
a RETIRED schedule refused), and (2) while the account still holds the
replicated fund the model is built from, a SECURITY exclusion per held asset
of that fund (fee_schedules.create_exclusion — validate_exclusion's rules), so
the fund's own fee and the model's are not both charged. The fee module gives
neither an approval of its own (Task 1e): the join task that proposed them —
whose payload names them — is their approval. Ending the link ends both.

THE CUSTODY CLIFF. Nothing here trades or moves money.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from services import human_tasks as HT

log = logging.getLogger(__name__)

T_LINK = "public.account_model_links"
VIEW = "view_proposals"
MANAGE = "manage_proposals"
APPROVE = "approve_proposals"
TASK_LINK = "account_model_link_approval"
SUBJECT_LINK = "account_model_link"
EVENT_SOURCE = "account_model_link"

LINK_STATUS_LABELS = {"pending": "Awaiting approval", "active": "Active", "ended": "Ended", "rejected": "Rejected",
                      "superseded": "Replaced by a newer version"}
CHANGE_LABELS = {"join": "Join a model", "settings": "Change settings", "change_model": "Change model",
                 "end": "Leave the model"}
EXCLUSION_POLICY_LABELS = {"pro_rata": "Spread its weight pro rata", "to_cash": "Hold its weight as cash",
                           "use_alternates": "Use the model's alternate (else pro rata)"}
HARVESTING_ENGINE_LABELS = {"hollisworks": "Hollisworks", "custodian": "The custodian's own harvesting"}
FREQUENCY_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly", "quarterly": "Quarterly",
                    "semiannually": "Twice a year", "annually": "Once a year"}
SETTING_FIELDS = ("cash_floor", "drift_threshold", "rebalance_frequency", "exclusions", "exclusion_policy",
                  "gain_budget", "avoid_short_term", "short_term_wait_days", "harvesting_engine",
                  "fee_schedule_override_id", "harvest_enabled", "harvest_loss_pct", "harvest_loss_min",
                  "harvest_to_cash", "harvest_swap_back")
DEFAULT_SETTINGS = {"cash_floor": Decimal(0), "drift_threshold": None, "rebalance_frequency": None, "exclusions": [],
                    "exclusion_policy": "pro_rata", "gain_budget": None, "avoid_short_term": True,
                    "short_term_wait_days": 30, "harvesting_engine": "hollisworks",
                    "fee_schedule_override_id": None, "harvest_enabled": False, "harvest_loss_pct": None,
                    "harvest_loss_min": None,
                    "harvest_to_cash": None, "harvest_swap_back": None}


class AccountModelError(ValueError):
    """A request the rules refuse (422)."""


class AccountModelNotFound(LookupError):
    pass


class AccountModelConflict(RuntimeError):
    """The account's state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    pass


MAKER_CHECKER_RULE = ("Maker-checker: you requested this change, so a different holder of approve_proposals must "
                      "approve or reject it")


def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else o.isoformat()
                      if isinstance(o, (date, datetime)) else str(o))


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise AccountModelNotFound(f"{what} not found") from None


def _num(v):
    return None if v is None else str(v)


def _iso(v):
    return v.isoformat() if v else None


# ── Reading ─────────────────────────────────────────────────────────────────
async def load_account(conn, org_id, account_id) -> dict:
    row = await conn.fetchrow(
        "SELECT id, org_id, account_number_masked, custodian_code, registration_type, tax_status, primary_entity_id, "
        "household_id, is_billable, is_discretionary, is_held_away, opened_on, closed_on, base_currency "
        "FROM public.accounts WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL AND valid_to IS NULL",
        _uuid(account_id, "account"), str(org_id))
    if row is None:
        raise AccountModelNotFound("account not found")
    return dict(row)


def is_closed(account: dict, today: date | None = None) -> bool:
    c = account.get("closed_on")
    return c is not None and c <= (today or date.today())


async def link_row(conn, org_id, link_id, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_LINK} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), _uuid(link_id, "link"), str(org_id))
    if row is None:
        raise AccountModelNotFound("account model link not found")
    return dict(row)


async def active_link(conn, account_id, *, lock: bool = False) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_LINK} WHERE account_id = $1::uuid AND status = 'active' "
                              "AND valid_to IS NULL AND system_to IS NULL" + (" FOR UPDATE" if lock else ""),
                              str(account_id))
    return dict(row) if row else None


async def pending_link(conn, account_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_LINK} WHERE account_id = $1::uuid AND status = 'pending' "
                              "AND valid_to IS NULL AND system_to IS NULL", str(account_id))
    return dict(row) if row else None


def settings_of(link: dict | None) -> dict:
    if not link:
        return dict(DEFAULT_SETTINGS)
    out = {k: link.get(k) for k in SETTING_FIELDS}
    out["exclusions"] = _loads(link["exclusions"]) or []
    return out


async def model_brief(conn, org_id, model_id) -> dict:
    from services import models as M

    row = await conn.fetchrow(
        f"SELECT id, name, source, status, default_fee_schedule_id, replica_security_id FROM {M.T_MODEL} "
        "WHERE id = $1::uuid AND org_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL",
        _uuid(model_id, "model"), str(org_id))
    if row is None:
        raise AccountModelNotFound("model not found")
    live = await M.live_approved(conn, row["id"])
    return {**dict(row), "live_version_id": live["id"] if live else None,
            "live_version_no": live["version_no"] if live else None}


async def registry_offer(conn, org_id) -> list[dict]:
    """The models an account may join: existing, active, with an approved
    version (rep3's registry). Nothing here builds one."""
    from services import models as M

    return [{"id": m["id"], "name": m["name"], "source": m["source"], "live_version_no": m["live_version_no"],
             "approved_at": m["approved_at"]}
            for m in await M.list_models(conn, org_id) if m["status"] == "active" and m["live_version_no"]]


async def harvesting_options(conn, account: dict) -> list[dict]:
    """'hollisworks' always; 'custodian' only when that custodian's
    custodian_tax_loss_harvesting is evidenced 'yes' ('unknown' = not available)."""
    from services.custodian_capabilities import available, capability
    from services.custody.registry import trade_custodian_key

    cap = await capability(conn, trade_custodian_key(account.get("custodian_code")) or "manual",
                           "custodian_tax_loss_harvesting")
    out = [{"key": "hollisworks", "label": HARVESTING_ENGINE_LABELS["hollisworks"]}]
    if available(cap) and not account.get("is_held_away"):
        out.append({"key": "custodian", "label": HARVESTING_ENGINE_LABELS["custodian"],
                    "evidence": cap.evidence, "as_of": _iso(cap.as_of)})
    return out


# ── Holdings (the account's own custodian valuation) ────────────────────────
_POS_SQL = """
SELECT DISTINCT ON (p.asset_id) p.id, p.asset_id, p.as_of_date, p.quantity, p.market_value, p.cost_basis,
       a.global_security_id, a.asset_type, a.name AS asset_name, g.name AS security_name,
       (SELECT id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = g.id
         AND t.id_type = 'ticker' AND t.system_to IS NULL AND t.valid_to IS NULL LIMIT 1) AS ticker
FROM portfolio.positions p
JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
LEFT JOIN portfolio.securities_global g ON g.id = a.global_security_id AND g.system_to IS NULL
WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid AND p.valid_to IS NULL AND p.system_to IS NULL
ORDER BY p.asset_id, p.as_of_date DESC, p.system_from DESC
"""


def label_for(name: str | None, ticker: str | None) -> str:
    if ticker and name:
        return f"{name} ({ticker})"
    return name or ticker or "?"


async def valuation_as_of(conn, org_id, account_id) -> date | None:
    """THE account's current valuation as-of — the ONE definition: the latest
    ``as_of_date`` of its live position rows, a row at zero quantity included
    (a custodian snapshot that says "none held" is still a valuation on that
    date). Every proposal stamps it (via :func:`load_holdings`) and rep5's
    expiry compares against it, so a proposal is never born expired."""
    return await conn.fetchval(
        "SELECT max(as_of_date) FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid "
        "AND valid_to IS NULL AND system_to IS NULL", str(org_id), str(account_id))


async def load_holdings(conn, org_id, account_id) -> dict:
    """What the account holds now, valued by ITS OWN positions.

    "Current" = each asset's latest live position row (``as_of_date`` desc);
    the valuation as-of is :func:`valuation_as_of`. A position's price is
    ``market_value / quantity`` on its as_of_date — the custodian's valuation,
    used as that security's price for THIS account only (source
    'account_position'). A position without both is "cannot value". Cash is
    the latest ``account_balances_daily.cash_value`` (one row: latest date,
    final first, billing source first — never a sum across source systems),
    plus any position whose asset_type is 'cash'."""
    from services import execution as X
    from services import rebalance_engine as E

    rows = [dict(r) for r in await conn.fetch(_POS_SQL, str(org_id), str(account_id))]
    lots = await X.account_lots(conn, org_id, account_id)
    by_sec: dict[str, list] = {}
    for lot in lots:
        by_sec.setdefault(lot["global_security_id"], []).append(lot)
    holdings: dict[str, E.Holding] = {}
    unresolved: list[dict] = []
    cash_from_positions = Decimal(0)
    for r in rows:
        qty = E.d(r["quantity"])
        if qty is None or qty == 0:
            continue
        if (r["asset_type"] or "").lower() == "cash":
            cash_from_positions += E.d(r["market_value"]) or Decimal(0)
            continue
        if r["global_security_id"] is None:
            unresolved.append({"asset_id": str(r["asset_id"]), "label": r["asset_name"], "quantity": qty})
            continue
        key = str(r["global_security_id"])
        mv = E.d(r["market_value"])
        h = holdings.get(key)
        if h is None:
            h = holdings[key] = E.Holding(key=key, label=label_for(r["security_name"] or r["asset_name"], r["ticker"]),
                                          quantity=Decimal(0), market_value=Decimal(0), price=None,
                                          price_as_of=r["as_of_date"], price_source="none")
        h.quantity += qty
        h.asset_ids.append(str(r["asset_id"]))
        if mv is None or h.market_value is None:
            h.market_value = None
        else:
            h.market_value += mv
        h.price_as_of = min(h.price_as_of, r["as_of_date"]) if h.price_as_of else r["as_of_date"]
    for key, h in holdings.items():
        if h.market_value is not None and h.market_value > 0 and h.quantity > 0:
            h.price = (h.market_value / h.quantity).quantize(E.QTY)
            h.price_source = "account_position"
        else:
            h.market_value = None
        for lot in by_sec.get(key, []):
            q = E.d(lot["quantity"])
            if q is None or q <= 0:
                continue
            cost = E.d(lot["adjusted_cost"]) if lot["adjusted_cost"] is not None else E.d(lot["original_cost"])
            h.lots.append(E.Lot(id=lot["id"], quantity=q, unit_cost=None if cost is None else cost / q,
                                acquired=date.fromisoformat(lot["acquired_date"]) if lot["acquired_date"] else None,
                                term_hint=lot["term"]))
        if not h.lots and h.price is not None:
            h.lots.append(E.Lot(id=None, quantity=h.quantity, unit_cost=None, acquired=None, term_hint=None))
    bal = await conn.fetchrow(
        "SELECT as_of_date, cash_value, total_market_value, source_system FROM public.account_balances_daily "
        "WHERE org_id = $1::uuid AND account_id = $2::uuid "
        "ORDER BY as_of_date DESC, is_final DESC, is_billing_source DESC, source_system LIMIT 1",
        str(org_id), str(account_id))
    cash = (E.d(bal["cash_value"]) if bal else Decimal(0)) + cash_from_positions
    return {"holdings": list(holdings.values()), "unresolved": unresolved, "cash": cash,
            "cash_as_of": bal["as_of_date"] if bal else None, "cash_source": bal["source_system"] if bal else None,
            "valuation_as_of": await valuation_as_of(conn, org_id, account_id)}


async def fund_security_ids(conn, model: dict) -> list[str]:
    """The security the model REPLACES: the replicated fund a rep4 model is
    built from (portfolio.replica_securities.global_security_id)."""
    if not model.get("replica_security_id"):
        return []
    g = await conn.fetchval("SELECT global_security_id FROM portfolio.replica_securities WHERE id = $1::uuid "
                            "AND system_to IS NULL ORDER BY valid_to NULLS FIRST LIMIT 1",
                            str(model["replica_security_id"]))
    return [str(g)] if g else []


# ── Settings ────────────────────────────────────────────────────────────────
async def clean_settings(conn, org_id, account: dict, raw: dict, base: dict) -> dict:
    """Validate a settings change against the account and merge it onto
    ``base``. Decimal only (the API refuses binary floats)."""
    s = dict(base)
    for k, v in raw.items():
        if k not in SETTING_FIELDS:
            raise AccountModelError(f"unknown setting {k!r}")
        s[k] = v

    def dec(name, lo=None, hi=None, lo_open=False, hi_open=False, nullable=True):
        v = s.get(name)
        if v is None:
            if not nullable:
                raise AccountModelError(f"{name} is required")
            return None
        if not isinstance(v, Decimal):
            raise AccountModelError(f"{name} must be a decimal")
        if lo is not None and (v < lo or (lo_open and v == lo)):
            raise AccountModelError(f"{name} must be {'above' if lo_open else 'at least'} {lo}")
        if hi is not None and (v > hi or (hi_open and v == hi)):
            raise AccountModelError(f"{name} must be {'below' if hi_open else 'at most'} {hi}")
        return v

    s["cash_floor"] = dec("cash_floor", Decimal(0), Decimal(1), hi_open=True, nullable=False)
    s["drift_threshold"] = dec("drift_threshold", Decimal(0), Decimal(1), lo_open=True)
    s["gain_budget"] = dec("gain_budget", Decimal(0))
    if s["gain_budget"] is not None:
        s["gain_budget"] = s["gain_budget"].quantize(Decimal("0.01"))
    s["harvest_loss_pct"] = dec("harvest_loss_pct", Decimal(0), Decimal(1), lo_open=True, hi_open=True)
    s["harvest_loss_min"] = dec("harvest_loss_min", Decimal(0))
    if s["harvest_loss_min"] is not None:
        s["harvest_loss_min"] = s["harvest_loss_min"].quantize(Decimal("0.01"))
    if s.get("harvest_enabled") is None:
        s["harvest_enabled"] = False          # a link from before the setting existed: off
    if not isinstance(s["harvest_enabled"], bool):
        raise AccountModelError("harvest_enabled is true or false")
    for b in ("harvest_to_cash", "harvest_swap_back"):
        if s.get(b) is not None and not isinstance(s[b], bool):
            raise AccountModelError(f"{b} is true, false or empty (the org default)")
    if s.get("rebalance_frequency") is not None and s["rebalance_frequency"] not in FREQUENCY_LABELS:
        raise AccountModelError(f"rebalance frequency is one of {list(FREQUENCY_LABELS)}")
    if s.get("exclusion_policy") not in EXCLUSION_POLICY_LABELS:
        raise AccountModelError(f"exclusion policy is one of {list(EXCLUSION_POLICY_LABELS)}")
    if not isinstance(s.get("avoid_short_term"), bool):
        raise AccountModelError("avoid_short_term is true or false")
    w = s.get("short_term_wait_days")
    if not isinstance(w, int) or isinstance(w, bool) or not 0 <= w <= 366:
        raise AccountModelError("short_term_wait_days is a whole number of days from 0 to 366")
    if s.get("harvesting_engine") not in HARVESTING_ENGINE_LABELS:
        raise AccountModelError(f"harvesting engine is one of {list(HARVESTING_ENGINE_LABELS)}")
    if s["harvesting_engine"] == "custodian":
        allowed = {o["key"] for o in await harvesting_options(conn, account)}
        if "custodian" not in allowed:
            raise AccountModelError("the custodian's own tax-loss harvesting is not evidenced for this account's "
                                    "custodian (custodian_tax_loss_harvesting is not 'yes') — choose Hollisworks")
    ex_out, seen = [], set()
    for e in s.get("exclusions") or []:
        gid = _uuid(e.get("global_security_id"), "excluded security")
        reason = (e.get("reason") or "").strip()
        if not reason:
            raise AccountModelError("every exclusion needs a reason")
        if gid in seen:
            raise AccountModelError("a security is excluded twice")
        seen.add(gid)
        if not await conn.fetchval("SELECT 1 FROM portfolio.securities_global WHERE id = $1::uuid "
                                   "AND system_to IS NULL", gid):
            raise AccountModelError(f"excluded security {gid} is not in the security master")
        ex_out.append({"global_security_id": gid, "reason": reason[:500]})
    s["exclusions"] = ex_out
    fo = s.get("fee_schedule_override_id")
    if fo is not None:
        fo = _uuid(fo, "fee schedule")
        st = await conn.fetchval("SELECT status FROM public.fee_schedules WHERE id = $1::uuid AND org_id = $2::uuid "
                                 "AND system_to IS NULL", fo, str(org_id))
        if st is None:
            raise AccountModelError("the fee schedule override is not one of this organization's schedules")
        if st != "APPROVED":
            raise AccountModelError(f"the fee schedule override is {st}; only an APPROVED schedule is assigned")
        s["fee_schedule_override_id"] = fo
    return s


async def proposed_fee(conn, org_id, account: dict, model: dict, settings: dict, holdings: dict | None = None) -> dict:
    """What the approval will write through the fee module."""
    sched_id = settings.get("fee_schedule_override_id") or (
        str(model["default_fee_schedule_id"]) if model.get("default_fee_schedule_id") else None)
    source = "override" if settings.get("fee_schedule_override_id") else ("model_default" if sched_id else None)
    sched = None
    if sched_id:
        sched = await conn.fetchrow("SELECT id, code, version, name, status FROM public.fee_schedules "
                                    "WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                                    sched_id, str(org_id))
    funds = await fund_security_ids(conn, model)
    hold = holdings or await load_holdings(conn, org_id, account["id"])
    excl = []
    for h in hold["holdings"]:
        if h.key in funds:
            for aid in h.asset_ids:
                excl.append({"asset_id": aid, "global_security_id": h.key, "label": h.label,
                             "reason": f"{h.label} is the fund {model['name']} replicates — while it is still held "
                                       "it is not billed again under the model's fee"})
    return {"schedule_id": str(sched["id"]) if sched else None,
            "schedule": f"{sched['code']} v{sched['version']} — {sched['name']}" if sched else None,
            "schedule_status": sched["status"] if sched else None, "source": source, "exclusions": excl,
            "note": None if sched else "the model has no default fee schedule and no override is set — no fee "
                                       "assignment is proposed"}


# ── Writing primitives ──────────────────────────────────────────────────────
async def _archive(conn, link_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {T_LINK}
            SELECT (jsonb_populate_record(NULL::{T_LINK}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {T_LINK} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(link_id))
    if got is None:
        raise AccountModelConflict("the link is not live — refusing to change it")


async def _update(conn, link_id, fields: dict) -> dict:
    await _archive(conn, link_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    row = await conn.fetchrow(f"UPDATE {T_LINK} SET {sets}, system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(link_id), *[fields[c] for c in cols])
    if row is None:
        raise AccountModelConflict("the link changed underneath this request — reload and retry")
    return dict(row)


async def _audit(conn, org_id, user_id, action: str, link_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"account_model.{action}", EVENT_SOURCE, str(link_id),
        _dumps(payload))


async def _publish(pool, org_id, action: str, link_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    await publish_event(pool, str(org_id), f"account_model.{action}", EVENT_SOURCE, str(link_id),
                        json.loads(_dumps(payload)), created_by=str(actor_id) if actor_id else None)


def _settings_brief(s: dict) -> dict:
    return {"cash_floor": _num(s["cash_floor"]), "drift_threshold": _num(s["drift_threshold"]),
            "rebalance_frequency": s["rebalance_frequency"], "exclusions": s["exclusions"],
            "exclusion_policy": s["exclusion_policy"], "gain_budget": _num(s["gain_budget"]),
            "avoid_short_term": s["avoid_short_term"], "short_term_wait_days": s["short_term_wait_days"],
            "harvesting_engine": s["harvesting_engine"], "fee_schedule_override_id": s["fee_schedule_override_id"],
            "harvest_enabled": bool(s.get("harvest_enabled")), "harvest_loss_pct": _num(s.get("harvest_loss_pct")),
            "harvest_loss_min": _num(s.get("harvest_loss_min")), "harvest_to_cash": s.get("harvest_to_cash"), "harvest_swap_back": s.get("harvest_swap_back")}


# ── Proposing a change (join / settings / change of model / end) ───────────
async def propose_change(pool, *, org_id, actor_id, account_id, change_kind: str, model_id=None,
                         settings: dict | None = None, reason: str | None = None, notes: str | None = None) -> dict:
    """A pending version + its maker-checker task. Returns the pending row."""
    if change_kind not in CHANGE_LABELS:
        raise AccountModelError(f"change is one of {list(CHANGE_LABELS)}")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await load_account(conn, org_id, account_id)
        if is_closed(account):
            raise AccountModelConflict(f"account {account['account_number_masked']} is closed "
                                       f"({account['closed_on'].isoformat()}) — a closed account cannot join a model")
        if change_kind != "end":   # rep7 — a held-away account never joins (a join is a transition: it trades)
            from services.held_away import refuse

            refuse(account, f"{CHANGE_LABELS[change_kind].lower()}", AccountModelConflict)
        current = await active_link(conn, account["id"])
        if await pending_link(conn, account["id"]):
            raise AccountModelConflict("a change for this account is already awaiting approval — decide it first")
        if change_kind == "join":
            if current:
                raise AccountModelConflict("this account already follows a model — change its model instead")
            if not model_id:
                raise AccountModelError("choose an approved model from the registry to join")
        elif not current:
            raise AccountModelConflict("this account does not follow a model yet — join one first")
        if change_kind == "change_model":
            if not model_id:
                raise AccountModelError("choose the model to change to")
            if str(model_id) == str(current["model_id"]):
                raise AccountModelError("the account already follows that model")
        if change_kind == "end" and not (reason or "").strip():
            raise AccountModelError("leaving a model needs a reason")
        mid = model_id if change_kind in ("join", "change_model") else current["model_id"]
        model = await model_brief(conn, org_id, mid)
        if change_kind in ("join", "change_model") and (model["status"] != "active" or not model["live_version_id"]):
            raise AccountModelConflict(f"{model['name']} has no approved version — only an approved model from the "
                                       "registry can be joined")
        base = settings_of(current) if current else dict(DEFAULT_SETTINGS)
        clean = await clean_settings(conn, org_id, account, settings or {}, base) if change_kind != "end" else base
        if change_kind == "settings" and _settings_brief(clean) == _settings_brief(base):
            raise AccountModelError("nothing to change")
        fee_changes = change_kind in ("join", "change_model") or (
            change_kind == "settings" and clean["fee_schedule_override_id"] != base["fee_schedule_override_id"])
        fee = await proposed_fee(conn, org_id, account, model, clean) if fee_changes else {}
        group = current["link_group_id"] if current else None
        version_no = 1
        if group:
            version_no = int(await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) FROM {T_LINK} "
                                                 "WHERE link_group_id = $1", group)) + 1
    new_id = uuid.uuid4()
    advice_only = not account["is_discretionary"]
    title = f"{CHANGE_LABELS[change_kind]}: {account['account_number_masked']} — {model['name']}"
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_LINK, subject_type=SUBJECT_LINK, subject_id=new_id, maker_id=actor["id"],
        eligible_permission=APPROVE, title=title,
        payload={"account": account["account_number_masked"], "model": model["name"], "change": change_kind,
                 "settings": _settings_brief(clean), "advice_only": advice_only, "proposed_fee": fee,
                 "reason": (reason or "").strip() or None},
        action_path=f"/portfolio/accounts/{account['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_LINK} (id, org_id, account_id, model_id, link_group_id, version_no, change_kind,
                            replaces_link_id, status, advice_only, cash_floor, drift_threshold, rebalance_frequency,
                            exclusions, exclusion_policy, gain_budget, avoid_short_term, short_term_wait_days,
                            harvesting_engine, fee_schedule_override_id, proposed_fee, maker_id, approval_task_id,
                            end_reason, notes, harvest_loss_pct, harvest_loss_min, harvest_to_cash, harvest_swap_back,
                            harvest_enabled)
                        VALUES ($1, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7, $8::uuid, 'pending', $9, $10, $11,
                                $12, $13::jsonb, $14, $15, $16, $17, $18, $19::uuid, $20::jsonb, $21::uuid,
                                $22::uuid, $23, $24, $25, $26, $27, $28, $29) RETURNING *""",
                    new_id, str(org_id), str(account["id"]), str(model["id"]), str(group or new_id), version_no,
                    change_kind, str(current["id"]) if current else None, advice_only, clean["cash_floor"],
                    clean["drift_threshold"], clean["rebalance_frequency"], _dumps(clean["exclusions"]),
                    clean["exclusion_policy"], clean["gain_budget"], clean["avoid_short_term"],
                    clean["short_term_wait_days"], clean["harvesting_engine"], clean["fee_schedule_override_id"],
                    _dumps(fee), str(actor["id"]), task["id"], (reason or "").strip() or None,
                    (notes or "").strip() or None, clean.get("harvest_loss_pct"), clean.get("harvest_loss_min"),
                    clean.get("harvest_to_cash"), clean.get("harvest_swap_back"), bool(clean["harvest_enabled"]))
                await _audit(conn, org_id, actor["id"], f"propose_{change_kind}", new_id,
                             {"account_id": str(account["id"]), "model_id": str(model["id"]), "task_id": task["id"],
                              "settings": _settings_brief(clean), "proposed_fee": fee, "advice_only": advice_only})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the account model change could not be recorded")
        raise
    await _publish(pool, org_id, f"proposed_{change_kind}", new_id,
                   {"account_id": str(account["id"]), "model_id": str(model["id"]), "task_id": task["id"]},
                   actor["id"])
    return {**dict(row), "task": task}


# ── Deciding ────────────────────────────────────────────────────────────────
async def _write_fees(conn, org_id, account: dict, row: dict, actor_id, prior: dict | None) -> dict:
    """Fee rows the approved change proposed — through the fee module."""
    from services import fee_schedules as FS

    out = {"assignment": None, "exclusions": [], "ended_exclusions": [], "ended_assignment": None, "notes": []}
    fee = _loads(row["proposed_fee"]) or {}
    kind = row["change_kind"]
    if kind == "end" and prior:
        if prior["fee_assignment_id"]:
            try:
                out["ended_assignment"] = await FS.end_assignment(conn, str(org_id), prior["fee_assignment_id"])
            except FS.FeeScheduleNotFoundError:
                out["notes"].append("the fee assignment had already ended")
        for x in prior["fee_exclusion_ids"] or []:
            try:
                out["ended_exclusions"].append(await FS.end_exclusion(conn, str(org_id), x))
            except FS.FeeScheduleNotFoundError:
                pass
        return out
    if not fee:      # a settings change that does not touch the fee keeps the prior rows
        out["carried"] = True
        return out
    if kind == "change_model" and prior:
        for x in prior["fee_exclusion_ids"] or []:
            try:
                out["ended_exclusions"].append(await FS.end_exclusion(conn, str(org_id), x))
            except FS.FeeScheduleNotFoundError:
                pass
    if fee.get("schedule_id"):
        out["assignment"] = await FS.create_assignment(conn, str(org_id), fee_schedule_id=fee["schedule_id"],
                                                       scope_type="ACCOUNT", scope_id=str(account["id"]),
                                                       created_by=str(actor_id), replace_existing=True)
    else:
        out["notes"].append(fee.get("note") or "no fee schedule to assign")
    keep = [] if kind == "change_model" else list((prior or {}).get("fee_exclusion_ids") or [])
    have = set()
    if keep:
        have = {r["basis_value"] for r in await conn.fetch(
            "SELECT basis_value FROM public.fee_exclusions WHERE id = ANY($1::uuid[]) AND valid_to IS NULL "
            "AND system_to IS NULL", keep)}
    for e in fee.get("exclusions") or []:
        if e["asset_id"] in have:
            continue
        out["exclusions"].append(await FS.create_exclusion(
            conn, str(org_id), scope_type="ACCOUNT", scope_id=str(account["id"]), basis_type="SECURITY",
            basis_value=e["asset_id"], treatment="EXCLUDE", reason=e["reason"], created_by=str(actor_id)))
    out["keep_exclusion_ids"] = keep
    return out


async def _apply_outcome(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    """Apply a decided link task. Idempotent: a version no longer pending on
    THIS task is left alone."""
    follow_up = None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {T_LINK} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                  "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
        if row is None or row["status"] != "pending":
            return dict(row) if row else None
        row = dict(row)
        now = datetime.now(timezone.utc)
        checker = uuid.UUID(str(actor_id))
        if outcome == "approve":
            if checker == row["maker_id"]:
                raise MakerCheckerViolation(MAKER_CHECKER_RULE)
            account = await load_account(conn, org_id, row["account_id"])
            if is_closed(account):
                raise AccountModelConflict("the account has closed since this change was requested — reject it")
            prior = await link_row(conn, org_id, row["replaces_link_id"], lock=True) if row["replaces_link_id"] \
                else None
            if prior is not None and prior["status"] != "active":
                raise AccountModelConflict("the version this change replaces is no longer active — reject it")
            fees = await _write_fees(conn, org_id, account, row, checker, prior)
            if row["change_kind"] == "end":
                await _update(conn, prior["id"], {"status": "ended", "ended_at": now, "end_reason": row["end_reason"],
                                                  "valid_to": now})
                new = await _update(conn, row["id"], {
                    "status": "ended", "checker_id": checker, "decided_at": now, "decision_note": note,
                    "joined_at": prior["joined_at"], "ended_at": now, "valid_to": now})
            else:
                if prior is not None:
                    await _update(conn, prior["id"], {"status": "superseded", "valid_to": now})
                if fees.get("carried") and prior is not None:
                    fee_assign, fee_excl = prior["fee_assignment_id"], list(prior["fee_exclusion_ids"] or [])
                else:
                    fee_assign = uuid.UUID(fees["assignment"]["id"]) if fees.get("assignment") else (
                        prior["fee_assignment_id"] if prior is not None and row["change_kind"] == "settings"
                        else None)
                    fee_excl = [uuid.UUID(str(x)) for x in fees.get("keep_exclusion_ids") or []] + [
                        uuid.UUID(e["id"]) for e in fees.get("exclusions") or []]
                new = await _update(conn, row["id"], {
                    "status": "active", "checker_id": checker, "decided_at": now, "decision_note": note,
                    "joined_at": prior["joined_at"] if (prior is not None and row["change_kind"] == "settings")
                    else now, "fee_assignment_id": fee_assign, "fee_exclusion_ids": fee_excl})
                if row["change_kind"] in ("join", "change_model"):
                    follow_up = new
            await _audit(conn, org_id, checker, f"approve_{row['change_kind']}", row["id"],
                         {"task_id": task["id"], "note": note, "replaced": str(prior["id"]) if prior else None,
                          "fees": json.loads(_dumps(fees))})
        else:
            new = await _update(conn, row["id"], {"status": "rejected", "checker_id": checker, "decided_at": now,
                                                  "decision_note": note})
            await _audit(conn, org_id, checker, f"reject_{row['change_kind']}", row["id"],
                         {"task_id": task["id"], "note": note})
    await _publish(pool, org_id, f"{outcome}d_{row['change_kind']}", row["id"],
                   {"account_id": str(row["account_id"]), "model_id": str(row["model_id"]), "task_id": task["id"]},
                   actor_id)
    if follow_up is not None:
        from services import rebalance_proposals as RP

        try:
            p = await RP.generate(pool, org_id=org_id, account_id=follow_up["account_id"], trigger="join",
                                  maker_kind="user", maker_id=follow_up["maker_id"], auto_submit=True,
                                  trigger_ref={"link_id": str(follow_up["id"]), "task_id": task["id"]})
            new = {**new, "transition_proposal_id": p["id"] if p else None}
        except Exception as exc:  # noqa: BLE001 — the approval stands; the proposal can be generated again
            log.exception("transition proposal after join failed for %s", follow_up["account_id"])
            new = {**new, "transition_proposal_error": str(exc)}
    return new


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply_outcome(pool, task["org_id"], task, outcome, actor_id, note)


HT.register_kind(TASK_LINK, label="Account model change", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)


async def sync_task_outcome(pool, *, org_id, link_id) -> None:
    async with pool.acquire() as conn:
        row = await link_row(conn, org_id, link_id)
        if row["status"] != "pending" or not row["approval_task_id"]:
            return
        task = await HT.get_task(conn, org_id, row["approval_task_id"])
    if task["status"] == "completed" and task["outcome"] in ("approve", "reject") and task["completed_by"]:
        await _apply_outcome(pool, org_id, task, task["outcome"], task["completed_by"], task["note"])


async def decide(pool, *, org_id, actor_id, link_id, outcome: str, note: str | None = None) -> dict:
    """Approve / reject a pending change through its task. Refused (each
    leaving it unchanged): 403 the maker; 409 not pending; 422 a reject with
    no note."""
    if outcome not in ("approve", "reject"):
        raise AccountModelError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise AccountModelError("a rejection needs a note saying why")
    await sync_task_outcome(pool, org_id=org_id, link_id=link_id)
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await link_row(conn, org_id, link_id)
    if row["status"] != "pending":
        raise AccountModelConflict(f"this change is {LINK_STATUS_LABELS[row['status']].lower()}, not awaiting approval")
    if actor["id"] == row["maker_id"]:
        raise MakerCheckerViolation(MAKER_CHECKER_RULE)
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise AccountModelConflict(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await link_row(conn, org_id, link_id)
    return result


# ── Out ─────────────────────────────────────────────────────────────────────
def link_out(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "account_id": str(r["account_id"]), "model_id": str(r["model_id"]),
            "model_name": r.get("model_name"), "link_group_id": str(r["link_group_id"]),
            "version_no": r["version_no"], "change_kind": r["change_kind"], "status": r["status"],
            "advice_only": r["advice_only"], "settings": _settings_brief(settings_of(r)),
            "proposed_fee": _loads(r["proposed_fee"]) or {},
            "fee_assignment_id": str(r["fee_assignment_id"]) if r["fee_assignment_id"] else None,
            "fee_exclusion_ids": [str(x) for x in r["fee_exclusion_ids"] or []],
            "maker_id": str(r["maker_id"]), "checker_id": str(r["checker_id"]) if r["checker_id"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decision_note": r["decision_note"], "decided_at": _iso(r["decided_at"]),
            "joined_at": _iso(r["joined_at"]), "ended_at": _iso(r["ended_at"]), "end_reason": r["end_reason"],
            "created_at": _iso(r["created_at"]), "valid_to": _iso(r["valid_to"])}


async def links_detail(conn, org_id, account_id) -> dict:
    rows = [dict(r) for r in await conn.fetch(
        f"""SELECT l.*, m.name AS model_name FROM {T_LINK} l
            LEFT JOIN portfolio.models m ON m.id = l.model_id AND m.system_to IS NULL AND m.valid_to IS NULL
            WHERE l.account_id = $1::uuid AND l.org_id = $2::uuid AND l.system_to IS NULL
            ORDER BY l.created_at DESC""", str(account_id), str(org_id))]
    active = next((r for r in rows if r["status"] == "active" and r["valid_to"] is None), None)
    pending = next((r for r in rows if r["status"] == "pending" and r["valid_to"] is None), None)
    return {"active": link_out(active), "pending": link_out(pending), "history": [link_out(r) for r in rows]}


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"link_statuses": pairs(LINK_STATUS_LABELS), "changes": pairs(CHANGE_LABELS),
            "exclusion_policies": pairs(EXCLUSION_POLICY_LABELS),
            "harvesting_engines": pairs(HARVESTING_ENGINE_LABELS), "rebalance_frequencies": pairs(FREQUENCY_LABELS),
            "setting_fields": list(SETTING_FIELDS)}
