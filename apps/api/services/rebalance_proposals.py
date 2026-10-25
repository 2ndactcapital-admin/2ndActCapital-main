"""Rebalance proposals (rep5) — one generator, every trigger; approval on human
tasks; an approved proposal becomes execution instructions through rep2.

EVERY TRIGGER PRODUCES A PROPOSAL, NEVER A TRADE
──────────────────────────────────────────────────────────────────────────────
:func:`generate` is the one function. Triggers:
  join          after a join (or change of model) is approved — the transition
                from what the account already holds (services.account_models);
  model_change  'model.version_approved' for every account following the model
                live (a direct call from rep3's after_approval, Task 1c; the
                scheduler tick sweeps recent events again — idempotent per
                account + model version);
  drift         the daily scan on the scheduler tick: max |actual − target|
                over the account's own valuation above its drift threshold
                (link override → the model's spec → org setting → 1%);
  cash_flow     cash above the account's cash target (its cash floor, or the
                model's cash weight when higher) by more than the org threshold
                (a fraction of the account), or a withdrawal staff enter;
  manual        "Rebalance now".
  harvest       (rep6) the daily loss-harvest scan (services.harvest) — a SYSTEM
                proposal of loss sales and approved replacements (and any
                swap-backs), on the same review task; a person approves.
The arithmetic is services.rebalance_engine (pure). Prices are the account's
own position valuation (market_value / quantity, as-of shown); a security with
no price is bought by NOTIONAL and a holding with no price is never sold.

ONE OPEN PROPOSAL PER ACCOUNT (partial unique index: draft / awaiting approval
/ approved-and-executing). A newer one SUPERSEDES an older draft or pending one
(its task withdrawn; logged); while an approved one's instructions are still
working, a new one is refused. Proposals EXPIRE after N days (org setting) or
when the account's valuation date moves past the one they were computed on.

APPROVAL — services.human_tasks
──────────────────────────────────────────────────────────────────────────────
A person's proposal: a maker-checker task (``rebalance_proposal_approval``,
approve_proposals, maker excluded at the app, the engine and the CHECK). A
system trigger's proposal: a ``rebalance_proposal_review`` task with no maker
(``system_open``). Reject needs a note.

EXECUTION — rep2, unchanged after the first step (Task 1d)
──────────────────────────────────────────────────────────────────────────────
On approval the trade list is routed through route_trade_list (Altruist
'unknown' and held-away → manual execution instructions). For a PERSON's
proposal the instructions start 'approved' — maker = the proposal's maker,
approved_by = its checker, the lots carried — because the proposal's own
maker-checker is the approval (services.execution re-reads it; the database's
insert guard re-checks it). A SYSTEM proposal has no human maker, and rep2's
CHECKs require one distinct from the approver, so its instructions are created
as drafts by the reviewer and submitted to rep2's own approval (a second
person) — recorded as a [FIND], not hidden. Mark sent / fills / reconcile /
close are rep2's flow. The proposal becomes 'executed' when every instruction
is closed, cancelled or expired (:func:`sync_executed`, on the tick and on
request). Nothing is ever sent anywhere automatically.

WASH-SALE CONTROL (rep6 — replaced the basic flag): services.wash_sale, on
every proposal. Every trade line carries its result (clear | replaced |
deferred | blocked, with the matches that caused it); a deferred or blocked
line is recorded on the proposal and is never routed to an instruction (the
CHECK rebalance_proposal_trades_wash_route_chk backs it). Proposals made before
rep6 keep their flag (``wash_sale_flag``) and route as they did.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from services import account_models as AM
from services import human_tasks as HT
from services import rebalance_engine as E
from services import wash_sale as WS

log = logging.getLogger(__name__)

T_P = "public.rebalance_proposals"
T_T = "public.rebalance_proposal_trades"
T_PLAN = "public.account_transition_plans"
TASK_APPROVAL = "rebalance_proposal_approval"
TASK_REVIEW = "rebalance_proposal_review"
SUBJECT = "rebalance_proposal"
VIEW, MANAGE, APPROVE = AM.VIEW, AM.MANAGE, AM.APPROVE

TRIGGER_LABELS = {"join": "Joined the model", "model_change": "The model changed", "drift": "Drift over threshold",
                  "cash_flow": "Cash flow", "manual": "Rebalance now", "harvest": "Tax-loss harvest"}
# the triggers that never supersede an open proposal (they wait for the account to be free)
NO_SUPERSEDE_TRIGGERS = ("harvest",)
STATUS_LABELS = {"draft": "Draft", "pending_approval": "Awaiting approval", "approved": "Approved — executing",
                 "rejected": "Rejected", "expired": "Expired", "superseded": "Superseded", "executed": "Executed"}
OPEN_STATUSES = ("draft", "pending_approval", "approved")
WARNING_LABELS = {
    "cannot_value": "Cannot value a holding — needs a price", "unpriced_buy": "Bought by amount (no price)",
    "stale_valuation": "Valuation is older than the stale limit", "capability_unknown": "Custodian capability unknown",
    "held_away": "Held-away account — manual execution", "advice_only": "Advice only — non-discretionary account",
    "dust_suppressed": "Trades under the minimum size left out", "buys_scaled": "Buys scaled to the cash available",
    "sell_later": "Some sales wait for a later year", "unknown_cost_basis": "A lot has no cost basis",
    "no_cash_balance": "No cash balance on record", "unresolved_holding": "A holding is not in the security master",
    "unresolved_target": "A model position is not resolved", "no_trades": "Nothing to trade",
    "wash_sale_flag": "Possible wash sale (flag only — before rep6)", "exclusion_note": "Exclusion applied",
    "wash_sale_control": "Wash-sale control changed a trade", "wash_sale_held": "Loss lots held back (wash sale)",
    "harvest_note": "Tax-loss harvest",
}
PRICE_SOURCE_LABELS = {"account_position": "The account's own position valuation",
                       "security_price": "Security price", "none": "No price — bought by amount"}
PLAN_ACTION_LABELS = {"keep": "Keep", "trim": "Trim", "add": "Add", "sell_now": "Sell now",
                      "sell_later": "Sell later", "sell_now_and_later": "Sell now and later",
                      "cannot_value": "Cannot value — needs a price"}

MIN_TRADE_SETTING = "replication.proposals.min_trade_size"
EXPIRY_SETTING = "replication.proposals.expiry_days"
STALE_SETTING = "replication.proposals.stale_valuation_days"
CASH_FLOW_SETTING = "replication.proposals.cash_flow_threshold"
EXECUTE_WITHIN_SETTING = "replication.proposals.execute_within_days"
DEFAULT_MIN_TRADE = Decimal("100")
DEFAULT_EXPIRY_DAYS = 5
DEFAULT_STALE_DAYS = 3
DEFAULT_CASH_FLOW = Decimal("0.02")
DEFAULT_EXECUTE_WITHIN = 5
EVENT_SWEEP_DAYS = 7


class ProposalError(ValueError):
    pass


class ProposalNotFound(LookupError):
    pass


class ProposalConflict(RuntimeError):
    pass


class OpenProposalExists(ProposalConflict):
    """A trigger that never supersedes (harvest) met the account's open proposal."""

    def __init__(self, proposal: dict):
        self.proposal = proposal
        super().__init__(f"an open proposal exists ({proposal['status']}) — a {', '.join(NO_SUPERSEDE_TRIGGERS)} "
                         "proposal never supersedes it")


class MakerCheckerViolation(PermissionError):
    pass


MAKER_CHECKER_RULE = ("Maker-checker: you made this proposal, so a different holder of approve_proposals must approve "
                      "or reject it")


def _dumps(v) -> str:
    return AM._dumps(v)


def _loads(v):
    return AM._loads(v)


def _uuid(v, what="proposal") -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise ProposalNotFound(f"{what} not found") from None


def _num(v):
    return None if v is None else str(v)


def _iso(v):
    return v.isoformat() if v else None


# ── Settings ────────────────────────────────────────────────────────────────
async def _setting(conn, org_id, key: str, default, *, lo=None, hi=None, integer: bool = False):
    from services.org_settings import get_setting_with_origin

    try:
        v, is_default = await get_setting_with_origin(conn, uuid.UUID(str(org_id)), key)
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the stated default
        return default
    if v is None or is_default:
        return default
    try:
        x = int(v) if integer else Decimal(str(v))
    except Exception:  # noqa: BLE001
        return default
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        return default
    return x


async def drift_threshold(conn, org_id, link: dict, version_id) -> tuple[Decimal, str]:
    from services import replica_models as RM

    if link.get("drift_threshold") is not None:
        return Decimal(str(link["drift_threshold"])), "account"
    spec = await RM.spec_for_version(conn, version_id)
    if spec and spec.get("drift_threshold") is not None:
        return Decimal(str(spec["drift_threshold"])), "model"
    v = await _setting(conn, org_id, RM.DRIFT_SETTING, None, lo=Decimal("0.0000000001"), hi=Decimal(1))
    if v is not None:
        return v, "org_setting"
    return RM.DEFAULT_DRIFT, "default"


