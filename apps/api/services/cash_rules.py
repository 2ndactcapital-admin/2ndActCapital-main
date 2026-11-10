"""CASH06 — CASH RULES, stage A: standing rules that keep a family's cash where its policy wants it — built as WORKFLOW
TRIGGERS on the existing engine. Every firing DRAFTS payment instructions (cash07) and submits each for a PERSON's
approval. A rule never approves, sends or moves money (stage A; stages B / C need Altruist write access and counsel).

THE FOUR RULE TYPES (Mercury's, kept — docs/cash/CASH_TREASURY_DESIGN_v1.md §4)
──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  distribute          % of the source's balance, a fixed amount, or % of each incoming credit, to 1–5 same-owner
                      destinations
  maintain_target     when the source is BELOW the target, draw the gap from the supporting account; when ABOVE,
                      sweep the excess to it
  recurring           a fixed amount to one destination on a schedule
  redirect_incoming   sweep % of each incoming credit to one destination (event-driven)
Frequencies: daily, business_days, weekly, monthly (scheduled triggers, 10:00 Eastern) and on_incoming (an event
trigger on ``cash.incoming_credit``, published by the feed import — :func:`publish_incoming_credits`).

A RULE = ONE workflow definition + ONE trigger (owned by the rule's OWNER) + versioned rows in ``public.cash_rules``.
The trigger fires the engine's normal run (run history, holds and alerts come from the engine); its one Service Task
is ``cash.rule_fire`` → :func:`fire`, which reads the rule's ACTIVE version, claims (rule, period | incoming
transaction) ONCE in ``public.cash_rule_firings``, evaluates (:func:`evaluate`, pure) and calls ONE service verb per
draft: ``services.payments.draft_and_submit`` — as the rule OWNER, so the owner is the instruction's maker and the
cash07 FLOOR makes its checker someone else (S30 Task 8: a rule's maker is the trigger owner).

THE AMOUNTS (Decimal, never float — documented rounding)
  * % legs (of the balance or of an incoming credit) are rounded DOWN to the cent: a rule never moves more than its
    percentage, and the legs of one firing never sum past the balance (Σ % ≤ 100).
  * maintain_target BELOW draws (target − balance) rounded UP to the cent (it reaches the target); ABOVE sweeps
    (balance − target) rounded DOWN (it never sweeps below the target).
  * A leg under $0.01 drafts nothing (a note, not a hold).
  * SHORTFALL: when one payment source cannot cover every leg it pays, NOTHING is drafted from it — a hold with the
    reason (no partial overdraft).
  * STALE / MISSING BALANCE: a balance read more than one business day before the firing day (or none) → hold.
  * CAPS: the rule's per-firing $ cap and per-day instruction count (each ≤ the org ceiling, settings
    ``cash.rules.max_amount_per_firing`` / ``cash.rules.max_instructions_per_day``); the excess is HELD with an alert.
  * VALUE DATE: the firing day when it is a business day; a daily / business-day rule SKIPS a non-business day; a
    weekly / monthly / on-incoming rule uses the next business day and SAYS so in the draft's reason (never silently).
Every hold alerts the owner and every org admin through the engine's own ``workflow_autonomy.hold_alerts``.

GOVERNANCE (S30 item type ``cash_rule`` — a FLOOR, class 'transfer': a rule that drafts money movement is money-movement
configuration). Creating, editing (a NEW version — the active one keeps running until the new one is approved),
activating and RESUMING are maker-checker through a human task (kind ``cash_rule_approval``); the owner is a maker of
every version, so the owner never activates their own rule. Pausing and retiring are tightening — immediate.

AUTONOMY (S29b): ``cash.rule_fire`` is ``eligible_with_cap`` — it DRAFTS + SUBMITS only (approve / send / reconcile stay
``never``, unregistered). The engine runs it unattended only with the org switch on and the rule's workflow version
opted in after its shadow period; the kill switches stop every rule. It is WORKFLOW-ONLY: never offered to the
assistant and refused by /assistant/confirm (services.action_registry), and :func:`fire` refuses any call that is not
the engine running this rule's own trigger.

NO NETWORK, NO MODEL, NO FULL ACCOUNT NUMBER: accounts appear by their stored mask (last four) only.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from services import cash_activity as CA
from services import human_tasks as HT
from services import payments as PS
from services import sod
from services import us_business_days as BD
from services import workflow_autonomy as WA
from services.bitemporal import archive_live_row
from services.money_text import fixed
from services.portfolio_cash import CASH_ASSET_TYPE

log = logging.getLogger(__name__)

T = "public.cash_rules"
T_FIRE = "public.cash_rule_firings"
TASK_KIND = "cash_rule_approval"
SUBJECT = "cash_rule"
EVENT_SOURCE = "cash_rule"
ACTION_KEY = "cash.rule_fire"
STEP_KEY = "cash_rule_fire"
EVENT_TYPE = "cash.incoming_credit"
FEED_SOURCE = "portfolio_transaction"
DEFINITION_PREFIX = "system:cash_rule:"
INITIATE = PS.INITIATE
APPROVE = PS.APPROVE
READ_PERMISSIONS = PS.READ_PERMISSIONS
TZ = "America/New_York"
FIRE_HOUR = 10
MAX_DESTINATIONS = 5
STALE_BUSINESS_DAYS = 1
CENT = Decimal("0.01")
HUNDRED = Decimal(100)
ZERO = Decimal(0)
SETTING_MAX_AMOUNT = "cash.rules.max_amount_per_firing"
SETTING_MAX_PER_DAY = "cash.rules.max_instructions_per_day"
DEFAULT_MAX_AMOUNT = Decimal("250000.00")
DEFAULT_MAX_PER_DAY = 5
DISCLOSURE_KEY = "cash.rules"
RULES_DISCLOSURE = ("Rules prepare payment instructions for approval; no money moves without a person approving it.")

TYPES = (("distribute", "Distribute across accounts"), ("maintain_target", "Maintain a target balance"),
         ("recurring", "Scheduled recurring transfer"), ("redirect_incoming", "Redirect incoming funds"))
BASES = (("pct_balance", "% of the balance"), ("fixed", "Fixed amount"), ("pct_incoming", "% of each incoming credit"))
CONDITIONS = (("below", "When below the target — draw the gap from the supporting account"),
              ("above", "When above the target — sweep the excess to the supporting account"))
FREQUENCIES = (("daily", "Daily (drafts on business days only)"), ("business_days", "Every business day"),
               ("weekly", "Weekly"), ("monthly", "Monthly"), ("on_incoming", "On each incoming credit"))
WEEKDAYS = ((1, "Monday"), (2, "Tuesday"), (3, "Wednesday"), (4, "Thursday"), (5, "Friday"))
STATUS_LABELS = {"draft": "Draft — not submitted", "pending_approval": "Awaiting a second person's approval",
                 "active": "Active", "paused": "Paused", "superseded": "Replaced by a newer version",
                 "retired": "Retired"}
OUTCOME_LABELS = {"firing": "Started, not finished — a person must look (never retried)",
                  "drafted": "Drafted for approval", "held": "Held", "partly_held": "Drafted in part; the rest held",
                  "skipped": "Nothing to draft"}
HOLD_KINDS = {"shortfall": "Shortfall", "stale": "Stale balance", "no_balance": "No balance", "cap": "Per-firing cap",
              "per_day": "Per-day limit", "refused": "Refused by the payment checks"}
TERMS = ("name", "rule_type", "source_account_id", "destinations", "basis", "condition", "target_amount",
         "supporting_account_id", "frequency", "day_of_week", "day_of_month", "rail", "cap_per_firing", "max_per_day")
EDITABLE = TERMS
STATUS_IMMUTABLE = ("superseded", "retired")


class RuleError(ValueError):
    """A refused definition or request (422) — every reason, together."""

    def __init__(self, reasons):
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__(" · ".join(self.reasons))


class RuleNotFound(LookupError):
    pass


class RuleConflict(Exception):
    pass


class MakerCheckerViolation(PermissionError):
    pass


# ── time (injected by the verify; payments' clock otherwise) ────────────────────────────────────────────────────────
_CLOCK: dict = {"fn": None}


def set_clock(fn) -> None:
    _CLOCK["fn"] = fn


def today() -> date:
    return _CLOCK["fn"]() if _CLOCK["fn"] else PS.today()


# ═══ Pure: amounts, periods, schedules ═════════════════════════════════════════════════════════════════════════════
def cent_down(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_DOWN)


def cent_up(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_UP)


def money(v) -> str | None:
    return fixed(v.quantize(CENT)) if isinstance(v, Decimal) else fixed(v)


def _dec(v, what: str) -> Decimal:
    if isinstance(v, (float, bool)):
        raise RuleError(f"{what} is a decimal string (e.g. \"2500.00\"), never a binary float")
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v).strip())
    except (InvalidOperation, AttributeError):
        raise RuleError(f"{what} must be a number") from None
    if not d.is_finite():
        raise RuleError(f"{what} must be a number")
    return d


def parse_pct(v, what: str = "a percentage") -> Decimal:
    d = _dec(v, what)
    if d <= 0 or d > HUNDRED:
        raise RuleError(f"{what} is more than 0 and at most 100")
    if d != d.quantize(Decimal("0.0001")):
        raise RuleError(f"{what} has at most four decimals")
    return d


def parse_cents(v, what: str) -> Decimal:
    d = _dec(v, what)
    if d < CENT:
        raise RuleError(f"{what} must be at least $0.01")
    if d != d.quantize(CENT):
        raise RuleError(f"{what} has at most two decimals (cents)")
    if d >= Decimal("1e15"):
        raise RuleError(f"{what} is too large")
    return d


def period_key(frequency: str, on: date, txn_id=None) -> str:
    """The idempotency key of one firing: a day, an ISO week, a month — or the incoming transaction."""
    if frequency == "on_incoming":
        if not txn_id:
            raise RuleError("an incoming-credit firing names its transaction")
        return f"txn:{uuid.UUID(str(txn_id))}"
    if frequency in ("daily", "business_days"):
        return f"d:{on.isoformat()}"
    if frequency == "weekly":
        y, w, _ = on.isocalendar()
        return f"w:{y}-W{w:02d}"
    if frequency == "monthly":
        return f"m:{on.year:04d}-{on.month:02d}"
    raise RuleError(f"unknown frequency {frequency!r}")


def cron_for(frequency: str, day_of_week=None, day_of_month=None) -> str | None:
    """The trigger's schedule (Eastern time); None for an event-driven rule. The holiday rule is the evaluator's."""
    if frequency == "daily":
        return f"0 {FIRE_HOUR} * * *"
    if frequency == "business_days":
        return f"0 {FIRE_HOUR} * * 1-5"
    if frequency == "weekly":
        return f"0 {FIRE_HOUR} * * {int(day_of_week)}"
    if frequency == "monthly":
        return f"0 {FIRE_HOUR} {int(day_of_month)} * *"
    return None


def firing_day(run_context: dict | None, fallback: date) -> date:
    """The firing's own day in Eastern time: the scheduled occurrence when there is one, else ``fallback``."""
    occ = (run_context or {}).get("scheduled_occurrence")
    if occ:
        try:
            ts = datetime.fromisoformat(str(occ))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(ZoneInfo(TZ)).date()
        except ValueError:
            pass
    return fallback


