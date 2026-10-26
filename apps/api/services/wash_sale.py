"""Household wash-sale CONTROL (rep6) — replaces rep5's flag. Used by every
proposal: rebalance, transition and harvest.

THE WINDOW — 61 days
──────────────────────────────────────────────────────────────────────────────
A loss sale on day L is washed by a purchase of a substantially identical
security on any day from L − 30 to L + 30 (both edges IN the window). Day 31
either side is clear: a buy is clear from L + 31; a loss sale is clear from
B + 31 after the last matching buy B. A pending trade (an open instruction, a
proposal awaiting approval or approved and not yet routed) has no date yet: it
counts over every day it could still happen (today → its execute-by / expiry),
and its window ends 30 days after the last of them.

WHO — every account of the household (rep5's ``household_accounts``: the same
household, the same primary entity, or a shared owner) — the spouse and every
IRA / Roth included, and (rep7) every HELD-AWAY account: its trades are
portfolio.transactions like any other (source 'held_away'), so an IRA held
elsewhere makes rule (c) block — plus the household's DECLARED outside trades.
A declared trade that a held-away transaction matches (held_away_records.
matched_declared_id) counts ONCE: the held-away row stands, the declaration is
skipped.

  A LOSS opens a window only where it is a deductible loss: a sale in a taxable
  account (or one whose tax status is unknown — treated as taxable). A loss
  realized INSIDE an IRA / Roth is not deductible, so it opens no window. A
  BUY counts in every account: an IRA / Roth buy of a substantially identical
  security inside a taxable loss's window disallows that loss PERMANENTLY
  (Rev. Rul. 2008-5 — the basis does not move into the IRA).

"SUBSTANTIALLY IDENTICAL" = the same security, OR a pair in
public.substitution_denials (active, either direction). Nothing is ever
inferred; an APPROVED substitute is not identical (unless a denial says so —
a denial overrides a set).

THE RULES — the outcome of every trade: clear | replaced | deferred | blocked
──────────────────────────────────────────────────────────────────────────────
  (a) a BUY of S inside a household loss window of S (or a denied pair) →
      replaced by the next APPROVED substitute that is itself clear; else
      deferred to the first clear date (or, by the org's setting, the cash is
      kept — also deferred, saying so);
  (b) a LOSS SALE of S when S (or a denied pair) was bought in the household
      within 30 days (or a pending buy may still happen) → its loss lots are
      deferred to the first clear date and the account's other lots are sold
      instead where the plan wants them (rebalance_engine.plan_sales);
  (c) a BUY into an IRA / Roth inside a taxable loss window, with no clear
      substitute → BLOCKED: the loss would be lost for good;
  (d) a planned loss sale (an open instruction, a pending proposal, this
      proposal's own) reserves its forward 30 days: later proposals see it.
A deferred or blocked line is recorded on the proposal and never reaches an
instruction. A sale whose cost basis is unknown cannot be shown to be a loss:
it is not held, but the matches are recorded on it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

log = logging.getLogger(__name__)

WINDOW_DAYS = 30
OUTCOMES = ("clear", "replaced", "deferred", "blocked")
OUTCOME_LABELS = {"clear": "Clear", "replaced": "Replaced by an approved substitute",
                  "deferred": "Deferred until it is clear", "blocked": "Blocked"}
TAX_CLASS_LABELS = {"taxable": "Taxable", "tax_advantaged": "IRA / Roth (tax-advantaged)",
                    "unknown": "Tax status unknown (treated as taxable)"}
SOURCE_LABELS = {"transaction": "A recorded trade", "held_away": "A trade in a held-away account (its source and as-of "
                 "are on the account)", "fill": "A reported fill", "instruction": "An open instruction",
                 "proposal": "A pending proposal", "declared_outside": "A declared outside trade",
                 "this_proposal": "This proposal"}
FALLBACK_SETTING = "replication.tax.wash_buy_fallback"
FALLBACKS = ("defer", "cash")
ADVANTAGED_STATUS = ("tax_deferred", "tax_exempt", "tax_free", "tax-deferred", "tax-exempt", "deferred", "exempt")
ADVANTAGED_REG = ("ira", "roth", "401k", "401(k)", "403b", "403(b)", "457", "hsa", "529", "sep", "simple", "keogh")


def tax_class(account: dict) -> str:
    """'taxable' | 'tax_advantaged' | 'unknown' from the account's own
    tax_status (and its registration when the status says nothing)."""
    ts = (account.get("tax_status") or "").strip().lower()
    rt = (account.get("registration_type") or "").strip().lower()
    if ts in ADVANTAGED_STATUS:
        return "tax_advantaged"
    if ts == "taxable":
        return "taxable"
    if any(k in rt for k in ADVANTAGED_REG):
        return "tax_advantaged"
    return "unknown"


@dataclass(frozen=True)
class Event:
    """A household trade that counts in windows. A realized trade has
    start == end; a pending one spans every day it could still happen."""
    key: str
    side: str
    start: date
    end: date
    loss: bool | None
    account_id: str | None
    account: str
    tax_class: str
    source: str
    ref: str | None = None
    quantity: Decimal | None = None

    def out(self) -> dict:
        return {"security_id": self.key, "side": self.side, "from": self.start.isoformat(),
                "to": self.end.isoformat(), "pending": self.start != self.end, "loss": self.loss,
                "account": self.account, "account_id": self.account_id, "tax_class": self.tax_class,
                "source": self.source, "ref": self.ref,
                "quantity": None if self.quantity is None else str(self.quantity)}


@dataclass
class Context:
    today: date
    events: list[Event]
    denials: dict[frozenset, dict]
    substitutes: dict[str, list[dict]]
    labels: dict[str, str]
    fallback: str = "defer"

    def label(self, key: str) -> str:
        return self.labels.get(key, key)


def identical(a: str, b: str, ctx: Context) -> bool:
    return a == b or frozenset((a, b)) in ctx.denials


def _overlaps(e: Event, lo: date, hi: date) -> bool:
    return e.start <= hi and e.end >= lo


def window(ctx: Context) -> tuple[date, date]:
    return ctx.today - timedelta(days=WINDOW_DAYS), ctx.today + timedelta(days=WINDOW_DAYS)


def clear_from(events: list[Event]) -> date:
    """The first clear day after these events: 31 days after the last."""
    return max(e.end for e in events) + timedelta(days=WINDOW_DAYS + 1)


def loss_windows(key: str, ctx: Context, extra: list[Event] | tuple = ()) -> list[Event]:
    """Deductible (or possibly deductible) household loss sales of S or a
    denied pair whose 61-day window covers today."""
    lo, hi = window(ctx)
    return [e for e in list(ctx.events) + list(extra)
            if e.side == "sell" and e.loss is not False and e.tax_class != "tax_advantaged"
            and identical(e.key, key, ctx) and _overlaps(e, lo, hi)]


def matching_buys(key: str, ctx: Context, extra: list[Event] | tuple = ()) -> list[Event]:
    """Household buys of S or a denied pair within 30 days either side of today
    (a pending buy may still happen)."""
    lo, hi = window(ctx)
    return [e for e in list(ctx.events) + list(extra)
            if e.side == "buy" and identical(e.key, key, ctx) and _overlaps(e, lo, hi)]


def _why_identical(a: str, b: str, ctx: Context) -> str:
    if a == b:
        return "the same security"
    d = ctx.denials.get(frozenset((a, b))) or {}
    return f"a denied pair ({d.get('rule', 'denial')}: {d.get('reason') or 'substantially identical'})"


def _matches(events: list[Event], key: str, ctx: Context) -> list[dict]:
    return [{**e.out(), "label": ctx.label(e.key), "identical_because": _why_identical(e.key, key, ctx)}
            for e in sorted(events, key=lambda x: (x.end, x.start, x.source))]


def sale_restriction(key: str, *, account_class: str, ctx: Context) -> dict | None:
    """Rule (b): the loss lots of S wait while a household buy of S (or a
    denied pair) sits within 30 days. None when the sale is clear (or when the
    selling account is tax-advantaged — its loss is not deductible)."""
    if account_class == "tax_advantaged":
        return None
    buys = matching_buys(key, ctx)
    if not buys:
        return None
    until = clear_from(buys)
    forfeit = [e for e in buys if e.tax_class == "tax_advantaged"]
    last = max(buys, key=lambda e: e.end)
    reason = (f"wash sale: the household {'may buy' if last.start != last.end else 'bought'} "
              f"{ctx.label(last.key)} ({last.account}, {last.end.isoformat()}) within 30 days — selling at a loss now "
              f"would have the loss disallowed")
    if forfeit:
        reason += (" PERMANENTLY, because the purchase is in an IRA / Roth (Rev. Rul. 2008-5)")
    reason += f"; the loss lots are deferred until {until.isoformat()} (the first clear day)"
    return {"until": until, "reason": reason, "matches": _matches(buys, key, ctx), "forfeit": bool(forfeit)}


def evaluate_buy(key: str, *, account_class: str, ctx: Context, own: list[Event] | tuple = (),
                 fallback: str | None = None) -> dict:
    """Rules (a) and (c) for a BUY of S in an account of ``account_class``.
    ``own`` = this proposal's own loss sales (rule d). Returns the result."""
    fallback = fallback or ctx.fallback
    wins = loss_windows(key, ctx, own)
    if not wins:
        return {"outcome": "clear", "rule": None, "security_id": key, "label": ctx.label(key), "matches": [],
                "reason": "no household loss sale of this security, or of a pair denied with it, within 30 days"}
    until = clear_from(wins)
    matches = _matches(wins, key, ctx)
    tried = []
    for cand in ctx.substitutes.get(key, []):
        r = cand["key"]
        if identical(r, key, ctx):
            tried.append({"security_id": r, "label": cand["label"], "why_not": "a denial pairs it with "
                          f"{ctx.label(key)} — a denial overrides the substitution set"})
            continue
        w2 = loss_windows(r, ctx, own)
        if w2:
            tried.append({"security_id": r, "label": cand["label"], "why_not": "it has a household loss window of "
                          f"its own until {clear_from(w2).isoformat()}"})
            continue
        return {"outcome": "replaced", "rule": "a", "security_id": key, "label": ctx.label(key),
                "replacement": {"security_id": r, "label": cand["label"], "reason": cand["reason"],
                                "set_id": cand.get("set_id"), "set_version": cand.get("version_no"),
                                "rank": cand.get("rank")},
                "matches": matches, "tried": tried,
                "reason": f"wash sale: the household sold {ctx.label(key)} at a loss within 30 days — bought "
                          f"{cand['label']} instead (an approved substitute, not substantially identical)"}
    if account_class == "tax_advantaged":
        last = max(wins, key=lambda e: e.end)
        return {"outcome": "blocked", "rule": "c", "security_id": key, "label": ctx.label(key), "until": None,
                "matches": matches, "tried": tried,
                "reason": f"blocked: buying {ctx.label(key)} in this IRA / Roth now would permanently forfeit the "
                          f"household's loss on the sale in {last.account} ({last.end.isoformat()}) — an IRA purchase "
                          f"inside the 30-day window disallows the loss for good (Rev. Rul. 2008-5). No approved "
                          f"substitute is clear. It may be bought from {until.isoformat()}."}
    if fallback == "cash":
        why = f"the cash is kept (the organisation's wash-sale setting); it may be bought from {until.isoformat()}"
    else:
        why = f"deferred until {until.isoformat()}, the first clear day"
    return {"outcome": "deferred", "rule": "a", "security_id": key, "label": ctx.label(key),
            "until": until.isoformat(), "fallback": fallback, "matches": matches, "tried": tried,
            "reason": f"wash sale: the household sold {ctx.label(key)} (or a pair denied with it) at a loss within 30 "
                      f"days and no approved substitute is clear — {why}"}