# ── Inputs ──────────────────────────────────────────────────────────────────
async def _ticker_map(conn, tickers: set[str]) -> dict[str, str]:
    if not tickers:
        return {}
    rows = await conn.fetch(
        "SELECT upper(id_value) AS t, global_security_id FROM portfolio.securities_global_identifiers "
        "WHERE id_type = 'ticker' AND upper(id_value) = ANY($1::text[]) AND valid_to IS NULL AND system_to IS NULL",
        sorted(tickers))
    return {r["t"]: str(r["global_security_id"]) for r in rows}


async def target_for(conn, org_id, link: dict, version_id) -> dict:
    """The model's look-through weights, minus the account's exclusions by its
    exclusion policy (exact), with labels."""
    from services import models as M

    lt = await M.look_through(conn, version_id)
    target: dict[str, Decimal] = {}
    labels: dict[str, str] = {}
    warnings = []
    for k, v in lt.items():
        w = Decimal(str(v["weight"]))
        if v["resolution_state"] == "cash":
            target[E.CASH] = target.get(E.CASH, Decimal(0)) + w
        elif v["global_security_id"]:
            target[v["global_security_id"]] = target.get(v["global_security_id"], Decimal(0)) + w
            labels[v["global_security_id"]] = v["label"]
        else:
            target[E.CASH] = target.get(E.CASH, Decimal(0)) + w
            warnings.append({"code": "unresolved_target", "message": f"model position {v['label']} is not resolved "
                             f"to a security — its {E.pct(w)} is held as cash"})
    alts_raw = {str(p["global_security_id"]): [str(a).upper() for a in p["alternates"] or []]
                for p in await M.positions_of(conn, version_id) if p["global_security_id"]}
    tmap = await _ticker_map(conn, {a for v in alts_raw.values() for a in v})
    alternates = {k: [tmap[a] for a in v if a in tmap] for k, v in alts_raw.items()}
    for k, v in alts_raw.items():
        for a in v:
            if a in tmap:
                labels.setdefault(tmap[a], a)
    excluded = {e["global_security_id"]: e["reason"] for e in (AM._loads(link["exclusions"]) or [])}
    for gid in excluded:
        if gid not in labels:
            n = await conn.fetchval("SELECT name FROM portfolio.securities_global WHERE id = $1::uuid", gid)
            labels[gid] = n or gid
    tgt, notes = E.apply_exclusions(target, excluded, link["exclusion_policy"], alternates, labels)
    return {"target": tgt, "labels": labels, "alternates": alternates, "excluded": excluded, "notes": notes,
            "warnings": warnings}


async def realized_ytd(conn, org_id, account_id, today: date) -> Decimal:
    """Gains already realized this calendar year: closed lots of this account
    whose CLOSING transaction's trade_date is this year
    (tax_lots.realized_gain_loss — the transaction carries no gain itself)."""
    v = await conn.fetchval(
        """SELECT COALESCE(sum(tl.realized_gain_loss), 0)
           FROM portfolio.tax_lots tl
           JOIN portfolio.positions p ON p.id = tl.position_id
           JOIN portfolio.transactions x ON x.id = tl.closing_transaction_id AND x.system_to IS NULL
           WHERE tl.org_id = $1::uuid AND p.account_id = $2::uuid AND tl.status = 'closed'
             AND tl.valid_to IS NULL AND tl.system_to IS NULL AND tl.realized_gain_loss IS NOT NULL
             AND x.trade_date >= $3 AND x.trade_date <= $4""",
        str(org_id), str(account_id), date(today.year, 1, 1), today)
    return Decimal(str(v or 0))


async def household_accounts(conn, org_id, account: dict) -> list[dict]:
    """The account's household: same household_id, same primary entity, or a
    shared owner (account_owners) — every registration, IRAs included."""
    rows = await conn.fetch(
        """WITH owners AS (
             SELECT $3::uuid AS entity_id
             UNION SELECT entity_id FROM public.account_owners WHERE account_id = $2::uuid AND org_id = $1::uuid
               AND valid_to IS NULL AND system_to IS NULL)
           SELECT a.id, a.account_number_masked, a.registration_type, a.tax_status FROM public.accounts a
           WHERE a.org_id = $1::uuid AND a.system_to IS NULL AND a.valid_to IS NULL
             AND (a.id = $2::uuid OR ($4::uuid IS NOT NULL AND a.household_id = $4::uuid)
                  OR a.primary_entity_id IN (SELECT entity_id FROM owners)
                  OR a.id IN (SELECT ao.account_id FROM public.account_owners ao WHERE ao.org_id = $1::uuid
                               AND ao.entity_id IN (SELECT entity_id FROM owners)
                               AND ao.valid_to IS NULL AND ao.system_to IS NULL))""",
        str(org_id), str(account["id"]), str(account["primary_entity_id"]),
        str(account["household_id"]) if account.get("household_id") else None)
    return [dict(r) for r in rows]


async def household_transactions(conn, org_id, accounts: list[dict], keys: list[str], today: date) -> list[dict]:
    if not keys or not accounts:
        return []
    by_id = {str(a["id"]): a for a in accounts}
    rows = await conn.fetch(
        """SELECT x.id, x.transaction_type_code, x.trade_date, x.quantity, p.account_id, a.global_security_id,
                  (SELECT sum(tl.realized_gain_loss) FROM portfolio.tax_lots tl WHERE tl.closing_transaction_id = x.id
                     AND tl.system_to IS NULL AND tl.valid_to IS NULL) AS realized
           FROM portfolio.transactions x
           JOIN portfolio.positions p ON p.id = x.position_id
           JOIN portfolio.assets a ON a.id = p.asset_id
           WHERE x.org_id = $1::uuid AND p.account_id = ANY($2::uuid[]) AND x.trade_date >= $3 AND x.trade_date <= $4
             AND x.transaction_type_code IN ('buy', 'sell') AND x.valid_to IS NULL AND x.system_to IS NULL
             AND a.global_security_id = ANY($5::uuid[])""",
        str(org_id), list(by_id), today - timedelta(days=E.WASH_WINDOW_DAYS), today, keys)
    out = []
    for r in rows:
        acct = by_id[str(r["account_id"])]
        realized = r["realized"]
        out.append({"key": str(r["global_security_id"]), "side": r["transaction_type_code"],
                    "trade_date": r["trade_date"], "quantity": r["quantity"],
                    "loss": None if realized is None else Decimal(str(realized)) < 0,
                    "account": acct["account_number_masked"], "registration_type": acct["registration_type"],
                    "tax_status": acct["tax_status"]})
    return out


async def _unheld_prices(conn, keys: list[str], since: date) -> dict:
    if not keys:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (global_security_id) global_security_id, price, price_date "
        "FROM portfolio.securities_global_prices WHERE global_security_id = ANY($1::uuid[]) AND price > 0 "
        "AND price_date >= $2 AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY global_security_id, price_date DESC", keys, since)
    return {str(r["global_security_id"]): (Decimal(str(r["price"])).quantize(E.QTY), r["price_date"],
                                           "security_price") for r in rows}