def not_business_text(d: date) -> str:
    why = BD.holiday_name(d)
    if why:
        return f"a holiday ({why.replace('_', ' ')})"
    return "a Saturday" if d.weekday() == 5 else "a Sunday" if d.weekday() == 6 else "not a business day"


def business_days_after(a: date, b: date) -> int:
    """Business days strictly after ``a`` up to and including ``b`` (0 when b <= a)."""
    return PS.business_days_between(a, b)


@dataclass
class Balance:
    available: Decimal | None
    as_of: date | None


@dataclass
class Draft:
    source_account_id: str
    destination_account_id: str
    amount: Decimal
    rail: str
    value_date: date
    reason: str

    def out(self) -> dict:
        return {"source_account_id": self.source_account_id, "destination_account_id": self.destination_account_id,
                "amount": money(self.amount), "rail": self.rail, "value_date": self.value_date.isoformat(),
                "reason": self.reason}


@dataclass
class Hold:
    kind: str
    reason: str
    amount: Decimal | None = None
    source_account_id: str | None = None
    destination_account_id: str | None = None

    def out(self) -> dict:
        return {"kind": self.kind, "kind_label": HOLD_KINDS.get(self.kind, self.kind), "reason": self.reason,
                "amount": money(self.amount) if self.amount is not None else None,
                "source_account_id": self.source_account_id, "destination_account_id": self.destination_account_id}


@dataclass
class Evaluation:
    on: date
    period_key: str | None
    value_date: date | None = None
    drafts: list = field(default_factory=list)
    holds: list = field(default_factory=list)
    skipped: str | None = None
    notes: list = field(default_factory=list)

    @property
    def outcome(self) -> str:
        if self.drafts and self.holds:
            return "partly_held"
        if self.holds:
            return "held"
        if self.drafts:
            return "drafted"
        return "skipped"

    @property
    def reason(self) -> str | None:
        if self.holds:
            return " · ".join(h.reason for h in self.holds)
        if not self.drafts:
            return self.skipped or (" · ".join(self.notes) if self.notes else "nothing to draft")
        return None

    def out(self) -> dict:
        return {"on": self.on.isoformat(), "period_key": self.period_key, "outcome": self.outcome,
                "outcome_label": OUTCOME_LABELS[self.outcome],
                "value_date": self.value_date.isoformat() if self.value_date else None,
                "drafts": [d.out() for d in self.drafts], "holds": [h.out() for h in self.holds],
                "skipped": self.skipped, "notes": list(self.notes), "reason": self.reason,
                "total": money(sum((d.amount for d in self.drafts), ZERO))}


def payment_sources(rule: dict) -> list[str]:
    """The account(s) a rule's legs are PAID FROM (for the rail, authority and shortfall checks)."""
    if rule["rule_type"] == "maintain_target":
        return [str(rule["supporting_account_id"] if rule["condition"] == "below" else rule["source_account_id"])]
    return [str(rule["source_account_id"])]


def read_accounts(rule: dict) -> list[str]:
    """The accounts whose balance the evaluation reads."""
    out = [str(rule["source_account_id"])]
    if rule["rule_type"] == "maintain_target":
        out.append(str(rule["supporting_account_id"]))
    return out


def evaluate(rule: dict, balances: dict, incoming: list, on: date, *, drafted_today: int = 0,
             ceiling_amount: Decimal | None = None, ceiling_per_day: int | None = None,
             labels: dict | None = None) -> Evaluation:
    """PURE and deterministic: (rule, balances {account_id: Balance}, incoming credits [{transaction_id, account_id,
    amount}], the firing day) → zero or more drafts with amounts and reasons, and / or holds with reasons."""
    lab = labels or {}

    def name(a) -> str:
        return lab.get(str(a)) or "the account"

    freq = rule["frequency"]
    txn = None
    if freq == "on_incoming":
        txn = next((c.get("transaction_id") for c in incoming or [] if c.get("transaction_id")), None)
    ev = Evaluation(on=on, period_key=period_key(freq, on, txn) if (freq != "on_incoming" or txn) else None)
    if rule.get("status") != "active":
        ev.skipped = f"the rule is {STATUS_LABELS.get(rule.get('status'), rule.get('status')).lower()} — it does not fire"
        return ev
    if freq in ("daily", "business_days") and not BD.is_business_day(on):
        ev.skipped = (f"{on.isoformat()} is {not_business_text(on)} — this rule drafts on business days only; money "
                      "moves on business days")
        return ev
    vd = on if BD.is_business_day(on) else BD.next_business_day(on)
    ev.value_date = vd
    if vd != on:
        ev.notes.append(f"{on.isoformat()} is {not_business_text(on)} — the value date is the next business day, "
                        f"{vd.isoformat()}")
    src = str(rule["source_account_id"])
    rail = rule["rail"]
    when = "" if vd == on else f" (value date {vd.isoformat()}, the next business day)"

    # 1. every balance the evaluation reads must exist and be fresh (≤ 1 business day old)
    for acct in read_accounts(rule):
        b = balances.get(acct)
        if b is None or b.available is None or b.as_of is None:
            ev.holds.append(Hold("no_balance", f"no cash balance on record for {name(acct)} — nothing drafted",
                                 source_account_id=acct))
        elif business_days_after(b.as_of, on) > STALE_BUSINESS_DAYS:
            ev.holds.append(Hold("stale", f"the cash balance of {name(acct)} is as of {b.as_of.isoformat()}, more than "
                                          f"{STALE_BUSINESS_DAYS} business day before {on.isoformat()} — nothing "
                                          "drafted on a stale balance", source_account_id=acct))
    if ev.holds:
        return ev

    # 2. the legs (source, destination, amount, reason)
    legs: list[tuple[str, str, Decimal, str]] = []
    t, basis = rule["rule_type"], rule.get("basis")
    dests = rule.get("destinations") or []
    if freq == "on_incoming":
        credits = [c for c in incoming or [] if str(c.get("account_id")) == src and Decimal(str(c["amount"])) > 0]
        if not credits:
            ev.skipped = f"no incoming credit to {name(src)} in this firing"
            return ev
        base = sum((Decimal(str(c["amount"])) for c in credits), ZERO)
        for d in dests:
            pct = Decimal(str(d["pct"]))
            legs.append((src, str(d["account_id"]), cent_down(base * pct / HUNDRED),
                         f"{fixed(pct)}% of the incoming credit of {money(base)} to {name(src)}{when}"))
    elif t == "maintain_target":
        bal = balances[src].available
        target = Decimal(str(rule["target_amount"]))
        sup = str(rule["supporting_account_id"])
        if rule["condition"] == "below":
            if bal >= target:
                ev.skipped = f"{name(src)} holds {money(bal)}, at or above its target of {money(target)} — nothing to draw"
                return ev
            legs.append((sup, src, cent_up(target - bal),
                         f"{name(src)} holds {money(bal)}, below its target of {money(target)}: draw the gap from "
                         f"{name(sup)}{when}"))
        else:
            if bal <= target:
                ev.skipped = f"{name(src)} holds {money(bal)}, at or below its target of {money(target)} — nothing to sweep"
                return ev
            legs.append((src, sup, cent_down(bal - target),
                         f"{name(src)} holds {money(bal)}, above its target of {money(target)}: sweep the excess to "
                         f"{name(sup)}{when}"))
    elif basis == "pct_balance":
        bal = balances[src].available
        for d in dests:
            pct = Decimal(str(d["pct"]))
            legs.append((src, str(d["account_id"]), cent_down(max(bal, ZERO) * pct / HUNDRED),
                         f"{fixed(pct)}% of {name(src)}'s balance of {money(bal)}{when}"))
    else:  # fixed (distribute fixed, recurring)
        for d in dests:
            legs.append((src, str(d["account_id"]), Decimal(str(d["amount"])),
                         f"a fixed {money(Decimal(str(d['amount'])))} from {name(src)}{when}"))

    # 3. a leg under one cent drafts nothing
    kept = []
    for s_, d_, amt, why in legs:
        if amt < CENT:
            ev.notes.append(f"{name(d_)}: the amount rounds to under $0.01 — nothing to draft")
        else:
            kept.append((s_, d_, amt, why))

    # 4. shortfall per payment source: NO partial overdraft
    by_src: dict[str, Decimal] = {}
    for s_, _d, amt, _w in kept:
        by_src[s_] = by_src.get(s_, ZERO) + amt
    short = {s_ for s_, total in by_src.items() if total > balances[s_].available}
    funded = []
    for s_, d_, amt, why in kept:
        if s_ in short:
            ev.holds.append(Hold("shortfall", f"{name(s_)} holds {money(balances[s_].available)} available; this "
                                              f"firing needs {money(by_src[s_])} from it — nothing is drafted from it "
                                              "(no partial overdraft)", amt, s_, d_))
        else:
            funded.append((s_, d_, amt, why))

    # 5. the per-firing $ cap (the rule's, never above the org ceiling): the excess is held
    cap = Decimal(str(rule["cap_per_firing"]))
    if ceiling_amount is not None:
        cap = min(cap, ceiling_amount)
    running = ZERO
    capped = []
    for s_, d_, amt, why in funded:
        room = cap - running
        if room >= amt:
            capped.append((s_, d_, amt, why))
            running += amt
        elif room >= CENT:
            capped.append((s_, d_, room, f"{why} — capped at the per-firing limit of {money(cap)}"))
            ev.holds.append(Hold("cap", f"{money(amt - room)} to {name(d_)} is over the per-firing cap of {money(cap)} "
                                        "— held", amt - room, s_, d_))
            running = cap
        else:
            ev.holds.append(Hold("cap", f"{money(amt)} to {name(d_)} is over the per-firing cap of {money(cap)} — held",
                                 amt, s_, d_))

    # 6. the per-day instruction count
    per_day = int(rule["max_per_day"])
    if ceiling_per_day is not None:
        per_day = min(per_day, int(ceiling_per_day))
    allowed = max(per_day - int(drafted_today), 0)
    for i, (s_, d_, amt, why) in enumerate(capped):
        if i < allowed:
            ev.drafts.append(Draft(s_, d_, amt, rail, vd, why))
        else:
            ev.holds.append(Hold("per_day", f"{money(amt)} to {name(d_)}: the rule's limit of {per_day} instruction(s) "
                                            f"a day is reached ({drafted_today} drafted earlier today) — held",
                                 amt, s_, d_))
    return ev