def own_loss_events(trades, account: dict, today: date) -> list[Event]:
    """This proposal's own loss sales, as events (rule d inside a proposal)."""
    out = []
    for t in trades:
        if t.side != "sell":
            continue
        loss = any(lot.get("est_gain") is not None and lot["est_gain"] < 0 for lot in t.lots) or (
            (t.est_st_gain + t.est_lt_gain) < 0)
        if loss:
            out.append(Event(t.key, "sell", today, today, True, str(account["id"]), account["account_number_masked"],
                             tax_class(account), "this_proposal", None, t.quantity))
    return out


def _sell_result(t, ctx: Context, account_class: str) -> dict:
    """A sale line that goes ahead: clear, with any recent buys recorded (an
    unknown-cost lot cannot be shown to be a loss, so it is not held)."""
    buys = matching_buys(t.key, ctx) if account_class != "tax_advantaged" else []
    unknown = [lot for lot in t.lots if lot.get("est_gain") is None]
    res = {"outcome": "clear", "rule": None, "security_id": t.key, "label": t.label, "matches": [],
           "reason": "no household buy of this security, or of a pair denied with it, within 30 days"}
    if buys:
        res["matches"] = _matches(buys, t.key, ctx)
        res["reason"] = ("only lots at a gain (or of unknown cost) are sold now; any loss lots wait for the first "
                         "clear day (see the deferred line)")
        if unknown:
            res["reason"] += "; a lot of unknown cost cannot be shown to be a loss — it is not held"
    elif account_class == "tax_advantaged":
        res["reason"] = "a sale inside an IRA / Roth realizes no deductible loss — no window applies"
    return res