async def compute(conn, org_id, account: dict, link: dict, version_id, *, withdrawal: Decimal | None = None,
                  today: date | None = None) -> dict:
    """Everything a proposal stores, computed (no writes)."""
    from services.custodian_capabilities import available, capability
    from services.custody.registry import trade_custodian_key

    today = today or date.today()
    hold = await AM.load_holdings(conn, org_id, account["id"])
    tg = await target_for(conn, org_id, link, version_id)
    key = trade_custodian_key(account.get("custodian_code")) or "manual"
    frac = await capability(conn, key, "fractional_shares")
    trading = await capability(conn, key, "equity_trading_api")
    min_trade = await _setting(conn, org_id, MIN_TRADE_SETTING, DEFAULT_MIN_TRADE, lo=Decimal(0))
    stale_days = await _setting(conn, org_id, STALE_SETTING, DEFAULT_STALE_DAYS, lo=0, hi=366, integer=True)
    ytd = await realized_ytd(conn, org_id, account["id"], today)
    budget = Decimal(str(link["gain_budget"])) if link["gain_budget"] is not None else None
    labels = dict(tg["labels"])
    for h in hold["holdings"]:
        labels.setdefault(h.key, h.label)
    unheld = [k for k in tg["target"] if k != E.CASH and k not in {h.key for h in hold["holdings"]}]
    prices = await _unheld_prices(conn, unheld, today - timedelta(days=stale_days))
    # rep6 — household wash-sale control: the context, then rule (b) on the loss lots before the plan
    own_open = await open_proposal(conn, account["id"])
    ctx = await WS.load_context(conn, org_id, account, today, exclude_proposal_ids=[own_open["id"]] if own_open
                                and own_open["status"] in ("draft", "pending_approval") else [])
    cls = WS.tax_class(account)
    hold_rules = {}
    for h in hold["holdings"]:
        r = WS.sale_restriction(h.key, account_class=cls, ctx=ctx)
        if r:
            hold_rules[h.key] = r
    s = E.BuildSettings(
        sale=E.SaleSettings(today=today, gain_budget=budget, realized_ytd=ytd,
                            avoid_short_term=link["avoid_short_term"],
                            short_term_wait_days=link["short_term_wait_days"], fractional=available(frac),
                            wash_hold=hold_rules),
        cash_floor=Decimal(str(link["cash_floor"])), min_trade=min_trade, withdrawal=withdrawal)
    res = E.build(hold["holdings"], hold["cash"], tg["target"], labels, prices, s, tg["excluded"])
    warnings = list(tg["warnings"]) + [{"code": "exclusion_note", "message": n} for n in tg["notes"]] + res.warnings
    warnings += [{"code": "exclusion_note", "message": n} for n in res.notes]
    va = hold["valuation_as_of"]
    if va is None or (today - va).days > stale_days:
        warnings.append({"code": "stale_valuation", "message": f"the account's valuation is as of "
                         f"{va.isoformat() if va else 'never'} — older than {stale_days} day(s)"})
    if hold["cash_as_of"] is None:
        warnings.append({"code": "no_cash_balance", "message": "no cash balance is on record for this account — "
                         "cash is taken as zero"})
    for u in hold["unresolved"]:
        warnings.append({"code": "unresolved_holding", "message": f"{u['label']}: not in the security master — "
                         "not traded and left out of the account value"})
    if not available(trading):
        warnings.append({"code": "capability_unknown", "message": f"{key}: equity_trading_api is '{trading.value}' — "
                         "every trade becomes a manual execution instruction"})
    if not available(frac):
        warnings.append({"code": "capability_unknown", "message": f"{key}: fractional_shares is '{frac.value}' — "
                         "whole shares only (unpriced buys by amount)"})
    if account["is_held_away"]:
        warnings.append({"code": "held_away", "message": "held-away account: always manual execution instructions"})
    if link["advice_only"] or not account["is_discretionary"]:
        warnings.append({"code": "advice_only", "message": "non-discretionary account: this proposal is advice "
                         "only — its trades go to the member as instructions"})
    if not res.trades:
        warnings.append({"code": "no_trades", "message": "the account is at its target within the rules — nothing "
                         "to trade"})
    lines, res, w2 = await apply_wash_control(conn, account, res, hold, ctx, fractional=available(frac),
                                              today=today, prices=prices)
    warnings += w2
    for k in {t.key for t, _ in lines}:
        labels.setdefault(k, ctx.label(k))
    thr, thr_src = await drift_threshold(conn, org_id, link, version_id)
    return {"result": res, "lines": lines, "warnings": warnings, "labels": labels, "holdings": hold,
            "realized_ytd": ytd, "gain_budget": budget, "fractional": available(frac), "threshold": thr,
            "threshold_source": thr_src, "min_trade": min_trade, "today": today}


async def apply_wash_control(conn, account: dict, res: E.BuildResult, hold: dict, ctx, *, fractional: bool,
                             today: date, prices: dict | None = None) -> tuple[list, E.BuildResult, list[dict]]:
    """Run rules (a, c, d) on the built trades; re-state the after-weights on the
    lines that will actually trade. Returns (lines, result, warnings)."""
    import dataclasses

    px = dict(prices or {})
    for h in hold["holdings"]:
        if h.price is not None:
            px[h.key] = (h.price, h.price_as_of, h.price_source)
    subs = {c["key"] for t in res.trades for c in ctx.substitutes.get(t.key, [])} - set(px)
    if subs:
        stale = await _setting(conn, account["org_id"], STALE_SETTING, DEFAULT_STALE_DAYS, lo=0, hi=366, integer=True)
        px.update(await _unheld_prices(conn, sorted(subs), today - timedelta(days=stale)))
    lines = WS.control(res, account=account, ctx=ctx, prices=px, fractional=fractional)
    warnings = []
    changed = [r for _, r in lines if r["outcome"] != "clear"]
    if changed:
        counts = {o: sum(1 for r in changed if r["outcome"] == o) for o in WS.OUTCOMES if o != "clear"}
        warnings.append({"code": "wash_sale_control", "message": "household wash-sale control: " + ", ".join(
            f"{n} {o}" for o, n in counts.items() if n) + " — a deferred or blocked line is never routed"})
    going = [t for t, r in lines if WS.routable(r)]
    after = E.after_weights(hold["holdings"], hold["cash"], going, res.account_value)
    res = dataclasses.replace(res, after=after, drift_after=E.qw(E.max_drift(after, res.target)))
    return lines, res, warnings


def _weights_out(w: dict[str, Decimal], labels: dict[str, str]) -> list[dict]:
    return [{"key": k, "label": "Cash" if k == E.CASH else labels.get(k, k), "weight": str(v)}
            for k, v in sorted(w.items(), key=lambda kv: (kv[0] == E.CASH, -kv[1], kv[0]))]


# ── Reading ─────────────────────────────────────────────────────────────────
async def load(conn, org_id, proposal_id, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_P} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), _uuid(proposal_id), str(org_id))
    if row is None:
        raise ProposalNotFound("proposal not found")
    return dict(row)


async def open_proposal(conn, account_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_P} WHERE account_id = $1::uuid AND system_to IS NULL "
                              "AND status = ANY($2::text[])", str(account_id), list(OPEN_STATUSES))
    return dict(row) if row else None


async def trades_of(conn, proposal_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"""SELECT t.*, g.name AS security_name,
                   (SELECT id_value FROM portfolio.securities_global_identifiers i WHERE i.global_security_id = g.id
                     AND i.id_type = 'ticker' AND i.system_to IS NULL AND i.valid_to IS NULL LIMIT 1) AS ticker
            FROM {T_T} t JOIN portfolio.securities_global g ON g.id = t.global_security_id
            WHERE t.proposal_id = $1::uuid AND t.valid_to IS NULL AND t.system_to IS NULL ORDER BY t.line_no""",
        str(proposal_id))]