# ═══ Pure: the definition ═══════════════════════════════════════════════════════════════════════════════════════
def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise RuleError(f"{what} is not a valid id") from None


def normalise(data: dict) -> dict:
    """The shape of a rule — every reason gathered (the database's CHECKs repeat the essentials)."""
    reasons: list[str] = []
    out: dict = {}
    name = (data.get("name") or "").strip()
    if not name or len(name) > 120:
        reasons.append("a rule needs a name (at most 120 characters)")
    out["name"] = name
    t = data.get("rule_type")
    if t not in dict(TYPES):
        raise RuleError([f"rule_type is one of {[k for k, _ in TYPES]}"] + reasons)
    out["rule_type"] = t
    try:
        out["source_account_id"] = _uuid(data.get("source_account_id"), "the source account")
    except RuleError as exc:
        reasons += exc.reasons
    freq = data.get("frequency")
    if t == "redirect_incoming":
        freq = "on_incoming"
    if freq not in dict(FREQUENCIES):
        reasons.append(f"frequency is one of {[k for k, _ in FREQUENCIES]}")
    out["frequency"] = freq
    out["day_of_week"] = out["day_of_month"] = None
    if freq == "weekly":
        try:
            dow = int(data.get("day_of_week"))
            if not 1 <= dow <= 5:
                raise ValueError
            out["day_of_week"] = dow
        except (TypeError, ValueError):
            reasons.append("a weekly rule names a weekday (1 = Monday … 5 = Friday)")
    if freq == "monthly":
        try:
            dom = int(data.get("day_of_month"))
            if not 1 <= dom <= 28:
                raise ValueError
            out["day_of_month"] = dom
        except (TypeError, ValueError):
            reasons.append("a monthly rule names a day of the month (1 … 28)")
    rail = data.get("rail")
    if rail not in PS.RAIL_KEYS:
        reasons.append(f"rail is one of {list(PS.RAIL_KEYS)}")
    out["rail"] = rail
    try:
        out["cap_per_firing"] = parse_cents(data.get("cap_per_firing"), "the per-firing cap")
    except RuleError as exc:
        reasons += exc.reasons
    try:
        mpd = int(data.get("max_per_day") if data.get("max_per_day") is not None else 1)
        if not 1 <= mpd <= 20:
            raise ValueError
        out["max_per_day"] = mpd
    except (TypeError, ValueError):
        reasons.append("the per-day limit is a whole number of instructions from 1 to 20")
    out["basis"] = out["condition"] = out["target_amount"] = out["supporting_account_id"] = None
    dests_in = data.get("destinations") or []
    dests: list[dict] = []
    if t == "maintain_target":
        if data.get("condition") not in dict(CONDITIONS):
            reasons.append("a target rule's condition is below or above")
        out["condition"] = data.get("condition")
        try:
            tgt = _dec(data.get("target_amount"), "the target balance")
            if tgt < 0 or tgt != tgt.quantize(CENT):
                raise RuleError("the target balance is $0.00 or more, in cents")
            out["target_amount"] = tgt
        except RuleError as exc:
            reasons += exc.reasons
        try:
            out["supporting_account_id"] = _uuid(data.get("supporting_account_id"), "the supporting account")
        except RuleError as exc:
            reasons += exc.reasons
        if out.get("supporting_account_id") and out["supporting_account_id"] == out.get("source_account_id"):
            reasons.append("the supporting account is a different account")
        if freq == "on_incoming":
            reasons.append("a target rule runs on a schedule")
        if dests_in:
            reasons.append("a target rule has no destinations (it moves money to or from its supporting account)")
    else:
        basis = {"recurring": "fixed", "redirect_incoming": "pct_incoming"}.get(t, data.get("basis"))
        if basis not in dict(BASES):
            reasons.append(f"basis is one of {[k for k, _ in BASES]}")
        out["basis"] = basis
        if t == "distribute" and (basis == "pct_incoming") != (freq == "on_incoming"):
            reasons.append("% of incoming fires on each incoming credit (frequency on_incoming), and only it does")
        if t == "recurring" and freq == "on_incoming":
            reasons.append("a recurring transfer runs on a schedule")
        if not isinstance(dests_in, list) or not dests_in:
            reasons.append("name at least one destination")
            dests_in = []
        if t in ("recurring", "redirect_incoming") and len(dests_in) != 1:
            reasons.append(f"a {dict(TYPES)[t].lower()} has exactly one destination")
        if len(dests_in) > MAX_DESTINATIONS:
            reasons.append(f"at most {MAX_DESTINATIONS} destinations")
        seen = set()
        for i, d in enumerate(dests_in[:MAX_DESTINATIONS]):
            d = d if isinstance(d, dict) else {}
            try:
                acct = _uuid(d.get("account_id"), f"destination {i + 1}")
            except RuleError as exc:
                reasons += exc.reasons
                continue
            if acct in seen:
                reasons.append(f"destination {i + 1} repeats an account")
            if acct == out.get("source_account_id"):
                reasons.append(f"destination {i + 1} is the source account")
            seen.add(acct)
            try:
                if basis in ("pct_balance", "pct_incoming"):
                    dests.append({"account_id": acct, "pct": fixed(parse_pct(d.get("pct"), f"destination {i + 1}'s %"))})
                else:
                    dests.append({"account_id": acct,
                                  "amount": money(parse_cents(d.get("amount"), f"destination {i + 1}'s amount"))})
            except RuleError as exc:
                reasons += exc.reasons
        if basis in ("pct_balance", "pct_incoming") and dests:
            total = sum((Decimal(x["pct"]) for x in dests), ZERO)
            if total > HUNDRED:
                reasons.append(f"the destinations' percentages add up to {fixed(total)}%, more than 100%")
    out["destinations"] = dests
    if reasons:
        raise RuleError(reasons)
    return out


def terms_out(r: dict) -> dict:
    """The JSON-safe terms of a row or a normalised definition."""
    out = {}
    for k in TERMS:
        v = r.get(k)
        if isinstance(v, Decimal):
            v = money(v)
        elif isinstance(v, uuid.UUID):
            v = str(v)
        elif k == "destinations":
            v = _loads(v) or []
        out[k] = v
    return out