def _qty(dollars: Decimal, price: Decimal, fractional: bool) -> Decimal:
    unit = Decimal("0.00000001") if fractional else Decimal(1)
    return (dollars / price / unit).to_integral_value(rounding=ROUND_DOWN) * unit


def control(result, *, account: dict, ctx: Context, prices: dict, fractional: bool) -> list[tuple]:
    """Every line of a built proposal with its result: [(TradeOut, result)].
    Buys are evaluated (rules a, c, d); a replaced buy becomes a buy of the
    substitute for the same dollars (whole shares unless fractional; by
    notional when the substitute has no price); the engine's wash-held loss
    lots become deferred sale lines."""
    from services import rebalance_engine as E

    cls = tax_class(account)
    own = own_loss_events(result.trades, account, ctx.today)
    lines: list[tuple] = []
    for t in result.trades:
        if t.side == "sell":
            lines.append((t, _sell_result(t, ctx, cls)))
            continue
        res = evaluate_buy(t.key, account_class=cls, ctx=ctx, own=own)
        if res["outcome"] != "replaced":
            lines.append((t, res))
            continue
        dollars = t.notional if t.notional is not None else t.quantity * t.price
        rk = res["replacement"]["security_id"]
        rl = res["replacement"]["label"]
        reason = f"{t.reason}; replaced by {rl} (an approved substitute for {t.label}) — wash-sale control"
        hit = prices.get(rk)
        if hit is not None:
            price, as_of, src = hit
            q = _qty(dollars, price, fractional)
            if q > 0:
                lines.append((E.TradeOut(rk, rl, "buy", q, None, price, src, as_of, [], E.ZERO, E.ZERO, reason),
                              {**res, "original": {"security_id": t.key, "label": t.label}}))
                continue
        n = dollars.quantize(E.CENT, rounding=ROUND_DOWN)
        lines.append((E.TradeOut(rk, rl, "buy", None, n, None, "none", None, [], E.ZERO, E.ZERO,
                                 reason + f" — no price for it, so it is bought by amount (${n})"),
                      {**res, "original": {"security_id": t.key, "label": t.label}}))
    for held in getattr(result, "wash_held", None) or []:
        lots = held["lots"]
        q = sum((x["quantity"] for x in lots), E.ZERO)
        st = sum((x["est_gain"] for x in lots if x["est_gain"] is not None and x["term"] != "long"), E.ZERO)
        lt = sum((x["est_gain"] for x in lots if x["est_gain"] is not None and x["term"] == "long"), E.ZERO)
        r = held["restriction"]
        t = E.TradeOut(held["key"], held["label"], "sell", q, None, held["price"], held["price_source"],
                       held["price_as_of"], lots, st, lt,
                       f"Loss lots held back by wash-sale control — sell on or after {r['until'].isoformat()}")
        lines.append((t, {"outcome": "deferred", "rule": "b", "security_id": held["key"], "label": held["label"],
                          "until": r["until"].isoformat(), "forfeit": r["forfeit"], "matches": r["matches"],
                          "reason": r["reason"]}))
    return lines


