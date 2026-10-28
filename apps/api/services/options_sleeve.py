"""Replication Studio (rep8) — the OPTIONS SLEEVE: buffer (defined-outcome)
ETFs and covered-call / income ETFs replicated as the underlying plus an
options package.

PHASE-2 GATES ARE NOT MET — built honestly around them
──────────────────────────────────────────────────────────────────────────────
* Altruist's options level / FLEX access is unconfirmed (every capability
  'unknown' = not available), and no options trading API exists in this build:
  EVERY options trade is a MANUAL execution instruction (services.
  options_proposals). :func:`options_viability` says why; it never blocks the
  core.
* No options data source and no licensed price vendor: premiums, index levels,
  underlying prices and implied vols come ONLY from adviser-entered quotes
  (``portfolio.adviser_quotes`` — value, source text, as-of, entered_by; a new
  row per quote, never updated). Nothing is fetched, guessed or defaulted.
  Anything that needs a missing input reads "not priced — enter a quote".
  At-expiry payoffs need strikes only and are always available.

OPTION CONTRACTS live in the security master: a ``securities_global`` row of
type 'option' plus a PLATFORM ``portfolio.option_contracts`` row with its
terms (underlying, call / put, strike, expiry, European / American, cash /
physical, FLEX, multiplier, OCC symbol when listed, broad-based-index flag,
mini flag). The terms ARE the identity (``terms_key``): defining the same
contract twice returns the first (a platform job under ``app.is_super_admin``,
like rep1's fund creation). Positions, lots, instructions and wash-sale control
all reference the contract's ``global_security_id`` like any security.

THE PACKAGE (``portfolio.option_packages`` + ``option_package_legs``): a
sleeve's legs as roles (underlying | long_put_atm | short_put_buffer_end |
short_call_cap | short_call_income | long_put_tail), each with its ratio, its
strike as a % of the outcome-period start level and (once the start level is
quoted) as a level, its expiry and contract. Cap / buffer / floor are
COMPUTED from the legs (exact Decimal) and stored next to the fund's stated
values. Status draft → approved (maker-checker, approve_replication) → rolled
| expired; a draft is rejected with a note. Versioned on the system axis.

THE PAYOFF (pure, exact): at expiry, per unit of start level, an underlying
return r gives s = 1 + r and

    value(s) = Σ underlying ratio·s + Σ ±ratio·intrinsic(strike_pct, s)
    replica return = (value(s) − Σ underlying ratio) ÷ Σ underlying ratio

with + for a long leg, − for a short one; premiums excluded (a package opened
at zero net premium — stated). The buffer ETF's stated outcome:
r ≥ 0 → min(r, cap); −buffer ≤ r < 0 → 0; r < −buffer → r + buffer (and never
below a stated floor).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal

import asyncpg

from services import human_tasks as HT
from services import models as M
from services import replica_terms as T

log = logging.getLogger(__name__)

T_CON = "portfolio.option_contracts"
T_PKG = "portfolio.option_packages"
T_LEG = "portfolio.option_package_legs"
T_QUOTE = "portfolio.adviser_quotes"
T_SETTLE = "public.option_settlement_entries"
VIEW, MANAGE, APPROVE = "view_replication", "manage_replication", "approve_replication"
SUBJECT = "option_package"
TASK_APPROVAL = "option_package_approval"
TASK_ROLL = "option_roll_due"
TASK_EXPIRED = "option_package_expired"

OPTIONS_CLASSES = ("buffer_etf", "income_etf")
KIND_FOR_CLASS = {"buffer_etf": "buffer", "income_etf": "income"}
KIND_LABELS = {"buffer": "Buffer (defined outcome)", "income": "Covered call / income"}
LEG_ROLE_LABELS = {
    "underlying": "Underlying (the core sleeve)", "long_put_atm": "Long put at the money (starts the buffer)",
    "short_put_buffer_end": "Short put (ends the buffer)", "short_call_cap": "Short call (sets the cap)",
    "short_call_income": "Short call (income)", "long_put_tail": "Long put (tail protection)",
}
ROLE_SHAPE = {"long_put_atm": ("put", "long"), "short_put_buffer_end": ("put", "short"),
              "short_call_cap": ("call", "short"), "short_call_income": ("call", "short"),
              "long_put_tail": ("put", "long")}
QUOTE_KIND_LABELS = {"premium": "Option premium (per share of the multiplier)", "index_level": "Index level",
                     "underlying_price": "Underlying price (stock or fund)", "implied_vol": "Implied volatility"}
STATUS_LABELS = {"draft": "Draft", "approved": "Approved", "rejected": "Rejected", "rolled": "Rolled",
                 "expired": "Expired"}
CORE_SOURCE_LABELS = {"index_replica": "The underlying basket (an approved index replica)",
                      "etf_itself": "The ETF itself"}
EXERCISE_STYLES = ("european", "american")
SETTLEMENTS = ("cash", "physical")
CALL_RULES = ("pct_otm", "delta")
CALL_RULE_LABELS = {"pct_otm": "Strike a percent out of the money", "delta": "Strike at a target delta"}

TOLERANCE_SETTING = "replication.options.hedge_ratio_tolerance"
ROLL_NOTICE_SETTING = "replication.options.roll_notice_days"
PAYOFF_STEP_SETTING = "replication.options.payoff_step_pct"
DEFAULT_TOLERANCE = Decimal("0.05")
DEFAULT_ROLL_NOTICE = 10
DEFAULT_STEP = Decimal("0.05")
PAYOFF_LO, PAYOFF_HI = Decimal("-0.50"), Decimal("0.50")
DEFAULT_MULTIPLIER = Decimal(100)
#: options level an options sleeve needs at the custodian (spreads with short legs: 3; covered calls: 1)
LEVEL_NEEDED = {"buffer": 3, "income": 1}
NOT_PRICED = "not priced — enter a quote"
ZERO, ONE = Decimal(0), Decimal(1)
CENT = Decimal("0.01")
ESTIMATE = "estimate"


class OptionsError(ValueError):
    """A request the rules refuse (422)."""


class OptionsNotFound(LookupError):
    pass


class OptionsConflict(RuntimeError):
    """The state does not allow it (409)."""

    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.missing = missing or []


class MakerCheckerViolation(PermissionError):
    pass


MAKER_CHECKER_RULE = ("Maker-checker: you made or priced this package, so a different holder of approve_replication "
                      "must decide it")


# ── small helpers ───────────────────────────────────────────────────────────
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
        raise OptionsNotFound(f"{what} not found") from None


def D(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, float):
        return Decimal(repr(v))
    return Decimal(str(v))


def dstr(v: Decimal | None) -> str | None:
    if v is None:
        return None
    s = format(v.normalize(), "f")
    return "0" if s in ("-0", "") else s


def _need_decimal(v, what: str, *, positive: bool = True) -> Decimal:
    if isinstance(v, bool) or isinstance(v, float):
        raise OptionsError(f"{what} must be a decimal string, not a float")
    try:
        d = Decimal(str(v).strip())
    except Exception:  # noqa: BLE001
        raise OptionsError(f"{what} must be a number") from None
    if not d.is_finite():
        raise OptionsError(f"{what} must be a finite number")
    if positive and d <= 0:
        raise OptionsError(f"{what} must be greater than zero")
    return d


def _iso(v):
    return v.isoformat() if v is not None else None


async def _setting(conn, org_id, key: str, default, *, lo=None, hi=None, integer: bool = False):
    from services import rebalance_proposals as RP

    return await RP._setting(conn, org_id, key, default, lo=lo, hi=hi, integer=integer)


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"options.{action}", SUBJECT, str(resource_id),
        _dumps(payload))


async def _publish(pool, org_id, action: str, resource_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    try:
        await publish_event(pool, str(org_id), f"options.{action}", SUBJECT, str(resource_id),
                            json.loads(_dumps(payload)), created_by=str(actor_id) if actor_id else None)
    except Exception:  # noqa: BLE001 — the action stands; the event is a notification
        log.exception("publishing options.%s failed", action)


# ── security master: option contracts ──────────────────────────────────────
async def _security(conn, gsid) -> dict:
    row = await conn.fetchrow(
        "SELECT g.id, g.name, g.security_type, (SELECT id_value FROM portfolio.securities_global_identifiers i "
        "WHERE i.global_security_id = g.id AND i.id_type = 'ticker' AND i.valid_to IS NULL AND i.system_to IS NULL "
        "ORDER BY i.is_primary DESC LIMIT 1) AS ticker FROM portfolio.securities_global g WHERE g.id = $1::uuid "
        "AND g.system_to IS NULL", _uuid(gsid, "security"))
    if row is None:
        raise OptionsNotFound("security not found in the security master")
    return dict(row)


def underlying_kind(security_type: str) -> str:
    if security_type == "index":
        return "index"
    if security_type == "equity":
        return "equity"
    if security_type == "fund":
        return "fund"
    raise OptionsError(f"an option's underlying is an index, a stock or a fund (not {security_type!r})")


def terms_key(*, underlying, option_type: str, strike: Decimal, expiry: date, exercise_style: str, settlement: str,
              is_flex: bool, multiplier: Decimal, is_mini: bool, mini_ratio: Decimal | None) -> str:
    return "|".join([str(underlying), option_type, dstr(strike), expiry.isoformat(), exercise_style, settlement,
                     "flex" if is_flex else "listed", dstr(multiplier),
                     f"mini:{dstr(mini_ratio)}" if is_mini else "std"])


def contract_name(label: str, c: dict) -> str:
    return (f"{label} {c['expiry'].isoformat()} {dstr(D(c['strike']))} {c['option_type'].upper()} "
            f"({'FLEX' if c['is_flex'] else 'listed'}, {c['exercise_style']}, {c['settlement']}-settled, "
            f"×{dstr(D(c['multiplier']))}{', mini' if c.get('is_mini') else ''})")


def contract_out(c: dict | None) -> dict | None:
    if not c:
        return None
    return {"id": str(c["id"]), "global_security_id": str(c["global_security_id"]),
            "underlying_global_security_id": str(c["underlying_global_security_id"]),
            "underlying_kind": c["underlying_kind"], "option_type": c["option_type"], "strike": dstr(D(c["strike"])),
            "expiry": _iso(c["expiry"]), "exercise_style": c["exercise_style"], "settlement": c["settlement"],
            "is_flex": c["is_flex"], "multiplier": dstr(D(c["multiplier"])), "occ_symbol": c["occ_symbol"],
            "exchange_listed": c["exchange_listed"], "broad_based_index": c["broad_based_index"],
            "is_mini": c["is_mini"], "mini_ratio": dstr(D(c["mini_ratio"])), "name": c.get("name")}


async def load_contract(conn, *, contract_id=None, global_security_id=None) -> dict | None:
    if contract_id is not None:
        where, arg = "c.id = $1::uuid", _uuid(contract_id, "option contract")
    else:
        where, arg = "c.global_security_id = $1::uuid", _uuid(global_security_id, "security")
    row = await conn.fetchrow(
        f"SELECT c.*, g.name FROM {T_CON} c JOIN portfolio.securities_global g ON g.id = c.global_security_id "
        f"WHERE {where} AND c.valid_to IS NULL AND c.system_to IS NULL", arg)
    return dict(row) if row else None


async def contracts_by_security(conn, gsids) -> dict[str, dict]:
    ids = [str(g) for g in gsids if g]
    if not ids:
        return {}
    rows = await conn.fetch(
        f"SELECT c.*, g.name FROM {T_CON} c JOIN portfolio.securities_global g ON g.id = c.global_security_id "
        "WHERE c.global_security_id = ANY($1::uuid[]) AND c.valid_to IS NULL AND c.system_to IS NULL", ids)
    return {str(r["global_security_id"]): dict(r) for r in rows}


async def find_or_create_contract(pool, *, actor_id, underlying_global_security_id, option_type: str, strike,
                                  expiry: date, exercise_style: str, settlement: str, is_flex: bool, multiplier,
                                  occ_symbol: str | None = None, exchange_listed: bool = True,
                                  broad_based_index: bool | None = None, is_mini: bool = False,
                                  mini_ratio=None) -> dict:
    """Define an option contract (idempotent on its terms). A platform job: the
    contract row and its 'option' security are written under
    ``app.is_super_admin`` in their own transaction — exactly how rep1 creates a
    missing fund. Returns the live contract (the existing one when the same
    terms are already on file)."""
    from services import securities_global as SG
    from services.replica_holdings import platform_tx

    if option_type not in ("call", "put"):
        raise OptionsError("an option is a call or a put")
    if exercise_style not in EXERCISE_STYLES:
        raise OptionsError(f"exercise style is one of {list(EXERCISE_STYLES)}")
    if settlement not in SETTLEMENTS:
        raise OptionsError(f"settlement is one of {list(SETTLEMENTS)}")
    if not isinstance(expiry, date):
        raise OptionsError("expiry is a date")
    strike = _need_decimal(strike, "strike")
    multiplier = _need_decimal(multiplier, "multiplier")
    if not isinstance(is_flex, bool) or not isinstance(exchange_listed, bool) or not isinstance(is_mini, bool):
        raise OptionsError("is_flex, exchange_listed and is_mini are yes / no")
    ratio = _need_decimal(mini_ratio, "mini ratio") if is_mini else None
    if is_mini and not (ZERO < ratio < ONE):
        raise OptionsError("a mini contract's ratio to the full index level is between 0 and 1 (e.g. 0.1)")
    if not is_mini and mini_ratio is not None:
        raise OptionsError("only a mini contract carries a mini ratio")
    occ = (occ_symbol or "").strip() or None
    if occ and is_flex:
        raise OptionsError("a FLEX contract has no listed OCC symbol")
    async with pool.acquire() as conn:
        und = await _security(conn, underlying_global_security_id)
    kind = underlying_kind(und["security_type"])
    if kind != "index" and settlement == "cash":
        raise OptionsError("only index options settle in cash here; a stock or fund option settles physically")
    if broad_based_index is None:
        raise OptionsError("state whether the underlying index is broad-based (it decides Section 1256)"
                           if kind == "index" else "broad_based_index is false for a stock or fund option")
    if broad_based_index and kind != "index":
        raise OptionsError("only an index is broad-based")
    key = terms_key(underlying=und["id"], option_type=option_type, strike=strike, expiry=expiry,
                    exercise_style=exercise_style, settlement=settlement, is_flex=is_flex, multiplier=multiplier,
                    is_mini=is_mini, mini_ratio=ratio)
    async with pool.acquire() as conn:
        got = await conn.fetchrow(f"SELECT id FROM {T_CON} WHERE terms_key = $1 AND valid_to IS NULL "
                                  "AND system_to IS NULL", key)
        if got:
            return await load_contract(conn, contract_id=got["id"])
    label = und["ticker"] or und["name"]
    c = {"expiry": expiry, "strike": strike, "option_type": option_type, "is_flex": is_flex,
         "exercise_style": exercise_style, "settlement": settlement, "multiplier": multiplier, "is_mini": is_mini}
    try:
        async with platform_tx(pool) as conn:
            gsid = await SG.create_security(conn, name=contract_name(label, c), security_type="option",
                                            short_name=occ, currency_code="USD", is_super_admin=True)
            cid = await conn.fetchval(
                f"""INSERT INTO {T_CON} (global_security_id, underlying_global_security_id, underlying_kind,
                        option_type, strike, expiry, exercise_style, settlement, is_flex, multiplier, occ_symbol,
                        exchange_listed, broad_based_index, is_mini, mini_ratio, terms_key, created_by)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                            $17::uuid) RETURNING id""",
                str(gsid), str(und["id"]), kind, option_type, strike, expiry, exercise_style, settlement, is_flex,
                multiplier, occ, exchange_listed, bool(broad_based_index), is_mini, ratio, key,
                str(actor_id) if actor_id else None)
    except asyncpg.UniqueViolationError:
        async with pool.acquire() as conn:
            got = await conn.fetchrow(f"SELECT id FROM {T_CON} WHERE terms_key = $1 AND valid_to IS NULL "
                                      "AND system_to IS NULL", key)
            return await load_contract(conn, contract_id=got["id"])
    async with pool.acquire() as conn:
        return await load_contract(conn, contract_id=cid)


async def list_contracts(conn, *, underlying_global_security_id=None, limit: int = 200) -> list[dict]:
    args, where = [], "c.valid_to IS NULL AND c.system_to IS NULL"
    if underlying_global_security_id:
        args.append(_uuid(underlying_global_security_id, "security"))
        where += " AND c.underlying_global_security_id = $1::uuid"
    rows = await conn.fetch(
        f"SELECT c.*, g.name FROM {T_CON} c JOIN portfolio.securities_global g ON g.id = c.global_security_id "
        f"WHERE {where} ORDER BY c.expiry, c.option_type, c.strike LIMIT {int(limit)}", *args)
    return [contract_out(dict(r)) for r in rows]


# ── adviser quotes ──────────────────────────────────────────────────────────
def _age(as_of: datetime, now: datetime) -> tuple[int, str]:
    secs = max(0, int((now - as_of).total_seconds()))
    days = secs // 86400
    if days >= 1:
        return days, f"{days} day{'s' if days != 1 else ''} old"
    hours = secs // 3600
    if hours >= 1:
        return 0, f"{hours} hour{'s' if hours != 1 else ''} old"
    return 0, f"{secs // 60} minute{'s' if secs // 60 != 1 else ''} old"


def quote_out(q: dict | None, now: datetime | None = None) -> dict | None:
    if not q:
        return None
    now = now or datetime.now(timezone.utc)
    days, text = _age(q["as_of"], now)
    return {"id": str(q["id"]), "global_security_id": str(q["global_security_id"]), "kind": q["kind"],
            "kind_label": QUOTE_KIND_LABELS[q["kind"]], "value": dstr(D(q["value"])), "source": q["source"],
            "as_of": q["as_of"].isoformat(), "entered_by": str(q["entered_by"]), "entered_at": _iso(q["created_at"]),
            "age_days": days, "age": text, "security_name": q.get("security_name")}


async def enter_quote(pool, *, org_id, actor_id, global_security_id, kind: str, value, source: str,
                      as_of: datetime) -> dict:
    """A new quote is a new row — never an update. The guard trigger checks the
    instrument fits the kind (a premium / implied vol on an option contract, an
    index level on an index, an underlying price on a stock or fund)."""
    if kind not in QUOTE_KIND_LABELS:
        raise OptionsError(f"quote kind is one of {list(QUOTE_KIND_LABELS)}")
    v = _need_decimal(value, "quote value")
    if not (source or "").strip():
        raise OptionsError("name the quote's source (e.g. 'Cboe screen, desk call with …')")
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise OptionsError("the quote's as-of needs a date, time and time zone")
    if as_of > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise OptionsError("a quote's as-of cannot be in the future")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        await _security(conn, global_security_id)
        try:
            row = await conn.fetchrow(
                f"INSERT INTO {T_QUOTE} (org_id, global_security_id, kind, value, source, as_of, entered_by) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::uuid) RETURNING *",
                str(org_id), _uuid(global_security_id, "security"), kind, v, source.strip(), as_of, str(actor["id"]))
        except asyncpg.CheckViolationError as exc:
            raise OptionsError(str(exc).split("\n")[0]) from None
        await _audit(conn, org_id, actor["id"], "quote_entered", row["id"],
                     {"global_security_id": str(global_security_id), "kind": kind, "value": str(v),
                      "source": source.strip(), "as_of": as_of.isoformat()})
    return quote_out(dict(row))


async def latest_quote(conn, org_id, global_security_id, kind: str, *, on_date: date | None = None) -> dict | None:
    """The newest live quote of this kind for the instrument (as-of not in the
    future); with ``on_date``, the newest whose as-of falls on that UTC date."""
    args = [str(org_id), str(global_security_id), kind]
    extra = " AND as_of <= now() + interval '5 minutes'"
    if on_date is not None:
        args.append(on_date)
        extra += " AND (as_of AT TIME ZONE 'UTC')::date = $4::date"
    row = await conn.fetchrow(
        f"SELECT q.*, g.name AS security_name FROM {T_QUOTE} q JOIN portfolio.securities_global g "
        "ON g.id = q.global_security_id WHERE q.org_id = $1::uuid AND q.global_security_id = $2::uuid "
        f"AND q.kind = $3 AND q.valid_to IS NULL AND q.system_to IS NULL{extra} ORDER BY q.as_of DESC, "
        "q.created_at DESC LIMIT 1", *args)
    return dict(row) if row else None


async def list_quotes(conn, org_id, *, global_security_ids=None, kind: str | None = None,
                      limit: int = 200) -> list[dict]:
    args: list = [str(org_id)]
    where = "q.org_id = $1::uuid AND q.valid_to IS NULL AND q.system_to IS NULL"
    if global_security_ids:
        args.append([_uuid(g, "security") for g in global_security_ids])
        where += f" AND q.global_security_id = ANY(${len(args)}::uuid[])"
    if kind:
        if kind not in QUOTE_KIND_LABELS:
            raise OptionsError(f"quote kind is one of {list(QUOTE_KIND_LABELS)}")
        args.append(kind)
        where += f" AND q.kind = ${len(args)}"
    now = datetime.now(timezone.utc)
    rows = await conn.fetch(
        f"SELECT q.*, g.name AS security_name FROM {T_QUOTE} q JOIN portfolio.securities_global g "
        f"ON g.id = q.global_security_id WHERE {where} ORDER BY q.as_of DESC, q.created_at DESC LIMIT {int(limit)}",
        *args)
    return [quote_out(dict(r), now) for r in rows]


def level_kind(underlying_type: str) -> str:
    """The quote kind that prices the underlying: an index level, else a price."""
    return "index_level" if underlying_type == "index" else "underlying_price"


# ── the payoff (pure, exact) ────────────────────────────────────────────────
@dataclass(frozen=True)
class PLeg:
    role: str
    option_type: str | None
    direction: str
    ratio: Decimal
    strike_pct: Decimal | None


def legs_from_rows(rows: list[dict]) -> list[PLeg]:
    return [PLeg(r["role"], r["option_type"], r["direction"], D(r["ratio"]), D(r["strike_pct"])) for r in rows]


def intrinsic(option_type: str, k: Decimal, s: Decimal) -> Decimal:
    return max(s - k, ZERO) if option_type == "call" else max(k - s, ZERO)


def package_value(legs: list[PLeg], s: Decimal) -> Decimal:
    v = ZERO
    for leg in legs:
        if leg.role == "underlying":
            v += leg.ratio * s
        elif leg.strike_pct is not None:
            sign = ONE if leg.direction == "long" else -ONE
            v += sign * leg.ratio * intrinsic(leg.option_type, leg.strike_pct, s)
    return v


def package_return(legs: list[PLeg], r: Decimal) -> Decimal:
    """The replica's at-expiry return for an underlying return ``r`` (exact;
    premiums excluded)."""
    u = sum((x.ratio for x in legs if x.role == "underlying"), ZERO)
    if u <= 0:
        raise OptionsError("a package needs its underlying leg")
    return (package_value(legs, ONE + r) - u) / u


def stated_outcome(r: Decimal, *, cap: Decimal | None, buffer: Decimal | None, floor: Decimal | None = None
                   ) -> Decimal | None:
    """The buffer ETF's stated at-expiry outcome; None when it states no cap /
    buffer."""
    if cap is None or buffer is None:
        return None
    if r >= 0:
        out = min(r, cap)
    elif r >= -buffer:
        out = ZERO
    else:
        out = r + buffer
    if floor is not None:
        out = max(out, floor)
    return out


def computed_terms(legs: list[PLeg], kind: str) -> dict:
    """Cap / buffer / floor from the legs. cap = the flat return above the
    highest short call (None when uncapped or a cap leg is missing); buffer =
    the ATM long put strike − the short put strike; floor = the worst at-expiry
    return (s = 0)."""
    if kind != "buffer":
        return {"cap_pct": None, "buffer_pct": None, "floor_pct": None}
    calls = [x for x in legs if x.role == "short_call_cap" and x.strike_pct is not None]
    lp = [x for x in legs if x.role == "long_put_atm" and x.strike_pct is not None]
    sp = [x for x in legs if x.role == "short_put_buffer_end" and x.strike_pct is not None]
    cap = None
    if calls:
        top = max(x.strike_pct for x in calls)
        a, b = package_return(legs, top), package_return(legs, top + ONE)    # two returns above the cap strike
        cap = a if a == b else None
    buffer = (lp[0].strike_pct - sp[0].strike_pct) if (lp and sp) else None
    floor = package_return(legs, -ONE)
    return {"cap_pct": cap, "buffer_pct": buffer, "floor_pct": floor}


def payoff_grid(step: Decimal, edges: list[Decimal]) -> list[Decimal]:
    pts, r = set(), PAYOFF_LO
    while r <= PAYOFF_HI:
        pts.add(r)
        r += step
    for e in edges:
        if e is not None and PAYOFF_LO <= e <= PAYOFF_HI:
            pts.add(e)
    return sorted(pts)


def payoff_points(legs: list[PLeg], *, cap, buffer, floor, step: Decimal) -> list[dict]:
    edges = [ZERO] + ([-buffer] if buffer is not None else []) + ([cap] if cap is not None else [])
    out = []
    for r in payoff_grid(step, edges):
        rep = package_return(legs, r)
        st = stated_outcome(r, cap=cap, buffer=buffer, floor=floor)
        out.append({"underlying_return": dstr(r), "replica_return": dstr(rep),
                    "stated_return": dstr(st) if st is not None else None,
                    "difference": dstr(rep - st) if st is not None else None})
    return out


# ── packages: reading ───────────────────────────────────────────────────────
async def load_package(conn, org_id, package_id, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_PKG} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), _uuid(package_id, "option package"), str(org_id))
    if row is None:
        raise OptionsNotFound("option package not found")
    return dict(row)


async def legs_of(conn, package_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_LEG} WHERE package_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY leg_no", str(package_id))]


def leg_out(leg: dict, contract: dict | None = None) -> dict:
    return {"id": str(leg["id"]), "leg_no": leg["leg_no"], "role": leg["role"], "role_label": LEG_ROLE_LABELS[leg["role"]],
            "option_type": leg["option_type"], "direction": leg["direction"], "ratio": dstr(D(leg["ratio"])),
            "strike_pct": dstr(D(leg["strike_pct"])), "strike_level": dstr(D(leg["strike_level"])),
            "expiry": _iso(leg["expiry"]), "exercise_style": leg["exercise_style"], "settlement": leg["settlement"],
            "is_flex": leg["is_flex"], "multiplier": dstr(D(leg["multiplier"])),
            "contract_id": str(leg["contract_id"]) if leg["contract_id"] else None,
            "contract": contract_out(contract)}


def package_out(p: dict) -> dict:
    return {"id": str(p["id"]), "replica_security_id": str(p["replica_security_id"]), "model_id": str(p["model_id"]),
            "model_version_id": str(p["model_version_id"]) if p["model_version_id"] else None,
            "terms_version_id": str(p["terms_version_id"]) if p["terms_version_id"] else None,
            "package_no": p["package_no"], "kind": p["kind"], "kind_label": KIND_LABELS[p["kind"]],
            "core_source": p["core_source"], "core_source_label": CORE_SOURCE_LABELS[p["core_source"]],
            "core_model_id": str(p["core_model_id"]) if p["core_model_id"] else None,
            "underlying_global_security_id": str(p["underlying_global_security_id"]),
            "outcome_start": _iso(p["outcome_start"]), "outcome_end": _iso(p["outcome_end"]),
            "start_level": dstr(D(p["start_level"])),
            "start_level_quote_id": str(p["start_level_quote_id"]) if p["start_level_quote_id"] else None,
            "cap_pct": dstr(D(p["cap_pct"])), "buffer_pct": dstr(D(p["buffer_pct"])),
            "floor_pct": dstr(D(p["floor_pct"])), "stated_cap_pct": dstr(D(p["stated_cap_pct"])),
            "stated_buffer_pct": dstr(D(p["stated_buffer_pct"])), "stated_floor_pct": dstr(D(p["stated_floor_pct"])),
            "cap_text": (f"{dstr(D(p['cap_pct']) * 100)}%" if p["cap_pct"] is not None
                         else ("not priced — enter quotes and set the cap" if p["kind"] == "buffer" else None)),
            "call_rule": p["call_rule"], "call_otm_pct": dstr(D(p["call_otm_pct"])),
            "call_delta": dstr(D(p["call_delta"])), "tenor_days": p["tenor_days"],
            "coverage_pct": dstr(D(p["coverage_pct"])), "status": p["status"],
            "status_label": STATUS_LABELS[p["status"]], "status_reason": p["status_reason"],
            "rolled_from_id": str(p["rolled_from_id"]) if p["rolled_from_id"] else None, "notes": p["notes"],
            "maker_ids": [str(m) for m in p["maker_ids"] or []],
            "checker_id": str(p["checker_id"]) if p["checker_id"] else None,
            "approval_task_id": str(p["approval_task_id"]) if p["approval_task_id"] else None,
            "decided_at": _iso(p["decided_at"]), "decision_note": p["decision_note"],
            "created_at": _iso(p["created_at"])}


def priced(p: dict, legs: list[dict]) -> list[str]:
    """What the package still needs before it can be approved (empty = ready)."""
    need = []
    if p["start_level"] is None:
        need.append("a start level (enter an index-level / price quote and set it)")
    if p["kind"] == "buffer" and not any(x["role"] == "short_call_cap" for x in legs):
        need.append("the cap leg (not priced — enter call premium quotes and set the cap)")
    if p["kind"] == "income" and not any(x["role"] == "short_call_income" for x in legs):
        need.append("the written-call strike (not priced — enter quotes and set it)")
    if any(x["role"] != "underlying" and x["contract_id"] is None for x in legs) and p["start_level"] is not None:
        need.append("every option leg's contract")
    return need


async def packages_of_entry(conn, org_id, entry_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_PKG} WHERE org_id = $1::uuid AND replica_security_id = $2::uuid AND system_to IS NULL "
        "ORDER BY package_no DESC", str(org_id), str(entry_id))]


# ── terms → package ────────────────────────────────────────────────────────
BUFFER_TERMS = ("outcome_period_start", "outcome_period_end", "cap_pct", "buffer_pct", "leg_long_put_strike_pct",
                "leg_short_put_strike_pct", "leg_short_call_strike_pct")
INCOME_TERMS = ("call_strike_rule", "call_tenor_days", "call_coverage_pct")


def _term(terms: dict, key: str):
    v = (_loads(terms["term_values"]) or {}).get(key) or {}
    return v.get("value")


def _pct(terms: dict, key: str) -> Decimal | None:
    v = _term(terms, key)
    return None if v is None else D(v) / 100


def missing_terms(terms: dict | None, kind: str) -> list[str]:
    if terms is None:
        return ["an approved terms version"]
    need = BUFFER_TERMS if kind == "buffer" else INCOME_TERMS
    out = [f"approved term {k}" for k in need if _term(terms, k) is None]
    if kind == "income":
        rule = _term(terms, "call_strike_rule")
        if rule == "pct_otm" and _term(terms, "call_strike_pct_otm") is None:
            out.append("approved term call_strike_pct_otm")
        if rule not in (None, *CALL_RULES):
            out.append("a call-writing rule this build models (pct_otm or delta)")
    return out


def _opt_leg(role: str, *, ratio: Decimal, strike_pct: Decimal, expiry: date, style: str, settlement: str,
             is_flex: bool, multiplier: Decimal) -> dict:
    ot, direction = ROLE_SHAPE[role]
    return {"role": role, "option_type": ot, "direction": direction, "ratio": ratio, "strike_pct": strike_pct,
            "strike_level": None, "expiry": expiry, "contract_id": None, "exercise_style": style,
            "settlement": settlement, "is_flex": is_flex, "multiplier": multiplier}


def _underlying_leg() -> dict:
    return {"role": "underlying", "option_type": None, "direction": "long", "ratio": ONE, "strike_pct": None,
            "strike_level": None, "expiry": None, "contract_id": None, "exercise_style": None, "settlement": None,
            "is_flex": None, "multiplier": None}


async def _strike_contracts(pool, *, actor_id, underlying_id, legs: list[dict], start_level: Decimal,
                            broad_based: bool) -> list[dict]:
    """Strike levels from the start level (cents, half-even) and each option
    leg's contract (found or defined)."""
    out = []
    for leg in legs:
        leg = dict(leg)
        if leg["role"] != "underlying" and leg["strike_pct"] is not None:
            level = (D(leg["strike_pct"]) * start_level).quantize(CENT, rounding=ROUND_HALF_EVEN)
            c = await find_or_create_contract(
                pool, actor_id=actor_id, underlying_global_security_id=underlying_id,
                option_type=leg["option_type"], strike=level, expiry=leg["expiry"],
                exercise_style=leg["exercise_style"], settlement=leg["settlement"], is_flex=bool(leg["is_flex"]),
                multiplier=D(leg["multiplier"]), broad_based_index=broad_based)
            leg["strike_level"], leg["contract_id"] = level, c["id"]
        out.append(leg)
    return out