def _loads(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _row(r) -> dict:
    d = dict(r)
    d["destinations"] = _loads(d.get("destinations")) or []
    return d


# ═══ Database: reads ═════════════════════════════════════════════════════════════════════════════════════════════
BALANCE_SQL = f"""
SELECT p.account_id::text AS account_id, p.as_of_date, sum(p.market_value) AS v
  FROM portfolio.positions p
  JOIN portfolio.assets a ON a.id = p.asset_id AND a.org_id = p.org_id
 WHERE p.org_id = $1::uuid AND p.account_id = ANY($2::uuid[]) AND a.asset_type = '{CASH_ASSET_TYPE}'
   AND p.superseded_by_source IS NULL AND p.valid_to IS NULL AND p.system_to IS NULL
   AND a.valid_to IS NULL AND a.system_to IS NULL AND p.as_of_date <= $3::date
 GROUP BY 1, 2
"""


async def account_balances(conn, org_id, account_ids, on: date) -> dict[str, Balance]:
    """Each account's available CASH (its cash positions on their latest as-of date ≤ ``on``) — the same predicates
    as services.portfolio_cash.get_cash_balance (live, not superseded by source precedence, asset_type cash), read per
    ACCOUNT (that function reads per owner, and one owner holds several accounts)."""
    ids = sorted({str(a) for a in account_ids if a})
    out: dict[str, Balance] = {a: Balance(None, None) for a in ids}
    if not ids:
        return out
    for r in await conn.fetch(BALANCE_SQL, str(org_id), ids, on):
        cur = out[r["account_id"]]
        if cur.as_of is None or r["as_of_date"] > cur.as_of:
            out[r["account_id"]] = Balance(Decimal(str(r["v"])) if r["v"] is not None else None, r["as_of_date"])
    return out


CREDIT_SQL = f"""
SELECT t.id::text AS transaction_id, t.transaction_type_code AS code, p.account_id::text AS account_id,
       COALESCE(t.net_amount, t.gross_amount, t.quantity * t.price) AS amount, COALESCE(t.settle_date, t.trade_date) AS on_date
  FROM portfolio.transactions t
  JOIN portfolio.positions p ON p.id = t.position_id AND p.org_id = t.org_id
  JOIN portfolio.assets a ON a.id = p.asset_id AND a.org_id = p.org_id AND a.asset_type = '{CASH_ASSET_TYPE}'
 WHERE t.org_id = $1::uuid AND t.id = ANY($2::uuid[]) AND t.valid_to IS NULL AND t.system_to IS NULL
"""


def is_credit(code: str | None, amount) -> bool:
    """An INCOMING credit: money INTO the cash holding (cash_activity.TYPE_MAP sign +1 — the one sign table), > 0."""
    m = CA.TYPE_MAP.get(code or "")
    try:
        return bool(m and m[1] == 1 and amount is not None and Decimal(str(amount)) > 0)
    except InvalidOperation:
        return False


async def incoming_credits(conn, org_id, transaction_ids) -> list[dict]:
    ids = sorted({str(x) for x in transaction_ids if x})
    if not ids:
        return []
    return [{"transaction_id": r["transaction_id"], "account_id": r["account_id"], "amount": Decimal(str(r["amount"])),
             "on": r["on_date"], "code": r["code"]}
            for r in await conn.fetch(CREDIT_SQL, str(org_id), ids) if is_credit(r["code"], r["amount"])]


async def ceilings(conn, org_id) -> tuple[Decimal, int]:
    from services.org_settings import get_setting

    amt, per_day = DEFAULT_MAX_AMOUNT, DEFAULT_MAX_PER_DAY
    try:
        v = await get_setting(conn, str(org_id), SETTING_MAX_AMOUNT)
        if v is not None:
            d = Decimal(str(v))
            if d >= CENT and d == d.quantize(CENT):
                amt = d
    except (InvalidOperation, TypeError, ValueError):
        pass
    try:
        v = await get_setting(conn, str(org_id), SETTING_MAX_PER_DAY)
        if v is not None and 1 <= int(v) <= 20:
            per_day = int(v)
    except (TypeError, ValueError):
        pass
    return amt, per_day


async def load(conn, org_id, version_id, *, lock: bool = False) -> dict:
    try:
        vid = str(uuid.UUID(str(version_id)))
    except (TypeError, ValueError):
        raise RuleNotFound("cash rule not found") from None
    r = await conn.fetchrow(f"SELECT * FROM {T} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                            + (" FOR UPDATE" if lock else ""), vid, str(org_id))
    if r is None:
        raise RuleNotFound("cash rule not found")
    return _row(r)


async def versions_of(conn, org_id, rule_id) -> list[dict]:
    return [_row(r) for r in await conn.fetch(
        f"SELECT * FROM {T} WHERE org_id = $1::uuid AND rule_id = $2::uuid AND system_to IS NULL ORDER BY version_no",
        str(org_id), str(rule_id))]


async def running_version(conn, org_id, rule_id) -> dict | None:
    r = await conn.fetchrow(f"SELECT * FROM {T} WHERE org_id = $1::uuid AND rule_id = $2::uuid AND system_to IS NULL "
                            "AND status IN ('active', 'paused')", str(org_id), str(rule_id))
    return _row(r) if r else None


async def _labels(conn, org_id, ids) -> dict[str, str]:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    return {str(r["id"]): r["account_number_masked"] for r in await conn.fetch(
        "SELECT id, account_number_masked FROM public.accounts WHERE org_id = $1::uuid AND id = ANY($2::uuid[]) "
        "AND system_to IS NULL AND valid_to IS NULL", str(org_id), ids)}


def _rule_accounts(r: dict) -> list[str]:
    return [str(x) for x in [r.get("source_account_id"), r.get("supporting_account_id")]
            + [d.get("account_id") for d in (r.get("destinations") or [])] if x]


# ═══ Database: validation (the reasons a person reads) ═══════════════════════════════════════════════════════════
async def validate(pool, conn, *, org_id, owner_id, household_id, clean: dict, t: date, what: str,
                   audit: bool = True) -> None:
    """Every account rule, ALL reasons together: each account is this household's; every destination (and the
    supporting account) has the SAME owner as the source; the rail is DECLARED by each paying custodian; the OWNER
    holds both authorities on each paying account; the caps are within the org ceilings. Reuses services.payments'
    own checks (same_owner_refusal, rail_refusal, authority_refusals) — never a copy."""
    reasons: list[str] = []
    accts: dict[str, dict] = {}
    for a in _rule_accounts(clean):
        try:
            accts[a] = await PS.load_account(conn, org_id, a, t)
        except PS.PaymentNotFound:
            reasons.append("an account of this rule is not one of this organization's live accounts")
    if reasons:
        raise RuleError(reasons)
    src = accts[str(clean["source_account_id"])]
    if str(src.get("household_id")) != str(household_id):
        reasons.append("the source account is not one of this household's")
    src_owners = await PS._owners(conn, src["id"])
    others = [str(d["account_id"]) for d in clean["destinations"]]
    if clean.get("supporting_account_id"):
        others.append(str(clean["supporting_account_id"]))
    for a in others:
        why = PS.same_owner_refusal(src, accts[a], household_id, src_owners, await PS._owners(conn, a))
        if why:
            reasons.append(f"{accts[a]['account_number_masked']}: {why}")
    for a in payment_sources(clean):
        why = await PS.rail_refusal(conn, accts[a], clean["rail"])
        if why:
            reasons.append(why)
        for r in await PS.authority_refusals(pool, conn, org_id, owner_id, accts[a]):
            reasons.append(f"the rule's owner — {r}")
    amt, per_day = await ceilings(conn, org_id)
    if clean["cap_per_firing"] > amt:
        reasons.append(f"the per-firing cap {money(clean['cap_per_firing'])} is above the organization's ceiling of "
                       f"{money(amt)} ({SETTING_MAX_AMOUNT})")
    if clean["max_per_day"] > per_day:
        reasons.append(f"the per-day limit {clean['max_per_day']} is above the organization's ceiling of {per_day} "
                       f"({SETTING_MAX_PER_DAY})")
    if clean["basis"] == "fixed":
        total = sum((Decimal(d["amount"]) for d in clean["destinations"]), ZERO)
        if total > clean["cap_per_firing"]:
            reasons.append(f"the fixed amounts add up to {money(total)}, more than the per-firing cap of "
                           f"{money(clean['cap_per_firing'])} — raise the cap or lower an amount")
    if reasons:
        if audit:
            await PS._refused(org_id, owner_id, what, reasons)
        raise RuleError(reasons)


# ═══ Writing primitives ══════════════════════════════════════════════════════════════════════════════════════════
def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: money(o) if isinstance(o, Decimal) else str(o) if isinstance(o, uuid.UUID)
                      else o.isoformat() if isinstance(o, (date, datetime)) else str(o))


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, 'cash_rule', $4::uuid, $5::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"cash.rule.{action}",
        str(resource_id) if resource_id else None, _dumps(payload))


async def _publish(pool, org_id, action: str, resource_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    try:
        await publish_event(pool, str(org_id), f"cash_rule.{action}", EVENT_SOURCE, str(resource_id),
                            json.loads(_dumps(payload)), created_by=str(actor_id) if actor_id else None)
    except Exception:  # noqa: BLE001 — the event is a notification; the write stands
        log.exception("cash_rule.%s event not published", action)


async def _archive(conn, row_id) -> None:
    """System axis (Rule 3): the SHARED helper services.bitemporal.archive_live_row (the id stays stable)."""
    if await archive_live_row(conn, T, row_id) is None:
        raise RuleConflict("the cash rule version is not live — refusing to change it")


async def _update(conn, row: dict, fields: dict) -> dict:
    await _archive(conn, row["id"])
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" + ("::jsonb" if c == "destinations" else "") for i, c in enumerate(cols))
    vals = [json.dumps(fields[c]) if c == "destinations" else fields[c] for c in cols]
    new = await conn.fetchrow(f"UPDATE {T} SET {sets}, system_from = now() WHERE id = $1::uuid AND system_to IS NULL "
                              "RETURNING *", str(row["id"]), *vals)
    if new is None:
        raise RuleConflict("the cash rule changed underneath this request — reload and retry")
    return _row(new)


def _bpmn(rule_id) -> str:
    rid = str(rule_id).replace("-", "")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="D_cash_rule_{rid}" '
            'targetNamespace="https://hollisworks.com/bpmn/system">'
            f'<bpmn:process id="p_cash_rule_{rid}" isExecutable="true">'
            '<bpmn:startEvent id="start"><bpmn:outgoing>f0</bpmn:outgoing></bpmn:startEvent>'
            f'<bpmn:serviceTask id="{STEP_KEY}" name="Cash rule: draft payment instructions for approval">'
            '<bpmn:incoming>f0</bpmn:incoming><bpmn:outgoing>f1</bpmn:outgoing></bpmn:serviceTask>'
            '<bpmn:endEvent id="end"><bpmn:incoming>f1</bpmn:incoming></bpmn:endEvent>'
            f'<bpmn:sequenceFlow id="f0" sourceRef="start" targetRef="{STEP_KEY}"/>'
            f'<bpmn:sequenceFlow id="f1" sourceRef="{STEP_KEY}" targetRef="end"/>'
            '</bpmn:process></bpmn:definitions>')


async def ensure_workflow(conn, *, org_id, rule_id, name: str, owner_id, clean: dict) -> dict:
    """The rule's ONE workflow definition (v1, one Service Task ``cash.rule_fire``) and ONE trigger, created through
    the engine's own tables (as services.security_master_jobs.ensure_platform_jobs does), the trigger INACTIVE until
    a version is approved. ``created_by`` = the owner: the S30 trigger owner — the maker of every run it fires."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"{org_id}:cash_rule:{rule_id}")
    dname = f"{DEFINITION_PREFIX}{rule_id}"
    row = await conn.fetchrow(
        "SELECT d.id, v.id AS vid FROM workflow_definitions d JOIN workflow_versions v ON v.workflow_definition_id = "
        "d.id AND v.is_current WHERE d.org_id = $1::uuid AND d.name = $2 ORDER BY d.created_at LIMIT 1",
        str(org_id), dname)
    if row is None:
        def_id = await conn.fetchval(
            "INSERT INTO workflow_definitions (org_id, name, description, is_active, created_by) VALUES ($1::uuid, $2, "
            "$3, true, $4::uuid) RETURNING id", str(org_id), dname,
            f"System — the cash rule '{name[:80]}': drafts payment instructions for a person's approval. Created by "
            "services.cash_rules; do not edit.", str(owner_id))
        vid = await conn.fetchval(
            "INSERT INTO workflow_versions (workflow_definition_id, org_id, version_number, bpmn_xml, change_summary, "
            "is_current, created_by) VALUES ($1, $2::uuid, 1, $3, $4, true, $5::uuid) RETURNING id",
            def_id, str(org_id), _bpmn(rule_id), "CASH06 cash rule (system)", str(owner_id))
        await conn.execute(
            "INSERT INTO workflow_steps (workflow_version_id, org_id, step_key, step_type, autonomy_tier, "
            "action_registry_key, display_name) VALUES ($1, $2::uuid, $3, 'service', 3, $4, $5)",
            vid, str(org_id), STEP_KEY, ACTION_KEY, "Cash rule: draft payment instructions for approval")
    else:
        def_id, vid = row["id"], row["vid"]
    trig = await conn.fetchval("SELECT id FROM workflow_triggers WHERE workflow_definition_id = $1 AND org_id = "
                               "$2::uuid ORDER BY created_at LIMIT 1", def_id, str(org_id))
    if trig is None:
        cron = cron_for(clean["frequency"], clean.get("day_of_week"), clean.get("day_of_month"))
        trig = await conn.fetchval(
            "INSERT INTO workflow_triggers (workflow_definition_id, org_id, trigger_type, schedule_cron, event_type, "
            "timezone, is_active, created_by) VALUES ($1, $2::uuid, $3, $4, $5, $6, false, $7::uuid) RETURNING id",
            def_id, str(org_id), "event" if cron is None else "scheduled", cron, EVENT_TYPE if cron is None else None,
            TZ, str(owner_id))
    return {"definition_id": def_id, "version_id": vid, "trigger_id": trig}


async def _set_trigger(conn, trigger_id, row: dict | None, *, active: bool) -> None:
    """Turn the rule's trigger on / off; on activation its schedule (or event) follows the approved version."""
    if row is None or not active:
        await conn.execute("UPDATE workflow_triggers SET is_active = false WHERE id = $1::uuid", str(trigger_id))
        return
    cron = cron_for(row["frequency"], row.get("day_of_week"), row.get("day_of_month"))
    await conn.execute(
        "UPDATE workflow_triggers SET is_active = true, trigger_type = $2, schedule_cron = $3, event_type = $4, "
        "timezone = $5 WHERE id = $1::uuid", str(trigger_id), "event" if cron is None else "scheduled", cron,
        EVENT_TYPE if cron is None else None, TZ)