def routable(result: dict | None) -> bool:
    """Only clear and replaced lines become instructions (a legacy line with no
    result — rep5's flag era — routes as before)."""
    return result is None or result.get("outcome") in ("clear", "replaced")


def note_of(result: dict | None) -> str | None:
    """The instruction's wash-sale note for a routed line."""
    if not result or (result.get("outcome") == "clear" and not result.get("matches")):
        return None
    return f"[wash-sale {result['outcome']}] {result.get('reason', '')}"[:4000]


# ── Loading the household context ───────────────────────────────────────────
async def household_subjects(conn, org_id, accounts: list[dict]) -> list[tuple[str, str]]:
    """The tax subjects of the household's accounts (for declared outside trades)."""
    out: set[tuple[str, str]] = set()
    ids = [str(a["id"]) for a in accounts]
    if not ids:
        return []
    for r in await conn.fetch("SELECT household_id, primary_entity_id FROM public.accounts WHERE id = ANY($1::uuid[]) "
                              "AND org_id = $2::uuid AND system_to IS NULL AND valid_to IS NULL", ids, str(org_id)):
        if r["household_id"]:
            out.add(("household", str(r["household_id"])))
        out.add(("entity", str(r["primary_entity_id"])))
    return sorted(out)


async def _setting(conn, org_id, key, default):
    from services.org_settings import get_setting_with_origin
    import uuid as _u

    try:
        v, is_default = await get_setting_with_origin(conn, _u.UUID(str(org_id)), key)
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the stated default
        return default
    return default if v is None or is_default else v