# ── Writing primitives ──────────────────────────────────────────────────────
async def _archive(conn, proposal_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {T_P}
            SELECT (jsonb_populate_record(NULL::{T_P}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {T_P} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(proposal_id))
    if got is None:
        raise ProposalConflict("the proposal is not live — refusing to change it")


async def _update(conn, proposal_id, fields: dict) -> dict:
    await _archive(conn, proposal_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    vals = [(_dumps(v) if c == "execution" else v) for c, v in fields.items()]
    row = await conn.fetchrow(f"UPDATE {T_P} SET {sets}, system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(proposal_id), *vals)
    if row is None:
        raise ProposalConflict("the proposal changed underneath this request — reload and retry")
    return dict(row)


async def _audit(conn, org_id, user_id, action: str, proposal_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"proposal.{action}", SUBJECT, str(proposal_id),
        _dumps(payload))


async def _publish(pool, org_id, action: str, proposal_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    await publish_event(pool, str(org_id), f"proposal.{action}", SUBJECT, str(proposal_id),
                        json.loads(_dumps(payload)), created_by=str(actor_id) if actor_id else None)


async def _withdraw_task(pool, task_id, reason: str) -> None:
    """Close an open approval/review task on the engine (no actor needed —
    the subject went away; the same call rep2's expiry makes)."""
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided; the proposal state stands
        log.info("task %s was not open to withdraw", task_id)


# ── Generate ────────────────────────────────────────────────────────────────
async def generate(pool, *, org_id, account_id, trigger: str, maker_kind: str, maker_id=None,
                   auto_submit: bool = False, trigger_ref: dict | None = None, withdrawal: Decimal | None = None,
                   today: date | None = None, harvest_plan: dict | None = None) -> dict:
    """The ONE generator. Writes the proposal (draft), its trades and — for a
    join — the transition plan; supersedes an older open draft / pending one
    (never for a harvest: that raises OpenProposalExists instead);
    a system trigger (or ``auto_submit``) submits it."""
    if trigger not in TRIGGER_LABELS:
        raise ProposalError(f"trigger is one of {list(TRIGGER_LABELS)}")
    if maker_kind not in ("user", "system") or (maker_kind == "user") != (maker_id is not None):
        raise ProposalError("a proposal is made by a person (maker_id) or by the system (no maker)")
    if withdrawal is not None and (not isinstance(withdrawal, Decimal) or withdrawal <= 0):
        raise ProposalError("a withdrawal is a decimal amount above zero")
    today = today or date.today()
    async with pool.acquire() as conn:
        if maker_kind == "user":
            await HT.assert_human(conn, maker_id, org_id)
        account = await AM.load_account(conn, org_id, account_id)
        if AM.is_closed(account, today):
            raise ProposalConflict(f"account {account['account_number_masked']} is closed")
        link = await AM.active_link(conn, account["id"])
        if link is None:
            raise ProposalConflict("the account does not follow a model — join one first")
        model = await AM.model_brief(conn, org_id, link["model_id"])
        if not model["live_version_id"]:
            raise ProposalConflict(f"{model['name']} has no approved version")
        existing = await open_proposal(conn, account["id"])
        if existing and trigger in NO_SUPERSEDE_TRIGGERS:
            raise OpenProposalExists(existing)
        if existing and existing["status"] == "approved":
            raise ProposalConflict("the instructions of the approved proposal for this account are still working — "
                                   "a new proposal waits until they are closed or cancelled")
        if trigger == "harvest":
            from services import harvest as HV

            if harvest_plan is None or maker_kind != "system":
                raise ProposalError("a harvest proposal is the system's, from the daily scan's plan")
            c = await HV.compute_for_proposal(conn, org_id, account, link, model["live_version_id"], harvest_plan,
                                              today=today)
        else:
            c = await compute(conn, org_id, account, link, model["live_version_id"], withdrawal=withdrawal,
                              today=today)
        expiry_days = await _setting(conn, org_id, EXPIRY_SETTING, DEFAULT_EXPIRY_DAYS, lo=1, hi=90, integer=True)
    r: E.BuildResult = c["result"]
    pid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    advice_only = bool(link["advice_only"] or not account["is_discretionary"])
    later = [{"key": x.key, "label": c["labels"].get(x.key, x.key), "lot_id": x.lot_id, "quantity": x.quantity,
              "year": x.year, "on_date": _iso(x.on_date), "est_gain": x.gain, "reason": x.reason} for x in r.later]
    ref = dict(trigger_ref or {})
    superseded = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            cur = await open_proposal(conn, account["id"])
            if cur and trigger in NO_SUPERSEDE_TRIGGERS:
                raise OpenProposalExists(cur)
            if cur and cur["status"] == "approved":
                raise ProposalConflict("an approved proposal for this account is executing")
            if cur:
                cur = await load(conn, org_id, cur["id"], lock=True)
                superseded = await _update(conn, cur["id"], {
                    "status": "superseded", "superseded_by_id": pid,
                    "status_reason": f"superseded by a newer proposal ({TRIGGER_LABELS[trigger].lower()}) {pid}"})
                await _audit(conn, org_id, maker_id, "superseded", cur["id"],
                             {"superseded_by_id": str(pid), "trigger": trigger, "previous_status": cur["status"]})
            await conn.execute(
                f"""INSERT INTO {T_P} (id, org_id, account_id, link_id, model_id, model_version_id, trigger,
                        trigger_ref, status, advice_only, valuation_as_of, account_value, cash_value, withdrawal_amount,
                        target_weights, before_weights, after_weights, drift_before, drift_after, est_st_gain,
                        est_lt_gain, gain_budget, gain_realized_ytd, warnings, sell_later, expires_at, maker_kind,
                        maker_id)
                    VALUES ($1, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6::uuid, $7, $8::jsonb, 'draft', $9, $10, $11,
                            $12, $13, $14::jsonb, $15::jsonb, $16::jsonb, $17, $18, $19, $20, $21, $22, $23::jsonb,
                            $24::jsonb, $25, $26, $27::uuid)""",
                pid, str(org_id), str(account["id"]), str(link["id"]), str(model["id"]),
                str(model["live_version_id"]), trigger, _dumps({**ref, "threshold": c["threshold"],
                                                                "threshold_source": c["threshold_source"]}),
                advice_only, c["holdings"]["valuation_as_of"], r.account_value.quantize(E.QTY),
                r.cash.quantize(E.QTY), withdrawal, _dumps(_weights_out(r.target, c["labels"])),
                _dumps(_weights_out(r.before, c["labels"])), _dumps(_weights_out(r.after, c["labels"])),
                r.drift_before, r.drift_after, r.est_st_gain.quantize(E.QTY), r.est_lt_gain.quantize(E.QTY),
                c["gain_budget"], c["realized_ytd"].quantize(E.QTY), _dumps(c["warnings"]), _dumps(later),
                now + timedelta(days=expiry_days), maker_kind, str(maker_id) if maker_id else None)
            for i, (t, wash) in enumerate(c["lines"], start=1):
                await conn.execute(
                    f"""INSERT INTO {T_T} (org_id, proposal_id, line_no, global_security_id, side, quantity, notional,
                            price, price_source, price_as_of, lots, est_st_gain, est_lt_gain, reason, wash_sale)
                        VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13, $14,
                                $15::jsonb)""",
                    str(org_id), pid, i, t.key, t.side, None if t.quantity is None else t.quantity.quantize(E.QTY),
                    t.notional, t.price, t.price_source, t.price_as_of,
                    _dumps([{**lot, "quantity": str(lot["quantity"]),
                             "unit_cost": _num(None if lot["unit_cost"] is None else lot["unit_cost"].quantize(E.QTY)),
                             "est_gain": _num(None if lot["est_gain"] is None else lot["est_gain"].quantize(E.QTY))}
                            for lot in t.lots]),
                    t.est_st_gain.quantize(E.QTY), t.est_lt_gain.quantize(E.QTY), t.reason, _dumps(wash))
            if trigger == "join":
                await conn.execute(f"UPDATE {T_PLAN} SET valid_to = now() WHERE account_id = $1::uuid "
                                   "AND valid_to IS NULL AND system_to IS NULL", str(account["id"]))
                await conn.execute(
                    f"""INSERT INTO {T_PLAN} (org_id, account_id, link_id, proposal_id, valuation_as_of, holdings,
                            plan_by_year, total_embedded_gain, gain_budget, years_to_complete, created_by)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb, $7::jsonb, $8, $9, $10, $11::uuid)""",
                    str(org_id), str(account["id"]), str(link["id"]), pid, c["holdings"]["valuation_as_of"],
                    _dumps(r.plan), _dumps(r.plan_by_year), r.total_embedded_gain.quantize(E.QTY), c["gain_budget"],
                    r.years_to_complete, str(maker_id) if maker_id else None)
            await _audit(conn, org_id, maker_id, "generated", pid,
                         {"trigger": trigger, "trigger_ref": ref, "trades": len(c["lines"]), "maker_kind": maker_kind,
                          "supersedes": str(superseded["id"]) if superseded else None})
    if superseded and superseded.get("approval_task_id"):
        await _withdraw_task(pool, superseded["approval_task_id"], "superseded by a newer proposal")
    if superseded:
        await _publish(pool, org_id, "superseded", superseded["id"], {"superseded_by_id": str(pid)}, maker_id)
    await _publish(pool, org_id, "generated", pid, {"trigger": trigger, "account_id": str(account["id"]),
                                                    "trades": len(c["lines"])}, maker_id)
    out = None
    if maker_kind == "system" or auto_submit:
        out = await submit(pool, org_id=org_id, proposal_id=pid, actor_id=maker_id, system=maker_kind == "system")
    if out is None:
        async with pool.acquire() as conn:
            out = await load(conn, org_id, pid)
    return out


async def transition_preview(conn, org_id, account_id, *, model_id=None, settings: dict | None = None,
                             today: date | None = None) -> dict:
    """The transition analysis for a joining (or re-targeted) account, computed
    without writing anything: keep / trim / add / sell now / sell later by year."""
    account = await AM.load_account(conn, org_id, account_id)
    link = await AM.active_link(conn, account["id"])
    mid = model_id or (link["model_id"] if link else None)
    if mid is None:
        raise ProposalConflict("choose a model to preview the transition against")
    model = await AM.model_brief(conn, org_id, mid)
    if not model["live_version_id"]:
        raise ProposalConflict(f"{model['name']} has no approved version")
    base = AM.settings_of(link)
    s = await AM.clean_settings(conn, org_id, account, settings or {}, base)
    shim = {**s, "exclusions": _dumps(s["exclusions"]), "advice_only": not account["is_discretionary"],
            "model_id": model["id"]}
    c = await compute(conn, org_id, account, shim, model["live_version_id"], today=today)
    return {"plan": plan_out(c["result"], c["labels"]), "warnings": c["warnings"],
            "valuation_as_of": _iso(c["holdings"]["valuation_as_of"]), "model": {"id": str(model["id"]),
                                                                                  "name": model["name"]}}


def plan_out(r: E.BuildResult, labels: dict) -> dict:
    def clean(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v

    return {"holdings": clean(r.plan), "plan_by_year": clean(r.plan_by_year),
            "total_embedded_gain": str(r.total_embedded_gain.quantize(E.QTY)),
            "years_to_complete": r.years_to_complete}


# ── Submit / decide ─────────────────────────────────────────────────────────
def _title(account: dict, p: dict) -> str:
    return f"{'Review' if p['maker_kind'] == 'system' else 'Approve'} rebalance: {account['account_number_masked']} " \
           f"— {TRIGGER_LABELS[p['trigger']].lower()}"


async def _expire_if_due(pool, org_id, p: dict) -> bool:
    """Expire an open draft / pending proposal whose time ran out or whose
    valuation date moved. Returns True when it expired it."""
    if p["status"] not in ("draft", "pending_approval"):
        return False
    reason = None
    if p["expires_at"] <= datetime.now(timezone.utc):
        reason = f"expired at {p['expires_at'].isoformat()}"
    else:
        async with pool.acquire() as conn:
            latest = await AM.valuation_as_of(conn, org_id, p["account_id"])
        if latest is not None and (p["valuation_as_of"] is None or latest > p["valuation_as_of"]):
            reason = (f"the account's valuation moved from {_iso(p['valuation_as_of']) or 'none'} to "
                      f"{latest.isoformat()} — generate a new proposal on the new valuation")
    if reason is None:
        return False
    async with pool.acquire() as conn:
        async with conn.transaction():
            cur = await load(conn, org_id, p["id"], lock=True)
            if cur["status"] not in ("draft", "pending_approval"):
                return False
            await _update(conn, cur["id"], {"status": "expired", "status_reason": reason})
            await _audit(conn, org_id, None, "expired", cur["id"], {"reason": reason})
    if p.get("approval_task_id"):
        await _withdraw_task(pool, p["approval_task_id"], f"the proposal expired: {reason}")
    await _publish(pool, org_id, "expired", p["id"], {"reason": reason}, None)
    return True


async def submit(pool, *, org_id, proposal_id, actor_id=None, system: bool = False) -> dict:
    """draft → awaiting approval. A person submits their own proposal (it opens
    the maker-checker task); a system proposal is submitted by the platform
    (a review task with no maker)."""
    async with pool.acquire() as conn:
        p = await load(conn, org_id, proposal_id)
        account = await AM.load_account(conn, org_id, p["account_id"])
        if p["status"] != "draft":
            raise ProposalConflict(f"the proposal is {STATUS_LABELS[p['status']].lower()}, not a draft")
        if system:
            if p["maker_kind"] != "system":
                raise ProposalError("only a system proposal is submitted by the system")
        else:
            actor = await HT.assert_human(conn, actor_id, org_id)
            if p["maker_kind"] != "user" or str(p["maker_id"]) != str(actor["id"]):
                raise MakerCheckerViolation("a proposal is submitted by the person who made it")
    if await _expire_if_due(pool, org_id, p):
        raise ProposalConflict("the proposal has expired — generate a new one")
    trades = len(await pool.fetch(f"SELECT 1 FROM {T_T} WHERE proposal_id = $1::uuid AND valid_to IS NULL "
                                  "AND system_to IS NULL", str(p["id"])))
    payload = {"account": account["account_number_masked"], "trigger": p["trigger"], "trades": trades,
               "advice_only": p["advice_only"], "valuation_as_of": _iso(p["valuation_as_of"]),
               "drift_before": _num(p["drift_before"]), "drift_after": _num(p["drift_after"])}
    if system:
        task = await HT.open_task(pool, org_id=org_id, kind=TASK_REVIEW, subject_type=SUBJECT, subject_id=p["id"],
                                  maker_id=None, eligible_permission=APPROVE, title=_title(account, p),
                                  payload=payload, action_path=f"/portfolio/proposals/{p['id']}",
                                  actor_kind="system")
    else:
        task = await HT.open_task(pool, org_id=org_id, kind=TASK_APPROVAL, subject_type=SUBJECT, subject_id=p["id"],
                                  maker_id=p["maker_id"], eligible_permission=APPROVE, title=_title(account, p),
                                  payload=payload, action_path=f"/portfolio/proposals/{p['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, p["id"], lock=True)
                if cur["status"] != "draft":
                    raise ProposalConflict("the proposal changed underneath this request")
                new = await _update(conn, cur["id"], {"status": "pending_approval",
                                                      "approval_task_id": uuid.UUID(task["id"]),
                                                      "submitted_at": datetime.now(timezone.utc)})
                await _audit(conn, org_id, p["maker_id"], "submitted", p["id"],
                             {"task_id": task["id"], "kind": task["kind"], "notified": task.get("notified", [])})
    except Exception:
        await _withdraw_task(pool, task["id"], "the proposal could not be submitted")
        raise
    await _publish(pool, org_id, "submitted", p["id"], {"task_id": task["id"]}, p["maker_id"])
    return new


async def _apply_outcome(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    """Apply a decided approval / review task. Idempotent."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_P} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if row is None or row["status"] != "pending_approval":
                return dict(row) if row else None
            row = dict(row)
            checker = uuid.UUID(str(actor_id))
            if row["maker_id"] is not None and checker == row["maker_id"]:
                raise MakerCheckerViolation(MAKER_CHECKER_RULE)
            now = datetime.now(timezone.utc)
            if outcome == "approve":
                new = await _update(conn, row["id"], {"status": "approved", "checker_id": checker, "decided_at": now,
                                                      "decision_note": note})
            else:
                new = await _update(conn, row["id"], {"status": "rejected", "checker_id": checker, "decided_at": now,
                                                      "decision_note": note})
            await _audit(conn, org_id, checker, "approved" if outcome == "approve" else "rejected", row["id"],
                         {"task_id": task["id"], "note": note})
    await _publish(pool, org_id, "approved" if outcome == "approve" else "rejected", row["id"],
                   {"task_id": task["id"]}, actor_id)
    if outcome == "approve":
        try:
            new = await execute(pool, org_id=org_id, proposal_id=row["id"], actor_id=actor_id)
        except Exception as exc:  # noqa: BLE001 — approved stands; POST /proposals/{id}/execute retries
            log.exception("routing approved proposal %s failed", row["id"])
            async with pool.acquire() as conn:
                new = {**await load(conn, org_id, row["id"]), "execution_error": str(exc)}
    return new


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply_outcome(pool, task["org_id"], task, outcome, actor_id, note)


HT.register_kind(TASK_APPROVAL, label="Rebalance proposal approval", outcomes=("approve", "reject"),
                 maker_checker=True, on_complete=_on_task_complete)
HT.register_kind(TASK_REVIEW, label="Rebalance proposal review", outcomes=("approve", "reject"),
                 maker_checker=False, on_complete=_on_task_complete, system_open=True)


async def sync_task_outcome(pool, *, org_id, proposal_id) -> None:
    async with pool.acquire() as conn:
        p = await load(conn, org_id, proposal_id)
        if p["status"] != "pending_approval" or not p["approval_task_id"]:
            return
        task = await HT.get_task(conn, org_id, p["approval_task_id"])
    if task["status"] == "completed" and task["outcome"] in ("approve", "reject") and task["completed_by"]:
        await _apply_outcome(pool, org_id, task, task["outcome"], task["completed_by"], task["note"])


async def decide(pool, *, org_id, actor_id, proposal_id, outcome: str, note: str | None = None) -> dict:
    """Approve / reject through the proposal's open task. Refused, each leaving
    it unchanged: 403 its maker; 409 not awaiting approval, or expired; 422 a
    rejection without a note."""
    if outcome not in ("approve", "reject"):
        raise ProposalError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise ProposalError("a rejection needs a note saying why")
    await sync_task_outcome(pool, org_id=org_id, proposal_id=proposal_id)
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        p = await load(conn, org_id, proposal_id)
    if p["status"] != "pending_approval":
        raise ProposalConflict(f"the proposal is {STATUS_LABELS[p['status']].lower()}, not awaiting approval")
    if p["maker_id"] is not None and actor["id"] == p["maker_id"]:
        raise MakerCheckerViolation(MAKER_CHECKER_RULE)
    if await _expire_if_due(pool, org_id, p):
        raise ProposalConflict("the proposal has expired — generate a new one on the current valuation")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=p["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise ProposalConflict(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await load(conn, org_id, proposal_id)
    return result


# ── Execute (rep2) ──────────────────────────────────────────────────────────
def _trade_line(t: dict):
    from services.custody.trading import LotInstruction, PriceGuidance, TradeLine

    ref_at = datetime.combine(t["price_as_of"], time(0, 0), tzinfo=timezone.utc) if t["price_as_of"] else None
    pg = PriceGuidance(kind="market", reference_price=t["price"], reference_at=ref_at) if t["price"] is not None \
        else PriceGuidance(kind="market")
    lots = [LotInstruction(tax_lot_id=x["lot_id"], quantity=Decimal(x["quantity"]))
            for x in (_loads(t["lots"]) or []) if x.get("lot_id")]
    flag = _loads(t["wash_sale_flag"])
    wash = _loads(t.get("wash_sale"))
    note = WS.note_of(wash) if wash else (f"[{flag['kind']}] {flag['note']}" if flag else None)
    return TradeLine(global_security_id=t["global_security_id"], side=t["side"],
                     quantity=None if t["quantity"] is None else Decimal(str(t["quantity"])),
                     notional=None if t["notional"] is None else Decimal(str(t["notional"])),
                     lot_instructions=lots, price_guidance=pg, wash_sale_notes=note)


async def execute(pool, *, org_id, proposal_id, actor_id) -> dict:
    """An APPROVED proposal → its trade list routed through rep2. Idempotent:
    a proposal whose instructions exist is left alone."""
    from services import execution as X
    from services.custody.trading import TradeList, route_trade_list

    async with pool.acquire() as conn:
        p = await load(conn, org_id, proposal_id)
        if p["status"] != "approved":
            raise ProposalConflict(f"only an approved proposal is executed (this one is {p['status']})")
        execution = _loads(p["execution"]) or {}
        if execution.get("instruction_ids"):
            return p
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await X.load_account(conn, org_id, p["account_id"])
        acct = await AM.load_account(conn, org_id, p["account_id"])
        lines = await trades_of(conn, p["id"])
        within = await _setting(conn, org_id, EXECUTE_WITHIN_SETTING, DEFAULT_EXECUTE_WITHIN, lo=0, hi=90,
                                integer=True)
    # rep6: a line wash-sale control deferred or blocked is never routed
    trades = [t for t in lines if WS.routable(_loads(t.get("wash_sale")))]
    held = len(lines) - len(trades)
    now = datetime.now(timezone.utc)
    if not trades:
        why = "nothing to trade" if not held else (f"nothing routed: all {held} line(s) are deferred or blocked by "
                                                   "wash-sale control")
        async with pool.acquire() as conn:
            async with conn.transaction():
                new = await _update(conn, p["id"], {"status": "executed", "executed_at": now,
                                                    "status_reason": why,
                                                    "execution": {"instruction_ids": [], "routing": None,
                                                                  "held_by_wash_sale": held}})
                await _audit(conn, org_id, actor["id"], "executed", p["id"], {"reason": why})
        return new
    advice = p["advice_only"]
    executor = "member" if (advice or acct["is_held_away"]) else "custodian_desk"
    tl = TradeList(account_id=p["account_id"], trades=[_trade_line(t) for t in trades], executor_type=executor,
                   execute_by=date.today() + timedelta(days=within), if_not_filled="ask", source="proposal",
                   source_ref=p["id"],
                   notes=(f"From rebalance proposal {p['id']} ({TRIGGER_LABELS[p['trigger']].lower()})"
                          + ("; ADVICE ONLY — the member decides and executes" if advice else "")))
    human_maker = p["maker_kind"] == "user"
    upstream = {"proposal_id": str(p["id"]), "maker_id": str(p["maker_id"]), "checker_id": str(p["checker_id"])} \
        if human_maker else None
    result = await route_trade_list(pool, org_id=org_id, actor_id=actor["id"], account=account, trade_list=tl,
                                    upstream=upstream)
    ids = list(result.instruction_ids)
    notes = []
    if held:
        notes.append(f"{held} line(s) deferred or blocked by wash-sale control were not routed")
    if not human_maker:
        notes.append("system proposal: no human maker, so the instructions are drafts made by the reviewer and "
                     "submitted to rep2's own approval (a second person)")
        for iid in ids:
            await X.submit(pool, org_id=org_id, instruction_id=iid, actor_id=actor["id"])
    async with pool.acquire() as conn:
        async with conn.transaction():
            for t, iid in zip(trades, ids):
                await conn.execute(f"UPDATE {T_T} SET valid_to = now() WHERE id = $1 AND valid_to IS NULL", t["id"])
                await conn.execute(
                    f"""INSERT INTO {T_T} (org_id, proposal_id, line_no, global_security_id, side, quantity, notional,
                            price, price_source, price_as_of, lots, est_st_gain, est_lt_gain, reason, wash_sale_flag,
                            wash_sale, instruction_id)
                        SELECT org_id, proposal_id, line_no, global_security_id, side, quantity, notional, price,
                               price_source, price_as_of, lots, est_st_gain, est_lt_gain, reason, wash_sale_flag,
                               wash_sale, $2::uuid FROM {T_T} WHERE id = $1""", t["id"], iid)
            new = await _update(conn, p["id"], {"execution": {
                "instruction_ids": ids, "routing": result.as_dict(), "executor_type": executor,
                "held_by_wash_sale": held,
                "starts": "approved" if human_maker else "pending_approval", "notes": notes,
                "routed_at": now.isoformat(), "routed_by": str(actor["id"])}})
            await _audit(conn, org_id, actor["id"], "routed", p["id"], {"instruction_ids": ids,
                                                                        "routed_to": result.routed_to,
                                                                        "reason": result.reason})
    await _publish(pool, org_id, "routed", p["id"], {"instruction_ids": ids}, actor["id"])
    return new


DONE_STATUSES = ("closed", "cancelled", "expired")


async def sync_executed(pool, *, org_id, proposal_id) -> dict:
    """approved → executed when every instruction is closed, cancelled or
    expired — the reason states the count of each (and any cancel reasons)."""
    async with pool.acquire() as conn:
        p = await load(conn, org_id, proposal_id)
        if p["status"] != "approved":
            return p
        ids = (_loads(p["execution"]) or {}).get("instruction_ids") or []
        if not ids:
            return p
        rows = await conn.fetch("SELECT id, status, cancel_reason, expiry_reason FROM public.execution_instructions "
                                "WHERE id = ANY($1::uuid[]) AND org_id = $2::uuid AND system_to IS NULL",
                                ids, str(org_id))
    if len(rows) != len(ids) or any(r["status"] not in DONE_STATUSES for r in rows):
        return p
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in DONE_STATUSES}
    why = [r["cancel_reason"] or r["expiry_reason"] for r in rows if r["status"] != "closed"]
    reason = ("every instruction is done: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n)
              + (f" ({'; '.join(w for w in why if w)})" if any(why) else ""))
    async with pool.acquire() as conn:
        async with conn.transaction():
            cur = await load(conn, org_id, proposal_id, lock=True)
            if cur["status"] != "approved":
                return cur
            new = await _update(conn, cur["id"], {"status": "executed", "executed_at": datetime.now(timezone.utc),
                                                  "status_reason": reason[:2000]})
            await _audit(conn, org_id, None, "executed", cur["id"], {"reason": reason, "counts": counts})
    await _publish(pool, org_id, "executed", proposal_id, {"counts": counts}, None)
    return new


# ── Triggers from outside ───────────────────────────────────────────────────
async def on_model_version_approved(pool, *, org_id, model_id, version_id) -> list[dict]:
    """'model.version_approved' → a system proposal for every account following
    the model live. Idempotent per (account, model version)."""
    out = []
    async with pool.acquire() as conn:
        links = [dict(r) for r in await conn.fetch(
            f"SELECT id, account_id FROM {AM.T_LINK} WHERE org_id = $1::uuid AND model_id = $2::uuid "
            "AND status = 'active' AND follow = 'live' AND valid_to IS NULL AND system_to IS NULL",
            str(org_id), str(model_id))]
        event_id = await conn.fetchval(
            "SELECT id FROM domain_events WHERE org_id = $1::uuid AND event_type = 'model.version_approved' "
            "AND source_id = $2::uuid", str(org_id), str(version_id))
    for ln in links:
        async with pool.acquire() as conn:
            done = await conn.fetchval(
                f"SELECT id FROM {T_P} WHERE account_id = $1::uuid AND trigger = 'model_change' "
                "AND model_version_id = $2::uuid AND archived_from_id IS NULL AND system_to IS NULL LIMIT 1",
                str(ln["account_id"]), str(version_id))
        if done:
            out.append({"account_id": str(ln["account_id"]), "proposal_id": str(done), "skipped": "already proposed"})
            continue
        try:
            p = await generate(pool, org_id=org_id, account_id=ln["account_id"], trigger="model_change",
                               maker_kind="system", trigger_ref={"event_id": str(event_id) if event_id else None,
                                                                 "model_version_id": str(version_id)})
            out.append({"account_id": str(ln["account_id"]), "proposal_id": str(p["id"])})
        except Exception as exc:  # noqa: BLE001 — one account never stops the others
            log.exception("model_change proposal failed for %s", ln["account_id"])
            out.append({"account_id": str(ln["account_id"]), "error": str(exc)})
    return out


async def scan_account(pool, *, org_id, account_id, today: date | None = None) -> dict:
    """The daily drift / cash-flow check for one account (its own valuation)."""
    today = today or date.today()
    async with pool.acquire() as conn:
        account = await AM.load_account(conn, org_id, account_id)
        link = await AM.active_link(conn, account["id"])
        if link is None or AM.is_closed(account, today):
            return {"action": "none", "reason": "no active link"}
        model = await AM.model_brief(conn, org_id, link["model_id"])
        if not model["live_version_id"]:
            return {"action": "none", "reason": "the model has no approved version"}
        cur = await open_proposal(conn, account["id"])
        hold = await AM.load_holdings(conn, org_id, account["id"])
        if cur and (cur["status"] == "approved" or cur["valuation_as_of"] == hold["valuation_as_of"]):
            return {"action": "none", "reason": f"an open proposal ({cur['status']}) already covers this valuation"}
        c = await compute(conn, org_id, account, link, model["live_version_id"], today=today)
        cf = await _setting(conn, org_id, CASH_FLOW_SETTING, DEFAULT_CASH_FLOW, lo=Decimal(0), hi=Decimal(1))
    r: E.BuildResult = c["result"]
    # the cash the account should hold: its cash floor, or the model's cash weight when that is higher
    cash_target = r.target.get(E.CASH, Decimal(0)) * r.account_value
    if r.drift_before > c["threshold"]:
        p = await generate(pool, org_id=org_id, account_id=account["id"], trigger="drift", maker_kind="system",
                           trigger_ref={"drift": str(r.drift_before), "threshold": str(c["threshold"])}, today=today)
        return {"action": "drift", "proposal_id": str(p["id"]), "drift": str(r.drift_before)}
    excess = r.cash - cash_target
    if r.account_value > 0 and excess > cf * r.account_value:
        p = await generate(pool, org_id=org_id, account_id=account["id"], trigger="cash_flow", maker_kind="system",
                           trigger_ref={"excess_cash": str(excess.quantize(E.CENT)), "threshold": str(cf)}, today=today)
        return {"action": "cash_flow", "proposal_id": str(p["id"])}
    return {"action": "none", "reason": f"drift {E.pct(r.drift_before)} is within {E.pct(c['threshold'])} and cash "
                                        "is within its threshold"}


async def release_fee_exclusions(pool, *, org_id) -> list[str]:
    """End a link's SECURITY fee exclusion once the account no longer holds the
    asset it names (the double-charge it prevented is gone)."""
    from services import fee_schedules as FS

    ended = []
    async with pool.acquire() as conn:
        links = [dict(r) for r in await conn.fetch(
            f"SELECT * FROM {AM.T_LINK} WHERE org_id = $1::uuid AND status = 'active' AND valid_to IS NULL "
            "AND system_to IS NULL AND cardinality(fee_exclusion_ids) > 0", str(org_id))]
    for ln in links:
        async with pool.acquire() as conn:
            async with conn.transaction():
                keep = []
                for x in ln["fee_exclusion_ids"]:
                    ex = await conn.fetchrow("SELECT basis_value FROM public.fee_exclusions WHERE id = $1 "
                                             "AND valid_to IS NULL AND system_to IS NULL", x)
                    if ex is None:
                        continue
                    held = await conn.fetchval(
                        "SELECT 1 FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid "
                        "AND asset_id = $3::uuid AND valid_to IS NULL AND system_to IS NULL AND COALESCE(quantity, 0) <> 0 "
                        "ORDER BY as_of_date DESC LIMIT 1", str(org_id), str(ln["account_id"]), ex["basis_value"])
                    if held:
                        keep.append(x)
                    else:
                        await FS.end_exclusion(conn, str(org_id), x)
                        ended.append(str(x))
                if len(keep) != len(ln["fee_exclusion_ids"]):
                    await AM._update(conn, ln["id"], {"fee_exclusion_ids": keep})
    return ended


async def org_tick(pool, *, org_id, today: date | None = None, include_test_orgs: bool = False) -> dict:
    """Every proposal step for one org (the caller set its RLS context).

    A test org (services.orgs) is skipped — no step runs — unless the caller
    passes ``include_test_orgs=True``: only a verify script, for its own org."""
    from services import orgs as ORGS

    today = today or date.today()
    out = {"expired": 0, "executed": 0, "model_change": [], "harvest": [], "scans": [], "fee_exclusions_ended": [],
           "loss_bank": None, "errors": []}
    async with pool.acquire() as conn:
        if not include_test_orgs and await ORGS.is_test_org(conn, org_id):
            return {**out, "skipped": "test org (organizations.is_test) — schedulers never act on one"}
        open_rows = [dict(r) for r in await conn.fetch(
            f"SELECT * FROM {T_P} WHERE org_id = $1::uuid AND system_to IS NULL AND status = ANY($2::text[])",
            str(org_id), list(OPEN_STATUSES))]
        events = [dict(r) for r in await conn.fetch(
            "SELECT source_id, payload FROM domain_events WHERE org_id = $1::uuid AND event_type = "
            "'model.version_approved' AND occurred_at >= now() - make_interval(days => $2)", str(org_id),
            EVENT_SWEEP_DAYS)]
        accounts = [r["account_id"] for r in await conn.fetch(
            f"SELECT account_id FROM {AM.T_LINK} WHERE org_id = $1::uuid AND status = 'active' AND valid_to IS NULL "
            "AND system_to IS NULL ORDER BY account_id", str(org_id))]
    for p in open_rows:
        try:
            if p["status"] == "approved":
                if (await sync_executed(pool, org_id=org_id, proposal_id=p["id"]))["status"] == "executed":
                    out["executed"] += 1
            elif await _expire_if_due(pool, org_id, p):
                out["expired"] += 1
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"proposal {p['id']}: {exc}")
    for ev in events:
        payload = _loads(ev["payload"]) or {}
        if payload.get("model_id"):
            try:
                out["model_change"] += await on_model_version_approved(
                    pool, org_id=org_id, model_id=payload["model_id"], version_id=ev["source_id"])
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"event {ev['source_id']}: {exc}")
    # rep6 — the realized gains / losses into the loss bank, then the daily harvest scan BEFORE the drift
    # scan. Harvesting is opt-in per link (harvest_enabled) and NEVER supersedes an open proposal: an account
    # with one is skipped ('an open proposal exists'); drift / cash_flow / model_change behave as in rep5
    from services import harvest as HV
    from services import loss_bank as LB

    try:
        out["loss_bank"] = await LB.ingest(pool, org_id=org_id, today=today)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"loss bank: {exc}")
    for a in accounts:
        try:
            out["harvest"].append({"account_id": str(a), **await HV.scan_account(pool, org_id=org_id, account_id=a,
                                                                                 today=today)})
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"harvest {a}: {exc}")
    for a in accounts:
        try:
            out["scans"].append({"account_id": str(a), **await scan_account(pool, org_id=org_id, account_id=a,
                                                                             today=today)})
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"account {a}: {exc}")
    try:
        out["fee_exclusions_ended"] = await release_fee_exclusions(pool, org_id=org_id)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"fee exclusions: {exc}")
    return out


async def proposals_tick(pool, *, today: date | None = None) -> dict:
    """The scheduler entrypoint's step (after the holdings refresh and rep2's
    execute-by handling): every org with an active link, each in its own RLS
    context. Test orgs are never enumerated (services.orgs)."""
    from services import orgs as ORGS
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            # never a test org (services.orgs): a crashed verify's fixture accounts are not scanned
            orgs = [r["org_id"] for r in await conn.fetch(
                f"SELECT org_id FROM (SELECT DISTINCT org_id FROM {AM.T_LINK} WHERE status = 'active' "
                "AND valid_to IS NULL AND system_to IS NULL UNION SELECT DISTINCT org_id FROM " + T_P +
                " WHERE system_to IS NULL AND status = ANY($1::text[])) x WHERE " + ORGS.not_test_org_sql("x.org_id"),
                list(OPEN_STATUSES))]
    finally:
        reset_rls_context(tokens)
    out = {}
    for org in orgs:
        tokens = set_rls_context(str(org), False)
        try:
            out[str(org)] = await org_tick(pool, org_id=org, today=today)
        except Exception as exc:  # noqa: BLE001 — one org never stops the others
            out[str(org)] = {"errors": [str(exc)]}
        finally:
            reset_rls_context(tokens)
    return out


# ── Out ─────────────────────────────────────────────────────────────────────
def trade_out(t: dict) -> dict:
    return {"id": str(t["id"]), "line_no": t["line_no"], "global_security_id": str(t["global_security_id"]),
            "security": {"name": t.get("security_name"), "ticker": t.get("ticker")}, "side": t["side"],
            "quantity": _num(t["quantity"]), "notional": _num(t["notional"]), "price": _num(t["price"]),
            "price_source": t["price_source"], "price_as_of": _iso(t["price_as_of"]), "lots": _loads(t["lots"]) or [],
            "est_st_gain": _num(t["est_st_gain"]), "est_lt_gain": _num(t["est_lt_gain"]), "reason": t["reason"],
            "wash_sale_flag": _loads(t["wash_sale_flag"]), "wash_sale": _loads(t.get("wash_sale")),
            "routable": WS.routable(_loads(t.get("wash_sale"))),
            "instruction_id": str(t["instruction_id"]) if t["instruction_id"] else None}


def proposal_out(p: dict, account: dict | None = None) -> dict:
    return {"id": str(p["id"]), "account_id": str(p["account_id"]), "link_id": str(p["link_id"]),
            "model_id": str(p["model_id"]), "model_version_id": str(p["model_version_id"]), "trigger": p["trigger"],
            "trigger_ref": _loads(p["trigger_ref"]) or {}, "status": p["status"], "advice_only": p["advice_only"],
            "valuation_as_of": _iso(p["valuation_as_of"]), "account_value": _num(p["account_value"]),
            "cash_value": _num(p["cash_value"]), "withdrawal_amount": _num(p["withdrawal_amount"]),
            "target_weights": _loads(p["target_weights"]) or [], "before_weights": _loads(p["before_weights"]) or [],
            "after_weights": _loads(p["after_weights"]) or [], "drift_before": _num(p["drift_before"]),
            "drift_after": _num(p["drift_after"]), "est_st_gain": _num(p["est_st_gain"]),
            "est_lt_gain": _num(p["est_lt_gain"]), "gain_budget": _num(p["gain_budget"]),
            "gain_realized_ytd": _num(p["gain_realized_ytd"]), "warnings": _loads(p["warnings"]) or [],
            "sell_later": _loads(p["sell_later"]) or [], "expires_at": _iso(p["expires_at"]),
            "maker_kind": p["maker_kind"], "maker_id": str(p["maker_id"]) if p["maker_id"] else None,
            "checker_id": str(p["checker_id"]) if p["checker_id"] else None,
            "approval_task_id": str(p["approval_task_id"]) if p["approval_task_id"] else None,
            "submitted_at": _iso(p["submitted_at"]), "decision_note": p["decision_note"],
            "decided_at": _iso(p["decided_at"]), "status_reason": p["status_reason"],
            "superseded_by_id": str(p["superseded_by_id"]) if p["superseded_by_id"] else None,
            "execution": _loads(p["execution"]) or {}, "executed_at": _iso(p["executed_at"]),
            "created_at": _iso(p["created_at"]),
            "account": {"account_number_masked": account["account_number_masked"],
                        "custodian_code": account["custodian_code"], "is_held_away": account["is_held_away"],
                        "is_discretionary": account["is_discretionary"]} if account else None}


async def list_proposals(conn, org_id, *, account_id=None, status=None, limit: int = 200) -> list[dict]:
    args: list = [str(org_id)]
    sql = (f"SELECT p.*, a.account_number_masked, a.custodian_code, a.is_held_away, a.is_discretionary "
           f"FROM {T_P} p JOIN public.accounts a ON a.id = p.account_id AND a.system_to IS NULL AND a.valid_to IS NULL "
           "WHERE p.org_id = $1::uuid AND p.system_to IS NULL")
    if account_id:
        args.append(_uuid(account_id, "account"))
        sql += f" AND p.account_id = ${len(args)}::uuid"
    if status:
        if status == "open":
            args.append(list(OPEN_STATUSES))
            sql += f" AND p.status = ANY(${len(args)}::text[])"
        elif status in STATUS_LABELS:
            args.append(status)
            sql += f" AND p.status = ${len(args)}"
        else:
            raise ProposalError(f"unknown status {status!r}")
    args.append(max(1, min(int(limit), 1000)))
    sql += f" ORDER BY p.created_at DESC LIMIT ${len(args)}"
    return [proposal_out(dict(r), dict(r)) for r in await conn.fetch(sql, *args)]


async def detail(conn, org_id, proposal_id) -> dict:
    p = await load(conn, org_id, proposal_id)
    account = await AM.load_account(conn, org_id, p["account_id"])
    out = proposal_out(p, account)
    out["trades"] = [trade_out(t) for t in await trades_of(conn, p["id"])]
    model = await conn.fetchrow("SELECT name FROM portfolio.models WHERE id = $1 AND system_to IS NULL "
                                "ORDER BY valid_to NULLS FIRST LIMIT 1", p["model_id"])
    out["model_name"] = model["name"] if model else None
    tasks = await HT.list_tasks(conn, org_id, subject_type=SUBJECT, subject_id=p["id"], limit=20)
    out["tasks"] = tasks
    ids = (_loads(p["execution"]) or {}).get("instruction_ids") or []
    out["instructions"] = [{"id": str(r["id"]), "status": r["status"], "side": r["side"],
                            "quantity": _num(r["quantity"]), "notional": _num(r["notional"])}
                           for r in await conn.fetch("SELECT id, status, side, quantity, notional FROM "
                                                     "public.execution_instructions WHERE id = ANY($1::uuid[]) "
                                                     "AND system_to IS NULL", ids)] if ids else []
    plan = await conn.fetchrow(f"SELECT * FROM {T_PLAN} WHERE proposal_id = $1 AND system_to IS NULL "
                               "ORDER BY created_at DESC LIMIT 1", p["id"])
    out["transition_plan"] = plan_row_out(dict(plan)) if plan else None
    return out


def plan_row_out(r: dict) -> dict:
    return {"id": str(r["id"]), "valuation_as_of": _iso(r["valuation_as_of"]), "holdings": _loads(r["holdings"]) or [],
            "plan_by_year": _loads(r["plan_by_year"]) or [], "total_embedded_gain": _num(r["total_embedded_gain"]),
            "gain_budget": _num(r["gain_budget"]), "years_to_complete": r["years_to_complete"],
            "created_at": _iso(r["created_at"]), "current": r["valid_to"] is None}


async def current_plan(conn, org_id, account_id) -> dict | None:
    r = await conn.fetchrow(f"SELECT * FROM {T_PLAN} WHERE account_id = $1::uuid AND org_id = $2::uuid "
                            "AND valid_to IS NULL AND system_to IS NULL", str(account_id), str(org_id))
    return plan_row_out(dict(r)) if r else None


def actions_for(p: dict, *, can_write: bool, can_approve: bool, user_id, task_open: bool) -> list[str]:
    """The SERVER's list of actions this caller may take now; the screen renders
    a control only for an action listed here."""
    acts: list[str] = []
    s = p["status"]
    if can_write and s == "draft" and p["maker_kind"] == "user" and p["maker_id"] == str(user_id):
        acts.append("submit")
    if can_write and s == "approved":
        acts.append("refresh_execution")
        if not (p.get("execution") or {}).get("instruction_ids"):
            acts.append("execute")
    if can_approve and s == "pending_approval" and task_open and p["maker_id"] != str(user_id):
        acts += ["approve", "reject"]
    return acts


async def accounts_overview(conn, org_id, *, model_id=None, status=None, q=None) -> list[dict]:
    """Portfolio → Accounts: each open account with its model, last drift, last
    proposal and open instructions."""
    from services import execution as X

    args: list = [str(org_id)]
    sql = f"""
        SELECT a.id, a.account_number_masked, a.custodian_code, a.registration_type, a.is_held_away,
               a.is_discretionary, l.id AS link_id, l.model_id, l.status AS link_status, m.name AS model_name,
               pl.id AS pending_link_id,
               lp.id AS last_proposal_id, lp.status AS last_proposal_status, lp.trigger AS last_proposal_trigger,
               lp.created_at AS last_proposal_at, lp.drift_before AS last_drift, lp.valuation_as_of AS last_valuation,
               (SELECT count(*) FROM public.execution_instructions i WHERE i.account_id = a.id
                  AND i.system_to IS NULL AND i.status = ANY($2::text[])) AS open_instructions
        FROM public.accounts a
        LEFT JOIN {AM.T_LINK} l ON l.account_id = a.id AND l.status = 'active' AND l.valid_to IS NULL
             AND l.system_to IS NULL
        LEFT JOIN {AM.T_LINK} pl ON pl.account_id = a.id AND pl.status = 'pending' AND pl.valid_to IS NULL
             AND pl.system_to IS NULL
        LEFT JOIN portfolio.models m ON m.id = l.model_id AND m.valid_to IS NULL AND m.system_to IS NULL
        LEFT JOIN LATERAL (SELECT * FROM {T_P} p WHERE p.account_id = a.id AND p.system_to IS NULL
                           ORDER BY p.created_at DESC LIMIT 1) lp ON true
        WHERE a.org_id = $1::uuid AND a.system_to IS NULL AND a.valid_to IS NULL AND a.closed_on IS NULL"""
    args.append(list(X.OPEN_STATUSES))
    if model_id:
        args.append(_uuid(model_id, "model"))
        sql += f" AND l.model_id = ${len(args)}::uuid"
    if status == "following":
        sql += " AND l.id IS NOT NULL"
    elif status == "not_following":
        sql += " AND l.id IS NULL"
    elif status == "open_proposal":
        sql += " AND lp.status = ANY(ARRAY['draft', 'pending_approval', 'approved'])"
    elif status:
        raise ProposalError("filter is following, not_following or open_proposal")
    if q:
        args.append(f"%{q.strip()}%")
        sql += f" AND a.account_number_masked ILIKE ${len(args)}"
    sql += " ORDER BY a.custodian_code, a.account_number_masked LIMIT 1000"
    out = []
    for r in await conn.fetch(sql, *args):
        out.append({"id": str(r["id"]), "account_number_masked": r["account_number_masked"],
                    "custodian_code": r["custodian_code"], "registration_type": r["registration_type"],
                    "is_held_away": r["is_held_away"], "is_discretionary": r["is_discretionary"],
                    "model": {"id": str(r["model_id"]), "name": r["model_name"]} if r["model_id"] else None,
                    "pending_change": r["pending_link_id"] is not None,
                    "last_drift": _num(r["last_drift"]), "last_valuation": _iso(r["last_valuation"]),
                    "last_proposal": {"id": str(r["last_proposal_id"]), "status": r["last_proposal_status"],
                                      "trigger": r["last_proposal_trigger"],
                                      "created_at": _iso(r["last_proposal_at"])} if r["last_proposal_id"] else None,
                    "open_instructions": int(r["open_instructions"])})
    return out


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"triggers": pairs(TRIGGER_LABELS), "statuses": pairs(STATUS_LABELS), "open_statuses": list(OPEN_STATUSES),
            "warning_codes": pairs(WARNING_LABELS), "price_sources": pairs(PRICE_SOURCE_LABELS),
            "plan_actions": pairs(PLAN_ACTION_LABELS), **WS.vocabularies(), "account_filters": [
                {"key": "following", "label": "Following a model"},
                {"key": "not_following", "label": "Not following a model"},
                {"key": "open_proposal", "label": "With an open proposal"}], **AM.vocabularies()}