# ═══ Governance: draft / edit / submit / decide / pause / resume / retire ══════════════════════════════════════════
async def save_draft(pool, *, org_id, actor_id, household_id, data: dict, rule_id=None, reason: str | None = None) -> dict:
    """A NEW rule (its owner is the drafter), an edit of an open DRAFT (the editor joins its makers), or a NEW VERSION
    of a running rule (the running version keeps running until this one is approved). Never activates anything."""
    clean = normalise(data)
    t = today()
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        existing = await versions_of(conn, org_id, rule_id) if rule_id else []
        if rule_id and not existing:
            raise RuleNotFound("cash rule not found")
        if existing and str(existing[0]["household_id"]) != str(household_id):
            raise RuleNotFound("cash rule not found")
        owner = existing[0]["owner_id"] if existing else aid
        live = [v for v in existing if v["status"] not in STATUS_IMMUTABLE]
        if rule_id and not live:
            raise RuleConflict("this rule is retired — draft a new rule instead")
        await validate(pool, conn, org_id=org_id, owner_id=owner, household_id=household_id, clean=clean, t=t,
                       what="save a cash rule")
        rid = uuid.UUID(str(rule_id)) if rule_id else uuid.uuid4()
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"cash_rule:{rid}")
            open_ = await conn.fetchrow(f"SELECT * FROM {T} WHERE org_id = $1::uuid AND rule_id = $2::uuid AND "
                                        "system_to IS NULL AND status IN ('draft', 'pending_approval') FOR UPDATE",
                                        str(org_id), rid)
            if open_ is not None and open_["status"] == "pending_approval":
                raise RuleConflict("a version of this rule is awaiting approval — decide it first")
            wf = await ensure_workflow(conn, org_id=org_id, rule_id=rid, name=clean["name"], owner_id=owner,
                                       clean=clean)
            if open_ is not None:
                cur = _row(open_)
                makers = list(cur["maker_ids"]) + ([aid] if aid not in cur["maker_ids"] else [])
                row = await _update(conn, cur, {**{k: clean[k] for k in TERMS}, "maker_ids": makers,
                                                "reason": reason or cur.get("reason")})
                action = "draft_edited"
            else:
                vno = int(await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) + 1 FROM {T} WHERE org_id = "
                                              "$1::uuid AND rule_id = $2::uuid", str(org_id), rid))
                makers = sorted({aid, uuid.UUID(str(owner))}, key=str)
                row = _row(await conn.fetchrow(
                    f"""INSERT INTO {T} (org_id, rule_id, version_no, household_id, name, rule_type, source_account_id,
                            destinations, basis, condition, target_amount, supporting_account_id, frequency,
                            day_of_week, day_of_month, rail, cap_per_firing, max_per_day, stage, status, owner_id,
                            maker_ids, workflow_definition_id, workflow_version_id, trigger_id, reason, created_by)
                        VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7::uuid, $8::jsonb, $9, $10, $11, $12::uuid, $13,
                                $14, $15, $16, $17, $18, 'A', 'draft', $19::uuid, $20::uuid[], $21, $22, $23, $24,
                                $25::uuid) RETURNING *""",
                    str(org_id), rid, vno, str(household_id), clean["name"], clean["rule_type"],
                    clean["source_account_id"], json.dumps(clean["destinations"]), clean["basis"], clean["condition"],
                    clean["target_amount"], clean["supporting_account_id"], clean["frequency"], clean["day_of_week"],
                    clean["day_of_month"], clean["rail"], clean["cap_per_firing"], clean["max_per_day"], str(owner),
                    makers, wf["definition_id"], wf["version_id"], wf["trigger_id"], reason, str(aid)))
                action = "draft_created" if vno == 1 else "version_drafted"
            await _audit(conn, org_id, aid, action, row["id"], {"rule_id": str(rid), "version_no": row["version_no"],
                                                               "household_id": str(household_id), **terms_out(row)})
    await _publish(pool, org_id, action, row["id"], {"rule_id": str(rid), "version_no": row["version_no"]}, actor["id"])
    return row


def _task_title(row: dict) -> str:
    return f"Approve cash rule: {row['name']} (v{row['version_no']}, {dict(TYPES)[row['rule_type']].lower()})"


async def submit(pool, *, org_id, actor_id, version_id) -> dict:
    """DRAFT → PENDING_APPROVAL (activate) with its maker-checker task (kind cash_rule_approval). The submitter is a
    maker; the owner is a maker of every version. Every rule is re-checked at submit."""
    t = today()
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await load(conn, org_id, version_id)
        if row["status"] != "draft":
            raise RuleConflict(f"the rule version is {STATUS_LABELS[row['status']].lower()}, not a draft")
        await validate(pool, conn, org_id=org_id, owner_id=row["owner_id"], household_id=row["household_id"],
                       clean=normalise(terms_out(row)), t=t, what="submit a cash rule")
    aid = uuid.UUID(str(actor["id"]))
    makers = sorted({str(m) for m in row["maker_ids"]} | {str(aid), str(row["owner_id"])})
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=row["id"], maker_id=actor["id"],
        eligible_permission=APPROVE, title=_task_title(row),
        payload={"action": "activate", "rule_id": str(row["rule_id"]), "version_no": row["version_no"],
                 "terms": terms_out(row), "maker_ids": makers, "owner_id": str(row["owner_id"]),
                 "floor": "no self-approval, ever (S30 floor — a rule drafts money movement)"},
        action_path=f"/portfolio/households/{row['household_id']}?tab=treasury&rule={row['rule_id']}#treasury-rules")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, row["id"], lock=True)
                if cur["status"] != "draft":
                    raise RuleConflict(f"the rule version is {cur['status']}, not a draft")
                new = await _update(conn, cur, {"status": "pending_approval", "pending_action": "activate",
                                                "approval_task_id": uuid.UUID(task["id"]), "submitted_by": aid,
                                                "submitted_at": datetime.now(timezone.utc),
                                                "maker_ids": [uuid.UUID(m) for m in makers]})
                await _audit(conn, org_id, aid, "submitted", cur["id"], {"task_id": task["id"],
                                                                        "version_no": cur["version_no"]})
    except Exception:
        await _withdraw_task(pool, task["id"], "the cash rule could not be submitted")
        raise
    await _publish(pool, org_id, "submitted", new["id"], {"task_id": task["id"]}, actor["id"])
    return {**new, "task": task}


async def _withdraw_task(pool, task_id, reason: str) -> None:
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        log.info("task %s was not open to withdraw", task_id)


def _refuse(_d) -> Exception:
    return MakerCheckerViolation("maker-checker (S30 floor): you made or own this cash rule — or asked to resume it — "
                                 f"so a different holder of {APPROVE} must decide it; no setting and no role (Super "
                                 "Admin included) relaxes this")


def decision_makers(row: dict) -> list[str]:
    """Every maker of the decision: the version's makers (the owner always among them) + a resume's requester."""
    out = {str(m) for m in row["maker_ids"]} | {str(row["owner_id"])}
    if row.get("pending_action") == "resume" and row.get("resume_requested_by"):
        out.add(str(row["resume_requested_by"]))
    return sorted(out)


async def _precheck(pool, task, outcome, actor_id):
    """BEFORE the engine completes the task (it stays open on refusal): still awaiting this decision; on approve, the
    FLOOR against every maker (the task itself knows only the submitter) and the definition re-checked as of now."""
    async with pool.acquire() as conn:
        row = await load(conn, task["org_id"], task["subject_id"])
        awaiting = (row["status"] == "pending_approval" or (row["status"] == "paused"
                                                            and row.get("pending_action") == "resume"))
        if not awaiting or str(row.get("approval_task_id")) != str(task["id"]):
            raise HT.HumanTaskConflict(f"the cash rule is {row['status']}, not awaiting this decision")
        if outcome != "approve":
            return
        await sod.gate(conn, org_id=task["org_id"], item_type=sod.CASH_RULE, checker_id=actor_id,
                       maker_ids=decision_makers(row), item_id=str(row["id"]), household_id=row["household_id"],
                       site="cash_rules._precheck", refuse=_refuse)
        await validate(pool, conn, org_id=task["org_id"], owner_id=row["owner_id"], household_id=row["household_id"],
                       clean=normalise(terms_out(row)), t=today(), what="approve a cash rule")


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    """The decision lands (idempotent). Activate: approve → ACTIVE (the running version, if any, SUPERSEDED; the
    trigger follows the new terms and turns on); reject → back to DRAFT. Resume: approve → ACTIVE (trigger on);
    reject → stays PAUSED."""
    org_id = task["org_id"]
    withdraw: list = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await load(conn, org_id, task["subject_id"], lock=True)
            resume = row["status"] == "paused" and row.get("pending_action") == "resume"
            if not (row["status"] == "pending_approval" or resume):
                return row
            checker = uuid.UUID(str(actor_id))
            now_ = datetime.now(timezone.utc)
            prev_id = None
            if outcome == "approve":
                await sod.gate(conn, org_id=org_id, item_type=sod.CASH_RULE, checker_id=checker,
                               maker_ids=decision_makers(row), item_id=str(row["id"]),
                               household_id=row["household_id"], site="cash_rules._apply", refuse=_refuse)
                if not resume:
                    prev = await conn.fetchrow(f"SELECT * FROM {T} WHERE org_id = $1::uuid AND rule_id = $2::uuid AND "
                                               "system_to IS NULL AND status IN ('active', 'paused') AND id <> $3::uuid "
                                               "FOR UPDATE", str(org_id), row["rule_id"], row["id"])
                    if prev is not None:
                        prev_id = str(prev["id"])
                        if prev["pending_action"] == "resume" and prev["approval_task_id"]:
                            withdraw.append(prev["approval_task_id"])   # the new version replaces the resume request
                        await _archive(conn, prev["id"])
                        await conn.execute(f"UPDATE {T} SET status = 'superseded', pending_action = NULL, valid_to = "
                                           "now(), superseded_by = $2::uuid, system_from = now() WHERE id = $1::uuid "
                                           "AND system_to IS NULL", prev["id"], row["id"])
                new = await _update(conn, row, {"status": "active", "pending_action": None, "approved_by": checker,
                                                "approved_at": now_, "decision_note": note,
                                                **({} if resume else {"valid_from": now_})})
                await _set_trigger(conn, new["trigger_id"], new, active=True)
            elif resume:
                new = await _update(conn, row, {"pending_action": None, "decision_note": note})
            else:
                new = await _update(conn, row, {"status": "draft", "pending_action": None, "approval_task_id": None,
                                                "rejected_by": checker, "rejected_at": now_, "reject_reason": note})
            verb = ("resumed" if resume else "approved") if outcome == "approve" else (
                "resume_rejected" if resume else "rejected")
            await _audit(conn, org_id, checker, verb, row["id"], {"task_id": task["id"], "note": note,
                                                                  "superseded": prev_id,
                                                                  "version_no": row["version_no"]})
    for tid in withdraw:
        await _withdraw_task(pool, tid, "a newer version of the rule was approved")
    await _publish(pool, org_id, verb, new["id"], {"task_id": task["id"], "superseded": prev_id}, actor_id)
    return {**new, "superseded_version_id": prev_id}


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK_KIND, label="Cash rule approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)
HT.register_precheck(TASK_KIND, _precheck)