async def load_context(conn, org_id, account: dict, today: date, *, exclude_proposal_ids=()) -> Context:
    """Everything the rules read for one account's household, as of ``today``."""
    from services import execution as X
    from services import rebalance_proposals as RP

    accts = await RP.household_accounts(conn, org_id, account)
    by_id = {str(a["id"]): a for a in accts}
    ids = list(by_id)
    lo = today - timedelta(days=WINDOW_DAYS)
    events: list[Event] = []

    def ev(key, side, start, end, loss, acct_id, source, ref, qty):
        a = by_id.get(str(acct_id)) if acct_id else None
        events.append(Event(str(key), side, start, end, loss, str(acct_id) if acct_id else None,
                            a["account_number_masked"] if a else "?", tax_class(a) if a else "unknown", source,
                            str(ref) if ref else None, None if qty is None else Decimal(str(qty))))

    # recorded trades (a sale's loss from the lots it closed; none recorded → unknown)
    for r in await conn.fetch(
            """SELECT x.id, x.transaction_type_code, x.trade_date, x.quantity, p.account_id, a.global_security_id,
                      (SELECT sum(tl.realized_gain_loss) FROM portfolio.tax_lots tl WHERE tl.closing_transaction_id = x.id
                         AND tl.system_to IS NULL AND tl.valid_to IS NULL) AS realized
               FROM portfolio.transactions x
               JOIN portfolio.positions p ON p.id = x.position_id
               JOIN portfolio.assets a ON a.id = p.asset_id
               WHERE x.org_id = $1::uuid AND p.account_id = ANY($2::uuid[]) AND x.trade_date >= $3 AND x.trade_date <= $4
                 AND x.transaction_type_code IN ('buy', 'sell') AND x.valid_to IS NULL AND x.system_to IS NULL
                 AND a.global_security_id IS NOT NULL""", str(org_id), ids, lo, today):
        loss = None if (r["transaction_type_code"] == "buy" or r["realized"] is None) else Decimal(str(r["realized"])) < 0
        held = bool((by_id.get(str(r["account_id"])) or {}).get("is_held_away"))
        ev(r["global_security_id"], r["transaction_type_code"], r["trade_date"], r["trade_date"],
           loss if r["transaction_type_code"] == "sell" else None, r["account_id"],
           "held_away" if held else "transaction", r["id"], r["quantity"])
    # reported fills (dated by the fill)
    for r in await conn.fetch(
            """SELECT f.id, f.quantity, f.price, f.filled_at, f.lots, i.side, i.account_id, i.global_security_id
               FROM public.execution_fills f JOIN public.execution_instructions i ON i.id = f.instruction_id
                    AND i.system_to IS NULL
               WHERE f.org_id = $1::uuid AND i.account_id = ANY($2::uuid[]) AND f.valid_to IS NULL
                 AND f.system_to IS NULL AND f.filled_at >= $3""",
            str(org_id), ids, datetime.combine(lo, datetime.min.time(), tzinfo=timezone.utc)):
        d = r["filled_at"].date()
        loss = None
        if r["side"] == "sell":
            loss = await _lots_loss(conn, RP._loads(r["lots"]) or [], Decimal(str(r["price"])))
        ev(r["global_security_id"], r["side"], d, d, loss, r["account_id"], "fill", r["id"], r["quantity"])
    # open instructions — a pending trade, from today to its execute-by
    for r in await conn.fetch(
            "SELECT id, side, quantity, notional, reference_price, execute_by, account_id, global_security_id "
            "FROM public.execution_instructions WHERE org_id = $1::uuid AND account_id = ANY($2::uuid[]) "
            "AND system_to IS NULL AND status = ANY($3::text[])", str(org_id), ids, list(X.OPEN_STATUSES)):
        end = max(r["execute_by"], today)
        loss = None
        if r["side"] == "sell":
            lots = [{"tax_lot_id": str(x["tax_lot_id"]), "quantity": str(x["quantity"])} for x in await conn.fetch(
                "SELECT tax_lot_id, quantity FROM public.execution_instruction_lots WHERE instruction_id = $1 "
                "AND valid_to IS NULL AND system_to IS NULL", r["id"])]
            loss = await _lots_loss(conn, lots, None if r["reference_price"] is None
                                    else Decimal(str(r["reference_price"])))
        ev(r["global_security_id"], r["side"], today, end, loss, r["account_id"], "instruction", r["id"],
           r["quantity"])
    # pending proposals not yet routed (their clear / replaced lines only)
    within = await RP._setting(conn, org_id, RP.EXECUTE_WITHIN_SETTING, RP.DEFAULT_EXECUTE_WITHIN, lo=0, hi=90,
                               integer=True)
    for r in await conn.fetch(
            f"""SELECT p.id, p.account_id, p.expires_at, p.execution, t.global_security_id, t.side, t.quantity, t.lots,
                       t.est_st_gain, t.est_lt_gain, t.wash_sale
                FROM {RP.T_P} p JOIN {RP.T_T} t ON t.proposal_id = p.id AND t.valid_to IS NULL AND t.system_to IS NULL
                WHERE p.org_id = $1::uuid AND p.account_id = ANY($2::uuid[]) AND p.system_to IS NULL
                  AND p.status IN ('pending_approval', 'approved') AND NOT (p.id = ANY($3::uuid[]))""",
            str(org_id), ids, [str(x) for x in exclude_proposal_ids]):
        if (RP._loads(r["execution"]) or {}).get("instruction_ids"):
            continue          # routed: its instructions count instead
        if not routable(RP._loads(r["wash_sale"])):
            continue          # a deferred / blocked line will not trade
        end = max(r["expires_at"].date(), today) + timedelta(days=within)
        loss = None
        if r["side"] == "sell":
            lots = RP._loads(r["lots"]) or []
            gains = [Decimal(x["est_gain"]) for x in lots if x.get("est_gain") is not None]
            loss = any(g < 0 for g in gains) or (Decimal(str(r["est_st_gain"])) + Decimal(str(r["est_lt_gain"]))) < 0
        ev(r["global_security_id"], r["side"], today, end, loss, r["account_id"], "proposal", r["id"], r["quantity"])
    # declared outside trades of the household's subjects
    subs = await household_subjects(conn, org_id, accts)
    if subs:
        for r in await conn.fetch(
                """SELECT id, account_description, account_tax_class, global_security_id, side, trade_date, quantity, loss
                   FROM public.declared_outside_trades d
                   WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL AND trade_date >= $2
                     AND (subject_type || ':' || subject_id::text) = ANY($3::text[])
                     AND NOT EXISTS (SELECT 1 FROM public.held_away_records h WHERE h.matched_declared_id = d.id
                                     AND h.valid_to IS NULL AND h.system_to IS NULL)""",
                str(org_id), lo - timedelta(days=WINDOW_DAYS), [f"{t}:{i}" for t, i in subs]):
            loss = None if r["side"] == "buy" else {"yes": True, "no": False}.get(r["loss"])
            events.append(Event(str(r["global_security_id"]), r["side"], r["trade_date"], r["trade_date"], loss, None,
                                f"outside: {r['account_description']}", r["account_tax_class"], "declared_outside",
                                str(r["id"]), None if r["quantity"] is None else Decimal(str(r["quantity"]))))
    denials = await active_denials(conn, org_id)
    subs_map = await approved_substitutes(conn, org_id)
    keys = {e.key for e in events} | {k for p in denials for k in p} | set(subs_map) | {
        c["key"] for v in subs_map.values() for c in v}
    labels = await security_labels(conn, keys)
    for v in subs_map.values():
        for c in v:
            c["label"] = labels.get(c["key"], c["key"])
    fb = await _setting(conn, org_id, FALLBACK_SETTING, "defer")
    return Context(today=today, events=events, denials=denials, substitutes=subs_map, labels=labels,
                   fallback=fb if fb in FALLBACKS else "defer")