async def _write_legs(conn, org_id, package_id, legs: list[dict], actor_id) -> None:
    await conn.execute(f"UPDATE {T_LEG} SET valid_to = now() WHERE package_id = $1::uuid AND valid_to IS NULL "
                       "AND system_to IS NULL", str(package_id))
    for i, leg in enumerate(legs, 1):
        await conn.execute(
            f"""INSERT INTO {T_LEG} (org_id, package_id, leg_no, role, option_type, direction, ratio, strike_pct,
                    strike_level, expiry, contract_id, exercise_style, settlement, is_flex, multiplier, created_by)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11::uuid, $12, $13, $14, $15,
                        $16::uuid)""",
            str(org_id), str(package_id), i, leg["role"], leg["option_type"], leg["direction"], D(leg["ratio"]),
            D(leg["strike_pct"]), D(leg["strike_level"]), leg["expiry"],
            str(leg["contract_id"]) if leg["contract_id"] else None, leg["exercise_style"], leg["settlement"],
            leg["is_flex"], D(leg["multiplier"]), str(actor_id) if actor_id else None)


async def _archive(conn, package_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {T_PKG}
            SELECT (jsonb_populate_record(NULL::{T_PKG}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {T_PKG} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(package_id))
    if got is None:
        raise OptionsConflict("the package is not live — refusing to change it")


async def _update(conn, package_id, fields: dict) -> dict:
    await _archive(conn, package_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    row = await conn.fetchrow(f"UPDATE {T_PKG} SET {sets}, system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(package_id), *fields.values())
    if row is None:
        raise OptionsConflict("the package changed underneath this request — reload and retry")
    return dict(row)


def _with_computed(kind: str, legs: list[dict]) -> dict:
    ct = computed_terms(legs_from_rows(legs), kind)
    return {"cap_pct": ct["cap_pct"], "buffer_pct": ct["buffer_pct"], "floor_pct": ct["floor_pct"]}


async def _start_quote(conn, org_id, quote_id, underlying: dict) -> dict:
    q = await conn.fetchrow(f"SELECT * FROM {T_QUOTE} WHERE id = $1::uuid AND org_id = $2::uuid AND valid_to IS NULL "
                            "AND system_to IS NULL", _uuid(quote_id, "quote"), str(org_id))
    if q is None:
        raise OptionsNotFound("quote not found")
    want = level_kind(underlying["security_type"])
    if str(q["global_security_id"]) != str(underlying["id"]) or q["kind"] != want:
        raise OptionsError(f"the start level is a {QUOTE_KIND_LABELS[want].lower()} quote on the package's underlying")
    return dict(q)


async def build(pool, *, org_id, actor_id, entry_id, underlying_global_security_id, core_model_id=None,
                core_global_security_id=None, broad_based_index: bool = True, is_flex: bool = True,
                exercise_style: str = "european", settlement: str | None = None, multiplier=DEFAULT_MULTIPLIER,
                fresh_period_start: date | None = None, start_level_quote_id=None,
                call_strike_pct=None, today: date | None = None) -> dict:
    """The "Holdings of …" builder for a buffer / income ETF (rep4 pattern).
    Core sleeve = the underlying basket (an APPROVED index replica model's
    look-through) or the ETF itself; options sleeve = the package (FLEX index
    options by default: European, cash-settled, broad-based → Section 1256).
    A fresh outcome period starting on the funding date is allowed (same length
    as the fund's). Find or build: one options model per registry entry."""
    today = today or date.today()
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        try:
            entry = await T.load_entry(conn, org_id, entry_id)
        except T.ReplicaNotFound as exc:
            raise OptionsNotFound(str(exc)) from None
        if entry["instrument_class"] not in OPTIONS_CLASSES:
            raise OptionsConflict(f"{entry['display_name']} is not a buffer or income ETF — the options builder "
                                  "is for those classes")
        kind = KIND_FOR_CLASS[entry["instrument_class"]]
        existing = await packages_of_entry(conn, org_id, entry["id"])
        if existing:
            model = await M.load_model(conn, org_id, existing[0]["model_id"])
            return {"joined": True, "model": M.model_out(model), "package": package_out(existing[0])}
        terms = await T.live_approved(conn, entry["id"])
        miss = missing_terms(terms, kind)
        if miss:
            raise OptionsConflict("cannot build the options sleeve yet — needs " + ", ".join(miss), miss)
        und = await _security(conn, underlying_global_security_id)
        ukind = underlying_kind(und["security_type"])
        if ukind != "index" and broad_based_index:
            broad_based_index = False      # only an index is broad-based (the default applies to index options)
        settle = settlement or ("cash" if ukind == "index" else "physical")
        if settle not in SETTLEMENTS or exercise_style not in EXERCISE_STYLES:
            raise OptionsError("settlement is cash | physical and exercise style european | american")
        mult = _need_decimal(multiplier, "multiplier")
        # the core sleeve
        if core_model_id:
            core_model = await M.load_model(conn, org_id, core_model_id)
            live = await M.live_approved(conn, core_model["id"])
            if live is None:
                raise OptionsConflict(f"{core_model['name']} has no approved version to use as the core basket")
            eff = await M.look_through(conn, live["id"])
            core_source = "index_replica"
            core_positions = []
            for i, (key, v) in enumerate(sorted(eff.items(), key=lambda kv: (kv[0] == "cash", -kv[1]["weight"],
                                                                              kv[0])), 1):
                core_positions.append({
                    "row_num": i, "global_security_id": v["global_security_id"], "raw_ticker": None,
                    "raw_cusip": None, "raw_isin": None, "raw_sedol": None, "raw_name": v["label"],
                    "target_weight": v["weight"], "asset_class": None, "sleeve_label": "core", "drift_min": None,
                    "drift_max": None, "alternates": [], "restrictions": None,
                    "notes": f"core basket from {core_model['name']} v{live['version_no']}",
                    "resolution_state": v["resolution_state"], "resolution_note": None})
        else:
            gsid = core_global_security_id or (und["id"] if ukind != "index" else None)
            if gsid is None:
                raise OptionsError("choose the core: an approved index replica model (the basket) or the ETF itself")
            core = await _security(conn, gsid)
            core_source, core_model = "etf_itself", None
            core_positions = [{"row_num": 1, "global_security_id": core["id"], "raw_ticker": core["ticker"],
                               "raw_cusip": None, "raw_isin": None, "raw_sedol": None, "raw_name": core["name"],
                               "target_weight": ONE, "asset_class": None, "sleeve_label": "core", "drift_min": None,
                               "drift_max": None, "alternates": [], "restrictions": None,
                               "notes": "core = the ETF itself", "resolution_state": "resolved",
                               "resolution_note": None}]
        start_q = await _start_quote(conn, org_id, start_level_quote_id, und) if start_level_quote_id else None
    # the outcome period and the legs
    if kind == "buffer":
        t_start = date.fromisoformat(str(_term(terms, "outcome_period_start")))
        t_end = date.fromisoformat(str(_term(terms, "outcome_period_end")))
        if t_end <= t_start:
            raise OptionsConflict("the approved outcome period ends before it starts — correct the terms")
        start = fresh_period_start or t_start
        end = start + (t_end - t_start) if fresh_period_start else t_end
        lp, sp, sc = (_pct(terms, "leg_long_put_strike_pct"), _pct(terms, "leg_short_put_strike_pct"),
                      _pct(terms, "leg_short_call_strike_pct"))
        style = exercise_style
        legs = [_underlying_leg()] + [
            _opt_leg(role, ratio=ONE, strike_pct=k, expiry=end, style=style, settlement=settle, is_flex=is_flex,
                     multiplier=mult)
            for role, k in (("long_put_atm", lp), ("short_put_buffer_end", sp), ("short_call_cap", sc))]
        extra = {"stated_cap_pct": _pct(terms, "cap_pct"), "stated_buffer_pct": _pct(terms, "buffer_pct"),
                 "stated_floor_pct": _pct(terms, "floor_pct"), "call_rule": None, "call_otm_pct": None,
                 "call_delta": None, "tenor_days": None, "coverage_pct": None}
    else:
        rule = _term(terms, "call_strike_rule")
        tenor = int(D(_term(terms, "call_tenor_days")))
        coverage = _pct(terms, "call_coverage_pct")
        start = fresh_period_start or today
        end = start + timedelta(days=tenor)
        if rule == "pct_otm":
            k = ONE + _pct(terms, "call_strike_pct_otm")
        else:
            if call_strike_pct is None:
                raise OptionsConflict("the fund writes calls at a target delta: enter the strike (as a percent of the "
                                      "start level) chosen for that delta — it is never computed without a model",
                                      ["call_strike_pct"])
            k = _need_decimal(call_strike_pct, "call strike pct") / 100
        legs = [_underlying_leg(), _opt_leg("short_call_income", ratio=coverage, strike_pct=k, expiry=end,
                                            style=exercise_style, settlement=settle, is_flex=is_flex,
                                            multiplier=mult)]
        delta = _term(terms, "call_delta")
        extra = {"stated_cap_pct": None, "stated_buffer_pct": None, "stated_floor_pct": None, "call_rule": rule,
                 "call_otm_pct": _pct(terms, "call_strike_pct_otm") if rule == "pct_otm" else None,
                 "call_delta": D(delta) if (rule == "delta" and delta is not None) else None, "tenor_days": tenor,
                 "coverage_pct": coverage}
        if rule == "delta" and extra["call_delta"] is None:
            raise OptionsConflict("the fund's target delta is not in its approved terms", ["call_delta"])
    start_level = D(start_q["value"]) if start_q else None
    if start_level is not None:
        legs = await _strike_contracts(pool, actor_id=actor["id"], underlying_id=und["id"], legs=legs,
                                       start_level=start_level, broad_based=bool(broad_based_index))
    refs = []
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                model, r1 = await M.create_fund_model(
                    conn, org_id=org_id, actor_id=actor["id"], name=T.display_name_for(entry["ticker"]),
                    replica_security_id=entry["id"], disclosure=T.disclosure_for(entry["ticker"]),
                    description=f"{KIND_LABELS[kind]}: core sleeve + options sleeve (manual execution only)")
                refs.append(r1)
                version = await M._create_version(
                    conn, org_id=org_id, model=model, origin="fund_build", maker_ids=[actor["id"]],
                    created_by=actor["id"], positions=core_positions, origin_ref=None,
                    notes=f"Core sleeve ({CORE_SOURCE_LABELS[core_source].lower()}); the options sleeve is option "
                          f"package #1 ({KIND_LABELS[kind].lower()}).")
                computed = _with_computed(kind, legs)
                pid = await conn.fetchval(
                    f"""INSERT INTO {T_PKG} (org_id, replica_security_id, model_id, model_version_id, terms_version_id,
                            package_no, kind, core_source, core_model_id, underlying_global_security_id,
                            outcome_start, outcome_end, start_level, start_level_quote_id, cap_pct, buffer_pct,
                            floor_pct, stated_cap_pct, stated_buffer_pct, stated_floor_pct, call_rule, call_otm_pct,
                            call_delta, tenor_days, coverage_pct, status, maker_ids, created_by, notes)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, 1, $6, $7, $8::uuid, $9::uuid, $10,
                                $11, $12, $13::uuid, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, 'draft',
                                ARRAY[$25::uuid], $25::uuid, $26) RETURNING id""",
                    str(org_id), str(entry["id"]), str(model["id"]), str(version["id"]), str(terms["id"]), kind,
                    core_source, str(core_model["id"]) if core_model else None, str(und["id"]), start, end,
                    start_level, str(start_q["id"]) if start_q else None, computed["cap_pct"],
                    computed["buffer_pct"], computed["floor_pct"], extra["stated_cap_pct"],
                    extra["stated_buffer_pct"], extra["stated_floor_pct"], extra["call_rule"],
                    extra["call_otm_pct"], extra["call_delta"], extra["tenor_days"], extra["coverage_pct"],
                    str(actor["id"]),
                    ("Fresh outcome period from the funding date" if fresh_period_start else None))
                await _write_legs(conn, org_id, pid, legs, actor["id"])
                refs.append(await M._event(conn, org_id=org_id, action="create_draft", actor_id=actor["id"],
                                           model_id=model["id"], version_id=version["id"],
                                           detail={"origin": "fund_build", "options_package_id": str(pid),
                                                   "kind": kind, "core_source": core_source}))
                await _audit(conn, org_id, actor["id"], "package_built", pid,
                             {"model_id": str(model["id"]), "entry_id": str(entry["id"]), "kind": kind,
                              "start_level_quote_id": str(start_q["id"]) if start_q else None})
                pkg = await load_package(conn, org_id, pid)
    except asyncpg.UniqueViolationError as exc:
        raise OptionsConflict(f"an options model for this fund was just built — reload ({exc.constraint_name})") \
            from None
    await M.publish(pool, refs)
    await _publish(pool, org_id, "package_built", pid, {"model_id": str(model["id"])}, actor["id"])
    return {"joined": False, "model": M.model_out(model), "version": M.version_out(version),
            "package": package_out(pkg)}


async def price_draft(pool, *, org_id, actor_id, package_id, start_level_quote_id=None, cap_strike_pct=None,
                      call_strike_pct=None, broad_based_index: bool | None = None) -> dict:
    """Price a DRAFT: set its start level from an index-level / price quote
    (strike levels and contracts follow), and — for a roll or a delta rule —
    the cap / written-call strike as a percent of the start level. The person
    becomes one of the package's makers (so cannot approve it)."""
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        p = await load_package(conn, org_id, package_id)
        if p["status"] != "draft":
            raise OptionsConflict(f"package #{p['package_no']} is {p['status']} — only a draft is priced")
        legs = await legs_of(conn, p["id"])
        und = await _security(conn, p["underlying_global_security_id"])
        start_q = await _start_quote(conn, org_id, start_level_quote_id, und) if start_level_quote_id else None
    template = next((x for x in legs if x["role"] != "underlying"), None)
    if template is None:
        raise OptionsConflict("the package has no option leg to copy terms from")
    if cap_strike_pct is not None:
        if p["kind"] != "buffer":
            raise OptionsError("a cap is set on a buffer package")
        k = _need_decimal(cap_strike_pct, "cap strike pct") / 100
        if k <= ONE:
            raise OptionsError("the cap call's strike is above the start level (more than 100%)")
        legs = [x for x in legs if x["role"] != "short_call_cap"] + [_opt_leg(
            "short_call_cap", ratio=ONE, strike_pct=k, expiry=p["outcome_end"], style=template["exercise_style"],
            settlement=template["settlement"], is_flex=template["is_flex"], multiplier=D(template["multiplier"]))]
    if call_strike_pct is not None:
        if p["kind"] != "income":
            raise OptionsError("a written-call strike is set on an income package")
        k = _need_decimal(call_strike_pct, "call strike pct") / 100
        legs = [x for x in legs if x["role"] != "short_call_income"] + [_opt_leg(
            "short_call_income", ratio=D(p["coverage_pct"]), strike_pct=k, expiry=p["outcome_end"],
            style=template["exercise_style"], settlement=template["settlement"], is_flex=template["is_flex"],
            multiplier=D(template["multiplier"]))]
    start_level = D(start_q["value"]) if start_q else D(p["start_level"])
    if start_level is not None:
        if broad_based_index is None:
            existing = next((x for x in legs if x.get("contract_id")), None)
            if existing is not None:
                async with pool.acquire() as conn:
                    broad_based_index = (await load_contract(conn, contract_id=existing["contract_id"]))[
                        "broad_based_index"]
            else:
                broad_based_index = und["security_type"] == "index"
        legs = await _strike_contracts(pool, actor_id=actor["id"], underlying_id=und["id"],
                                       legs=[{**x, "strike_level": None, "contract_id": None} for x in legs],
                                       start_level=start_level, broad_based=bool(broad_based_index))
    async with pool.acquire() as conn:
        async with conn.transaction():
            cur = await load_package(conn, org_id, package_id, lock=True)
            if cur["status"] != "draft":
                raise OptionsConflict("the package changed underneath this request")
            makers = sorted({*(str(m) for m in cur["maker_ids"] or []), str(actor["id"])})
            fields = {**_with_computed(cur["kind"], legs), "maker_ids": [uuid.UUID(m) for m in makers]}
            if start_q:
                fields.update(start_level=start_level, start_level_quote_id=uuid.UUID(str(start_q["id"])))
            await _update(conn, cur["id"], fields)
            await _write_legs(conn, org_id, cur["id"], legs, actor["id"])
            await _audit(conn, org_id, actor["id"], "package_priced", cur["id"],
                         {"start_level_quote_id": str(start_q["id"]) if start_q else None,
                          "cap_strike_pct": None if cap_strike_pct is None else str(cap_strike_pct),
                          "call_strike_pct": None if call_strike_pct is None else str(call_strike_pct)})
    async with pool.acquire() as conn:
        return await package_detail(conn, org_id, package_id)


# ── approval (maker-checker human task) ─────────────────────────────────────
async def submit_package(pool, *, org_id, actor_id, package_id) -> dict:
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        p = await load_package(conn, org_id, package_id)
        if p["status"] != "draft":
            raise OptionsConflict(f"package #{p['package_no']} is {p['status']}, not a draft")
        if p["approval_task_id"]:
            raise OptionsConflict("the package is already awaiting approval")
        legs = await legs_of(conn, p["id"])
        need = priced(p, legs)
        if need:
            raise OptionsConflict("not priced — the package still needs " + "; ".join(need), need)
        entry = await T.load_entry(conn, org_id, p["replica_security_id"])
    makers = sorted({*(str(m) for m in p["maker_ids"] or []), str(actor["id"])})
    task = await HT.open_task(pool, org_id=org_id, kind=TASK_APPROVAL, subject_type=SUBJECT, subject_id=p["id"],
                              maker_id=actor["id"], eligible_permission=APPROVE,
                              title=f"Approve option package #{p['package_no']}: {entry['display_name']}",
                              payload={"package_no": p["package_no"], "kind": p["kind"],
                                       "outcome_end": p["outcome_end"].isoformat()},
                              action_path=f"/portfolio/replication/{entry['id']}?tab=options")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load_package(conn, org_id, p["id"], lock=True)
                if cur["status"] != "draft":
                    raise OptionsConflict("the package changed underneath this request")
                await _update(conn, cur["id"], {"approval_task_id": uuid.UUID(task["id"]),
                                                "maker_ids": [uuid.UUID(m) for m in makers]})
                await _audit(conn, org_id, actor["id"], "package_submitted", cur["id"], {"task_id": task["id"]})
    except Exception:
        from services.workflow_engine import cancel_user_task

        try:
            await cancel_user_task(pool, uuid.UUID(task["id"]), None, "the package could not be submitted")
        except Exception:  # noqa: BLE001
            pass
        raise
    async with pool.acquire() as conn:
        return await package_detail(conn, org_id, package_id)


async def _apply_decision(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_PKG} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if row is None or row["status"] != "draft":
                return dict(row) if row else None
            p = dict(row)
            if str(actor_id) in {str(m) for m in p["maker_ids"] or []}:
                raise MakerCheckerViolation(MAKER_CHECKER_RULE)
            now = datetime.now(timezone.utc)
            if outcome == "approve":
                if p["rolled_from_id"]:
                    prev = await conn.fetchrow(f"SELECT * FROM {T_PKG} WHERE id = $1::uuid AND system_to IS NULL "
                                               "FOR UPDATE", p["rolled_from_id"])
                    if prev is not None and prev["status"] == "approved":
                        await _update(conn, prev["id"], {"status": "rolled", "status_reason":
                                                         f"rolled into package #{p['package_no']}"})
                new = await _update(conn, p["id"], {"status": "approved", "checker_id": uuid.UUID(str(actor_id)),
                                                    "decided_at": now, "decision_note": note})
            else:
                new = await _update(conn, p["id"], {"status": "rejected", "checker_id": uuid.UUID(str(actor_id)),
                                                    "decided_at": now, "decision_note": note})
            await _audit(conn, org_id, actor_id, f"package_{new['status']}", p["id"], {"task_id": task["id"],
                                                                                      "note": note})
    await _publish(pool, org_id, f"package_{new['status']}", new["id"], {"task_id": task["id"]}, actor_id)
    return new


async def _on_approval(pool, task, outcome, actor_id, note):
    return await _apply_decision(pool, task["org_id"], task, outcome, actor_id, note)


async def _approval_precheck(pool, task, outcome, actor_id):
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        p = await conn.fetchrow(f"SELECT * FROM {T_PKG} WHERE approval_task_id = $1::uuid AND system_to IS NULL",
                                task["id"])
        if p is None:
            return
        need = priced(dict(p), await legs_of(conn, p["id"]))
    if need:
        raise HT.HumanTaskConflict("not priced — the package still needs " + "; ".join(need))


async def decide_package(pool, *, org_id, actor_id, package_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise OptionsError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise OptionsError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        p = await load_package(conn, org_id, package_id)
    if p["status"] != "draft" or not p["approval_task_id"]:
        raise OptionsConflict(f"package #{p['package_no']} is not awaiting approval")
    if str(actor["id"]) in {str(m) for m in p["maker_ids"] or []}:
        raise MakerCheckerViolation(MAKER_CHECKER_RULE)
    try:
        await HT.complete_task(pool, org_id=org_id, task_id=p["approval_task_id"], actor_id=actor["id"],
                               outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise OptionsConflict(str(exc)) from exc
    async with pool.acquire() as conn:
        return await package_detail(conn, org_id, package_id)


async def _on_roll_done(pool, task, outcome, actor_id, note):
    return None     # the to-do is the reminder; pricing / approval is the package's own flow


async def _expired_precheck(pool, task, outcome, actor_id):
    """Confirming an expiry confirms its settlement entries — refused while a
    cash-settled entry is still unpriced (re-read from the quotes first)."""
    async with pool.acquire() as conn:
        p = await conn.fetchrow(f"SELECT id, org_id FROM {T_PKG} WHERE id = $1::uuid AND system_to IS NULL",
                                task["subject_id"])
    if p is None:
        return
    await refresh_settlements(pool, org_id=p["org_id"], package_id=p["id"])
    async with pool.acquire() as conn:
        n = await conn.fetchval(f"SELECT count(*) FROM {T_SETTLE} WHERE package_id = $1::uuid AND status = 'pending' "
                                "AND settlement_level IS NULL AND valid_to IS NULL AND system_to IS NULL", p["id"])
    if n:
        raise HT.HumanTaskConflict(f"{n} cash settlement entr{'y is' if n == 1 else 'ies are'} not priced — enter the "
                                   "index level at expiry (an index-level quote dated the expiry day) first")


async def _on_expired_done(pool, task, outcome, actor_id, note):
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                f"UPDATE {T_SETTLE} SET status = 'confirmed', confirmed_by = $2::uuid, confirmed_at = now(), "
                "note = $3, system_from = now() WHERE package_id = $1::uuid AND status = 'pending' "
                "AND valid_to IS NULL AND system_to IS NULL RETURNING id",
                task["subject_id"], str(actor_id), note)
            await _audit(conn, task["org_id"], actor_id, "settlement_confirmed", task["subject_id"],
                         {"entries": [str(r["id"]) for r in rows], "task_id": task["id"]})
    return {"confirmed": len(rows)}


HT.register_kind(TASK_APPROVAL, label="Option package approval", outcomes=("approve", "reject"),
                 maker_checker=True, on_complete=_on_approval)
HT.register_kind(TASK_ROLL, label="Option package roll due", outcomes=("done",), maker_checker=False,
                 on_complete=_on_roll_done, system_open=True)
HT.register_kind(TASK_EXPIRED, label="Option package expired — confirm settlement", outcomes=("done",),
                 maker_checker=False, on_complete=_on_expired_done, system_open=True)
HT.register_precheck(TASK_APPROVAL, _approval_precheck)
HT.register_precheck(TASK_EXPIRED, _expired_precheck)


# ── minimum account and viability ───────────────────────────────────────────
async def minimum_account(conn, org_id, p: dict, legs: list[dict]) -> dict:
    """The smallest account that holds the options sleeve within the hedge-ratio
    tolerance t: one contract covers N = multiplier × level of underlying
    notional; the sleeve covers c (the largest option-leg ratio) of the
    account, so n contracts suit accounts A with |n·N − c·A| ≤ t·c·A, i.e.
    n·N / (c(1+t)) ≤ A ≤ n·N / (c(1−t)). Minimum = N / (c(1+t)); every account
    from n*·N / (c(1+t)) up fits some n, n* = ⌈(1−t) / 2t⌉. Exact Decimal from
    the multiplier and the adviser's level quote; no quote → not priced. A mini
    contract on the same index (flagged in the security master) is considered
    too."""
    opts = [x for x in legs if x["role"] != "underlying"]
    if not opts:
        return {"status": "no_options", "message": "the package has no option leg"}
    t = await _setting(conn, org_id, TOLERANCE_SETTING, DEFAULT_TOLERANCE, lo=Decimal("0.0001"), hi=Decimal("0.5"))
    c = max(D(x["ratio"]) for x in opts)
    m = D(opts[0]["multiplier"])
    und = await _security(conn, p["underlying_global_security_id"])
    kind = level_kind(und["security_type"])
    q = await latest_quote(conn, org_id, und["id"], kind)
    base = {"tolerance": dstr(t), "tolerance_setting": TOLERANCE_SETTING, "coverage": dstr(c), "multiplier": dstr(m),
            "level_kind": kind}
    if q is None:
        return {**base, "status": "not_priced",
                "message": f"not priced — enter an {'index level' if kind == 'index_level' else 'underlying price'}"}
    level = D(q["value"])

    def figure(mult: Decimal, ratio: Decimal) -> dict:
        n_per = mult * level * ratio
        mn = n_per / (c * (ONE + t))
        n_star = int(((ONE - t) / (2 * t)).to_integral_value(rounding=ROUND_CEILING))
        cont = n_star * n_per / (c * (ONE + t))
        return {"notional_per_contract": dstr(n_per), "minimum_account": dstr(mn),
                "minimum_account_cents": dstr(mn.quantize(CENT, rounding=ROUND_CEILING)),
                "continuous_from_contracts": n_star, "continuous_from": dstr(cont),
                "continuous_from_cents": dstr(cont.quantize(CENT, rounding=ROUND_CEILING))}

    out = {**base, "status": "priced", "label": ESTIMATE, "level": dstr(level), "quote": quote_out(q),
           "standard": figure(m, ONE), "mini": []}
    for r in await conn.fetch(
            f"SELECT DISTINCT multiplier, mini_ratio FROM {T_CON} WHERE underlying_global_security_id = $1::uuid "
            "AND is_mini AND valid_to IS NULL AND system_to IS NULL ORDER BY mini_ratio", und["id"]):
        out["mini"].append({"multiplier": dstr(D(r["multiplier"])), "mini_ratio": dstr(D(r["mini_ratio"])),
                            **figure(D(r["multiplier"]), D(r["mini_ratio"]))})
    if not out["mini"]:
        out["mini_note"] = "no mini contract on this underlying is defined in the security master"
    return out


async def options_viability(conn, custodian_code: str | None, kind: str) -> dict:
    """Q4 for an options sleeve: the custodian's options level and FLEX access.
    'unknown' is not available → every options trade is a manual execution
    instruction. NEVER a block on the core."""
    from services import custodian_capabilities as CC

    code = (custodian_code or "").lower()
    label = CC.CUSTODIAN_LABELS.get(code, custodian_code or "the custodian")
    lvl = await CC.capability(conn, code, "options_level")
    flex = await CC.capability(conn, code, "flex_options")
    need = LEVEL_NEEDED[kind]
    unknown = [k for k, c in (("options_level", lvl), ("flex_options", flex)) if c.value == "unknown"]
    level_ok = CC.available(lvl, at_least=need)
    flex_ok = CC.available(flex)
    if unknown:
        note = f"manual execution only — {label} options access unconfirmed"
    elif not level_ok or not flex_ok:
        note = (f"manual execution only — {label} does not evidence options level {need}+"
                f"{' and FLEX options' if not flex_ok else ''}; a broker or trader carries out the options legs")
    else:
        note = "manual execution — no options trading API exists in this build"
    return {"manual_only": True, "core_affected": False, "note": note, "options_level_needed": need,
            "options_level": {"value": lvl.value, "evidence": lvl.evidence, "as_of": _iso(lvl.as_of),
                              "available": level_ok},
            "flex_options": {"value": flex.value, "evidence": flex.evidence, "as_of": _iso(flex.as_of),
                             "available": flex_ok},
            "missing": [f"evidence of {k} at {label}" for k in unknown]}


# ── payoff lab (estimates only from quotes) ─────────────────────────────────
async def mark_to_market(conn, org_id, legs: list[dict]) -> dict:
    """Today's value of the options legs per package unit (each leg's ratio in
    contracts): Σ ±ratio × premium × multiplier — ONLY from premium quotes, each
    listed with its as-of; any leg without one → not priced."""
    total, lines, missing = ZERO, [], []
    for leg in legs:
        if leg["role"] == "underlying":
            continue
        c = await load_contract(conn, contract_id=leg["contract_id"]) if leg["contract_id"] else None
        q = await latest_quote(conn, org_id, c["global_security_id"], "premium") if c else None
        if q is None:
            missing.append(LEG_ROLE_LABELS[leg["role"]])
            lines.append({"role": leg["role"], "status": "not_priced", "message": NOT_PRICED})
            continue
        sign = ONE if leg["direction"] == "long" else -ONE
        v = sign * D(leg["ratio"]) * D(q["value"]) * D(leg["multiplier"])
        total += v
        lines.append({"role": leg["role"], "status": "priced", "value": dstr(v), "quote": quote_out(q)})
    if missing:
        return {"status": "not_priced", "message": NOT_PRICED, "missing": missing, "lines": lines}
    return {"status": "priced", "label": ESTIMATE, "value_per_unit": dstr(total), "lines": lines,
            "note": "estimate from adviser-entered premium quotes (long legs +, short legs −), per unit of the "
                    "package (each leg's ratio in contracts)"}


async def reprice_cap(conn, org_id, p: dict, legs: list[dict]) -> dict:
    """The cap a new package would set at today's quotes: net cost of the puts
    (long ATM premium − short put premium) is paid for by the call; among the
    QUOTED call strikes on the same underlying and expiry, the highest strike
    whose premium still covers the net put cost. Quoted strikes only — never
    interpolated. Missing quotes → not priced."""
    if p["kind"] != "buffer":
        return {"status": "not_applicable", "message": "a cap is set on a buffer package"}
    lp = next((x for x in legs if x["role"] == "long_put_atm" and x["contract_id"]), None)
    sp = next((x for x in legs if x["role"] == "short_put_buffer_end" and x["contract_id"]), None)
    if not (lp and sp and p["start_level"] is not None):
        return {"status": "not_priced", "message": "not priced — set the start level so the put contracts exist, "
                                                   "then enter their premium quotes"}
    clp, csp = (await load_contract(conn, contract_id=lp["contract_id"]),
                await load_contract(conn, contract_id=sp["contract_id"]))
    qlp, qsp = (await latest_quote(conn, org_id, clp["global_security_id"], "premium"),
                await latest_quote(conn, org_id, csp["global_security_id"], "premium"))
    if qlp is None or qsp is None:
        return {"status": "not_priced", "message": NOT_PRICED,
                "missing": [n for n, q in (("long put", qlp), ("short put", qsp)) if q is None]}
    net = D(qlp["value"]) - D(qsp["value"])
    candidates = []
    for r in await conn.fetch(
            f"SELECT * FROM {T_CON} WHERE underlying_global_security_id = $1::uuid AND option_type = 'call' "
            "AND expiry = $2 AND multiplier = $3 AND NOT is_mini AND valid_to IS NULL AND system_to IS NULL "
            "ORDER BY strike", p["underlying_global_security_id"], clp["expiry"], clp["multiplier"]):
        q = await latest_quote(conn, org_id, r["global_security_id"], "premium")
        if q is not None:
            candidates.append((D(r["strike"]), D(q["value"]), q))
    fits = [x for x in candidates if x[1] >= net]
    if not candidates:
        return {"status": "not_priced", "message": "not priced — enter premium quotes for candidate call strikes "
                                                   "(same underlying and expiry)", "net_put_cost": dstr(net)}
    if not fits:
        return {"status": "not_priced", "message": "no quoted call strike covers the net put cost — quote lower "
                                                   "strikes", "net_put_cost": dstr(net)}
    k, prem, q = max(fits, key=lambda x: x[0])
    start = D(p["start_level"])
    return {"status": "priced", "label": ESTIMATE, "cap_pct": dstr(k / start - ONE), "call_strike": dstr(k),
            "call_premium": dstr(prem), "net_put_cost": dstr(net),
            "quotes": [quote_out(qlp), quote_out(qsp), quote_out(q)],
            "candidates": [{"strike": dstr(s), "premium": dstr(v), "as_of": qq["as_of"].isoformat()}
                           for s, v, qq in candidates],
            "note": "estimate: the highest quoted call strike whose premium covers the net put cost (zero net "
                    "premium); quoted strikes only, never interpolated"}


NOTE_SLOT = {"phase": "phase 3", "status": "empty",
             "label": "Structured-note comparison (note terms from the 424B2 pipeline) — phase 3", "rows": []}


async def payoff_lab(conn, org_id, package_id, *, step: Decimal | None = None) -> dict:
    p = await load_package(conn, org_id, package_id)
    legs = await legs_of(conn, p["id"])
    s = step if step is not None else await _setting(conn, org_id, PAYOFF_STEP_SETTING, DEFAULT_STEP,
                                                      lo=Decimal("0.005"), hi=Decimal("0.25"))
    pl = legs_from_rows(legs)
    stated_cap, stated_buffer, stated_floor = D(p["stated_cap_pct"]), D(p["stated_buffer_pct"]), \
        D(p["stated_floor_pct"])
    cap = stated_cap if stated_cap is not None else D(p["cap_pct"])
    buffer = stated_buffer if stated_buffer is not None else D(p["buffer_pct"])
    points = payoff_points(pl, cap=cap, buffer=buffer, floor=stated_floor, step=s)
    return {"package": package_out(p), "legs": [leg_out(x) for x in legs], "step": dstr(s),
            "range": [dstr(PAYOFF_LO), dstr(PAYOFF_HI)], "points": points,
            "stated": {"cap_pct": dstr(cap), "buffer_pct": dstr(buffer), "floor_pct": dstr(stated_floor),
                       "source": "the fund's approved terms" if stated_cap is not None else "computed from the legs"},
            "at_expiry_note": "exact at expiry from strikes and ratios — premiums excluded (a package opened at zero "
                              "net premium); needs no prices",
            "mark_to_market": await mark_to_market(conn, org_id, legs), "reprice": await reprice_cap(conn, org_id, p,
                                                                                                    legs),
            "note_comparison": NOTE_SLOT}


async def package_detail(conn, org_id, package_id) -> dict:
    p = await load_package(conn, org_id, package_id)
    legs = await legs_of(conn, p["id"])
    contracts = {str(x["contract_id"]): await load_contract(conn, contract_id=x["contract_id"])
                 for x in legs if x["contract_id"]}
    und = await _security(conn, p["underlying_global_security_id"])
    gsids = [und["id"]] + [c["global_security_id"] for c in contracts.values()]
    task = await HT.get_task(conn, org_id, p["approval_task_id"]) if p["approval_task_id"] else None
    return {"package": package_out(p), "legs": [leg_out(x, contracts.get(str(x["contract_id"]))) for x in legs],
            "underlying": {"id": str(und["id"]), "name": und["name"], "ticker": und["ticker"],
                           "security_type": und["security_type"]},
            "quotes": await list_quotes(conn, org_id, global_security_ids=gsids, limit=100),
            "minimum_account": await minimum_account(conn, org_id, p, legs), "needs": priced(p, legs),
            "basis_risk": {"tracking_error": "not estimated — needs prices",
                           "note": ("the core basket and the index the options settle on differ (replication "
                                    "error, dividends, cash) — tracking error between them is not estimated "
                                    "without a price history")},
            "task": task}


def actions_for(p: dict, *, can_manage: bool, can_approve: bool, user_id, task_open: bool, needs: list) -> list[str]:
    out = []
    makers = {str(m) for m in p.get("maker_ids") or []}
    if p["status"] == "draft" and can_manage is True:
        out.append("price")
        if not p.get("approval_task_id") and not needs:
            out.append("submit")
    if p["status"] == "draft" and task_open and can_approve is True and str(user_id) not in makers:
        out += ["approve", "reject"]
    return out


# ── rolls and expiry (the daily sweep; test orgs are skipped by the caller) ─
async def _next_no(conn, model_id) -> int:
    return int(await conn.fetchval(f"SELECT COALESCE(max(package_no), 0) + 1 FROM {T_PKG} WHERE model_id = $1::uuid "
                                   "AND system_to IS NULL", str(model_id)))


async def draft_roll(pool, *, org_id, package_id) -> dict | None:
    """The NEXT package as a system DRAFT: same buffer (or call rule), the next
    outcome period (same length), no start level, the cap leg omitted —
    "not priced" until a person enters quotes and sets it. Idempotent: a live
    draft / approved roll of this package is returned instead."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            p = await load_package(conn, org_id, package_id, lock=True)
            got = await conn.fetchrow(f"SELECT * FROM {T_PKG} WHERE rolled_from_id = $1::uuid AND system_to IS NULL "
                                      "AND status IN ('draft', 'approved')", p["id"])
            if got:
                return None
            legs = await legs_of(conn, p["id"])
            length = p["outcome_end"] - p["outcome_start"]
            start = p["outcome_end"]
            end = start + (timedelta(days=p["tenor_days"]) if p["kind"] == "income" else length)
            new_legs = []
            for x in legs:
                if x["role"] == "underlying":
                    new_legs.append(_underlying_leg())
                elif x["role"] in ("short_call_cap",) or (x["role"] == "short_call_income" and p["call_rule"] != "pct_otm"):
                    continue          # not priced until a person sets it from quotes
                else:
                    new_legs.append(_opt_leg(x["role"], ratio=D(x["ratio"]), strike_pct=D(x["strike_pct"]),
                                             expiry=end, style=x["exercise_style"], settlement=x["settlement"],
                                             is_flex=x["is_flex"], multiplier=D(x["multiplier"])))
            comp = _with_computed(p["kind"], new_legs)
            pid = await conn.fetchval(
                f"""INSERT INTO {T_PKG} (org_id, replica_security_id, model_id, model_version_id, terms_version_id,
                        package_no, kind, core_source, core_model_id, underlying_global_security_id, outcome_start,
                        outcome_end, cap_pct, buffer_pct, floor_pct, stated_cap_pct, stated_buffer_pct,
                        stated_floor_pct, call_rule, call_otm_pct, call_delta, tenor_days, coverage_pct, status,
                        rolled_from_id, maker_ids, notes)
                    SELECT org_id, replica_security_id, model_id, model_version_id, terms_version_id, $2, kind,
                           core_source, core_model_id, underlying_global_security_id, $3, $4, $5, $6, $7, NULL,
                           stated_buffer_pct, stated_floor_pct, call_rule, call_otm_pct, call_delta, tenor_days,
                           coverage_pct, 'draft', id, '{{}}'::uuid[], $8
                    FROM {T_PKG} WHERE id = $1::uuid RETURNING id""",
                p["id"], await _next_no(conn, p["model_id"]), start, end, comp["cap_pct"], comp["buffer_pct"],
                comp["floor_pct"],
                f"Roll of package #{p['package_no']} — cap not priced until quotes are entered")
            await _write_legs(conn, org_id, pid, new_legs, None)
            await _audit(conn, org_id, None, "roll_drafted", pid, {"rolled_from_id": str(p["id"])})
            new = await load_package(conn, org_id, pid)
            entry = await T.load_entry(conn, org_id, p["replica_security_id"])
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_ROLL, subject_type=SUBJECT, subject_id=new["id"], maker_id=None,
        eligible_permission=MANAGE, actor_kind="system",
        title=f"Roll due: {entry['display_name']} outcome period ends {p['outcome_end'].isoformat()}",
        payload={"package_no": new["package_no"], "rolled_from": p["package_no"],
                 "outcome_end": p["outcome_end"].isoformat(), "next_period": [start.isoformat(), end.isoformat()],
                 "cap": "not priced"},
        action_path=f"/portfolio/replication/{entry['id']}?tab=options")
    await _publish(pool, org_id, "roll_drafted", new["id"], {"rolled_from_id": str(p["id"]), "task_id": task["id"]},
                   None)
    return {"package": package_out(new), "task_id": task["id"]}


async def _holders(conn, org_id, contract_gsid) -> list[dict]:
    """Accounts whose latest live position in the contract is not zero."""
    return [dict(r) for r in await conn.fetch(
        """SELECT DISTINCT ON (p.account_id) p.account_id, p.quantity
           FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
           WHERE p.org_id = $1::uuid AND a.global_security_id = $2::uuid AND p.account_id IS NOT NULL
             AND p.valid_to IS NULL AND p.system_to IS NULL
           ORDER BY p.account_id, p.as_of_date DESC, p.valid_from DESC""", str(org_id), str(contract_gsid))
            if r["quantity"] is not None and D(r["quantity"]) != 0]


async def _settlement_rows(conn, org_id, p: dict) -> list[dict]:
    """The cash settlement figures of each cash-settled option leg (per holding
    account; a package-level entry when no account holds it): intrinsic at the
    index level quoted ON the expiry day × multiplier × signed contracts."""
    out = []
    legs = await legs_of(conn, p["id"])
    q = await latest_quote(conn, org_id, p["underlying_global_security_id"], "index_level", on_date=p["outcome_end"])
    level = D(q["value"]) if q else None
    for leg in legs:
        if leg["role"] == "underlying" or leg["settlement"] != "cash" or not leg["contract_id"]:
            continue
        c = await load_contract(conn, contract_id=leg["contract_id"])
        per = None if level is None else intrinsic(c["option_type"], D(c["strike"]), level) * D(c["multiplier"])
        holders = await _holders(conn, org_id, c["global_security_id"]) or [{"account_id": None, "quantity": None}]
        for h in holders:
            n = D(h["quantity"])
            out.append({"leg_id": leg["id"], "contract_id": c["id"], "account_id": h["account_id"],
                        "expiry": p["outcome_end"], "settlement_level": level,
                        "settlement_quote_id": q["id"] if q else None, "intrinsic_per_contract": per,
                        "contracts": n, "amount": (per * n) if (per is not None and n is not None) else None})
    return out


async def refresh_settlements(pool, *, org_id, package_id) -> list[dict]:
    """Write (or re-write, for an unpriced pending entry once its quote exists)
    the package's settlement entries. A priced or confirmed entry is never
    rewritten; an unpriced pending one is closed and replaced."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            p = await load_package(conn, org_id, package_id)
            for r in await _settlement_rows(conn, org_id, p):
                cur = await conn.fetchrow(
                    f"SELECT * FROM {T_SETTLE} WHERE leg_id = $1::uuid AND account_id IS NOT DISTINCT FROM $2::uuid "
                    "AND valid_to IS NULL AND system_to IS NULL", r["leg_id"],
                    str(r["account_id"]) if r["account_id"] else None)
                if cur is not None and (cur["status"] != "pending" or cur["settlement_level"] is not None
                                        or r["settlement_level"] is None):
                    continue
                if cur is not None:
                    await conn.execute(f"UPDATE {T_SETTLE} SET valid_to = now() WHERE id = $1", cur["id"])
                await conn.execute(
                    f"""INSERT INTO {T_SETTLE} (org_id, package_id, leg_id, contract_id, account_id, expiry,
                            settlement_level, settlement_quote_id, intrinsic_per_contract, contracts, amount,
                            confirm_task_id)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7, $8::uuid, $9, $10, $11,
                                $12::uuid)""",
                    str(org_id), str(p["id"]), str(r["leg_id"]), str(r["contract_id"]),
                    str(r["account_id"]) if r["account_id"] else None, r["expiry"], r["settlement_level"],
                    str(r["settlement_quote_id"]) if r["settlement_quote_id"] else None,
                    r["intrinsic_per_contract"], r["contracts"], r["amount"],
                    str(cur["confirm_task_id"]) if cur is not None and cur["confirm_task_id"] else None)
            return await settlements_of(conn, org_id, p["id"])


async def settlements_of(conn, org_id, package_id) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT s.*, a.account_number_masked FROM {T_SETTLE} s LEFT JOIN public.accounts a ON a.id = s.account_id
                AND a.valid_to IS NULL AND a.system_to IS NULL
            WHERE s.org_id = $1::uuid AND s.package_id = $2::uuid AND s.valid_to IS NULL AND s.system_to IS NULL
            ORDER BY s.created_at""", str(org_id), str(package_id))
    return [{"id": str(r["id"]), "leg_id": str(r["leg_id"]), "account": r["account_number_masked"],
             "account_id": str(r["account_id"]) if r["account_id"] else None, "expiry": _iso(r["expiry"]),
             "settlement_level": dstr(D(r["settlement_level"])),
             "intrinsic_per_contract": dstr(D(r["intrinsic_per_contract"])), "contracts": dstr(D(r["contracts"])),
             "amount": dstr(D(r["amount"])),
             "amount_text": (f"{dstr(D(r['amount']))} ({ESTIMATE} from the quoted settlement level)"
                             if r["amount"] is not None else
                             ("not priced — enter the index level at expiry" if r["settlement_level"] is None
                              else "no holding account recorded")),
             "status": r["status"], "confirmed_by": str(r["confirmed_by"]) if r["confirmed_by"] else None,
             "confirmed_at": _iso(r["confirmed_at"])} for r in rows]


async def expire(pool, *, org_id, package_id, today: date) -> dict | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            p = await load_package(conn, org_id, package_id, lock=True)
            if p["status"] != "approved" or p["outcome_end"] >= today:
                return None
            await _update(conn, p["id"], {"status": "expired", "status_reason":
                                          f"outcome period ended {p['outcome_end'].isoformat()} with no approved roll"})
            await _audit(conn, org_id, None, "package_expired", p["id"], {"outcome_end": p["outcome_end"].isoformat()})
            entry = await T.load_entry(conn, org_id, p["replica_security_id"])
    entries = await refresh_settlements(pool, org_id=org_id, package_id=p["id"])
    physical = []
    async with pool.acquire() as conn:
        physical = [LEG_ROLE_LABELS[x["role"]] for x in await legs_of(conn, p["id"])
                    if x["role"] != "underlying" and x["settlement"] == "physical"]
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_EXPIRED, subject_type=SUBJECT, subject_id=p["id"], maker_id=None,
        eligible_permission=MANAGE, actor_kind="system",
        title=f"Expired with no roll: {entry['display_name']} package #{p['package_no']} — confirm settlement",
        payload={"package_no": p["package_no"], "outcome_end": p["outcome_end"].isoformat(),
                 "settlement_entries": len(entries),
                 "physical_legs": physical, "note": ("cash-settled legs: confirm each settlement entry (priced from the "
                                                     "index-level quote on the expiry day); physically settled legs: "
                                                     "enter the delivery as a trade")},
        action_path=f"/portfolio/replication/{entry['id']}?tab=options")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"UPDATE {T_SETTLE} SET confirm_task_id = $2::uuid WHERE package_id = $1::uuid "
                               "AND status = 'pending' AND valid_to IS NULL AND system_to IS NULL",
                               str(p["id"]), task["id"])
    await _publish(pool, org_id, "package_expired", p["id"], {"task_id": task["id"]}, None)
    return {"package_id": str(p["id"]), "task_id": task["id"], "settlement_entries": len(entries)}


async def sweep(pool, *, org_id, today: date | None = None) -> dict:
    """The daily options step (rep5's org_tick runs it — never for a test org
    unless a verify asks): a roll to-do with a DRAFT next package N days
    (setting) before an outcome period ends; an approved package past its end
    with no approved roll → expired, a to-do, settlement entries."""
    today = today or date.today()
    out = {"rolls": [], "expired": [], "errors": []}
    async with pool.acquire() as conn:
        notice = await _setting(conn, org_id, ROLL_NOTICE_SETTING, DEFAULT_ROLL_NOTICE, lo=1, hi=120, integer=True)
        rows = [dict(r) for r in await conn.fetch(
            f"SELECT * FROM {T_PKG} WHERE org_id = $1::uuid AND status = 'approved' AND system_to IS NULL "
            "ORDER BY outcome_end", str(org_id))]
    for p in rows:
        try:
            if p["outcome_end"] < today:
                r = await expire(pool, org_id=org_id, package_id=p["id"], today=today)
                if r:
                    out["expired"].append(r)
            elif (p["outcome_end"] - today).days <= notice:
                r = await draft_roll(pool, org_id=org_id, package_id=p["id"])
                if r:
                    out["rolls"].append({"rolled_from": str(p["id"]), **r})
        except Exception as exc:  # noqa: BLE001 — one package never stops the others
            out["errors"].append(f"package {p['id']}: {exc}")
    return {**out, "notice_days": notice}


# ── fills → option lots ─────────────────────────────────────────────────────
async def book_fills(pool, *, org_id, actor_id, instruction_id) -> dict:
    """Book an options instruction's fills (entered manually on rep2's flow) as
    option positions, transactions and lots — idempotent per fill
    (transactions.external_ref 'option_fill:<id>'). Opening: a lot per fill
    (long: quantity +, cost = premium × multiplier × contracts paid; short:
    quantity −, cost = − premium received). Closing: the account's open lots
    in the contract, oldest first, closed on the valid axis (the closed state
    inserted with its realized result)."""
    from services import execution as X

    out = {"booked": [], "skipped": []}
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        ins = await X.load(conn, org_id, instruction_id)
        terms = _loads(ins.get("option_terms"))
        if not terms:
            raise OptionsConflict("this instruction carries no option leg")
        if ins["status"] not in ("partially_filled", "filled", "reconciled", "closed"):
            raise OptionsConflict(f"nothing to book: the instruction is {ins['status']}")
        acct = await X.load_account(conn, org_id, ins["account_id"])
        if not acct.get("primary_entity_id"):
            raise OptionsConflict("the account has no primary entity to own the option position")
        fills = await X._fills(conn, ins["id"])
        c = await load_contract(conn, global_security_id=ins["global_security_id"])
    mult = D(c["multiplier"])
    effect = terms["position_effect"]
    for f in fills:
        ref = f"option_fill:{f['id']}"
        async with pool.acquire() as conn:
            async with conn.transaction():
                if await conn.fetchval("SELECT 1 FROM portfolio.transactions WHERE org_id = $1::uuid AND external_ref "
                                       "= $2 AND system_to IS NULL", str(org_id), ref):
                    out["skipped"].append(str(f["id"]))
                    continue
                asset = await conn.fetchval(
                    "SELECT id FROM portfolio.assets WHERE org_id = $1::uuid AND global_security_id = $2::uuid "
                    "AND valid_to IS NULL AND system_to IS NULL LIMIT 1", str(org_id), str(ins["global_security_id"]))
                if asset is None:
                    asset = await conn.fetchval(
                        "INSERT INTO portfolio.assets (org_id, global_security_id, name, asset_type) "
                        "VALUES ($1::uuid, $2::uuid, $3, 'option') RETURNING id",
                        str(org_id), str(ins["global_security_id"]), c["name"])
                cur = await conn.fetchrow(
                    "SELECT * FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid AND asset_id = "
                    "$3::uuid AND valid_to IS NULL AND system_to IS NULL ORDER BY as_of_date DESC LIMIT 1",
                    str(org_id), str(ins["account_id"]), asset)
                qty = D(f["quantity"])
                signed = qty if ins["side"] == "buy" else -qty
                new_qty = (D(cur["quantity"]) if cur and cur["quantity"] is not None else ZERO) + signed
                fill_day = f["filled_at"].astimezone(timezone.utc).date()
                if cur is not None:
                    await conn.execute("UPDATE portfolio.positions SET valid_to = now() WHERE id = $1", cur["id"])
                pos = await conn.fetchval(
                    "INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, quantity, authority, "
                    "source_system, account_id) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, 'manual', 'manual', "
                    "$6::uuid) RETURNING id", str(org_id), str(acct["primary_entity_id"]), asset,
                    max(fill_day, cur["as_of_date"]) if cur else fill_day, new_qty, str(ins["account_id"]))
                gross = qty * D(f["price"]) * mult
                tx = await conn.fetchval(
                    "INSERT INTO portfolio.transactions (org_id, position_id, transaction_type_code, trade_date, "
                    "quantity, price, gross_amount, net_amount, currency_code, authority, source_system, external_ref) "
                    "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, 'USD', 'manual', 'manual', $9) RETURNING id",
                    str(org_id), pos, ins["side"], fill_day, qty, D(f["price"]), gross,
                    -gross if ins["side"] == "buy" else gross, ref)
                if effect == "open":
                    await conn.execute(
                        "INSERT INTO portfolio.tax_lots (org_id, position_id, opening_transaction_id, acquired_date, "
                        "quantity, original_cost, adjusted_cost, status, source_system, external_ref) "
                        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $6, 'open', 'manual', $7)",
                        str(org_id), pos, tx, fill_day, signed, gross if ins["side"] == "buy" else -gross, ref)
                else:
                    await _close_lots(conn, org_id=org_id, account_id=ins["account_id"],
                                      gsid=ins["global_security_id"], qty=qty, unit_value=D(f["price"]) * mult,
                                      closing_tx=tx, side=ins["side"])
                await _audit(conn, org_id, actor["id"], "fill_booked", ins["id"],
                             {"fill_id": str(f["id"]), "transaction_id": str(tx), "effect": effect})
                out["booked"].append(str(f["id"]))
    return out


async def _close_lots(conn, *, org_id, account_id, gsid, qty: Decimal, unit_value: Decimal, closing_tx, side: str):
    lots = await conn.fetch(
        """SELECT tl.* FROM portfolio.tax_lots tl
           JOIN portfolio.positions p ON p.id = tl.position_id AND p.system_to IS NULL
           JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
           WHERE tl.org_id = $1::uuid AND p.account_id = $2::uuid AND a.global_security_id = $3::uuid
             AND tl.status = 'open' AND tl.valid_to IS NULL AND tl.system_to IS NULL
           ORDER BY tl.acquired_date NULLS LAST, tl.valid_from""", str(org_id), str(account_id), str(gsid))
    left = qty
    for lot in lots:
        if left <= 0:
            break
        lq = abs(D(lot["quantity"]))
        take = min(lq, left)
        cost = D(lot["original_cost"]) * (take / lq)
        sign = ONE if D(lot["quantity"]) > 0 else -ONE
        # closing a long sells (proceeds +); closing a short buys back (proceeds −)
        proceeds = take * unit_value * (ONE if side == "sell" else -ONE)
        realized = proceeds - cost
        await conn.execute("UPDATE portfolio.tax_lots SET valid_to = now() WHERE id = $1", lot["id"])
        await conn.execute(
            "INSERT INTO portfolio.tax_lots (org_id, position_id, opening_transaction_id, closing_transaction_id, "
            "acquired_date, quantity, original_cost, adjusted_cost, realized_gain_loss, status, source_system, "
            "external_ref) VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7, $7, $8, 'closed', 'manual', $9)",
            str(org_id), lot["position_id"], lot["opening_transaction_id"], str(closing_tx), lot["acquired_date"],
            sign * take, cost, realized, lot["external_ref"])
        if take < lq:
            await conn.execute(
                "INSERT INTO portfolio.tax_lots (org_id, position_id, opening_transaction_id, acquired_date, quantity, "
                "original_cost, adjusted_cost, status, source_system, external_ref) VALUES ($1::uuid, $2, $3, $4, $5, "
                "$6, $6, 'open', 'manual', $7)", str(org_id), lot["position_id"], lot["opening_transaction_id"],
                lot["acquired_date"], sign * (lq - take), D(lot["original_cost"]) - cost, lot["external_ref"])
        left -= take
    if left > 0:
        raise OptionsConflict(f"the closing fill exceeds the account's open option lots by {dstr(left)} contract(s)")


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"leg_roles": pairs(LEG_ROLE_LABELS), "quote_kinds": pairs(QUOTE_KIND_LABELS),
            "package_statuses": pairs(STATUS_LABELS), "package_kinds": pairs(KIND_LABELS),
            "core_sources": pairs(CORE_SOURCE_LABELS), "call_rules": pairs(CALL_RULE_LABELS),
            "exercise_styles": [{"key": k, "label": k.capitalize()} for k in EXERCISE_STYLES],
            "settlements": [{"key": k, "label": k.capitalize()} for k in SETTLEMENTS]}