async def decide(pool, *, org_id, actor_id, version_id, outcome: str, note: str | None = None) -> dict:
    """The ONE decision path (route and inbox): the S30 floor against every maker (a refusal recorded in
    sod_decisions, nothing changes), then services.human_tasks.complete_task (which re-checks it and the permission)."""
    if outcome not in ("approve", "reject"):
        raise RuleError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise RuleError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await load(conn, org_id, version_id)
    if not (row["status"] == "pending_approval" or (row["status"] == "paused" and row.get("pending_action") == "resume")):
        raise RuleConflict(f"the cash rule is {STATUS_LABELS[row['status']].lower()}, not awaiting a decision")
    async with pool.acquire() as conn:
        await sod.gate(conn, org_id=org_id, item_type=sod.CASH_RULE, checker_id=actor["id"],
                       maker_ids=decision_makers(row), item_id=str(row["id"]), household_id=row["household_id"],
                       site="cash_rules.decide", refuse=_refuse)
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=(note or "").strip() or None,
                                      sod_household_id=row["household_id"])
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise RuleConflict(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await load(conn, org_id, version_id)
    return result


async def pause(pool, *, org_id, actor_id, version_id, reason: str) -> dict:
    """ACTIVE → PAUSED at once (tightening: no checker); the trigger turns off — a paused rule never fires."""
    reason = (reason or "").strip()
    if not reason:
        raise RuleError("say why the rule is paused")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        async with conn.transaction():
            row = await load(conn, org_id, version_id, lock=True)
            if row["status"] != "active":
                raise RuleConflict(f"only an active rule is paused (this one is {row['status']})")
            new = await _update(conn, row, {"status": "paused", "paused_by": aid, "paused_at": datetime.now(timezone.utc),
                                            "pause_reason": reason, "resume_requested_by": None,
                                            "resume_requested_at": None})
            await _set_trigger(conn, row["trigger_id"], None, active=False)
            await _audit(conn, org_id, aid, "paused", row["id"], {"reason": reason})
    await _publish(pool, org_id, "paused", row["id"], {"reason": reason}, actor["id"])
    return new


async def request_resume(pool, *, org_id, actor_id, version_id) -> dict:
    """PAUSED → (still paused) awaiting a DIFFERENT person's approval to resume (loosening: maker-checker)."""
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await load(conn, org_id, version_id)
        if row["status"] != "paused" or row.get("pending_action"):
            raise RuleConflict("only a paused rule with no open request can be asked to resume")
        await validate(pool, conn, org_id=org_id, owner_id=row["owner_id"], household_id=row["household_id"],
                       clean=normalise(terms_out(row)), t=today(), what="resume a cash rule")
    aid = uuid.UUID(str(actor["id"]))
    makers = sorted({str(m) for m in row["maker_ids"]} | {str(row["owner_id"]), str(aid)})
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=row["id"], maker_id=actor["id"],
        eligible_permission=APPROVE, title=f"Approve resuming cash rule: {row['name']} (v{row['version_no']})",
        payload={"action": "resume", "rule_id": str(row["rule_id"]), "version_no": row["version_no"],
                 "terms": terms_out(row), "maker_ids": makers, "owner_id": str(row["owner_id"])},
        action_path=f"/portfolio/households/{row['household_id']}?tab=treasury&rule={row['rule_id']}#treasury-rules")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, row["id"], lock=True)
                if cur["status"] != "paused" or cur.get("pending_action"):
                    raise RuleConflict("the rule changed — reload")
                new = await _update(conn, cur, {"pending_action": "resume", "approval_task_id": uuid.UUID(task["id"]),
                                                "resume_requested_by": aid,
                                                "resume_requested_at": datetime.now(timezone.utc)})
                await _audit(conn, org_id, aid, "resume_requested", cur["id"], {"task_id": task["id"]})
    except Exception:
        await _withdraw_task(pool, task["id"], "the resume request could not be recorded")
        raise
    await _publish(pool, org_id, "resume_requested", new["id"], {"task_id": task["id"]}, actor["id"])
    return {**new, "task": task}


async def retire(pool, *, org_id, actor_id, version_id, reason: str) -> dict:
    """A draft is discarded, or the running version (active / paused) retires the RULE: its trigger turns off and an
    open draft of it is retired too. Immediate (tightening). Refused while a version awaits approval."""
    reason = (reason or "").strip()
    if not reason:
        raise RuleError("say why the rule is retired")
    withdraw = []
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        async with conn.transaction():
            row = await load(conn, org_id, version_id, lock=True)
            if row["status"] not in ("draft", "active", "paused"):
                raise RuleConflict(f"a {row['status']} rule version cannot be retired")
            if row["status"] == "paused" and row.get("pending_action") == "resume":
                withdraw.append(row["approval_task_id"])
            others = [] if row["status"] == "draft" else [v for v in await versions_of(conn, org_id, row["rule_id"])
                                                          if v["id"] != row["id"] and v["status"] in ("draft",
                                                                                                     "pending_approval")]
            if any(v["status"] == "pending_approval" for v in others):
                raise RuleConflict("a version of this rule is awaiting approval — decide it first")
            stamp = {"status": "retired", "retired_by": aid, "retired_at": datetime.now(timezone.utc),
                     "retire_reason": reason, "pending_action": None, "valid_to": datetime.now(timezone.utc)}
            new = await _update(conn, row, stamp)
            for v in others:
                await _update(conn, v, dict(stamp, retire_reason=f"the rule was retired: {reason}"))
            if row["status"] in ("active", "paused"):
                await _set_trigger(conn, row["trigger_id"], None, active=False)
            await _audit(conn, org_id, aid, "retired", row["id"], {"reason": reason,
                                                                  "drafts_retired": [str(v["id"]) for v in others]})
    for tid in withdraw:
        await _withdraw_task(pool, tid, "the rule was retired")
    await _publish(pool, org_id, "retired", row["id"], {"reason": reason}, actor["id"])
    return new


# ═══ The engine's step: ONE firing ═══════════════════════════════════════════════════════════════════════════════
class FireRefused(Exception):
    """Not the engine running this rule's own trigger as its owner — nothing is evaluated, nothing drafted."""


async def _engine_run(conn, org_id, workflow_run_id) -> dict:
    """The run row as STORED — never the caller's copy of its context (a crafted call cannot forge a trigger)."""
    if workflow_run_id is None:
        raise FireRefused(f"{ACTION_KEY} runs only inside its rule's own workflow run")
    r = await conn.fetchrow("SELECT id, org_id, status, context FROM workflow_runs WHERE id = $1::uuid",
                            str(workflow_run_id))
    if r is None or str(r["org_id"]) != str(org_id) or r["status"] != "running":
        raise FireRefused(f"{ACTION_KEY}: run {workflow_run_id} is not a running run of this organization")
    ctx = _loads(r["context"]) or {}
    return ctx if isinstance(ctx, dict) else {}


def _save_engine_globals():
    from services import workflow_engine as E

    return E._SERVICE_CONTEXT, E._SERVICE_STEP_MAP


def _restore_engine_globals(saved) -> None:
    """The nested approval-task run (human_tasks.open_task → start_workflow_run) resets the engine's module-level
    service context on its way out; the outer run's step is still finishing — put it back (defensive)."""
    from services import workflow_engine as E

    E._SERVICE_CONTEXT, E._SERVICE_STEP_MAP = saved