async def _lots_loss(conn, lots: list[dict], price: Decimal | None) -> bool | None:
    """Is a sale of these lots at ``price`` a loss? None when it cannot be told."""
    if not lots or price is None:
        return None
    total = Decimal(0)
    for x in lots:
        r = await conn.fetchrow("SELECT quantity, adjusted_cost, original_cost FROM portfolio.tax_lots WHERE id = $1::uuid "
                                "AND system_to IS NULL ORDER BY valid_to NULLS FIRST LIMIT 1", str(x["tax_lot_id"]))
        if r is None or r["quantity"] in (None, 0):
            return None
        cost = r["adjusted_cost"] if r["adjusted_cost"] is not None else r["original_cost"]
        if cost is None:
            return None
        q = Decimal(str(x["quantity"]))
        total += q * (price - Decimal(str(cost)) / Decimal(str(r["quantity"])))
    return total < 0


async def active_denials(conn, org_id) -> dict[frozenset, dict]:
    return {frozenset((str(r["security_a_id"]), str(r["security_b_id"]))): {"id": str(r["id"]), "rule": r["rule"],
                                                                            "reason": r["reason"]}
            for r in await conn.fetch(
                "SELECT id, security_a_id, security_b_id, rule, reason FROM public.substitution_denials "
                "WHERE org_id = $1::uuid AND status = 'active' AND valid_to IS NULL AND system_to IS NULL",
                str(org_id))}