async def fire(pool, *, org_id, user_id, workflow_run_id, run_context: dict | None = None) -> dict:
    """ONE firing: the active version of the rule whose trigger started this run, claimed ONCE for its period (or its
    incoming transaction), evaluated, and each draft submitted through services.payments.draft_and_submit AS THE
    OWNER. Holds alert the owner + every org admin (workflow_autonomy.hold_alerts). Approves, sends nothing."""
    saved = _save_engine_globals()
    async with pool.acquire() as conn:
        ctx = await _engine_run(conn, org_id, workflow_run_id)
        trig = ctx.get("trigger_id")
        if not trig:
            raise FireRefused(f"{ACTION_KEY}: the run was not started by a trigger")
        r = await conn.fetchrow(f"SELECT * FROM {T} WHERE org_id = $1::uuid AND trigger_id = $2::uuid AND system_to IS "
                                "NULL AND status IN ('active', 'paused') ORDER BY version_no DESC LIMIT 1",
                                str(org_id), str(trig))
        if r is None:
            return {"data": {"fired": False, "reason": "the rule has no running version"},
                    "text": "cash rule: no running version — nothing drafted"}
        rule = _row(r)
        if user_id is None or str(user_id) != str(rule["owner_id"]):
            raise FireRefused("a cash rule's instructions are drafted as its OWNER only "
                              f"(this step ran as {user_id}, the owner is {rule['owner_id']})")
        if rule["status"] != "active":
            return {"data": {"fired": False, "rule_id": str(rule["rule_id"]), "reason": "paused"},
                    "text": "cash rule: paused — nothing drafted"}
        ttype = "event" if ctx.get("trigger_type") == "event" else "scheduled" if ctx.get("trigger_type") == \
            "scheduled" else "manual"
        on = firing_day(ctx, today())
        incoming: list[dict] = []
        if rule["frequency"] == "on_incoming":
            if ctx.get("event_type") != EVENT_TYPE or ctx.get("source_type") != FEED_SOURCE:
                return {"data": {"fired": False, "reason": "not an incoming-credit event"}, "text": "cash rule: no credit"}
            incoming = [c for c in await incoming_credits(conn, org_id, [ctx.get("source_id")])
                        if c["account_id"] == str(rule["source_account_id"])]
            if not incoming:
                return {"data": {"fired": False, "rule_id": str(rule["rule_id"]),
                                 "reason": "the credit is not to this rule's source account"},
                        "text": "cash rule: not this rule's account — nothing drafted"}
        elif ttype == "event":
            return {"data": {"fired": False, "reason": "a scheduled rule ignores events"}, "text": "cash rule: event"}
        pkey = period_key(rule["frequency"], on, incoming[0]["transaction_id"] if incoming else None)
        claim = await conn.fetchval(
            f"INSERT INTO {T_FIRE} (org_id, rule_id, rule_version_id, period_key, trigger_type, workflow_run_id, "
            "owner_id, evaluated_on) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::uuid, $7::uuid, $8) "
            "ON CONFLICT (org_id, rule_id, period_key) DO NOTHING RETURNING id",
            str(org_id), str(rule["rule_id"]), str(rule["id"]), pkey, ttype, str(workflow_run_id),
            str(rule["owner_id"]), on)
        if claim is None:
            return {"data": {"fired": False, "rule_id": str(rule["rule_id"]), "period_key": pkey,
                             "reason": "already fired for this period"},
                    "text": f"cash rule: {pkey} already fired — nothing drafted (idempotent)"}
        balances = await account_balances(conn, org_id, read_accounts(rule), on)
        drafted_today = int(await conn.fetchval(
            f"SELECT COALESCE(sum(cardinality(instruction_ids)), 0) FROM {T_FIRE} WHERE org_id = $1::uuid AND rule_id "
            "= $2::uuid AND evaluated_on = $3 AND id <> $4::uuid", str(org_id), str(rule["rule_id"]), on, claim))
        amt, per_day = await ceilings(conn, org_id)
        labels = await _labels(conn, org_id, _rule_accounts(rule))
    ev = evaluate(rule, balances, incoming, on, drafted_today=drafted_today, ceiling_amount=amt,
                  ceiling_per_day=per_day, labels=labels)
    made: list[str] = []
    drafts_out: list[dict] = []
    try:
        for d in ev.drafts:
            try:
                row = await PS.draft_and_submit(pool, org_id=org_id, actor_id=rule["owner_id"],
                                                household_id=rule["household_id"],
                                                data={"source_account_id": d.source_account_id,
                                                      "destination_account_id": d.destination_account_id,
                                                      "amount": d.amount, "rail": d.rail, "value_date": d.value_date,
                                                      "purpose": f"rule {rule['name']} — {d.reason}"[:500],
                                                      "purpose_ref_type": "cash_rule",
                                                      "purpose_ref_id": str(rule["rule_id"])})
                made.append(str(row["id"]))
                drafts_out.append({**d.out(), "payment_id": str(row["id"]), "status": row["status"]})
            except (PS.PaymentError, PS.PaymentAuthorityError, PS.PaymentConflict, HT.HumanTaskError) as exc:
                ev.holds.append(Hold("refused", f"{money(d.amount)} to {labels.get(d.destination_account_id, 'the account')}"
                                                f" was refused by the payment checks: {exc}", d.amount,
                                     d.source_account_id, d.destination_account_id))
            finally:
                _restore_engine_globals(saved)
        outcome = ("partly_held" if made and ev.holds else "held" if ev.holds else "drafted" if made else "skipped")
        reason = ev.reason if outcome != "drafted" else None
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {T_FIRE} SET outcome = $2, reason = $3, drafts = $4::jsonb, holds = $5::jsonb, "
                "instruction_ids = $6::uuid[], completed_at = now() WHERE id = $1::uuid",
                claim, outcome, reason, json.dumps(drafts_out), json.dumps([h.out() for h in ev.holds]), made)
            await _audit(conn, org_id, rule["owner_id"], "fired", rule["id"],
                         {"period_key": pkey, "outcome": outcome, "instruction_ids": made, "workflow_run_id":
                          str(workflow_run_id), "holds": [h.kind for h in ev.holds], "on": on.isoformat()})
    except Exception as exc:
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    f"UPDATE {T_FIRE} SET outcome = 'held', reason = $2, instruction_ids = $3::uuid[], drafts = "
                    "$4::jsonb, completed_at = now() WHERE id = $1::uuid AND outcome = 'firing'", claim,
                    f"the firing failed: {type(exc).__name__}: {exc}"[:2000], made, json.dumps(drafts_out))
        except Exception:  # noqa: BLE001
            log.exception("cash rule firing %s: could not record the failure", claim)
        raise
    if ev.holds:
        await WA.hold_alerts(pool, org_id=org_id, run_id=workflow_run_id, owner=str(rule["owner_id"]),
                             reason=f"Cash rule '{rule['name']}' ({pkey}): " + " · ".join(h.reason for h in ev.holds))
    return {"data": {"fired": True, "rule_id": str(rule["rule_id"]), "rule_version_id": str(rule["id"]),
                     "period_key": pkey, "outcome": outcome, "instruction_ids": made,
                     "holds": [h.out() for h in ev.holds], "approved": 0, "sent": 0},
            "text": f"cash rule {rule['name']}: {len(made)} instruction(s) drafted and submitted for approval; "
                    f"{len(ev.holds)} held; nothing approved or sent"}


async def _handler(*, pool=None, user_id=None, org_id=None, run_context=None, workflow_run_id=None, **_):
    return await fire(pool, org_id=org_id, user_id=user_id, workflow_run_id=workflow_run_id, run_context=run_context)


def register_actions() -> None:
    from services.action_registry import REGISTRY, AssistantAction

    REGISTRY.register(AssistantAction(
        key=ACTION_KEY, module="cash",
        description="Cash rule firing (engine only): evaluate the rule and DRAFT + SUBMIT payment instructions between "
                    "the family's own accounts for a person's approval. Never approves, sends or moves money.",
        access_type="write", required_permission=INITIATE, tier=2, reversible=False, render_target="inline",
        handler=_handler, params_schema={"type": "object", "properties": {}, "required": []},
        workflow_invocable=True, duty="propose", autonomy_class=WA.ELIGIBLE_WITH_CAP), workflow_only=True)


# ═══ The feed emitter: one cash.incoming_credit event per incoming credit to a watched account ═════════════════
async def publish_incoming_credits(org_id, transaction_ids, *, pool=None) -> list[dict]:
    """The MINIMAL emitter on the feed import (Task 1: no such event existed). For each new transaction that is an
    INCOMING credit (``is_credit``) to an account that is the SOURCE of an active on-incoming rule, publish ONE
    ``cash.incoming_credit`` domain event (source = the transaction: the deployed dedupe index makes it once per
    transaction, forever). Never raises — the import's write stands."""
    out = []
    try:
        if pool is None:
            from services.database import get_pool

            pool = await get_pool()
        from services.database import reset_rls_context, set_rls_context
        from services.domain_events import publish_event

        tokens = set_rls_context(str(org_id), False)
        try:
            async with pool.acquire() as conn:
                credits = await incoming_credits(conn, org_id, transaction_ids)
                watched = {str(r["source_account_id"]) for r in await conn.fetch(
                    f"SELECT DISTINCT source_account_id FROM {T} WHERE org_id = $1::uuid AND system_to IS NULL AND "
                    "status = 'active' AND frequency = 'on_incoming'", str(org_id))}
            for c in credits:
                if c["account_id"] not in watched:
                    continue
                res = await publish_event(pool, str(org_id), EVENT_TYPE, FEED_SOURCE, c["transaction_id"],
                                          {"transaction_id": c["transaction_id"], "account_id": c["account_id"],
                                           "amount": c["amount"], "on": c["on"].isoformat() if c["on"] else None})
                out.append(res)
        finally:
            reset_rls_context(tokens)
    except Exception:  # noqa: BLE001
        log.exception("cash rules: incoming-credit events not published (org %s)", org_id)
    return out


# ═══ Reads for the screens ═══════════════════════════════════════════════════════════════════════════════════════
def _iso(v):
    return v.isoformat() if v else None


async def _user_names(conn, ids) -> dict[str, str]:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    return {str(r["id"]): r["full_name"] or "A colleague" for r in await conn.fetch(
        "SELECT id, full_name FROM users WHERE id = ANY($1::uuid[])", ids)}


def _acct(a, labels: dict) -> dict | None:
    return {"id": str(a), "account_number_masked": labels.get(str(a)) or "****"} if a else None


def version_out(r: dict, labels: dict, users: dict, *, member: bool = False) -> dict:
    out = {"id": str(r["id"]), "rule_id": str(r["rule_id"]), "version_no": r["version_no"], "name": r["name"],
           "rule_type": r["rule_type"], "rule_type_label": dict(TYPES)[r["rule_type"]], "status": r["status"],
           "status_label": STATUS_LABELS[r["status"]], "stage": r["stage"],
           "source": _acct(r["source_account_id"], labels), "supporting": _acct(r.get("supporting_account_id"), labels),
           "destinations": [{**_acct(d["account_id"], labels), **({"pct": d["pct"]} if "pct" in d else {}),
                             **({"amount": d["amount"]} if "amount" in d else {})} for d in r["destinations"]],
           "basis": r["basis"], "basis_label": dict(BASES).get(r["basis"]) if r["basis"] else None,
           "condition": r["condition"], "condition_label": dict(CONDITIONS).get(r["condition"]) if r["condition"] else None,
           "target_amount": money(r["target_amount"]) if r["target_amount"] is not None else None,
           "frequency": r["frequency"], "frequency_label": dict(FREQUENCIES)[r["frequency"]],
           "day_of_week": r["day_of_week"], "day_of_month": r["day_of_month"],
           "rail": r["rail"], "rail_label": dict(PS.RAILS)[r["rail"]],
           "cap_per_firing": money(r["cap_per_firing"]), "max_per_day": r["max_per_day"],
           "pending_action": r.get("pending_action")}
    if member:
        out.pop("pending_action", None)
        return out
    out.update({"owner_id": str(r["owner_id"]), "owner_name": users.get(str(r["owner_id"]), "A colleague"),
                "maker_ids": [str(m) for m in r["maker_ids"]],
                "makers": [users.get(str(m), "A colleague") for m in r["maker_ids"]],
                "approved_by_name": users.get(str(r["approved_by"])) if r.get("approved_by") else None,
                "approved_at": _iso(r.get("approved_at")), "approval_task_id": str(r["approval_task_id"])
                if r.get("approval_task_id") else None, "pause_reason": r.get("pause_reason"),
                "retire_reason": r.get("retire_reason"), "reject_reason": r.get("reject_reason"),
                "resume_requested_by": str(r["resume_requested_by"]) if r.get("resume_requested_by") else None,
                "trigger_id": str(r["trigger_id"]) if r.get("trigger_id") else None,
                "workflow_version_id": str(r["workflow_version_id"]) if r.get("workflow_version_id") else None,
                "created_at": _iso(r.get("created_at"))})
    return out


async def _next_run(conn, row: dict) -> str | None:
    from services.workflow_schedule import ScheduleError, next_occurrences

    if row["status"] != "active" or not row.get("trigger_id"):
        return None
    t = await conn.fetchrow("SELECT schedule_cron, timezone, is_active FROM workflow_triggers WHERE id = $1::uuid",
                            str(row["trigger_id"]))
    if t is None or not t["is_active"] or not t["schedule_cron"]:
        return None
    try:
        nxt = next_occurrences(schedule_cron=t["schedule_cron"], timezone_name=t["timezone"],
                               after_utc=WA.now(), count=1)
    except ScheduleError:
        return None
    return nxt[0].isoformat() if nxt else None


async def firings_of(conn, org_id, rule_id, *, limit: int = 50) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_FIRE} WHERE org_id = $1::uuid AND rule_id = $2::uuid ORDER BY "
                            "created_at DESC, id LIMIT $3", str(org_id), str(rule_id), limit)
    pays = {}
    ids = sorted({str(i) for r in rows for i in (r["instruction_ids"] or [])})
    if ids:
        for p in await conn.fetch("SELECT id, status, amount, value_date FROM public.payment_instructions WHERE id = "
                                  "ANY($1::uuid[]) AND org_id = $2::uuid AND system_to IS NULL", ids, str(org_id)):
            pays[str(p["id"])] = {"id": str(p["id"]), "status": p["status"],
                                  "status_label": PS.STATUS_LABELS[p["status"]], "amount": money(p["amount"]),
                                  "value_date": p["value_date"].isoformat()}
    run_status = {}
    rids = sorted({str(r["workflow_run_id"]) for r in rows if r["workflow_run_id"]})
    if rids:
        for w in await conn.fetch("SELECT id, status FROM workflow_runs WHERE id = ANY($1::uuid[])", rids):
            run_status[str(w["id"])] = w["status"]
    return [{"id": str(r["id"]), "period_key": r["period_key"], "trigger_type": r["trigger_type"],
             "workflow_run_id": str(r["workflow_run_id"]) if r["workflow_run_id"] else None,
             "run_status": run_status.get(str(r["workflow_run_id"])) if r["workflow_run_id"] else None,
             "evaluated_on": r["evaluated_on"].isoformat(), "outcome": r["outcome"],
             "outcome_label": OUTCOME_LABELS[r["outcome"]], "reason": r["reason"], "holds": _loads(r["holds"]) or [],
             "instructions": [pays.get(str(i), {"id": str(i), "status": None}) for i in (r["instruction_ids"] or [])],
             "created_at": _iso(r["created_at"]), "completed_at": _iso(r["completed_at"])} for r in rows]


async def list_rules(conn, org_id, household_id, *, member: bool = False) -> list[dict]:
    """One entry per RULE: its running version (or its newest), any open version, last run, next run, holds."""
    rows = [_row(r) for r in await conn.fetch(
        f"SELECT * FROM {T} WHERE org_id = $1::uuid AND household_id = $2::uuid AND system_to IS NULL "
        "ORDER BY created_at, version_no", str(org_id), str(household_id))]
    if member:
        rows = [r for r in rows if r["status"] == "active"]
    labels = await _labels(conn, org_id, [a for r in rows for a in _rule_accounts(r)])
    users = {} if member else await _user_names(conn, [u for r in rows for u in [r["owner_id"], r.get("approved_by"),
                                                                              *r["maker_ids"]]])
    by_rule: dict[str, list[dict]] = {}
    for r in rows:
        by_rule.setdefault(str(r["rule_id"]), []).append(r)
    out = []
    for rid, vs in by_rule.items():
        running = next((v for v in vs if v["status"] in ("active", "paused")), None)
        open_ = next((v for v in vs if v["status"] in ("draft", "pending_approval")), None)
        head = running or open_ or vs[-1]
        if member and running is None:
            continue
        entry = {"rule_id": rid, "current": version_out(head, labels, users, member=member)}
        if not member:
            last = await conn.fetchrow(f"SELECT created_at, outcome FROM {T_FIRE} WHERE org_id = $1::uuid AND rule_id = "
                                       "$2::uuid ORDER BY created_at DESC LIMIT 1", str(org_id), rid)
            holds = int(await conn.fetchval(f"SELECT count(*) FROM {T_FIRE} WHERE org_id = $1::uuid AND rule_id = "
                                            "$2::uuid AND outcome IN ('held', 'partly_held', 'firing')", str(org_id), rid))
            entry.update({"open_version": version_out(open_, labels, users) if open_ and open_ is not head else None,
                          "last_run": {"at": _iso(last["created_at"]), "outcome": last["outcome"],
                                       "outcome_label": OUTCOME_LABELS[last["outcome"]]} if last else None,
                          "next_run": await _next_run(conn, head) if running else None,
                          "next_run_label": ("on each incoming credit" if head["frequency"] == "on_incoming"
                                             and head["status"] == "active" else None),
                          "holds": holds})
        out.append(entry)
    return out


async def detail(conn, org_id, version_id) -> dict:
    row = await load(conn, org_id, version_id)
    vs = await versions_of(conn, org_id, row["rule_id"])
    labels = await _labels(conn, org_id, [a for v in vs for a in _rule_accounts(v)])
    users = await _user_names(conn, [u for v in vs for u in [v["owner_id"], v.get("approved_by"), *v["maker_ids"]]])
    return {"version": version_out(row, labels, users), "household_id": str(row["household_id"]),
            "versions": [version_out(v, labels, users) for v in vs],
            "history": await firings_of(conn, org_id, row["rule_id"])}


async def preview(pool, conn, org_id, household_id, data: dict) -> dict:
    """What the rule WOULD draft today (the same evaluator, live balances) — writes nothing. A definition that does
    not pass the account checks says why instead."""
    clean = normalise(data)
    t = today()
    reasons = []
    if data.get("_owner_id"):
        try:
            await validate(pool, conn, org_id=org_id, owner_id=data["_owner_id"], household_id=household_id,
                           clean=clean, t=t, what="preview a cash rule", audit=False)
        except RuleError as exc:
            reasons = exc.reasons
    balances = await account_balances(conn, org_id, read_accounts(clean), t)
    amt, per_day = await ceilings(conn, org_id)
    labels = await _labels(conn, org_id, _rule_accounts(clean))
    incoming = []
    if clean["frequency"] == "on_incoming":
        incoming = [{"transaction_id": str(uuid.UUID(int=0)), "account_id": str(clean["source_account_id"]),
                     "amount": parse_cents(data.get("example_credit") or "1000.00", "the example credit")}]
    ev = evaluate({**clean, "status": "active"}, balances, incoming, t, ceiling_amount=amt, ceiling_per_day=per_day,
                  labels=labels)
    out = ev.out()
    for d in out["drafts"]:
        d["source"] = _acct(d["source_account_id"], labels)
        d["destination"] = _acct(d["destination_account_id"], labels)
    out["balances"] = [{**_acct(a, labels), "available": money(b.available) if b.available is not None else None,
                        "as_of": _iso(b.as_of)} for a, b in balances.items()]
    out["refusals"] = reasons
    out["example_credit"] = money(incoming[0]["amount"]) if incoming else None
    out["would_send"] = False
    return out


async def options(pool, conn, org_id, household_id, actor_id) -> dict:
    """The editor's choices, decided server-side: sources (the accounts this caller may make a rule on — both
    authorities, with the reasons for the others), each source's SAME-OWNER destinations and its declared rails."""
    t = today()
    accts = await PS.household_accounts(conn, org_id, household_id, t)
    srcs = []
    for a in accts:
        reasons = await PS.authority_refusals(pool, conn, org_id, actor_id, a)
        rails = await PS.declared_rails(conn, a)
        dests = [b for b in accts if PS.same_owner_refusal(a, b, household_id, a["owners"], b["owners"]) is None]
        srcs.append({"id": str(a["id"]), "account_number_masked": a["account_number_masked"],
                     "eligible": not reasons, "reasons": reasons,
                     "rails": [{"key": k, "label": dict(PS.RAILS)[k]} for k in PS.RAIL_KEYS if rails[k]["available"]],
                     "destinations": [{"id": str(b["id"]), "account_number_masked": b["account_number_masked"]}
                                      for b in dests]})
    amt, per_day = await ceilings(conn, org_id)
    return {"sources": srcs, "today": t.isoformat(),
            "ceilings": {"max_amount_per_firing": money(amt), "max_instructions_per_day": per_day,
                         "settings": [SETTING_MAX_AMOUNT, SETTING_MAX_PER_DAY]},
            "max_destinations": MAX_DESTINATIONS, "fire_time": f"{FIRE_HOUR}:00 Eastern"}


def vocabularies(*, can_write: bool) -> dict:
    return {"rule_types": [{"key": k, "label": v} for k, v in TYPES],
            "bases": [{"key": k, "label": v} for k, v in BASES],
            "conditions": [{"key": k, "label": v} for k, v in CONDITIONS],
            "frequencies": [{"key": k, "label": v} for k, v in FREQUENCIES],
            "weekdays": [{"key": k, "label": v} for k, v in WEEKDAYS],
            "rails": [{"key": k, "label": v} for k, v in PS.RAILS],
            "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "outcomes": [{"key": k, "label": v} for k, v in OUTCOME_LABELS.items()],
            "editable": list(EDITABLE) if can_write else [], "inline_editable": []}


def actions_for(row: dict, *, can_write: bool, can_approve: bool, user_id, member: bool) -> list[dict]:
    """What the caller may do with this version — decided here (a maker never sees Approve: the floor)."""
    if member:
        return []
    out = []
    st, pend = row["status"], row.get("pending_action")
    if can_write:
        if st == "draft":
            out += [{"key": "edit", "label": "Edit"}, {"key": "submit", "label": "Submit for approval"},
                    {"key": "retire", "label": "Discard draft"}]
        if st == "active":
            out += [{"key": "new_version", "label": "Edit (new version)"}, {"key": "pause", "label": "Pause"},
                    {"key": "retire", "label": "Retire"}]
        if st == "paused" and not pend:
            out += [{"key": "resume", "label": "Ask to resume"}, {"key": "new_version", "label": "Edit (new version)"},
                    {"key": "retire", "label": "Retire"}]
    awaiting = st == "pending_approval" or (st == "paused" and pend == "resume")
    if can_approve and awaiting and user_id is not None and str(user_id) not in decision_makers({
            "maker_ids": row.get("maker_ids") or [], "owner_id": row.get("owner_id"),
            "pending_action": pend, "resume_requested_by": row.get("resume_requested_by")}):
        out += [{"key": "approve", "label": "Approve"}, {"key": "reject", "label": "Return"}]
    return out