async def approved_substitutes(conn, org_id) -> dict[str, list[dict]]:
    """ONLY approved, live substitution sets — {security: [candidate, ...] in rank order}."""
    out: dict[str, list[dict]] = {}
    for r in await conn.fetch(
            """SELECT s.id AS set_id, s.global_security_id, s.version_no, c.rank, c.replacement_security_id, c.reason
               FROM public.substitution_sets s
               JOIN public.substitution_set_candidates c ON c.set_id = s.id AND c.valid_to IS NULL AND c.system_to IS NULL
               WHERE s.org_id = $1::uuid AND s.status = 'approved' AND s.valid_to IS NULL AND s.system_to IS NULL
               ORDER BY s.global_security_id, c.rank""", str(org_id)):
        out.setdefault(str(r["global_security_id"]), []).append(
            {"key": str(r["replacement_security_id"]), "label": str(r["replacement_security_id"]),
             "reason": r["reason"], "rank": r["rank"], "set_id": str(r["set_id"]), "version_no": r["version_no"]})
    return out


async def security_labels(conn, keys) -> dict[str, str]:
    keys = sorted({str(k) for k in keys if k})
    if not keys:
        return {}
    rows = await conn.fetch(
        """SELECT g.id, g.name, (SELECT id_value FROM portfolio.securities_global_identifiers t
                   WHERE t.global_security_id = g.id AND t.id_type = 'ticker' AND t.system_to IS NULL
                     AND t.valid_to IS NULL LIMIT 1) AS ticker
           FROM portfolio.securities_global g WHERE g.id = ANY($1::uuid[]) AND g.system_to IS NULL""", keys)
    from services.account_models import label_for

    return {str(r["id"]): label_for(r["name"], r["ticker"]) for r in rows}


async def open_windows(conn, org_id, account: dict, today: date) -> list[dict]:
    """The household's open wash-sale windows, for the screens: every loss
    sale (deductible or possibly) and every buy whose window has not ended."""
    ctx = await load_context(conn, org_id, account, today)
    out = []
    lo, _ = window(ctx)
    for e in ctx.events:
        if e.end < lo:
            continue
        if e.side == "sell" and (e.loss is False or e.tax_class == "tax_advantaged"):
            continue
        out.append({**e.out(), "label": ctx.label(e.key),
                    "kind": "loss_sale" if e.side == "sell" else "buy",
                    "blocks": ("buys of this security or a denied pair" if e.side == "sell"
                               else "loss sales of this security or a denied pair"),
                    "window_ends": (e.end + timedelta(days=WINDOW_DAYS)).isoformat(),
                    "clear_from": (e.end + timedelta(days=WINDOW_DAYS + 1)).isoformat()})
    return sorted(out, key=lambda x: (x["window_ends"], x["label"]))


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"wash_sale_outcomes": pairs(OUTCOME_LABELS), "tax_classes": pairs(TAX_CLASS_LABELS),
            "wash_sale_sources": pairs(SOURCE_LABELS),
            "wash_sale_fallbacks": [{"key": "defer", "label": "Defer the buy to the first clear day"},
                                    {"key": "cash", "label": "Keep the cash"}]}
